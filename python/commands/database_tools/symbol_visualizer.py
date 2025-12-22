"""
KiCad Symbol Visualizer - Render symbols directly in Jupyter notebooks.

This module provides utilities to visualize KiCad symbols from S-expressions
without creating any temporary files. Perfect for quick verification of
symbols before adding them to the database.

Usage:
    from commands.database_tools.symbol_visualizer import visualize_symbol, visualize_by_mpn

    # From S-expression string
    fig = visualize_symbol(sexp_text)

    # From database by MPN
    fig = visualize_by_mpn("STM32F103C8T6")

    # Compare two symbols
    fig = compare_symbols(sexp1, sexp2)
"""

from __future__ import annotations

import sqlite3
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import sexpdata
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.lines import Line2D
from matplotlib.figure import Figure
from matplotlib.axes import Axes

# Default database path
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SYMBOL_DB = PROJECT_ROOT / 'symbol_lib' / 'kicad_symbols.sqlite3'

# Pin type colors (matching KiCad visual conventions)
PIN_TYPE_COLORS = {
    'input': '#009900',        # Green
    'output': '#CC0000',       # Red
    'bidirectional': '#0000CC', # Blue
    'power_in': '#CC6600',     # Orange
    'power_out': '#996600',    # Dark orange
    'passive': '#666666',      # Gray
    'no_connect': '#000000',   # Black
    'unspecified': '#9900CC',  # Purple
    'tri_state': '#6600CC',    # Purple variant
    'open_collector': '#660000', # Dark red
    'open_emitter': '#660000',  # Dark red
    'free': '#999999',         # Light gray
}

# Pin style shapes
PIN_STYLE_MARKERS = {
    'inverted': True,  # Draw circle at pin end
    'clock': True,     # Draw clock symbol
    'line': False,     # Default line
}


def _atom_to_str(atom: Any) -> str:
    """Convert an S-expression atom to string."""
    if isinstance(atom, sexpdata.Symbol):
        return atom.value()
    if isinstance(atom, str):
        return atom
    return str(atom)


def _find_subexpr(expr: list, name: str) -> Optional[list]:
    """Find a sub-expression with the given head symbol name."""
    for item in expr:
        if isinstance(item, list) and len(item) > 0:
            head = item[0]
            if isinstance(head, sexpdata.Symbol) and head.value() == name:
                return item
    return None


def _find_all_subexpr(expr: list, name: str) -> List[list]:
    """Find all sub-expressions with the given head symbol name."""
    results = []
    for item in expr:
        if isinstance(item, list) and len(item) > 0:
            head = item[0]
            if isinstance(head, sexpdata.Symbol) and head.value() == name:
                results.append(item)
    return results


def _parse_coords(at_expr: list) -> Tuple[float, float, float]:
    """Parse (at X Y [ANGLE]) expression. Returns (x, y, angle)."""
    x = float(at_expr[1]) if len(at_expr) > 1 else 0.0
    y = float(at_expr[2]) if len(at_expr) > 2 else 0.0
    angle = float(at_expr[3]) if len(at_expr) > 3 else 0.0
    return x, y, angle


def _parse_rectangle(rect_expr: list) -> Tuple[Tuple[float, float], Tuple[float, float]]:
    """Parse (rectangle (start X Y) (end X Y) ...). Returns ((x1,y1), (x2,y2))."""
    start_expr = _find_subexpr(rect_expr, 'start')
    end_expr = _find_subexpr(rect_expr, 'end')
    if start_expr and end_expr:
        x1 = float(start_expr[1]) if len(start_expr) > 1 else 0.0
        y1 = float(start_expr[2]) if len(start_expr) > 2 else 0.0
        x2 = float(end_expr[1]) if len(end_expr) > 1 else 0.0
        y2 = float(end_expr[2]) if len(end_expr) > 2 else 0.0
        return ((x1, y1), (x2, y2))
    return ((0, 0), (0, 0))


def _parse_circle(circle_expr: list) -> Tuple[Tuple[float, float], float]:
    """Parse (circle (center X Y) (radius R) ...). Returns ((cx, cy), radius)."""
    center_expr = _find_subexpr(circle_expr, 'center')
    radius_expr = _find_subexpr(circle_expr, 'radius')
    cx, cy = 0.0, 0.0
    radius = 0.0
    if center_expr and len(center_expr) > 2:
        cx = float(center_expr[1])
        cy = float(center_expr[2])
    if radius_expr and len(radius_expr) > 1:
        radius = float(radius_expr[1])
    return ((cx, cy), radius)


