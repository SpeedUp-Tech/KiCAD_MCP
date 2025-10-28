"""
High-level schematic state representation for AI agents.

This module provides a function to extract a human-readable text summary
of a KiCAD schematic that captures topology and connectivity information.

The summary can be generated in two modes:
- Simple mode (show_details=False): Shows only topology and electrical properties
  (what connects to what, component values, pin types, label directions)
- Detailed mode (show_details=True): Includes all visual layout details
  (coordinates, rotation, footprints, pin positions)
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Set, Tuple, Optional
from collections import defaultdict

from skip import Schematic
import sexpdata
from sexpdata import Symbol as SSymbol
from .grid_utils import snap_to_grid

logger = logging.getLogger('kicad_interface')

COORD_PRECISION = 6


def _coord_key(x: Any, y: Any) -> Tuple[float, float]:
    try:
        return (round(float(x), COORD_PRECISION), round(float(y), COORD_PRECISION))
    except Exception:
        # Fall back to 0.0 when values are missing/unparseable, matching previous behaviour
        return (round(float(x or 0.0), COORD_PRECISION), round(float(y or 0.0), COORD_PRECISION))


def _atom_to_str(atom: Any) -> str:
    """Convert an S-expression atom to string."""
    if isinstance(atom, sexpdata.Symbol):
        return atom.value()
    return str(atom)


def _is_entry(node: Any, name: str) -> bool:
    """Check if a node is an S-expression entry with the given name."""
    if not isinstance(node, list):
        return False
    if not node:
        return False
    first = node[0]
    if not isinstance(first, sexpdata.Symbol):
        return False
    return first.value() == name


def _find_subelement(node: List[Any], name: str) -> Optional[Any]:
    """Find a subelement in an S-expression node."""
    for child in node:
        if _is_entry(child, name):
            return child
    return None


def _extract_pin_info_from_library(lib_symbols_node: List[Any], lib_id: str) -> Dict[str, Dict[str, Any]]:
    """
    Extract pin information from library symbols.
    
    Returns a dict mapping pin numbers to pin info (name, type, etc.)
    """
    pins_info: Dict[str, Dict[str, Any]] = {}
    
    # lib_id format is typically "Library:Symbol"
    symbol_name = lib_id.split(':')[-1] if ':' in lib_id else lib_id
    
    # Search for the symbol definition in lib_symbols
    for child in lib_symbols_node:
        if not _is_entry(child, 'symbol'):
            continue
        
        # Check if this is the symbol we're looking for
        if len(child) < 2:
            continue
        
        sym_name = _atom_to_str(child[1])
        # Symbol names in lib_symbols can be hierarchical like "Device:R" or "Device:R_1_1"
        if not (sym_name == lib_id or sym_name.startswith(lib_id + '_') or sym_name == symbol_name):
            continue
        
        # Extract pins from this symbol definition
        _extract_pins_recursive(child, pins_info)
    
    return pins_info


def _extract_pins_recursive(node: Any, pins_info: Dict[str, Dict[str, Any]]) -> None:
    """Recursively extract pin information from a symbol node."""
    if not isinstance(node, list):
        return
    
    for child in node:
        if _is_entry(child, 'pin'):
            # Pin format: (pin electrical_type shape (at x y rotation) (length len) (name "name" ...) (number "num" ...))
            pin_number = None
            pin_name = None
            pin_type = None
            
            # First element after 'pin' is usually the electrical type
            if len(child) > 1 and isinstance(child[1], sexpdata.Symbol):
                pin_type = child[1].value()
            
            # Find name and number subelements
            for elem in child:
                if _is_entry(elem, 'name') and len(elem) > 1:
                    pin_name = _atom_to_str(elem[1])
                elif _is_entry(elem, 'number') and len(elem) > 1:
                    pin_number = _atom_to_str(elem[1])
            
            if pin_number:
                pins_info[pin_number] = {
                    'name': pin_name or '',
                    'type': pin_type or 'passive'
                }
        elif isinstance(child, list):
            # Recurse into nested symbol definitions
            _extract_pins_recursive(child, pins_info)


def _extract_components(schematic: Schematic) -> List[Dict[str, Any]]:
    """
    Extract component information from schematic.

    Note: Power symbols are excluded as they are treated as labels, not components.
    """
    components = []

    # Find lib_symbols node for pin information
    lib_symbols_node = None
    for node in schematic.tree:
        if _is_entry(node, 'lib_symbols'):
            lib_symbols_node = node
            break

    # Iterate through all symbols in the schematic
    if hasattr(schematic, 'symbol'):
        for symbol in schematic.symbol:
            try:
                # Extract basic component info
                reference = getattr(getattr(symbol.property, 'Reference', None), 'value', 'Unknown')
                value = getattr(getattr(symbol.property, 'Value', None), 'value', '')
                lib_id = getattr(getattr(symbol, 'lib_id', None), 'value', '')
                footprint = getattr(getattr(symbol.property, 'Footprint', None), 'value', '')

                lib_id_lower = lib_id.lower() if lib_id else ''
                is_power_symbol = 'power' in lib_id_lower

                # Treat all power-library symbols (including PWR_FLAG) as labels instead of components
                if is_power_symbol:
                    continue

                # Extract library and symbol name
                library_name = ''
                symbol_name = ''
                if lib_id:
                    parts = lib_id.split(':')
                    if len(parts) == 2:
                        library_name, symbol_name = parts
                    else:
                        symbol_name = lib_id

                # Extract position and rotation
                position = None
                rotation = None
                if hasattr(symbol, 'at') and symbol.at is not None:
                    coords = list(symbol.at.value)
                    if coords:
                        position = (float(coords[0]), float(coords[1]) if len(coords) > 1 else 0.0)
                        rotation = float(coords[2]) if len(coords) > 2 else 0.0

                # Extract pin information from library definition
                pins = []
                if lib_symbols_node and lib_id:
                    pins_info = _extract_pin_info_from_library(lib_symbols_node, lib_id)

                    # Get pin instances from the symbol
                    if hasattr(symbol, 'pin'):
                        for pin in symbol.pin:
                            pin_number = str(getattr(pin, 'number', ''))
                            pin_data = pins_info.get(pin_number, {})

                            # Get pin position
                            pin_position = None
                            if hasattr(pin, 'location'):
                                loc = pin.location
                                pin_position = (round(float(loc.x), 2), round(float(loc.y), 2))

                            pins.append({
                                'number': pin_number,
                                'name': pin_data.get('name', ''),
                                'type': pin_data.get('type', 'passive'),
                                'position': pin_position
                            })

                components.append({
                    'reference': reference,
                    'value': value,
                    'library': library_name,
                    'symbol': symbol_name,
                    'footprint': footprint,
                    'position': position,
                    'rotation': rotation,
                    'pins': pins
                })
            except Exception as e:
                logger.warning(f"Error extracting component info: {e}")
                continue

    return components


def _extract_labels(schematic: Schematic) -> Dict[str, List[Dict[str, Any]]]:
    """
    Extract labels from schematic with position information.

    All labels except hierarchical are categorized as 'global' since they create
    global nets across the schematic. This includes:
    - global_label nodes
    - label (local label) nodes
    - power symbols (which create global power nets)

    Returns dict with keys: 'hierarchical', 'global'
    Each label includes: name, direction, and position
    """
    labels = {
        'hierarchical': [],
        'global': []
    }

    for node in schematic.tree:
        try:
            if _is_entry(node, 'hierarchical_label'):
                # Format: (hierarchical_label "NAME" (shape input/output) (at x y angle) ...)
                if len(node) > 1:
                    name = _atom_to_str(node[1])
                    shape = 'passive'
                    position = None

                    # Find shape
                    shape_node = _find_subelement(node, 'shape')
                    if shape_node and len(shape_node) > 1:
                        shape = _atom_to_str(shape_node[1])

                    # Find position
                    at_node = _find_subelement(node, 'at')
                    if at_node and len(at_node) >= 3:
                        position = _coord_key(at_node[1], at_node[2])

                    labels['hierarchical'].append({
                        'name': name,
                        'direction': shape,
                        'position': position
                    })

            elif _is_entry(node, 'global_label'):
                # Format: (global_label "NAME" (shape input/output) (at x y angle) ...)
                # These are global nets
                if len(node) > 1:
                    name = _atom_to_str(node[1])
                    shape = 'passive'
                    position = None

                    shape_node = _find_subelement(node, 'shape')
                    if shape_node and len(shape_node) > 1:
                        shape = _atom_to_str(shape_node[1])

                    # Find position
                    at_node = _find_subelement(node, 'at')
                    if at_node and len(at_node) >= 3:
                        position = _coord_key(at_node[1], at_node[2])

                    labels['global'].append({
                        'name': name,
                        'direction': shape,
                        'position': position
                    })

            elif _is_entry(node, 'label'):
                # Format: (label "NAME" (at x y angle) ...)
                # Local labels are treated as global in KiCAD
                if len(node) > 1:
                    name = _atom_to_str(node[1])
                    position = None

                    # Find position
                    at_node = _find_subelement(node, 'at')
                    if at_node and len(at_node) >= 3:
                        position = _coord_key(at_node[1], at_node[2])

                    labels['global'].append({
                        'name': name,
                        'direction': 'passive',
                        'position': position
                    })
        except Exception as e:
            logger.warning(f"Error extracting label: {e}")
            continue

    # Extract power symbols - they create GLOBAL power nets or power flag markers
    # All power symbols with the same value (e.g., "VCC") are connected globally
    if hasattr(schematic, 'symbol'):
        for symbol in schematic.symbol:
            try:
                lib_id = getattr(getattr(symbol, 'lib_id', None), 'value', '')
                lib_id_lower = lib_id.lower() if lib_id else ''
                is_power_symbol = 'power' in lib_id_lower
                is_power_flag = 'pwr_flag' in lib_id_lower

                if not is_power_symbol:
                    continue

                value = getattr(getattr(symbol.property, 'Value', None), 'value', '')
                reference = getattr(getattr(symbol.property, 'Reference', None), 'value', '')
                position = None

                # Get position from symbol
                if hasattr(symbol, 'at') and symbol.at is not None:
                    coords = list(symbol.at.value)
                    if coords:
                        # Snap to schematic grid to match wire coordinates
                        position = (
                            snap_to_grid(float(coords[0])),
                            snap_to_grid(float(coords[1])) if len(coords) > 1 else 0.0,
                        )

                if is_power_flag:
                    label_name = reference or value or lib_id
                    direction = 'power_flag'
                else:
                    label_name = value or reference or lib_id
                    direction = 'power'

                if label_name:
                    entry = {
                        'name': label_name,
                        'direction': direction,
                        'position': position,
                    }
                    if reference:
                        entry['reference'] = reference
                    if value:
                        entry['value'] = value
                    if is_power_flag:
                        entry['power_flag'] = True
                    labels['global'].append(entry)
            except Exception as e:
                logger.warning(f"Error extracting power symbol: {e}")
                continue

    return labels


def _build_connection_map(schematic: Schematic, components: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Build a complete connection map as explicit edges between endpoints.

    Returns a list of JSON-serializable edges with structure:
      { "net": NetName, "a": Endpoint, "b": Endpoint }

    where Endpoint is one of:
      - { "kind": "pin", "ref": Reference, "pin": PinNumber }
      - { "kind": "label", "name": LabelName }

    Notes:
    - Labels (local/global/hierarchical) and power rails are first-class endpoints.
    - NetName comes from any label or power symbol present on the wire's endpoints; otherwise a stable
      synthetic name (Net-(Ref-Pad)) is used based on wire connectivity groups.
    - This function is read-only and reflects the schematic AS-IS.
    """
    # Spatial index of endpoints (pins and labels)
    # (x, y) -> [endpoint_id], where endpoint_id is either "Ref.Pin" or label text (e.g., "GND")
    pin_locations: Dict[Tuple[float, float], List[str]] = defaultdict(list)
    label_locations: Dict[Tuple[float, float], List[str]] = defaultdict(list)

    # Collect pins from symbols (exclude power symbols which have no real pins)
    if hasattr(schematic, 'symbol'):
        for symbol in schematic.symbol:
            try:
                reference = getattr(getattr(symbol.property, 'Reference', None), 'value', None)
                if not reference:
                    continue

                lib_id = getattr(getattr(symbol, 'lib_id', None), 'value', '')
                lib_id_lower = lib_id.lower() if lib_id else ''
                is_power_symbol = 'power' in lib_id_lower
                is_power_flag = 'pwr_flag' in lib_id_lower
                if is_power_symbol and not is_power_flag:
                    # Power symbols don't expose real pins; skip for pin endpoints
                    continue

                if hasattr(symbol, 'pin'):
                    for pin in symbol.pin:
                        try:
                            pin_number = str(getattr(pin, 'number', ''))
                            if not pin_number:
                                continue
                            if hasattr(pin, 'location'):
                                loc = pin.location
                                x, y = _coord_key(loc.x, loc.y)
                                pin_locations[(x, y)].append(f"{reference}.{pin_number}")
                        except Exception as e:
                            logger.warning(f"Error extracting pin location: {e}")
                            continue
            except Exception as e:
                logger.warning(f"Error processing symbol for connection map: {e}")
                continue

    # Collect labels (including power and power flag markers) with positions
    labels = _extract_labels(schematic)
    label_metadata: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for group in ('hierarchical', 'global'):
        for lbl in labels.get(group, []):
            name = lbl.get('name')
            if name:
                label_metadata[name].append(lbl)
    # Combine hierarchical and global labels
    for group in ('hierarchical', 'global'):
        for lbl in labels.get(group, []):
            try:
                name = lbl.get('name')
                pos = lbl.get('position')
                if name and pos is not None:
                    x, y = _coord_key(pos[0], pos[1])
                    label_locations[(x, y)].append(name)
            except Exception as e:
                logger.warning(f"Error indexing label '{lbl}': {e}")
                continue

    # Quick set to recognize which endpoint strings are labels
    label_names_set: Set[str] = set()
    for names in label_locations.values():
        label_names_set.update(names)

    # Helper to convert an endpoint string to a structured endpoint object
    def _endpoint_obj(name: str) -> Dict[str, Any]:
        if name in label_names_set:
            return {"kind": "label", "name": name}
        if "." in name:
            ref, pin = name.split(".", 1)
            return {"kind": "pin", "ref": ref, "pin": pin}
        # Fallback to label if we cannot confidently parse as pin
        return {"kind": "label", "name": name}

    def _fallback_net_name(nodes: List[str], default_id: int) -> str:
        for endpoint in nodes:
            if endpoint in label_names_set:
                continue
            if "." in endpoint:
                ref, pin = endpoint.split(".", 1)
                return f"Net-({ref}-Pad{pin})"
        return f"Net-{default_id}"

    # Build wire endpoint list and a connectivity map for unlabeled net IDs
    wire_points: Dict[Tuple[float, float], List[int]] = defaultdict(list)
    wire_endpoints: Dict[int, Tuple[Tuple[float, float], Tuple[float, float]]] = {}

    if hasattr(schematic, 'wire'):
        for wire_idx, wire in enumerate(schematic.wire):
            try:
                if hasattr(wire, 'points'):
                    points = list(wire.points)
                    if len(points) >= 2:
                        # Index all points for connectivity grouping
                        for pt in points:
                            if hasattr(pt, 'value'):
                                coords = pt.value
                                if len(coords) >= 2:
                                    x, y = _coord_key(coords[0], coords[1])
                                    wire_points[(x, y)].append(wire_idx)
                        # Also record only the two wire endpoints (first and last)
                        p_start = points[0].value
                        p_end = points[-1].value
                        wire_endpoints[wire_idx] = (
                            _coord_key(p_start[0], p_start[1]),
                            _coord_key(p_end[0], p_end[1]),
                        )
            except Exception as e:
                logger.warning(f"Error analyzing wire points: {e}")
                continue

    # Group wires into nets (unlabeled naming fallback)
    wire_to_net: Dict[int, int] = {}
    net_counter = 0

    for point, wire_indices in wire_points.items():
        if len(wire_indices) > 1:
            existing_nets = set()
            for widx in wire_indices:
                if widx in wire_to_net:
                    existing_nets.add(wire_to_net[widx])
            if existing_nets:
                net_id = min(existing_nets)
                for widx in wire_indices:
                    wire_to_net[widx] = net_id
                for w_idx, n_id in list(wire_to_net.items()):
                    if n_id in existing_nets and n_id != net_id:
                        wire_to_net[w_idx] = net_id
            else:
                for widx in wire_indices:
                    wire_to_net[widx] = net_counter
                net_counter += 1
        elif len(wire_indices) == 1:
            widx = wire_indices[0]
            if widx not in wire_to_net:
                wire_to_net[widx] = net_counter
                net_counter += 1

    # Merge both pin and label locations into a single spatial index
    node_locations: Dict[Tuple[float, float], List[str]] = defaultdict(list)
    for pt, nodes in pin_locations.items():
        node_locations[pt].extend(nodes)
    for pt, names in label_locations.items():
        node_locations[pt].extend(names)

    # Build point->net_id map and collect nodes per net id
    point_to_net: Dict[Tuple[float, float], int] = {}
    net_to_nodes: Dict[int, Set[str]] = defaultdict(set)
    for pt, widxs in wire_points.items():
        if not widxs:
            continue
        net_id = wire_to_net.get(widxs[0], widxs[0])
        point_to_net[pt] = net_id
        for node in node_locations.get(pt, []):
            net_to_nodes[net_id].add(node)

    # Determine label-based names for nets (if any)
    net_label_name: Dict[int, str] = {}
    for pt, names in label_locations.items():
        if pt in point_to_net:
            net_id = point_to_net[pt]
            # Prefer non power-flag names when multiple labels occupy the same point
            sorted_names = sorted(set(names))
            power_names = [
                n for n in sorted_names
                if any(lbl.get('direction') == 'power' for lbl in label_metadata.get(n, []))
            ]
            non_flag_names = [
                n for n in sorted_names
                if not any(lbl.get('power_flag') for lbl in label_metadata.get(n, []))
            ]
            if power_names:
                chosen = power_names[0]
            elif non_flag_names:
                chosen = non_flag_names[0]
            else:
                chosen = sorted_names[0]
            net_label_name.setdefault(net_id, chosen)

    # Emit all unordered pairs per net using appropriate net name
    edge_keys: Set[Tuple[str, str, str]] = set()  # (net_name, a, b) canonicalized with a<=b
    for net_id, nodes in sorted(net_to_nodes.items()):
        node_list = sorted(nodes)
        if len(node_list) < 2:
            continue
        # Prefer explicit label name if present on the net, else synthetic
        net_name = net_label_name.get(net_id)
        if not net_name:
            net_name = _fallback_net_name(node_list, net_id)
        # Unordered unique pairs
        for i in range(len(node_list)):
            for j in range(i + 1, len(node_list)):
                a, b = node_list[i], node_list[j]
                a_key, b_key = (a, b) if a <= b else (b, a)
                edge_keys.add((net_name, a_key, b_key))

    # Build structured, deterministically sorted edge list
    result: List[Dict[str, Any]] = []
    for net_name, a_name, b_name in sorted(edge_keys, key=lambda t: (t[0], t[1], t[2])):
        result.append({
            "net": net_name,
            "a": _endpoint_obj(a_name),
            "b": _endpoint_obj(b_name),
        })

    return result


