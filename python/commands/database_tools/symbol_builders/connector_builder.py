"""
Connector symbol builder - N-pin vertical connectors.
"""

from typing import Dict, Any, List

from .base import (
    format_float, build_pin, build_polyline, build_rectangle,
    wrap_symbol, get_common_properties, snap_to_grid
)


def build_connector_symbol(params: Dict[str, Any]) -> str:
    """
    Build a connector symbol with N pins arranged vertically.
    
    Args:
        params: Dictionary containing:
            - name (str): Symbol name (required)
            - pin_count (int): Number of pins (required if pins not specified)
            - pins (list): Optional list of pin definitions with name/number
            - orientation (str): "left" or "right" - side where pins exit (default: "right")
            - footprint (str): Footprint reference
            - properties (dict): Additional properties
    """
    name = params.get("name")
    if not name:
        raise KeyError("'name' is required for connector symbol")
    
    # Get pin definitions
    pins_input = params.get("pins", [])
    pin_count = params.get("pin_count", len(pins_input))
    
    if not pin_count:
        raise KeyError("Either 'pins' or 'pin_count' is required")
    
    # Generate default pins if not specified
    if not pins_input:
        pins_input = [{"name": f"Pin_{i+1}", "number": str(i+1)} for i in range(pin_count)]
    
    orientation = params.get("orientation", "right").lower()
    
    pin_spacing = 2.54
    margin = pin_spacing  # 2.54mm margin beyond outermost pins
    body_width = 5.08

    # Calculate pin start position (snapped to grid)
    start_y = snap_to_grid((pin_count - 1) * pin_spacing / 2)

    # Body height based on pin extent plus margin (symmetric around y=0)
    half_height = start_y + margin

    graphics = []

    # Body rectangle
    graphics.append(build_rectangle(-body_width/2, half_height, body_width/2, -half_height))

    for i, pin_def in enumerate(pins_input):
        y = start_y - i * pin_spacing
        pin_name = pin_def.get("name", f"Pin_{i+1}")
        pin_number = pin_def.get("number", str(i+1))
        pin_type = pin_def.get("type", "passive")
        
        # Small rectangle as pin marker inside body
        if orientation == "right":
            marker_x = body_width/2 - 0.635
            graphics.append(build_rectangle(marker_x - 0.635, y + 0.381, marker_x + 0.635, y - 0.381))
            # Pin on right side
            graphics.append(build_pin(pin_type, pin_name, pin_number, 
                                      body_width/2 + 2.54, y, rotation=180, length=2.54))
        else:
            marker_x = -body_width/2 + 0.635
            graphics.append(build_rectangle(marker_x - 0.635, y + 0.381, marker_x + 0.635, y - 0.381))
            # Pin on left side
            graphics.append(build_pin(pin_type, pin_name, pin_number,
                                      -body_width/2 - 2.54, y, rotation=0, length=2.54))
    
    properties = get_common_properties(params, name, default_reference="J")
    
    return wrap_symbol(name, graphics, properties, pin_names_offset=1.016)