def _parse_polyline_pts(polyline_expr: list) -> List[Tuple[float, float]]:
    """Parse (polyline (pts (xy X Y) (xy X Y) ...) ...). Returns list of (x, y)."""
    pts_expr = _find_subexpr(polyline_expr, 'pts')
    points = []
    if pts_expr:
        for item in pts_expr[1:]:
            if isinstance(item, list) and len(item) >= 3:
                head = item[0]
                if isinstance(head, sexpdata.Symbol) and head.value() == 'xy':
                    x = float(item[1])
                    y = float(item[2])
                    points.append((x, y))
    return points


def _parse_arc(arc_expr: list) -> Optional[Dict[str, Any]]:
    """Parse (arc (start X Y) (mid X Y) (end X Y) ...). Returns arc params."""
    start_expr = _find_subexpr(arc_expr, 'start')
    mid_expr = _find_subexpr(arc_expr, 'mid')
    end_expr = _find_subexpr(arc_expr, 'end')
    if start_expr and mid_expr and end_expr:
        return {
            'start': (float(start_expr[1]), float(start_expr[2])),
            'mid': (float(mid_expr[1]), float(mid_expr[2])),
            'end': (float(end_expr[1]), float(end_expr[2])),
        }
    return None


def _parse_pin(pin_expr: list) -> Dict[str, Any]:
    """Parse a pin expression and return its properties."""
    pin_info = {
        'type': 'unspecified',
        'style': 'line',
        'x': 0.0,
        'y': 0.0,
        'angle': 0.0,
        'length': 2.54,
        'name': '',
        'number': '',
    }

    # Pin type is the second element
    if len(pin_expr) > 1 and isinstance(pin_expr[1], sexpdata.Symbol):
        pin_info['type'] = pin_expr[1].value()

    # Pin style is the third element
    if len(pin_expr) > 2 and isinstance(pin_expr[2], sexpdata.Symbol):
        pin_info['style'] = pin_expr[2].value()

    # Parse (at X Y ANGLE)
    at_expr = _find_subexpr(pin_expr, 'at')
    if at_expr:
        pin_info['x'], pin_info['y'], pin_info['angle'] = _parse_coords(at_expr)

    # Parse (length L)
    length_expr = _find_subexpr(pin_expr, 'length')
    if length_expr and len(length_expr) > 1:
        pin_info['length'] = float(length_expr[1])

    # Parse (name "N")
    name_expr = _find_subexpr(pin_expr, 'name')
    if name_expr and len(name_expr) > 1:
        pin_info['name'] = _atom_to_str(name_expr[1])

    # Parse (number "X")
    number_expr = _find_subexpr(pin_expr, 'number')
    if number_expr and len(number_expr) > 1:
        pin_info['number'] = _atom_to_str(number_expr[1])

    return pin_info


def _collect_graphical_elements(symbol_expr: list) -> Dict[str, List]:
    """Extract all graphical elements from a symbol S-expression."""
    elements = {
        'rectangles': [],
        'circles': [],
        'polylines': [],
        'arcs': [],
        'pins': [],
    }

    def _walk(node: Any) -> None:
        if not isinstance(node, list) or not node:
            return
        head = node[0]
        if not isinstance(head, sexpdata.Symbol):
            for child in node:
                _walk(child)
            return

        name = head.value()

        if name == 'rectangle':
            elements['rectangles'].append(_parse_rectangle(node))
        elif name == 'circle':
            elements['circles'].append(_parse_circle(node))
        elif name == 'polyline':
            elements['polylines'].append(_parse_polyline_pts(node))
        elif name == 'arc':
            arc_data = _parse_arc(node)
            if arc_data:
                elements['arcs'].append(arc_data)
        elif name == 'pin':
            elements['pins'].append(_parse_pin(node))
        elif name == 'symbol':
            # Recurse into sub-symbols (units)
            for child in node[2:]:  # Skip head and name
                _walk(child)
        else:
            for child in node:
                _walk(child)

    _walk(symbol_expr)
    return elements


def _extract_symbol_name(symbol_expr: list) -> str:
    """Extract the symbol name from the S-expression."""
    if len(symbol_expr) >= 2:
        return _atom_to_str(symbol_expr[1])
    return "Unknown"


