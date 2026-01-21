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


def _is_no_connect_net(net: object) -> bool:
    """Return True if this SKiDL net represents an explicit no-connect (NC)."""
    try:
        from skidl.pin import pin_drives

        return getattr(net, "drive", None) == pin_drives.NOCONNECT
    except Exception:
        name = getattr(net, "name", "") or ""
        return isinstance(name, str) and name.startswith("__NOCONNECT")


def build_simple_logic_hints(
    circuit,
    interface_nets: Iterable[str] | None = None,
    *,
    special_pin_threshold: int = SPECIAL_PIN_THRESHOLD,
    label_high_fanout_nets: bool = False,
    high_fanout_threshold: int = 4,
    force_label_net_names: Iterable[str] | None = None,
    power_symbol_map: dict[str, str] | None = None,
    use_direct_connections: bool = False,
    cluster_components: bool = False,
    cluster_max_connections: int = 12,
    cluster_min_size: int = 4,
    cluster_max_size: int = 0,
    cluster_ignore_fanout_ge: int = 8,
    cluster_ignore_nets: Iterable[str] | None = None,
) -> dict:
    """
    Build logic hints that apply the satellite-style rules:
    - Special parts (pin count >= threshold) connect via net labels only.
    - Optionally labelize high-fanout nets (refs >= threshold) to reduce crossings.
    - Each component gets its own power symbol per supported power net.
    - Interface nets get a hierarchical label anchor.
    - Optionally emit direct connections instead of layout ordering.
    - Optionally split dense circuits into visual clusters by cutting
      inter-cluster nets into labels (still 100% netlist-accurate).
    - Optionally force selected nets to be labelized (cut into labels) regardless
      of fanout/clustering.
    """
    interface_set = {str(n) for n in (interface_nets or []) if n is not None}
    power_symbol_map = power_symbol_map or POWER_SYMBOL_MAP
    forced_label_nets = {str(n).strip() for n in (force_label_net_names or []) if str(n).strip()}

    connected_pins = _connected_pins_by_ref(circuit)
    special_refs = {
        part.ref
        for part in getattr(circuit, "parts", []) or []
        if getattr(part, "ref", None)
        and _pin_count(part, connected_pins) >= special_pin_threshold
    }
    pin_side_map = _pin_sides(circuit, SymbolGeometryFetcher())

    ref_to_cluster: dict[str, int] = {}
    if cluster_components:
        ignore_nets = {str(n) for n in (cluster_ignore_nets or []) if n is not None}
        power_net_names = set(power_symbol_map.keys())
        ignore_nets |= power_net_names
        ignore_nets |= interface_set
        ref_to_cluster = _cluster_refs_by_connectivity(
            circuit,
            max_cluster_connections=cluster_max_connections,
            min_cluster_size=cluster_min_size,
            max_cluster_size=cluster_max_size,
            ignore_net_names=ignore_nets,
            ignore_fanout_ge=cluster_ignore_fanout_ge,
            power_net_names=power_net_names,
            interface_net_names=interface_set,
        )

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
        if _is_no_connect_net(net):
            continue
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

        if forced_label_nets and net_name in forced_label_nets:
            label_type = "hierarchical" if is_interface else "local"
            for ref in refs:
                for pin_num in sorted(component_pins.get(ref, []), key=str):
                    label_id = add_label(f"{net_name}_{ref}_p{pin_num}", net_name, label_type)
                    side = pin_side_map.get(ref, {}).get(str(pin_num))
                    add_connection(label_id, ref, side)
                    add_adjacency(label_id, ref, side)
            continue

        if ref_to_cluster:
            net_clusters = {ref_to_cluster.get(ref) for ref in refs}
            net_clusters.discard(None)
            if len(net_clusters) > 1:
                # Cut net across clusters: replace with per-cluster labels.
                label_type = "hierarchical" if is_interface else "local"
                for cluster_id in sorted(net_clusters):
                    label_id = add_label(f"{net_name}_cluster_{cluster_id}", net_name, label_type)
                    for ref in refs:
                        if ref_to_cluster.get(ref) != cluster_id:
                            continue
                        side = choose_side(ref, component_pins.get(ref, []))
                        add_connection(label_id, ref, side)
                        add_adjacency(label_id, ref, side)
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


