"""
Test library name suggestion feature for add_component_symbol().

This test demonstrates that when an LLM agent uses an incorrect library name
(e.g., "Power" instead of "power"), the system provides helpful suggestions.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from commands.component_schematic import _find_similar_library_names, _resolve_library_path


class LibraryNameSuggestionTests(unittest.TestCase):
    """Test that library name errors provide helpful suggestions."""

    def setUp(self):
        """Create a temporary directory with mock library files."""
        self.temp_dir = tempfile.mkdtemp(prefix='kicad_lib_test_')
        self.lib_path = Path(self.temp_dir)
        
        # Create mock library files with common KiCAD library names
        self.mock_libraries = [
            'power',
            'Device',
            'Connector',
            'Connector_Generic',
            'MCU_Module',
            'RF_Module',
            'Sensor',
        ]
        
        for lib_name in self.mock_libraries:
            lib_file = self.lib_path / f'{lib_name}.kicad_sym'
            lib_file.write_text('(kicad_symbol_lib (version 20211014) (generator kicad_symbol_editor))')

    def tearDown(self):
        """Clean up temporary directory."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_case_insensitive_exact_match_suggestion(self):
        """Test that 'Power' suggests 'power' (case mismatch)."""
        suggestions = _find_similar_library_names('Power', [self.lib_path])
        
        self.assertEqual(len(suggestions), 1, "Should return exactly one suggestion for case mismatch")
        self.assertEqual(suggestions[0], 'power', "Should suggest 'power' for 'Power'")

    def test_case_insensitive_device_suggestion(self):
        """Test that 'device' suggests 'Device' (case mismatch)."""
        suggestions = _find_similar_library_names('device', [self.lib_path])
        
        self.assertEqual(len(suggestions), 1, "Should return exactly one suggestion for case mismatch")
        self.assertEqual(suggestions[0], 'Device', "Should suggest 'Device' for 'device'")

    def test_partial_match_suggestion(self):
        """Test that 'Connector' finds both 'Connector' and 'Connector_Generic'."""
        suggestions = _find_similar_library_names('Connect', [self.lib_path])
        
        self.assertGreater(len(suggestions), 0, "Should find suggestions for partial match")
        # Should include libraries starting with 'Connect'
        connector_libs = [s for s in suggestions if 'Connector' in s]
        self.assertGreater(len(connector_libs), 0, "Should suggest Connector libraries")

    def test_no_suggestions_for_completely_wrong_name(self):
        """Test that a completely wrong name returns empty suggestions."""
        suggestions = _find_similar_library_names('XYZ_NonExistent_Library_12345', [self.lib_path])
        
        self.assertEqual(len(suggestions), 0, "Should return no suggestions for completely wrong name")

    def test_resolve_library_path_error_message_with_suggestion(self):
        """Test that _resolve_library_path raises FileNotFoundError with helpful message."""
        with self.assertRaises(FileNotFoundError) as context:
            _resolve_library_path('Power', None, [self.temp_dir])
        
        error_message = str(context.exception)
        
        # Check that the error message contains the suggestion
        self.assertIn("Could not locate library 'Power'", error_message)
        self.assertIn("power", error_message, "Error message should suggest 'power'")
        self.assertIn("Did you mean", error_message, "Error message should ask 'Did you mean'")

    def test_resolve_library_path_error_message_multiple_suggestions(self):
        """Test error message with multiple suggestions."""
        with self.assertRaises(FileNotFoundError) as context:
            _resolve_library_path('Conn', None, [self.temp_dir])
        
        error_message = str(context.exception)
        
        # Check that the error message contains suggestions
        self.assertIn("Could not locate library 'Conn'", error_message)
        self.assertIn("Did you mean one of:", error_message)

    def test_resolve_library_path_success_with_correct_name(self):
        """Test that correct library name resolves successfully."""
        resolved_path = _resolve_library_path('power', None, [self.temp_dir])
        
        self.assertTrue(resolved_path.exists(), "Should resolve to existing file")
        self.assertEqual(resolved_path.stem, 'power', "Should resolve to 'power' library")
        self.assertEqual(resolved_path.suffix, '.kicad_sym', "Should have .kicad_sym extension")

    def test_max_suggestions_limit(self):
        """Test that suggestions are limited to max_suggestions parameter."""
        # Create many similar libraries
        for i in range(10):
            lib_file = self.lib_path / f'Test_Library_{i}.kicad_sym'
            lib_file.write_text('(kicad_symbol_lib (version 20211014) (generator kicad_symbol_editor))')
        
        suggestions = _find_similar_library_names('Test', [self.lib_path], max_suggestions=3)
        
        self.assertLessEqual(len(suggestions), 3, "Should limit suggestions to max_suggestions")


class IntegrationTestWithComponentManager(unittest.TestCase):
    """Integration test with ComponentManager.add_component()."""

    def test_add_component_with_wrong_library_case(self):
        """Test that add_component provides helpful error when library case is wrong."""
        from commands.schematic import SchematicManager
        from commands.component_schematic import ComponentManager
        
        # Create a temporary schematic
        schematic = SchematicManager.create_schematic('TestSchematic')
        
        # Try to add a component with wrong library case
        # This should fail with a helpful error message
        component_def = {
            'type': 'GND',
            'library': 'Power',  # Wrong case - should be 'power'
            'reference': 'GND1',
            'x': 100,
            'y': 100,
        }
        
        with self.assertRaises(FileNotFoundError) as context:
            ComponentManager.add_component(schematic, component_def)
        
        error_message = str(context.exception)
        
        # The error should mention the wrong library name
        self.assertIn('Power', error_message)
        
        # If 'power' library exists in the system, it should be suggested
        # Otherwise, the error should still be informative
        self.assertTrue(
            "Did you mean" in error_message or "Checked:" in error_message,
            "Error message should provide suggestions or checked paths"
        )


if __name__ == '__main__':
    unittest.main()

