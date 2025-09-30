from __future__ import annotations

import os
import tempfile
import unittest

from python.commands.schematic import SchematicManager


class CreateSchematicTests(unittest.TestCase):
    def test_create_schematic_uses_defaults(self) -> None:
        schematic = SchematicManager.create_schematic('DemoProject')

        self.assertEqual(schematic.version, '20230121')
        self.assertEqual(schematic.generator, 'KiCAD-MCP-Server')

        self.assertTrue(hasattr(schematic, 'uuid'))
        self.assertIsNotNone(schematic.uuid.value)

        self.assertTrue(hasattr(schematic, 'paper'))
        self.assertEqual(schematic.paper.value, 'A4')

        self.assertTrue(hasattr(schematic, 'title_block'))
        self.assertEqual(schematic.title_block.title.value, 'DemoProject')

        self.assertTrue(hasattr(schematic, 'sheet'))
        self.assertEqual(schematic.sheet.name.value, 'Sheet1')
        self.assertIsNotNone(schematic.sheet.uuid.value)
        self.assertEqual(schematic.sheet.title_block.title.value, 'DemoProject')

    def test_create_schematic_applies_metadata(self) -> None:
        metadata = {
            'title': 'Custom Title',
            'date': '2024-05-01',
            'revision': 'B',
            'company': 'ACME Corp',
            'paper': 'USLetter',
            'generator': 'KiCAD-MCP-Tester',
            'version': 20240201,
            'description': 'Primary description',
            'author': 'Alice Example',
            'comments': ['Override comment 1', None, 'Override comment 3'],
            'sheet': {
                'title': 'Root Sheet',
                'date': '2024-06-01',
                'revision': 'C',
                'company': 'Sheet Co',
                'comment1': 'Sheet comment override',
            },
        }

        schematic = SchematicManager.create_schematic('DemoProject', metadata)

        self.assertEqual(schematic.version, '20240201')
        self.assertEqual(schematic.generator, 'KiCAD-MCP-Tester')
        self.assertEqual(schematic.paper.value, 'USLetter')
        self.assertEqual(schematic.title_block.title.value, 'Custom Title')
        self.assertEqual(schematic.title_block.date.value, '2024-05-01')
        self.assertEqual(schematic.title_block.rev.value, 'B')
        self.assertEqual(schematic.title_block.company.value, 'ACME Corp')

        comments = schematic.title_block.comment
        self.assertEqual(comments[0].value[1], 'Override comment 1')
        # Second comment defaults to author when explicit override is missing.
        self.assertEqual(comments[1].value[1], 'Alice Example')
        self.assertEqual(comments[2].value[1], 'Override comment 3')

        sheet_block = schematic.sheet.title_block
        self.assertEqual(sheet_block.title.value, 'Root Sheet')
        self.assertEqual(sheet_block.date.value, '2024-06-01')
        self.assertEqual(sheet_block.rev.value, 'C')
        self.assertEqual(sheet_block.company.value, 'Sheet Co')
        self.assertEqual(sheet_block.comment[0].value[1], 'Sheet comment override')
        # sheet comments inherit unspecified values from top-level metadata.
        self.assertEqual(sheet_block.comment[1].value[1], 'Alice Example')

    def test_create_schematic_requires_valid_inputs(self) -> None:
        with self.assertRaises(ValueError):
            SchematicManager.create_schematic('')

        with self.assertRaises(ValueError):
            SchematicManager.create_schematic('  ')

        with self.assertRaises(TypeError):
            SchematicManager.create_schematic('Demo', metadata='not-a-dict')  # type: ignore[arg-type]

        with self.assertRaises(ValueError):
            SchematicManager.create_schematic('Demo', metadata={'version': 'not-a-number'})

    def test_create_schematic_produces_unique_identifiers(self) -> None:
        first = SchematicManager.create_schematic('Demo')
        second = SchematicManager.create_schematic('Demo')

        self.assertNotEqual(first.uuid.value, second.uuid.value)
        self.assertNotEqual(first.sheet.uuid.value, second.sheet.uuid.value)

    def test_create_schematic_round_trip_save_and_load(self) -> None:
        metadata = {'title': 'Round Trip'}
        schematic = SchematicManager.create_schematic('RoundTrip', metadata)

        with tempfile.TemporaryDirectory() as tmpdir:
            target_path = os.path.join(tmpdir, 'roundtrip.kicad_sch')
            save_result = SchematicManager.save_schematic(schematic, target_path)
            self.assertTrue(save_result)
            self.assertTrue(os.path.exists(target_path))

            loaded = SchematicManager.load_schematic(target_path)
            self.assertIsNotNone(loaded)
            assert loaded is not None  # for type checkers
            self.assertEqual(loaded.title_block.title.value, 'Round Trip')
            self.assertEqual(loaded.sheet.name.value, 'Sheet1')


if __name__ == '__main__':
    unittest.main()
