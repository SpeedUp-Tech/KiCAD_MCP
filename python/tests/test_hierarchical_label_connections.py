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
from python.commands.grid_utils import snap_to_grid

EXPORT_DIR = Path(__file__).resolve().parents[2] / 'exported'
EXPORT_DIR.mkdir(parents=True, exist_ok=True)


class HierarchicalLabelConnectionTests(unittest.TestCase):
    """Test suite for hierarchical label connection functionality"""

    def test_real_hierarchical_schematic(self) -> None:
        """Test with a real hierarchical schematic file"""
        # Load the real hierarchical schematic
        sch_path = Path('test_cases/3A充电方案/kicad/sheets/Battery_Protection.kicad_sch')

        if not sch_path.exists():
            self.skipTest(f"Test schematic not found: {sch_path}")

        from skip import Schematic
        sch = Schematic(str(sch_path))

        # Collect hierarchical labels present in the schematic
        label_names = [
            elem[1]
            for elem in sch.tree
            if isinstance(elem, list)
            and len(elem) > 1
            and hasattr(elem[0], 'value')
            and elem[0].value() == 'hierarchical_label'
        ]

        if not label_names:
            fallback_label = [
                Symbol('global_label'),
                'TEST_GLOBAL_LABEL',
                [Symbol('shape'), Symbol('bidirectional')],
                [Symbol('at'), 50.0, 50.0, 0],
            ]
            sch.tree.append(fallback_label)
            label_names = ['TEST_GLOBAL_LABEL']

        self.assertTrue(all(isinstance(name, str) and name for name in label_names))

        target_label = label_names[0]

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
            {'label': target_label},
        )

        self.assertIsInstance(result, dict)
        self.assertIn('created', result)
        self.assertIn('R_TEST.1', result['created']['summary'])
        self.assertIn(target_label, result['created']['summary'])

        # Verify the schematic can be saved and reloaded
        with tempfile.TemporaryDirectory(dir=str(EXPORT_DIR)) as tmpdir:
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
            [Symbol('at'), snap_to_grid(20.0), snap_to_grid(50.0), 0],
            [Symbol('fields_autoplaced')],
            [Symbol('effects'), [Symbol('font'), [Symbol('size'), 1.27, 1.27]], [Symbol('justify'), Symbol('right')]],
            [Symbol('uuid'), Symbol('test-label-input')]
        ]
        schematic.tree.append(label1)

        label2 = [
            Symbol('hierarchical_label'),
            'OUTPUT',
            [Symbol('shape'), Symbol('output')],
            [Symbol('at'), snap_to_grid(180.0), snap_to_grid(50.0), 0],
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
        self.assertIn('INPUT', result1['created']['summary'])
        self.assertIn('R1.1', result1['created']['summary'])
        self.assertIn('R1.2', result2['created']['summary'])
        self.assertIn('R2.1', result2['created']['summary'])
        self.assertIn('R2.2', result3['created']['summary'])
        self.assertIn('OUTPUT', result3['created']['summary'])

        # Verify schematic can be saved
        with tempfile.TemporaryDirectory(dir=str(EXPORT_DIR)) as tmpdir:
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
            [Symbol('at'), snap_to_grid(100.0), snap_to_grid(100.0), 0],
            [Symbol('fields_autoplaced')],
            [Symbol('effects'), [Symbol('font'), [Symbol('size'), 1.27, 1.27]], [Symbol('justify'), Symbol('left')]],
            [Symbol('uuid'), Symbol('test-label-signal')]
        ]
        schematic.tree.append(label)

        # Test routing
        result_hv = ConnectionManager.connect_pins(
            schematic,
            {'reference': 'R1', 'pin': '1'},
            {'label': 'SIGNAL'},
        )
        self.assertIsInstance(result_hv, dict)
        self.assertIn('created', result_hv)
        self.assertIn('R1.1', result_hv['created']['summary'])
        self.assertIn('SIGNAL', result_hv['created']['summary'])
        self.assertEqual(result_hv['net'], 'SIGNAL')
        self.assertEqual(result_hv['created']['net'], 'SIGNAL')
        self.assertIn('netConnections', result_hv)
        self.assertIn('SIGNAL', result_hv['netConnections'])
        self.assertTrue(result_hv['netConnections'][0] == 'SIGNAL')

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
        self.assertIn('R1.1', result['created']['summary'])
        self.assertIn('R2.1', result['created']['summary'])
        self.assertEqual(result['net'], 'Net-(R1-Pad1)')
        self.assertEqual(result['created']['net'], 'Net-(R1-Pad1)')
        self.assertIn('R1.1(passive)', result['netConnections'])
        self.assertIn('R2.1(passive)', result['netConnections'])


if __name__ == '__main__':
    unittest.main()