def append_forced_net_labels(
    logic_hints: dict,
    circuit,
    force_label_net_names: Iterable[str],
    *,
    interface_nets: Iterable[str] | None = None,
    use_direct_connections: bool = False,
    power_symbol_map: dict[str, str] | None = None,
) -> dict:
    """
    Augment an existing logic-hints dict by forcing selected nets to be labelized.

    This is useful when a caller provides custom `elk_support_fields` but still
    wants to apply the layout-first auto-cut pass (which needs to inject labels
    after analyzing crossings/lengths).
    """

    forced_label_nets = {str(n).strip() for n in (force_label_net_names or []) if str(n).strip()}
    if not forced_label_nets:
        return logic_hints

    interface_set = {str(n) for n in (interface_nets or []) if n is not None}
    power_symbol_map = power_symbol_map or POWER_SYMBOL_MAP
    power_net_names = set(power_symbol_map.keys())

    elk_fields = logic_hints.setdefault("elk_support_fields", {})
    if not isinstance(elk_fields, dict):
        elk_fields = {}
        logic_hints["elk_support_fields"] = elk_fields

    net_labels: list[dict] = elk_fields.setdefault("net_labels", [])
    layout_chains: list[dict] = elk_fields.setdefault("layout_chains", [])
    direct_connections: list[dict] = elk_fields.setdefault("direct_connections", [])
    constraints: list[dict] = elk_fields.setdefault("constraints", [])

    used_label_ids = {entry.get("id") for entry in net_labels if isinstance(entry, dict) and entry.get("id")}
    used_chain_ids = {entry.get("id") for entry in layout_chains if isinstance(entry, dict) and entry.get("id")}

    # If the caller already labelized a net explicitly, don't add more labels.
    already_labeled_nets = {
        str(entry.get("net_name") or entry.get("id") or "").strip()
        for entry in net_labels
        if isinstance(entry, dict) and (entry.get("net_name") or entry.get("id"))
    }

    pin_side_map = _pin_sides(circuit, SymbolGeometryFetcher())

    def add_label(base_id: str, net_name: str, label_type: str, shape: str = "bidirectional") -> str:
        label_id = _unique_id(_sanitize_id(base_id), used_label_ids)
        label = {"id": label_id, "type": label_type, "net_name": net_name}
        if label_type == "hierarchical":
            label["shape"] = shape
        net_labels.append(label)
        return label_id

    def add_chain(base_id: str, path: list[str]) -> str:
        chain_id = _unique_id(_sanitize_id(f"chain_{base_id}"), used_chain_ids)
        layout_chains.append({"id": chain_id, "path": path})
        return chain_id

    used_constraints: set[tuple[str, str, str]] = set()
    for c in constraints:
        if not isinstance(c, dict):
            continue
        if c.get("type") != "ADJACENT":
            continue
        comps = c.get("components")
        if not isinstance(comps, list) or len(comps) != 2:
            continue
        a, b = comps[0], comps[1]
        if isinstance(a, str) and isinstance(b, str):
            used_constraints.add((a, b, ""))

    def add_connection(node_id: str, ref: str, side: str | None) -> None:
        if use_direct_connections:
            entry = {"node": node_id, "component": ref}
            if side in {"left", "right"}:
                entry["side"] = side
            direct_connections.append(entry)
            return
        path = [node_id, ref] if side == "left" else [ref, node_id]
        add_chain(node_id, path)

    def add_adjacency(node_id: str, ref: str, side: str | None) -> None:
        if use_direct_connections:
            return
        if side not in {"left", "right"}:
            return
        components = [node_id, ref] if side == "left" else [ref, node_id]
        key = (components[0], components[1], "")
        if key in used_constraints:
            return
        used_constraints.add(key)
        constraints.append({"type": "ADJACENT", "components": components})

    sorted_nets = sorted(getattr(circuit, "nets", []) or [], key=_net_sort_key)
    for idx, net in enumerate(sorted_nets, start=1):
        if _is_no_connect_net(net):
            continue
        net_name = _normalize_net_name(getattr(net, "name", None), idx)
        if net_name in power_net_names:
            continue
        if net_name not in forced_label_nets:
            continue
        if net_name in already_labeled_nets:
            continue

        component_pins: dict[str, list[str]] = {}
        for pin in sorted(getattr(net, "pins", []) or [], key=_pin_sort_key):
            ref = _pin_ref(pin)
            num = _pin_num(pin)
            if not ref or not num:
                continue
            component_pins.setdefault(ref, []).append(num)

        refs = sorted(component_pins.keys(), key=_ref_sort_key)
        if not refs:
            continue

        label_type = "hierarchical" if net_name in interface_set else "local"
        for ref in refs:
            for pin_num in sorted(component_pins.get(ref, []), key=str):
                label_id = add_label(f"{net_name}_{ref}_p{pin_num}", net_name, label_type)
                side = pin_side_map.get(ref, {}).get(str(pin_num))
                add_connection(label_id, ref, side)
                add_adjacency(label_id, ref, side)

    return logic_hints


