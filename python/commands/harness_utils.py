"""
Utilities for building KiCad harness schematics around module sheets.

These helpers are shared between the blueprint generator and ERC workflows.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple
from uuid import uuid4

from sexpdata import Symbol
from skip import Schematic

from .grid_utils import snap_to_grid
from .schematic import SchematicManager

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HarnessLabel:
    """Definition of a sheet pin exported by the harness."""

    name: str
    direction: str = "bidirectional"

    def normalised_direction(self) -> str:
        """Return a KiCad-compatible direction token."""
        allowed = {
            "input",
            "output",
            "bidirectional",
            "tri_state",
            "passive",
            "unspecified",
            "power_in",
            "power_out",
            "no_connect",
        }
        candidate = (self.direction or "").strip().lower()
        # map some synonyms
        alias = {
            "in": "input",
            "out": "output",
            "inout": "bidirectional",
            "bi": "bidirectional",
        }
        candidate = alias.get(candidate, candidate)
        return candidate if candidate in allowed else "bidirectional"


def labels_from_connections(connections: Dict[str, Iterable[str]]) -> List[HarnessLabel]:
    """
    Convert the connection sets used by blueprint generation into a flat label list.

    The input dictionary typically contains:
        - power_inputs / power_outputs
        - signal_inputs / signal_outputs
        - bidirectional_labels
    """
    bidirectional = set(connections.get("bidirectional_labels", []))

    ordered: List[HarnessLabel] = []

    def _extend(names: Iterable[str], default_dir: str) -> None:
        for name in sorted(set(names)):
            direction = "bidirectional" if name in bidirectional else default_dir
            ordered.append(HarnessLabel(name=name, direction=direction))

    _extend(connections.get("power_outputs", []), "output")
    _extend(connections.get("power_inputs", []), "input")
    _extend(connections.get("signal_outputs", []), "output")
    _extend(connections.get("signal_inputs", []), "input")

    return ordered


def extract_hierarchical_labels_from_sheet(schematic) -> List[HarnessLabel]:
    """
    Inspect a module sheet and return its hierarchical labels as harness pins.
    """
    labels: List[HarnessLabel] = []
    tree = getattr(schematic, "tree", []) or []

    for entry in tree:
        if not (isinstance(entry, list) and entry):
            continue
        if entry[0] != Symbol("hierarchical_label"):
            continue
        if len(entry) < 2:
            continue

        try:
            name = str(entry[1]).strip()
        except Exception:
            name = ""
        if not name:
            continue

        direction = "bidirectional"
        for sub in entry:
            if isinstance(sub, list) and sub and sub[0] == Symbol("shape") and len(sub) > 1:
                try:
                    direction = str(sub[1]).strip().lower()
                except Exception:
                    direction = "bidirectional"
                break

        labels.append(HarnessLabel(name=name, direction=direction))

    labels.sort(key=lambda lbl: lbl.name)
    return labels


def create_harness_schematic(
    module_id: str,
    labels: Sequence[HarnessLabel],
    sheet_relpath: str,
    *,
    title: Optional[str] = None,
    description: Optional[str] = None,
    origin_x: float = 40.0,
    origin_y: float = 40.0,
    sheet_width: float = 50.0,
    sheet_height: float = 40.0,
) -> "Schematic":
    """
    Build a harness schematic S-expression that references a module sheet.
    """
    metadata = {
        "title": title or f"{module_id} Harness",
        "description": description or "",
    }
    harness = SchematicManager.create_schematic(f"{module_id}_Harness", metadata=metadata)
    tree = harness.tree
    if not isinstance(tree, list):
        raise RuntimeError("Harness schematic tree is not a list")

    root_uuid = get_root_uuid(tree)
    sheet_node, sheet_uuid = create_sheet_symbol(
        module_id=module_id,
        sheet_relpath=sheet_relpath,
        labels=labels,
        origin_x=origin_x,
        origin_y=origin_y,
        sheet_width=sheet_width,
        sheet_height=sheet_height,
    )

    tree.append(sheet_node)
    tree.extend(build_no_connect_markers(sheet_node))
    rebuild_sheet_instances_at_end(tree, root_uuid, {module_id: sheet_uuid})
    return harness


def save_harness_schematic(harness, path: str) -> None:
    """Persist a harness schematic to disk."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    SchematicManager.save_schematic(harness, path)


