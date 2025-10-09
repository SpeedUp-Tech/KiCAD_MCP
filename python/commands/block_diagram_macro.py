"""
Block diagram macro generator for KiCad schematics.

This module converts a blueprint JSON description containing modules, rails,
and signals into a hierarchical schematic skeleton:

project_dir/
  project.kicad_pro
  Top.kicad_sch
  sheets/{module}.kicad_sch
  out/{erc.txt, Top.pdf, Top.svg}
"""
from __future__ import annotations

from dataclasses import dataclass, field
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Tuple, Optional

import json
import logging
import math
from copy import deepcopy
from uuid import uuid4

from sexpdata import Symbol

from skip.element_template import ElementTemplate

from .schematic import SchematicManager

logger = logging.getLogger("kicad_interface.block_macro")


# Base layout constants (units: mm – KiCad schematic default uses mm internally)
PIN_LENGTH = 5.0
PIN_SPACING = 7.5
POWER_SPACING = 10.0
LABEL_OFFSET = 4.0
BLOCK_WIDTH_MIN = 50.0
BLOCK_HEIGHT_MIN = 35.0
BLOCK_PADDING = 8.0
COLUMN_SPACING = 80.0
ROW_SPACING = 65.0
PAPER_MARGIN = 40.0
WIRE_WIDTH = 0.254


@dataclass
class ModulePorts:
    """Categorised port lists for a module."""

    signal_inputs: List[str] = field(default_factory=list)
    signal_outputs: List[str] = field(default_factory=list)
    signal_bidirectional: List[str] = field(default_factory=list)
    power_inputs: List[str] = field(default_factory=list)
    power_outputs: List[str] = field(default_factory=list)


@dataclass
class BlockPin:
    """Represents a sheet pin and associated label placement."""

    name: str
    shape: str
    orientation: str
    angle: int
    position: Tuple[float, float]
    label_position: Tuple[float, float]


@dataclass
class ModuleBlock:
    """Top-level block metadata used when composing the root sheet."""

    module_id: str
    function: str
    center: Tuple[float, float]
    size: Tuple[float, float]
    pins: List[BlockPin]
    sheet_node: List
    sheet_path: Path
    uuid: str


def generate_block_diagram(blueprint_path: str, project_dir: str) -> Dict[str, object]:
    """
    Generate a KiCad project skeleton based on the provided blueprint JSON.

    Args:
        blueprint_path: Path to the blueprint JSON file.
        project_dir: Target directory for the generated KiCad project.

    Returns:
        Dictionary describing generated artefacts.
    """
    blueprint_file = Path(blueprint_path)
    if not blueprint_file.is_file():
        raise FileNotFoundError(f"Blueprint JSON not found: {blueprint_file}")

    # Respect the provided project_dir; caller decides where to place results.
    project_root = Path(project_dir).expanduser().resolve()
    sheets_dir = project_root / "sheets"
    out_dir = project_root / "out"

    project_root.mkdir(parents=True, exist_ok=True)
    sheets_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    with blueprint_file.open("r", encoding="utf-8") as handle:
        blueprint = json.load(handle)

    modules = blueprint.get("modules", [])
    rails = blueprint.get("rails", [])
    signals = blueprint.get("signals", [])
    logger.info(
        "Generating block diagram for %d modules, %d rails, %d signals",
        len(modules),
        len(rails),
        len(signals),
    )

    ports_by_module = _build_module_ports(modules, rails, signals)

    module_blocks: List[ModuleBlock] = []
    module_sheet_paths: Dict[str, str] = {}

    for index, module in enumerate(modules):
        module_id = module["module_id"]
        ports = ports_by_module[module_id]
        sheet_path = sheets_dir / f"{module_id}.kicad_sch"
        module_block = _create_module_sheet(module, ports, sheet_path)
        module_blocks.append(module_block)
        module_sheet_paths[module_id] = str(sheet_path)
        logger.debug("Created module sheet for %s at %s", module_id, sheet_path)

    top_path = project_root / "Top.kicad_sch"
    _create_top_sheet(blueprint, module_blocks, top_path, signals=signals, rails=rails)

    project_file = project_root / "project.kicad_pro"
    _write_project_file(project_file, blueprint, top_path, module_blocks)

    _export_with_kicad_cli(project_root, out_dir, top_path)

    return {
        "project_file": str(project_file),
        "top_schematic": str(top_path),
        "module_sheets": module_sheet_paths,
        "outputs": {
            "erc": str(out_dir / "erc.txt"),
            "pdf": str(out_dir / "Top.pdf"),
            "svg": str(out_dir / "Top.svg"),
        },
    }


