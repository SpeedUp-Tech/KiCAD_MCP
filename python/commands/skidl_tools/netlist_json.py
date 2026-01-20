"""Structured SKiDL netlist export helpers."""

from __future__ import annotations

import importlib.util
import inspect
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

from python.spice_tools.utils import disable_skidl_file_logging

LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[3]


def generate_skidl_netlist_json(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Generate a structured netlist summary from a SKiDL module.

    Args:
        skidlPath: Path to the SKiDL Python file containing subcircuit definitions.
        subcircuitName: Optional name of the @SubCircuit function to instantiate.
                        If not provided, auto-detects from the module.

    Returns:
        Dict with keys:
            - success: bool
            - subcircuitName: str
            - partsCount: int
            - netsCount: int
            - interfaceNets: list[str]
            - nets: list[dict]
            - parts: list[dict]
            - unconnectedPins: list[dict]
    """
    LOGGER.info("Generating structured SKiDL netlist")
    try:
        skidl_path = params.get("skidlPath")
        subcircuit_name = params.get("subcircuitName")

        if not skidl_path:
            return {"success": False, "message": "skidlPath is required"}

        skidl_path = os.path.abspath(os.path.expanduser(str(skidl_path)))
        if not os.path.exists(skidl_path):
            return {"success": False, "message": f"SKiDL file not found: {skidl_path}"}

        disable_skidl_file_logging()
        _configure_skidl_defaults()

        skidl_dir = os.path.dirname(skidl_path)
        original_cwd = os.getcwd()
        original_path = sys.path.copy()

        try:
            if skidl_dir and skidl_dir not in sys.path:
                sys.path.insert(0, skidl_dir)
            project_root = str(PROJECT_ROOT)
            if project_root not in sys.path:
                sys.path.insert(0, project_root)

            os.chdir(skidl_dir or original_cwd)

            module = _load_skidl_module(skidl_path)
            subckt_func, subckt_name = _select_subcircuit(module, subcircuit_name)
            if subckt_func is None:
                if subcircuit_name:
                    return {
                        "success": False,
                        "message": f"Subcircuit '{subcircuit_name}' not found in module",
                    }
                return {
                    "success": False,
                    "message": "No subcircuit function found. Please specify subcircuitName parameter.",
                }

            interface_params = _extract_interface_params(subckt_func)

            from skidl import Circuit, Net  # Lazy import to avoid heavy module load at import time.

            circuit = Circuit()

            interface_nets: Dict[str, Net] = {}
            with circuit:
                for param_name in interface_params:
                    interface_nets[param_name] = Net(param_name)

                _invoke_subcircuit(subckt_func, interface_nets, subckt_name)
                _best_effort_finalize(circuit)

            net_entries, net_name_map = _build_nets(circuit, interface_nets)
            parts_entries = _build_parts(circuit, net_name_map)
            unconnected = _find_unconnected_pins(circuit)

            return {
                "success": True,
                "subcircuitName": subckt_name,
                "partsCount": len(parts_entries),
                "netsCount": len(net_entries),
                "interfaceNets": list(interface_nets.keys()),
                "nets": net_entries,
                "parts": parts_entries,
                "unconnectedPins": unconnected,
            }
        finally:
            os.chdir(original_cwd)
            sys.path = original_path
    except Exception as exc:
        LOGGER.error("Error generating structured SKiDL netlist: %s", exc)
        return {"success": False, "message": str(exc)}


def _configure_skidl_defaults() -> None:
    try:
        from skidl import KICAD8, set_default_tool

        set_default_tool(KICAD8)
    except Exception:
        pass


def _load_skidl_module(skidl_path: str):
    module_name = Path(skidl_path).stem
    spec = importlib.util.spec_from_file_location(module_name, skidl_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load module from: {skidl_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _select_subcircuit(module, subcircuit_name: str | None) -> Tuple[Any | None, str | None]:
    if subcircuit_name:
        subckt_func = getattr(module, subcircuit_name, None)
        if callable(subckt_func):
            return subckt_func, subcircuit_name
        return None, None

    for name in dir(module):
        if name.startswith("_") or not name.isupper():
            continue
        obj = getattr(module, name)
        if callable(obj) and (hasattr(obj, "__wrapped__") or name.endswith("_PROTECTION") or name.endswith("_CHARGER")):
            return obj, name

    for name in dir(module):
        if name.startswith("_") or not name.isupper():
            continue
        obj = getattr(module, name)
        if callable(obj):
            return obj, name

    return None, None


def _extract_interface_params(subckt_func) -> List[str]:
    signature = inspect.signature(subckt_func)
    params: List[str] = []
    for name, param in signature.parameters.items():
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        lowered = name.lower()
        if lowered in ("tag", "ref", "kwargs"):
            continue
        params.append(name)
    return params


def _invoke_subcircuit(subckt_func, interface_nets: Dict[str, Any], subckt_name: str | None) -> None:
    try:
        subckt_func(**interface_nets, tag=subckt_name)
    except TypeError:
        subckt_func(**interface_nets)


def _best_effort_finalize(circuit) -> None:
    try:
        from skidl import ERC

        ERC()
    except Exception:
        pass
    try:
        circuit.generate_netlist(do_backup=False)
    except Exception:
        pass


def _build_nets(circuit, interface_nets: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[int, str]]:
    net_entries: List[Dict[str, Any]] = []
    net_name_map: Dict[int, str] = {}
    sorted_nets = sorted(circuit.nets, key=_net_sort_key)

    for idx, net in enumerate(sorted_nets, start=1):
        net_name = _normalize_net_name(getattr(net, "name", None), idx)
        net_name_map[id(net)] = net_name

        pins = sorted(getattr(net, "pins", []) or [], key=_pin_sort_key)
        nodes = [
            {
                "ref": str(getattr(pin.part, "ref", "")),
                "pin": str(getattr(pin, "num", "")),
                "pinName": _pin_name(pin),
            }
            for pin in pins
        ]

        net_entries.append(
            {
                "code": idx,
                "name": net_name,
                "isInterface": net_name in interface_nets,
                "isAnonymous": net_name.startswith("N$") or net_name == "",
                "nodes": nodes,
            }
        )

    return net_entries, net_name_map


def _build_parts(circuit, net_name_map: Dict[int, str]) -> List[Dict[str, Any]]:
    parts_entries: List[Dict[str, Any]] = []
    parts = sorted(circuit.parts, key=_part_sort_key)

    for part in parts:
        ref = str(getattr(part, "ref", ""))
        entry: Dict[str, Any] = {
            "ref": ref,
            "libId": _part_lib_id(part),
            "value": str(getattr(part, "value", "")),
            "footprint": str(getattr(part, "footprint", "") or ""),
            "pins": [],
        }

        fields = _part_fields(part)
        if fields:
            entry["fields"] = fields

        pins = sorted(getattr(part, "pins", []) or [], key=_pin_sort_key)
        for pin in pins:
            net = getattr(pin, "net", None)
            net_name = net_name_map.get(id(net)) if net is not None else ""
            if not net_name and hasattr(net, "name"):
                net_name = str(net.name or "")
            entry["pins"].append(
                {
                    "num": str(getattr(pin, "num", "")),
                    "name": _pin_name(pin),
                    "net": net_name,
                }
            )

        parts_entries.append(entry)

    return parts_entries


def _find_unconnected_pins(circuit) -> List[Dict[str, str]]:
    connected: set[Tuple[str, str]] = set()
    for net in circuit.nets:
        for pin in getattr(net, "pins", []) or []:
            ref = str(getattr(pin.part, "ref", ""))
            pin_num = str(getattr(pin, "num", ""))
            if ref and pin_num:
                connected.add((ref, pin_num))

    unconnected: List[Dict[str, str]] = []
    parts = sorted(circuit.parts, key=_part_sort_key)
    for part in parts:
        ref = str(getattr(part, "ref", ""))
        pins = sorted(getattr(part, "pins", []) or [], key=_pin_sort_key)
        for pin in pins:
            pin_num = str(getattr(pin, "num", ""))
            if (ref, pin_num) not in connected:
                unconnected.append(
                    {
                        "ref": ref,
                        "pin": pin_num,
                        "pinName": _pin_name(pin),
                    }
                )

    return unconnected


def _part_lib_id(part) -> str:
    lib_name = ""
    if hasattr(part, "_db_library_name") and part._db_library_name:
        lib_name = str(part._db_library_name)
    else:
        lib = getattr(part, "lib", None)
        if isinstance(lib, str):
            lib_name = lib.split(":")[0].strip()
        elif lib is not None:
            if hasattr(lib, "filename"):
                lib_name = str(getattr(lib, "filename", ""))
            elif hasattr(lib, "name"):
                lib_name = str(getattr(lib, "name", ""))
            else:
                lib_name = str(lib).split(":")[0].strip()

    if lib_name.endswith(".kicad_sym"):
        lib_name = Path(lib_name).stem

    symbol_name = str(getattr(part, "name", "") or "")
    if lib_name and symbol_name:
        return f"{lib_name}:{symbol_name}"
    if symbol_name:
        return symbol_name
    return lib_name


def _part_fields(part) -> Dict[str, str]:
    raw_fields = getattr(part, "fields", None) or {}
    fields: Dict[str, str] = {}
    for key, value in raw_fields.items():
        if value is None:
            continue
        key_str = str(key)
        if key_str.strip().lower() in {"reference", "ref", "value", "footprint"}:
            continue
        fields[key_str] = str(value)
    return fields


def _normalize_net_name(name: Any, idx: int) -> str:
    if name is None:
        return f"N${idx}"
    name_str = str(name).strip()
    return name_str or f"N${idx}"


def _net_sort_key(net) -> Tuple[int, str]:
    name = str(getattr(net, "name", "") or "")
    anonymous = 1 if name.startswith("N$") or name == "" else 0
    return (anonymous, name)


def _part_sort_key(part) -> Tuple[str, str]:
    ref = str(getattr(part, "ref", ""))
    prefix = "".join(ch for ch in ref if not ch.isdigit())
    suffix = "".join(ch for ch in ref if ch.isdigit())
    return (prefix, suffix or ref)


def _pin_sort_key(pin) -> Tuple[str, str]:
    ref = str(getattr(getattr(pin, "part", None), "ref", ""))
    num = str(getattr(pin, "num", ""))
    return (ref, num)


def _pin_name(pin) -> str:
    name = getattr(pin, "name", None)
    if name is None or str(name).strip() == "":
        return str(getattr(pin, "num", ""))
    return str(name)
