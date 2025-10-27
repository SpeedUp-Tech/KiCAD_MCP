#!/usr/bin/env python3
"""
Blueprint to Hierarchical KiCAD Schematic Generator

This module generates hierarchical KiCAD schematics from a blueprint JSON file.
It uses direct tree manipulation following the working pattern from manual builds.

Key principles:
1. Use SchematicManager for creating/saving schematics
2. Direct tree manipulation for hierarchical elements (no complex helpers)
3. Correct element ordering: sheet_instances MUST be at the END
4. Correct path format: /root_uuid/sheet_uuid (not just /sheet_uuid)
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple
from uuid import uuid4

from sexpdata import Symbol
from .grid_utils import snap_to_grid

from .schematic import SchematicManager

logger = logging.getLogger(__name__)


def generate_hierarchical_schematic(blueprint_path: str, output_dir: str) -> Dict:
    """
    Generate a hierarchical KiCAD schematic from a blueprint JSON file.
    
    Args:
        blueprint_path: Path to the blueprint JSON file
        output_dir: Directory where the project will be created
        
    Returns:
        Dictionary with paths to created files:
        {
            "top_schematic": "path/to/Top.kicad_sch",
            "module_sheets": {"module_id": "path/to/module.kicad_sch", ...},
            "output_dir": "path/to/output"
        }
    """
    # Load blueprint
    with open(blueprint_path, 'r', encoding='utf-8') as f:
        blueprint = json.load(f)
    
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    sheets_dir = output_path / "sheets"
    sheets_dir.mkdir(exist_ok=True)
    
    modules = blueprint.get("modules", [])
    signals = blueprint.get("signals", [])
    rails = blueprint.get("rails", [])
    
    logger.info(f"Generating hierarchical schematic with {len(modules)} modules")
    
    # Step 1: Analyze connections for each module
    module_connections = _analyze_module_connections(modules, signals, rails)
    
    # Step 2: Create module schematics with global labels
    module_data = {}
    for module in modules:
        module_id = module["module_id"]
        connections = module_connections[module_id]
        
        # Create schematic
        sch = SchematicManager.create_schematic(module_id, metadata={
            "title": module_id,
            "description": module.get("function", "")
        })

        # Ensure tree is a list (type guard for type checker)
        if not isinstance(sch.tree, list):
            raise RuntimeError(f"Schematic tree is not a list for module {module_id}")

        # Add global labels
        _add_global_labels_to_tree(sch.tree, connections)

        # Remove sheet_instances from child sheets
        _remove_sheet_instances(sch.tree)
        
        # Save
        module_path = sheets_dir / f"{module_id}.kicad_sch"
        SchematicManager.save_schematic(sch, str(module_path))
        
        module_data[module_id] = {
            "path": module_path,
            "connections": connections,
            "module": module
        }
        
        logger.info(f"Created module schematic: {module_id}")
    
    # Step 3: Create top schematic with sheet symbols (A3 paper size)
    top_sch = SchematicManager.create_schematic("Top", metadata={"paper": "A3"})

    # Ensure tree is a list (type guard for type checker)
    if not isinstance(top_sch.tree, list):
        raise RuntimeError("Top schematic tree is not a list")

    top_tree = top_sch.tree

    # Get root UUID
    root_uuid = _get_root_uuid(top_tree)
    
    # Add sheet symbols with pins
    sheet_uuids = _add_sheet_symbols_to_tree(top_tree, module_data, sheets_dir)
    
    # Add wires connecting sheets
    _add_wires_to_tree(top_tree, module_data, sheet_uuids, signals, rails)
    
    # CRITICAL: Move sheet_instances to the END and update paths
    _rebuild_sheet_instances_at_end(top_tree, root_uuid, sheet_uuids)
    
    # Save top schematic
    top_path = output_path / "Top.kicad_sch"
    SchematicManager.save_schematic(top_sch, str(top_path))
    
    logger.info(f"Created top schematic: {top_path}")
    
    return {
        "top_schematic": str(top_path),
        "module_sheets": {mid: str(data["path"]) for mid, data in module_data.items()},
        "output_dir": str(output_path)
    }


def _analyze_module_connections(modules: List[Dict], signals: List[Dict], rails: List[Dict]) -> Dict[str, Dict[str, Set[str]]]:
    """Analyze which signals/rails each module uses and determine direction."""
    module_connections = {}
    
    for module in modules:
        module_id = module["module_id"]
        connections = {
            "power_inputs": set(),
            "power_outputs": set(),
            "signal_inputs": set(),
            "signal_outputs": set(),
        }
        
        # Power rails
        if "uses_rails" in module:
            connections["power_inputs"].update(module["uses_rails"])
        if "produces_rails" in module:
            connections["power_outputs"].update(module["produces_rails"])
        
        # Signals
        for sig in signals:
            sig_id = sig.get("signal_id")
            if sig.get("source") == module_id:
                connections["signal_outputs"].add(sig_id)
            elif module_id in sig.get("sinks", []):
                connections["signal_inputs"].add(sig_id)
        
        # Also check uses_signals and drives_signals
        if "uses_signals" in module:
            for sig_id in module["uses_signals"]:
                # Determine direction from signal definition
                sig_def = next((s for s in signals if s["signal_id"] == sig_id), None)
                if sig_def and sig_def.get("source") != module_id:
                    connections["signal_inputs"].add(sig_id)
        
        if "drives_signals" in module:
            connections["signal_outputs"].update(module["drives_signals"])
        
        module_connections[module_id] = connections
    
    return module_connections


def _add_global_labels_to_tree(tree: List, connections: Dict[str, Set[str]]) -> None:
    """
    Add global labels to a schematic tree following schematic drawing principles:
    - Signal flow: left to right (inputs on left, outputs on right)
    - Power flow: top to bottom (inputs on top, outputs on bottom)
    - All labels are horizontal (0 degrees) for readability
    - Labels are spread adaptively across each edge

    Layout:
    - Power inputs: spread adaptively along the top edge
    - Signal inputs: spread adaptively along the left edge
    - Signal outputs: spread adaptively along the right edge
    - Power outputs: spread adaptively along the bottom edge
    """
    # A4 schematic working area is approximately 277mm x 190mm
    # Using grid units (typically 2.54mm per unit), we have roughly:
    # Width: ~270mm / 2.54 ≈ 106 units, Height: ~180mm / 2.54 ≈ 71 units
    # The workspace box typically starts around (25, 25) and ends around (270, 190)
    # Labels should be INSIDE the workspace box

    LEFT_X = 30.0      # Left edge for signal inputs (inside workspace)
    RIGHT_X = 260.0    # Right edge for signal outputs (inside workspace)
    TOP_Y = 20.0       # Top edge for power inputs (inside workspace)
    BOTTOM_Y = 180.0   # Bottom edge for power outputs (inside workspace)

    # Usable ranges for spreading labels (inside workspace)
    HORIZONTAL_START = 50.0   # Start X for top/bottom labels
    HORIZONTAL_END = 230.0    # End X for top/bottom labels
    VERTICAL_START = 50.0     # Start Y for left/right labels
    VERTICAL_END = 150.0      # End Y for left/right labels

    # Adaptive spacing parameters
    MIN_SPACING = 15.0        # Minimum spacing between labels
    MAX_SPACING = 50.0        # Maximum spacing between labels

    # All labels use angle 0 (horizontal text, reader-friendly)

    # Power inputs: spread adaptively along the top edge
    power_inputs = sorted(connections["power_inputs"])
    if power_inputs:
        positions = _calculate_adaptive_positions(
            len(power_inputs),
            HORIZONTAL_START,
            HORIZONTAL_END,
            MIN_SPACING,
            MAX_SPACING
        )
        for label_name, x_pos in zip(power_inputs, positions):
            tree.append(_make_global_label(label_name, "input", x_pos, TOP_Y, 0, "left"))

    # Signal inputs: spread adaptively along the left edge
    # Use "right" justify so text extends left and symbol is on the right (toward connections)
    signal_inputs = sorted(connections["signal_inputs"])
    if signal_inputs:
        positions = _calculate_adaptive_positions(
            len(signal_inputs),
            VERTICAL_START,
            VERTICAL_END,
            MIN_SPACING,
            MAX_SPACING
        )
        for label_name, y_pos in zip(signal_inputs, positions):
            tree.append(_make_global_label(label_name, "input", LEFT_X, y_pos, 0, "right"))

    # Signal outputs: spread adaptively along the right edge
    signal_outputs = sorted(connections["signal_outputs"])
    if signal_outputs:
        positions = _calculate_adaptive_positions(
            len(signal_outputs),
            VERTICAL_START,
            VERTICAL_END,
            MIN_SPACING,
            MAX_SPACING
        )
        for label_name, y_pos in zip(signal_outputs, positions):
            tree.append(_make_global_label(label_name, "output", RIGHT_X, y_pos, 0, "left"))

    # Power outputs: spread adaptively along the bottom edge
    power_outputs = sorted(connections["power_outputs"])
    if power_outputs:
        positions = _calculate_adaptive_positions(
            len(power_outputs),
            HORIZONTAL_START,
            HORIZONTAL_END,
            MIN_SPACING,
            MAX_SPACING
        )
        for label_name, x_pos in zip(power_outputs, positions):
            tree.append(_make_global_label(label_name, "output", x_pos, BOTTOM_Y, 0, "left"))


def _calculate_adaptive_positions(count: int, start: float, end: float, min_spacing: float, max_spacing: float) -> List[float]:
    """
    Calculate adaptive positions for labels along an edge.

    Strategy:
    - Single label: place at center
    - Multiple labels: use spacing between min_spacing and max_spacing
    - If labels fit with max_spacing, center them on the edge
    - If labels need more space, use the full edge with uniform spacing

    Args:
        count: Number of labels
        start: Start position of the edge
        end: End position of the edge
        min_spacing: Minimum spacing between labels
        max_spacing: Maximum spacing between labels

    Returns:
        List of positions for each label
    """
    if count == 0:
        return []

    if count == 1:
        # Single label: place at center
        return [(start + end) / 2]

    # Calculate total length needed with max_spacing
    total_length_max = (count - 1) * max_spacing
    available_length = end - start

    if total_length_max <= available_length:
        # Labels fit comfortably with max_spacing, center them
        actual_spacing = max_spacing
        total_length = total_length_max
        offset = (available_length - total_length) / 2
        start_pos = start + offset
    else:
        # Use full edge with uniform spacing
        actual_spacing = available_length / (count - 1)
        start_pos = start

    # Generate positions
    return [start_pos + i * actual_spacing for i in range(count)]


def _make_global_label(name: str, shape: str, x: float, y: float, angle: int, justify: str = "left") -> List:
    """
    Create a global label element.

    Args:
        name: Label name
        shape: Label shape ("input", "output", "bidirectional", "passive")
        x: X coordinate
        y: Y coordinate
        angle: Rotation angle in degrees (always 0 for horizontal, reader-friendly text)
        justify: Text justification ("left" or "right")
                 - "left": text extends right, symbol on left (format: <>[label])
                 - "right": text extends left, symbol on right (format: [label]<>)
    """
    # Snap coordinates to grid for KiCAD compliance
    x_snapped = snap_to_grid(x)
    y_snapped = snap_to_grid(y)

    # All labels use horizontal text (angle 0) for readability
    # Text justification controls where the symbol appears relative to text
    return [
        Symbol("global_label"),
        name,
        [Symbol("shape"), Symbol(shape)],
        [Symbol("at"), x_snapped, y_snapped, angle],
        [Symbol("fields_autoplaced")],
        [Symbol("effects"), [Symbol("font"), [Symbol("size"), 1.27, 1.27]], [Symbol("justify"), Symbol(justify)]],
        [Symbol("uuid"), Symbol(str(uuid4()))]
    ]


def _add_text_to_tree(tree: List, text: str, x: float, y: float, font_size: float = 1.8) -> None:
    """Add a text annotation to the tree."""
    # Snap coordinates to grid
    x_snapped = snap_to_grid(x)
    y_snapped = snap_to_grid(y)

    tree.append([
        Symbol("text"),
        text,
        [Symbol("at"), x_snapped, y_snapped, 0],
        [Symbol("effects"), [Symbol("font"), [Symbol("size"), font_size, font_size], [Symbol("thickness"), 0.4], Symbol("bold")], [Symbol("justify"), Symbol("left"), Symbol("bottom")]],
        [Symbol("uuid"), Symbol(str(uuid4()))]
    ])


def _remove_sheet_instances(tree: List) -> None:
    """Remove sheet_instances from child sheets."""
    for i in range(len(tree) - 1, -1, -1):
        if isinstance(tree[i], list) and tree[i] and tree[i][0] == Symbol("sheet_instances"):
            tree.pop(i)
            return


def _get_root_uuid(tree: List) -> str:
    """Extract root UUID from schematic tree."""
    for entry in tree:
        if isinstance(entry, list) and entry and entry[0] == Symbol("uuid"):
            return str(entry[1])
    raise RuntimeError("Root UUID not found in schematic tree")


def _add_sheet_symbols_to_tree(tree: List, module_data: Dict, sheets_dir: Path) -> Dict[str, str]:
    """Add sheet symbols to top schematic tree. Returns mapping of module_id to sheet_uuid."""
    sheet_uuids = {}
    
    # Layout in grid - snap base positions to grid
    x_pos, y_pos = snap_to_grid(40.0), snap_to_grid(40.0)
    sheet_width, sheet_height = snap_to_grid(50.0), snap_to_grid(40.0)
    x_spacing, y_spacing = snap_to_grid(80.0), snap_to_grid(60.0)

    for idx, (module_id, data) in enumerate(module_data.items()):
        col = idx % 3
        row = idx // 3
        x = snap_to_grid(x_pos + col * x_spacing)
        y = snap_to_grid(y_pos + row * y_spacing)
        
        sheet_uuid = str(uuid4())
        sheet_uuids[module_id] = sheet_uuid
        
        connections = data["connections"]
        module = data["module"]
        
        # Create sheet node
        sheet_node = [
            Symbol("sheet"),
            [Symbol("at"), x, y],
            [Symbol("size"), sheet_width, sheet_height],
            [Symbol("stroke"), [Symbol("width"), 0], [Symbol("type"), Symbol("solid")], [Symbol("color"), 0, 0, 0, 0]],
            [Symbol("fill"), [Symbol("color"), 0, 0, 0, 0.0]],
            [Symbol("uuid"), sheet_uuid],
            [Symbol("property"), "Sheet name", module_id, [Symbol("id"), 0], [Symbol("at"), x, y - 2.5, 0], [Symbol("effects"), [Symbol("font"), [Symbol("size"), 1.27, 1.27]], [Symbol("justify"), Symbol("left"), Symbol("bottom")]]],
            [Symbol("property"), "Sheet file", f"sheets/{module_id}.kicad_sch", [Symbol("id"), 1], [Symbol("at"), x, y + sheet_height + 0.5, 0], [Symbol("effects"), [Symbol("font"), [Symbol("size"), 1.27, 1.27]], [Symbol("justify"), Symbol("left"), Symbol("top")]]],
        ]
        
        # Add pins
        pin_y = 10.0
        all_labels = (
            [(name, "output") for name in sorted(connections["power_outputs"])] +
            [(name, "input") for name in sorted(connections["power_inputs"])] +
            [(name, "output") for name in sorted(connections["signal_outputs"])] +
            [(name, "input") for name in sorted(connections["signal_inputs"])]
        )
        
        for label_name, direction in all_labels:
            sheet_node.append([
                Symbol("pin"),
                label_name,
                Symbol(direction),
                [Symbol("at"), x, y + pin_y, 0],
                [Symbol("effects"), [Symbol("font"), [Symbol("size"), 1.27, 1.27]]],
                [Symbol("uuid"), str(uuid4())]
            ])
            pin_y += 5.0
        
        tree.append(sheet_node)
    
    return sheet_uuids


def _add_wires_to_tree(tree: List, module_data: Dict, sheet_uuids: Dict[str, str], signals: List[Dict], rails: List[Dict]) -> None:
    """Add wires connecting sheets (placeholder - can be enhanced)."""
    # This is a simplified version - wires can be added based on signal/rail connections
    # For now, we skip automatic wire generation as it requires pin position calculation
    pass


def _rebuild_sheet_instances_at_end(tree: List, root_uuid: str, sheet_uuids: Dict[str, str]) -> None:
    """Remove existing sheet_instances and rebuild it at the END with correct paths."""
    # Remove existing sheet_instances
    for i in range(len(tree) - 1, -1, -1):
        if isinstance(tree[i], list) and tree[i] and tree[i][0] == Symbol("sheet_instances"):
            tree.pop(i)
            break

    # Create new sheet_instances with correct paths
    sheet_instances: List[Any] = [Symbol("sheet_instances")]

    # Add root path
    sheet_instances.append([Symbol("path"), "/", [Symbol("page"), "1"]])

    # Add each sheet path with format: /root_uuid/sheet_uuid
    for page_num, (module_id, sheet_uuid) in enumerate(sheet_uuids.items(), start=2):
        sheet_instances.append([
            Symbol("path"),
            f"/{root_uuid}/{sheet_uuid}",
            [Symbol("page"), str(page_num)]
        ])

    # Append to END of tree
    tree.append(sheet_instances)