def _build_module_ports(
    modules: Iterable[Dict],
    rails: Iterable[Dict],
    signals: Iterable[Dict],
) -> Dict[str, ModulePorts]:
    ports_map: Dict[str, ModulePorts] = {
        module["module_id"]: ModulePorts() for module in modules
    }

    for module in modules:
        module_id = module["module_id"]
        module_ports = ports_map[module_id]

        for rail_id in module.get("uses_rails", []) or []:
            if rail_id not in module_ports.power_inputs:
                module_ports.power_inputs.append(rail_id)

        for rail_id in module.get("produces_rails", []) or []:
            if rail_id not in module_ports.power_outputs:
                module_ports.power_outputs.append(rail_id)

        for signal_id in module.get("uses_signals", []) or []:
            if signal_id not in module_ports.signal_inputs:
                module_ports.signal_inputs.append(signal_id)

        for signal_id in module.get("drives_signals", []) or []:
            if signal_id not in module_ports.signal_outputs:
                module_ports.signal_outputs.append(signal_id)

    # Supplement ports using rails/signal connectivity info for completeness
    for rail in rails:
        source_ref = rail.get("primary_source_ref")
        if rail.get("primary_source_kind") == "module" and source_ref in ports_map:
            port = ports_map[source_ref]
            if rail["rail_id"] not in port.power_outputs:
                port.power_outputs.append(rail["rail_id"])
        for consumer in rail.get("consumers", []) or []:
            if consumer in ports_map:
                port = ports_map[consumer]
                if rail["rail_id"] not in port.power_inputs:
                    port.power_inputs.append(rail["rail_id"])

    for signal in signals:
        source = signal.get("source")
        sinks = signal.get("sinks", []) or []
        direction = signal.get("direction", "").lower()
        signal_id = signal["signal_id"]
        if direction == "bidirectional":
            for participant in [source, *sinks]:
                if participant in ports_map:
                    ports = ports_map[participant]
                    if signal_id not in ports.signal_bidirectional:
                        ports.signal_bidirectional.append(signal_id)
            continue

        if source in ports_map:
            ports = ports_map[source]
            if signal_id not in ports.signal_outputs:
                ports.signal_outputs.append(signal_id)

        for sink in sinks:
            if sink in ports_map:
                ports = ports_map[sink]
                if signal_id not in ports.signal_inputs:
                    ports.signal_inputs.append(signal_id)

    # Remove bidirectional signals from inputs/outputs to avoid duplicates
    # Bidirectional signals should only appear in signal_bidirectional list
    for ports in ports_map.values():
        for bidir_signal in ports.signal_bidirectional:
            if bidir_signal in ports.signal_inputs:
                ports.signal_inputs.remove(bidir_signal)
            if bidir_signal in ports.signal_outputs:
                ports.signal_outputs.remove(bidir_signal)

    # Sort ports for deterministic ordering
    for ports in ports_map.values():
        ports.signal_inputs.sort()
        ports.signal_outputs.sort()
        ports.signal_bidirectional.sort()
        ports.power_inputs.sort()
        ports.power_outputs.sort()

    return ports_map


