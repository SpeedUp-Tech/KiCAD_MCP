"""
elk_to_kicad - Step 3: Convert ELK Layout Output to KiCad Schematic

This module takes the layouted ELK graph JSON and generates a proper
KiCad schematic file with correctly positioned components and wires.
"""

import json
from pathlib import Path
from typing import Union, Dict, Any, Tuple, List

from python.commands.kicad_schematics.schematic import SchematicManager
from python.commands.kicad_schematics.component_schematic import ComponentManager
from python.commands.kicad_schematics.connection_schematic import ConnectionManager
from python.commands.kicad_schematics.grid_utils import snap_to_grid, KICAD_SCHEMATIC_GRID_MM


# KiCad 6+ uses millimeters. ELK adapter outputs millimeters.
SCALE_FACTOR = 1.0 
OFFSET_X = 148.5  # A4 Center X (297/2)
OFFSET_Y = 105.0  # A4 Center Y (210/2)


def _collect_component_nodes(
    children: List[Dict[str, Any]], 
    parent_x: float = 0, 
    parent_y: float = 0
) -> List[Tuple[Dict[str, Any], float, float]]:
    """
    Recursively collect component nodes from a hierarchical ELK graph.
    
    Cluster nodes (like input_stage, output_stage) are containers - they have
    children but no 'lib' or 'symbol' properties. We extract their children
    and apply the parent's offset.
    
    Returns:
        List of (node, absolute_x, absolute_y) tuples
    """
    result = []
    
    for node in children:
        node_x = node.get("x", 0) + parent_x
        node_y = node.get("y", 0) + parent_y
        
        # Check if this is a component (has lib/symbol) or a cluster (has children to recurse into)
        meta = node.get("properties", {})
        
        if "lib" in meta and "symbol" in meta:
            # This is a component - collect it with its absolute position
            result.append((node, node_x, node_y))
        
        # Recurse into children (for clusters or any other container nodes)
        children_nodes = node.get("children", [])
        if children_nodes:
            result.extend(_collect_component_nodes(children_nodes, node_x, node_y))
    
    return result


