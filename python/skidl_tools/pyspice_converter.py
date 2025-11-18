"""
Helpers to convert DB-backed SKiDL modules into PySpice-ready subcircuits.

The converter loads a SKiDL module, instantiates the requested @SubCircuit,
maps each part to a PySpice primitive or SPICE .SUBCKT according to a JSON
mapping file, and writes a new Python module that can be imported by PySpice.
"""

from __future__ import annotations

import importlib.util
import inspect
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

from skidl import Circuit, Net


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MAPPING_PATH = REPO_ROOT / "config" / "spice_model_map.json"


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


def _load_mapping(mapping_path: Path) -> Dict[str, dict]:
    if not mapping_path.exists():
        raise FileNotFoundError(f"Mapping file {mapping_path} not found.")
    return json.loads(mapping_path.read_text(encoding="utf-8"))


def _resolve_model(mapping: Dict[str, dict], library: str, name: str) -> dict:
    key = f"{library}::{name}"
    if key not in mapping:
        available = ", ".join(sorted(mapping.keys()))
        raise KeyError(f"No SPICE mapping found for {key}. Known keys: {available}")
    return mapping[key]


def _emit_module(
    output: Path,
    subckt_out: str,
    interface_nets: List[str],
    circuit,
    parts: List[PartRecord],
    mapping: Dict[str, dict],
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
        model = _resolve_model(mapping, part.library, part.name)
        inst_kwargs = [
            f"ref='{part.ref}'",
            "dest='INSTANCE'",
        ]
        if part.value:
            inst_kwargs.append(f"value={part.value!r}")

        if model["type"] == "pyspice":
            inst = f"Part(lib='pyspice', name='{model['name']}', {', '.join(inst_kwargs)})"
        elif model["type"] == "subckt":
            rel_path = model["lib_file"]
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

    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def convert_skidl_module(
    input_path: Path,
    subckt_name: str,
    output_path: Path,
    mapping_path: Path | None = None,
    subckt_output: str | None = None,
) -> str:
    """
    Convert a SKiDL module/subcircuit into a PySpice-ready Python module.

    Returns the name of the generated @SubCircuit.
    """

    module = _load_module(input_path.resolve())
    circuit, interface = _instantiate_subckt(module, subckt_name)
    parts = [_extract_part(part) for part in circuit.parts]
    mapping = _load_mapping(mapping_path or DEFAULT_MAPPING_PATH)
    subckt_out = subckt_output or f"{subckt_name}_pyspice"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    _emit_module(output_path, subckt_out, interface, circuit, parts, mapping)
    return subckt_out


__all__ = ["convert_skidl_module", "DEFAULT_MAPPING_PATH"]

