"""Auto-generate ELK logic hints using simple satellite-style rules."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from python.netlist_to_schematic.elk_graph_adapter import SPECIAL_PIN_THRESHOLD, SymbolGeometryFetcher


POWER_SYMBOL_MAP = {
    "GND": "power:GND",
    "AGND": "power:AGND",
    "PGND": "power:PGND",
    "VSS": "power:VSS",
    "VCC": "power:VCC",
    "VDD": "power:VDD",
    "VBUS": "power:VBUS",
    "+5V": "power:+5V",
    "+3V3": "power:+3V3",
}


def build_simple_logic_hints(
    circuit,
    interface_nets: Iterable[str] | None = None,
    *,
    special_pin_threshold: int = SPECIAL_PIN_THRESHOLD,
    label_high_fanout_nets: bool = False,
    high_fanout_threshold: int = 4,
    power_symbol_map: dict[str, str] | None = None,
    use_direct_connections: bool = False,
) -> dict:
    """
    Build logic hints that apply the satellite-style rules:
    - Special parts (pin count >= threshold) connect via net labels only.
    - Optionally labelize high-fanout nets (refs >= threshold) to reduce crossings.
    - Each component gets its own power symbol per supported power net.
    - Interface nets get a hierarchical label anchor.
    - Optionally emit direct connections instead of layout ordering.
    """
    interface_set = {str(n) for n in (interface_nets or []) if n is not None}
    power_symbol_map = power_symbol_map or POWER_SYMBOL_MAP

    connected_pins = _connected_pins_by_ref(circuit)
    special_refs = {
        part.ref
        for part in getattr(circuit, "parts", []) or []
        if getattr(part, "ref", None)
        and _pin_count(part, connected_pins) >= special_pin_threshold
    }
    pin_side_map = _pin_sides(circuit, SymbolGeometryFetcher())

    net_labels: list[dict] = []
    power_symbols: list[dict] = []
    layout_chains: list[dict] = []
    direct_connections: list[dict] = []
    constraints: list[dict] = []

    used_label_ids: set[str] = set()
    used_power_ids: set[str] = set()
    used_chain_ids: set[str] = set()

    def add_label(base_id: str, net_name: str, label_type: str, shape: str = "bidirectional") -> str:
        label_id = _unique_id(_sanitize_id(base_id), used_label_ids)
        label = {"id": label_id, "type": label_type, "net_name": net_name}
        if label_type == "hierarchical":
            label["shape"] = shape
        net_labels.append(label)
        return label_id

    def add_power_symbol(base_id: str, symbol_type: str) -> str:
        power_id = _unique_id(_sanitize_id(base_id), used_power_ids)
        power_symbols.append({"id": power_id, "type": symbol_type})
        return power_id

    def add_chain(base_id: str, path: list[str]) -> str:
        chain_id = _unique_id(_sanitize_id(f"chain_{base_id}"), used_chain_ids)
        layout_chains.append({"id": chain_id, "path": path})
        return chain_id

    def add_connection(node_id: str, ref: str, side: str | None) -> None:
        if use_direct_connections:
            entry = {"node": node_id, "component": ref}
            if side in {"left", "right"}:
                entry["side"] = side
            direct_connections.append(entry)
            return
        path = [node_id, ref] if side == "left" else [ref, node_id]
        add_chain(node_id, path)

    used_constraints: set[tuple[str, str, str]] = set()

    def add_adjacency(node_id: str, ref: str, side: str | None) -> None:
        if use_direct_connections:
            return
        if side not in {"left", "right"}:
            return
        key = (node_id, ref, side)
        if key in used_constraints:
            return
        used_constraints.add(key)
        components = [node_id, ref] if side == "left" else [ref, node_id]
        constraints.append({"type": "ADJACENT", "components": components})

    def choose_side(ref: str, pin_nums: list[str]) -> str | None:
        sides = []
        for pin_num in sorted({str(p) for p in pin_nums}, key=str):
            side = pin_side_map.get(ref, {}).get(str(pin_num))
            if side:
                sides.append(side)
        if not sides:
            return None
        left = sides.count("left")
        right = sides.count("right")
        if left > right:
            return "left"
        if right > left:
            return "right"
        return sides[0]

    sorted_nets = sorted(getattr(circuit, "nets", []) or [], key=_net_sort_key)
    for idx, net in enumerate(sorted_nets, start=1):
        net_name = _normalize_net_name(getattr(net, "name", None), idx)

        component_pins: dict[str, list[str]] = {}
        for pin in sorted(getattr(net, "pins", []) or [], key=_pin_sort_key):
            ref = _pin_ref(pin)
            num = _pin_num(pin)
            if not ref or not num:
                continue
            component_pins.setdefault(ref, []).append(num)

        if not component_pins:
            continue

        refs = sorted(component_pins.keys(), key=_ref_sort_key)
        special_in_net = [ref for ref in refs if ref in special_refs]
        non_special_refs = [ref for ref in refs if ref not in special_refs]
        is_interface = net_name in interface_set

        power_symbol_type = power_symbol_map.get(net_name)
        if power_symbol_type:
            for ref in refs:
                if ref in special_refs:
                    for pin_num in sorted(component_pins.get(ref, []), key=str):
                        power_id = add_power_symbol(f"{net_name}_{ref}_p{pin_num}", power_symbol_type)
                        side = pin_side_map.get(ref, {}).get(str(pin_num))
                        add_connection(power_id, ref, side)
                        add_adjacency(power_id, ref, side)
                else:
                    power_id = add_power_symbol(f"{net_name}_{ref}", power_symbol_type)
                    side = choose_side(ref, component_pins.get(ref, []))
                    add_connection(power_id, ref, side)
                    add_adjacency(power_id, ref, side)
            continue

        if label_high_fanout_nets and len(refs) >= high_fanout_threshold:
            label_type = "hierarchical" if is_interface else "local"
            for ref in refs:
                for pin_num in sorted(component_pins.get(ref, []), key=str):
                    label_id = add_label(f"{net_name}_{ref}_p{pin_num}", net_name, label_type)
                    side = pin_side_map.get(ref, {}).get(str(pin_num))
                    add_connection(label_id, ref, side)
                    add_adjacency(label_id, ref, side)
            continue

        if special_in_net:
            special_label_type = "hierarchical" if is_interface else "local"
            for ref in special_in_net:
                for pin_num in sorted(component_pins.get(ref, []), key=str):
                    label_id = add_label(f"{net_name}_{ref}_p{pin_num}", net_name, special_label_type)
                    side = pin_side_map.get(ref, {}).get(str(pin_num))
                    add_connection(label_id, ref, side)
                    add_adjacency(label_id, ref, side)

        if non_special_refs and (special_in_net or is_interface):
            anchor_ref = non_special_refs[0]
            branch_label_type = "hierarchical" if is_interface and not special_in_net else "local"
            branch_id = add_label(f"{net_name}_{anchor_ref}_branch", net_name, branch_label_type)
            side = choose_side(anchor_ref, component_pins.get(anchor_ref, []))
            add_connection(branch_id, anchor_ref, side)
            add_adjacency(branch_id, anchor_ref, side)

    return {
        "elk_support_fields": {
            "net_labels": net_labels,
            "layout_chains": layout_chains,
            "power_symbols": power_symbols,
            "direct_connections": direct_connections,
            "constraints": constraints,
        }
    }


def _connected_pins_by_ref(circuit) -> dict[str, set[str]]:
    connected: dict[str, set[str]] = {}
    for net in getattr(circuit, "nets", []) or []:
        for pin in getattr(net, "pins", []) or []:
            ref = _pin_ref(pin)
            num = _pin_num(pin)
            if not ref or not num:
                continue
            connected.setdefault(ref, set()).add(num)
    return connected


def _pin_count(part, connected_pins: dict[str, set[str]]) -> int:
    pins = getattr(part, "pins", []) or []
    try:
        total = len(pins)
    except TypeError:
        total = 0
    connected = len(connected_pins.get(getattr(part, "ref", ""), set()))
    return max(total, connected)


def _pin_sides(circuit, fetcher: SymbolGeometryFetcher) -> dict[str, dict[str, str]]:
    side_map: dict[str, dict[str, str]] = {}
    for part in getattr(circuit, "parts", []) or []:
        ref = getattr(part, "ref", None)
        if not ref:
            continue
        lib_name = _determine_library(part)
        symbol_name = str(getattr(part, "name", "") or "")
        geom = fetcher.get_part_geometry(lib_name, symbol_name)
        pins = geom.get("pins", {})
        bbox = geom.get("bbox", {})
        if not pins or not bbox:
            continue
        center_x = (bbox.get("min_x", 0.0) + bbox.get("max_x", 0.0)) / 2
        for pin_num, pin_geo in pins.items():
            conn_x = pin_geo.get("conn_x")
            if conn_x is None:
                conn_x = pin_geo.get("x", 0.0)
            side = "left" if conn_x < center_x else "right"
            side_map.setdefault(ref, {})[str(pin_num)] = side
    return side_map


def _determine_library(part) -> str:
    if hasattr(part, "_db_library_name") and part._db_library_name:
        return part._db_library_name

    lib_name = "Power_Management_ICs"
    if hasattr(part, "lib") and part.lib:
        if hasattr(part.lib, "filename"):
            lib_name = part.lib.filename
        elif hasattr(part.lib, "name"):
            lib_name = part.lib.name
        else:
            lib_name = str(part.lib).split(":")[0].strip()

    if lib_name.endswith(".kicad_sym"):
        lib_name = Path(lib_name).stem
    elif lib_name.endswith(".sqlite3"):
        lib_name = "Power_Management_ICs"

    part_name = str(getattr(part, "name", "") or "")
    if part_name in ["R", "C", "L", "D", "LED"]:
        lib_name = "Device"
    elif "IP2312" in part_name:
        lib_name = "Power_Management_ICs"

    return lib_name


def _normalize_net_name(name, idx: int) -> str:
    if name is None:
        return f"N${idx}"
    name_str = str(name).strip()
    return name_str or f"N${idx}"


def _net_sort_key(net) -> tuple[int, str]:
    name = str(getattr(net, "name", "") or "")
    anonymous = 1 if name.startswith("N$") or name == "" else 0
    return (anonymous, name)


def _ref_sort_key(ref: str) -> tuple[str, str]:
    prefix = "".join(ch for ch in ref if ch.isalpha())
    suffix = "".join(ch for ch in ref if ch.isdigit())
    return (prefix, suffix or ref)


def _pin_sort_key(pin) -> tuple[str, str]:
    return (_pin_ref(pin), _pin_num(pin))


def _pin_ref(pin) -> str:
    return str(getattr(pin, "ref", "") or getattr(getattr(pin, "part", None), "ref", "") or "")


def _pin_num(pin) -> str:
    num = getattr(pin, "num", "")
    return str(num) if num is not None else ""


def _sanitize_id(value: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9_]+", "_", value or "")
    sanitized = sanitized.strip("_")
    return sanitized or "NET"


def _unique_id(base: str, used: set[str]) -> str:
    if base not in used:
        used.add(base)
        return base
    idx = 2
    while True:
        candidate = f"{base}_{idx}"
        if candidate not in used:
            used.add(candidate)
            return candidate
        idx += 1
