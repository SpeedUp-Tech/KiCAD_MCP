"""
Prototype runner that executes PySpice simulations for JSON-defined testbenches.

Scope:
    * Instantiate generated *_pyspice modules.
    * Support simple VIN + constant-current load stimuli.
    * Run transient analysis and compute basic measurements (mean, efficiency).
    * Report pass/fail per measurement.
"""

from __future__ import annotations

import json
import runpy
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

import skidl
import skidl.part as skidl_part
from skidl.pyspice import *
from skidl import Circuit, Net
from skidl.tools.skidl.libs import pyspice_sklib


_ORIG_PART_INIT = skidl_part.Part.__init__


def _patched_part_init(self, *args, **kwargs):
    dest = kwargs.get("dest")
    if isinstance(dest, str) and dest.upper() == "INSTANCE":
        kwargs["dest"] = skidl_part.NETLIST
        lib_name = kwargs.get("lib")
        if isinstance(lib_name, str) and lib_name.lower() == "pyspice":
            kwargs["lib"] = pyspice_sklib.pyspice_lib

    return _ORIG_PART_INIT(self, *args, **kwargs)


if getattr(skidl_part.Part.__init__, "__name__", "") != "_patched_part_init":
    skidl_part.Part.__init__ = _patched_part_init


skidl.config.backup_lib = pyspice_sklib.pyspice_lib
skidl.config.query_backup_lib = True


def _load_pyspice_module(module_path: Path):
    return runpy.run_path(str(module_path))


def _build_dut(module_globals: Dict[str, Any]):
    fn = module_globals.get("Battery_Protection_pyspice")
    if fn is None:
        raise RuntimeError("Battery_Protection_pyspice not found in module.")
    return fn


def _normalize_time_window(value: Any) -> Optional[Tuple[float, float]]:
    if value is None:
        return None
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return float(value[0]), float(value[1])
    if isinstance(value, (int, float)):
        return 0.0, float(value)
    return None


def _determine_sim_end_ms(use_case: Dict[str, Any]) -> float:
    end_ms = 10.0
    when = use_case.get("when", {}) or {}

    window = _normalize_time_window(when.get("observe_window_ms"))
    if window:
        end_ms = max(end_ms, window[1])

    stabilize_ms = when.get("stabilize_ms")
    if isinstance(stabilize_ms, (int, float)):
        end_ms = max(end_ms, float(stabilize_ms) + 5.0)

    apply_load_ms = when.get("apply_load_at_ms")
    if isinstance(apply_load_ms, (int, float)):
        end_ms = max(end_ms, float(apply_load_ms) + 5.0)

    for measurement in use_case.get("then", {}).get("measurements", []):
        window = _normalize_time_window(measurement.get("args", {}).get("window_ms"))
        if window:
            end_ms = max(end_ms, window[1])

    vin_cfg = (use_case.get("given") or {}).get("VIN", {})
    ramp = vin_cfg.get("ramp")
    if ramp:
        end_ms = max(end_ms, float(ramp.get("rise_ms", 0.0)) + float(ramp.get("delay_ms", 0.0)))
    step_cfg = vin_cfg.get("step")
    if step_cfg:
        end_ms = max(end_ms, float(step_cfg.get("at_ms", 0.0)) + 5.0)

    return end_ms


def _connect_two_terminal(part, pos_net: Net, neg_net: Net, pos_pin: str = "p", neg_pin: str = "n"):
    instance = _normalize_part_instance(part)
    pos_net += instance[pos_pin]
    neg_net += instance[neg_pin]


def _normalize_part_instance(part):
    if isinstance(part, (list, tuple)):
        for candidate in part:
            if candidate is not None:
                return candidate
        raise ValueError("Expected at least one part instance for source creation.")
    return part