def get_schematic_state(schematic: Schematic, show_details: bool = False, output_format: str = "json") -> Any:
    """
    Extract high-level schematic state representation.

    Args:
        schematic: The schematic to analyze
        show_details: If False (default), shows only topology and electrical properties.
                      If True, includes all visual layout details (coordinates, rotation, footprints).
        output_format: 'json' (default) returns structured data; 'text' returns a formatted summary string.

    Returns:
        Dict with JSON structure if output_format == 'json', else a text string.
    """
    # Extract raw data
    raw_components = _extract_components(schematic)
    raw_labels = _extract_labels(schematic)

    # Build connection map as structured JSON edges
    connections = _build_connection_map(schematic, raw_components)

    # Convert components to JSON-friendly form (and honor detail level)
    components: List[Dict[str, Any]] = []
    for comp in raw_components:
        comp_json: Dict[str, Any] = {
            "reference": comp.get("reference"),
            "symbol": comp.get("symbol"),
            "library": comp.get("library"),
            "value": comp.get("value"),
            "pins": [],
        }
        # Detailed-only fields
        if show_details:
            if comp.get("footprint"):
                comp_json["footprint"] = comp.get("footprint")
            if comp.get("position") is not None:
                x, y = comp.get("position") or (None, None)
                comp_json["position"] = {"x": x, "y": y}
            if comp.get("rotation") is not None:
                comp_json["rotation"] = comp.get("rotation")
        # Pins
        for pin in comp.get("pins", []):
            pin_json: Dict[str, Any] = {
                "number": pin.get("number"),
                "type": pin.get("type"),
                "name": pin.get("name", ""),
            }
            if show_details and pin.get("position") is not None:
                px, py = pin.get("position") or (None, None)
                pin_json["position"] = {"x": px, "y": py}
            comp_json["pins"].append(pin_json)
        components.append(comp_json)

    # Convert labels to a flat list with kind and honor detail level
    labels: List[Dict[str, Any]] = []
    for kind in ("hierarchical", "global"):
        for lbl in raw_labels.get(kind, []):
            item: Dict[str, Any] = {
                "kind": kind,
                "name": lbl.get("name"),
                "direction": lbl.get("direction", "passive"),
            }
            if show_details and lbl.get("position") is not None:
                lx, ly = lbl.get("position") or (None, None)
                item["position"] = {"x": lx, "y": ly}
            labels.append(item)

    state_json: Dict[str, Any] = {
        "mode": "detailed" if show_details else "simple",
        "components": components,
        "labels": labels,
        "connections": connections,
    }

    if str(output_format).lower() == "json":
        return state_json

    # Fallback: text post-process from JSON structure
    def _format_schematic_state_text(state: Dict[str, Any], detailed: bool) -> str:
        lines: List[str] = []
        lines.append("=== Schematic State ===\n")

        lines.append("Allocated components and pins:")
        for comp in state.get("components", []):
            ref = comp.get("reference") or ""
            sym = comp.get("symbol") or ""
            lib = comp.get("library") or ""
            val = comp.get("value") or ""
            if detailed:
                comp_line = f"{ref}: {sym}"
                if lib:
                    comp_line += f", {lib}:{sym}"
                if val:
                    comp_line += f", {val}"
                lines.append(comp_line)
                if comp.get("footprint"):
                    lines.append(f"  Footprint: {comp['footprint']}")
                pos = comp.get("position")
                rot = comp.get("rotation")
                if pos is not None:
                    lines.append(f"  Position: ({pos.get('x')}, {pos.get('y')}), Rotation: {rot or 0.0}°")
                for pin in comp.get("pins", []):
                    pin_line = f"  Pin {pin.get('number')}: {pin.get('type')}"
                    if pin.get("name"):
                        pin_line += f" ({pin['name']})"
                    ppos = pin.get("position")
                    if ppos is not None:
                        pin_line += f", Position: ({ppos.get('x')}, {ppos.get('y')})"
                    lines.append(pin_line)
            else:
                comp_line = f"{ref}: {sym}"
                if val:
                    comp_line += f", {val}"
                lines.append(comp_line)
                for pin in comp.get("pins", []):
                    pin_line = f"  Pin {pin.get('number')}: {pin.get('type')}"
                    if pin.get("name"):
                        pin_line += f" ({pin['name']})"
                    lines.append(pin_line)

        lines.append("")

        lbls = state.get("labels", [])
        if lbls:
            lines.append("Labels:")
            # Hierarchical first
            for label in [l for l in lbls if l.get("kind") == "hierarchical"]:
                label_line = f"  {label.get('name')}: hierarchical, {label.get('direction')}"
                label_value = label.get("value")
                if label_value and label_value != label.get("name"):
                    label_line += f" ({label_value})"
                if detailed and label.get("position") is not None:
                    lpos = label.get("position")
                    label_line += f", Position: ({lpos.get('x')}, {lpos.get('y')})"
                lines.append(label_line)
            # Global next
            for label in [l for l in lbls if l.get("kind") == "global"]:
                label_line = f"  {label.get('name')}: {label.get('direction')}"
                label_value = label.get("value")
                if label_value and label_value != label.get("name"):
                    label_line += f" ({label_value})"
                if detailed and label.get("position") is not None:
                    lpos = label.get("position")
                    label_line += f", Position: ({lpos.get('x')}, {lpos.get('y')})"
                lines.append(label_line)
            lines.append("")

        conns = state.get("connections", [])
        if conns:
            lines.append("Connection map:")
            for edge in conns:
                def _fmt_ep(ep: Dict[str, Any]) -> str:
                    if ep.get("kind") == "pin":
                        return f"{ep.get('ref')}.{ep.get('pin')}"
                    return str(ep.get("name"))
                lines.append(f"  {_fmt_ep(edge.get('a', {}))} - {_fmt_ep(edge.get('b', {}))} [{edge.get('net')}]")

        return "\n".join(lines)

    return _format_schematic_state_text(state_json, show_details)
