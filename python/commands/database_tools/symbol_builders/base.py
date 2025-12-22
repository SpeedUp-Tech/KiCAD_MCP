"""
Base utilities and shared functions for symbol builders.
"""

from typing import Dict, Any, List, Optional

# KiCad schematic grid size
GRID_SIZE = 2.54


def snap_to_grid(value: float, grid: float = GRID_SIZE) -> float:
    """Snap a value to the nearest grid point."""
    return round(value / grid) * grid


def format_float(value: float) -> str:
    """Format float to KiCad-friendly string (no trailing zeros)."""
    if isinstance(value, float):
        return f"{value:.4f}".rstrip('0').rstrip('.')
    return str(value)


def build_property(name: str, value: str, x: float = 0, y: float = 0, 
                   rotation: float = 0, font_size: float = 1.27, 
                   hide: bool = False, prop_id: Optional[int] = None) -> str:
    """
    Build a property S-expression.
    
    Args:
        name: Property name (Reference, Value, Footprint, etc.)
        value: Property value
        x, y: Position
        rotation: Rotation angle
        font_size: Font size
        hide: Whether to hide the property
        prop_id: Optional property ID
    """
    id_str = f" (id {prop_id})" if prop_id is not None else ""
    hide_str = " hide" if hide else ""
    
    return (
        f'(property "{name}" "{value}"{id_str} '
        f'(at {format_float(x)} {format_float(y)} {int(rotation)}) '
        f'(effects (font (size {format_float(font_size)} {format_float(font_size)})){hide_str}))'
    )


def build_pin(pin_type: str, name: str, number: str,
              x: float, y: float, rotation: int = 0,
              length: float = 2.54, shape: str = "line",
              font_size: float = 1.27, hide_name: bool = False,
              hide_number: bool = False) -> str:
    """
    Build a pin S-expression.

    Args:
        pin_type: Pin electrical type (input, output, bidirectional, passive, power_in, etc.)
        name: Pin name
        number: Pin number
        x, y: Pin position - this is where wires connect (outside the symbol body)
        rotation: Direction the pin points TOWARD (toward body center):
                  0=points right, 90=points up, 180=points left, 270=points down
                  For left-side pins use 0, for right-side pins use 180,
                  for top pins use 270, for bottom pins use 90.
        length: Pin length (extends from connection point toward body)
        shape: Pin shape (line, inverted, clock, etc.)
        font_size: Font size for name/number
        hide_name: Whether to hide pin name
        hide_number: Whether to hide pin number
    """
    name_effects = f'(effects (font (size {format_float(font_size)} {format_float(font_size)})))'
    number_effects = f'(effects (font (size {format_float(font_size)} {format_float(font_size)})))'
    
    return (
        f'(pin {pin_type} {shape} '
        f'(at {format_float(x)} {format_float(y)} {rotation}) '
        f'(length {format_float(length)}) '
        f'(name "{name}" {name_effects}) '
        f'(number "{number}" {number_effects}))'
    )


def build_rectangle(x1: float, y1: float, x2: float, y2: float,
                    stroke_width: float = 0, fill: str = "background") -> str:
    """Build a rectangle S-expression."""
    return (
        f'(rectangle (start {format_float(x1)} {format_float(y1)}) '
        f'(end {format_float(x2)} {format_float(y2)}) '
        f'(stroke (width {format_float(stroke_width)}) (type default) (color 0 0 0 0)) '
        f'(fill (type {fill})))'
    )


def build_polyline(points: List[tuple], stroke_width: float = 0, 
                   fill: str = "none") -> str:
    """
    Build a polyline S-expression.
    
    Args:
        points: List of (x, y) tuples
        stroke_width: Line width
        fill: Fill type (none, outline, background)
    """
    pts_str = " ".join(f"(xy {format_float(x)} {format_float(y)})" for x, y in points)
    return (
        f'(polyline (pts {pts_str}) '
        f'(stroke (width {format_float(stroke_width)}) (type default) (color 0 0 0 0)) '
        f'(fill (type {fill})))'
    )


