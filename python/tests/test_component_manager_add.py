import os
import tempfile
import unittest
from pathlib import Path


from python.commands.kicad_schematics.schematic import SchematicManager
from python.commands.kicad_schematics.component_schematic import ComponentManager

EXPORT_DIR = Path(__file__).resolve().parents[2] / 'exported'
EXPORT_DIR.mkdir(parents=True, exist_ok=True)


class ComponentManagerTests(unittest.TestCase):
    def test_add_component_populates_symbol_and_library(self) -> None:
        schematic = SchematicManager.create_schematic('ComponentManagerPopulates')
        symbol = ComponentManager.add_component(
            schematic,
            {
                'type': 'R',
                'reference': 'R42',
                'value': '47k',
                'footprint': 'Resistor_SMD:R_0603_1608Metric',
                'x': 12.34,
                'y': 56.78,
                'rotation': 90,
                'properties': {'Tolerance': '5%'},
            },
        )

        self.assertEqual(symbol.property.Reference.value, 'R42')
        self.assertEqual(symbol.property.Value.value, '47k')
        self.assertEqual(symbol.lib_id.value, 'Device:R')
        self.assertEqual(symbol.property.Footprint.value, 'Resistor_SMD:R_0603_1608Metric')

        at_value = symbol.at.value
        # Coordinates are automatically snapped to 1.27mm grid
        self.assertAlmostEqual(at_value[0], 12.7)   # 12.34 -> 12.7 (10 * 1.27)
        self.assertAlmostEqual(at_value[1], 57.15)  # 56.78 -> 57.15 (45 * 1.27)
        self.assertAlmostEqual(at_value[2], 90.0)
        self.assertEqual(symbol.unit.value, 1)
        self.assertEqual(symbol.property.Tolerance.value, '5%')

        lib_symbols = getattr(schematic, 'lib_symbols')
        self.assertIn('Device:R', lib_symbols._libsyms_by_id)

    def test_add_component_rejects_duplicate_reference(self) -> None:
        schematic = SchematicManager.create_schematic('ComponentManagerDuplicate')
        ComponentManager.add_component(schematic, {'type': 'R', 'reference': 'R1'})
        with self.assertRaises(ValueError):
            ComponentManager.add_component(schematic, {'type': 'C', 'reference': 'R1'})

    def test_add_component_missing_symbol_raises(self) -> None:
        schematic = SchematicManager.create_schematic('ComponentManagerMissingSymbol')
        with self.assertRaises((ValueError, FileNotFoundError)):
            ComponentManager.add_component(
                schematic,
                {
                    'type': 'ImaginaryPart',
                    'library': 'DoesNotExist',
                    'reference': 'X1',
                },
            )

    def test_add_component_round_trip_save(self) -> None:
        schematic = SchematicManager.create_schematic('ComponentManagerRoundTrip')
        ComponentManager.add_component(
            schematic,
            {
                'type': 'R',
                'reference': 'R1',
                'value': '10k',
                'x': 5.0,
                'y': 5.0,
            },
        )
        ComponentManager.add_component(
            schematic,
            {
                'type': 'C',
                'reference': 'C1',
                'value': '100nF',
                'x': 15.0,
                'y': 5.0,
            },
        )

        with tempfile.TemporaryDirectory(dir=str(EXPORT_DIR)) as tmpdir:
            schematic_path = os.path.join(tmpdir, 'round_trip.kicad_sch')
            self.assertTrue(SchematicManager.save_schematic(schematic, schematic_path))
            reloaded = SchematicManager.load_schematic(schematic_path)
            self.assertIsNotNone(reloaded)
            assert reloaded is not None
            self.assertEqual(len(reloaded.symbol), 2)
            references = [sym.property.Reference.value for sym in reloaded.symbol]
            self.assertIn('R1', references)
            self.assertIn('C1', references)


if __name__ == '__main__':
    unittest.main()
