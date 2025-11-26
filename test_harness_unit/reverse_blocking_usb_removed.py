from typing import Any, Dict, Tuple
from skidl.pyspice import *  # noqa: F401,F403
from skidl.pyspice import Circuit, Net


def build_harness(GND: Net, VBAT_CHG: Net, VBAT_PACK: Net) -> None:
    """Extend DUT environment with charger source, battery model, and sense path."""
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
    GND += vin["n"]
    l_src = L(ref="L_SRC", value=50e-9)
    r_src = R(ref="R_SRC", value=0.05)
    vin_node += l_src[1]
    n_src = Net("N_SRC")
    n_src += l_src[2]
    n_src += r_src[1]
    VBAT_CHG += r_src[2]

    vbatt = V(ref="VBATT", dc_value=3.9)
    batt_pos = Net("BATT_POS")
    batt_pos += vbatt["p"]
    GND += vbatt["n"]
    l_pack = L(ref="L_PACK", value=50e-9)
    r_esr = R(ref="R_PACK_ESR", value=0.05)
    vpack = Net("VBAT_PACK")
    vpack += l_pack[1]
    n_pack = Net("N_PACK")
    n_pack += l_pack[2]
    n_pack += r_esr[1]
    batt_pos += r_esr[2]

    probe_pc = V(ref="I_SENSE_PACK_CHG", dc_value=0 @ u_V)
    vpack += probe_pc["p"]
    VBAT_PACK += probe_pc["n"]


def configure_observation_mapping() -> Dict[str, Dict[str, str]]:
    """Define observable signals required by the testbench measurements."""
    observables: Dict[str, Dict[str, str]] = {
        "nodes": {
            "VBAT_PACK": "VBAT_PACK",
        },
        "branches": {
            "VBAT_PACK->VBAT_CHG": "vi_sense_pack_chg",
        },
    }
    return observables


def simulation_harness(use_case: Dict[str, Any], dut) -> Tuple[Circuit, Dict[str, Dict[str, str]]]:
    """Single-use-case env builder for 'reverse_blocking_usb_removed'."""
    # Validate that this harness is only used with its matching use_case.
    if use_case.get("name") != "reverse_blocking_usb_removed":
        raise ValueError(f"Unexpected use_case for this harness: {use_case.get('name')!r}")

    # Fixed scaffold: build circuit, attach DUT, and call harness helpers.
    circuit = Circuit()
    with circuit:
        GND = Net("0")
        VBAT_CHG = Net("VBAT_CHG")
        VBAT_PACK = Net("VBAT_PACK_INT")

        dut(GND=GND, VBAT_CHG=VBAT_CHG, VBAT_PACK=VBAT_PACK)

        build_harness(GND=GND, VBAT_CHG=VBAT_CHG, VBAT_PACK=VBAT_PACK)

    observables = configure_observation_mapping()
    return circuit, observables
