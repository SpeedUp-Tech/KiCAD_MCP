import os
import tempfile
import unittest
from sexpdata import Symbol

from python.commands.schematic import SchematicManager
from python.commands.connection_schematic import ConnectionManager
from python.commands.component_schematic import ComponentManager


class ConnectionManagerTests(unittest.TestCase):
    def test_add_wire_basic_attributes(self) -> None:
        schematic = SchematicManager.create_schematic('WireBasic')
        wire = ConnectionManager.add_wire(
            schematic,
            [0, 0],
            [12.5, 3.0],
            {
                'width': 0.5,
                'strokeType': 'dash',
            },
        )

        # Coordinates are automatically snapped to 1.27mm grid
        self.assertEqual([pt.value for pt in wire.points], [[0.0, 0.0], [12.7, 2.54]])
        self.assertAlmostEqual(wire.stroke.width.value, 0.5)
        self.assertEqual(wire.stroke.type.value, 'dash')
        self.assertIsInstance(wire.uuid.value, str)

    def test_add_wire_with_intermediate_points(self) -> None:
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
        self.assertEqual(len(wires), 2)

        first_segment = [pt.value for pt in wires[0].points]
        second_segment = [pt.value for pt in wires[1].points]

        # Coordinates are automatically snapped to 1.27mm grid
        self.assertEqual(first_segment, [[0.0, 0.0], [5.08, 0.0]])  # 5.0 -> 5.08 (4 * 1.27)
        self.assertEqual(second_segment, [[5.08, 0.0], [5.08, 5.08]])  # 5.0 -> 5.08

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

        with tempfile.TemporaryDirectory() as tmpdir:
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

        wires = ConnectionManager.connect_pins(
            schematic,
            {'reference': 'R1', 'pin': '1'},
            {'reference': 'R2', 'pin': '1'},
        )

        self.assertEqual(len(wires), 1)
        points = [pt.value for pt in wires[0].points]
        self.assertEqual(len(points), 2)
        self.assertAlmostEqual(points[0][1], points[1][1])
        self.assertLess(points[0][0], points[1][0])

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

        wires = ConnectionManager.connect_pins(
            schematic,
            {'reference': 'R1', 'pin': '2'},
            {'reference': 'R2', 'pin': '1'},
        )

        self.assertEqual(len(wires), 2)

        first_segment = [pt.value for pt in wires[0].points]
        second_segment = [pt.value for pt in wires[1].points]

        self.assertEqual(first_segment[-1], second_segment[0])
        self.assertNotEqual(first_segment[0], second_segment[-1])

        # First segment should be horizontal (y constant)
        self.assertAlmostEqual(first_segment[0][1], first_segment[1][1])
        self.assertNotAlmostEqual(first_segment[0][0], first_segment[1][0])

        # Second segment should be vertical (x constant)
        self.assertAlmostEqual(second_segment[0][0], second_segment[1][0])
        self.assertNotAlmostEqual(second_segment[0][1], second_segment[1][1])

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
            [Symbol('at'), 100.0, 50.0, 0],
            [Symbol('fields_autoplaced')],
            [Symbol('effects'), [Symbol('font'), [Symbol('size'), 1.27, 1.27]], [Symbol('justify'), Symbol('left')]],
            [Symbol('uuid'), Symbol('test-label-uuid-1')]
        ]
        schematic.tree.append(label_node)

        # Connect pin to label
        wires = ConnectionManager.connect_pins(
            schematic,
            {'reference': 'R1', 'pin': '2'},
            {'label': 'VBAT'},
        )

        self.assertIsInstance(wires, list)
        self.assertGreater(len(wires), 0)

        # Verify wire was created
        points = [pt.value for pt in wires[0].points]
        self.assertEqual(len(points), 2)

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
            [Symbol('at'), 30.0, 80.0, 0],
            [Symbol('fields_autoplaced')],
            [Symbol('effects'), [Symbol('font'), [Symbol('size'), 1.27, 1.27]], [Symbol('justify'), Symbol('right')]],
            [Symbol('uuid'), Symbol('test-label-uuid-2')]
        ]
        schematic.tree.append(label_node)

        # Connect label to pin (reversed order from previous test)
        wires = ConnectionManager.connect_pins(
            schematic,
            {'labelName': 'GND'},  # Test alternative field name
            {'reference': 'C1', 'pin': '1'},
        )

        self.assertIsInstance(wires, list)
        self.assertGreater(len(wires), 0)

    def test_connect_hierarchical_label_to_hierarchical_label(self) -> None:
        """Test connecting two hierarchical labels"""
        schematic = SchematicManager.create_schematic('LabelToLabel')

        # Add two hierarchical labels
        label1_node = [
            Symbol('hierarchical_label'),
            'VCC',
            [Symbol('shape'), Symbol('input')],
            [Symbol('at'), 20.0, 20.0, 0],
            [Symbol('fields_autoplaced')],
            [Symbol('effects'), [Symbol('font'), [Symbol('size'), 1.27, 1.27]], [Symbol('justify'), Symbol('left')]],
            [Symbol('uuid'), Symbol('test-label-uuid-3')]
        ]
        schematic.tree.append(label1_node)

        label2_node = [
            Symbol('hierarchical_label'),
            'VDD',
            [Symbol('shape'), Symbol('output')],
            [Symbol('at'), 120.0, 20.0, 0],
            [Symbol('fields_autoplaced')],
            [Symbol('effects'), [Symbol('font'), [Symbol('size'), 1.27, 1.27]], [Symbol('justify'), Symbol('left')]],
            [Symbol('uuid'), Symbol('test-label-uuid-4')]
        ]
        schematic.tree.append(label2_node)

        # Connect the two labels
        wires = ConnectionManager.connect_pins(
            schematic,
            {'label': 'VCC'},
            {'label': 'VDD'},
        )

        self.assertIsInstance(wires, list)
        self.assertGreater(len(wires), 0)

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
            [Symbol('at'), 50.0, 50.0, 0],
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


if __name__ == '__main__':
    unittest.main()
