"""Shared helpers for PySpice/SKiDL harnesses."""

from __future__ import annotations

import io
import re
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any, Dict, List, Sequence, TYPE_CHECKING

import skidl.part as skidl_part
from skidl.tools.skidl.libs import pyspice_sklib

if TYPE_CHECKING:
    from skidl.pyspice import Circuit

_ORIG_PART_INIT = skidl_part.Part.__init__


def _patched_part_init(self, *args, **kwargs):
    if kwargs.get("dest") == "INSTANCE":
        kwargs["dest"] = skidl_part.NETLIST
    if kwargs.get("lib") == "pyspice":
        kwargs["lib"] = pyspice_sklib.pyspice_lib
    return _ORIG_PART_INIT(self, *args, **kwargs)


if getattr(skidl_part.Part.__init__, "__name__", "") != "_patched_part_init":
    skidl_part.Part.__init__ = _patched_part_init


def _coerce_spice_value(val):
    s = str(val).strip()
    if not s:
        return val

    token = s.split()[0].replace("Ω", "").replace("Ω", "")
    m = re.match(r"^([+-]?(?:\d+(?:\.\d*)?|\\.\\d+)(?:[eE][+-]?\\d+)?)([a-zA-Z]*)$", token)
    if not m:
        return val

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


def _sanitize_values(circuit: Circuit) -> None:
    for p in circuit.parts:
        try:
            val = getattr(p, "value", None)
            if val is not None:
                setattr(p, "value", _coerce_spice_value(val))
        except Exception:
            continue


def run_transient(circuit: "Circuit", title: str, step_s: float, end_s: float, temp_c: float):
    from InSpice.Spice.Simulator import Simulator
    from skidl.tools.spice.spice import gen_netlist

    _sanitize_values(circuit)
    netlist = gen_netlist(circuit, title=title)
    sim: Any = Simulator.factory().simulation(netlist, temperature=temp_c)
    return sim.transient(step_time=step_s, end_time=end_s)


def _determine_step_s(given: Dict[str, Any], end_ms: float) -> float:
    vin_cfg = given.get("VIN", {})
    if "ac_freq_kHz" in vin_cfg:
        freq_hz = float(vin_cfg["ac_freq_kHz"]) * 1e3
        period_s = 1.0 / freq_hz
        return period_s / 200.0
    end_s = end_ms * 1e-3
    target_points = 2000.0
    step = end_s / target_points
    return max(step, 1e-7)


def _configure_vin(source, vin_cfg: Dict[str, Any]) -> None:
    if "ramp" in vin_cfg:
        r = vin_cfg["ramp"]
        source.initial_value = float(r["start_V"])
        source.pulsed_value = float(r["stop_V"])
        source.delay_time = float(r.get("delay_ms", 0.0)) * 1e-3
        source.rise_time = float(r["rise_ms"]) * 1e-3
        source.fall_time = 1e-9
        source.pulse_width = 1.0
        source.period = 2.0
        return

    if "step" in vin_cfg:
        s = vin_cfg["step"]
        source.initial_value = float(s["from_V"])
        source.pulsed_value = float(s["to_V"])
        source.delay_time = float(s["at_ms"]) * 1e-3
        source.rise_time = 1e-9
        source.fall_time = 1e-9
        source.pulse_width = 1.0
        source.period = 2.0
        return

    if "dc_V" in vin_cfg and "ac_ripple_pp_V" in vin_cfg:
        dc_v = float(vin_cfg["dc_V"])
        pp_v = float(vin_cfg["ac_ripple_pp_V"])
        freq_hz = float(vin_cfg["ac_freq_kHz"]) * 1e3
        v1 = dc_v - pp_v / 2.0
        v2 = dc_v + pp_v / 2.0
        period = 1.0 / freq_hz
        source.initial_value = v1
        source.pulsed_value = v2
        source.delay_time = 0.0
        source.rise_time = period * 0.01
        source.fall_time = period * 0.01
        source.pulse_width = period / 2.0
        source.period = period
        return

    if "dc_V" in vin_cfg:
        dc_v = float(vin_cfg["dc_V"])
        source.initial_value = dc_v
        source.pulsed_value = dc_v
        source.delay_time = 0.0
        source.rise_time = 1e-9
        source.fall_time = 1e-9
        source.pulse_width = 1.0
        source.period = 2.0
        return


