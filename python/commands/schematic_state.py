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

logger = logging.getLogger('kicad_interface')


def _atom_to_str(atom: Any) -> str:
    """Convert an S-expression atom to string."""
    if isinstance(atom, sexpdata.Symbol):
        return atom.value()
    return str(atom)


def _is_entry(node: Any, name: str) -> bool:
    """Check if a node is an S-expression entry with the given name."""
    return (
        isinstance(node, list)
        and node
        and isinstance(node[0], sexpdata.Symbol)
        and node[0].value() == name
    )


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

    Note: Power symbols are excluded as they are labels, not components.
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

                # Skip power symbols - they are labels, not components
                if lib_id and 'power' in lib_id.lower():
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
                        position = (float(at_node[1]), float(at_node[2]))

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
                        position = (float(at_node[1]), float(at_node[2]))

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
                        position = (float(at_node[1]), float(at_node[2]))

                    labels['global'].append({
                        'name': name,
                        'direction': 'passive',
                        'position': position
                    })
        except Exception as e:
            logger.warning(f"Error extracting label: {e}")
            continue

    # Extract power symbols - they create GLOBAL power nets
    # All power symbols with the same value (e.g., "VCC") are connected globally
    if hasattr(schematic, 'symbol'):
        for symbol in schematic.symbol:
            try:
                lib_id = getattr(getattr(symbol, 'lib_id', None), 'value', '')
                if lib_id and 'power' in lib_id.lower():
                    value = getattr(getattr(symbol.property, 'Value', None), 'value', '')
                    position = None

                    # Get position from symbol
                    if hasattr(symbol, 'at') and symbol.at is not None:
                        coords = list(symbol.at.value)
                        if coords:
                            position = (float(coords[0]), float(coords[1]) if len(coords) > 1 else 0.0)

                    if value:
                        labels['global'].append({
                            'name': value,
                            'direction': 'power',
                            'position': position
                        })
            except Exception as e:
                logger.warning(f"Error extracting power symbol: {e}")
                continue

    return labels


def _build_connection_map(schematic: Schematic, components: List[Dict[str, Any]]) -> List[str]:
    """
    Build a connection map by analyzing component pins and wires.

    Returns a list of connection strings in the format:
    "R1.1 -> C1.2 [Net-0]"
    """
    connections = []

    # Build a spatial index of component pins: (x, y) -> [(reference, pin_number), ...]
    # Note: Power symbols are excluded as they don't have real pins
    pin_locations: Dict[Tuple[float, float], List[Tuple[str, str]]] = defaultdict(list)

    if hasattr(schematic, 'symbol'):
        for symbol in schematic.symbol:
            try:
                reference = getattr(getattr(symbol.property, 'Reference', None), 'value', None)
                if not reference:
                    continue

                # Skip power symbols - they don't have real pins
                lib_id = getattr(getattr(symbol, 'lib_id', None), 'value', '')
                if lib_id and 'power' in lib_id.lower():
                    continue

                # Get pins from the symbol
                if hasattr(symbol, 'pin'):
                    for pin in symbol.pin:
                        try:
                            pin_number = str(getattr(pin, 'number', ''))
                            if not pin_number:
                                continue

                            # Get absolute pin position
                            if hasattr(pin, 'location'):
                                loc = pin.location
                                x = round(float(loc.x), 1)
                                y = round(float(loc.y), 1)
                                pin_locations[(x, y)].append((reference, pin_number))
                        except Exception as e:
                            logger.warning(f"Error extracting pin location: {e}")
                            continue
            except Exception as e:
                logger.warning(f"Error processing symbol for connection map: {e}")
                continue

    # Build a spatial index of wire endpoints and all intermediate points
    # (x, y) -> [wire_indices]
    wire_points: Dict[Tuple[float, float], List[int]] = defaultdict(list)

    if hasattr(schematic, 'wire'):
        for wire_idx, wire in enumerate(schematic.wire):
            try:
                if hasattr(wire, 'points'):
                    points = wire.points
                    if len(points) >= 2:
                        # Add all points (start, end, and any intermediate points)
                        for point in points:
                            if hasattr(point, 'value'):
                                coords = point.value
                                if len(coords) >= 2:
                                    x = round(float(coords[0]), 1)
                                    y = round(float(coords[1]), 1)
                                    wire_points[(x, y)].append(wire_idx)
            except Exception as e:
                logger.warning(f"Error analyzing wire points: {e}")
                continue

    # Build a net map by grouping connected wires
    wire_to_net: Dict[int, int] = {}
    net_counter = 0

    for point, wire_indices in wire_points.items():
        if len(wire_indices) > 1:
            # Multiple wires meet at this point - they're on the same net
            existing_nets = set()
            for wire_idx in wire_indices:
                if wire_idx in wire_to_net:
                    existing_nets.add(wire_to_net[wire_idx])

            if existing_nets:
                # Use the lowest net number
                net_id = min(existing_nets)
                # Merge all nets
                for wire_idx in wire_indices:
                    wire_to_net[wire_idx] = net_id
                # Update all wires that were on the other nets
                for w_idx, n_id in list(wire_to_net.items()):
                    if n_id in existing_nets and n_id != net_id:
                        wire_to_net[w_idx] = net_id
            else:
                # Create new net
                for wire_idx in wire_indices:
                    wire_to_net[wire_idx] = net_counter
                net_counter += 1
        elif len(wire_indices) == 1:
            # Single wire at this point
            wire_idx = wire_indices[0]
            if wire_idx not in wire_to_net:
                wire_to_net[wire_idx] = net_counter
                net_counter += 1

    # Map each point to its net ID
    point_to_net: Dict[Tuple[float, float], int] = {}
    for point, wire_indices in wire_points.items():
        if wire_indices:
            # Use the net of the first wire at this point (they should all be the same after merging)
            point_to_net[point] = wire_to_net.get(wire_indices[0], wire_indices[0])

    # Build pin-to-pin connections
    # Group pins by net
    net_to_pins: Dict[int, List[str]] = defaultdict(list)

    for point, pins in pin_locations.items():
        # Check if this point is on a net
        if point in point_to_net:
            net_id = point_to_net[point]
            for reference, pin_number in pins:
                pin_ref = f"{reference}.{pin_number}"
                net_to_pins[net_id].append(pin_ref)

    # Generate connection strings
    for net_id, pins in sorted(net_to_pins.items()):
        if len(pins) >= 2:
            # Create connections between all pairs of pins on this net
            for i in range(len(pins) - 1):
                connections.append(f"{pins[i]} -> {pins[i+1]} [Net-{net_id}]")
        elif len(pins) == 1:
            # Single pin on this net (unconnected or connected to label/power)
            connections.append(f"{pins[0]} [Net-{net_id}]")

    return connections