def _create_module_sheet(module: Dict, ports: ModulePorts, sheet_path: Path) -> ModuleBlock:
    """Generate a per-module sheet populated with global labels."""
    schematic = SchematicManager.create_schematic(module["module_id"])
    tree = schematic.tree
    if tree is None:
        raise RuntimeError("Schematic tree is None")

    function_text = module.get("function") or module["module_id"]
    description = module.get("notes", "")

    if function_text:
        text_node = _make_text_node(function_text, 80.0, 30.0)
        tree.append(text_node)
    if description:
        desc_node = _make_text_node(description, 80.0, 38.0, font_size=1.2)
        tree.append(desc_node)

    labels = _build_module_global_labels(ports)
    tree.extend(labels)

    # Remove the root sheet_instances section from child sheets
    # Child sheets should not have (path "/" (page "1")) - that's only for root sheets
    # The instance information is stored in the parent sheet instead
    _remove_sheet_instances(tree)

    SchematicManager.save_schematic(schematic, str(sheet_path))

    # The top sheet needs block metadata; compute size from ports
    block = _prepare_module_block(module, ports, sheet_path)
    return block


def _remove_sheet_instances(tree: List) -> None:
    """Remove the sheet_instances section from a schematic tree.

    Child sheets in a hierarchical design should not have a root sheet_instances
    section with (path "/" (page "1")). The instance information is stored in the
    parent sheet instead. Only the root schematic should have this section.
    """
    # Find and remove the sheet_instances entry
    for i, entry in enumerate(tree):
        if isinstance(entry, list) and entry and entry[0] == Symbol("sheet_instances"):
            tree.pop(i)
            return


def _prepare_module_block(module: Dict, ports: ModulePorts, sheet_path: Path) -> ModuleBlock:
    """Compute block size, pin placement, and build sheet entry."""
    module_id = module["module_id"]
    function = module.get("function") or module_id

    # Determine block dimensions from connector counts
    left_count = len(ports.signal_inputs) + len(ports.signal_bidirectional)
    right_count = len(ports.signal_outputs)
    top_count = len(ports.power_inputs)
    bottom_count = len(ports.power_outputs)

    width = max(
        BLOCK_WIDTH_MIN,
        BLOCK_PADDING * 2 + max(1, max(top_count, bottom_count)) * POWER_SPACING,
    )
    height = max(
        BLOCK_HEIGHT_MIN,
        BLOCK_PADDING * 2 + max(1, max(left_count, right_count)) * PIN_SPACING,
    )

    # Placeholder centre; will be updated when laying out the top sheet
    center = (0.0, 0.0)

    pins: List[BlockPin] = []
    # Helper closures for coordinate generation
    def horizontal_positions(count: int, offset: float) -> Iterable[float]:
        if count <= 0:
            return []
        span = (count - 1) * POWER_SPACING
        start = -span / 2.0
        return (offset + start + idx * POWER_SPACING for idx in range(count))

    def vertical_positions(count: int, offset: float) -> Iterable[float]:
        if count <= 0:
            return []
        span = (count - 1) * PIN_SPACING
        start = -span / 2.0
        return (offset + start + idx * PIN_SPACING for idx in range(count))

    # Top (power inputs)
    for x_offset, rail in zip(horizontal_positions(len(ports.power_inputs), 0.0), ports.power_inputs):
        pin_pos = (x_offset, -height / 2.0)
        label_pos = (pin_pos[0], pin_pos[1] - LABEL_OFFSET)
        pins.append(
            BlockPin(
                name=rail,
                shape="power_in",
                orientation="top",
                angle=90,
                position=pin_pos,
                label_position=label_pos,
            )
        )

    # Bottom (power outputs)
    for x_offset, rail in zip(horizontal_positions(len(ports.power_outputs), 0.0), ports.power_outputs):
        pin_pos = (x_offset, height / 2.0)
        label_pos = (pin_pos[0], pin_pos[1] + LABEL_OFFSET)
        pins.append(
            BlockPin(
                name=rail,
                shape="power_out",
                orientation="bottom",
                angle=270,
                position=pin_pos,
                label_position=label_pos,
            )
        )

    # Left (signal inputs + bidirectional)
    left_stream = list(ports.signal_inputs) + list(ports.signal_bidirectional)
    for y_offset, signal in zip(vertical_positions(len(left_stream), 0.0), left_stream):
        shape = "bidirectional" if signal in ports.signal_bidirectional else "input"
        pin_pos = (-width / 2.0, y_offset)
        label_pos = (pin_pos[0] - LABEL_OFFSET, pin_pos[1])
        pins.append(
            BlockPin(
                name=signal,
                shape=shape,
                orientation="left",
                angle=180,
                position=pin_pos,
                label_position=label_pos,
            )
        )

    # Right (signal outputs)
    for y_offset, signal in zip(vertical_positions(len(ports.signal_outputs), 0.0), ports.signal_outputs):
        pin_pos = (width / 2.0, y_offset)
        label_pos = (pin_pos[0] + LABEL_OFFSET, pin_pos[1])
        pins.append(
            BlockPin(
                name=signal,
                shape="output",
                orientation="right",
                angle=0,
                position=pin_pos,
                label_position=label_pos,
            )
        )

    # Build sheet node with placeholder centre (0,0); actual coordinates injected later.
    sheet_node, uuid_str = _build_sheet_node(module_id, function, sheet_path, width, height, pins)

    return ModuleBlock(
        module_id=module_id,
        function=function,
        center=center,
        size=(width, height),
        pins=pins,
        sheet_node=sheet_node,
        sheet_path=sheet_path,
        uuid=uuid_str,
    )