def _build_vin_source(vchg: Net, gnd: Net, vin_cfg: Dict[str, Any], sim_end_ms: float):
    if "ramp" in vin_cfg:
        ramp = vin_cfg["ramp"]
        start_value = ramp.get("start_V", 0.0) or 0.0
        stop_value = ramp.get("stop_V", start_value) or start_value
        start_v = float(start_value) @ u_V
        stop_v = float(stop_value) @ u_V
        delay_ms = float(ramp.get("delay_ms", 0.0))
        rise_ms = float(ramp.get("rise_ms", 0.0))
        values = [
            (0 @ u_ms, start_v),
        ]
        if delay_ms > 0:
            values.append((delay_ms @ u_ms, start_v))
        ramp_end = delay_ms + rise_ms
        values.append((ramp_end @ u_ms, stop_v))
        hold_time = max(sim_end_ms, ramp_end + 1.0)
        values.append((hold_time @ u_ms, stop_v))
        src = PWLV(ref="VIN", values=values)
    elif "step" in vin_cfg:
        step_cfg = vin_cfg["step"]
        pre_value = step_cfg.get("from_V", vin_cfg.get("dc_V", 0.0)) or 0.0
        post_value = step_cfg.get("to_V", pre_value) or pre_value
        pre_v = float(pre_value) @ u_V
        post_v = float(post_value) @ u_V
        at_ms = float(step_cfg.get("at_ms", 0.0))
        eps = 1e-6
        values = [
            (0 @ u_ms, pre_v),
            (at_ms @ u_ms, pre_v),
            ((at_ms + eps) @ u_ms, post_v),
            ((max(sim_end_ms, at_ms + 1.0)) @ u_ms, post_v),
        ]
        src = PWLV(ref="VIN", values=values)
    elif "ac_ripple_pp_V" in vin_cfg:
        dc_v = float(vin_cfg.get("dc_V", 0.0)) @ u_V
        amp = float(vin_cfg["ac_ripple_pp_V"]) / 2.0 @ u_V
        freq_hz = float(vin_cfg.get("ac_freq_kHz", 0.0)) * 1e3 @ u_Hz
        src = SINEV(ref="VIN", dc_offset=dc_v, amplitude=amp, frequency=freq_hz)
    elif "dc_V" in vin_cfg:
        src = V(ref="VIN", dc_value=float(vin_cfg["dc_V"]) @ u_V)
    else:
        raise ValueError("VIN config not supported in prototype.")

    _connect_two_terminal(src, vchg, gnd)


def _build_load(
    vpack: Net,
    gnd: Net,
    load_cfg: Dict[str, Any],
    when_cfg: Dict[str, Any],
    sim_end_ms: float,
    nets: Dict[str, Net],
    branch_sensors: Dict[str, str],
):
    needs_load_node = any(branch.endswith("LOAD") or branch.startswith("LOAD") for branch in branch_sensors)
    if load_cfg["type"] == "const_current":
        current = float(load_cfg["I_A"]) @ u_A
        apply_ms = when_cfg.get("apply_load_at_ms")
        load_net = vpack
        if needs_load_node:
            load_net = nets.get("LOAD")
            if load_net is None:
                load_net = Net("LOAD")
                nets["LOAD"] = load_net
        if isinstance(apply_ms, (int, float)):
            eps = 1e-6
            values = [
                (0 @ u_ms, 0 @ u_A),
                (float(apply_ms) @ u_ms, 0 @ u_A),
                ((float(apply_ms) + eps) @ u_ms, current),
                ((max(sim_end_ms, float(apply_ms) + 1.0)) @ u_ms, current),
            ]
            load = PWLI(ref="ILOAD", values=values)
        else:
            load = I(ref="ILOAD", dc_value=current)
        _connect_two_terminal(load, load_net, gnd)
    elif load_cfg["type"] == "battery_model":
        vbatt = V(ref="BAT", dc_value=float(load_cfg.get("voc_V", 4.0)) @ u_V)
        _connect_two_terminal(vbatt, vpack, gnd)
        esr = R(ref="BAT_ESR", value=float(load_cfg.get("esr_ohm", 0.05)) @ u_Ohm)
        _connect_two_terminal(esr, vpack, gnd)
    else:
        raise ValueError("Load type not supported in prototype.")


def _extract_branch_sensors(use_case: Dict[str, Any]) -> Dict[str, str]:
    sensors: Dict[str, str] = {}
    counter = 1
    for measurement in use_case.get("then", {}).get("measurements", []):
        branch = measurement.get("args", {}).get("branch")
        if not branch:
            continue
        key = branch.strip()
        if not key:
            continue
        if key not in sensors:
            sensors[key] = f"VBRANCH_{counter}"
            counter += 1
    return sensors


