"""
Top-level schematic generation from a contract.

This module is intentionally separate from the netlist-to-schematic pipeline: it generates
only the *top* sheet for a hierarchical KiCad project by placing hierarchical sheet symbols
and connecting their sheet pins via net labels.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterable
from uuid import uuid4

from sexpdata import Symbol

from python.commands.kicad_schematics.grid_utils import snap_to_grid
from python.commands.kicad_schematics.harness_utils import (
    get_root_uuid,
    rebuild_sheet_instances_at_end,
)
from python.commands.kicad_schematics.schematic import SchematicManager


def generate_top_schematic_from_contract(
    *,
    module_sheets: Dict[str, str],
    signals: Iterable[Dict[str, Any]] = (),
    rails: Iterable[Dict[str, Any]] = (),
    output_path: str,
    export_svg: bool = False,
    relative_sheet_paths: bool = True,
    connect_pins: bool = True,
    show_net_labels: bool = True,
    paper: str | None = None,
    columns: int | None = None,
    origin_x: float = 40.0,
    origin_y: float = 40.0,
    x_spacing: float = 80.0,
    y_spacing: float = 60.0,
    sheet_width: float = 50.0,
    sheet_height: float = 40.0,
    pin_stub_length: float = 2.54,
    pin_stub_width: float = 0.254,
) -> Dict[str, Any]:
    """
    Generate a top-level *hierarchical* KiCad schematic from a pre-extracted contract.

    Contract (source of truth):
    - `module_sheets`: module_id -> module schematic path (stored verbatim in "Sheet file")
    - `signals`: items with keys:
        - signal_id: str (pin/net name)
        - source: str (module_id)
        - sinks: list[str] (module_id list)
        - direction: "source->sink" | "bidirectional" (controls pin type only)
    - `rails`: items with keys:
        - rail_id: str (pin/net name)
        - primary_source_kind: usually "module" or "external"
        - primary_source_ref: module_id if primary_source_kind == "module"
        - consumers: list[str] (module_id list)

    Output:
    - One sheet symbol per module_id (a box).
    - Sheet pins are placed on left/right:
        - rails/signals sourced by the module -> right side
        - rails/signals consumed by the module -> left side
    - When `connect_pins=True`, each sheet pin gets a short wire stub plus a net label at the
      stub end. This ensures pins participate in a named net on the top sheet and are electrically
      connected to other pins with the same net label.

    Notes:
    - This function does not read/validate module `.kicad_sch` contents; it only references paths.
    - For portable KiCad projects, prefer relative sheet file paths (e.g. `modules/{module_id}.kicad_sch`).
      If you pass absolute paths and want them rewritten relative to the output schematic folder, set
      `relative_sheet_paths=True`.
    - Net labels are visible by default so the top sheet is readable. Set
      `show_net_labels=False` if you only want electrical connectivity without extra text.
    - If `paper`/`columns` are omitted, they are selected automatically based on module count:
        - <= 6 modules: A4, 3 columns
        - 7..12 modules: A3, 4 columns
        - > 12 modules: A2, 6 columns
    """

    def _normalise_direction(value: Any) -> str:
        text = str(value or "").strip().lower()
        if text == "source->sink":
            return "source->sink"
        if text == "bidirectional":
            return "bidirectional"
        return "bidirectional"

    def _add_pin(
        side_map: Dict[str, str],
        opposite_map: Dict[str, str],
        name: str,
        direction: str,
    ) -> None:
        if not name:
            return
        if name in opposite_map:
            opposite_map.pop(name, None)
        existing = side_map.get(name)
        if existing is None:
            side_map[name] = direction
            return
        if existing == direction:
            return
        if existing == "bidirectional" or direction == "bidirectional":
            side_map[name] = "bidirectional"

    def _make_wire_node(
        points: list[tuple[float, float]],
        *,
        width: float,
        stroke_type: str = "default",
    ) -> list:
        pts_expr: list = [Symbol("pts")]
        for x_val, y_val in points:
            pts_expr.append([Symbol("xy"), round(float(x_val), 6), round(float(y_val), 6)])

        return [
            Symbol("wire"),
            pts_expr,
            [
                Symbol("stroke"),
                [Symbol("width"), round(float(width), 6)],
                [Symbol("type"), Symbol(str(stroke_type))],
            ],
            [Symbol("uuid"), str(uuid4())],
        ]

    def _make_net_label_node(
        name: str,
        x: float,
        y: float,
        *,
        angle: int,
        justify: str,
    ) -> list:
        x_snapped = snap_to_grid(float(x))
        y_snapped = snap_to_grid(float(y))

        effects: list = [Symbol("effects"), [Symbol("font"), [Symbol("size"), 1.27, 1.27]]]
        effects.append([Symbol("justify"), Symbol(str(justify))])
        if not show_net_labels:
            effects.append([Symbol("hide"), Symbol("yes")])

        return [
            Symbol("label"),
            name,
            [Symbol("at"), x_snapped, y_snapped, int(angle)],
            [Symbol("fields_autoplaced")],
            effects,
            [Symbol("uuid"), str(uuid4())],
        ]

    def _required_sheet_height_mm(*, left_count: int, right_count: int) -> float:
        pin_margin_top = 10.0
        pin_margin_bottom = 10.0
        pin_step = 5.0
        max_pins = max(int(left_count), int(right_count))
        if max_pins <= 0:
            return float(sheet_height)
        return max(
            float(sheet_height),
            float(pin_margin_top + pin_margin_bottom + (max_pins - 1) * pin_step),
        )

    def _create_sheet_node(
        *,
        module_id: str,
        sheet_file: str,
        x: float,
        y: float,
        width: float,
        height: float,
        left_pins: Dict[str, str],
        right_pins: Dict[str, str],
    ) -> tuple[list, str, list]:
        sheet_uuid = str(uuid4())

        x_snapped = snap_to_grid(float(x))
        y_snapped = snap_to_grid(float(y))
        w_snapped = snap_to_grid(float(width))
        h_snapped = snap_to_grid(float(height))

        sheet_node: list = [
            Symbol("sheet"),
            [Symbol("at"), x_snapped, y_snapped],
            [Symbol("size"), w_snapped, h_snapped],
            [Symbol("stroke"), [Symbol("width"), 0], [Symbol("type"), Symbol("solid")], [Symbol("color"), 0, 0, 0, 0]],
            [Symbol("fill"), [Symbol("color"), 0, 0, 0, 0.0]],
            [Symbol("uuid"), Symbol(sheet_uuid)],
            [
                Symbol("property"),
                "Sheet name",
                module_id,
                [Symbol("id"), 0],
                [Symbol("at"), x_snapped, y_snapped - 2.5, 0],
                [Symbol("effects"), [Symbol("font"), [Symbol("size"), 1.27, 1.27]], [Symbol("justify"), Symbol("left"), Symbol("bottom")]],
            ],
            [
                Symbol("property"),
                "Sheet file",
                sheet_file,
                [Symbol("id"), 1],
                [Symbol("at"), x_snapped, y_snapped + h_snapped + 0.5, 0],
                [
                    Symbol("effects"),
                    [Symbol("font"), [Symbol("size"), 1.27, 1.27]],
                    [Symbol("justify"), Symbol("left"), Symbol("top")],
                    [Symbol("hide"), Symbol("yes")],
                ],
            ],
        ]

        extras: list = []
        pin_margin_top = 10.0
        pin_step = 5.0
        stub_length = snap_to_grid(float(pin_stub_length))
        stub_width = float(pin_stub_width)

        def _add_stub_and_label(*, name: str, pin_y: float, side_orientation: int) -> None:
            if not connect_pins:
                return
            if side_orientation == 0:
                connection_x = x_snapped + w_snapped
                end_x = snap_to_grid(connection_x + stub_length)
                angle = 0
                justify = "left"
            elif side_orientation == 180:
                connection_x = x_snapped
                end_x = snap_to_grid(connection_x - stub_length)
                angle = 180
                justify = "right"
            else:
                return

            extras.append(
                _make_wire_node(
                    [(connection_x, pin_y), (end_x, pin_y)],
                    width=stub_width,
                )
            )
            extras.append(
                _make_net_label_node(
                    name,
                    end_x,
                    pin_y,
                    angle=angle,
                    justify=justify,
                )
            )

        left_names = sorted(left_pins)
        right_names = sorted(right_pins)

        for index, name in enumerate(left_names):
            pin_y = snap_to_grid(y_snapped + pin_margin_top + index * pin_step)
            sheet_node.append([
                Symbol("pin"),
                name,
                Symbol(left_pins[name]),
                [Symbol("at"), x_snapped + w_snapped, pin_y, 180],
                [
                    Symbol("effects"),
                    [Symbol("font"), [Symbol("size"), 1.27, 1.27]],
                    # Place pin name text inside the sheet box (away from the connection side).
                    [Symbol("justify"), Symbol("left")],
                ],
                [Symbol("uuid"), Symbol(str(uuid4()))],
            ])
            _add_stub_and_label(name=name, pin_y=pin_y, side_orientation=180)

        for index, name in enumerate(right_names):
            pin_y = snap_to_grid(y_snapped + pin_margin_top + index * pin_step)
            sheet_node.append([
                Symbol("pin"),
                name,
                Symbol(right_pins[name]),
                [Symbol("at"), x_snapped, pin_y, 0],
                [
                    Symbol("effects"),
                    [Symbol("font"), [Symbol("size"), 1.27, 1.27]],
                    # Place pin name text inside the sheet box (away from the connection side).
                    [Symbol("justify"), Symbol("right")],
                ],
                [Symbol("uuid"), Symbol(str(uuid4()))],
            ])
            _add_stub_and_label(name=name, pin_y=pin_y, side_orientation=0)

        return sheet_node, sheet_uuid, extras

    try:
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)

        module_count = len(module_sheets or {})
        if module_count <= 6:
            default_paper = "A4"
            default_columns = 3
        elif module_count <= 12:
            default_paper = "A3"
            default_columns = 4
        else:
            default_paper = "A2"
            default_columns = 6

        paper_value = (paper or "").strip() or default_paper
        columns_value = int(columns) if columns is not None else int(default_columns)

        schematic = SchematicManager.create_schematic(
            output_file.stem or "Top",
            metadata={
                "paper": paper_value,
                "title": output_file.stem or "Top",
            },
        )

        if not isinstance(schematic.tree, list):
            raise RuntimeError("Top schematic tree is not a list")

        tree = schematic.tree
        root_uuid = get_root_uuid(tree)

        left_by_module: Dict[str, Dict[str, str]] = {mid: {} for mid in module_sheets}
        right_by_module: Dict[str, Dict[str, str]] = {mid: {} for mid in module_sheets}

        for rail in rails or []:
            rail_id = str(rail.get("rail_id") or "").strip()
            if not rail_id:
                continue

            source_kind = str(rail.get("primary_source_kind") or "").strip().lower()
            source_ref = str(rail.get("primary_source_ref") or "").strip()
            consumers = rail.get("consumers") or []

            if source_kind == "module" and source_ref in module_sheets:
                _add_pin(
                    right_by_module[source_ref],
                    left_by_module[source_ref],
                    rail_id,
                    "bidirectional",
                )

            for consumer in consumers:
                consumer_id = str(consumer or "").strip()
                if consumer_id in module_sheets:
                    _add_pin(
                        left_by_module[consumer_id],
                        right_by_module[consumer_id],
                        rail_id,
                        "bidirectional",
                    )

        for sig in signals or []:
            sig_id = str(sig.get("signal_id") or "").strip()
            if not sig_id:
                continue

            direction = _normalise_direction(sig.get("direction"))
            source = str(sig.get("source") or "").strip()
            sinks = sig.get("sinks") or []

            if direction == "source->sink":
                if source in module_sheets:
                    _add_pin(
                        right_by_module[source],
                        left_by_module[source],
                        sig_id,
                        "output",
                    )
                for sink in sinks:
                    sink_id = str(sink or "").strip()
                    if sink_id in module_sheets:
                        _add_pin(
                            left_by_module[sink_id],
                            right_by_module[sink_id],
                            sig_id,
                            "input",
                        )
            else:
                if source in module_sheets:
                    _add_pin(
                        right_by_module[source],
                        left_by_module[source],
                        sig_id,
                        "bidirectional",
                    )
                for sink in sinks:
                    sink_id = str(sink or "").strip()
                    if sink_id in module_sheets:
                        _add_pin(
                            left_by_module[sink_id],
                            right_by_module[sink_id],
                            sig_id,
                            "bidirectional",
                        )

        module_ids_in_order = list(module_sheets.keys())
        columns_value = max(1, int(columns_value))
        base_row_gap = max(0.0, float(y_spacing) - float(sheet_height))

        sheet_uuids: Dict[str, str] = {}

        y_cursor = float(origin_y)
        for row_start in range(0, len(module_ids_in_order), columns_value):
            row_ids = module_ids_in_order[row_start : row_start + columns_value]
            if not row_ids:
                break

            row_heights: Dict[str, float] = {}
            for module_id in row_ids:
                left_count = len(left_by_module.get(module_id, {}))
                right_count = len(right_by_module.get(module_id, {}))
                row_heights[module_id] = _required_sheet_height_mm(left_count=left_count, right_count=right_count)

            row_height = max(row_heights.values()) if row_heights else float(sheet_height)

            for col_index, module_id in enumerate(row_ids):
                x = float(origin_x) + float(col_index) * float(x_spacing)
                y = y_cursor

                sheet_file_raw = module_sheets[module_id]
                sheet_file = str(sheet_file_raw)
                sheet_file_path = Path(sheet_file)
                if relative_sheet_paths and sheet_file_path.is_absolute():
                    sheet_file = Path(os.path.relpath(sheet_file, start=output_file.parent)).as_posix()
                else:
                    sheet_file = sheet_file_path.as_posix()

                sheet_node, sheet_uuid, extras = _create_sheet_node(
                    module_id=module_id,
                    sheet_file=sheet_file,
                    x=x,
                    y=y,
                    width=float(sheet_width),
                    height=row_heights[module_id],
                    left_pins=left_by_module.get(module_id, {}),
                    right_pins=right_by_module.get(module_id, {}),
                )

                tree.append(sheet_node)
                tree.extend(extras)
                sheet_uuids[module_id] = sheet_uuid

            y_cursor += row_height + base_row_gap

        rebuild_sheet_instances_at_end(tree, root_uuid, sheet_uuids)

        saved = SchematicManager.save_schematic(schematic, str(output_file))
        if not saved:
            raise RuntimeError(f"Failed to save schematic to {output_file}")

        result: Dict[str, Any] = {
            "success": True,
            "schematic_path": str(output_file),
            "modules_count": len(module_sheets),
        }

        if export_svg:
            svg_dir = output_file.parent / f"{output_file.stem}_svg"
            svg_dir.mkdir(parents=True, exist_ok=True)

            proc = subprocess.run(
                ["kicad-cli", "sch", "export", "svg", "--output", f"{svg_dir}/", str(output_file)],
                capture_output=True,
                text=True,
            )
            if proc.returncode != 0:
                raise RuntimeError(f"SVG export failed: {proc.stderr}")

            svg_paths = sorted([path.as_posix() for path in svg_dir.glob("*.svg")])
            result["svg_dir"] = str(svg_dir)
            result["svg_paths"] = svg_paths

            expected_top = (svg_dir / f"{output_file.stem}.svg").as_posix()
            if expected_top in svg_paths:
                result["svg_path"] = expected_top
            elif svg_paths:
                result["svg_path"] = svg_paths[0]

        return result
    except Exception as e:
        import traceback

        return {
            "success": False,
            "message": str(e),
            "traceback": traceback.format_exc(),
        }