def _build_sheet_node(
    module_id: str,
    function: str,
    sheet_path: Path,
    width: float,
    height: float,
    pins: List[BlockPin],
) -> Tuple[List, str]:
    """Compose a hierarchical sheet S-expression node."""
    uuid_str = str(uuid4())
    sheet_uuid = Symbol(uuid_str)

    # Sheet node format must match KiCad's exact order (verified from demo projects):
    # (sheet (at ...) (size ...) (stroke ...) (fill ...) (uuid ...) (property ...) ...)
    # Note: No (name ...) entry - the name is only in the "Sheet name" property
    sheet_node: List = [
        Symbol("sheet"),
        [Symbol("at"), 0.0, 0.0],
        [Symbol("size"), round(width, 3), round(height, 3)],
        [Symbol("stroke"), [Symbol("width"), 0], [Symbol("type"), Symbol("solid")], [Symbol("color"), 0, 0, 0, 0]],
        [Symbol("fill"), [Symbol("color"), 0, 0, 0, 0.0000]],
        [Symbol("uuid"), sheet_uuid],
        _sheet_property("Sheet name", module_id, 0.0, -height / 2.0 - 5.0, 0),
        _sheet_property("Sheet file", f"sheets/{sheet_path.name}", 0.0, height / 2.0 + 5.0, 1),
    ]

    if function:
        sheet_node.append(_sheet_property("Sheet description", function, 0.0, height / 2.0 + 12.0, 2))

    for index, pin in enumerate(pins, start=1):
        sheet_node.append(_build_sheet_pin(pin, index))

    # Note: The instances section will be added later by _add_sheet_instances
    # after we know the project name and page number

    return sheet_node, uuid_str


def _sanitize_text(text: str) -> str:
    """Sanitize text for KiCad schematic files.

    KiCad's parser may have issues with certain non-ASCII characters.
    Replace common problematic characters with ASCII equivalents.
    """
    # Replace en-dash and em-dash with regular hyphen
    text = text.replace('–', '-').replace('—', '-')
    # Replace non-breaking hyphen with regular hyphen
    text = text.replace('‑', '-')
    # Replace curly quotes with straight quotes
    text = text.replace('"', '"').replace('"', '"').replace(''', "'").replace(''', "'")
    # Keep only ASCII characters (allow basic Latin characters)
    text = ''.join(char if ord(char) < 128 else '?' for char in text)
    return text