def create_sheet_symbol(
    module_id: str,
    sheet_relpath: str,
    labels: Sequence[HarnessLabel],
    *,
    origin_x: float,
    origin_y: float,
    sheet_width: float,
    sheet_height: float,
) -> Tuple[List, str]:
    """Construct a sheet symbol referencing the module sheet."""
    x = snap_to_grid(origin_x)
    y = snap_to_grid(origin_y)
    width = snap_to_grid(sheet_width)
    height = snap_to_grid(sheet_height)

    sheet_uuid = str(uuid4())

    node: List = [
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

    node.extend(_build_sheet_pin_entries(labels, x, y))
    return node, sheet_uuid


def _build_sheet_pin_entries(labels: Sequence[HarnessLabel], x: float, y: float) -> List[List]:
    """Create pin entries for the sheet symbol."""
    pins: List[List] = []
    pin_offset = 10.0
    pin_step = 5.0

    for label in labels:
        pin_y = snap_to_grid(y + pin_offset)
        pins.append([
            Symbol("pin"),
            label.name,
            Symbol(label.normalised_direction()),
            [Symbol("at"), x, pin_y, 0],
            [Symbol("effects"), [Symbol("font"), [Symbol("size"), 1.27, 1.27]]],
            [Symbol("uuid"), Symbol(str(uuid4()))],
        ])
        pin_offset += pin_step

    return pins


def build_no_connect_markers(sheet_node: List) -> List[List]:
    """Generate 'no_connect' markers for every sheet pin in the harness."""
    markers: List[List] = []
    for x_pos, y_pos in extract_sheet_pin_positions(sheet_node):
        markers.append([
            Symbol("no_connect"),
            [Symbol("at"), snap_to_grid(x_pos), snap_to_grid(y_pos)],
            [Symbol("uuid"), Symbol(str(uuid4()))],
        ])
    return markers


def extract_sheet_pin_positions(sheet_node: List) -> List[Tuple[float, float]]:
    """Return the schematic coordinates where each sheet pin connects."""
    origin = None
    size = None

    for entry in sheet_node:
        if isinstance(entry, list) and entry:
            if entry[0] == Symbol("at") and len(entry) >= 3:
                origin = (float(entry[1]), float(entry[2]))
            elif entry[0] == Symbol("size") and len(entry) >= 3:
                size = (float(entry[1]), float(entry[2]))

    if origin is None or size is None:
        return []

    origin_x, origin_y = origin
    width, height = size
    positions: List[Tuple[float, float]] = []

    for entry in sheet_node:
        if not (isinstance(entry, list) and entry and entry[0] == Symbol("pin")):
            continue

        at_node = next((item for item in entry if isinstance(item, list) and item and item[0] == Symbol("at")), None)
        if at_node is None or len(at_node) < 3:
            continue

        pin_x = float(at_node[1])
        pin_y = float(at_node[2])
        orientation = int(at_node[3]) if len(at_node) >= 4 else 0

        if orientation == 0:
            positions.append((origin_x + width, pin_y))
        elif orientation == 180:
            positions.append((origin_x, pin_y))
        elif orientation == 90:
            positions.append((pin_x, origin_y))
        elif orientation == 270:
            positions.append((pin_x, origin_y + height))
        else:
            positions.append((origin_x + width, pin_y))

    return positions


def get_root_uuid(tree: List) -> str:
    """Extract the root schematic UUID from a tree."""
    for entry in tree:
        if isinstance(entry, list) and entry and entry[0] == Symbol("uuid"):
            return str(entry[1])
    raise RuntimeError("Root UUID not found in schematic tree")


def rebuild_sheet_instances_at_end(tree: List, root_uuid: str, sheet_uuids: Dict[str, str]) -> None:
    """Ensure sheet_instances exists at end of tree with correct paths."""
    for index in range(len(tree) - 1, -1, -1):
        if isinstance(tree[index], list) and tree[index] and tree[index][0] == Symbol("sheet_instances"):
            tree.pop(index)
            break

    sheet_instances: List = [Symbol("sheet_instances")]
    sheet_instances.append([Symbol("path"), "/", [Symbol("page"), "1"]])

    for page, sheet_uuid in enumerate(sheet_uuids.values(), start=2):
        sheet_instances.append([
            Symbol("path"),
            f"/{root_uuid}/{sheet_uuid}",
            [Symbol("page"), str(page)],
        ])

    tree.append(sheet_instances)
