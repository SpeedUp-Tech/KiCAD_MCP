"""
Unit tests for grid alignment utilities.
"""

import unittest
from python.commands.grid_utils import (
    snap_to_grid,
    snap_point_to_grid,
    snap_points_to_grid,
    is_on_grid,
    KICAD_SCHEMATIC_GRID_MM
)


class TestGridUtils(unittest.TestCase):
    """Test grid alignment functions."""
    
    def test_snap_to_grid_basic(self):
        """Test basic grid snapping."""
        # Common off-grid values snap to NEAREST grid point
        # 50.0 / 1.27 = 39.37 -> rounds to 39 -> 39 * 1.27 = 49.53
        self.assertAlmostEqual(snap_to_grid(50.0), 49.53, places=2)
        # 70.0 / 1.27 = 55.12 -> rounds to 55 -> 55 * 1.27 = 69.85
        self.assertAlmostEqual(snap_to_grid(70.0), 69.85, places=2)
        # 100.0 / 1.27 = 78.74 -> rounds to 79 -> 79 * 1.27 = 100.33
        self.assertAlmostEqual(snap_to_grid(100.0), 100.33, places=2)
        # 110.0 / 1.27 = 86.61 -> rounds to 87 -> 87 * 1.27 = 110.49
        self.assertAlmostEqual(snap_to_grid(110.0), 110.49, places=2)
        
    def test_snap_to_grid_already_on_grid(self):
        """Test that on-grid values remain unchanged."""
        # Values already on grid
        self.assertAlmostEqual(snap_to_grid(50.80), 50.80, places=2)  # 40 * 1.27
        self.assertAlmostEqual(snap_to_grid(49.53), 49.53, places=2)  # 39 * 1.27
        self.assertAlmostEqual(snap_to_grid(69.85), 69.85, places=2)  # 55 * 1.27
        
    def test_snap_to_grid_zero(self):
        """Test zero coordinate."""
        self.assertAlmostEqual(snap_to_grid(0.0), 0.0, places=2)
        
    def test_snap_to_grid_negative(self):
        """Test negative coordinates."""
        # -50.0 / 1.27 = -39.37 -> rounds to -39 -> -39 * 1.27 = -49.53
        self.assertAlmostEqual(snap_to_grid(-50.0), -49.53, places=2)
        self.assertAlmostEqual(snap_to_grid(-5.08), -5.08, places=2)  # Already on grid (-4 * 1.27)
        
    def test_snap_point_to_grid(self):
        """Test 2D point snapping."""
        x, y = snap_point_to_grid(50.0, 70.0)
        self.assertAlmostEqual(x, 49.53, places=2)  # 39 * 1.27
        self.assertAlmostEqual(y, 69.85, places=2)  # 55 * 1.27
        
    def test_snap_points_to_grid(self):
        """Test list of points snapping."""
        points = [(50.0, 70.0), (100.0, 110.0)]
        snapped = snap_points_to_grid(points)

        self.assertEqual(len(snapped), 2)
        self.assertAlmostEqual(snapped[0][0], 49.53, places=2)  # 39 * 1.27
        self.assertAlmostEqual(snapped[0][1], 69.85, places=2)  # 55 * 1.27
        self.assertAlmostEqual(snapped[1][0], 100.33, places=2)  # 79 * 1.27
        self.assertAlmostEqual(snapped[1][1], 110.49, places=2)  # 87 * 1.27
        
    def test_snap_points_to_grid_with_lists(self):
        """Test list of points as lists (not tuples)."""
        points = [[50.0, 70.0], [100.0, 110.0]]
        snapped = snap_points_to_grid(points)

        self.assertEqual(len(snapped), 2)
        self.assertAlmostEqual(snapped[0][0], 49.53, places=2)  # 39 * 1.27
        self.assertAlmostEqual(snapped[0][1], 69.85, places=2)  # 55 * 1.27
        
    def test_is_on_grid_true(self):
        """Test on-grid detection for valid values."""
        self.assertTrue(is_on_grid(50.80))
        self.assertTrue(is_on_grid(49.53))
        self.assertTrue(is_on_grid(69.85))
        self.assertTrue(is_on_grid(0.0))
        self.assertTrue(is_on_grid(1.27))
        self.assertTrue(is_on_grid(2.54))
        
    def test_is_on_grid_false(self):
        """Test on-grid detection for off-grid values."""
        self.assertFalse(is_on_grid(50.0))
        self.assertFalse(is_on_grid(70.0))
        self.assertFalse(is_on_grid(100.0))
        self.assertFalse(is_on_grid(44.92))  # The problematic pin position
        
    def test_grid_constant(self):
        """Test that grid constant is correct."""
        self.assertAlmostEqual(KICAD_SCHEMATIC_GRID_MM, 1.27, places=2)
        
    def test_pin_offset_calculation(self):
        """Test the specific case from the ERC error."""
        # Component at 50.0mm with pin offset -5.08mm
        component_x = 50.0
        pin_offset = -5.08

        # Without snapping - causes error
        pin_x_wrong = component_x + pin_offset
        self.assertAlmostEqual(pin_x_wrong, 44.92, places=2)
        self.assertFalse(is_on_grid(pin_x_wrong))

        # With snapping - should work
        # 50.0 snaps to 49.53 (nearest grid point)
        component_x_snapped = snap_to_grid(component_x)
        pin_x_correct = component_x_snapped + pin_offset
        self.assertAlmostEqual(component_x_snapped, 49.53, places=2)  # 39 * 1.27
        self.assertAlmostEqual(pin_x_correct, 44.45, places=2)  # 35 * 1.27
        self.assertTrue(is_on_grid(pin_x_correct))
        
    def test_hierarchical_label_positions(self):
        """Test hierarchical label positions from the ERC report."""
        # From the ERC report: @(30.00 mm, 100.00 mm)
        x_wrong = 30.0
        y_wrong = 100.0
        
        self.assertFalse(is_on_grid(x_wrong))
        self.assertFalse(is_on_grid(y_wrong))
        
        # Snap to grid
        x_correct = snap_to_grid(x_wrong)
        y_correct = snap_to_grid(y_wrong)
        
        self.assertTrue(is_on_grid(x_correct))
        self.assertTrue(is_on_grid(y_correct))
        self.assertAlmostEqual(x_correct, 30.48, places=2)  # 24 * 1.27
        self.assertAlmostEqual(y_correct, 100.33, places=2)  # 79 * 1.27


if __name__ == '__main__':
    unittest.main()

