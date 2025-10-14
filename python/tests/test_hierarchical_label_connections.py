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
        result = ConnectionManager.connect_pins(
            sch,
            {'reference': 'R_TEST', 'pin': '1'},
            {'label': 'R_VBAT_RAW'},
        )

        self.assertIsInstance(result, dict)
        self.assertIn('created', result)
        self.assertIn('R_TEST.1', result['created'])
        self.assertIn('R_VBAT_RAW', result['created'])
        
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
        result1 = ConnectionManager.connect_pins(
            schematic,
            {'label': 'INPUT'},
            {'reference': 'R1', 'pin': '1'},
        )
        self.assertIsInstance(result1, dict)
        self.assertIn('created', result1)

        # 2. Pin to pin
        result2 = ConnectionManager.connect_pins(
            schematic,
            {'reference': 'R1', 'pin': '2'},
            {'reference': 'R2', 'pin': '1'},
        )
        self.assertIsInstance(result2, dict)
        self.assertIn('created', result2)

        # 3. Pin to label
        result3 = ConnectionManager.connect_pins(
            schematic,
            {'reference': 'R2', 'pin': '2'},
            {'label': 'OUTPUT'},
        )
        self.assertIsInstance(result3, dict)
        self.assertIn('created', result3)

        # Verify all connections were created
        self.assertIn('INPUT', result1['created'])
        self.assertIn('R1.1', result1['created'])
        self.assertIn('R1.2', result2['created'])
        self.assertIn('R2.1', result2['created'])
        self.assertIn('R2.2', result3['created'])
        self.assertIn('OUTPUT', result3['created'])
        
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
        result_hv = ConnectionManager.connect_pins(
            schematic,
            {'reference': 'R1', 'pin': '1'},
            {'label': 'SIGNAL'},
            routing={'pattern': 'hv'},
        )
        self.assertIsInstance(result_hv, dict)
        self.assertIn('created', result_hv)
        self.assertIn('R1.1', result_hv['created'])
        self.assertIn('SIGNAL', result_hv['created'])

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
        result = ConnectionManager.connect_pins(
            schematic,
            {'reference': 'R1', 'pin': '1'},
            {'reference': 'R2', 'pin': '1'},
        )

        # Verify new return format
        self.assertIsInstance(result, dict)
        self.assertIn('created', result)
        self.assertIn('net', result)
        self.assertIn('netConnections', result)
        self.assertIn('R1.1', result['created'])
        self.assertIn('R2.1', result['created'])


if __name__ == '__main__':
    unittest.main()