def build_circle(cx: float, cy: float, radius: float,
                 stroke_width: float = 0, fill: str = "none") -> str:
    """Build a circle S-expression."""
    return (
        f'(circle (center {format_float(cx)} {format_float(cy)}) '
        f'(radius {format_float(radius)}) '
        f'(stroke (width {format_float(stroke_width)}) (type default) (color 0 0 0 0)) '
        f'(fill (type {fill})))'
    )


def build_arc(start: tuple, mid: tuple, end: tuple,
              stroke_width: float = 0, fill: str = "none") -> str:
    """Build an arc S-expression."""
    return (
        f'(arc (start {format_float(start[0])} {format_float(start[1])}) '
        f'(mid {format_float(mid[0])} {format_float(mid[1])}) '
        f'(end {format_float(end[0])} {format_float(end[1])}) '
        f'(stroke (width {format_float(stroke_width)}) (type default) (color 0 0 0 0)) '
        f'(fill (type {fill})))'
    )


def wrap_symbol(name: str, graphics: List[str], properties: List[str],
                in_bom: bool = True, on_board: bool = True,
                pin_names_offset: Optional[float] = None,
                hide_pin_names: bool = False,
                hide_pin_numbers: bool = False) -> str:
    """
    Wrap graphics and properties into a complete symbol S-expression.
    
    Args:
        name: Symbol name (library-qualified, e.g., "MyLib:PartName")
        graphics: List of graphic element S-expressions (polylines, pins, etc.)
        properties: List of property S-expressions
        in_bom: Include in BOM
        on_board: Include on board
        pin_names_offset: Pin name offset, None for default
        hide_pin_names: Hide all pin names
        hide_pin_numbers: Hide all pin numbers
    """
    lines = [f'(symbol "{name}"']
    
    # Pin name settings
    if hide_pin_names:
        lines.append('  (pin_names hide)')
    elif pin_names_offset is not None:
        lines.append(f'  (pin_names (offset {format_float(pin_names_offset)}))')
    
    if hide_pin_numbers:
        lines.append('  (pin_numbers hide)')
    
    lines.append(f'  (in_bom {"yes" if in_bom else "no"})')
    lines.append(f'  (on_board {"yes" if on_board else "no"})')
    
    # Properties
    for prop in properties:
        lines.append(f'  {prop}')
    
    # Graphics unit
    lines.append(f'  (symbol "{name}_0_1"')
    for graphic in graphics:
        lines.append(f'    {graphic}')
    lines.append('  )')
    
    lines.append(')')
    
    return '\n'.join(lines)


def get_common_properties(params: Dict[str, Any], symbol_name: str,
                          default_reference: str = "U") -> List[str]:
    """
    Build common properties (Reference, Value, Footprint, Datasheet) from params.
    
    Args:
        params: Parameter dict with optional 'properties' key
        symbol_name: Symbol name for Value property default
        default_reference: Default reference designator
    """
    props = params.get("properties", {})
    
    reference = props.get("reference", default_reference)
    value = props.get("value", symbol_name.split(":")[-1] if ":" in symbol_name else symbol_name)
    footprint = props.get("footprint", params.get("footprint", ""))
    datasheet = props.get("datasheet", "")
    
    result = [
        build_property("Reference", reference, y=5, prop_id=0),
        build_property("Value", value, y=-5, prop_id=1),
        build_property("Footprint", footprint, y=-7, font_size=1.0, hide=True, prop_id=2),
        build_property("Datasheet", datasheet, y=-9, font_size=1.0, hide=True, prop_id=3),
    ]
    
    # Add custom properties
    prop_id = 4
    for key, val in props.items():
        if key.lower() not in ("reference", "value", "footprint", "datasheet"):
            result.append(build_property(key, str(val), hide=True, prop_id=prop_id))
            prop_id += 1
    
    return result
