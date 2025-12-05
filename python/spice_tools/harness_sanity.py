"""Helpers to sanity-check SPICE harnesses using a dummy DUT.

This is intended for quick checks like:

    - Does the harness topology converge with a simple, well-behaved DUT?
    - Do node voltages / branch currents stay within a reasonable range?

It runs the harness with an auto-generated passive DUT instead of the real
design, using a short transient simulation, and reports basic metrics.

Typical usage from a notebook:

    from python.spice_tools.harness_sanity import harness_sanity_check

    result = harness_sanity_check(
        \"test_cases/case_3A_charger/spice/battery_protection/testcases/forward_conduction_drop_3A_60C.py\"
    )
    print(result)

If this fails to converge or produces absurd voltages/currents, the harness
topology is likely at fault rather than the real DUT model.
"""

from __future__ import annotations

import importlib.util
import inspect
import re
from pathlib import Path
from typing import Any, Dict, Tuple, cast, TYPE_CHECKING

import numpy as np

from python.spice_tools.testbench_runner import _collect_signals_from_analysis
from python.spice_tools.utils import run_transient, disable_inspice_cache, disable_skidl_file_logging

disable_inspice_cache()
disable_skidl_file_logging()

if TYPE_CHECKING:
    from skidl.pyspice import C, R # type: ignore


def make_dummy_dut(
    series_r_ohm: float = 0.1,
    shunt_r_ohm: float = 1e6,
    c_f: float = 1e-9,
):
    """
    Build a simple, well-behaved dummy DUT.

    The returned callable matches the real DUT's interface via **nodes.
    It:
      - Chooses a ground-like node by name (GND/VSS) or falls back to the first.
      - Connects small resistors between every pair of non-ground nodes.
      - Shunts each non-ground node to ground with a large resistor and small cap.

    This ensures:
      - No floating nodes.
      - Finite impedances between pins.
      - Linear, convergence-friendly behavior.
    """

    def _dummy_dut(**nodes):
        from skidl.pyspice import C, R  # type: ignore # Lazy import to avoid polluting global state

        if not nodes:
            return

        gnd_key = None
        for name in nodes:
            if re.search(r"(gnd|vss)", name, re.IGNORECASE):
                gnd_key = name
                break
        if gnd_key is None:
            gnd_key = next(iter(nodes))

        gnd = nodes[gnd_key]
        others = [nodes[k] for k in nodes if k != gnd_key]

        # Series resistors between every pair of non-ground nodes.
        for i in range(len(others)):
            for j in range(i + 1, len(others)):
                r = R(ref=f"R_DUMMY_SER_{i}_{j}", value=series_r_ohm)
                others[i] += r[1]
                others[j] += r[2]

        # Shunt each non-ground node to ground with a large resistor and cap.
        for idx, node in enumerate(others):
            r_shunt = R(ref=f"R_DUMMY_SHUNT_{idx}", value=shunt_r_ohm)
            node += r_shunt[1]
            gnd += r_shunt[2]

            if c_f > 0:
                c = C(ref=f"C_DUMMY_{idx}", value=c_f)
                node += c[1]
                gnd += c[2]

    return _dummy_dut


def harness_sanity_check(
    harness_path: str | Path,
    use_case_name: str | None = None,
    step_s: float = 1e-5,
    end_s: float = 5e-3,
    temp_c: float = 25.0,
    voltage_limit: float = 1e3,
    current_limit: float = 1e3,
) -> Dict[str, Any]:
    """
    Run a short transient using the harness with a dummy DUT and report metrics.

    Args:
        harness_path: Filesystem path to a harness module that exposes
            simulation_harness(use_case: dict, dut: callable)
            and returns (circuit, observables_dict).
        use_case_name: Optional use_case['name'] to pass into the harness.
            If omitted, defaults to harness file stem.
        step_s: Transient timestep in seconds for the sanity run.
        end_s: Transient end time in seconds for the sanity run.
        temp_c: Ambient temperature in degrees C for the sim.
        voltage_limit: Maximum allowed |V| on any node before flagging as bad.
        current_limit: Maximum allowed |I| on any branch before flagging as bad.

    Returns:
        Dict with fields:
            ok: bool, True if sim converged and metrics stayed within limits.
            max_abs_voltage: float, max |V| over all node signals.
            max_abs_current: float, max |I| over all branch signals.
            num_points: int, number of time samples.
            error: Optional string if the simulation failed.
    """
    harness_path = Path(harness_path)
    spec = importlib.util.spec_from_file_location(harness_path.stem, harness_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load harness module from {harness_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[arg-type]

    sim_harness = getattr(module, "simulation_harness", None)
    if sim_harness is None or not callable(sim_harness):
        raise RuntimeError(
            f"Harness module {harness_path} must define a callable "
            "simulation_harness(use_case: dict, dut: callable)"
        )

    sig = inspect.signature(sim_harness)
    if len(sig.parameters) != 2:
        raise RuntimeError(
            "harness_sanity_check currently expects simulation_harness(use_case, dut); "
            f"got signature {sig}"
        )

    if use_case_name is None:
        use_case_name = harness_path.stem

    use_case: Dict[str, Any] = {"name": use_case_name}
    dummy_dut = make_dummy_dut()

    error: str | None = None
    try:
        circuit, observables = cast(Tuple[Any, Any], sim_harness(use_case, dummy_dut))
        analysis = run_transient(
            circuit=circuit,
            title=f"harness_sanity:{use_case_name}",
            step_s=step_s,
            end_s=end_s,
            temp_c=temp_c,
        )
        times_s, signals = _collect_signals_from_analysis(analysis, observables)
    except Exception as exc:  # pragma: no cover - defensive
        return {
            "ok": False,
            "max_abs_voltage": float("nan"),
            "max_abs_current": float("nan"),
            "num_points": 0,
            "error": str(exc),
        }

    max_abs_v = 0.0
    max_abs_i = 0.0

    for key, vals in signals.items():
        arr = np.asarray(vals, dtype=float)
        if arr.size == 0:
            continue
        max_abs = float(np.nanmax(np.abs(arr)))
        if "->" in key:
            max_abs_i = max(max_abs_i, max_abs)
        else:
            max_abs_v = max(max_abs_v, max_abs)

    finite_times = np.isfinite(times_s).all()
    finite_signals = all(np.isfinite(np.asarray(v, dtype=float)).all() for v in signals.values())

    ok = (
        finite_times
        and finite_signals
        and max_abs_v <= voltage_limit
        and max_abs_i <= current_limit
    )

    return {
        "ok": bool(ok),
        "max_abs_voltage": max_abs_v,
        "max_abs_current": max_abs_i,
        "num_points": int(len(times_s)),
        "error": error,
    }


__all__ = ["make_dummy_dut", "harness_sanity_check"]