def _sheet_property(name: str, value: str, x: float, y: float, prop_id: int) -> List:
    """Build a hierarchical sheet property.

    Sheet properties define metadata about the sheet such as its name and file path.
    According to KiCad file format (verified from demo projects), sheet properties
    require an (id N) field between the value and the position.

    The justify setting depends on the property:
    - "Sheet name" uses "left bottom"
    - "Sheet file" uses "left top"
    - Other properties use "left top"
    """
    # Sanitize the value to remove non-ASCII characters that may cause parsing issues
    value = _sanitize_text(value)

    # Determine justify based on property name
    if name == "Sheet name":
        justify = [Symbol("justify"), Symbol("left"), Symbol("bottom")]
    else:
        justify = [Symbol("justify"), Symbol("left"), Symbol("top")]

    effects = [
        Symbol("effects"),
        [Symbol("font"), [Symbol("size"), 1.27, 1.27]],
        justify,
    ]
    return [
        Symbol("property"),
        name,
        value,
        [Symbol("id"), prop_id],
        [Symbol("at"), round(x, 3), round(y, 3), 0],
        effects,
    ]


def _build_sheet_pin(pin: BlockPin, number: int) -> List:
    """Build a hierarchical sheet pin according to KiCad file format.

    Format from KiCad documentation:
    (pin
      "NAME"                                                      (1)
      input | output | bidirectional | tri_state | passive        (2)
      POSITION_IDENTIFIER                                         (3)
      TEXT_EFFECTS                                                (4)
      UNIQUE_IDENTIFIER                                           (5)
    )
    """
    text_effects = [
        Symbol("effects"),
        [Symbol("font"), [Symbol("size"), 1.27, 1.27]],
    ]

    # Map power_in/power_out to valid KiCad pin types
    # KiCad hierarchical sheet pins only support: input, output, bidirectional, tri_state, passive
    # power_in and power_out are NOT valid pin types for hierarchical sheets
    shape = pin.shape
    if shape == "power_in":
        shape = "input"
    elif shape == "power_out":
        shape = "output"

    return [
        Symbol("pin"),
        pin.name,  # Pin name (not number!)
        Symbol(shape),  # Shape: input, output, bidirectional, tri_state, passive
        [Symbol("at"), round(pin.position[0], 3), round(pin.position[1], 3), pin.angle],
        text_effects,
        [Symbol("uuid"), Symbol(str(uuid4()))],
    ]


def _build_module_global_labels(ports: ModulePorts) -> List[List]:
    """Create global labels positioned around a module schematic."""
    labels: List[List] = []

    top_y = 30.0
    bottom_y = 110.0
    left_x = 30.0
    right_x = 130.0

    def add_labels(items: Iterable[str], anchor: Tuple[float, float], orientation: str, shape: str) -> None:
        names = list(items)
        count = len(names)
        if count == 0:
            return
        if orientation in {"left", "right"}:
            span = (count - 1) * PIN_SPACING
            start = anchor[1] - span / 2.0
            for index, name in enumerate(names):
                x = anchor[0]
                y = start + index * PIN_SPACING
                labels.append(_make_global_label(name, shape, x, y, orientation))
            return

        span = (count - 1) * POWER_SPACING
        start = anchor[0] - span / 2.0
        for index, name in enumerate(names):
            x = start + index * POWER_SPACING
            y = anchor[1]
            labels.append(_make_global_label(name, shape, x, y, orientation))

    # For labels, use logical directions only (KiCad global_label does not support power_in/out shapes)
    add_labels(ports.power_inputs, (60.0, top_y), "top", "input")
    add_labels(ports.power_outputs, (60.0, bottom_y), "bottom", "output")
    add_labels(ports.signal_inputs, (left_x, 60.0), "left", "input")
    add_labels(ports.signal_outputs, (right_x, 60.0), "right", "output")
    add_labels(ports.signal_bidirectional, (left_x, 80.0), "left", "bidirectional")

    return labels


