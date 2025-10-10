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
from typing import Dict, List, Set, Tuple
from uuid import uuid4

from sexpdata import Symbol

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
    
    # Step 2: Create module schematics with hierarchical labels
    module_data = {}
    for module in modules:
        module_id = module["module_id"]
        connections = module_connections[module_id]
        
        # Create schematic
        sch = SchematicManager.create_schematic(module_id, metadata={
            "title": module_id,
            "description": module.get("function", "")
        })
        
        # Add hierarchical labels
        _add_hierarchical_labels_to_tree(sch.tree, connections)

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


def _add_hierarchical_labels_to_tree(tree: List, connections: Dict[str, Set[str]]) -> None:
    """Add hierarchical labels to a schematic tree."""
    y_pos = 50.0
    
    # Add labels for each connection type
    for label_name in sorted(connections["power_inputs"]):
        tree.append(_make_hierarchical_label(label_name, "input", 30.0, y_pos, 0))
        y_pos += 10.0
    
    for label_name in sorted(connections["power_outputs"]):
        tree.append(_make_hierarchical_label(label_name, "output", 130.0, y_pos, 0))
        y_pos += 10.0
    
    for label_name in sorted(connections["signal_inputs"]):
        tree.append(_make_hierarchical_label(label_name, "input", 30.0, y_pos, 0))
        y_pos += 10.0
    
    for label_name in sorted(connections["signal_outputs"]):
        tree.append(_make_hierarchical_label(label_name, "output", 130.0, y_pos, 0))
        y_pos += 10.0


def _make_hierarchical_label(name: str, shape: str, x: float, y: float, angle: int) -> List:
    """Create a hierarchical label element."""
    justify = "left" if angle == 0 else "right"
    return [
        Symbol("hierarchical_label"),
        name,
        [Symbol("shape"), Symbol(shape)],
        [Symbol("at"), x, y, angle],
        [Symbol("fields_autoplaced")],
        [Symbol("effects"), [Symbol("font"), [Symbol("size"), 1.27, 1.27]], [Symbol("justify"), Symbol(justify)]],
        [Symbol("uuid"), Symbol(str(uuid4()))]
    ]


def _add_text_to_tree(tree: List, text: str, x: float, y: float, font_size: float = 1.8) -> None:
    """Add a text annotation to the tree."""
    tree.append([
        Symbol("text"),
        text,
        [Symbol("at"), x, y, 0],
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
    
    # Layout in grid
    x_pos, y_pos = 40.0, 40.0
    sheet_width, sheet_height = 50.0, 40.0
    x_spacing, y_spacing = 80.0, 60.0
    
    for idx, (module_id, data) in enumerate(module_data.items()):
        col = idx % 3
        row = idx // 3
        x = x_pos + col * x_spacing
        y = y_pos + row * y_spacing
        
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
    sheet_instances = [Symbol("sheet_instances")]
    
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

