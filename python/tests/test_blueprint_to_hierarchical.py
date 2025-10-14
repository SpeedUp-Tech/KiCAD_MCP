#!/usr/bin/env python3
"""
Unit tests for blueprint_to_hierarchical.py

Tests the hierarchical schematic generator with multiple blueprints
and verifies correct structure, element ordering, and SVG rendering.
"""

import json
import subprocess
import sys
import unittest
from pathlib import Path
from uuid import uuid4

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from python.commands.blueprint_to_hierarchical import generate_hierarchical_schematic
from python.commands.schematic import SchematicManager
from sexpdata import loads, Symbol


class TestBlueprintToHierarchical(unittest.TestCase):
    """Test suite for blueprint_to_hierarchical generator"""
    
    @classmethod
    def setUpClass(cls):
        """Set up test fixtures"""
        cls.test_output_dir = Path("exported/test_blueprint_to_hierarchical")
        cls.test_output_dir.mkdir(parents=True, exist_ok=True)
        
        cls.test_cases = [
            {
                "name": "3A充电方案",
                "blueprint": "test_cases/3A充电方案/blueprint.json",
                "expected_modules": 6,
            },
            {
                "name": "140w笔记本充电",
                "blueprint": "test_cases/140w笔记本充电/blueprint.json",
                "expected_modules": 16,
            }
        ]
    
    def test_01_generate_from_blueprint_3a(self):
        """Test generation from 3A充电方案 blueprint"""
        test_case = self.test_cases[0]
        output_dir = self.test_output_dir / f"{test_case['name']}_{uuid4().hex[:8]}"
        
        result = generate_hierarchical_schematic(
            test_case["blueprint"],
            str(output_dir)
        )
        
        # Verify result structure
        self.assertIn("top_schematic", result)
        self.assertIn("module_sheets", result)
        self.assertIn("output_dir", result)
        
        # Verify files exist
        self.assertTrue(Path(result["top_schematic"]).exists())
        with open(test_case["blueprint"], 'r', encoding='utf-8') as f:
            expected_modules = len(json.load(f).get("modules", []))
        self.assertEqual(len(result["module_sheets"]), expected_modules)
        
        for module_path in result["module_sheets"].values():
            self.assertTrue(Path(module_path).exists())
    
    def test_02_generate_from_blueprint_140w(self):
        """Test generation from 140w笔记本充电 blueprint"""
        test_case = self.test_cases[1]
        output_dir = self.test_output_dir / f"{test_case['name']}_{uuid4().hex[:8]}"
        
        result = generate_hierarchical_schematic(
            test_case["blueprint"],
            str(output_dir)
        )
        
        # Verify result structure
        self.assertIn("top_schematic", result)
        self.assertIn("module_sheets", result)
        self.assertIn("output_dir", result)
        
        # Verify module count
        with open(test_case["blueprint"], 'r', encoding='utf-8') as f:
            expected_modules = len(json.load(f).get("modules", []))
        self.assertEqual(len(result["module_sheets"]), expected_modules)
    
    def test_03_element_ordering(self):
        """Test that sheet_instances is at the END of the tree"""
        test_case = self.test_cases[0]
        output_dir = self.test_output_dir / f"element_order_{uuid4().hex[:8]}"
        
        result = generate_hierarchical_schematic(
            test_case["blueprint"],
            str(output_dir)
        )
        
        # Load top schematic
        top_tree = loads(Path(result["top_schematic"]).read_text(encoding="utf-8"))
        
        # Get element order
        element_order = []
        for entry in top_tree:
            if isinstance(entry, list) and entry:
                element_order.append(str(entry[0]))
        
        # Find positions
        sheet_instances_pos = element_order.index("sheet_instances")
        last_sheet_pos = len(element_order) - 1 - element_order[::-1].index("sheet")
        
        # sheet_instances MUST be after all sheets
        self.assertGreater(
            sheet_instances_pos,
            last_sheet_pos,
            f"sheet_instances at {sheet_instances_pos} must be after last sheet at {last_sheet_pos}"
        )
    
    def test_04_path_format(self):
        """Test that sheet paths use correct format: /root_uuid/sheet_uuid"""
        test_case = self.test_cases[0]
        output_dir = self.test_output_dir / f"path_format_{uuid4().hex[:8]}"
        
        result = generate_hierarchical_schematic(
            test_case["blueprint"],
            str(output_dir)
        )
        
        # Load top schematic
        top_tree = loads(Path(result["top_schematic"]).read_text(encoding="utf-8"))
        
        # Get root UUID
        root_uuid = None
        for entry in top_tree:
            if isinstance(entry, list) and entry and entry[0] == Symbol("uuid"):
                root_uuid = str(entry[1])
                break
        
        self.assertIsNotNone(root_uuid, "Root UUID not found")
        
        # Get sheet_instances
        sheet_instances = None
        for entry in top_tree:
            if isinstance(entry, list) and entry and entry[0] == Symbol("sheet_instances"):
                sheet_instances = entry
                break
        
        self.assertIsNotNone(sheet_instances, "sheet_instances not found")
        
        # Verify path format
        for path_entry in sheet_instances[1:]:
            if isinstance(path_entry, list) and path_entry[0] == Symbol("path"):
                path_str = str(path_entry[1])
                
                # Root path should be "/"
                if path_str == "/":
                    continue
                
                # Other paths should be /root_uuid/sheet_uuid
                self.assertTrue(
                    path_str.startswith(f"/{root_uuid}/"),
                    f"Path {path_str} should start with /{root_uuid}/"
                )
    
    def test_05_hierarchical_labels(self):
        """Test that module schematics use hierarchical_label (not global_label)"""
        test_case = self.test_cases[0]
        output_dir = self.test_output_dir / f"labels_{uuid4().hex[:8]}"
        
        result = generate_hierarchical_schematic(
            test_case["blueprint"],
            str(output_dir)
        )
        
        total_hierarchical = 0
        total_global = 0
        
        for module_path in result["module_sheets"].values():
            module_tree = loads(Path(module_path).read_text(encoding="utf-8"))
            
            h_count = sum(1 for e in module_tree if isinstance(e, list) and e and e[0] == Symbol("hierarchical_label"))
            g_count = sum(1 for e in module_tree if isinstance(e, list) and e and e[0] == Symbol("global_label"))
            
            total_hierarchical += h_count
            total_global += g_count
        
        # Should have hierarchical labels
        self.assertGreater(total_hierarchical, 0, "Should have hierarchical labels")
        
        # Should NOT have global labels
        self.assertEqual(total_global, 0, "Should not have global labels")
    
    def test_06_child_sheets_no_sheet_instances(self):
        """Test that child sheets do not have sheet_instances section"""
        test_case = self.test_cases[0]
        output_dir = self.test_output_dir / f"child_sheets_{uuid4().hex[:8]}"
        
        result = generate_hierarchical_schematic(
            test_case["blueprint"],
            str(output_dir)
        )
        
        for module_id, module_path in result["module_sheets"].items():
            module_tree = loads(Path(module_path).read_text(encoding="utf-8"))
            
            # Check for sheet_instances
            has_sheet_instances = any(
                isinstance(e, list) and e and e[0] == Symbol("sheet_instances")
                for e in module_tree
            )
            
            self.assertFalse(
                has_sheet_instances,
                f"Child sheet {module_id} should not have sheet_instances"
            )
    
    def test_07_svg_export(self):
        """Test that SVG export works and contains hierarchical labels"""
        test_case = self.test_cases[0]
        output_dir = self.test_output_dir / f"svg_export_{uuid4().hex[:8]}"
        
        result = generate_hierarchical_schematic(
            test_case["blueprint"],
            str(output_dir)
        )
        
        # Export SVG
        svg_dir = Path(result["output_dir"]) / "out"
        cmd = [
            "kicad-cli", "sch", "export", "svg",
            "--output", f"{svg_dir}/",
            result["top_schematic"]
        ]
        
        proc = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, f"SVG export failed: {proc.stderr}")
        
        # Check that SVG files were created
        svg_files = list(svg_dir.glob("Top-*.svg"))
        self.assertGreater(len(svg_files), 0, "No SVG files generated")
        
        # Collect expected label names dynamically from top schematic sheet pins
        top_tree = loads(Path(result["top_schematic"]).read_text(encoding="utf-8"))
        expected_label_names = set()
        for entry in top_tree:
            if isinstance(entry, list) and entry and entry[0] == Symbol("sheet"):
                for item in entry:
                    if isinstance(item, list) and item and item[0] == Symbol("pin"):
                        expected_label_names.add(str(item[1]))

        self.assertGreater(len(expected_label_names), 0, "No sheet pins/labels found in top schematic")

        # Check that at least one SVG contains one of the discovered label texts
        found_label = False
        for svg_file in svg_files:
            content = svg_file.read_text()
            if any(label in content for label in expected_label_names):
                found_label = True
                break

        self.assertTrue(found_label, "No hierarchical labels found in SVG exports")
    
    def test_08_sheet_symbols_have_pins(self):
        """Test that sheet symbols in top schematic have pins"""
        test_case = self.test_cases[0]
        output_dir = self.test_output_dir / f"sheet_pins_{uuid4().hex[:8]}"
        
        result = generate_hierarchical_schematic(
            test_case["blueprint"],
            str(output_dir)
        )
        
        # Load top schematic
        top_tree = loads(Path(result["top_schematic"]).read_text(encoding="utf-8"))
        
        # Find sheet symbols
        sheet_count = 0
        sheets_with_pins = 0
        
        for entry in top_tree:
            if isinstance(entry, list) and entry and entry[0] == Symbol("sheet"):
                sheet_count += 1
                
                # Check if sheet has pins
                has_pins = any(
                    isinstance(item, list) and item and item[0] == Symbol("pin")
                    for item in entry
                )
                
                if has_pins:
                    sheets_with_pins += 1
        
        self.assertGreater(sheet_count, 0, "No sheet symbols found")
        self.assertEqual(
            sheets_with_pins,
            sheet_count,
            "Not all sheet symbols have pins"
        )
    
    def test_09_module_metadata_in_title_block(self):
        """Test that module metadata is stored in title_block (not as text annotations)"""
        test_case = self.test_cases[0]
        output_dir = self.test_output_dir / f"metadata_{uuid4().hex[:8]}"

        result = generate_hierarchical_schematic(
            test_case["blueprint"],
            str(output_dir)
        )

        # Load blueprint to get expected metadata
        with open(test_case["blueprint"], 'r', encoding='utf-8') as f:
            blueprint = json.load(f)

        # Check first module
        module = blueprint["modules"][0]
        module_path = result["module_sheets"][module["module_id"]]
        module_tree = loads(Path(module_path).read_text(encoding="utf-8"))

        # Verify NO text annotations in working area
        text_elements = [
            e for e in module_tree
            if isinstance(e, list) and e and e[0] == Symbol("text")
        ]

        self.assertEqual(
            len(text_elements),
            0,
            "Module schematics should not have text annotations in working area"
        )

        # Verify title_block has the module title
        title_block = next(
            (e for e in module_tree if isinstance(e, list) and e and e[0] == Symbol("title_block")),
            None
        )

        self.assertIsNotNone(title_block, "title_block not found")

        # Check that title contains module_id
        title_entry = next(
            (e for e in title_block if isinstance(e, list) and e and e[0] == Symbol("title")),
            None
        )

        if title_entry and len(title_entry) > 1:
            self.assertEqual(
                str(title_entry[1]),
                module["module_id"],
                f"Title should be module_id: {module['module_id']}"
            )


def suite():
    """Create test suite"""
    suite = unittest.TestSuite()
    suite.addTest(unittest.makeSuite(TestBlueprintToHierarchical))
    return suite


if __name__ == '__main__':
    unittest.main(verbosity=2)

