import math
import os
import tempfile
import unittest
from pathlib import Path
from typing import List, cast

from sexpdata import Symbol
from skip.eeschema.wire import WireWrapper

EXPORT_DIR = Path(__file__).resolve().parents[2] / 'exported'
EXPORT_DIR.mkdir(parents=True, exist_ok=True)


from python.commands.kicad_schematics.schematic import SchematicManager
from python.commands.kicad_schematics.connection_schematic import ConnectionManager
from python.commands.kicad_schematics.component_schematic import ComponentManager
from python.commands.kicad_schematics.grid_utils import snap_to_grid


class ConnectionManagerTests(unittest.TestCase):
    def test_add_wire_preserves_endpoints_and_nonzero_length(self) -> None:
        schematic = SchematicManager.create_schematic('WireBasic')
        start = [0.0, 0.0]
        end = [12.5, 3.0]
        wire = ConnectionManager.add_wire(
            schematic,
            start,
            end,
            {
                'width': 0.5,
                'strokeType': 'dash',
            },
        )
        self.assertIsInstance(wire, WireWrapper)
        wire = cast(WireWrapper, wire)

        points = [pt.value for pt in wire.points]
        self.assertEqual(len(points), 2, "Expected a single straight segment")
        self.assertTrue(math.isclose(points[0][0], start[0]))
        self.assertTrue(math.isclose(points[0][1], start[1]))
        self.assertTrue(math.isclose(points[-1][0], end[0]))
        self.assertTrue(math.isclose(points[-1][1], end[1]))
        self.assertGreater(math.hypot(points[-1][0] - points[0][0], points[-1][1] - points[0][1]), 0.0)
        self.assertAlmostEqual(wire.stroke.width.value, 0.5)
        self.assertEqual(wire.stroke.type.value, 'dash')
        self.assertIsInstance(wire.uuid.value, str)

    def test_add_wire_with_intermediate_points_creates_contiguous_segments(self) -> None:
        schematic = SchematicManager.create_schematic('WirePoints')
        wires = ConnectionManager.add_wire(
            schematic,
            None,
            None,
            {
                'points': [[0, 0], [5, 0], [5, 5]],
            },
        )
        self.assertIsInstance(wires, list)
        wires = cast(List[WireWrapper], wires)
        self.assertEqual(len(wires), 2)

        first_segment = [pt.value for pt in wires[0].points]
        second_segment = [pt.value for pt in wires[1].points]

        # Segments should trace the provided path exactly and remain contiguous
        self.assertEqual(first_segment, [[0.0, 0.0], [5.0, 0.0]])
        self.assertEqual(second_segment, [[5.0, 0.0], [5.0, 5.0]])
        self.assertEqual(first_segment[-1], second_segment[0])
        self.assertGreater(math.hypot(first_segment[1][0] - first_segment[0][0], first_segment[1][1] - first_segment[0][1]), 0.0)
        self.assertGreater(math.hypot(second_segment[1][0] - second_segment[0][0], second_segment[1][1] - second_segment[0][1]), 0.0)

    def test_add_wire_rejects_segments_below_threshold(self) -> None:
        schematic = SchematicManager.create_schematic('WireDegenerate')
        with self.assertRaises(ValueError):
            ConnectionManager.add_wire(
                schematic,
                None,
                None,
                {
                    'points': [[0, 0], [0.2, 0], [0.2, 5.0]],
                },
            )

    def test_add_wire_rejects_invalid_inputs(self) -> None:
        schematic = SchematicManager.create_schematic('WireInvalid')
        with self.assertRaises(ValueError):
            ConnectionManager.add_wire(schematic, [1, 1], [1, 1])
        with self.assertRaises(ValueError):
            ConnectionManager.add_wire(schematic, None, None, {'points': [[0, 0]]})
        with self.assertRaises(ValueError):
            ConnectionManager.add_wire(schematic, [0, 0], [1, 1], {'unknown': 'value'})

    def test_add_wire_round_trip_save(self) -> None:
        schematic = SchematicManager.create_schematic('WireRoundTrip')
        ConnectionManager.add_wire(
            schematic,
            [0, 0],
            [10, 0],
            {
                'width': 0.35,
            },
        )
        ConnectionManager.add_wire(
            schematic,
            None,
            None,
            {
                'points': [[2, 2], [2, 8]],
                'strokeType': 'dot',
            },
        )

        with tempfile.TemporaryDirectory(dir=str(EXPORT_DIR)) as tmpdir:
            schematic_path = os.path.join(tmpdir, 'wires.kicad_sch')
            self.assertTrue(SchematicManager.save_schematic(schematic, schematic_path))
            reloaded = SchematicManager.load_schematic(schematic_path)
            self.assertIsNotNone(reloaded)
            assert reloaded is not None
            self.assertEqual(len(reloaded.wire), 2)
            widths = [wire.stroke.width.value for wire in reloaded.wire]
            self.assertIn(0.35, widths)
            stroke_types = {wire.stroke.type.value for wire in reloaded.wire}
            self.assertIn('dot', stroke_types)

    def test_connect_pins_creates_wire_between_components(self) -> None:
        schematic = SchematicManager.create_schematic('ConnectPins')
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

        result = ConnectionManager.connect_pins(
            schematic,
            {'reference': 'R1', 'pin': '1'},
            {'reference': 'R2', 'pin': '1'},
        )

        self.assertIsInstance(result, dict)
        self.assertIn('created', result)
        self.assertIn('net', result)
        self.assertIn('netConnections', result)
        self.assertIsInstance(result['created'], dict)
        self.assertIn('source', result['created'])
        self.assertIn('target', result['created'])
        self.assertIn('net', result['created'])
        self.assertIn('summary', result['created'])
        self.assertIn('R1.1', result['created']['summary'])
        self.assertIn('R2.1', result['created']['summary'])
        self.assertTrue(result['net'].startswith('Net '))
        self.assertIn('R1.1(passive)', result['netConnections'])
        self.assertIn('R2.1(passive)', result['netConnections'])

    def test_connect_pins_generates_manhattan_corner(self) -> None:
        schematic = SchematicManager.create_schematic('ConnectPinsCorner')
        ComponentManager.add_component(
            schematic,
            {
                'type': 'R',
                'reference': 'R1',
                'x': 0,
                'y': 0,
            },
        )
        ComponentManager.add_component(
            schematic,
            {
                'type': 'R',
                'reference': 'R2',
                'x': 30,
                'y': 40,
            },
        )

        result = ConnectionManager.connect_pins(
            schematic,
            {'reference': 'R1', 'pin': '2'},
            {'reference': 'R2', 'pin': '1'},
        )

        self.assertIsInstance(result, dict)
        self.assertIn('created', result)
        self.assertIn('net', result)
        self.assertIn('netConnections', result)
        # Verify both pins are in the connection
        self.assertIsInstance(result['created'], dict)
        self.assertIn('summary', result['created'])
        self.assertIn('R1.2', result['created']['summary'])
        self.assertIn('R2.1', result['created']['summary'])
        self.assertTrue(result['net'].startswith('Net '))
        self.assertIn('R1.2(passive)', result['netConnections'])
        self.assertIn('R2.1(passive)', result['netConnections'])

    def test_connect_pins_requires_valid_input(self) -> None:
        schematic = SchematicManager.create_schematic('ConnectPinsInvalid')
        ComponentManager.add_component(
            schematic,
            {
                'type': 'R',
                'reference': 'R1',
                'x': 0,
                'y': 0,
            },
        )

        with self.assertRaises(ValueError):
            ConnectionManager.connect_pins(
                schematic,
                {'reference': 'R1', 'pin': '1'},
                {'reference': 'R1', 'pin': '1'},
            )

        with self.assertRaises(ValueError):
            ConnectionManager.connect_pins(
                schematic,
                {'reference': 'R1', 'pin': '1'},
                {'reference': 'R2', 'pin': '1'},
            )

    def test_connect_pin_to_hierarchical_label(self) -> None:
        """Test connecting a component pin to a hierarchical label"""
        schematic = SchematicManager.create_schematic('PinToLabel')

        # Add a component
        ComponentManager.add_component(
            schematic,
            {
                'type': 'R',
                'reference': 'R1',
                'x': 50,
                'y': 50,
            },
        )

        # Add a hierarchical label manually to the schematic tree
        label_node = [
            Symbol('hierarchical_label'),
            'VBAT',
            [Symbol('shape'), Symbol('input')],
            [Symbol('at'), snap_to_grid(100.0), snap_to_grid(50.0), 0],
            [Symbol('fields_autoplaced')],
            [Symbol('effects'), [Symbol('font'), [Symbol('size'), 1.27, 1.27]], [Symbol('justify'), Symbol('left')]],
            [Symbol('uuid'), Symbol('test-label-uuid-1')]
        ]
        schematic.tree.append(label_node)

        # Connect pin to label
        result = ConnectionManager.connect_pins(
            schematic,
            {'reference': 'R1', 'pin': '2'},
            {'label': 'VBAT'},
        )

        self.assertIsInstance(result, dict)
        self.assertIn('created', result)
        self.assertIn('net', result)
        self.assertIn('netConnections', result)
        # Verify pin and label are in the connection
        self.assertIsInstance(result['created'], dict)
        self.assertIn('summary', result['created'])
        self.assertIn('R1.2', result['created']['summary'])
        self.assertIn('VBAT', result['created']['summary'])

    def test_connect_hierarchical_label_to_pin(self) -> None:
        """Test connecting a hierarchical label to a component pin"""
        schematic = SchematicManager.create_schematic('LabelToPin')

        # Add a component
        ComponentManager.add_component(
            schematic,
            {
                'type': 'C',
                'reference': 'C1',
                'x': 80,
                'y': 80,
            },
        )

        # Add a hierarchical label
        label_node = [
            Symbol('hierarchical_label'),
            'GND',
            [Symbol('shape'), Symbol('output')],
            [Symbol('at'), snap_to_grid(30.0), snap_to_grid(80.0), 0],
            [Symbol('fields_autoplaced')],
            [Symbol('effects'), [Symbol('font'), [Symbol('size'), 1.27, 1.27]], [Symbol('justify'), Symbol('right')]],
            [Symbol('uuid'), Symbol('test-label-uuid-2')]
        ]
        schematic.tree.append(label_node)

        # Connect label to pin (reversed order from previous test)
        result = ConnectionManager.connect_pins(
            schematic,
            {'labelName': 'GND'},  # Test alternative field name
            {'reference': 'C1', 'pin': '1'},
        )

        self.assertIsInstance(result, dict)
        self.assertIn('created', result)
        self.assertIsInstance(result['created'], dict)
        self.assertIn('summary', result['created'])
        self.assertIn('GND', result['created']['summary'])
        self.assertIn('C1.1', result['created']['summary'])

    def test_connect_hierarchical_label_to_hierarchical_label(self) -> None:
        """Test connecting two hierarchical labels"""
        schematic = SchematicManager.create_schematic('LabelToLabel')

        # Add two hierarchical labels
        label1_node = [
            Symbol('hierarchical_label'),
            'VCC',
            [Symbol('shape'), Symbol('input')],
            [Symbol('at'), snap_to_grid(20.0), snap_to_grid(20.0), 0],
            [Symbol('fields_autoplaced')],
            [Symbol('effects'), [Symbol('font'), [Symbol('size'), 1.27, 1.27]], [Symbol('justify'), Symbol('left')]],
            [Symbol('uuid'), Symbol('test-label-uuid-3')]
        ]
        schematic.tree.append(label1_node)

        label2_node = [
            Symbol('hierarchical_label'),
            'VDD',
            [Symbol('shape'), Symbol('output')],
            [Symbol('at'), snap_to_grid(120.0), snap_to_grid(20.0), 0],
            [Symbol('fields_autoplaced')],
            [Symbol('effects'), [Symbol('font'), [Symbol('size'), 1.27, 1.27]], [Symbol('justify'), Symbol('left')]],
            [Symbol('uuid'), Symbol('test-label-uuid-4')]
        ]
        schematic.tree.append(label2_node)

        # Connect the two labels
        result = ConnectionManager.connect_pins(
            schematic,
            {'label': 'VCC'},
            {'label': 'VDD'},
        )

        self.assertIsInstance(result, dict)
        self.assertIn('created', result)
        self.assertIsInstance(result['created'], dict)
        self.assertIn('summary', result['created'])
        self.assertIn('VCC', result['created']['summary'])
        self.assertIn('VDD', result['created']['summary'])

    def test_hierarchical_label_not_found(self) -> None:
        """Test error handling when hierarchical label is not found"""
        schematic = SchematicManager.create_schematic('LabelNotFound')

        ComponentManager.add_component(
            schematic,
            {
                'type': 'R',
                'reference': 'R1',
                'x': 50,
                'y': 50,
            },
        )

        # Try to connect to non-existent label
        with self.assertRaises(ValueError) as context:
            ConnectionManager.connect_pins(
                schematic,
                {'reference': 'R1', 'pin': '1'},
                {'label': 'NONEXISTENT'},
            )

        self.assertIn('not found', str(context.exception).lower())

    def test_ambiguous_connection_spec(self) -> None:
        """Test error handling for ambiguous connection specification"""
        schematic = SchematicManager.create_schematic('AmbiguousSpec')

        ComponentManager.add_component(
            schematic,
            {
                'type': 'R',
                'reference': 'R1',
                'x': 50,
                'y': 50,
            },
        )

        # Try to use both reference and label in same spec
        with self.assertRaises(ValueError) as context:
            ConnectionManager.connect_pins(
                schematic,
                {'reference': 'R1', 'pin': '1', 'label': 'VBAT'},
                {'reference': 'R1', 'pin': '2'},
            )

        self.assertIn('ambiguous', str(context.exception).lower())

    def test_invalid_connection_spec(self) -> None:
        """Test error handling for invalid connection specification"""
        schematic = SchematicManager.create_schematic('InvalidSpec')

        ComponentManager.add_component(
            schematic,
            {
                'type': 'R',
                'reference': 'R1',
                'x': 50,
                'y': 50,
            },
        )

        # Try to use spec with neither reference nor label
        with self.assertRaises(ValueError) as context:
            ConnectionManager.connect_pins(
                schematic,
                {'reference': 'R1', 'pin': '1'},
                {'pin': '2'},  # Missing reference or label
            )

        self.assertIn('invalid', str(context.exception).lower())

    def test_connect_label_to_itself(self) -> None:
        """Test error handling when trying to connect a label to itself"""
        schematic = SchematicManager.create_schematic('LabelToItself')

        # Add a hierarchical label
        label_node = [
            Symbol('hierarchical_label'),
            'SIGNAL',
            [Symbol('shape'), Symbol('bidirectional')],
            [Symbol('at'), snap_to_grid(50.0), snap_to_grid(50.0), 0],
            [Symbol('fields_autoplaced')],
            [Symbol('effects'), [Symbol('font'), [Symbol('size'), 1.27, 1.27]], [Symbol('justify'), Symbol('left')]],
            [Symbol('uuid'), Symbol('test-label-uuid-5')]
        ]
        schematic.tree.append(label_node)

        # Try to connect label to itself
        with self.assertRaises(ValueError) as context:
            ConnectionManager.connect_pins(
                schematic,
                {'label': 'SIGNAL'},
                {'label': 'SIGNAL'},
            )

        self.assertIn('itself', str(context.exception).lower())


    def test_connect_pin_to_global_label(self) -> None:
        schematic = SchematicManager.create_schematic('PinToGlobalLabel')

        # Add a component
        ComponentManager.add_component(
            schematic,
            {
                'type': 'R',
                'reference': 'R1',
                'x': 10,
                'y': 10,
            },
        )

        # Add a global label
        glabel_node = [
            Symbol('global_label'),
            'GND',
            [Symbol('shape'), Symbol('passive')],
            [Symbol('at'), snap_to_grid(50.0), snap_to_grid(10.0), 0],
            [Symbol('uuid'), Symbol('test-global-label')],
        ]
        schematic.tree.append(glabel_node)

        result = ConnectionManager.connect_pins(
            schematic,
            {'reference': 'R1', 'pin': '2'},
            {'label': 'GND'},
        )

        self.assertIsInstance(result, dict)
        self.assertIn('created', result)
        self.assertIsInstance(result['created'], dict)
        self.assertIn('summary', result['created'])
        self.assertIn('R1.2', result['created']['summary'])
        self.assertIn('GND', result['created']['summary'])

    def test_connect_pin_to_local_label(self) -> None:
        schematic = SchematicManager.create_schematic('PinToLocalLabel')

        # Add a component
        ComponentManager.add_component(
            schematic,
            {
                'type': 'R',
                'reference': 'R1',
                'x': 20,
                'y': 20,
            },
        )

        # Add a local label
        llabel_node = [
            Symbol('label'),
            'NET_LOCAL',
            [Symbol('at'), snap_to_grid(60.0), snap_to_grid(20.0), 0],
            [Symbol('uuid'), Symbol('test-local-label')],
        ]
        schematic.tree.append(llabel_node)

        result = ConnectionManager.connect_pins(
            schematic,
            {'reference': 'R1', 'pin': '1'},
            {'label': 'NET_LOCAL'},
        )

        self.assertIsInstance(result, dict)
        self.assertIn('created', result)
        self.assertIsInstance(result['created'], dict)
        self.assertIn('summary', result['created'])
        self.assertIn('R1.1', result['created']['summary'])
        self.assertIn('NET_LOCAL', result['created']['summary'])

if __name__ == '__main__':
    unittest.main()
