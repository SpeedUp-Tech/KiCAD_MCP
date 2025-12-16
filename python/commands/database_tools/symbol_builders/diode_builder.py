"""
Diode symbol builders - standard diode, Zener, LED.
"""

from typing import Dict, Any

from .base import (
    build_pin, build_polyline, wrap_symbol, get_common_properties
)


def build_diode_symbol(params: Dict[str, Any]) -> str:
    """
    Build a standard diode symbol.
    
    Triangle pointing right with bar at cathode.
    Anode (left), Cathode (right).
    
    Args:
        params: Dictionary containing:
            - name (str): Symbol name (required)
            - pins (dict): Pin mapping, e.g., {"A": "1", "K": "2"}
            - footprint (str): Footprint reference
            - properties (dict): Additional properties
    """
    name = params.get("name")
    if not name:
        raise KeyError("'name' is required for diode symbol")
    
    pins_map = params.get("pins", {"A": "1", "K": "2"})
    
    graphics = []
    
    # Triangle (anode side)
    graphics.append(build_polyline([
        (-1.27, 1.27), (-1.27, -1.27), (1.27, 0), (-1.27, 1.27)
    ], stroke_width=0.254, fill="none"))
    
    # Cathode bar
    graphics.append(build_polyline([
        (1.27, 1.27), (1.27, -1.27)
    ], stroke_width=0.254))
    
    # Connection lines
    graphics.append(build_polyline([(-3.81, 0), (-1.27, 0)]))
    graphics.append(build_polyline([(1.27, 0), (3.81, 0)]))
    
    # Pins
    a_num = pins_map.get("A", pins_map.get("a", pins_map.get("1", "1")))
    k_num = pins_map.get("K", pins_map.get("k", pins_map.get("2", "2")))
    
    graphics.append(build_pin("passive", "A", a_num, -6.35, 0, rotation=0, length=2.54))
    graphics.append(build_pin("passive", "K", k_num, 6.35, 0, rotation=180, length=2.54))
    
    properties = get_common_properties(params, name, default_reference="D")
    
    return wrap_symbol(name, graphics, properties, 
                       hide_pin_names=True, hide_pin_numbers=True)


def build_zener_symbol(params: Dict[str, Any]) -> str:
    """
    Build a Zener diode symbol.
    
    Similar to standard diode but with bent cathode bar.
    
    Args:
        params: Same as build_diode_symbol
    """
    name = params.get("name")
    if not name:
        raise KeyError("'name' is required for Zener symbol")
    
    pins_map = params.get("pins", {"A": "1", "K": "2"})
    
    graphics = []
    
    # Triangle
    graphics.append(build_polyline([
        (-1.27, 1.27), (-1.27, -1.27), (1.27, 0), (-1.27, 1.27)
    ], stroke_width=0.254))
    
    # Zener cathode bar (with bends at ends)
    graphics.append(build_polyline([
        (0.762, 1.27), (1.27, 1.27), (1.27, -1.27), (1.778, -1.27)
    ], stroke_width=0.254))
    
    # Connection lines
    graphics.append(build_polyline([(-3.81, 0), (-1.27, 0)]))
    graphics.append(build_polyline([(1.27, 0), (3.81, 0)]))
    
    # Pins
    a_num = pins_map.get("A", pins_map.get("a", "1"))
    k_num = pins_map.get("K", pins_map.get("k", "2"))
    
    graphics.append(build_pin("passive", "A", a_num, -6.35, 0, rotation=0, length=2.54))
    graphics.append(build_pin("passive", "K", k_num, 6.35, 0, rotation=180, length=2.54))
    
    properties = get_common_properties(params, name, default_reference="D")
    
    return wrap_symbol(name, graphics, properties,
                       hide_pin_names=True, hide_pin_numbers=True)


def build_led_symbol(params: Dict[str, Any]) -> str:
    """
    Build an LED symbol.
    
    Standard diode with light emission arrows.
    
    Args:
        params: Same as build_diode_symbol
    """
    name = params.get("name")
    if not name:
        raise KeyError("'name' is required for LED symbol")
    
    pins_map = params.get("pins", {"A": "1", "K": "2"})
    
    graphics = []
    
    # Triangle
    graphics.append(build_polyline([
        (-1.27, 1.27), (-1.27, -1.27), (1.27, 0), (-1.27, 1.27)
    ], stroke_width=0.254, fill="none"))
    
    # Cathode bar
    graphics.append(build_polyline([
        (1.27, 1.27), (1.27, -1.27)
    ], stroke_width=0.254))
    
    # Light emission arrows (two arrows pointing up-right)
    # Arrow 1
    graphics.append(build_polyline([(-0.508, 1.778), (0.762, 3.048)]))
    graphics.append(build_polyline([
        (0.254, 3.048), (0.762, 3.048), (0.762, 2.54)
    ]))
    
    # Arrow 2
    graphics.append(build_polyline([(0.508, 1.524), (1.778, 2.794)]))
    graphics.append(build_polyline([
        (1.27, 2.794), (1.778, 2.794), (1.778, 2.286)
    ]))
    
    # Connection lines
    graphics.append(build_polyline([(-3.81, 0), (-1.27, 0)]))
    graphics.append(build_polyline([(1.27, 0), (3.81, 0)]))
    
    # Pins
    a_num = pins_map.get("A", pins_map.get("a", "1"))
    k_num = pins_map.get("K", pins_map.get("k", "2"))
    
    graphics.append(build_pin("passive", "A", a_num, -6.35, 0, rotation=0, length=2.54))
    graphics.append(build_pin("passive", "K", k_num, 6.35, 0, rotation=180, length=2.54))
    
    properties = get_common_properties(params, name, default_reference="D")
    
    return wrap_symbol(name, graphics, properties,
                       hide_pin_names=True, hide_pin_numbers=True)