def _draw_pin(ax: Axes, pin: Dict[str, Any], scale: float = 1.0) -> None:
    """Draw a single pin on the axes."""
    x, y = pin['x'], pin['y']
    angle = pin['angle']
    length = pin['length']
    pin_type = pin['type']
    pin_name = pin['name']
    pin_number = pin['number']
    style = pin['style']

    color = PIN_TYPE_COLORS.get(pin_type, '#9900CC')

    # Calculate pin end position
    angle_rad = math.radians(angle)
    dx = length * math.cos(angle_rad)
    dy = length * math.sin(angle_rad)
    end_x = x + dx
    end_y = y + dy

    # Draw pin line
    ax.plot([x, end_x], [y, end_y], color=color, linewidth=1.5, solid_capstyle='round')

    # Draw connection point marker (small dot at the wire connection point)
    ax.plot(x, y, 'o', color=color, markersize=4, markerfacecolor='white', markeredgewidth=1.5)

    # Draw inverted circle if style is inverted
    if style == 'inverted':
        circle_radius = 0.5
        circle_cx = x + circle_radius * math.cos(angle_rad)
        circle_cy = y + circle_radius * math.sin(angle_rad)
        circle = plt.Circle((circle_cx, circle_cy), circle_radius,
                             fill=False, color=color, linewidth=1.0)
        ax.add_patch(circle)

    # Draw clock symbol if style is clock
    if style == 'clock':
        # Small triangle pointing into pin
        tri_size = 0.8
        perp_angle = angle_rad + math.pi / 2
        # Points for the clock triangle
        ax.plot([x, x + tri_size * math.cos(perp_angle)],
                [y, y + tri_size * math.sin(perp_angle)],
                color=color, linewidth=1.0)
        ax.plot([x, x - tri_size * math.cos(perp_angle)],
                [y, y - tri_size * math.sin(perp_angle)],
                color=color, linewidth=1.0)

    # Position labels based on pin angle
    # Pin name goes inside (towards symbol body)
    # Pin number goes outside (at pin end)
    name_offset = 1.0
    num_offset = 0.5

    # Determine text alignment based on angle
    if abs(angle) < 45:  # Pointing right
        name_ha, name_va = 'left', 'center'
        num_ha, num_va = 'center', 'bottom'
        name_x = end_x + name_offset
        name_y = end_y
        num_x = x
        num_y = y - num_offset
    elif abs(angle - 180) < 45 or abs(angle + 180) < 45:  # Pointing left
        name_ha, name_va = 'right', 'center'
        num_ha, num_va = 'center', 'bottom'
        name_x = end_x - name_offset
        name_y = end_y
        num_x = x
        num_y = y - num_offset
    elif abs(angle - 90) < 45:  # Pointing up
        name_ha, name_va = 'center', 'bottom'
        num_ha, num_va = 'left', 'center'
        name_x = end_x
        name_y = end_y + name_offset
        num_x = x + num_offset
        num_y = y
    else:  # Pointing down
        name_ha, name_va = 'center', 'top'
        num_ha, num_va = 'left', 'center'
        name_x = end_x
        name_y = end_y - name_offset
        num_x = x + num_offset
        num_y = y

    # Draw pin name
    if pin_name and pin_name != '~':
        ax.text(name_x, name_y, pin_name, fontsize=7 * scale,
                ha=name_ha, va=name_va, color='black')

    # Draw pin number at the connection point
    if pin_number:
        ax.text(num_x, num_y, pin_number, fontsize=6 * scale,
                ha=num_ha, va=num_va, color='#666666')


