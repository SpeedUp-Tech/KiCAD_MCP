"""
Transistor symbol builders - NMOS, PMOS, NPN, PNP.

These use standard schematic symbols with fixed graphics.
"""

from typing import Dict, Any

from .base import (
    format_float, build_pin, build_polyline, build_circle,
    wrap_symbol, get_common_properties
)


def build_nmos_symbol(params: Dict[str, Any]) -> str:
    """
    Build an N-channel MOSFET symbol.
    
    Standard symbol with Gate (left), Drain (top), Source (bottom).
    Includes body diode and enhancement mode indicator.
    
    Args:
        params: Dictionary containing:
            - name (str): Symbol name (required)
            - pins (dict): Pin mapping, e.g., {"G": "1", "D": "2", "S": "3"}
            - footprint (str): Footprint reference
            - properties (dict): Additional properties
    """
    name = params.get("name")
    if not name:
        raise KeyError("'name' is required for NMOS symbol")
    
    pins_map = params.get("pins", {"G": "1", "D": "2", "S": "3"})
    
    graphics = []
    
    # Gate vertical bar
    graphics.append(build_polyline([(0.254, 1.905), (0.254, -1.905)], stroke_width=0.254))
    
    # Gate connection line
    graphics.append(build_polyline([(0.254, 0), (-2.54, 0)]))
    
    # Channel segments (three lines)
    graphics.append(build_polyline([(0.762, 2.286), (0.762, 1.27)], stroke_width=0.254))
    graphics.append(build_polyline([(0.762, 0.508), (0.762, -0.508)], stroke_width=0.254))
    graphics.append(build_polyline([(0.762, -1.27), (0.762, -2.286)], stroke_width=0.254))
    
    # Drain connection
    graphics.append(build_polyline([(0.762, 1.778), (2.54, 1.778), (2.54, 2.54)]))
    
    # Source connection
    graphics.append(build_polyline([(0.762, -1.778), (2.54, -1.778), (2.54, -2.54)]))
    
    # Arrow (enhancement mode indicator) - points inward
    graphics.append(build_polyline([(0.762, 0), (1.778, 0)]))
    graphics.append(build_polyline([
        (1.778, 0.762), (1.778, -0.762), (0.762, 0), (1.778, 0.762)
    ], fill="outline"))
    
    # Body diode
    graphics.append(build_polyline([(2.54, 0.508), (2.54, -0.508)], stroke_width=0.254))
    graphics.append(build_polyline([
        (1.778, -0.508), (2.54, 0.508), (3.302, -0.508), (1.778, -0.508)
    ]))
    
    # Pins: Gate (left), Drain (top), Source (bottom)
    g_num = pins_map.get("G", pins_map.get("g", "1"))
    d_num = pins_map.get("D", pins_map.get("d", "2"))
    s_num = pins_map.get("S", pins_map.get("s", "3"))
    
    graphics.append(build_pin("input", "G", g_num, -5.08, 0, rotation=0, length=2.54))
    graphics.append(build_pin("passive", "D", d_num, 2.54, 5.08, rotation=270, length=2.54))
    graphics.append(build_pin("passive", "S", s_num, 2.54, -5.08, rotation=90, length=2.54))
    
    properties = get_common_properties(params, name, default_reference="Q")
    
    return wrap_symbol(name, graphics, properties, hide_pin_names=True)


def build_pmos_symbol(params: Dict[str, Any]) -> str:
    """
    Build a P-channel MOSFET symbol.
    
    Similar to NMOS but with arrow pointing outward and bubble on gate.
    
    Args:
        params: Same as build_nmos_symbol
    """
    name = params.get("name")
    if not name:
        raise KeyError("'name' is required for PMOS symbol")
    
    pins_map = params.get("pins", {"G": "1", "D": "2", "S": "3"})
    
    graphics = []
    
    # Gate vertical bar
    graphics.append(build_polyline([(0.254, 1.905), (0.254, -1.905)], stroke_width=0.254))
    
    # Gate connection line with bubble
    graphics.append(build_polyline([(-0.508, 0), (-2.54, 0)]))
    graphics.append(build_circle(-0.127, 0, 0.381))  # Bubble
    
    # Channel segments
    graphics.append(build_polyline([(0.762, 2.286), (0.762, 1.27)], stroke_width=0.254))
    graphics.append(build_polyline([(0.762, 0.508), (0.762, -0.508)], stroke_width=0.254))
    graphics.append(build_polyline([(0.762, -1.27), (0.762, -2.286)], stroke_width=0.254))
    
    # Source connection (top for PMOS)
    graphics.append(build_polyline([(0.762, 1.778), (2.54, 1.778), (2.54, 2.54)]))
    
    # Drain connection (bottom for PMOS)
    graphics.append(build_polyline([(0.762, -1.778), (2.54, -1.778), (2.54, -2.54)]))
    
    # Arrow pointing outward (from channel)
    graphics.append(build_polyline([(0.762, 0), (1.778, 0)]))
    graphics.append(build_polyline([
        (0.762, 0.762), (0.762, -0.762), (1.778, 0), (0.762, 0.762)
    ], fill="outline"))
    
    # Body diode (reversed)
    graphics.append(build_polyline([(2.54, 0.508), (2.54, -0.508)], stroke_width=0.254))
    graphics.append(build_polyline([
        (1.778, 0.508), (2.54, -0.508), (3.302, 0.508), (1.778, 0.508)
    ]))
    
    # Pins
    g_num = pins_map.get("G", pins_map.get("g", "1"))
    s_num = pins_map.get("S", pins_map.get("s", "2"))
    d_num = pins_map.get("D", pins_map.get("d", "3"))
    
    graphics.append(build_pin("input", "G", g_num, -5.08, 0, rotation=0, length=2.54))
    graphics.append(build_pin("passive", "S", s_num, 2.54, 5.08, rotation=270, length=2.54))
    graphics.append(build_pin("passive", "D", d_num, 2.54, -5.08, rotation=90, length=2.54))
    
    properties = get_common_properties(params, name, default_reference="Q")
    
    return wrap_symbol(name, graphics, properties, hide_pin_names=True)