def _normalize_expected_pinout(expected_pinout: Sequence[Dict[str, Any]] | None) -> tuple[List[str], List[str]]:
    """
    Normalize a strict pinout shape into an ordered list of selectors.

    Required shape:
        [{"number": "...", "name": "...", "type": "..."} , ...]

    Returns:
        (selectors, problems)
    """

    if expected_pinout is None:
        return [], []

    if not isinstance(expected_pinout, Sequence):
        return [], [f"Pinout must be a sequence of pin dicts; got {type(expected_pinout).__name__}."]

    normalized: List[str] = []
    problems: List[str] = []

    for idx, entry in enumerate(expected_pinout):
        if not isinstance(entry, dict):
            problems.append(f"Pinout entry {idx} must be a dict with number/name/type; got {type(entry).__name__}.")
            continue

        number = entry.get("number")
        name = entry.get("name")
        ptype = entry.get("type")

        if number is None or name is None or ptype is None:
            problems.append(
                f"Pinout entry {idx} missing required fields; expected keys number/name/type."
            )
            continue

        selector = str(number).strip()
        if not selector:
            problems.append(f"Pinout entry {idx} has empty number field.")
            continue

        normalized.append(selector)

    return normalized, problems


def _extract_pin_selectors(part) -> List[str]:
    """Return a stable string selector for each pin on a SKiDL part."""

    selectors: List[str] = []
    for idx, pin in enumerate(getattr(part, "pins", [])):
        selector = getattr(pin, "num", None) or getattr(pin, "name", None) or f"{idx+1}"
        selectors.append(str(selector))
    return selectors


def disable_inspice_cache() -> None:
    """
    Turn off InSpice cache artifacts (db.pickle / *.yaml) to avoid bleed-over between runs.
    """

    try:
        from InSpice.Spice.Library import SpiceLibrary as _RawSpiceLibrary
        from InSpice.Spice.Library.SpiceInclude import SpiceInclude as _RawSpiceInclude

        _RawSpiceLibrary.save = lambda self: None  # type: ignore[assignment]
        _RawSpiceLibrary.load = lambda self: None  # type: ignore[assignment]
        _RawSpiceInclude.write_yaml = lambda self: None  # type: ignore[assignment]
    except Exception:
        # Best-effort patching; ignore if InSpice isn't importable yet.
        pass


def disable_skidl_file_logging() -> None:
    """
    Stop SKiDL from emitting .log/.erc files (best-effort).
    """

    try:
        from skidl.logger import stop_log_file_output

        stop_log_file_output(True)
    except Exception:
        pass


def _build_subckt_template(lib, subckt_name: str, lib_entry):
    """
    Build a SPICE subcircuit part template from a parsed library entry.

    This mirrors the PySpice 1.6+ behavior where the parser returns pin metadata
    instead of a full SKiDL Part with a copy() method.
    """

    from skidl import SPICE, Pin  # Imported lazily to avoid heavy module load at import time.
    from skidl.tools.spice.spice import add_subcircuit_to_circuit

    template = skidl_part.Part(part_defn="don't care", tool=SPICE, dest=skidl_part.LIBRARY)
    template.fplist = []
    template.aliases = []
    template.num_units = 1
    template.ref_prefix = "X"
    template._ref = None
    template.filename = ""
    template.name = subckt_name
    template.pins = []

    nodes = getattr(lib_entry, "_nodes", None) or getattr(lib_entry, "nodes", None) or []
    for idx, node in enumerate(nodes):
        num = getattr(node, "internal_node", None) or getattr(node, "num", None)
        name = getattr(node, "name", None)
        if num is None:
            num = idx + 1
        pin_name = str(name or num)
        template.pins.append(Pin(num=num, name=pin_name))

    template.associate_pins()
    lib_path = getattr(lib_entry, "path", None) or getattr(lib, "_path", None)
    template.pyspice = {
        "name": "X",
        "add": add_subcircuit_to_circuit,
        "lib": lib,
        "lib_path": str(lib_path) if lib_path else "",
        "lib_section": getattr(lib, "_section", None),
    }
    return template


