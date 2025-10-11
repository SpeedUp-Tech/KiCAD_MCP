"""
High-level schematic state representation for AI agents.

This module provides a function to extract a structured, human-readable
representation of a KiCAD schematic that captures essential topology
and connectivity information without exposing low-level S-expression details.
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
    """Extract component information from schematic."""
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
                
                # Extract library and symbol name
                library_name = ''
                symbol_name = ''
                if lib_id:
                    parts = lib_id.split(':')
                    if len(parts) == 2:
                        library_name, symbol_name = parts
                    else:
                        symbol_name = lib_id
                
                # Extract pin information from library definition
                pins = []
                if lib_symbols_node and lib_id:
                    pins_info = _extract_pin_info_from_library(lib_symbols_node, lib_id)
                    
                    # Get pin instances from the symbol
                    if hasattr(symbol, 'pin'):
                        for pin in symbol.pin:
                            pin_number = str(getattr(pin, 'number', ''))
                            pin_data = pins_info.get(pin_number, {})
                            pins.append({
                                'number': pin_number,
                                'name': pin_data.get('name', ''),
                                'type': pin_data.get('type', 'passive')
                            })
                
                components.append({
                    'reference': reference,
                    'value': value,
                    'library': library_name,
                    'symbol': symbol_name,
                    'footprint': footprint,
                    'pins': pins
                })
            except Exception as e:
                logger.warning(f"Error extracting component info: {e}")
                continue
    
    return components


def _extract_labels(schematic: Schematic) -> Dict[str, List[Dict[str, Any]]]:
    """
    Extract labels from schematic.
    
    Returns dict with keys: 'hierarchical', 'global', 'power'
    """
    labels = {
        'hierarchical': [],
        'global': [],
        'power': []
    }
    
    for node in schematic.tree:
        try:
            if _is_entry(node, 'hierarchical_label'):
                # Format: (hierarchical_label "NAME" (shape input/output) (at x y angle) ...)
                if len(node) > 1:
                    name = _atom_to_str(node[1])
                    shape = 'passive'
                    
                    # Find shape
                    shape_node = _find_subelement(node, 'shape')
                    if shape_node and len(shape_node) > 1:
                        shape = _atom_to_str(shape_node[1])
                    
                    labels['hierarchical'].append({
                        'name': name,
                        'direction': shape
                    })
            
            elif _is_entry(node, 'global_label'):
                # Format: (global_label "NAME" (shape input/output) (at x y angle) ...)
                if len(node) > 1:
                    name = _atom_to_str(node[1])
                    shape = 'passive'
                    
                    shape_node = _find_subelement(node, 'shape')
                    if shape_node and len(shape_node) > 1:
                        shape = _atom_to_str(shape_node[1])
                    
                    labels['global'].append({
                        'name': name,
                        'direction': shape
                    })
            
            elif _is_entry(node, 'label'):
                # Format: (label "NAME" (at x y angle) ...)
                if len(node) > 1:
                    name = _atom_to_str(node[1])
                    labels['global'].append({
                        'name': name,
                        'direction': 'passive'
                    })
        except Exception as e:
            logger.warning(f"Error extracting label: {e}")
            continue
    
    # Extract power symbols (they appear as regular symbols with specific lib_ids)
    if hasattr(schematic, 'symbol'):
        for symbol in schematic.symbol:
            try:
                lib_id = getattr(getattr(symbol, 'lib_id', None), 'value', '')
                if lib_id and 'power' in lib_id.lower():
                    value = getattr(getattr(symbol.property, 'Value', None), 'value', '')
                    if value:
                        labels['power'].append({
                            'name': value,
                            'direction': 'power'
                        })
            except Exception as e:
                logger.warning(f"Error extracting power symbol: {e}")
                continue
    
    return labels


def _build_connection_map(schematic: Schematic, components: List[Dict[str, Any]]) -> List[str]:
    """
    Build a connection map by analyzing wires and their endpoints.

    Returns a list of connection strings in the format:
    "R1.1 -> C1.2 (Net: Net-R1-Pad1)"
    """
    connections = []

    # Build a spatial index of wire endpoints
    # We'll track which wires connect to which points
    wire_endpoints: Dict[Tuple[float, float], List[int]] = defaultdict(list)  # (x, y) -> [wire_indices]

    if hasattr(schematic, 'wire'):
        for wire_idx, wire in enumerate(schematic.wire):
            try:
                # Get wire endpoints
                if hasattr(wire, 'points'):
                    points = wire.points
                    if len(points) >= 2:
                        # Add start and end points
                        for point in [points[0], points[-1]]:
                            if hasattr(point, 'value'):
                                coords = point.value
                                if len(coords) >= 2:
                                    x = round(float(coords[0]), 1)
                                    y = round(float(coords[1]), 1)
                                    wire_endpoints[(x, y)].append(wire_idx)
            except Exception as e:
                logger.warning(f"Error analyzing wire endpoints: {e}")
                continue

    # Build a net map by grouping connected wires
    # Wires that share endpoints are on the same net
    wire_to_net: Dict[int, int] = {}
    net_counter = 0

    for point, wire_indices in wire_endpoints.items():
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

    # Now build a simple text representation
    # For each wire, show what it connects
    if hasattr(schematic, 'wire'):
        for wire_idx, wire in enumerate(schematic.wire):
            try:
                if hasattr(wire, 'points'):
                    points = wire.points
                    if len(points) >= 2:
                        start_point = points[0]
                        end_point = points[-1]

                        if hasattr(start_point, 'value') and hasattr(end_point, 'value'):
                            start_coords = start_point.value
                            end_coords = end_point.value

                            if len(start_coords) >= 2 and len(end_coords) >= 2:
                                start_x = round(float(start_coords[0]), 1)
                                start_y = round(float(start_coords[1]), 1)
                                end_x = round(float(end_coords[0]), 1)
                                end_y = round(float(end_coords[1]), 1)

                                net_id = wire_to_net.get(wire_idx, wire_idx)
                                connections.append(
                                    f"Wire {wire_idx}: ({start_x}, {start_y}) -> ({end_x}, {end_y}) [Net-{net_id}]"
                                )
            except Exception as e:
                logger.warning(f"Error formatting wire connection: {e}")
                continue

    return connections


def get_schematic_state(schematic: Schematic) -> Dict[str, Any]:
    """
    Extract high-level schematic state representation.
    
    Returns a structured dict containing:
    - components: List of components with pins
    - labels: Hierarchical, global, and power labels
    - connections: Connection map between pins
    - summary: Text summary in human-readable format
    """
    # Extract components
    components = _extract_components(schematic)
    
    # Extract labels
    labels = _extract_labels(schematic)
    
    # Build connection map
    connections = _build_connection_map(schematic, components)
    
    # Generate text summary
    summary_lines = []
    summary_lines.append("=== Schematic State Summary ===\n")
    
    # Components section
    summary_lines.append("Allocated components and pins:")
    for comp in components:
        comp_line = f"{comp['reference']}: {comp['symbol']}"
        if comp['library']:
            comp_line += f", {comp['library']}:{comp['symbol']}"
        if comp['footprint']:
            comp_line += f", {comp['footprint']}"
        if comp['value']:
            comp_line += f", {comp['value']}"
        summary_lines.append(comp_line)
        
        for pin in comp['pins']:
            pin_line = f"  Pin {pin['number']}: {pin['type']}"
            if pin['name']:
                pin_line += f" ({pin['name']})"
            summary_lines.append(pin_line)
    
    summary_lines.append("")
    
    # Labels section
    if labels['hierarchical']:
        summary_lines.append("Hierarchical labels:")
        for label in labels['hierarchical']:
            summary_lines.append(f"  {label['name']}: {label['direction']}")
        summary_lines.append("")
    
    if labels['global']:
        summary_lines.append("Global labels:")
        for label in labels['global']:
            summary_lines.append(f"  {label['name']}: {label['direction']}")
        summary_lines.append("")
    
    if labels['power']:
        summary_lines.append("Power rails:")
        for label in labels['power']:
            summary_lines.append(f"  {label['name']}")
        summary_lines.append("")
    
    # Connections section
    if connections:
        summary_lines.append("Connection map:")
        for conn in connections:
            summary_lines.append(f"  {conn}")
    
    return {
        'components': components,
        'labels': labels,
        'connections': connections,
        'summary': '\n'.join(summary_lines)
    }

