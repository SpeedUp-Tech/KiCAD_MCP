"""
Miscellaneous symbol builders - Optocoupler, Transformer, etc.
"""

from typing import Dict, Any

from .base import (
    build_pin, build_polyline, build_rectangle, build_arc, build_circle,
    wrap_symbol, get_common_properties
)


def build_optocoupler_symbol(params: Dict[str, Any]) -> str:
    """
    Build an optocoupler symbol.
    
    LED on left side, phototransistor on right, with isolation bar.
    
    Args:
        params: Dictionary containing:
            - name (str): Symbol name (required)
            - pins (dict): Pin mapping, e.g., {"A": "1", "K": "2", "C": "3", "E": "4"}
            - footprint (str): Footprint reference
            - properties (dict): Additional properties
    """
    name = params.get("name")
    if not name:
        raise KeyError("'name' is required for optocoupler symbol")
    
    pins_map = params.get("pins", {"A": "1", "K": "2", "C": "3", "E": "4"})
    
    graphics = []
    
    # Outer box
    graphics.append(build_rectangle(-5.08, 5.08, 5.08, -5.08))
    
    # LED (left side) - simplified triangle
    graphics.append(build_polyline([
        (-3.81, 1.27), (-3.81, -1.27), (-1.27, 0), (-3.81, 1.27)
    ], stroke_width=0.254))
    graphics.append(build_polyline([(-1.27, 1.27), (-1.27, -1.27)], stroke_width=0.254))
    
    # Light arrows
    graphics.append(build_polyline([(-1.27, 0.635), (0, 1.27)]))
    graphics.append(build_polyline([(-1.27, -0.635), (0, 0)]))
    
    # Isolation bar (dashed line in center)
    graphics.append(build_polyline([(0, 3.81), (0, -3.81)], stroke_width=0.254))
    
    # Phototransistor (right side) - simplified
    graphics.append(build_polyline([(1.27, 1.27), (1.27, -1.27)], stroke_width=0.508))
    graphics.append(build_polyline([(1.27, 0.635), (3.81, 2.54)]))
    graphics.append(build_polyline([(1.27, -0.635), (3.81, -2.54)]))
    # Arrow on emitter
    graphics.append(build_polyline([
        (2.54, -1.524), (3.048, -2.032), (3.556, -1.524)
    ]))
    
    # Pins
    a_num = pins_map.get("A", pins_map.get("a", "1"))
    k_num = pins_map.get("K", pins_map.get("k", "2"))
    c_num = pins_map.get("C", pins_map.get("c", "3"))
    e_num = pins_map.get("E", pins_map.get("e", "4"))
    
    graphics.append(build_pin("passive", "A", a_num, -7.62, 2.54, rotation=0, length=2.54))
    graphics.append(build_pin("passive", "K", k_num, -7.62, -2.54, rotation=0, length=2.54))
    graphics.append(build_pin("passive", "C", c_num, 7.62, 2.54, rotation=180, length=2.54))
    graphics.append(build_pin("passive", "E", e_num, 7.62, -2.54, rotation=180, length=2.54))
    
    properties = get_common_properties(params, name, default_reference="U")
    
    return wrap_symbol(name, graphics, properties)


def build_transformer_symbol(params: Dict[str, Any]) -> str:
    """
    Build a transformer symbol.
    
    Two coupled coils with core lines.
    
    Args:
        params: Dictionary containing:
            - name (str): Symbol name (required)
            - pins (dict): Pin mapping, e.g., {"P1": "1", "P2": "2", "S1": "3", "S2": "4"}
            - footprint (str): Footprint reference
            - properties (dict): Additional properties
    """
    name = params.get("name")
    if not name:
        raise KeyError("'name' is required for transformer symbol")
    
    pins_map = params.get("pins", {"P1": "1", "P2": "2", "S1": "3", "S2": "4"})
    
    graphics = []
    
    # Primary coil (left side) - 4 arcs
    graphics.append(build_arc((-2.54, 2.54), (-3.175, 1.905), (-2.54, 1.27)))
    graphics.append(build_arc((-2.54, 1.27), (-3.175, 0.635), (-2.54, 0)))
    graphics.append(build_arc((-2.54, 0), (-3.175, -0.635), (-2.54, -1.27)))
    graphics.append(build_arc((-2.54, -1.27), (-3.175, -1.905), (-2.54, -2.54)))
    
    # Secondary coil (right side) - 4 arcs
    graphics.append(build_arc((2.54, 2.54), (3.175, 1.905), (2.54, 1.27)))
    graphics.append(build_arc((2.54, 1.27), (3.175, 0.635), (2.54, 0)))
    graphics.append(build_arc((2.54, 0), (3.175, -0.635), (2.54, -1.27)))
    graphics.append(build_arc((2.54, -1.27), (3.175, -1.905), (2.54, -2.54)))
    
    # Core lines (two vertical lines in center)
    graphics.append(build_polyline([(-0.508, 3.175), (-0.508, -3.175)], stroke_width=0.254))
    graphics.append(build_polyline([(0.508, 3.175), (0.508, -3.175)], stroke_width=0.254))
    
    # Connection lines
    graphics.append(build_polyline([(-2.54, 2.54), (-2.54, 3.81)]))
    graphics.append(build_polyline([(-2.54, -2.54), (-2.54, -3.81)]))
    graphics.append(build_polyline([(2.54, 2.54), (2.54, 3.81)]))
    graphics.append(build_polyline([(2.54, -2.54), (2.54, -3.81)]))
    
    # Pins
    p1_num = pins_map.get("P1", pins_map.get("p1", "1"))
    p2_num = pins_map.get("P2", pins_map.get("p2", "2"))
    s1_num = pins_map.get("S1", pins_map.get("s1", "3"))
    s2_num = pins_map.get("S2", pins_map.get("s2", "4"))
    
    graphics.append(build_pin("passive", "P1", p1_num, -2.54, 6.35, rotation=270, length=2.54))
    graphics.append(build_pin("passive", "P2", p2_num, -2.54, -6.35, rotation=90, length=2.54))
    graphics.append(build_pin("passive", "S1", s1_num, 2.54, 6.35, rotation=270, length=2.54))
    graphics.append(build_pin("passive", "S2", s2_num, 2.54, -6.35, rotation=90, length=2.54))
    
    properties = get_common_properties(params, name, default_reference="T")
    
    return wrap_symbol(name, graphics, properties)
