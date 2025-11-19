#!/usr/bin/env python3
"""
Blueprint to Hierarchical KiCAD Schematic Generator

This module generates a KiCad project folder from a blueprint JSON file using a
streamlined hierarchical layout. The generated structure now follows the convention:

output/
└─ kicad/
   ├─ top.kicad_sch                 (references modules/{module_id}.kicad_sch)
   ├─ modules/
   │  └─ {module_id}.kicad_sch      (module logic with hierarchical labels)
   ├─ erc/
   └─ export/

Key principles:
1. Use SchematicManager for creating/saving schematics.
2. Modules expose interfaces via hierarchical labels (no global labels inside the sheet).
3. The top schematic directly references module sheets; no harnesses are generated.
4. Maintain correct element ordering: sheet_instances MUST be the final entry.
5. Maintain correct path format: /root_uuid/sheet_uuid (not just /sheet_uuid).
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple
from uuid import uuid4

from sexpdata import Symbol
from .grid_utils import snap_to_grid
from .schematic import SchematicManager

PROJECT_ROOT = Path(__file__).resolve().parents[3]

logger = logging.getLogger(__name__)


def generate_hierarchical_schematic(blueprint_path: str, output_dir: str) -> Dict[str, Any]:
    """Generate a hierarchical KiCad project from a blueprint JSON file."""

    blueprint_file = Path(blueprint_path)
    if not blueprint_file.is_absolute():
        blueprint_file = (PROJECT_ROOT / blueprint_file).resolve()
    if not blueprint_file.exists():
        fallback = (Path.cwd() / blueprint_path).resolve()
        if fallback.exists():
            blueprint_file = fallback

    with open(blueprint_file, "r", encoding="utf-8") as f:
        blueprint = json.load(f)

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    kicad_dir = output_path / "kicad"
    modules_dir = kicad_dir / "modules"
    erc_dir = kicad_dir / "erc"
    export_dir = kicad_dir / "export"

    for directory in (kicad_dir, modules_dir, erc_dir, export_dir):
        directory.mkdir(parents=True, exist_ok=True)

    modules = blueprint.get("modules", [])
    signals = blueprint.get("signals", [])
    rails = blueprint.get("rails", [])

    logger.info("Generating hierarchical schematic with %s modules", len(modules))

    module_connections = _analyze_module_connections(modules, signals, rails)

    module_records: Dict[str, Dict[str, Any]] = {}

    for module in modules:
        module_id = module["module_id"]
        connections = module_connections[module_id]

        sheet_path = _create_module_sheet(module, connections, modules_dir)

        module_records[module_id] = {
            "module": module,
            "connections": connections,
            "sheet_path": sheet_path,
            "sheet_relpath": (Path("modules") / f"{module_id}.kicad_sch").as_posix(),
        }

    top_path = _create_top_schematic(kicad_dir, module_records)

    logger.info("Created top schematic: %s", top_path)

    return {
        "top_schematic": str(top_path),
        "module_sheets": {module_id: str(record["sheet_path"]) for module_id, record in module_records.items()},
        "output_dir": str(output_path),
    }


def _create_module_sheet(module: Dict[str, Any], connections: Dict[str, Set[str]], modules_dir: Path) -> Path:
    """Build the module sheet using hierarchical labels and save it."""

    module_id = module["module_id"]

    schematic = SchematicManager.create_schematic(
        module_id,
        metadata={
            "title": module_id,
            "description": module.get("function", ""),
        },
    )

    if not isinstance(schematic.tree, list):
        raise RuntimeError(f"Schematic tree is not a list for module {module_id}")

    tree = schematic.tree

    _add_hierarchical_labels_to_tree(tree, connections)
    _remove_sheet_instances(tree)

    sheet_path = modules_dir / f"{module_id}.kicad_sch"
    SchematicManager.save_schematic(schematic, str(sheet_path))

    logger.info("Created module sheet: %s", sheet_path)
    return sheet_path


def _create_top_schematic(kicad_dir: Path, module_records: Dict[str, Dict[str, Any]]) -> Path:
    """Create the top-level schematic referencing all modules."""

    top_schematic = SchematicManager.create_schematic("Top", metadata={"paper": "A3"})

    if not isinstance(top_schematic.tree, list):
        raise RuntimeError("Top schematic tree is not a list")

    tree = top_schematic.tree
    root_uuid = _get_root_uuid(tree)

    sheet_uuids = _add_sheet_symbols_to_tree(tree, module_records)
    _rebuild_sheet_instances_at_end(tree, root_uuid, sheet_uuids)

    top_path = kicad_dir / "top.kicad_sch"
    SchematicManager.save_schematic(top_schematic, str(top_path))

    return top_path


def _analyze_module_connections(modules: List[Dict], signals: List[Dict], rails: List[Dict]) -> Dict[str, Dict[str, Set[str]]]:
    """Analyze which signals/rails each module uses and determine direction."""
    signal_lookup = {
        sig["signal_id"]: sig
        for sig in signals
        if isinstance(sig, dict) and sig.get("signal_id")
    }

    module_connections = {}

    for module in modules:
        module_id = module["module_id"]
        connections = {
            "power_inputs": set(),
            "power_outputs": set(),
            "signal_inputs": set(),
            "signal_outputs": set(),
            "bidirectional_labels": set(),
        }

        # Power rails
        if "uses_rails" in module:
            connections["power_inputs"].update(module["uses_rails"])
        if "produces_rails" in module:
            connections["power_outputs"].update(module["produces_rails"])
        
        # Signals
        for sig in signals:
            sig_id = sig.get("signal_id")
            if not sig_id:
                continue

            sig_direction = (sig.get("direction") or "").lower()
            is_bidirectional = sig_direction == "bidirectional"

            if sig.get("source") == module_id:
                connections["signal_outputs"].add(sig_id)
                if is_bidirectional:
                    connections["bidirectional_labels"].add(sig_id)

            if module_id in sig.get("sinks", []):
                connections["signal_inputs"].add(sig_id)
                if is_bidirectional:
                    connections["bidirectional_labels"].add(sig_id)

        # Also check uses_signals and drives_signals
        if "uses_signals" in module:
            for sig_id in module["uses_signals"]:
                # Determine direction from signal definition
                sig_def = signal_lookup.get(sig_id)
                if sig_def and sig_def.get("source") != module_id:
                    connections["signal_inputs"].add(sig_id)
                    if (sig_def.get("direction") or "").lower() == "bidirectional":
                        connections["bidirectional_labels"].add(sig_id)

        if "drives_signals" in module:
            for sig_id in module["drives_signals"]:
                connections["signal_outputs"].add(sig_id)
                sig_def = signal_lookup.get(sig_id)
                if sig_def and (sig_def.get("direction") or "").lower() == "bidirectional":
                    connections["bidirectional_labels"].add(sig_id)

        module_connections[module_id] = connections

    return module_connections


def _add_hierarchical_labels_to_tree(tree: List, connections: Dict[str, Set[str]]) -> None:
    """Add hierarchical labels around a module sheet to expose its interfaces."""
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

    bidirectional_labels = set(connections.get("bidirectional_labels", set()))

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
            shape = "bidirectional" if label_name in bidirectional_labels else "input"
            tree.append(_make_hierarchical_label(label_name, shape, x_pos, TOP_Y, "bottom"))

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
            shape = "bidirectional" if label_name in bidirectional_labels else "input"
            tree.append(_make_hierarchical_label(label_name, shape, LEFT_X, y_pos, "right"))

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
            shape = "bidirectional" if label_name in bidirectional_labels else "output"
            tree.append(_make_hierarchical_label(label_name, shape, RIGHT_X, y_pos, "left"))

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
            shape = "bidirectional" if label_name in bidirectional_labels else "output"
            tree.append(_make_hierarchical_label(label_name, shape, x_pos, BOTTOM_Y, "top"))


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


def _make_hierarchical_label(name: str, shape: str, x: float, y: float, alignment: str) -> List[Any]:
    """Create a hierarchical label element with alignment helpers."""

    x_snapped = snap_to_grid(x)
    y_snapped = snap_to_grid(y)

    justify_tokens = _hierarchical_label_justify(alignment)

    effects = [Symbol("effects"), [Symbol("font"), [Symbol("size"), 1.27, 1.27]]]
    if justify_tokens:
        effects.append([Symbol("justify"), *map(Symbol, justify_tokens)])

    return [
        Symbol("hierarchical_label"),
        name,
        [Symbol("shape"), Symbol(shape)],
        [Symbol("at"), x_snapped, y_snapped, 0],
        [Symbol("fields_autoplaced")],
        effects,
        [Symbol("uuid"), Symbol(str(uuid4()))],
    ]


def _hierarchical_label_justify(alignment: str) -> List[str]:
    """Map a placement alignment to KiCad justify tokens."""

    mapping = {
        "left": ["right"],
        "right": ["left"],
        "top": ["left", "bottom"],
        "bottom": ["left", "top"],
    }

    return mapping.get(alignment, ["left"])


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


def _create_sheet_symbol(
    module_id: str,
    sheet_relpath: str,
    connections: Dict[str, Set[str]],
    origin_x: float,
    origin_y: float,
    sheet_width: float = 50.0,
    sheet_height: float = 40.0,
) -> Tuple[List[Any], str]:
    """Create a hierarchical sheet symbol for a parent schematic."""

    x = snap_to_grid(origin_x)
    y = snap_to_grid(origin_y)
    width = snap_to_grid(sheet_width)
    height = snap_to_grid(sheet_height)

    sheet_uuid = str(uuid4())

    sheet_node: List[Any] = [
        Symbol("sheet"),
        [Symbol("at"), x, y],
        [Symbol("size"), width, height],
        [Symbol("stroke"), [Symbol("width"), 0], [Symbol("type"), Symbol("solid")], [Symbol("color"), 0, 0, 0, 0]],
        [Symbol("fill"), [Symbol("color"), 0, 0, 0, 0.0]],
        [Symbol("uuid"), Symbol(sheet_uuid)],
        [
            Symbol("property"),
            "Sheet name",
            module_id,
            [Symbol("id"), 0],
            [Symbol("at"), x, y - 2.5, 0],
            [Symbol("effects"), [Symbol("font"), [Symbol("size"), 1.27, 1.27]], [Symbol("justify"), Symbol("left"), Symbol("bottom")]],
        ],
        [
            Symbol("property"),
            "Sheet file",
            sheet_relpath,
            [Symbol("id"), 1],
            [Symbol("at"), x, y + height + 0.5, 0],
            [Symbol("effects"), [Symbol("font"), [Symbol("size"), 1.27, 1.27]], [Symbol("justify"), Symbol("left"), Symbol("top")]],
        ],
    ]

    sheet_node.extend(_build_sheet_pin_entries(connections, x, y))

    return sheet_node, sheet_uuid


def _build_sheet_pin_entries(connections: Dict[str, Set[str]], x: float, y: float) -> List[List[Any]]:
    """Generate pin entries for a sheet symbol based on module connections."""

    bidirectional_labels = set(connections.get("bidirectional_labels", set()))

    ordered_labels: List[Tuple[str, str]] = []

    for name in sorted(connections.get("power_outputs", [])):
        direction = "bidirectional" if name in bidirectional_labels else "output"
        ordered_labels.append((name, direction))

    for name in sorted(connections.get("power_inputs", [])):
        direction = "bidirectional" if name in bidirectional_labels else "input"
        ordered_labels.append((name, direction))

    for name in sorted(connections.get("signal_outputs", [])):
        direction = "bidirectional" if name in bidirectional_labels else "output"
        ordered_labels.append((name, direction))

    for name in sorted(connections.get("signal_inputs", [])):
        direction = "bidirectional" if name in bidirectional_labels else "input"
        ordered_labels.append((name, direction))

    pins: List[List[Any]] = []
    pin_offset = 10.0
    pin_step = 5.0

    for label_name, direction in ordered_labels:
        pin_y = snap_to_grid(y + pin_offset)
        pins.append([
            Symbol("pin"),
            label_name,
            Symbol(direction),
            [Symbol("at"), x, pin_y, 0],
            [Symbol("effects"), [Symbol("font"), [Symbol("size"), 1.27, 1.27]]],
            [Symbol("uuid"), Symbol(str(uuid4()))],
        ])
        pin_offset += pin_step

    return pins


def _add_sheet_symbols_to_tree(tree: List, module_data: Dict[str, Dict[str, Any]]) -> Dict[str, str]:
    """Add sheet symbols to a parent schematic. Returns module_id -> sheet_uuid mapping."""

    sheet_uuids: Dict[str, str] = {}

    x_origin = 40.0
    y_origin = 40.0
    x_spacing = 80.0
    y_spacing = 60.0

    for idx, (module_id, data) in enumerate(module_data.items()):
        col = idx % 3
        row = idx // 3
        origin_x = x_origin + col * x_spacing
        origin_y = y_origin + row * y_spacing

        sheet_node, sheet_uuid = _create_sheet_symbol(
            module_id=module_id,
            sheet_relpath=data["sheet_relpath"],
            connections=data["connections"],
            origin_x=origin_x,
            origin_y=origin_y,
        )

        tree.append(sheet_node)
        sheet_uuids[module_id] = sheet_uuid

    return sheet_uuids


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
