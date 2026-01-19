"""
IC symbol builder - rectangular box with pins on sides.

This handles the most common symbol type: ICs, modules, and any
component that is represented as a rectangular box with pins.
"""

from typing import Dict, Any, List

from .base import (
    format_float, build_property, build_pin, build_rectangle,
    build_circle, wrap_symbol, get_common_properties, snap_to_grid,
    ceil_to_grid, estimate_text_width_mm, DEFAULT_PIN_FONT_SIZE, DEFAULT_PIN_NAME_OFFSET
)


def build_ic_symbol(params: Dict[str, Any]) -> str:
    """
    Build an IC (rectangular box) symbol.
    
    Args:
        params: Dictionary containing:
            - name (str): Symbol name (required)
            - pins (list): List of pin definitions, each with:
                - name (str): Pin name
                - number (str): Pin number
                - type (str): Pin electrical type (input, output, power_in, etc.)
                - orientation (str): left, right, up, down (optional, auto-assigned)
            - body_width (float): Minimum box width (default: 10.0, may auto-grow)
            - body_height (float): Box height (default: auto-calculated)
            - pin_names_offset (float): Global pin name offset (default: 1.016)
            - footprint (str): Footprint reference
            - properties (dict): Additional properties
    
    Returns:
        S-expression string
    """
    name = params.get("name")
    if not name:
        raise KeyError("'name' is required for IC symbol")
    
    pins_input = params.get("pins", [])
    min_body_width = float(params.get("body_width", 10.0))
    body_height = params.get("body_height")
    pin_names_offset = float(params.get("pin_names_offset", DEFAULT_PIN_NAME_OFFSET))
    pin_name_font_size = float(params.get("pin_name_font_size", DEFAULT_PIN_FONT_SIZE))
    
    # Process pins - assign orientations if not specified
    pins = _process_pins(pins_input)
    
    # Count pins per side
    left_pins = [p for p in pins if p["orientation"] == "left"]
    right_pins = [p for p in pins if p["orientation"] == "right"]
    top_pins = [p for p in pins if p["orientation"] == "up"]
    bottom_pins = [p for p in pins if p["orientation"] == "down"]
    
    # Calculate body dimensions
    pin_spacing = 2.54
    margin = pin_spacing  # 2.54mm margin beyond outermost pins
    pin_length = 2.54

    # Calculate pin start positions (these get snapped to grid)
    max_vertical = max(len(left_pins), len(right_pins), 1)
    max_horizontal = max(len(top_pins), len(bottom_pins), 1)

    vert_start_y = snap_to_grid((max_vertical - 1) * pin_spacing / 2)
    horiz_start_x = snap_to_grid((max_horizontal - 1) * pin_spacing / 2)

    # Body half dimensions = outermost pin position + margin
    # Since start positions are on grid and margin is on grid, result is on grid
    if body_height is None:
        half_h = vert_start_y + margin
    else:
        half_h = snap_to_grid(body_height / 2)

    # Determine minimum width constraints (always grid-aligned, never rounded down)
    min_half_w_user = ceil_to_grid(min_body_width / 2)
    min_half_w_pins = horiz_start_x + margin if (top_pins or bottom_pins) else 0.0
    min_half_w_text = _estimate_min_half_width_for_pin_names(
        left_pins=left_pins,
        right_pins=right_pins,
        pin_name_font_size=pin_name_font_size,
        pin_names_offset=pin_names_offset,
        pin_spacing=pin_spacing,
    )

    # Keep a reasonable aspect ratio so high pin-count symbols aren't extremely narrow
    default_max_aspect_ratio = 2.5 if (left_pins and right_pins) else 4.0
    max_aspect_ratio = float(params.get("max_aspect_ratio", default_max_aspect_ratio))
    min_half_w_aspect = ceil_to_grid(half_h / max_aspect_ratio) if max_aspect_ratio > 0 else 0.0

    half_w = max(min_half_w_user, min_half_w_pins, min_half_w_text, min_half_w_aspect)
    
    graphics = []
    
    # Body rectangle
    graphics.append(build_rectangle(-half_w, half_h, half_w, -half_h))
    
    # Pin 1 indicator (small circle)
    if pins:
        graphics.append(build_circle(-half_w + 1.27, half_h - 1.27, 0.38))
    
    # Build pins
    # In KiCAD, pin rotation indicates direction the pin points TOWARD (toward body center)
    # Left side pins: connection point is left of body, pin points RIGHT (rotation=0)
    # Snap start position to grid to ensure all pins land on grid
    left_start_y = snap_to_grid((len(left_pins) - 1) * pin_spacing / 2) if left_pins else 0
    for i, pin in enumerate(left_pins):
        x = -half_w - pin_length
        y = left_start_y - i * pin_spacing
        graphics.append(build_pin(
            pin.get("type", "passive"),
            pin["name"],
            pin["number"],
            x, y, rotation=0,
            length=pin_length
        ))

    # Right side pins: connection point is right of body, pin points LEFT (rotation=180)
    right_start_y = snap_to_grid((len(right_pins) - 1) * pin_spacing / 2) if right_pins else 0
    for i, pin in enumerate(right_pins):
        x = half_w + pin_length
        y = right_start_y - i * pin_spacing
        graphics.append(build_pin(
            pin.get("type", "passive"),
            pin["name"],
            pin["number"],
            x, y, rotation=180,
            length=pin_length
        ))

    # Top side pins: connection point is above body, pin points DOWN (rotation=270)
    top_start_x = snap_to_grid(-((len(top_pins) - 1) * pin_spacing / 2)) if top_pins else 0
    for i, pin in enumerate(top_pins):
        x = top_start_x + i * pin_spacing
        y = half_h + pin_length
        graphics.append(build_pin(
            pin.get("type", "passive"),
            pin["name"],
            pin["number"],
            x, y, rotation=270,
            length=pin_length
        ))

    # Bottom side pins: connection point is below body, pin points UP (rotation=90)
    bottom_start_x = snap_to_grid(-((len(bottom_pins) - 1) * pin_spacing / 2)) if bottom_pins else 0
    for i, pin in enumerate(bottom_pins):
        x = bottom_start_x + i * pin_spacing
        y = -half_h - pin_length
        graphics.append(build_pin(
            pin.get("type", "passive"),
            pin["name"],
            pin["number"],
            x, y, rotation=90,
            length=pin_length
        ))
    
    # Build properties
    properties = get_common_properties(params, name, default_reference="U")
    
    return wrap_symbol(name, graphics, properties, pin_names_offset=pin_names_offset)