def _draw_symbol(ax: Axes, elements: Dict[str, List],
                 symbol_name: str = "", scale: float = 1.0) -> None:
    """Draw all symbol elements on the axes."""

    # Draw rectangles (symbol body)
    for ((x1, y1), (x2, y2)) in elements['rectangles']:
        width = x2 - x1
        height = y2 - y1
        rect = mpatches.Rectangle((x1, y1), width, height,
                                   linewidth=2, edgecolor='#663300',
                                   facecolor='#FFFFCC', fill=True)
        ax.add_patch(rect)

    # Draw circles
    for ((cx, cy), radius) in elements['circles']:
        if radius > 0.5:  # Large circles are symbol body parts
            circle = plt.Circle((cx, cy), radius,
                                 linewidth=2, edgecolor='#663300',
                                 facecolor='#FFFFCC', fill=True)
        else:  # Small circles are pin 1 indicators
            circle = plt.Circle((cx, cy), radius,
                                 linewidth=1, edgecolor='#663300',
                                 facecolor='none', fill=False)
        ax.add_patch(circle)

    # Draw polylines
    for points in elements['polylines']:
        if len(points) >= 2:
            xs = [p[0] for p in points]
            ys = [p[1] for p in points]
            ax.plot(xs, ys, color='#663300', linewidth=1.5)

    # Draw arcs (simplified - draw as polylines through start, mid, end)
    for arc in elements['arcs']:
        pts = [arc['start'], arc['mid'], arc['end']]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        ax.plot(xs, ys, color='#663300', linewidth=1.5)

    # Draw pins
    for pin in elements['pins']:
        _draw_pin(ax, pin, scale)

    # Set title
    if symbol_name:
        ax.set_title(symbol_name, fontsize=12, fontweight='bold')


def visualize_symbol(
    sexp: str,
    figsize: Tuple[float, float] = (10, 8),
    show: bool = True,
    ax: Optional[Axes] = None,
) -> Figure:
    """
    Visualize a KiCad symbol from its S-expression string.

    Args:
        sexp: The symbol S-expression as a string.
        figsize: Figure size in inches (width, height).
        show: If True, display the figure immediately (for Jupyter).
        ax: Optional axes to draw on. If None, creates new figure.

    Returns:
        matplotlib Figure object.

    Example:
        >>> fig = visualize_symbol(sexp_text)
    """
    # Parse S-expression
    symbol_expr = sexpdata.loads(sexp)

    # Extract name
    symbol_name = _extract_symbol_name(symbol_expr)

    # Collect graphical elements
    elements = _collect_graphical_elements(symbol_expr)

    # Create figure if needed
    if ax is None:
        fig, ax = plt.subplots(figsize=figsize)
    else:
        fig = ax.get_figure()

    # Draw the symbol
    _draw_symbol(ax, elements, symbol_name)

    # Calculate bounds for setting axis limits
    all_x = []
    all_y = []

    for ((x1, y1), (x2, y2)) in elements['rectangles']:
        all_x.extend([x1, x2])
        all_y.extend([y1, y2])

    for ((cx, cy), radius) in elements['circles']:
        all_x.extend([cx - radius, cx + radius])
        all_y.extend([cy - radius, cy + radius])

    for points in elements['polylines']:
        all_x.extend([p[0] for p in points])
        all_y.extend([p[1] for p in points])

    for pin in elements['pins']:
        # Include pin endpoint
        angle_rad = math.radians(pin['angle'])
        end_x = pin['x'] + pin['length'] * math.cos(angle_rad)
        end_y = pin['y'] + pin['length'] * math.sin(angle_rad)
        all_x.extend([pin['x'], end_x])
        all_y.extend([pin['y'], end_y])

    if all_x and all_y:
        margin = 5
        ax.set_xlim(min(all_x) - margin, max(all_x) + margin)
        ax.set_ylim(min(all_y) - margin, max(all_y) + margin)

    # Set equal aspect and grid
    ax.set_aspect('equal')
    ax.grid(True, linestyle='--', alpha=0.3)
    ax.axhline(y=0, color='gray', linewidth=0.5, alpha=0.5)
    ax.axvline(x=0, color='gray', linewidth=0.5, alpha=0.5)

    # Add legend for pin types
    legend_elements = []
    pin_types_in_symbol = set(pin['type'] for pin in elements['pins'])
    for pin_type in pin_types_in_symbol:
        color = PIN_TYPE_COLORS.get(pin_type, '#9900CC')
        legend_elements.append(Line2D([0], [0], color=color, linewidth=2,
                                       label=pin_type.replace('_', ' ')))

    if legend_elements:
        ax.legend(handles=legend_elements, loc='upper right', fontsize=8)

    plt.tight_layout()

    if show:
        plt.show()

    return fig


