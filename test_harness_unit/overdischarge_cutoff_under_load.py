from __future__ import annotations

import runpy
from pathlib import Path
from typing import Any, Dict, Tuple

import numpy as np
from skidl.pyspice import *  # noqa: F401,F403
from skidl.pyspice import Circuit, Net, Part

from python.spice_tools.testbench_runner import determine_end_ms
from python.spice_tools.utils import _configure_vin, _determine_step_s, run_transient


MODULE_PATH = (
    Path(__file__).resolve().parent.parent
    / "test_cases"
    / "case_3A_charger"
    / "spice"
    / "modules"
    / "battery_protection_pyspice.py"
)


def _build_dut(gnd: Net, vchg: Net, vpack_int: Net) -> None:
    dut = runpy.run_path(str(MODULE_PATH))["Battery_Protection_pyspice"]
    dut(GND=gnd, VBAT_CHG=vchg, VBAT_PACK=vpack_int)


def build_harness() -> tuple[Circuit, Part, Part, Part]:
    circuit = Circuit()
    with circuit:
        gnd = Net("0")
        vchg = Net("VBAT_CHG")
        vpack = Net("VBAT_PACK")
        vpack_int = Net("VBAT_PACK_INT")
        load_node = Net("LOAD")

        vin = PULSEV(
            ref="VIN",
            initial_value=0.0,
            pulsed_value=0.0,
            delay_time=0.0,
            rise_time=1e-9,
            fall_time=1e-9,
            pulse_width=1.0,
            period=2.0,
        )
        vin_node = Net("VIN_SRC")
        vin_node += vin["p"]
        gnd += vin["n"]
        l_src = L(ref="L_SRC", value=50e-9)
        r_src = R(ref="R_SRC", value=0.05)
        vin_node += l_src[1]
        n_src = Net("N_SRC")
        n_src += l_src[2]
        n_src += r_src[1]
        vchg += r_src[2]

        vbatt = V(ref="VBATT", dc_value=0.0)
        batt_pos = Net("BATT_POS")
        batt_pos += vbatt["p"]
        gnd += vbatt["n"]
        l_pack = L(ref="L_PACK", value=50e-9)
        r_esr = R(ref="R_PACK_ESR", value=0.05)
        vpack += l_pack[1]
        n_pack = Net("N_PACK")
        n_pack += l_pack[2]
        n_pack += r_esr[1]
        batt_pos += r_esr[2]

        probe_pc = V(ref="I_SENSE_PACK_CHG", dc_value=0 @ u_V)
        vpack += probe_pc["p"]
        vpack_int += probe_pc["n"]

        probe_pl = V(ref="I_SENSE_PACK_LOAD", dc_value=0 @ u_V)
        vpack += probe_pl["p"]
        load_node += probe_pl["n"]

        iload = PULSEI(
            ref="ILOAD",
            initial_value=0.0,
            pulsed_value=0.0,
            delay_time=0.0,
            rise_time=1e-9,
            fall_time=1e-9,
            pulse_width=1.0,
            period=2.0,
        )
        load_node += iload["p"]
        gnd += iload["n"]

        _build_dut(gnd=gnd, vchg=vchg, vpack_int=vpack_int)

    return circuit, vin, iload, vbatt


def build_harness_interface() -> tuple[Circuit, dict, dict]:
    circuit, vin, iload, vbatt = build_harness()
    stimuli = {"VIN": vin, "ILOAD": iload, "VBATT": vbatt}
    observables = {
        "node:VBAT_CHG": "VBAT_CHG",
        "node:VBAT_PACK": "VBAT_PACK",
        "branch:VBAT_PACK->VBAT_CHG": "vi_sense_pack_chg",
        "branch:VBAT_PACK->LOAD": "vi_sense_pack_load",
    }
    return circuit, stimuli, observables


def choose_sim_window(use_case: Dict[str, Any]) -> float:
    return determine_end_ms(use_case)


def choose_step(use_case: Dict[str, Any], end_ms: float) -> float:
    given = use_case.get("given", {})
    return _determine_step_s(given, end_ms)


def run_simulation(circuit: Circuit, use_case: Dict[str, Any], step_s: float, end_s: float, temp_c: float):
    return run_transient(
        circuit=circuit,
        title=use_case.get("name", ""),
        step_s=step_s,
        end_s=end_s,
        temp_c=temp_c,
    )


def collect_signals(analysis, observables: Dict[str, str]) -> Tuple[np.ndarray, Dict[str, np.ndarray]]:
    times_s = np.array([float(x) for x in analysis.time])
    signals: Dict[str, np.ndarray] = {}
    for logical, key in observables.items():
        if logical.startswith("node:"):
            signals[key] = np.array([float(v) for v in analysis[key]])
        elif logical.startswith("branch:"):
            branch_id = logical.split(":", 1)[1]
            signals[branch_id] = np.array([float(v) for v in analysis.branches[key]])
    return times_s, signals


USE_CASE: Dict[str, Any] = {
    "name": "overdischarge_cutoff_under_load",
    "mode": "auto",
    "given": {
        "VIN": {"dc_V": 0.0},
        "load": {"type": "const_current", "I_A": 3.0},
        "env": {"pack_esr_mohm": 50.0},
        "ambient": {"temp_C": 25.0},
        "faults": {"initial_battery_voc_V": 2.9},
    },
    "when": {"apply_load_at_ms": 2.0},
}


def simulation_harness(use_case: Dict[str, Any] | None = None) -> Tuple[np.ndarray, Dict[str, np.ndarray]]:
    uc = USE_CASE if use_case is None else use_case

    circuit, stimuli, observables = build_harness_interface()
    vin = stimuli["VIN"]
    iload = stimuli["ILOAD"]
    vbatt = stimuli["VBATT"]

    _configure_vin(vin, uc["given"]["VIN"])

    vbatt.dc_value = float(uc["given"].get("faults", {}).get("initial_battery_voc_V", 0.0))
    iload.initial_value = 0.0
    iload.pulsed_value = 3.0
    iload.delay_time = 0.002  # 2 ms
    iload.pulse_width = 1.0
    iload.period = 2.0

    temp_c = float(uc["given"].get("ambient", {}).get("temp_C", 25.0))
    end_ms = choose_sim_window(uc)
    step_s = choose_step(uc, end_ms)
    end_s = end_ms * 1e-3

    analysis = run_simulation(circuit, uc, step_s, end_s, temp_c)
    return collect_signals(analysis, observables)