def append_forced_component_net_labels(
    logic_hints: dict,
    circuit,
    force_label_component_nets: Iterable[tuple[str, str]],
    *,
    interface_nets: Iterable[str] | None = None,
    use_direct_connections: bool = False,
    power_symbol_map: dict[str, str] | None = None,
) -> dict:
    """
    Augment an existing logic-hints dict by forcing selected (component, net) endpoints
    to connect via labels.

    This is used by the edge-level auto-cut strategy: cut only specific wire
    connections by adding labels at the endpoints instead of labelizing the whole net.
    """

    requested = {(str(ref).strip(), str(net).strip()) for ref, net in (force_label_component_nets or [])}
    requested = {(ref, net) for ref, net in requested if ref and net}
    if not requested:
        return logic_hints

    interface_set = {str(n) for n in (interface_nets or []) if n is not None}
    power_symbol_map = power_symbol_map or POWER_SYMBOL_MAP
    power_net_names = set(power_symbol_map.keys())

    elk_fields = logic_hints.setdefault("elk_support_fields", {})
    if not isinstance(elk_fields, dict):
        elk_fields = {}
        logic_hints["elk_support_fields"] = elk_fields

    net_labels: list[dict] = elk_fields.setdefault("net_labels", [])
    layout_chains: list[dict] = elk_fields.setdefault("layout_chains", [])
    direct_connections: list[dict] = elk_fields.setdefault("direct_connections", [])
    constraints: list[dict] = elk_fields.setdefault("constraints", [])

    used_label_ids = {entry.get("id") for entry in net_labels if isinstance(entry, dict) and entry.get("id")}
    used_chain_ids = {entry.get("id") for entry in layout_chains if isinstance(entry, dict) and entry.get("id")}

    # If a (component, net) already has a label node connected, don't add another
    # one. This prevents duplicate labels (same net name) being wired to the same
    # pin when the base logic already introduced an interface/high-fanout label.
    label_id_to_net: dict[str, str] = {}
    for entry in net_labels:
        if not isinstance(entry, dict):
            continue
        label_id = entry.get("id")
        if not isinstance(label_id, str) or not label_id.strip():
            continue
        net_name = entry.get("net_name", label_id)
        if not isinstance(net_name, str) or not net_name.strip():
            continue
        label_id_to_net[label_id.strip()] = net_name.strip()

    existing_labeled_endpoints: set[tuple[str, str]] = set()
    label_ids = set(label_id_to_net.keys())

    def _record_label_connection(node_id: str, ref: str) -> None:
        net_name = label_id_to_net.get(node_id)
        if not net_name:
            return
        ref = str(ref).strip()
        if not ref:
            return
        existing_labeled_endpoints.add((ref, net_name))

    for conn in direct_connections:
        if not isinstance(conn, dict):
            continue
        node_id = conn.get("node")
        ref = conn.get("component")
        if not isinstance(node_id, str) or not isinstance(ref, str):
            continue
        node_id = node_id.strip()
        ref = ref.strip()
        if node_id in label_ids:
            _record_label_connection(node_id, ref)

    for chain in layout_chains:
        if not isinstance(chain, dict):
            continue
        path = chain.get("path")
        if not isinstance(path, list) or len(path) < 2:
            continue
        for left, right in zip(path, path[1:]):
            if not isinstance(left, str) or not isinstance(right, str):
                continue
            if left in label_ids and right not in label_ids:
                _record_label_connection(left, right)
            elif right in label_ids and left not in label_ids:
                _record_label_connection(right, left)

    pin_side_map = _pin_sides(circuit, SymbolGeometryFetcher())

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

    def add_label(base_id: str, net_name: str, label_type: str, shape: str = "bidirectional") -> str:
        label_id = _unique_id(_sanitize_id(base_id), used_label_ids)
        label = {"id": label_id, "type": label_type, "net_name": net_name}
        if label_type == "hierarchical":
            label["shape"] = shape
        net_labels.append(label)
        return label_id

    def add_chain(base_id: str, path: list[str]) -> str:
        chain_id = _unique_id(_sanitize_id(f"chain_{base_id}"), used_chain_ids)
        layout_chains.append({"id": chain_id, "path": path})
        return chain_id

    used_constraints: set[tuple[str, str, str]] = set()
    for c in constraints:
        if not isinstance(c, dict):
            continue
        if c.get("type") != "ADJACENT":
            continue
        comps = c.get("components")
        if not isinstance(comps, list) or len(comps) != 2:
            continue
        a, b = comps[0], comps[1]
        if isinstance(a, str) and isinstance(b, str):
            used_constraints.add((a, b, ""))

    def add_connection(node_id: str, ref: str, side: str | None) -> None:
        if use_direct_connections:
            entry = {"node": node_id, "component": ref}
            if side in {"left", "right"}:
                entry["side"] = side
            direct_connections.append(entry)
            return
        path = [node_id, ref] if side == "left" else [ref, node_id]
        add_chain(node_id, path)

    def add_adjacency(node_id: str, ref: str, side: str | None) -> None:
        if use_direct_connections:
            return
        if side not in {"left", "right"}:
            return
        components = [node_id, ref] if side == "left" else [ref, node_id]
        key = (components[0], components[1], "")
        if key in used_constraints:
            return
        used_constraints.add(key)
        constraints.append({"type": "ADJACENT", "components": components})

    # Build a lookup of pins per (ref, net) so we can decide if/where to place the label.
    pins_by_ref_net: dict[tuple[str, str], list[str]] = {}
    for idx, net in enumerate(sorted(getattr(circuit, "nets", []) or [], key=_net_sort_key), start=1):
        if _is_no_connect_net(net):
            continue
        net_name = _normalize_net_name(getattr(net, "name", None), idx)
        if net_name in power_net_names:
            continue
        for pin in sorted(getattr(net, "pins", []) or [], key=_pin_sort_key):
            ref = _pin_ref(pin)
            num = _pin_num(pin)
            if not ref or not num:
                continue
            pins_by_ref_net.setdefault((ref, net_name), []).append(num)

    for ref, net_name in sorted(requested, key=lambda item: (_ref_sort_key(item[0]), item[1])):
        if net_name in power_net_names:
            continue
        if (ref, net_name) in existing_labeled_endpoints:
            continue
        pins = pins_by_ref_net.get((ref, net_name), [])
        if not pins:
            continue

        label_type = "hierarchical" if net_name in interface_set else "local"
        label_id = add_label(f"{net_name}_{ref}_cut", net_name, label_type)
        side = choose_side(ref, pins)
        add_connection(label_id, ref, side)
        add_adjacency(label_id, ref, side)

    return logic_hints


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