def _make_global_label(name: str, shape: str, x: float, y: float, orientation: str) -> List:
    node = deepcopy(ElementTemplate["global_label"])
    node[1] = name
    node[2][1] = Symbol(shape)
    angle = _angle_for_orientation(orientation)
    node[3][1] = round(x, 3)
    node[3][2] = round(y, 3)
    node[3][3] = angle
    node[5] = _label_effects_for_orientation(orientation)
    node[6][1] = Symbol(str(uuid4()))
    node[7][3][1] = round(x, 3)
    node[7][3][2] = round(y, 3)
    return node


def _label_effects_for_orientation(orientation: str) -> List:
    if orientation == "left":
        justify = [Symbol("justify"), Symbol("right")]
    elif orientation == "right":
        justify = [Symbol("justify"), Symbol("left")]
    elif orientation == "top":
        justify = [Symbol("justify"), Symbol("bottom"), Symbol("center")]
    else:
        justify = [Symbol("justify"), Symbol("top"), Symbol("center")]
    return [
        Symbol("effects"),
        [Symbol("font"), [Symbol("size"), 1.27, 1.27]],
        justify,
    ]


def _angle_for_orientation(orientation: str) -> int:
    return {
        "left": 180,
        "right": 0,
        "top": 90,
        "bottom": 270,
    }[orientation]


def _make_text_node(
    value: str,
    x: float,
    y: float,
    *,
    font_size: float = 1.8,
) -> List:
    # Sanitize the text to remove non-ASCII characters
    value = _sanitize_text(value)

    node = deepcopy(ElementTemplate["text"])
    node[1] = value
    node[2] = [Symbol("at"), round(x, 3), round(y, 3), 0]

    effects = node[3]
    if len(effects) >= 2 and isinstance(effects[1], list) and effects[1][0] == Symbol("font"):
        font_spec = effects[1]
        if len(font_spec) >= 2 and isinstance(font_spec[1], list) and font_spec[1][0] == Symbol("size"):
            font_spec[1][1] = round(font_size, 3)
            font_spec[1][2] = round(font_size, 3)

    node[4][1] = Symbol(str(uuid4()))
    return node


