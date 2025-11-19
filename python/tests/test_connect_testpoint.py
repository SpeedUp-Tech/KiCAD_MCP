import tempfile
import unittest
from pathlib import Path

from python.commands.kicad_schematics.schematic import SchematicManager
from python.commands.kicad_schematics.component_schematic import ComponentManager
from python.commands.kicad_schematics.connection_schematic import ConnectionManager
from python.commands.kicad_schematics.schematic_state import get_schematic_state


class ConnectTestPointTests(unittest.TestCase):
    """Verify connect_schematic_pins can wire power labels to single-pin test points."""

    def setUp(self):
        self._prev_tempdir = tempfile.tempdir
        temp_root = Path(__file__).resolve().parents[2] / 'exported' / 'tests_tmp'
        temp_root.mkdir(parents=True, exist_ok=True)
        tempfile.tempdir = str(temp_root)

    def tearDown(self):
        tempfile.tempdir = self._prev_tempdir

    def test_connect_power_flag_to_testpoint(self):
        sch = SchematicManager.create_schematic('connect_testpoint')

        # Drop a GND power symbol (treated as a label source)
        ComponentManager.add_component(
            sch,
            {
                'type': 'GND',
                'library': 'power',
                'reference': '#PWR0101',
                'x': 10.0,
                'y': 10.0,
            },
        )

        # Add a single-pin test point from the Connector library
        ComponentManager.add_component(
            sch,
            {
                'type': 'TestPoint',
                'library': 'Connector',
                'reference': 'TP1',
                'x': 30.0,
                'y': 10.0,
            },
        )

        result = ConnectionManager.connect_pins(
            sch,
            {'label': 'GND'},
            {'reference': 'TP1', 'pin': '1'},
            wire={'width': 0.254},
        )

        self.assertIsInstance(result, dict)
        self.assertEqual(result['net'], 'GND')
        self.assertIn('TP1.1(passive)', result['netConnections'])

        state_text = get_schematic_state(sch, show_details=True, output_format='text')
        self.assertIn('TP1: TestPoint', state_text)
        self.assertIn('Pin 1: passive (1)', state_text)
        self.assertIn('GND - TP1.1 [GND]', state_text)


if __name__ == '__main__':
    unittest.main()