def build_npn_symbol(params: Dict[str, Any]) -> str:
    """
    Build an NPN BJT symbol.
    
    Standard symbol with Base (left), Collector (top), Emitter (bottom).
    
    Args:
        params: Dictionary containing:
            - name (str): Symbol name (required)
            - pins (dict): Pin mapping, e.g., {"B": "1", "C": "2", "E": "3"}
            - footprint (str): Footprint reference
            - properties (dict): Additional properties
    """
    name = params.get("name")
    if not name:
        raise KeyError("'name' is required for NPN symbol")
    
    pins_map = params.get("pins", {"B": "1", "C": "2", "E": "3"})
    
    graphics = []
    
    # Base vertical bar
    graphics.append(build_polyline([(0.635, 1.27), (0.635, -1.27)], stroke_width=0.508))
    
    # Base connection
    graphics.append(build_polyline([(0.635, 0), (-2.54, 0)]))
    
    # Collector line
    graphics.append(build_polyline([(0.635, 0.635), (2.54, 2.54)]))
    
    # Emitter line with arrow
    graphics.append(build_polyline([(0.635, -0.635), (2.54, -2.54)]))
    graphics.append(build_polyline([
        (1.27, -1.778), (1.778, -1.27), (2.286, -2.286), (1.27, -1.778)
    ], fill="outline"))
    
    # Circle (optional body outline)
    graphics.append(build_circle(1.27, 0, 2.54, stroke_width=0.254))
    
    # Pins
    b_num = pins_map.get("B", pins_map.get("b", "1"))
    c_num = pins_map.get("C", pins_map.get("c", "2"))
    e_num = pins_map.get("E", pins_map.get("e", "3"))
    
    graphics.append(build_pin("input", "B", b_num, -5.08, 0, rotation=0, length=2.54))
    graphics.append(build_pin("passive", "C", c_num, 2.54, 5.08, rotation=270, length=2.54))
    graphics.append(build_pin("passive", "E", e_num, 2.54, -5.08, rotation=90, length=2.54))
    
    properties = get_common_properties(params, name, default_reference="Q")
    
    return wrap_symbol(name, graphics, properties, hide_pin_names=True)


def build_pnp_symbol(params: Dict[str, Any]) -> str:
    """
    Build a PNP BJT symbol.
    
    Similar to NPN but with arrow pointing inward (toward base).
    
    Args:
        params: Same as build_npn_symbol
    """
    name = params.get("name")
    if not name:
        raise KeyError("'name' is required for PNP symbol")
    
    pins_map = params.get("pins", {"B": "1", "C": "2", "E": "3"})
    
    graphics = []
    
    # Base vertical bar
    graphics.append(build_polyline([(0.635, 1.27), (0.635, -1.27)], stroke_width=0.508))
    
    # Base connection
    graphics.append(build_polyline([(0.635, 0), (-2.54, 0)]))
    
    # Collector line
    graphics.append(build_polyline([(0.635, 0.635), (2.54, 2.54)]))
    
    # Emitter line with arrow pointing inward
    graphics.append(build_polyline([(0.635, -0.635), (2.54, -2.54)]))
    graphics.append(build_polyline([
        (0.889, -0.889), (1.397, -1.397), (1.143, -1.905), (0.889, -0.889)
    ], fill="outline"))
    
    # Circle
    graphics.append(build_circle(1.27, 0, 2.54, stroke_width=0.254))
    
    # Pins (Emitter at top for PNP)
    b_num = pins_map.get("B", pins_map.get("b", "1"))
    e_num = pins_map.get("E", pins_map.get("e", "2"))
    c_num = pins_map.get("C", pins_map.get("c", "3"))
    
    graphics.append(build_pin("input", "B", b_num, -5.08, 0, rotation=0, length=2.54))
    graphics.append(build_pin("passive", "E", e_num, 2.54, 5.08, rotation=270, length=2.54))
    graphics.append(build_pin("passive", "C", c_num, 2.54, -5.08, rotation=90, length=2.54))
    
    properties = get_common_properties(params, name, default_reference="Q")
    
    return wrap_symbol(name, graphics, properties, hide_pin_names=True)