def _create_top_sheet(blueprint: Dict, module_blocks: List[ModuleBlock], top_path: Path, *, signals: List[Dict], rails: List[Dict]) -> None:
    """Compose the root schematic with sheet symbols and labels."""
    top_schematic = SchematicManager.create_schematic("Top")
    tree = top_schematic.tree
    if tree is None:
        raise RuntimeError("Schematic tree is None")

    # Layout modules onto a loose grid
    column_count = max(1, int(math.ceil(math.sqrt(len(module_blocks)))))
    for idx, block in enumerate(module_blocks):
        column = idx % column_count
        row = idx // column_count
        center_x = PAPER_MARGIN + column * COLUMN_SPACING
        center_y = PAPER_MARGIN + row * ROW_SPACING
        block.center = (center_x, center_y)
        _update_sheet_position(block.sheet_node, center_x, center_y)
        tree.append(block.sheet_node)

    _populate_sheet_instances(tree, module_blocks)

    # Build a map of absolute pin coordinates by (module_id, pin_name)
    # Pin format: (pin "NAME" shape (at x y angle) (effects ...) (uuid ...))
    abs_pin_map: Dict[Tuple[str, str], Tuple[float, float]] = {}
    for block in module_blocks:
        for entry in block.sheet_node:
            if isinstance(entry, list) and entry and entry[0] == Symbol("pin"):
                # In the new format, pin name is the second element (entry[1])
                if len(entry) < 4:
                    raise RuntimeError(f"Malformed sheet pin in module {block.module_id}: {entry}")
                pin_name = str(entry[1])  # Second element is the pin name
                at_entry = next((e for e in entry if isinstance(e, list) and e and e[0] == Symbol("at")), None)
                if at_entry is None or len(at_entry) < 3:
                    raise RuntimeError(f"Malformed sheet pin 'at' in module {block.module_id}")
                abs_pin_map[(block.module_id, pin_name)] = (float(at_entry[1]), float(at_entry[2]))

    # Wire signals: from source module output/bidir pin to each sink module input/bidir pin
    channel_offset: Dict[str, float] = {}

    def _route_between(a: Tuple[float, float], b: Tuple[float, float], *, key: str) -> List[List]:
        sx, sy = a
        tx, ty = b
        if key not in channel_offset:
            channel_offset[key] = (sx + tx) / 2.0
        mx = channel_offset[key]
        # Manhattan route: (sx,sy) -> (mx,sy) -> (mx,ty) -> (tx,ty)
        pts = [(sx, sy), (mx, sy), (mx, ty), (tx, ty)]
        wires = []
        for p, q in zip(pts, pts[1:]):
            wires.append(_make_wire(p, q))
        return wires

    # Signals
    for sig in signals:
        sid = sig.get("signal_id")
        if not sid:
            raise ValueError("Signal missing 'signal_id'")
        src = sig.get("source")
        sinks = sig.get("sinks", []) or []
        if not src or not isinstance(sinks, list) or not sinks:
            # A signal without both endpoints is invalid for top wiring
            raise ValueError(f"Signal '{sid}' missing source or sinks")
        try:
            start = abs_pin_map[(src, sid)]
        except KeyError as exc:
            raise ValueError(f"Source pin for signal '{sid}' not found on module '{src}'") from exc
        for sk in sinks:
            try:
                end = abs_pin_map[(sk, sid)]
            except KeyError as exc:
                raise ValueError(f"Sink pin for signal '{sid}' not found on module '{sk}'") from exc
            for w in _route_between(start, end, key=f"sig:{sid}"):
                tree.append(w)

    # Rails
    for rail in rails:
        rid = rail.get("rail_id")
        if not rid:
            raise ValueError("Rail missing 'rail_id'")
        kind = (rail.get("primary_source_kind") or "").lower()
        src_ref = rail.get("primary_source_ref")
        consumers = rail.get("consumers", []) or []
        if kind != "module" or not src_ref:
            # External or unspecified primary source: skip top wiring
            continue
        if not consumers:
            continue
        try:
            start = abs_pin_map[(src_ref, rid)]
        except KeyError as exc:
            raise ValueError(f"Producer pin for rail '{rid}' not found on module '{src_ref}'") from exc
        for c in consumers:
            try:
                end = abs_pin_map[(c, rid)]
            except KeyError as exc:
                raise ValueError(f"Consumer pin for rail '{rid}' not found on module '{c}'") from exc
            for w in _route_between(start, end, key=f"rail:{rid}"):
                tree.append(w)

    SchematicManager.save_schematic(top_schematic, str(top_path))


def _update_sheet_position(sheet_node: List, center_x: float, center_y: float) -> None:
    for entry in sheet_node:
        if isinstance(entry, list) and entry and entry[0] == Symbol("at") and len(entry) >= 3:
            entry[1] = round(center_x, 3)
            entry[2] = round(center_y, 3)
            break

    # Shift pin coordinates to absolute sheet space
    for entry in sheet_node:
        if isinstance(entry, list) and entry and entry[0] == Symbol("pin"):
            at_entry = next((item for item in entry if isinstance(item, list) and item and item[0] == Symbol("at")), None)
            if at_entry is not None:
                at_entry[1] = round(center_x + at_entry[1], 3)
                at_entry[2] = round(center_y + at_entry[2], 3)

        if isinstance(entry, list) and entry and entry[0] == Symbol("property"):
            # Property format: (property "name" "value" (id N) (at x y angle) (effects ...))
            # Find the (at ...) entry
            at_entry = next((item for item in entry if isinstance(item, list) and item and item[0] == Symbol("at")), None)
            if at_entry is not None:
                at_entry[1] = round(center_x + at_entry[1], 3)
                at_entry[2] = round(center_y + at_entry[2], 3)


