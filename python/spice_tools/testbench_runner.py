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
import traceback
import inspect
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

import numpy as np

import skidl
import skidl.part as skidl_part
from skidl.pyspice import *
from skidl.pyspice import Circuit, Net
from skidl.tools.skidl.libs import pyspice_sklib
from skidl.tools.spice.spice import gen_netlist

from InSpice.Spice.Simulator import Simulator


_ORIG_PART_INIT = skidl_part.Part.__init__


def _patched_part_init(self, *args, **kwargs):
    dest = kwargs.get("dest")
    if isinstance(dest, str) and dest.upper() == "INSTANCE":
        kwargs["dest"] = skidl_part.NETLIST
        lib_name = kwargs.get("lib")
        if isinstance(lib_name, str) and lib_name.lower() == "pyspice":
            kwargs["lib"] = pyspice_sklib.pyspice_lib
        # Normalize value strings like "0.015Ω 1% 1W" to numeric
        val = kwargs.get("value")
        if val is not None:
            try:
                cleaned = str(val).split()[0]
                cleaned = cleaned.replace("Ω", "ohm").replace("Ω", "ohm")
                numeric = _parse_quantity(cleaned)
                kwargs["value"] = numeric
            except Exception:
                pass

    obj = _ORIG_PART_INIT(self, *args, **kwargs)
    # Ensure value stored is numeric to avoid SPICE parsing stray tokens.
    try:
        cleaned = str(self.value).split()[0]
        cleaned = cleaned.replace("Ω", "ohm").replace("Ω", "ohm")
        self.value = _parse_quantity(cleaned)
    except Exception:
        pass
    return obj


if getattr(skidl_part.Part.__init__, "__name__", "") != "_patched_part_init":
    skidl_part.Part.__init__ = _patched_part_init


skidl.config.backup_lib = pyspice_sklib.pyspice_lib
skidl.config.query_backup_lib = True


def _load_pyspice_module(module_path: Path):
    return runpy.run_path(str(module_path))


def _build_dut(module_globals: Dict[str, Any], subckt_name: Optional[str] = None):
    if subckt_name is None:
        # default to any @SubCircuit ending with _pyspice
        for key, value in module_globals.items():
            if callable(value) and key.endswith("_pyspice"):
                return value
        raise RuntimeError("No *_pyspice subcircuit found in module.")
    fn = module_globals.get(subckt_name)
    if fn is None:
        raise RuntimeError(f"{subckt_name} not found in module.")
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


def _determine_step_ms(sim_end_ms: float) -> float:
    # Aim for a few thousand points; clamp to avoid overly small steps.
    return max(0.001, min(0.05, sim_end_ms / 2000.0))


def _format_error(exc: Exception) -> str:
    msg = str(exc)
    if msg:
        return f"{exc.__class__.__name__}: {msg}"
    return f"{exc.__class__.__name__}: {traceback.format_exc().strip()}"


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
        start_v = float(start_value)
        stop_v = float(stop_value)
        delay_ms = float(ramp.get("delay_ms", 0.0))
        rise_ms = float(ramp.get("rise_ms", 0.0))
        delay_s = delay_ms / 1000.0
        rise_s = rise_ms / 1000.0
        hold_s = max(sim_end_ms, delay_ms + rise_ms + 1.0) / 1000.0
        # Use PULSE: V1 start_v, V2 stop_v, TD delay_s, TR rise_s, TF small, PW hold_s, PER large
        src = PULSEV(
            ref="VIN",
            initial_value=start_v,
            pulsed_value=stop_v,
            delay_time=delay_s,
            rise_time=rise_s if rise_s > 0 else 1e-9,
            fall_time=1e-9,
            pulse_width=hold_s,
            period=hold_s + 1.0,
        )
    elif "step" in vin_cfg:
        step_cfg = vin_cfg["step"]
        pre_value = step_cfg.get("from_V", vin_cfg.get("dc_V", 0.0)) or 0.0
        post_value = step_cfg.get("to_V", pre_value) or pre_value
        pre_v = float(pre_value)
        post_v = float(post_value)
        at_ms = float(step_cfg.get("at_ms", 0.0))
        at_s = at_ms / 1000.0
        hold_s = max(sim_end_ms, at_ms + 1.0) / 1000.0
        src = PULSEV(
            ref="VIN",
            initial_value=pre_v,
            pulsed_value=post_v,
            delay_time=at_s,
            rise_time=1e-9,
            fall_time=1e-9,
            pulse_width=hold_s,
            period=hold_s + 1.0,
        )
    elif "ac_ripple_pp_V" in vin_cfg:
        dc_v = float(vin_cfg.get("dc_V", 0.0))
        amp = float(vin_cfg["ac_ripple_pp_V"]) / 2.0
        freq_hz = float(vin_cfg.get("ac_freq_kHz", 0.0)) * 1e3
        src = SINEV(ref="VIN", dc_offset=dc_v, amplitude=amp, frequency=freq_hz)
    elif "dc_V" in vin_cfg:
        src = V(ref="VIN", dc_value=float(vin_cfg["dc_V"]))
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
        current = float(load_cfg["I_A"])
        apply_ms = when_cfg.get("apply_load_at_ms")
        load_net = vpack
        if needs_load_node:
            load_net = nets.get("LOAD")
            if load_net is None:
                load_net = Net("LOAD")
                nets["LOAD"] = load_net
        if isinstance(apply_ms, (int, float)):
            delay_s = float(apply_ms) / 1000.0
            hold_s = max(sim_end_ms, float(apply_ms) + 1.0) / 1000.0
            load = PULSEI(
                ref="ILOAD",
                initial_value=0.0,
                pulsed_value=current,
                delay_time=delay_s,
                rise_time=1e-9,
                fall_time=1e-9,
                pulse_width=hold_s,
                period=hold_s + 1.0,
            )
        else:
            load = I(ref="ILOAD", dc_value=current)
        _connect_two_terminal(load, load_net, gnd)
    elif load_cfg["type"] == "battery_model":
        vbatt = V(ref="BAT", dc_value=float(load_cfg.get("voc_V", 4.0)))
        _connect_two_terminal(vbatt, vpack, gnd)
        esr = R(ref="BAT_ESR", value=float(load_cfg.get("esr_ohm", 0.05)))
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


