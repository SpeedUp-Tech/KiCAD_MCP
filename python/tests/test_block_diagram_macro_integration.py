from __future__ import annotations

import unittest
from pathlib import Path
from uuid import uuid4

from sexpdata import Symbol, loads

# Allow running this file directly with: python python/tests/test_block_diagram_macro_integration.py
# by ensuring the repo root is on sys.path so the 'python' package resolves.
import sys as _sys
from pathlib import Path as _Path
_PROJECT_ROOT = _Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_PROJECT_ROOT))

from python.commands.block_diagram_macro import generate_block_diagram


BLUEPRINT = Path("/root/workspace/KiCAD_MCP/test_cases/3A充电方案/blueprint.json")


def _load_tree(path: Path):
    return loads(path.read_text(encoding="utf-8"))


def _iter_nodes(tree):
    if isinstance(tree, list):
        yield tree
        for el in tree:
            yield from _iter_nodes(el)


class BlockDiagramMacroIntegrationTest(unittest.TestCase):
    def test_generate_and_export(self) -> None:
        # Arrange
        self.assertTrue(BLUEPRINT.exists(), "Fixture blueprint missing")
        project_dir = Path("exported") / f"block_macro_integration_{uuid4().hex}"

        # Act
        result = generate_block_diagram(str(BLUEPRINT), str(project_dir))

        # Assert files exist
        project_file = Path(result["project_file"])  # project.kicad_pro
        top_path = Path(result["top_schematic"])     # Top.kicad_sch
        self.assertTrue(project_file.exists())
        self.assertTrue(top_path.exists())

        # Module sheets
        module_sheets = result["module_sheets"]
        self.assertGreater(len(module_sheets), 0)
        for _, p in module_sheets.items():
            self.assertTrue(Path(p).exists())

        # Exported outputs must be present and non-empty (real kicad-cli export)
        outputs = result["outputs"]
        for key in ("pdf", "svg", "erc"):
            outp = Path(outputs[key])
            self.assertTrue(outp.exists(), f"Missing {key}")
            self.assertGreater(outp.stat().st_size, 0, f"{key} is empty")

        # Parse Top and validate structure: one hierarchical sheet per module
        top_tree = _load_tree(top_path)
        sheet_nodes = [
            n for n in _iter_nodes(top_tree)
            if isinstance(n, list)
            and n
            and n[0] == Symbol("sheet")
        ]
        self.assertEqual(len(sheet_nodes), len(module_sheets))

        # Build a pin position map from sheet pins (absolute 'at' coords)
        def _kv(node, key: str):
            for item in node:
                if isinstance(item, list) and item and item[0] == Symbol(key):
                    return item
            return None

        def _get_sheet_name(sheet_node):
            """Extract the sheet name from the 'Sheet name' property."""
            for item in sheet_node:
                if isinstance(item, list) and item and item[0] == Symbol("property"):
                    if len(item) > 2 and item[1] == "Sheet name":
                        return item[2]
            return None

        pin_pos: dict[tuple[str, str], tuple[float, float]] = {}
        for sh in sheet_nodes:
            modname = _get_sheet_name(sh)
            for item in sh:
                if isinstance(item, list) and item and item[0] == Symbol("pin"):
                    nm = item[1]  # Pin name is the second element
                    at = _kv(item, "at")
                    pin_pos[(modname, nm)] = (float(at[1]), float(at[2]))

        # Collect wires
        def _wire_points(n):
            pts = n[1]
            return (float(pts[1][1]), float(pts[1][2])), (float(pts[2][1]), float(pts[2][2]))

        wires = [n for n in _iter_nodes(top_tree) if isinstance(n, list) and n and n[0] == Symbol("wire")]

        # Generic connectivity check: verify that wires exist and connect to pins
        # Just check that we have some wires if there are multiple modules
        if len(sheet_nodes) > 1:
            self.assertGreater(len(wires), 0, "Expected wires connecting modules")

            # Verify that wires connect to actual pin positions
            wire_points = set()
            for w in wires:
                start, end = _wire_points(w)
                wire_points.add(start)
                wire_points.add(end)

            # Check that at least some wire endpoints match pin positions
            pin_positions = set(pin_pos.values())
            connected_pins = wire_points & pin_positions
            self.assertGreater(len(connected_pins), 0, "Wires should connect to sheet pins")


if __name__ == "__main__":
    unittest.main()