def _populate_sheet_instances(tree: List, module_blocks: List[ModuleBlock]) -> None:
    """Populate sheet instances in both the root sheet_instances section and each sheet node.

    According to KiCad file format:
    1. The root schematic has a sheet_instances section listing all hierarchical paths
    2. Each sheet node also needs an instances section with project/path/page info
    """
    # Find the root sheet_instances section
    sheet_instances = next(
        (entry for entry in tree if isinstance(entry, list) and entry and entry[0] == Symbol("sheet_instances")),
        None,
    )
    if sheet_instances is None:
        return

    # Add each sheet's path to the root sheet_instances section
    for index, block in enumerate(module_blocks, start=1):
        sheet_instances.append([
            Symbol("path"), f"/{block.uuid}", [Symbol("page"), str(index + 1)]
        ])

        # Note: Based on KiCad demo projects (complex_hierarchy), sheet nodes do NOT
        # have an instances section. Only the root sheet_instances section is needed.


def _translate_point(origin: Tuple[float, float], offset: Tuple[float, float]) -> Tuple[float, float]:
    return (round(origin[0] + offset[0], 3), round(origin[1] + offset[1], 3))


def _make_wire(start: Tuple[float, float], end: Tuple[float, float]) -> List:
    wire = deepcopy(ElementTemplate["wire"])
    pts = wire[1]
    pts[1] = [Symbol("xy"), round(start[0], 3), round(start[1], 3)]
    pts[2] = [Symbol("xy"), round(end[0], 3), round(end[1], 3)]
    # Update stroke to match KiCad demo format: (stroke (width ...) (type solid) (color 0 0 0 0))
    wire[2][1][1] = round(WIRE_WIDTH, 3)
    wire[2][2][1] = Symbol("solid")  # Change type from "default" to "solid"
    wire[2].append([Symbol("color"), 0, 0, 0, 0])  # Add color
    wire[3][1] = Symbol(str(uuid4()))
    return wire


def _write_project_file(project_file: Path, blueprint: Dict, top_path: Path, blocks: List[ModuleBlock]) -> None:
    """Write a minimal valid KiCad project file.

    KiCad project files (.kicad_pro) are JSON files with a specific structure.
    For schematic-only projects, we need minimal board and schematic sections.
    """
    data = {
        "board": {
            "design_settings": {}
        },
        "schematic": {
            "drawing": {},
            "legacy_lib_dir": "",
            "legacy_lib_list": []
        },
        "text_variables": {}
    }
    project_file.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _resolve_kicad_cli() -> str:
    import shutil
    cand = os.environ.get("KICAD_CLI")
    if cand:
        path = shutil.which(cand) if os.sep not in cand else cand
        if path and Path(path).exists():
            return path
    path = shutil.which("kicad-cli")
    if path:
        return path
    raise FileNotFoundError("kicad-cli not found; set KICAD_CLI env var or add to PATH")


def _run(cmd: List[str], cwd: Optional[str] = None) -> None:
    import subprocess
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    if proc.returncode != 0:
        raise RuntimeError(f"Command failed ({' '.join(cmd)}): {proc.stderr or proc.stdout}")


def _export_with_kicad_cli(project_root: Path, out_dir: Path, top_path: Path) -> None:
    cli = _resolve_kicad_cli()
    pdf_path = out_dir / "Top.pdf"
    svg_path = out_dir / "Top.svg"
    erc_path = out_dir / "erc.txt"

    _run([cli, "sch", "export", "pdf", "-o", str(pdf_path), str(top_path)], cwd=str(project_root))
    _run([cli, "sch", "export", "svg", "-o", str(svg_path), str(top_path)], cwd=str(project_root))
    _run([cli, "sch", "erc", "-o", str(erc_path), str(top_path)], cwd=str(project_root))

    # Basic file checks
    for p in (pdf_path, svg_path, erc_path):
        if not p.exists() or p.stat().st_size == 0:
            raise RuntimeError(f"Export failed or empty output: {p}")


__all__ = [
    "generate_block_diagram",
]