def _estimate_min_half_width_for_pin_names(
    left_pins: List[Dict[str, Any]],
    right_pins: List[Dict[str, Any]],
    pin_name_font_size: float,
    pin_names_offset: float,
    pin_spacing: float,
) -> float:
    left_max = max(
        (estimate_text_width_mm(p.get("name", ""), pin_name_font_size) for p in left_pins),
        default=0.0,
    )
    right_max = max(
        (estimate_text_width_mm(p.get("name", ""), pin_name_font_size) for p in right_pins),
        default=0.0,
    )

    if left_pins and right_pins:
        clearance = pin_spacing
        return ceil_to_grid((left_max + right_max + clearance) / 2 + pin_names_offset)

    max_side = max(left_max, right_max)
    if max_side <= 0:
        return 0.0
    return ceil_to_grid((max_side / 2) + pin_names_offset)


def _process_pins(pins_input: List[Dict]) -> List[Dict]:
    """
    Process pin list, assigning orientations if not specified.
    
    Default behavior: first half on left, second half on right.
    """
    pins = []
    total = len(pins_input)
    
    for i, pin in enumerate(pins_input):
        pin_copy = dict(pin)
        
        # Ensure required fields
        if "name" not in pin_copy:
            pin_copy["name"] = f"PIN{i+1}"
        if "number" not in pin_copy:
            pin_copy["number"] = str(i + 1)
        
        # Assign orientation if not specified
        if "orientation" not in pin_copy:
            pin_copy["orientation"] = "left" if i < total / 2 else "right"
        
        # Normalize orientation
        orientation = pin_copy["orientation"].lower()
        if orientation in ("l", "left"):
            pin_copy["orientation"] = "left"
        elif orientation in ("r", "right"):
            pin_copy["orientation"] = "right"
        elif orientation in ("u", "up", "top"):
            pin_copy["orientation"] = "up"
        elif orientation in ("d", "down", "bottom"):
            pin_copy["orientation"] = "down"
        
        pins.append(pin_copy)
    
    return pins