def validate_spice_model(
    model_path: str | Path,
    expected_subckt_name: str,
    expected_pinout: Sequence[Dict[str, Any]] | None = None,
) -> List[str]:
    """
    Validate a SPICE model file by parsing and smoke-instantiating its subcircuit.

    Args:
        model_path: Filesystem path to the .lib/.spice file containing the model.
        expected_subckt_name: Name of the .SUBCKT expected in the library.
        expected_pinout: Ordered pinout description used for comparison and wiring.

    Returns:
        List of human-readable problems. Empty list means validation passed.
    """

    problems: List[str] = []
    path = Path(model_path)

    if not path.exists():
        return [f"Model file not found: {path}"]

    # Disable InSpice cache artifacts (db.pickle / *.yaml) that can bleed across runs.
    disable_inspice_cache()
    # Stop SKiDL from dropping .log/.erc files during imports.
    disable_skidl_file_logging()

    # Track cache artifacts so we can clean up after parsing.
    db_path = (path.parent / "db.pickle") if path.is_file() else (path / "db.pickle")

    log_capture = io.StringIO()
    try:
        from skidl.pyspice import SpiceLibrary  # type: ignore

        with redirect_stdout(log_capture), redirect_stderr(log_capture):
            # Force a fresh parse to avoid picking up stale entries from a cached db.pickle.
            lib = SpiceLibrary(str(path), scan=True)
    except Exception as exc:
        raw_log = log_capture.getvalue().replace("/root/workspace/KiCAD_MCP/", "")
        parser_lines = [line.strip() for line in raw_log.splitlines() if line.strip()]
        problems.append(f"Parse failed: {exc}")
        problems.extend(f"parser: {line}" for line in parser_lines)
        return problems

    raw_log = log_capture.getvalue().replace("/root/workspace/KiCAD_MCP/", "")
    parser_lines = [line.strip() for line in raw_log.splitlines() if line.strip()]
    subckts = list(getattr(lib, "subcircuits", []) or [])
    if not subckts:
        problems.append("Parse failed: library contained no subcircuits.")
        problems.extend(f"parser: {line}" for line in parser_lines)
        return problems

    if expected_subckt_name not in subckts:
        available = ", ".join(sorted(subckts)) if subckts else "none"
        problems.append(
            f"Expected subcircuit {expected_subckt_name!r} not found; available: {available}."
        )
        problems.extend(f"parser: {line}" for line in parser_lines)
        return problems

    expected_pins, pinout_problems = _normalize_expected_pinout(expected_pinout)
    if pinout_problems:
        problems.extend(pinout_problems)
        return problems

    try:
        from skidl.pyspice import Circuit, Net  # type: ignore
        from skidl.tools.spice.spice import gen_netlist

        circuit = Circuit()
        with circuit:
            lib_entry = lib[expected_subckt_name]
            if hasattr(lib_entry, "copy") and callable(getattr(lib_entry, "copy")):
                template = lib_entry
            else:
                template = _build_subckt_template(lib, expected_subckt_name, lib_entry)

            part = template.copy(dest=skidl_part.NETLIST, circuit=circuit, ref="XVAL")
            actual_pins = _extract_pin_selectors(part)

            if expected_pins:
                if len(expected_pins) != len(actual_pins):
                    problems.append(
                        f"Pin count mismatch for {expected_subckt_name}: expected {len(expected_pins)}, parsed {len(actual_pins)}."
                    )
                elif expected_pins != actual_pins:
                    problems.append(
                        f"Pin order/name mismatch for {expected_subckt_name}: expected {expected_pins}, parsed {actual_pins}."
                    )

            net_labels = (
                expected_pins if expected_pins and len(expected_pins) == len(part.pins) else actual_pins
            )
            if not net_labels:
                net_labels = [f"PIN{i+1}" for i in range(len(part.pins))]

            for idx, (label, pin) in enumerate(zip(net_labels, part.pins)):
                net = Net(str(label) or f"PIN{idx+1}")
                net += pin

        gen_netlist(circuit, title="validate_spice_model")
    except Exception as exc:
        problems.append(f"Smoke instantiation failed: {exc}")

    # Remove cache artifact if it exists (avoid stale models bleeding across runs).
    if db_path.exists():
        try:
            db_path.unlink()
        except Exception:
            pass

    return problems
