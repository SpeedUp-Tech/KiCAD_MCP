from __future__ import annotations

import runpy
from pathlib import Path
from typing import Any, Dict, Tuple

from skidl.pyspice import *  # noqa: F401,F403
from skidl.pyspice import Circuit, Net, Part

from python.spice_tools.utils import _configure_vin


MODULE_PATH = (
    Path(__file__).resolve().parent.parent
    / "test_cases"
    / "case_3A_charger"
    / "spice"
    / "modules"
    / "battery_protection_pyspice.py"
)


def simulation_harness(use_case: Dict[str, Any]) -> Tuple[Circuit, Dict[str, str]]:
    """Single-use-case env builder for 'reverse_blocking_usb_removed'."""
    # Validate that this harness is only used with its matching use_case.
    if use_case.get("name") != "reverse_blocking_usb_removed":
        raise ValueError(f"Unexpected use_case for this harness: {use_case.get('name')!r}")

    # Build DUT environment: charger side source, battery model, and sense path.
    circuit = Circuit()
    with circuit:
        gnd = Net("0")
        vchg = Net("VBAT_CHG")
        vpack = Net("VBAT_PACK")
        vpack_int = Net("VBAT_PACK_INT")

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

        dut = runpy.run_path(str(MODULE_PATH))["Battery_Protection_pyspice"]
        dut(GND=gnd, VBAT_CHG=vchg, VBAT_PACK=vpack_int)

    # Configure stimulus from use_case: VIN shape and battery open-circuit voltage.
    _configure_vin(vin, use_case["given"]["VIN"])
    vbatt.dc_value = float(use_case["given"]["load"].get("voc_V", 0.0))

    # Collect observable signals required by the testbench measurements.
    observables = {
        "node:VBAT_PACK": "VBAT_PACK",
        "branch:VBAT_PACK->VBAT_CHG": "vi_sense_pack_chg",
    }

    return circuit, observables