def run_conversion(
    elk_input: Union[Path, str, Dict[str, Any]], 
    output_sch: Union[Path, str]
) -> None:
    """
    Convert ELK layout output to KiCad schematic.
    
    Args:
        elk_input: Either a path to elk_output.json, or the graph dict directly
        output_sch: Path for the output .kicad_sch file
    """
    output_sch = Path(output_sch)
    
    # Load graph
    if isinstance(elk_input, dict):
        graph = elk_input
    else:
        elk_file = Path(elk_input)
        if not elk_file.exists():
            raise FileNotFoundError(f"ELK output file not found: {elk_file}")
        with open(elk_file, 'r') as f:
            graph = json.load(f)

    # Create Schematic
    print(f"Creating schematic: {output_sch}")
    schematic = SchematicManager.create_schematic(output_sch.stem, {})
    
    # Calculate Graph Bounding Box to center on page
    root_w = graph.get("width", 0)
    root_h = graph.get("height", 0)
    
    # Center: Page Center = (148.5, 105). Graph Center = (w/2, h/2).
    shift_x = OFFSET_X - (root_w / 2)
    shift_y = OFFSET_Y - (root_h / 2)

    # Build pin position lookup table
    # Key: port_id (e.g., "U1.1"), Value: (absolute_x, absolute_y) in KiCad coordinates
    pin_positions: Dict[str, Tuple[float, float]] = {}

    # Collect all component nodes recursively (handles clusters)
    component_nodes = _collect_component_nodes(graph.get("children", []))

    # Add Components and build pin position map
    for node, node_x, node_y in component_nodes:
        meta = node.get("properties", {})
        ref = node["id"]
        
        # Node dimensions
        w_raw = node.get("width", 0)
        h_raw = node.get("height", 0)
        
        # ELK Position is Top-Left of the Node Box
        # KiCad Position is the Symbol Origin (0,0)
        origin_off_x = meta.get("origin_offset_x", w_raw / 2)
        origin_off_y = meta.get("origin_offset_y", h_raw / 2)
        
        # Calculate symbol origin in KiCad coordinates
        # node_x and node_y are absolute positions (already include parent cluster offsets)
        # IMPORTANT: Snap to grid since ComponentManager will snap the symbol position
        pos_x = snap_to_grid((node_x + origin_off_x) * SCALE_FACTOR + shift_x)
        pos_y = snap_to_grid((node_y + origin_off_y) * SCALE_FACTOR + shift_y)
        
        # Build pin positions from port properties
        # Pin positions must also be snapped to grid for wire connections
        for port in node.get("ports", []):
            port_id = port["id"]
            port_props = port.get("properties", {})
            
            if "kicad_offset_x" in port_props and "kicad_offset_y" in port_props:
                # Pin position = symbol origin + pin offset
                # Pin offsets are defined on 2.54mm grid in KiCad symbols
                # Note: KiCad Y-axis is inverted (positive Y goes down)
                pin_x = snap_to_grid(pos_x + port_props["kicad_offset_x"])
                pin_y = snap_to_grid(pos_y - port_props["kicad_offset_y"])
                pin_positions[port_id] = (pin_x, pin_y)
        
        comp_def = {
            "reference": ref,
            "value": meta.get("value", "Val"),
            "libId": f"{meta.get('lib', 'Lib')}:{meta.get('symbol', 'Sym')}",
            "x": pos_x,
            "y": pos_y,
            "rotation": 0
        }
        
        try:
            print(f"Adding Component {ref} at ({pos_x:.2f}, {pos_y:.2f})")
            ComponentManager.add_component(schematic, comp_def)
        except Exception as e:
            print(f"Failed to add component {ref}: {e}")

    # Add Wires using ELK's routed sections directly
    # Since we now use FIXED_POS, ELK's port positions match KiCad pin positions
    for edge in graph.get("edges", []):
        edge_id = edge.get("id", "unknown")
        
        # Skip phantom edges - they're for layout guidance only, not real wires
        if edge_id.startswith(("flow_", "align_", "adjacent_", "halign_")):
            continue
            
        sections = edge.get("sections", [])
        
        if not sections:
            print(f"Skipping edge {edge_id}: no routing sections")
            continue
        
        for section in sections:
            points: List[List[float]] = []
            
            # Start point from ELK
            start = section.get("startPoint", {})
            start_x = snap_to_grid(start.get("x", 0) * SCALE_FACTOR + shift_x)
            start_y = snap_to_grid(start.get("y", 0) * SCALE_FACTOR + shift_y)
            points.append([start_x, start_y])
            
            # Bend points from ELK routing
            for bp in section.get("bendPoints", []):
                bp_x = snap_to_grid(bp["x"] * SCALE_FACTOR + shift_x)
                bp_y = snap_to_grid(bp["y"] * SCALE_FACTOR + shift_y)
                points.append([bp_x, bp_y])
            
            # End point from ELK
            end = section.get("endPoint", {})
            end_x = snap_to_grid(end.get("x", 0) * SCALE_FACTOR + shift_x)
            end_y = snap_to_grid(end.get("y", 0) * SCALE_FACTOR + shift_y)
            points.append([end_x, end_y])
            
            try:
                bend_count = len(section.get("bendPoints", []))
                print(f"Adding wire {edge_id}: ({start_x:.2f}, {start_y:.2f}) -> ({end_x:.2f}, {end_y:.2f}) [{bend_count} bends]")
                ConnectionManager.add_wire(
                    schematic, 
                    start_point=None, 
                    end_point=None, 
                    properties={"points": points}
                )
            except Exception as e:
                print(f"Failed to add wire {edge_id}: {e}")

    # Save
    SchematicManager.save_schematic(schematic, str(output_sch))
    print(f"Schematic saved: {output_sch}")