def _parse_quantity(value: Any, quantity_kind: Optional[str] = None) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        raise ValueError(f"Unsupported quantity type: {type(value)}")
    text = value.strip()
    if not text:
        raise ValueError("Empty quantity string")

    import re

    match = re.match(r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)([A-Za-z]*)$", text)
    if not match:
        raise ValueError(f"Cannot parse quantity '{value}'")
    number = float(match.group(1))
    suffix = match.group(2)

    factor = 1.0
    unit = ""
    prefix_table = [
        ("meg", 1e6),
        ("MEG", 1e6),
        ("M", 1e6),
        ("G", 1e9),
        ("K", 1e3),
        ("k", 1e3),
        ("m", 1e-3),
        ("U", 1e-6),
        ("u", 1e-6),
        ("N", 1e-9),
        ("n", 1e-9),
        ("P", 1e-12),
        ("p", 1e-12),
    ]
    for key, val in prefix_table:
        if suffix.startswith(key):
            factor = val
            unit = suffix[len(key) :]
            break
    else:
        unit = suffix

    unit_lower = unit.lower()

    if unit_lower in ("", "v", "a", "ohm", "r", "f", "h"):
        return number * factor
    if unit_lower == "s":
        return number * factor
    if unit_lower == "ms":
        return number * 1e-3
    # Fall back to honoring prefix only if quantity kind expects time.
    if quantity_kind == "time":
        return number * factor
    raise ValueError(f"Unsupported unit in quantity '{value}'")


def _parse_branch(branch_name: str) -> Tuple[str, str]:
    parts = [p.strip() for p in branch_name.split("->", 1)]
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError(f"Invalid branch identifier '{branch_name}'")
    return parts[0], parts[1]


def _collect_nodes(interfaces: Dict[str, Any], use_case: Dict[str, Any], branch_sensors: Dict[str, str]) -> set[str]:
    nodes: set[str] = set()
    nodes.update(interfaces.get("inputs", []))
    nodes.update(interfaces.get("outputs", []))

    # Measurements may reference nodes/branches.
    for meas in use_case.get("then", {}).get("measurements", []):
        args = meas.get("args", {})
        branch = args.get("branch")
        if branch:
            start, end = _parse_branch(branch)
            nodes.update([start, end])
        node = args.get("node")
        if node:
            nodes.add(node)
        node_diff = args.get("node_diff") or []
        nodes.update(node_diff)
        if meas.get("fn") == "efficiency":
            if args.get("input_node"):
                nodes.add(args["input_node"])
            if args.get("output_node"):
                nodes.add(args["output_node"])

    # Branch sensors already parsed
    for branch in branch_sensors.keys():
        start, end = _parse_branch(branch)
        nodes.update([start, end])

    # Allow load probe node if measurement refers to LOAD
    return nodes


def _resolve_ground(nodes: set[str]) -> str:
    if "GND" in nodes:
        return "GND"
    if "0" in nodes:
        return "0"
    nodes.add("GND")
    return "GND"


def _call_subckt_with_nets(subckt_fn, nets: Dict[str, Net]):
    sig = inspect.signature(subckt_fn)
    kwargs = {}
    for name in sig.parameters:
        if name not in nets:
            raise ValueError(f"Net '{name}' required by subcircuit but not provided.")
        kwargs[name] = nets[name]
    return subckt_fn(**kwargs)