_PASSIVE_REF_PREFIXES = {"R", "C", "L", "D"}


def _cluster_refs_by_connectivity(
    circuit,
    *,
    max_cluster_connections: int,
    min_cluster_size: int,
    max_cluster_size: int,
    ignore_net_names: set[str],
    ignore_fanout_ge: int,
    power_net_names: set[str],
    interface_net_names: set[str],
) -> dict[str, int]:
    """
    Cluster component references using a weighted connectivity graph and a
    "connection complexity" budget.

    The goal is visual clustering, not exact community detection:
    - Build a weighted part graph where each multi-pin net contributes
      an edge weight of 1/(fanout-1) between all pairs of connected refs.
    - Ignore very high-fanout nets (typically power/buses) so they don't
      collapse everything into a single cluster.
    - Greedily grow clusters while keeping each cluster under a maximum
      connection complexity budget (branching + cycles in the signal
      connectivity graph). This keeps long chains intact while splitting
      "spiderweb" regions.
    """
    parts = getattr(circuit, "parts", []) or []
    refs = sorted(
        {str(getattr(p, "ref", "") or "") for p in parts if getattr(p, "ref", None)},
        key=_ref_sort_key,
    )
    if not refs:
        return {}

    ref_set = set(refs)
    adjacency: dict[str, dict[str, float]] = {ref: {} for ref in refs}
    degree: dict[str, float] = {ref: 0.0 for ref in refs}

    sorted_nets = sorted(getattr(circuit, "nets", []) or [], key=_net_sort_key)
    nets_info: list[tuple[str, list[str]]] = []
    ref_to_nets: dict[str, list[int]] = {ref: [] for ref in refs}

    for idx, net in enumerate(sorted_nets, start=1):
        net_name = _normalize_net_name(getattr(net, "name", None), idx)
        pins = getattr(net, "pins", []) or []
        net_refs = sorted({_pin_ref(pin) for pin in pins if _pin_ref(pin) in ref_set}, key=_ref_sort_key)
        if len(net_refs) < 2:
            continue
        net_idx = len(nets_info)
        nets_info.append((net_name, net_refs))
        for ref in net_refs:
            ref_to_nets[ref].append(net_idx)

    net_is_signal = [False] * len(nets_info)
    for net_idx, (net_name, net_refs) in enumerate(nets_info):
        if net_name in ignore_net_names:
            continue
        fanout = len(net_refs)
        if ignore_fanout_ge > 0 and fanout >= ignore_fanout_ge:
            continue
        net_is_signal[net_idx] = True
        weight = 1.0 / float(fanout - 1)
        for i, ref_a in enumerate(net_refs):
            for ref_b in net_refs[i + 1 :]:
                adjacency[ref_a][ref_b] = adjacency[ref_a].get(ref_b, 0.0) + weight
                adjacency[ref_b][ref_a] = adjacency[ref_b].get(ref_a, 0.0) + weight
                degree[ref_a] += weight
                degree[ref_b] += weight

    def is_passive(ref: str) -> bool:
        prefix = "".join(ch for ch in ref if ch.isalpha()).upper()
        return prefix in _PASSIVE_REF_PREFIXES

    if not any(net_is_signal):
        # Nothing meaningful to cluster (only ignored/high-fanout nets).
        return {ref: 0 for ref in refs}

    def net_weight_scale(net_name: str, fanout: int) -> float:
        scale = 1.0
        if net_name in power_net_names:
            scale = 0.05
        elif net_name in interface_net_names:
            scale = 0.25
        elif net_name in ignore_net_names:
            scale = 0.10
        if ignore_fanout_ge > 0 and fanout >= ignore_fanout_ge:
            scale *= 0.02
        return scale

    size_cap = max_cluster_size if max_cluster_size > 0 else None
    max_connections = float(max_cluster_connections) if max_cluster_connections > 0 else float("inf")
    metrics_cache: dict[tuple[str, ...], tuple[int, int, int]] = {}

    def cluster_metrics(refs_in_cluster: Iterable[str]) -> tuple[int, int, int]:
        """Return (cycle_rank, branchiness, tangledness) for signal connectivity."""
        key = tuple(sorted(set(refs_in_cluster), key=_ref_sort_key))
        cached = metrics_cache.get(key)
        if cached is not None:
            return cached
        ref_subset = set(key)
        if not ref_subset:
            metrics_cache[key] = (0, 0, 0)
            return metrics_cache[key]

        net_counts: dict[int, int] = {}
        for ref in ref_subset:
            for net_idx in ref_to_nets.get(ref, []):
                if 0 <= net_idx < len(net_is_signal) and net_is_signal[net_idx]:
                    net_counts[net_idx] = net_counts.get(net_idx, 0) + 1

        internal_nets = {net_idx: cnt for net_idx, cnt in net_counts.items() if cnt >= 2}
        if not internal_nets:
            metrics_cache[key] = (0, 0, 0)
            return metrics_cache[key]

        ref_adj: dict[str, list[int]] = {ref: [] for ref in ref_subset}
        net_adj: dict[int, list[str]] = {}
        edges = 0
        for net_idx, cnt in internal_nets.items():
            edges += cnt
            net_refs = [ref for ref in nets_info[net_idx][1] if ref in ref_subset]
            net_adj[net_idx] = net_refs
            for ref in net_refs:
                ref_adj[ref].append(net_idx)

        # Connection complexity: count only nets that "branch" (fanout > 2) plus cycles.
        # A long chain or hub-and-spoke of point-to-point nets remains cheap.
        branchiness = sum(max(0, cnt - 2) for cnt in internal_nets.values())

        seen_refs: set[str] = set()
        seen_nets: set[int] = set()
        components = 0
        for ref in key:
            if ref in seen_refs:
                continue
            components += 1
            stack: list[tuple[str, str | int]] = [("ref", ref)]
            seen_refs.add(ref)
            while stack:
                kind, node = stack.pop()
                if kind == "ref":
                    for nb_net in ref_adj.get(str(node), []):
                        if nb_net in seen_nets:
                            continue
                        seen_nets.add(nb_net)
                        stack.append(("net", nb_net))
                else:
                    for nb_ref in net_adj.get(int(node), []):
                        if nb_ref in seen_refs:
                            continue
                        seen_refs.add(nb_ref)
                        stack.append(("ref", nb_ref))

        nodes = len(ref_subset) + len(internal_nets)
        cycle_rank = max(0, edges - nodes + components)
        tangledness = branchiness + cycle_rank
        metrics_cache[key] = (cycle_rank, branchiness, tangledness)
        return metrics_cache[key]

    def fits_budget(refs_in_cluster: Iterable[str]) -> bool:
        refs_set_local = set(refs_in_cluster)
        if size_cap is not None and len(refs_set_local) > size_cap:
            return False
        if max_connections == float("inf"):
            return True
        _, _, tangledness = cluster_metrics(refs_set_local)
        return float(tangledness) <= max_connections + 1e-9

    core_refs = [ref for ref in refs if degree.get(ref, 0.0) > 0.0]
    if not core_refs:
        return {ref: 0 for ref in refs}
    unassigned = set(core_refs)
    clusters: list[list[str]] = []

    while unassigned:
        seed = min(
            unassigned,
            key=lambda ref: (is_passive(ref), -degree.get(ref, 0.0), _ref_sort_key(ref)),
        )
        cluster: list[str] = [seed]
        unassigned.remove(seed)

        candidate_scores: dict[str, float] = {
            nb: w for nb, w in adjacency.get(seed, {}).items() if nb in unassigned
        }

        while candidate_scores:
            if size_cap is not None and len(cluster) >= size_cap:
                break

            candidates = sorted(
                candidate_scores.keys(),
                key=lambda ref: (
                    -candidate_scores.get(ref, 0.0),
                    -degree.get(ref, 0.0),
                    int(is_passive(ref)),
                    _ref_sort_key(ref),
                ),
            )

            next_ref = None
            for cand in candidates:
                if fits_budget(cluster + [cand]):
                    next_ref = cand
                    break
            if next_ref is None:
                break

            cluster.append(next_ref)
            unassigned.remove(next_ref)
            candidate_scores.pop(next_ref, None)

            for nb, w in adjacency.get(next_ref, {}).items():
                if nb in unassigned:
                    candidate_scores[nb] = candidate_scores.get(nb, 0.0) + w

        clusters.append(cluster)

    # Optional refinement: move nodes to improve internal connectivity (signal nets only).
    # Keeps determinism via sorted iteration order.
    def cluster_score(ref: str, cluster_id: int) -> float:
        score = 0.0
        for nb, w in adjacency.get(ref, {}).items():
            if ref_to_cluster.get(nb) == cluster_id:
                score += w
        return score

    ref_to_cluster: dict[str, int] = {}
    cluster_sizes: list[int] = []
    for cid, cluster in enumerate(clusters):
        cluster_sizes.append(len(cluster))
        for ref in cluster:
            ref_to_cluster[ref] = cid

    for _ in range(2):
        changed = False
        for ref in core_refs:
            cur = ref_to_cluster.get(ref)
            if cur is None:
                continue
            # Candidate clusters are the clusters of adjacent nodes.
            neighbor_clusters = {
                ref_to_cluster.get(nb)
                for nb in adjacency.get(ref, {}).keys()
                if nb in ref_to_cluster
            }
            neighbor_clusters.discard(cur)
            if not neighbor_clusters:
                continue
            cur_score = cluster_score(ref, cur)
            best_cluster = cur
            best_gain = 0.0
            for cand in sorted(neighbor_clusters):
                if cand is None:
                    continue
                if max_cluster_size > 0 and cluster_sizes[cand] >= max_cluster_size:
                    continue
                # Enforce complexity budget if enabled.
                if max_cluster_connections > 0:
                    cur_refs = {r for r, cid in ref_to_cluster.items() if cid == cur and r != ref}
                    cand_refs = {r for r, cid in ref_to_cluster.items() if cid == cand} | {ref}
                    _, _, cur_tangles = cluster_metrics(cur_refs)
                    _, _, cand_tangles = cluster_metrics(cand_refs)
                    if cur_tangles > max_cluster_connections or cand_tangles > max_cluster_connections:
                        continue
                cand_score = cluster_score(ref, cand)
                gain = cand_score - cur_score
                if gain > best_gain + 1e-9:
                    best_gain = gain
                    best_cluster = cand
            if best_cluster != cur:
                cluster_sizes[cur] -= 1
                cluster_sizes[best_cluster] += 1
                ref_to_cluster[ref] = best_cluster
                changed = True
        if not changed:
            break

    # Attach isolates (no signal connectivity) to the most relevant cluster using all nets.
    # This keeps decoupling caps (rail + GND) with the block they decouple without letting
    # global rails dominate the primary clustering.
    isolate_refs = [ref for ref in refs if ref not in ref_to_cluster]
    if isolate_refs and ref_to_cluster:
        def cluster_capacity_ok(cluster_id: int) -> bool:
            if max_cluster_size <= 0:
                return 0 <= cluster_id < len(cluster_sizes)
            return 0 <= cluster_id < len(cluster_sizes) and cluster_sizes[cluster_id] < max_cluster_size

        # Deterministic isolate processing order.
        for ref in sorted(isolate_refs, key=_ref_sort_key):
            scores: dict[int, float] = {}
            for net_idx in ref_to_nets.get(ref, []):
                net_name, net_refs = nets_info[net_idx]
                fanout = len(net_refs)
                scale = net_weight_scale(net_name, fanout)
                if scale <= 0.0:
                    continue
                weight = scale / float(fanout - 1)
                for nb in net_refs:
                    if nb == ref:
                        continue
                    cid = ref_to_cluster.get(nb)
                    if cid is None:
                        continue
                    nb_weight = weight * (1.5 if not is_passive(nb) else 1.0)
                    scores[cid] = scores.get(cid, 0.0) + nb_weight

            if not scores:
                # No informative nets: attach to the smallest cluster to avoid creating noise clusters.
                target = min(range(len(cluster_sizes)), key=lambda cid: (cluster_sizes[cid], cid))
            else:
                # Prefer clusters with capacity, but do not force new clusters for passives.
                sorted_candidates = sorted(
                    scores.items(),
                    key=lambda item: (-item[1], cluster_sizes[item[0]], item[0]),
                )
                target = None
                for cid, _ in sorted_candidates:
                    if cluster_capacity_ok(cid):
                        target = cid
                        break
                if target is None:
                    target = sorted_candidates[0][0]

            ref_to_cluster[ref] = target
            cluster_sizes[target] += 1

    # Merge tiny clusters to avoid "dust" groups (still graph-based; no electrical assumptions).
    min_cluster_size = max(0, int(min_cluster_size))
    if min_cluster_size > 1 and ref_to_cluster:
        cluster_to_refs: dict[int, set[str]] = {}
        for ref, cid in ref_to_cluster.items():
            if cid is None:
                continue
            cluster_to_refs.setdefault(cid, set()).add(ref)

        def cluster_capacity_ok(cluster_id: int, add_count: int = 0) -> bool:
            if max_cluster_size <= 0:
                return True
            size = len(cluster_to_refs.get(cluster_id, set()))
            return size + add_count <= max_cluster_size

        def merge_score(from_refs: set[str]) -> dict[int, float]:
            scores: dict[int, float] = {}
            for ref in from_refs:
                for net_idx in ref_to_nets.get(ref, []):
                    net_name, net_refs = nets_info[net_idx]
                    fanout = len(net_refs)
                    scale = net_weight_scale(net_name, fanout)
                    if scale <= 0.0:
                        continue
                    weight = scale / float(fanout - 1)
                    for nb in net_refs:
                        if nb == ref:
                            continue
                        cid = ref_to_cluster.get(nb)
                        if cid is None:
                            continue
                        nb_weight = weight * (1.5 if not is_passive(nb) else 1.0)
                        scores[cid] = scores.get(cid, 0.0) + nb_weight
            return scores

        locked: set[int] = set()
        while True:
            small_clusters = [
                cid
                for cid, refs_in_cluster in cluster_to_refs.items()
                if len(refs_in_cluster) < min_cluster_size
                and cid not in locked
            ]
            if not small_clusters or len(cluster_to_refs) <= 1:
                break
            cid = min(small_clusters, key=lambda c: (len(cluster_to_refs.get(c, set())), c))
            from_refs = cluster_to_refs.get(cid, set())
            if not from_refs:
                cluster_to_refs.pop(cid, None)
                continue

            scores = merge_score(from_refs)
            scores.pop(cid, None)
            candidates = list(cluster_to_refs.keys())
            candidates = [c for c in candidates if c != cid]

            def candidate_order(c: int) -> tuple[float, int, int]:
                return (-scores.get(c, 0.0), -len(cluster_to_refs.get(c, set())), c)

            ordered_candidates = sorted(candidates, key=candidate_order)
            target = None
            for cand in ordered_candidates:
                if not cluster_capacity_ok(cand, add_count=len(from_refs)):
                    continue
                if max_cluster_connections > 0:
                    merged = set(cluster_to_refs.get(cand, set())) | set(from_refs)
                    _, _, merged_tangles = cluster_metrics(merged)
                    if merged_tangles > max_cluster_connections:
                        continue
                target = cand
                break
            if target is None:
                locked.add(cid)
                continue

            cluster_to_refs.setdefault(target, set()).update(from_refs)
            for ref in from_refs:
                ref_to_cluster[ref] = target
            cluster_to_refs.pop(cid, None)

    # Re-pack cluster ids to be dense and deterministic (sorted by size desc).
    cluster_to_refs: dict[int, list[str]] = {}
    for ref, cid in ref_to_cluster.items():
        if cid is None:
            continue
        cluster_to_refs.setdefault(cid, []).append(ref)
    for refs_in_cluster in cluster_to_refs.values():
        refs_in_cluster.sort(key=_ref_sort_key)

    ordered = sorted(
        cluster_to_refs.items(),
        key=lambda item: (-len(item[1]), _ref_sort_key(item[1][0]) if item[1] else ("", "")),
    )
    new_ids: dict[int, int] = {old: idx for idx, (old, _) in enumerate(ordered)}
    return {ref: new_ids[cid] for ref, cid in ref_to_cluster.items() if cid in new_ids}
