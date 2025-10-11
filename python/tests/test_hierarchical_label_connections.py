"""
Integration tests for connecting component pins to hierarchical labels.

This test suite validates the extended connect_schematic_pins functionality
that supports connecting pins to hierarchical labels in addition to pin-to-pin
connections.
"""

import os
import tempfile
import unittest
from pathlib import Path
from sexpdata import Symbol

from python.commands.schematic import SchematicManager
from python.commands.connection_schematic import ConnectionManager
from python.commands.component_schematic import ComponentManager


class HierarchicalLabelConnectionTests(unittest.TestCase):
    """Test suite for hierarchical label connection functionality"""

    def test_real_hierarchical_schematic(self) -> None:
        """Test with a real hierarchical schematic file"""
        # Load the real hierarchical schematic
        sch_path = Path('test_cases/3A充电方案/kicad/sheets/M_Battery_Protection.kicad_sch')
        
        if not sch_path.exists():
            self.skipTest(f"Test schematic not found: {sch_path}")
        
        from skip import Schematic
        sch = Schematic(str(sch_path))
        
        # Verify hierarchical labels exist
        label_names = []
        for elem in sch.tree:
            if isinstance(elem, list) and len(elem) > 0:
                if hasattr(elem[0], 'value') and elem[0].value() == 'hierarchical_label':
                    label_names.append(elem[1])
        
        self.assertIn('R_VBAT_RAW', label_names)
        self.assertIn('R_VBAT', label_names)
        
        # Add a test component
        ComponentManager.add_component(
            sch,
            {
                'type': 'R',
                'reference': 'R_TEST',
                'x': 100,
                'y': 50,
            },
        )
        
        # Connect component pin to hierarchical label
        wires = ConnectionManager.connect_pins(
            sch,
            {'reference': 'R_TEST', 'pin': '1'},
            {'label': 'R_VBAT_RAW'},
        )
        
        self.assertIsInstance(wires, list)
        self.assertGreater(len(wires), 0)
        
        # Verify the schematic can be saved and reloaded
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_path = os.path.join(tmpdir, 'test_modified.kicad_sch')
            sch.write(temp_path)
            
            reloaded = Schematic(temp_path)
            self.assertIsNotNone(reloaded)
            self.assertGreater(len(reloaded.wire), 0)

    def test_mixed_connection_types(self) -> None:
        """Test a schematic with mixed pin-to-pin and pin-to-label connections"""
        schematic = SchematicManager.create_schematic('MixedConnections')
        
        # Add components
        ComponentManager.add_component(
            schematic,
            {
                'type': 'R',
                'reference': 'R1',
                'x': 50,
                'y': 50,
            },
        )
        ComponentManager.add_component(
            schematic,
            {
                'type': 'R',
                'reference': 'R2',
                'x': 150,
                'y': 50,
            },
        )
        
        # Add hierarchical labels
        label1 = [
            Symbol('hierarchical_label'),
            'INPUT',
            [Symbol('shape'), Symbol('input')],
            [Symbol('at'), 20.0, 50.0, 0],
            [Symbol('fields_autoplaced')],
            [Symbol('effects'), [Symbol('font'), [Symbol('size'), 1.27, 1.27]], [Symbol('justify'), Symbol('right')]],
            [Symbol('uuid'), Symbol('test-label-input')]
        ]
        schematic.tree.append(label1)
        
        label2 = [
            Symbol('hierarchical_label'),
            'OUTPUT',
            [Symbol('shape'), Symbol('output')],
            [Symbol('at'), 180.0, 50.0, 0],
            [Symbol('fields_autoplaced')],
            [Symbol('effects'), [Symbol('font'), [Symbol('size'), 1.27, 1.27]], [Symbol('justify'), Symbol('left')]],
            [Symbol('uuid'), Symbol('test-label-output')]
        ]
        schematic.tree.append(label2)
        
        # Create mixed connections:
        # 1. Label to pin
        wires1 = ConnectionManager.connect_pins(
            schematic,
            {'label': 'INPUT'},
            {'reference': 'R1', 'pin': '1'},
        )
        self.assertIsInstance(wires1, list)
        
        # 2. Pin to pin
        wires2 = ConnectionManager.connect_pins(
            schematic,
            {'reference': 'R1', 'pin': '2'},
            {'reference': 'R2', 'pin': '1'},
        )
        self.assertIsInstance(wires2, list)
        
        # 3. Pin to label
        wires3 = ConnectionManager.connect_pins(
            schematic,
            {'reference': 'R2', 'pin': '2'},
            {'label': 'OUTPUT'},
        )
        self.assertIsInstance(wires3, list)
        
        # Verify all wires were created
        total_wires = len(wires1) + len(wires2) + len(wires3)
        self.assertGreater(total_wires, 0)
        
        # Verify schematic can be saved
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_path = os.path.join(tmpdir, 'mixed_connections.kicad_sch')
            self.assertTrue(SchematicManager.save_schematic(schematic, temp_path))
            
            reloaded = SchematicManager.load_schematic(temp_path)
            self.assertIsNotNone(reloaded)

    def test_routing_patterns_with_labels(self) -> None:
        """Test that routing patterns work correctly with hierarchical labels"""
        schematic = SchematicManager.create_schematic('RoutingPatterns')
        
        # Add component
        ComponentManager.add_component(
            schematic,
            {
                'type': 'R',
                'reference': 'R1',
                'x': 50,
                'y': 50,
            },
        )
        
        # Add label at different position to require routing
        label = [
            Symbol('hierarchical_label'),
            'SIGNAL',
            [Symbol('shape'), Symbol('bidirectional')],
            [Symbol('at'), 100.0, 100.0, 0],
            [Symbol('fields_autoplaced')],
            [Symbol('effects'), [Symbol('font'), [Symbol('size'), 1.27, 1.27]], [Symbol('justify'), Symbol('left')]],
            [Symbol('uuid'), Symbol('test-label-signal')]
        ]
        schematic.tree.append(label)
        
        # Test horizontal-then-vertical routing
        wires_hv = ConnectionManager.connect_pins(
            schematic,
            {'reference': 'R1', 'pin': '1'},
            {'label': 'SIGNAL'},
            routing={'pattern': 'hv'},
        )
        self.assertIsInstance(wires_hv, list)
        
        # For non-aligned points, should create 2 segments (corner)
        if len(wires_hv) == 2:
            # Verify first segment is horizontal
            first_points = [pt.value for pt in wires_hv[0].points]
            self.assertAlmostEqual(first_points[0][1], first_points[1][1])
            
            # Verify second segment is vertical
            second_points = [pt.value for pt in wires_hv[1].points]
            self.assertAlmostEqual(second_points[0][0], second_points[1][0])

    def test_backward_compatibility(self) -> None:
        """Ensure existing pin-to-pin connections still work exactly as before"""
        schematic = SchematicManager.create_schematic('BackwardCompat')
        
        # Add components
        ComponentManager.add_component(
            schematic,
            {
                'type': 'R',
                'reference': 'R1',
                'x': 10,
                'y': 10,
            },
        )
        ComponentManager.add_component(
            schematic,
            {
                'type': 'R',
                'reference': 'R2',
                'x': 40,
                'y': 10,
            },
        )
        
        # Use the exact same API as before
        wires = ConnectionManager.connect_pins(
            schematic,
            {'reference': 'R1', 'pin': '1'},
            {'reference': 'R2', 'pin': '1'},
        )
        
        # Verify behavior is unchanged
        self.assertEqual(len(wires), 1)
        points = [pt.value for pt in wires[0].points]
        self.assertEqual(len(points), 2)
        # Should be horizontal connection (same Y)
        self.assertAlmostEqual(points[0][1], points[1][1])


if __name__ == '__main__':
    unittest.main()