def _parse_branch(branch_name: str) -> Tuple[str, str]:
    parts = [p.strip() for p in branch_name.split("->", 1)]
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError(f"Invalid branch identifier '{branch_name}'")
    return parts[0], parts[1]


def _create_circuit(subckt_fn, use_case, sim_end_ms, branch_sensors: Dict[str, str]):
    circuit = Circuit()
    skidl.config.backup_lib = pyspice_sklib.pyspice_lib
    skidl.config.query_backup_lib = True
    with circuit:
        gnd = Net("GND")
        vchg = Net("VBAT_CHG")
        vpack = Net("VBAT_PACK")
        nets = {"GND": gnd, "VBAT_CHG": vchg, "VBAT_PACK": vpack}

        vin_cfg = use_case["given"]["VIN"]
        _build_vin_source(vchg, gnd, vin_cfg, sim_end_ms)

        load_cfg = use_case["given"]["load"]
        _build_load(vpack, gnd, load_cfg, use_case.get("when", {}) or {}, sim_end_ms, nets, branch_sensors)

        for branch_name, probe_ref in branch_sensors.items():
            start_node, end_node = _parse_branch(branch_name)
            try:
                start_net = nets[start_node]
                end_net = nets[end_node]
            except KeyError as exc:
                raise ValueError(f"Unknown net '{exc.args[0]}' in branch '{branch_name}'") from exc
            probe = V(ref=probe_ref, dc_value=0 @ u_V)
            _connect_two_terminal(probe, start_net, end_net)

        subckt_fn(GND=gnd, VBAT_CHG=vchg, VBAT_PACK=vpack)

    return circuit


def _evaluate_measurements(analysis, use_case, branch_sensors: Dict[str, str]):
    results = []
    for measurement in use_case["then"]["measurements"]:
        mid = measurement["id"]
        fn = measurement["fn"]
        args = measurement.get("args", {})
        expectation = list(measurement["assert"].items())[0]

        if fn == "mean":
            node_diff = args.get("node_diff")
            node = args.get("node")
            branch = args.get("branch")
            if branch:
                probe_ref = branch_sensors.get(branch)
                if probe_ref is None:
                    raise ValueError(f"No branch sensor found for '{branch}'")
                values = analysis.branches[probe_ref]
            elif node_diff:
                values = analysis[node_diff[0]] - analysis[node_diff[1]]
            else:
                values = analysis[node]
            value = float(values.mean())
        elif fn == "efficiency":
            input_node = args["input_node"]
            output_node = args["output_node"]
            current = args["I_out_A"]
            v_in = float(analysis[input_node].mean())
            v_out = float(analysis[output_node].mean())
            value = (v_out * current) / max(v_in * current, 1e-9) * 100
        else:
            value = float("nan")

        op, threshold = expectation
        threshold_value = float(str(threshold).rstrip("%"))
        if op == ">=":
            passed = value >= threshold_value
        elif op == "<=":
            passed = value <= threshold_value
        else:
            passed = False

        results.append({"id": mid, "value": value, "assert": measurement["assert"], "pass": passed})
    return results


def run_testbench(module_path: Path, testbench_path: Path) -> List[Dict[str, Any]]:
    data = json.loads(Path(testbench_path).read_text())
    module_globals = _load_pyspice_module(module_path)
    subckt_fn = _build_dut(module_globals)

    reports = []
    for use_case in data["use_cases"]:
        try:
            sim_end_ms = _determine_sim_end_ms(use_case)
            branch_sensors = _extract_branch_sensors(use_case)
            circuit = _create_circuit(subckt_fn, use_case, sim_end_ms, branch_sensors)
            simulator = circuit.simulator(temperature=use_case.get("given", {}).get("ambient", {}).get("temp_C", 25))
            analysis = simulator.transient(step_time=1 @ u_ms, end_time=sim_end_ms @ u_ms)
            measurements = _evaluate_measurements(analysis, use_case, branch_sensors)
            reports.append({"use_case": use_case["name"], "measurements": measurements})
        except Exception as exc:
            reports.append({"use_case": use_case["name"], "error": str(exc)})
    return reports


__all__ = ["run_testbench"]