def _create_circuit(subckt_fn, interfaces: Dict[str, Any], use_case, sim_end_ms, branch_sensors: Dict[str, str]):
    circuit = Circuit()
    skidl.config.backup_lib = pyspice_sklib.pyspice_lib
    skidl.config.query_backup_lib = True

    with circuit:
        nodes = _collect_nodes(interfaces, use_case, branch_sensors)
        gnd_name = _resolve_ground(nodes)
        nets: Dict[str, Net] = {name: Net(name) for name in nodes}
        if gnd_name != "0" and "0" not in nets:
            nets["0"] = nets[gnd_name]

        vin_cfg = use_case["given"]["VIN"]
        load_cfg = use_case["given"]["load"]

        if not interfaces.get("inputs"):
            raise ValueError("Testbench interfaces.inputs is empty")
        vin_pos = nets[interfaces["inputs"][0]]
        gnd = nets[gnd_name]
        _build_vin_source(vin_pos, gnd, vin_cfg, sim_end_ms)

        if not interfaces.get("outputs"):
            raise ValueError("Testbench interfaces.outputs is empty")
        load_net = nets[interfaces["outputs"][0]]
        _build_load(load_net, gnd, load_cfg, use_case.get("when", {}) or {}, sim_end_ms, nets, branch_sensors)

        for branch_name, probe_ref in branch_sensors.items():
            start_node, end_node = _parse_branch(branch_name)
            try:
                start_net = nets[start_node]
                end_net = nets[end_node]
            except KeyError as exc:
                raise ValueError(f"Unknown net '{exc.args[0]}' in branch '{branch_name}'") from exc
            probe = V(ref=probe_ref, dc_value=0 @ u_V)
            _connect_two_terminal(probe, start_net, end_net)

        _call_subckt_with_nets(subckt_fn, nets)

    # Sanitize part values to numeric to avoid ngspice model parsing issues.
    for part in circuit.parts:
        try:
            import re
            raw = str(part.value)
            match = re.search(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", raw)
            if match:
                cleaned = match.group(0)
                part.value = _parse_quantity(cleaned)
        except Exception:
            continue

    return circuit


def _get_time_axis_seconds(analysis) -> np.ndarray:
    return np.array([float(t) for t in analysis.time])


def _window_indices(times_s: np.ndarray, window_ms: Optional[Tuple[float, float]]):
    if window_ms is None:
        return np.ones_like(times_s, dtype=bool)
    start_ms, end_ms = window_ms
    start_s, end_s = start_ms / 1000.0, end_ms / 1000.0
    return (times_s >= start_s) & (times_s <= end_s)


def _extract_series(analysis, branch_sensors: Dict[str, str], args: Dict[str, Any]) -> np.ndarray:
    branch = args.get("branch")
    node_diff = args.get("node_diff")
    if branch:
        probe_ref = branch_sensors.get(branch)
        if probe_ref is None:
            raise ValueError(f"No branch sensor found for '{branch}'")
        data = analysis.branches[probe_ref]
        return np.array([float(v) for v in data])
    if node_diff:
        data = analysis[node_diff[0]] - analysis[node_diff[1]]
        return np.array([float(v) for v in data])
    node = args.get("node")
    if node is None:
        raise ValueError("Measurement args require 'node', 'node_diff', or 'branch'")
    data = analysis[node]
    return np.array([float(v) for v in data])


def _first_settling_time(times_s: np.ndarray, values: np.ndarray, target: float, band_pct: float) -> Optional[float]:
    if np.isnan(target):
        return None
    band = abs(target) * (band_pct / 100.0)
    if band == 0:
        band = 1e-9
    lower = target - band
    upper = target + band
    for idx in range(len(values)):
        if np.all((values[idx:] >= lower) & (values[idx:] <= upper)):
            return times_s[idx]
    return None


def _time_to_cross(times_s: np.ndarray, values: np.ndarray, level: float, direction: str) -> Optional[float]:
    for i in range(1, len(values)):
        prev = values[i - 1]
        curr = values[i]
        if direction == "rising" and prev < level <= curr:
            # Linear interpolation
            frac = (level - prev) / max(curr - prev, 1e-12)
            return times_s[i - 1] + frac * (times_s[i] - times_s[i - 1])
        if direction == "falling" and prev > level >= curr:
            frac = (prev - level) / max(prev - curr, 1e-12)
            return times_s[i - 1] + frac * (times_s[i] - times_s[i - 1])
    return None


def _evaluate_measurements(analysis, use_case, branch_sensors: Dict[str, str]):
    times_s = _get_time_axis_seconds(analysis)
    results = []
    for measurement in use_case["then"]["measurements"]:
        mid = measurement["id"]
        fn = measurement["fn"]
        args = measurement.get("args", {})
        expectation = list(measurement["assert"].items())[0]

        window = _normalize_time_window(args.get("window_ms"))
        mask = _window_indices(times_s, window)

        if fn == "mean":
            values = _extract_series(analysis, branch_sensors, args)
            value = float(values[mask].mean())
        elif fn == "efficiency":
            current = float(args["I_out_A"])
            vin_values = np.array([float(v) for v in analysis[args["input_node"]]])
            vout_values = np.array([float(v) for v in analysis[args["output_node"]]])
            v_in = float(vin_values[mask].mean())
            v_out = float(vout_values[mask].mean())
            denom = max(v_in * current, 1e-9)
            value = (v_out * current) / denom * 100.0
        elif fn == "overshoot_pp":
            values = _extract_series(analysis, branch_sensors, args)[mask]
            target = float(args["target_V"])
            value = max(0.0, float(values.max() - target))
        elif fn == "undershoot_pp":
            values = _extract_series(analysis, branch_sensors, args)[mask]
            target = float(args["target_V"])
            value = max(0.0, float(target - values.min()))
        elif fn == "ripple_pp":
            values = _extract_series(analysis, branch_sensors, args)[mask]
            value = float(values.max() - values.min())
        elif fn == "sample_at":
            values = _extract_series(analysis, branch_sensors, args)
            target_time_ms = float(args["at_ms"])
            target_time_s = target_time_ms / 1000.0
            idx = int(np.abs(times_s - target_time_s).argmin())
            value = float(values[idx])
        elif fn == "settling_time":
            values = _extract_series(analysis, branch_sensors, args)
            window_ms = _normalize_time_window(args.get("window_ms"))
            settle_mask = _window_indices(times_s, window_ms)
            band_pct = float(args.get("band_pct", 5.0))
            if "target_V" in args:
                target = float(args["target_V"])
            elif "target_A" in args:
                target = float(args["target_A"])
            else:
                target = float("nan")
            t = _first_settling_time(times_s[settle_mask], values[settle_mask], target, band_pct)
            value = t * 1000.0 if t is not None else float("inf")  # report in ms
        elif fn == "time_to_cross":
            values = _extract_series(analysis, branch_sensors, args)
            window_ms = _normalize_time_window(args.get("window_ms"))
            mask = _window_indices(times_s, window_ms)
            t = _time_to_cross(times_s[mask], values[mask], float(args["level_V"]), args["direction"])
            value = t * 1000.0 if t is not None else float("inf")  # report in ms
        elif fn == "in_band_time":
            values = _extract_series(analysis, branch_sensors, args)
            lo = float(args["lo_V"])
            hi = float(args["hi_V"])
            times = times_s[mask]
            vals = values[mask]
            if len(times) < 2:
                fraction = 0.0
            else:
                inside = (vals >= lo) & (vals <= hi)
                dt = np.diff(times)
                inside_time = float(np.sum(dt * inside[:-1]))
                total_time = float(np.sum(dt))
                fraction = inside_time / total_time if total_time > 0 else 0.0
            value = fraction
        else:
            raise ValueError(f"Unsupported measurement fn '{fn}'")

        op, threshold = expectation
        quantity_kind = "time" if fn in ("settling_time", "time_to_cross") else None
        threshold_value = _parse_quantity(threshold, quantity_kind=quantity_kind)
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
    # Try to match module name from testbench; fallback to any *_pyspice function.
    preferred = f"{data.get('module', '')}_pyspice" if data.get("module") else None
    subckt_fn = _build_dut(module_globals, subckt_name=preferred)

    reports = []
    for use_case in data["use_cases"]:
        try:
            mode = use_case.get("mode", "auto")
            if mode != "auto":
                continue  # silently skip complex modes for now
            sim_end_ms = _determine_sim_end_ms(use_case)
            branch_sensors = _extract_branch_sensors(use_case)
            circuit = _create_circuit(subckt_fn, data.get("interfaces", {}), use_case, sim_end_ms, branch_sensors)
            spice_circuit = gen_netlist(circuit, title=use_case.get("name", ""))
            simulator = Simulator.factory()
            simulation = simulator.simulation(
                spice_circuit, temperature=use_case.get("given", {}).get("ambient", {}).get("temp_C", 25)
            )
            step_ms = _determine_step_ms(sim_end_ms)
            step_s = step_ms / 1000.0
            end_s = sim_end_ms / 1000.0
            analysis = simulation.transient(step_time=step_s, end_time=end_s)
            measurements = _evaluate_measurements(analysis, use_case, branch_sensors)
            reports.append({"use_case": use_case["name"], "measurements": measurements})
        except Exception as exc:
            reports.append({"use_case": use_case.get("name", ""), "error": _format_error(exc)})
    return reports


__all__ = ["run_testbench"]
