"""
Generic SPICE test runner (Layer 1).

This module is intentionally DUT-agnostic:

- It knows nothing about SKiDL, PySpice, or any specific harness.
- It only understands:
    * a testbench spec (JSON-like dict) with use_cases / measurements
    * a harness_runner callable that, for a given use_case, returns:
        times_s: 1D np.ndarray of time points (seconds)
        signals: dict[str, np.ndarray] mapping signal names to waveforms

Signal names are arbitrary strings defined by the testbench + harness
contract. Measurement args (e.g. node, branch, node_diff, input_node)
refer to these names; the harness is responsible for populating them.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple, cast

import numpy as np
import importlib.util


@dataclass
class MeasurementResult:
    use_case: str
    measurement_id: str
    value: float
    op: str
    limit: float
    passed: bool


# Type: given a single use_case spec, build+simulate the harness and
# return time axis + named signals defined by the harness contract.
HarnessRunner = Callable[[Dict[str, Any]], Tuple[np.ndarray, Dict[str, np.ndarray]]]


def _window_to_range_s(
    window_ms: Any,
    default_start_ms: float,
    default_end_ms: float,
) -> Tuple[float, float]:
    if isinstance(window_ms, (int, float)):
        return default_start_ms * 1e-3, float(window_ms) * 1e-3
    if isinstance(window_ms, (list, tuple)) and len(window_ms) == 2:
        return float(window_ms[0]) * 1e-3, float(window_ms[1]) * 1e-3
    return default_start_ms * 1e-3, default_end_ms * 1e-3


def _in_band_fraction(
    times_s: np.ndarray,
    values: np.ndarray,
    lo: float,
    hi: float,
    window_s: Tuple[float, float],
) -> float:
    start_s, end_s = window_s
    mask = (times_s >= start_s) & (times_s <= end_s)
    if np.count_nonzero(mask) < 2:
        return 0.0
    t = times_s[mask]
    v = values[mask]
    inside = (v >= lo) & (v <= hi)
    dt = np.diff(t)
    inside_time = float(np.sum(dt * inside[:-1]))
    total_time = float(np.sum(dt))
    return inside_time / total_time if total_time > 0 else 0.0


def _first_settling_time(
    times_s: np.ndarray,
    values: np.ndarray,
    target: float,
    band_pct: float,
    window_s: Tuple[float, float],
) -> float | None:
    start_s, end_s = window_s
    mask = (times_s >= start_s) & (times_s <= end_s)
    t = times_s[mask]
    v = values[mask]
    if t.size == 0:
        return None
    band = abs(target) * (band_pct / 100.0)
    if band == 0:
        band = 1e-9
    lo = target - band
    hi = target + band
    for i in range(len(v)):
        if np.all((v[i:] >= lo) & (v[i:] <= hi)):
            return float(t[i])
    return None


def _parse_spice_number(val: Any) -> float:
    if isinstance(val, (int, float)):
        return float(val)

    s = str(val).strip()
    if not s:
        raise ValueError(f"Empty numeric string: {val!r}")

    token = s.split()[0].replace("Ω", "").replace("Ω", "")
    m = re.match(
        r"^([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)([a-zA-Z]*)$",
        token,
    )
    if not m:
        # Best effort fallback.
        return float(token)

    num = float(m.group(1))
    suffix = m.group(2)
    if not suffix:
        return num

    scale = suffix[0]
    factors = {
        "f": 1e-15,
        "p": 1e-12,
        "n": 1e-9,
        "u": 1e-6,
        "m": 1e-3,
        "k": 1e3,
        "K": 1e3,
        "M": 1e6,
        "g": 1e9,
        "G": 1e9,
        "t": 1e12,
        "T": 1e12,
    }
    factor = factors.get(scale, 1.0)
    return num * factor


def _parse_limit(val: Any) -> float:
    return _parse_spice_number(val)


def determine_end_ms(use_case: Dict[str, Any]) -> float:
    """
    Heuristic to choose simulation end time in ms from a use_case spec.

    Uses any observe_window_ms / stabilize_ms / apply_load_at_ms / window_ms
    hints it can find; intended to be reused by harness adapters.
    """
    when = use_case.get("when", {})
    then = use_case.get("then", {})

    candidates: List[float] = []

    observe = when.get("observe_window_ms")
    if isinstance(observe, (int, float)):
        candidates.append(float(observe))
    elif isinstance(observe, (list, tuple)) and len(observe) == 2:
        candidates.append(float(observe[1]))

    stabilize = when.get("stabilize_ms")
    if isinstance(stabilize, (int, float)):
        candidates.append(float(stabilize))

    apply_at = when.get("apply_load_at_ms")
    if isinstance(apply_at, (int, float)):
        candidates.append(float(apply_at))

    for m in then.get("measurements", []):
        win = m.get("args", {}).get("window_ms")
        if isinstance(win, (int, float)):
            candidates.append(float(win))
        elif isinstance(win, (list, tuple)) and len(win) == 2:
            candidates.append(float(win[1]))

    if not candidates:
        return 10.0

    end_ms = max(candidates)
    return end_ms * 1.2


def _eval_measurement_on_signals(
    use_case_name: str,
    measurement: Dict[str, Any],
    times_s: np.ndarray,
    signals: Dict[str, np.ndarray],
    default_end_ms: float,
) -> MeasurementResult:
    mid = measurement["id"]
    fn = measurement["fn"]
    args = measurement.get("args", {})
    asserts = measurement.get("assert", {})
    if len(asserts) != 1:
        raise ValueError(f"Measurement {mid}: expected single assert op, got {asserts}")
    op, limit_raw = next(iter(asserts.items()))
    limit = _parse_limit(limit_raw)

    default_window = (0.0, default_end_ms)

    def window_from_args() -> Tuple[float, float]:
        win_ms = args.get("window_ms", default_window[1])
        return _window_to_range_s(win_ms, default_window[0], default_window[1])

    value: float

    if fn == "mean":
        win_s = window_from_args()
        start_s, end_s = win_s
        mask = (times_s >= start_s) & (times_s <= end_s)
        if "branch" in args:
            vals = signals[args["branch"]]
        elif "node_diff" in args:
            n1, n2 = args["node_diff"]
            vals = signals[n1] - signals[n2]
        else:
            node = args["node"]
            vals = signals[node]
        vals_win = vals[mask]
        value = float(vals_win.mean()) if vals_win.size else float("nan")

    elif fn == "overshoot_pp":
        win_s = window_from_args()
        start_s, end_s = win_s
        mask = (times_s >= start_s) & (times_s <= end_s)
        node = args["node"]
        target = float(args["target_V"])
        vals = signals[node][mask]
        if vals.size:
            value = max(0.0, float(vals.max() - target))
        else:
            value = float("nan")

    elif fn == "undershoot_pp":
        win_s = window_from_args()
        start_s, end_s = win_s
        mask = (times_s >= start_s) & (times_s <= end_s)
        node = args["node"]
        target = float(args["target_V"])
        vals = signals[node][mask]
        if vals.size:
            value = max(0.0, float(target - vals.min()))
        else:
            value = float("nan")

    elif fn == "in_band_time":
        win_s = window_from_args()
        node = args["node"]
        lo = float(args["lo_V"])
        hi = float(args["hi_V"])
        vals = signals[node]
        value = _in_band_fraction(times_s, vals, lo, hi, win_s)

    elif fn == "efficiency":
        win_s = window_from_args()
        start_s, end_s = win_s
        mask = (times_s >= start_s) & (times_s <= end_s)
        input_node = args["input_node"]
        output_node = args["output_node"]
        i_out = float(args["I_out_A"])
        vin_vals = signals[input_node][mask]
        vout_vals = signals[output_node][mask]
        vchg_mean = float(vin_vals.mean()) if vin_vals.size else 0.0
        vpack_mean = float(vout_vals.mean()) if vout_vals.size else 0.0
        pin = max(vchg_mean * i_out, 1e-9)
        pout = vpack_mean * i_out
        value = pout / pin * 100.0

    elif fn == "settling_time":
        win_ms = args.get("window_ms", default_end_ms)
        win_s = _window_to_range_s(win_ms, 0.0, default_end_ms)
        if "branch" in args:
            vals = signals[args["branch"]]
            target = float(args["target_A"])
        else:
            vals = signals[args["node"]]
            target = float(args["target_V"])
        band_pct = float(args.get("band_pct", 5.0))
        t_settle = _first_settling_time(times_s, vals, target, band_pct, win_s)
        # Return in seconds; assertions can use "ms" suffix which is
        # parsed into seconds by _parse_spice_number.
        value = float(t_settle) if t_settle is not None else float("inf")

    elif fn == "ripple_pp":
        win_s = window_from_args()
        start_s, end_s = win_s
        mask = (times_s >= start_s) & (times_s <= end_s)
        node = args["node"]
        vals = signals[node][mask]
        if vals.size:
            value = float(vals.max() - vals.min())
        else:
            value = float("nan")

    elif fn == "sample_at":
        node = args["node"]
        at_s = float(args["at_ms"]) * 1e-3
        idx = int(np.argmin(np.abs(np.asarray(times_s) - at_s)))
        vals = signals[node]
        if vals.size == 0:
            value = float("nan")
        else:
            idx = max(0, min(idx, vals.size - 1))
            value = float(vals[idx])

    elif fn == "time_to_cross":
        node = args["node"]
        level = float(args["level_V"])
        direction = args["direction"]
        win_s = _window_to_range_s(args.get("window_ms", default_end_ms), 0.0, default_end_ms)
        start_s, end_s = win_s
        mask = (times_s >= start_s) & (times_s <= end_s)
        t = times_s[mask]
        v = signals[node][mask]
        t_cross: float | None = None
        if t.size >= 2:
            for i in range(len(v) - 1):
                v0, v1 = v[i], v[i + 1]
                t0, t1 = t[i], t[i + 1]
                if direction == "rising" and v0 < level <= v1:
                    dv = v1 - v0
                    frac = 0.0 if dv == 0 else (level - v0) / dv
                    t_cross = float(t0 + frac * (t1 - t0))
                    break
                if direction == "falling" and v0 > level >= v1:
                    dv = v1 - v0
                    frac = 0.0 if dv == 0 else (level - v0) / dv
                    t_cross = float(t0 + frac * (t1 - t0))
                    break
        value = float(t_cross) if t_cross is not None else float("inf")

    else:
        raise ValueError(f"Unsupported measurement fn: {fn}")

    if op == "<=":
        passed = value <= limit
    elif op == ">=":
        passed = value >= limit
    else:
        raise ValueError(f"Unsupported assert op: {op}")

    return MeasurementResult(
        use_case=use_case_name,
        measurement_id=mid,
        value=value,
        op=op,
        limit=limit,
        passed=passed,
    )


def run_testbench(
    testbench: Dict[str, Any],
    harness_runner: HarnessRunner,
    mode: str = "auto",
) -> List[MeasurementResult]:
    """
    Fully generic Layer-1 runner.

    Args:
        testbench: Dict loaded from a JSON schema-like file, expected to have
            a ``"use_cases"`` list; each use_case is passed verbatim to the
            harness_runner.
        harness_runner: Callable that, for a given ``use_case`` dict,
            returns ``(times_s, signals)``.
        mode: If not None, only run use_cases whose ``"mode"`` equals this.

    Returns:
        Flat list of MeasurementResult for all executed use-cases.
    """
    results: List[MeasurementResult] = []
    for uc in testbench.get("use_cases", []):
        if mode is not None and uc.get("mode") != mode:
            continue

        times_s, signals = harness_runner(uc)
        end_ms = determine_end_ms(uc)

        then = uc.get("then", {})
        for m in then.get("measurements", []):
            res = _eval_measurement_on_signals(
                uc["name"],
                m,
                times_s,
                signals,
                end_ms,
            )
            results.append(res)

    return results


def run_testbench_file(
    schema_path: Path | str,
    harness_path: Path | str,
    mode: str = "auto",
) -> List[MeasurementResult]:
    """
    Load testbench JSON and execute all matching use-cases via a harness file.

    The harness file must be a Python module that defines a callable:

        simulation_harness(use_case: dict) -> (times_s, signals)

    where:
        - times_s is a 1D np.ndarray of time points in seconds.
        - signals is a dict[str, np.ndarray] mapping signal names
          (as used in the testbench schema) to waveforms.
    """
    schema_path = Path(schema_path)
    data = json.loads(schema_path.read_text())

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
            "simulation_harness(use_case: dict) -> (times_s, signals)"
        )

    return run_testbench(data, cast(HarnessRunner, sim_harness), mode=mode)


__all__ = ["MeasurementResult", "run_testbench_file"]
