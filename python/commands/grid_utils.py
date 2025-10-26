"""
Grid alignment utilities for KiCAD schematic coordinates.

KiCAD schematics use a 50 mil (1.27mm) grid. All coordinates must be
aligned to this grid for proper electrical connectivity and ERC compliance.
"""

import logging
from typing import List, Sequence, Tuple, Union, Any

logger = logging.getLogger('kicad_interface')

# KiCAD standard schematic grid
KICAD_SCHEMATIC_GRID_MM = 1.27  # 50 mil


def snap_to_grid(value: float, grid: float = KICAD_SCHEMATIC_GRID_MM) -> float:
    """
    Snap a coordinate value to the nearest grid point.
    
    Args:
        value: Coordinate value in millimeters
        grid: Grid size in millimeters (default: 1.27mm)
    
    Returns:
        Snapped coordinate value
    
    Example:
        >>> snap_to_grid(50.0)
        50.80
        >>> snap_to_grid(70.0)
        69.85
    """
    snapped = round(value / grid) * grid
    
    # Log if significant adjustment was made (more than 0.01mm)
    if abs(value - snapped) > 0.01:
        logger.debug(
            f"Grid snap: {value:.2f}mm → {snapped:.2f}mm "
            f"(adjusted by {abs(value - snapped):.2f}mm)"
        )
    
    return snapped


def snap_point_to_grid(
    x: float,
    y: float,
    grid: float = KICAD_SCHEMATIC_GRID_MM
) -> Tuple[float, float]:
    """
    Snap a 2D point to the nearest grid point.
    
    Args:
        x: X coordinate in millimeters
        y: Y coordinate in millimeters
        grid: Grid size in millimeters (default: 1.27mm)
    
    Returns:
        Tuple of (snapped_x, snapped_y)
    """
    return (snap_to_grid(x, grid), snap_to_grid(y, grid))


def snap_points_to_grid(
    points: Sequence[Union[Tuple[float, float], Sequence[float]]],
    grid: float = KICAD_SCHEMATIC_GRID_MM
) -> List[Tuple[float, float]]:
    """
    Snap a list of points to the grid.
    
    Args:
        points: List of (x, y) tuples or [x, y] lists
        grid: Grid size in millimeters (default: 1.27mm)
    
    Returns:
        List of snapped (x, y) tuples
    """
    result = []
    for point in points:
        if isinstance(point, (list, tuple)) and len(point) >= 2:
            x, y = point[0], point[1]
            result.append(snap_point_to_grid(x, y, grid))
        else:
            logger.warning(f"Invalid point format: {point}, skipping")
    return result


def is_on_grid(
    value: float,
    grid: float = KICAD_SCHEMATIC_GRID_MM,
    tolerance: float = 0.001
) -> bool:
    """
    Check if a coordinate value is aligned to the grid.
    
    Args:
        value: Coordinate value in millimeters
        grid: Grid size in millimeters (default: 1.27mm)
        tolerance: Tolerance for floating-point comparison (default: 0.001mm)
    
    Returns:
        True if value is on-grid, False otherwise
    """
    grid_position = value / grid
    return abs(grid_position - round(grid_position)) < tolerance


def round_to_grid(value: float, decimals: int = 6) -> float:
    """
    Round a grid-snapped value to specified decimal places.
    This is used when writing coordinates to the schematic file.
    
    Args:
        value: Already grid-snapped coordinate value
        decimals: Number of decimal places (default: 6)
    
    Returns:
        Rounded value
    """
    return round(value, decimals)
