"""
IC symbol builder - rectangular box with pins on sides.

This handles the most common symbol type: ICs, modules, and any
component that is represented as a rectangular box with pins.
"""

from typing import Dict, Any, List

from .base import (
    format_float, build_property, build_pin, build_rectangle,
    build_circle, wrap_symbol, get_common_properties, snap_to_grid
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
            - body_width (float): Box width (default: 10.0)
            - body_height (float): Box height (default: auto-calculated)
            - footprint (str): Footprint reference
            - properties (dict): Additional properties
    
    Returns:
        S-expression string
    """
    name = params.get("name")
    if not name:
        raise KeyError("'name' is required for IC symbol")
    
    pins_input = params.get("pins", [])
    body_width = float(params.get("body_width", 10.0))
    body_height = params.get("body_height")
    
    # Process pins - assign orientations if not specified
    pins = _process_pins(pins_input)
    
    # Count pins per side
    left_pins = [p for p in pins if p["orientation"] == "left"]
    right_pins = [p for p in pins if p["orientation"] == "right"]
    top_pins = [p for p in pins if p["orientation"] == "up"]
    bottom_pins = [p for p in pins if p["orientation"] == "down"]
    
    # Calculate body height if not specified
    pin_spacing = 2.54
    if body_height is None:
        max_vertical = max(len(left_pins), len(right_pins), 1)
        body_height = max(5.08, max_vertical * pin_spacing + pin_spacing)
    
    # Adjust body width for top/bottom pins
    if top_pins or bottom_pins:
        max_horizontal = max(len(top_pins), len(bottom_pins), 1)
        min_width = max_horizontal * pin_spacing + pin_spacing
        body_width = max(body_width, min_width)
    
    # Snap body dimensions to grid to ensure pin positions are grid-aligned
    # This is critical for schematic wire connections
    half_w = snap_to_grid(body_width / 2)
    half_h = snap_to_grid(body_height / 2)
    pin_length = 2.54
    
    graphics = []
    
    # Body rectangle
    graphics.append(build_rectangle(-half_w, half_h, half_w, -half_h))
    
    # Pin 1 indicator (small circle)
    if pins:
        graphics.append(build_circle(-half_w + 1.27, half_h - 1.27, 0.38))
    
    # Build pins
    # In KiCAD, pin rotation indicates direction the pin points TOWARD (toward body center)
    # Left side pins: connection point is left of body, pin points RIGHT (rotation=0)
    left_start_y = ((len(left_pins) - 1) * pin_spacing / 2) if left_pins else 0
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
    right_start_y = ((len(right_pins) - 1) * pin_spacing / 2) if right_pins else 0
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
    top_start_x = -((len(top_pins) - 1) * pin_spacing / 2) if top_pins else 0
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
    bottom_start_x = -((len(bottom_pins) - 1) * pin_spacing / 2) if bottom_pins else 0
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
    
    return wrap_symbol(name, graphics, properties)


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
