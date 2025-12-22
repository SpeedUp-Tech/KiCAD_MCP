"""
elk_to_kicad - Step 3: Convert ELK Layout Output to KiCad Schematic

This module takes the layouted ELK graph JSON and generates a proper
KiCad schematic file with correctly positioned components and wires.
"""

import json
import uuid
from pathlib import Path
from typing import Union, Dict, Any, Tuple, List

from python.commands.kicad_schematics.schematic import SchematicManager, Schematic
from python.commands.kicad_schematics.component_schematic import ComponentManager
from python.commands.kicad_schematics.connection_schematic import ConnectionManager, SchematicCompiler
from python.commands.kicad_schematics.grid_utils import snap_to_grid, KICAD_SCHEMATIC_GRID_MM
from sexpdata import Symbol as SSymbol


# KiCad 6+ uses millimeters. ELK adapter outputs millimeters.
SCALE_FACTOR = 1.0 
OFFSET_X = 148.5  # A4 Center X (297/2)
OFFSET_Y = 105.0  # A4 Center Y (210/2)


def _add_net_label(
    schematic: Schematic,
    name: str,
    x: float,
    y: float,
    label_type: str = "hierarchical",
    label_shape: str = "bidirectional",
    label_role: str = "both"
) -> None:
    """
    Add a net label (local label or hierarchical label) to the schematic.
    
    Args:
        schematic: The schematic object
        name: The net name to display
        x: X coordinate in mm
        y: Y coordinate in mm
        label_type: One of 'hierarchical', 'local' - determines label type
        label_shape: One of 'input', 'output', 'bidirectional' - determines arrow direction (hierarchical only)
        label_role: One of 'source', 'target', 'both' - determines rotation
    """
    snapped_x = snap_to_grid(x)
    snapped_y = snap_to_grid(y)
    
    # Determine label shape based on type (only used for hierarchical labels)
    if label_shape == "input":
        shape = "input"
    elif label_shape == "output":
        shape = "output"
    else:
        shape = "bidirectional"
    
    # Determine rotation based on role
    # Source labels: text should extend LEFT (rotation=180)
    # Target labels: text should extend RIGHT (rotation=0)
    if label_role == "source":
        rotation = 180
        justify = "right"
    else:
        rotation = 0
        justify = "left"
    
    effects_node = [
        SSymbol('effects'),
        [SSymbol('font'), [SSymbol('size'), 1.27, 1.27]],
        [SSymbol('justify'), SSymbol(justify)],
    ]
    
    if label_type == "local":
        # Local label - simple net name without direction arrows
        label_node = [
            SSymbol('label'),
            name,
            [SSymbol('at'), snapped_x, snapped_y, rotation],
            [SSymbol('fields_autoplaced')],
            effects_node,
            [SSymbol('uuid'), str(uuid.uuid4())],
        ]
    else:
        # Hierarchical label - has direction shape for sheet connections
        label_node = [
            SSymbol('hierarchical_label'),
            name,
            [SSymbol('shape'), SSymbol(shape)],
            [SSymbol('at'), snapped_x, snapped_y, rotation],
            [SSymbol('fields_autoplaced')],
            effects_node,
            [SSymbol('uuid'), str(uuid.uuid4())],
        ]
    
    schematic.tree.append(label_node)


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
    
    Also collects net_label nodes which are identified by their node_type property.
    
    Returns:
        List of (node, absolute_x, absolute_y) tuples
    """
    result = []
    
    for node in children:
        node_x = node.get("x", 0) + parent_x
        node_y = node.get("y", 0) + parent_y
        
        # Check if this is a component (has lib/symbol), net_label, or a cluster
        meta = node.get("properties", {})
        
        if "lib" in meta and "symbol" in meta:
            # This is a component - collect it with its absolute position
            result.append((node, node_x, node_y))
        elif meta.get("node_type") == "net_label":
            # This is a net label node - collect it
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
    all_nodes = _collect_component_nodes(graph.get("children", []))
    
    # Separate net label nodes from component/power symbol nodes
    component_nodes = []
    net_label_nodes = []
    
    for node, node_x, node_y in all_nodes:
        meta = node.get("properties", {})
        if meta.get("node_type") == "net_label":
            net_label_nodes.append((node, node_x, node_y))
        else:
            component_nodes.append((node, node_x, node_y))

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
        # Pin positions are exact: symbol origin + pin offset (no extra snapping)
        # The offsets come from the symbol definition which should already be grid-aligned
        for port in node.get("ports", []):
            port_id = port["id"]
            port_props = port.get("properties", {})

            if "kicad_offset_x" in port_props and "kicad_offset_y" in port_props:
                # Pin position = symbol origin + pin offset
                # Note: KiCad Y-axis is inverted (positive Y goes down)
                # The kicad_offset values are already rotated in elk_graph_adapter
                pin_x = pos_x + port_props["kicad_offset_x"]
                pin_y = pos_y - port_props["kicad_offset_y"]
                pin_positions[port_id] = (pin_x, pin_y)
        
        # Get rotation from node properties (set by rotation optimizer)
        rotation = meta.get("rotation", 0)
        
        comp_def = {
            "reference": ref,
            "value": meta.get("value", "Val"),
            "libId": f"{meta.get('lib', 'Lib')}:{meta.get('symbol', 'Sym')}",
            "x": pos_x,
            "y": pos_y,
            "rotation": rotation
        }
        
        try:
            print(f"Adding Component {ref} at ({pos_x:.2f}, {pos_y:.2f})")
            ComponentManager.add_component(schematic, comp_def)
        except Exception as e:
            print(f"Failed to add component {ref}: {e}")

    # Add Net Labels
    for node, node_x, node_y in net_label_nodes:
        meta = node.get("properties", {})
        label_id = node["id"]
        net_name = meta.get("net_name", label_id)
        label_type = meta.get("label_type", "hierarchical")  # hierarchical/local
        label_shape = meta.get("label_shape", "bidirectional")  # input/output/bidirectional
        label_role = meta.get("label_role", "both")
        
        # Get the port position - this is where wires will connect
        # In KiCad, the label's "at" position IS its connection point
        # So we must place the label at the port position, not node center
        port = node.get("ports", [{}])[0]  # Labels have one port
        port_x = port.get("x", 0)
        port_y = port.get("y", 0)
        
        # Calculate label position at the port (connection point)
        # Label positions should be on grid for clean schematic appearance
        label_x = snap_to_grid((node_x + port_x) * SCALE_FACTOR + shift_x)
        label_y = snap_to_grid((node_y + port_y) * SCALE_FACTOR + shift_y)

        # Store port position for wire connections
        # Use the same snapped position since the label IS at this position
        for p in node.get("ports", []):
            port_id = p["id"]
            # Label port positions match the label position (snapped)
            pin_positions[port_id] = (label_x, label_y)
        
        try:
            print(f"Adding Net Label '{net_name}' ({label_type}, {label_shape}, {label_role}) at ({label_x:.2f}, {label_y:.2f})")
            _add_net_label(schematic, net_name, label_x, label_y, label_type, label_shape, label_role)
        except Exception as e:
            print(f"Failed to add net label {label_id}: {e}")

    # Track which nets already have labels (from explicit net_labels in graph)
    labeled_nets: set[str] = set()
    for node, node_x, node_y in net_label_nodes:
        meta = node.get("properties", {})
        net_name = meta.get("net_name", node["id"])
        labeled_nets.add(net_name)
    
    # Also track net -> first wire endpoint (for adding labels to unlabeled nets)
    net_first_endpoint: dict[str, tuple[float, float]] = {}

    # Add Wires using ELK's routed sections
    # Wire endpoints must use exact pin positions from pin_positions map
    # to ensure proper connections (no snapping that could cause misalignment)
    for edge in graph.get("edges", []):
        edge_id = edge.get("id", "unknown")

        # Skip phantom edges - they're for layout guidance only, not real wires
        if edge_id.startswith(("flow_", "chain_", "align_", "adjacent_", "halign_")):
            continue

        sections = edge.get("sections", [])

        if not sections:
            print(f"Skipping edge {edge_id}: no routing sections")
            continue

        # Get net_name from edge properties if available
        edge_props = edge.get("properties", {})
        net_name = edge_props.get("net_name")

        # Get source and target port IDs for exact pin positions
        sources = edge.get("sources", [])
        targets = edge.get("targets", [])
        source_port_id = sources[0] if sources else None
        target_port_id = targets[0] if targets else None

        for section in sections:
            points: List[List[float]] = []

            # Start point: use exact pin position if available, otherwise transform ELK coords
            if source_port_id and source_port_id in pin_positions:
                start_x, start_y = pin_positions[source_port_id]
            else:
                start = section.get("startPoint", {})
                start_x = start.get("x", 0) * SCALE_FACTOR + shift_x
                start_y = start.get("y", 0) * SCALE_FACTOR + shift_y
            points.append([start_x, start_y])

            # Bend points from ELK routing - snap these for clean routing
            for bp in section.get("bendPoints", []):
                bp_x = snap_to_grid(bp["x"] * SCALE_FACTOR + shift_x)
                bp_y = snap_to_grid(bp["y"] * SCALE_FACTOR + shift_y)
                points.append([bp_x, bp_y])

            # End point: use exact pin position if available, otherwise transform ELK coords
            if target_port_id and target_port_id in pin_positions:
                end_x, end_y = pin_positions[target_port_id]
            else:
                end = section.get("endPoint", {})
                end_x = end.get("x", 0) * SCALE_FACTOR + shift_x
                end_y = end.get("y", 0) * SCALE_FACTOR + shift_y
            points.append([end_x, end_y])

            # Track first endpoint for nets that need labels
            if net_name and net_name not in labeled_nets and net_name not in net_first_endpoint:
                net_first_endpoint[net_name] = (start_x, start_y)

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
    
    # Add local labels for unlabeled nets using their SKiDL net names
    for net_name, (label_x, label_y) in net_first_endpoint.items():
        try:
            print(f"Adding auto net label '{net_name}' at ({label_x:.2f}, {label_y:.2f})")
            _add_net_label(schematic, net_name, label_x, label_y, label_type="local")
        except Exception as e:
            print(f"Failed to add net label {net_name}: {e}")
    
    # Compile: add labels to any remaining unlabeled nets (e.g., GND connections to power symbols)
    compile_result = SchematicCompiler.compile(schematic)
    if compile_result.get("generatedLabelCount", 0) > 0:
        print(f"Added {compile_result['generatedLabelCount']} auto-generated net labels")

    # Save
    SchematicManager.save_schematic(schematic, str(output_sch))
    print(f"Schematic saved: {output_sch}")