def visualize_by_mpn(
    mpn: str,
    library: str,
    db_path: Optional[Path] = None,
    figsize: Tuple[float, float] = (10, 8),
    show: bool = True,
) -> Figure:
    """
    Look up and visualize a symbol by MPN and library from the database.

    Args:
        mpn: Manufacturer part number / symbol name to look up.
        library: Library name (required - same MPN can exist in different libraries).
        db_path: Path to the symbols database. Defaults to symbol_lib/kicad_symbols.sqlite3.
        figsize: Figure size in inches.
        show: If True, display immediately.

    Returns:
        matplotlib Figure object.

    Example:
        >>> fig = visualize_by_mpn("HX711", "ADC_DAC_Data_Conversion")
    """
    if db_path is None:
        db_path = DEFAULT_SYMBOL_DB

    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute(
            "SELECT mpn, library, sexp FROM symbol_index WHERE mpn = ? AND library = ?",
            (mpn, library)
        )

        row = cursor.fetchone()
        if row is None:
            raise ValueError(f"Symbol '{library}/{mpn}' not found in database")

        _, lib_name, sexp = row

        # Call with show=False to avoid double display, we'll handle show after updating title
        fig = visualize_symbol(sexp, figsize=figsize, show=False)

        # Update title to include library
        ax = fig.axes[0]
        ax.set_title(f"{mpn} (from {lib_name})", fontsize=12, fontweight='bold')

        if show:
            plt.show()

        return fig
    finally:
        conn.close()


def compare_symbols(
    sexp1: str,
    sexp2: str,
    title1: str = "Symbol 1",
    title2: str = "Symbol 2",
    figsize: Tuple[float, float] = (16, 8),
    show: bool = True,
) -> Figure:
    """
    Display two symbols side by side for comparison.

    Args:
        sexp1: First symbol S-expression.
        sexp2: Second symbol S-expression.
        title1: Title for the first symbol.
        title2: Title for the second symbol.
        figsize: Figure size in inches.
        show: If True, display immediately.

    Returns:
        matplotlib Figure object.

    Example:
        >>> fig = compare_symbols(sexp_original, sexp_modified)
    """
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=figsize)

    # Parse and draw first symbol
    expr1 = sexpdata.loads(sexp1)
    elements1 = _collect_graphical_elements(expr1)
    name1 = _extract_symbol_name(expr1)
    _draw_symbol(ax1, elements1, title1 or name1)

    # Parse and draw second symbol
    expr2 = sexpdata.loads(sexp2)
    elements2 = _collect_graphical_elements(expr2)
    name2 = _extract_symbol_name(expr2)
    _draw_symbol(ax2, elements2, title2 or name2)

    # Set bounds for both axes
    for ax, elements in [(ax1, elements1), (ax2, elements2)]:
        all_x, all_y = [], []
        for ((x1, y1), (x2, y2)) in elements['rectangles']:
            all_x.extend([x1, x2])
            all_y.extend([y1, y2])
        for ((cx, cy), radius) in elements['circles']:
            all_x.extend([cx - radius, cx + radius])
            all_y.extend([cy - radius, cy + radius])
        for pin in elements['pins']:
            angle_rad = math.radians(pin['angle'])
            end_x = pin['x'] + pin['length'] * math.cos(angle_rad)
            end_y = pin['y'] + pin['length'] * math.sin(angle_rad)
            all_x.extend([pin['x'], end_x])
            all_y.extend([pin['y'], end_y])

        if all_x and all_y:
            margin = 5
            ax.set_xlim(min(all_x) - margin, max(all_x) + margin)
            ax.set_ylim(min(all_y) - margin, max(all_y) + margin)

        ax.set_aspect('equal')
        ax.grid(True, linestyle='--', alpha=0.3)

    plt.tight_layout()

    if show:
        plt.show()

    return fig


def list_symbols_by_prefix(
    prefix: str,
    db_path: Optional[Path] = None,
    limit: int = 20,
) -> List[Dict[str, str]]:
    """
    Search for symbols by MPN prefix in the database.

    Args:
        prefix: MPN prefix to search for.
        db_path: Path to the symbols database.
        limit: Maximum number of results.

    Returns:
        List of dicts with 'mpn' and 'library' keys.

    Example:
        >>> matches = list_symbols_by_prefix("STM32")
        >>> for m in matches:
        ...     print(f"{m['library']}/{m['mpn']}")
    """
    if db_path is None:
        db_path = DEFAULT_SYMBOL_DB

    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute(
            "SELECT mpn, library FROM symbol_index WHERE mpn LIKE ? LIMIT ?",
            (f"{prefix}%", limit)
        )
        return [{'mpn': row[0], 'library': row[1]} for row in cursor.fetchall()]
    finally:
        conn.close()


# Convenient aliases for Jupyter notebook usage
viz = visualize_symbol
viz_mpn = visualize_by_mpn
