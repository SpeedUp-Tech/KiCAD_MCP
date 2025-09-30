import os
import tempfile
import unittest

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

        self.assertEqual([pt.value for pt in wire.points], [[0.0, 0.0], [12.5, 3.0]])
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

        self.assertEqual(first_segment, [[0.0, 0.0], [5.0, 0.0]])
        self.assertEqual(second_segment, [[5.0, 0.0], [5.0, 5.0]])

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


if __name__ == '__main__':
    unittest.main()
