#!/usr/bin/env python3
"""
Unit tests for generate_top_schematic_from_contract.
"""

import sys
import unittest
from pathlib import Path
from uuid import uuid4

from sexpdata import Symbol, loads

# Add repo root to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from python.commands.kicad_schematics.grid_utils import snap_to_grid
from python.commands.kicad_schematics.schematic import SchematicManager
from python.top_schematic_from_contract import generate_top_schematic_from_contract


class TestTopSchematicFromContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.test_output_root = Path("exported/test_top_schematic_from_contract")
        cls.test_output_root.mkdir(parents=True, exist_ok=True)

    def test_generate_top_schematic_and_svg(self) -> None:
        run_dir = self.test_output_root / f"run_{uuid4().hex[:8]}"
        modules_dir = run_dir / "modules"
        modules_dir.mkdir(parents=True, exist_ok=True)

        module_ids = ["MOD_A", "MOD_B"]
        for module_id in module_ids:
            sch = SchematicManager.create_schematic(module_id, metadata={"title": module_id})
            SchematicManager.save_schematic(sch, str(modules_dir / f"{module_id}.kicad_sch"))

        module_sheets = {module_id: f"modules/{module_id}.kicad_sch" for module_id in module_ids}

        signals = [
            {
                "signal_id": "SCL",
                "source": "MOD_A",
                "sinks": ["MOD_B"],
                "direction": "bidirectional",
            },
            {
                "signal_id": "EN",
                "source": "MOD_A",
                "sinks": ["MOD_B"],
                "direction": "source->sink",
            },
        ]

        rails = [
            {
                "rail_id": "VCC",
                "primary_source_kind": "module",
                "primary_source_ref": "MOD_A",
                "consumers": ["MOD_B"],
            },
        ]

        top_path = run_dir / "top.kicad_sch"
        result = generate_top_schematic_from_contract(
            module_sheets=module_sheets,
            signals=signals,
            rails=rails,
            output_path=str(top_path),
            export_svg=True,
        )

        self.assertTrue(result.get("success"), msg=result.get("message"))
        self.assertTrue(top_path.exists())

        tree = loads(top_path.read_text(encoding="utf-8"))

        sheet_nodes = [
            entry for entry in tree if isinstance(entry, list) and entry and entry[0] == Symbol("sheet")
        ]
        self.assertEqual(len(sheet_nodes), 2)

        sheets_by_id = {}
        for sheet in sheet_nodes:
            sheet_name_prop = next(
                (
                    item
                    for item in sheet
                    if isinstance(item, list)
                    and item
                    and item[0] == Symbol("property")
                    and item[1] == "Sheet name"
                ),
                None,
            )
            self.assertIsNotNone(sheet_name_prop)
            assert sheet_name_prop is not None
            sheets_by_id[str(sheet_name_prop[2])] = sheet

        for module_id in module_ids:
            self.assertIn(module_id, sheets_by_id)
            sheet = sheets_by_id[module_id]

            sheet_file_prop = next(
                (
                    item
                    for item in sheet
                    if isinstance(item, list)
                    and item
                    and item[0] == Symbol("property")
                    and item[1] == "Sheet file"
                ),
                None,
            )
            self.assertIsNotNone(sheet_file_prop)
            assert sheet_file_prop is not None
            self.assertEqual(str(sheet_file_prop[2]), module_sheets[module_id])

        def _get_sheet_origin_and_size(sheet: list) -> tuple[float, float, float, float]:
            origin = next((e for e in sheet if isinstance(e, list) and e and e[0] == Symbol("at")), None)
            size = next((e for e in sheet if isinstance(e, list) and e and e[0] == Symbol("size")), None)
            assert origin is not None and size is not None
            return float(origin[1]), float(origin[2]), float(size[1]), float(size[2])

        def _pins(sheet: list) -> dict[str, dict[str, float | int]]:
            pins = {}
            for entry in sheet:
                if not (isinstance(entry, list) and entry and entry[0] == Symbol("pin")):
                    continue
                name = str(entry[1])
                direction = str(entry[2])
                at_node = next((e for e in entry if isinstance(e, list) and e and e[0] == Symbol("at")), None)
                assert at_node is not None
                pins[name] = {
                    "direction": direction,
                    "x": float(at_node[1]),
                    "y": float(at_node[2]),
                    "orientation": int(at_node[3]),
                }
            return pins

        pins_a = _pins(sheets_by_id["MOD_A"])
        pins_b = _pins(sheets_by_id["MOD_B"])

        self.assertEqual(set(pins_a.keys()), {"SCL", "EN", "VCC"})
        self.assertEqual(set(pins_b.keys()), {"SCL", "EN", "VCC"})

        self.assertEqual(str(pins_a["EN"]["direction"]), "output")
        self.assertEqual(str(pins_b["EN"]["direction"]), "input")
        self.assertEqual(str(pins_a["SCL"]["direction"]), "bidirectional")
        self.assertEqual(str(pins_b["SCL"]["direction"]), "bidirectional")
        self.assertEqual(str(pins_a["VCC"]["direction"]), "bidirectional")
        self.assertEqual(str(pins_b["VCC"]["direction"]), "bidirectional")

        self.assertTrue(all(int(pins_a[name]["orientation"]) == 0 for name in pins_a))
        self.assertTrue(all(int(pins_b[name]["orientation"]) == 180 for name in pins_b))

        label_nodes = [entry for entry in tree if isinstance(entry, list) and entry and entry[0] == Symbol("label")]
        wire_nodes = [entry for entry in tree if isinstance(entry, list) and entry and entry[0] == Symbol("wire")]

        self.assertEqual(len(label_nodes), 6)
        self.assertEqual(len(wire_nodes), 6)

        label_positions: dict[tuple[float, float], str] = {}
        for node in label_nodes:
            name = str(node[1])
            at_node = next((e for e in node if isinstance(e, list) and e and e[0] == Symbol("at")), None)
            assert at_node is not None
            label_positions[(round(float(at_node[1]), 6), round(float(at_node[2]), 6))] = name

        self.assertEqual(set(label_positions.values()), {"SCL", "EN", "VCC"})

        pin_connection_positions: dict[str, set[tuple[float, float]]] = {"SCL": set(), "EN": set(), "VCC": set()}
        for sheet in sheet_nodes:
            origin_x, origin_y, width, height = _get_sheet_origin_and_size(sheet)
            for entry in sheet:
                if not (isinstance(entry, list) and entry and entry[0] == Symbol("pin")):
                    continue
                name = str(entry[1])
                at_node = next((e for e in entry if isinstance(e, list) and e and e[0] == Symbol("at")), None)
                assert at_node is not None
                pin_y = float(at_node[2])
                orientation = int(at_node[3])
                if orientation == 0:
                    pos = (round(origin_x + width, 6), round(pin_y, 6))
                elif orientation == 180:
                    pos = (round(origin_x, 6), round(pin_y, 6))
                else:
                    continue
                pin_connection_positions[name].add(pos)

        for node in wire_nodes:
            pts_node = next((e for e in node if isinstance(e, list) and e and e[0] == Symbol("pts")), None)
            assert pts_node is not None
            points = []
            for xy in pts_node[1:]:
                if isinstance(xy, list) and len(xy) >= 3 and xy[0] == Symbol("xy"):
                    points.append((round(float(xy[1]), 6), round(float(xy[2]), 6)))
            self.assertEqual(len(points), 2)

            start, end = points
            if start in label_positions:
                label_pos = start
                pin_pos = end
            elif end in label_positions:
                label_pos = end
                pin_pos = start
            else:
                self.fail("Wire stub does not terminate at any label position")

            net_name = label_positions[label_pos]
            self.assertIn(pin_pos, pin_connection_positions[net_name])

        self.assertEqual(str(Path(result["svg_dir"])), str(run_dir / "top_svg"))
        svg_paths = [Path(p) for p in result.get("svg_paths", [])]
        self.assertGreater(len(svg_paths), 0)
        self.assertTrue(any(path.name == "top.svg" for path in svg_paths))
        self.assertTrue(Path(result["svg_path"]).exists())

    def test_adaptive_sheet_height_avoids_pins_outside(self) -> None:
        run_dir = self.test_output_root / f"run_{uuid4().hex[:8]}"
        modules_dir = run_dir / "modules"
        modules_dir.mkdir(parents=True, exist_ok=True)

        module_id = "MOD_A"
        sch = SchematicManager.create_schematic(module_id, metadata={"title": module_id})
        SchematicManager.save_schematic(sch, str(modules_dir / f"{module_id}.kicad_sch"))

        module_sheets = {module_id: f"modules/{module_id}.kicad_sch"}

        signals = []
        for idx in range(20):
            signals.append(
                {
                    "signal_id": f"OUT_{idx}",
                    "source": module_id,
                    "sinks": [],
                    "direction": "source->sink",
                }
            )
        for idx in range(10):
            signals.append(
                {
                    "signal_id": f"IN_{idx}",
                    "source": "EXTERNAL",
                    "sinks": [module_id],
                    "direction": "source->sink",
                }
            )

        top_path = run_dir / "top.kicad_sch"
        result = generate_top_schematic_from_contract(
            module_sheets=module_sheets,
            signals=signals,
            rails=[],
            output_path=str(top_path),
            export_svg=False,
        )

        self.assertTrue(result.get("success"), msg=result.get("message"))
        tree = loads(top_path.read_text(encoding="utf-8"))

        sheet = next(
            (
                entry
                for entry in tree
                if isinstance(entry, list) and entry and entry[0] == Symbol("sheet")
            ),
            None,
        )
        self.assertIsNotNone(sheet)
        assert sheet is not None

        origin = next((e for e in sheet if isinstance(e, list) and e and e[0] == Symbol("at")), None)
        size = next((e for e in sheet if isinstance(e, list) and e and e[0] == Symbol("size")), None)
        self.assertIsNotNone(origin)
        self.assertIsNotNone(size)
        assert origin is not None and size is not None

        origin_y = float(origin[2])
        height = float(size[2])

        # Contract produces 20 right-side pins and 10 left-side pins.
        expected_min_height = snap_to_grid(10.0 + 10.0 + (20 - 1) * 5.0)
        self.assertGreaterEqual(height, expected_min_height)

        pin_ys = []
        for entry in sheet:
            if not (isinstance(entry, list) and entry and entry[0] == Symbol("pin")):
                continue
            at_node = next((e for e in entry if isinstance(e, list) and e and e[0] == Symbol("at")), None)
            assert at_node is not None
            pin_ys.append(float(at_node[2]))
        self.assertGreater(len(pin_ys), 0)
        self.assertLessEqual(max(pin_ys), origin_y + height - snap_to_grid(10.0))


if __name__ == "__main__":
    unittest.main(verbosity=2)
