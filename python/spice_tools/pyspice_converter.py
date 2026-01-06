"""
Helpers to convert DB-backed SKiDL modules into PySpice-ready subcircuits.

The converter loads a SKiDL module, instantiates the requested @SubCircuit,
maps each part to a PySpice primitive or SPICE .SUBCKT using the shared model
database, and writes a new Python module that can be imported by PySpice.
"""

from __future__ import annotations

import importlib.util
import inspect
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Tuple, Optional

import os

from skidl import Circuit, Net

from .model_db import resolve_model_db_path, search_spice_model
from .utils import disable_inspice_cache, disable_skidl_file_logging

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class PinRecord:
    selector: str
    net: str


@dataclass
class PartRecord:
    ref: str
    library: str
    name: str
    value: str | None
    pins: List[PinRecord]


def _load_module(module_path: Path):
    spec = importlib.util.spec_from_file_location(module_path.stem, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to build module spec for {module_path}.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[assignment]
    return module


def _instantiate_subckt(module, subckt_name: str) -> Tuple[Circuit, List[str]]:
    subckt = getattr(module, subckt_name, None)
    if subckt is None:
        raise AttributeError(f"Subcircuit {subckt_name} not found in module {module.__file__}.")
    params = list(inspect.signature(subckt).parameters.keys())
    circuit = Circuit()
    with circuit:
        nets = {param: Net(param) for param in params}
        subckt(**nets)
    return circuit, params


def _extract_part(part) -> PartRecord:
    pins: List[PinRecord] = []
    for pin in part.pins:
        if not getattr(pin, "net", None):
            continue
        net_name = pin.net.name
        if net_name == "__NOCONNECT":
            continue
        selector = pin.num or pin.name
        pins.append(PinRecord(selector=str(selector), net=net_name))
    return PartRecord(
        ref=part.ref,
        library=getattr(part, "filename", ""),
        name=part.name,
        value=getattr(part, "value", None),
        pins=pins,
    )


def _unique_sorted(items: Iterable[str]) -> List[str]:
    return sorted(dict.fromkeys(items))


def _relative_path(target: Path, base: Path) -> str:
    target_abs = target.resolve()
    base_abs = base.resolve()
    rel = os.path.relpath(target_abs, base_abs)
    return Path(rel).as_posix()


PRIMITIVE_MODELS = {
    "Device::R": {"type": "pyspice", "name": "R"},
    "Device::C": {"type": "pyspice", "name": "C"},
    "Device::L": {"type": "pyspice", "name": "L"},
    "Device::V": {"type": "pyspice", "name": "V"},
    "Device::I": {"type": "pyspice", "name": "I"},
}


def _parse_subckt_name(model_content: str) -> Optional[str]:
    for raw in model_content.splitlines():
        line = raw.strip()
        if not line or line.startswith("*"):
            continue
        if line.upper().startswith(".SUBCKT"):
            tokens = line.split()
            if len(tokens) >= 2:
                return tokens[1]
    return None


def _emit_module(
    output_path: Path,
    subckt_out: str,
    interface_nets: List[str],
    circuit,
    parts: List[PartRecord],
    model_specs: Dict[str, dict],
) -> None:
    internal_nets = [
        net.name
        for net in circuit.nets
        if net.name not in interface_nets and net.name != "__NOCONNECT"
    ]
    internal_nets = _unique_sorted(internal_nets)

    lib_vars: Dict[str, str] = {}
    lib_lines: List[str] = []
    lines: List[str] = []
    lines.append('"""Auto-generated PySpice version of the SKiDL module."""')
    lines.append("from pathlib import Path")
    lines.append("from skidl.pyspice import *  # noqa: F401,F403")
    lines.append("from skidl import SchLib")
    lines.append("")
    lines.append("_LIB_CACHE: dict[str, SchLib] = {}")
    lines.append("")
    lines.append("def _load_schlib(rel_path: str) -> SchLib:")
    lines.append("    abs_path = (Path(__file__).resolve().parent / rel_path).resolve()")
    lines.append("    cached = _LIB_CACHE.get(str(abs_path))")
    lines.append("    if cached is None:")
    lines.append("        cached = SchLib(str(abs_path), tool=SPICE)")
    lines.append("        _LIB_CACHE[str(abs_path)] = cached")
    lines.append("    return cached")
    lines.append("")
    params = ", ".join(interface_nets)
    lines.append("@SubCircuit")
    lines.append(f"def {subckt_out}({params}):")
    lines.append("    nets = {}")
    for name in interface_nets:
        lines.append(f"    nets['{name}'] = {name}")
    for name in internal_nets:
        lines.append(f"    nets['{name}'] = Net('{name}')")
    lines.append("    parts = {}")
    lines.append("")

    for part in sorted(parts, key=lambda p: p.ref):
        key = f"{part.library}::{part.name}"
        model = model_specs.get(key)
        if model is None:
            available = ", ".join(sorted(model_specs.keys()))
            raise KeyError(f"No SPICE model registered for {key}. Known keys: {available}")
        inst_kwargs = [
            f"ref='{part.ref}'",
            "dest='INSTANCE'",
        ]
        if part.value:
            inst_kwargs.append(f"value={part.value!r}")

        if model["type"] == "pyspice":
            inst = f"Part(lib='pyspice', name='{model['name']}', {', '.join(inst_kwargs)})"
        elif model["type"] == "subckt":
            lib_path = Path(model["lib_file"])
            if not lib_path.is_absolute():
                lib_path = (REPO_ROOT / lib_path).resolve()
            rel_path = _relative_path(lib_path, output_path.parent)
            lib_var = lib_vars.get(rel_path)
            if lib_var is None:
                lib_var = f"_LIB_{len(lib_vars)}"
                lib_vars[rel_path] = lib_var
                rel_code = rel_path.replace("'", "\\'")
                lib_lines.append(f"{lib_var} = _load_schlib('{rel_code}')")
            inst = f"Part(lib={lib_var}, name='{model['name']}', {', '.join(inst_kwargs)})"
        else:
            raise ValueError(f"Unknown model type {model['type']} for {part.ref}")

        lines.append(f"    parts['{part.ref}'] = {inst}")
        for pin in part.pins:
            lines.append(f"    nets['{pin.net}'] += parts['{part.ref}'][{pin.selector!r}]")
        lines.append("")

    lines.append("    return parts")
    lines.append("")
    if lib_lines:
        lines.append("# Preload external SPICE libraries referenced in this module.")
        lines.extend(lib_lines)
        lines.append("")
    lines.append("if __name__ == '__main__':")
    lines.append("    circuit = Circuit()")
    names_literal = ", ".join(repr(n) for n in interface_nets)
    lines.append(f"    interface = {{n: Net(n) for n in [{names_literal}]}}")
    lines.append("    with circuit:")
    lines.append(f"        {subckt_out}(**interface)")
    lines.append("    print(f'Generated PySpice netlist with {len(circuit.parts)} parts and {len(circuit.nets)} nets.')")

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def convert_skidl_module(
    input_path: Path,
    subckt_name: str,
    output_path: Path,
    subckt_output: str | None = None,
    model_db_path: Path | None = None,
) -> str:
    """
    Convert a SKiDL module/subcircuit into a PySpice-ready Python module.

    Returns the name of the generated @SubCircuit.
    """

    disable_inspice_cache()
    disable_skidl_file_logging()

    module = _load_module(input_path.resolve())
    circuit, interface = _instantiate_subckt(module, subckt_name)
    parts = [_extract_part(part) for part in circuit.parts]
    subckt_out = subckt_output or f"{subckt_name}_pyspice"

    model_specs: Dict[str, dict] = {}
    model_specs.update(PRIMITIVE_MODELS)

    db_models: Dict[str, dict] = {}
    model_db = resolve_model_db_path(model_db_path, for_write=False)
    for part in parts:
        key = f"{part.library}::{part.name}"
        if key in model_specs or key in db_models:
            continue
        entry = search_spice_model(name=part.name, library=part.library, db_path=model_db)
        if entry is None:
            raise KeyError(f"No SPICE model found in database for {key}.")
        model_content = str(entry["model_content"])
        subckt = _parse_subckt_name(model_content)
        if not subckt:
            raise ValueError(f"Unable to determine .SUBCKT name for model {key}.")
        db_models[key] = {
            "subckt": subckt,
            "content": model_content.strip(),
        }

    model_lib_path = output_path.with_suffix(".spice.lib")
    lib_rel_path = None
    if db_models:
        model_lib_path.parent.mkdir(parents=True, exist_ok=True)
        combined = "\n\n".join(model["content"] for model in db_models.values()) + "\n"
        model_lib_path.write_text(combined, encoding="utf-8")
        lib_rel_path = model_lib_path.resolve().as_posix()
        for key, model in db_models.items():
            model_specs[key] = {
                "type": "subckt",
                "lib_file": lib_rel_path,
                "name": model["subckt"],
            }
    else:
        if model_lib_path.exists():
            model_lib_path.unlink()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    _emit_module(output_path, subckt_out, interface, circuit, parts, model_specs)
    return subckt_out


__all__ = ["convert_skidl_module"]