def get_schematic_state(schematic: Schematic, show_details: bool = False) -> str:
    """
    Extract high-level schematic state representation as a text summary.

    Args:
        schematic: The schematic to analyze
        show_details: If False (default), shows only topology and electrical properties.
                     If True, includes all visual layout details (coordinates, rotation, footprints).

    Returns:
        Text summary string representing the schematic state
    """
    # Extract components
    components = _extract_components(schematic)

    # Extract labels
    labels = _extract_labels(schematic)

    # Build connection map
    connections = _build_connection_map(schematic, components)

    # Generate text summary
    summary_lines = []
    summary_lines.append("=== Schematic State ===\n")

    # Components section
    summary_lines.append("Allocated components and pins:")
    for comp in components:
        if show_details:
            # Detailed mode: Show library:symbol, value, footprint, position, rotation
            comp_line = f"{comp['reference']}: {comp['symbol']}"
            if comp['library']:
                comp_line += f", {comp['library']}:{comp['symbol']}"
            if comp['value']:
                comp_line += f", {comp['value']}"
            summary_lines.append(comp_line)

            # Show footprint
            if comp['footprint']:
                summary_lines.append(f"  Footprint: {comp['footprint']}")

            # Show position and rotation
            if comp['position'] is not None:
                x, y = comp['position']
                rot = comp['rotation'] if comp['rotation'] is not None else 0.0
                summary_lines.append(f"  Position: ({x}, {y}), Rotation: {rot}°")

            # Show pins with positions
            for pin in comp['pins']:
                pin_line = f"  Pin {pin['number']}: {pin['type']}"
                if pin['name']:
                    pin_line += f" ({pin['name']})"
                if pin['position'] is not None:
                    px, py = pin['position']
                    pin_line += f", Position: ({px}, {py})"
                summary_lines.append(pin_line)
        else:
            # Simple mode: Show only symbol, value, and pin types
            comp_line = f"{comp['reference']}: {comp['symbol']}"
            if comp['value']:
                comp_line += f", {comp['value']}"
            summary_lines.append(comp_line)

            # Show pins without positions
            for pin in comp['pins']:
                pin_line = f"  Pin {pin['number']}: {pin['type']}"
                if pin['name']:
                    pin_line += f" ({pin['name']})"
                summary_lines.append(pin_line)

    summary_lines.append("")

    # Labels section - always show full details (direction is electrical property)
    has_labels = labels['hierarchical'] or labels['global']
    if has_labels:
        summary_lines.append("Labels:")

        # Hierarchical labels - show type since they're special
        for label in labels['hierarchical']:
            label_line = f"  {label['name']}: hierarchical, {label['direction']}"
            if show_details and label['position'] is not None:
                x, y = label['position']
                label_line += f", Position: ({x}, {y})"
            summary_lines.append(label_line)

        # Global labels - show name and direction (electrical property)
        for label in labels['global']:
            label_line = f"  {label['name']}: {label['direction']}"
            if show_details and label['position'] is not None:
                x, y = label['position']
                label_line += f", Position: ({x}, {y})"
            summary_lines.append(label_line)

        summary_lines.append("")

    # Connection map section
    if connections:
        summary_lines.append("Connection map:")
        for conn in connections:
            summary_lines.append(f"  {conn}")

    return '\n'.join(summary_lines)

