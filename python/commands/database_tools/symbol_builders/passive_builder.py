"""
Passive component symbol builders - Resistor, Capacitor, Inductor.
"""

from typing import Dict, Any

from .base import (
    build_pin, build_polyline, build_rectangle, build_arc,
    wrap_symbol, get_common_properties
)


def build_resistor_symbol(params: Dict[str, Any]) -> str:
    """
    Build a resistor symbol (EU rectangular style).
    
    Args:
        params: Dictionary containing:
            - name (str): Symbol name (required)
            - pins (dict): Pin mapping, e.g., {"1": "1", "2": "2"}
            - footprint (str): Footprint reference
            - properties (dict): Additional properties
    """
    name = params.get("name")
    if not name:
        raise KeyError("'name' is required for resistor symbol")
    
    pins_map = params.get("pins", {"1": "1", "2": "2"})
    
    graphics = []
    
    # EU style: rectangle
    graphics.append(build_rectangle(-2.54, 0.762, 2.54, -0.762, stroke_width=0.254))
    
    # Connection lines
    graphics.append(build_polyline([(-3.81, 0), (-2.54, 0)]))
    graphics.append(build_polyline([(2.54, 0), (3.81, 0)]))
    
    # Pins
    p1 = pins_map.get("1", "1")
    p2 = pins_map.get("2", "2")
    
    graphics.append(build_pin("passive", "1", p1, -6.35, 0, rotation=0, length=2.54))
    graphics.append(build_pin("passive", "2", p2, 6.35, 0, rotation=180, length=2.54))
    
    properties = get_common_properties(params, name, default_reference="R")
    
    return wrap_symbol(name, graphics, properties,
                       hide_pin_names=True, hide_pin_numbers=True)


def build_capacitor_symbol(params: Dict[str, Any]) -> str:
    """
    Build a capacitor symbol (non-polarized).
    
    Two parallel lines.
    
    Args:
        params: Dictionary containing:
            - name (str): Symbol name (required)
            - pins (dict): Pin mapping, e.g., {"1": "1", "2": "2"}
            - polarized (bool): If True, show + marker (default: False)
            - footprint (str): Footprint reference
            - properties (dict): Additional properties
    """
    name = params.get("name")
    if not name:
        raise KeyError("'name' is required for capacitor symbol")
    
    pins_map = params.get("pins", {"1": "1", "2": "2"})
    polarized = params.get("polarized", False)
    
    graphics = []
    
    # Two parallel plates
    graphics.append(build_polyline([(-0.635, 1.27), (-0.635, -1.27)], stroke_width=0.254))
    graphics.append(build_polyline([(0.635, 1.27), (0.635, -1.27)], stroke_width=0.254))
    
    # Connection lines
    graphics.append(build_polyline([(-3.81, 0), (-0.635, 0)]))
    graphics.append(build_polyline([(0.635, 0), (3.81, 0)]))
    
    # Polarized marker (+)
    if polarized:
        graphics.append(build_polyline([(-2.286, 1.27), (-2.286, 0.508)], stroke_width=0.254))
        graphics.append(build_polyline([(-2.667, 0.889), (-1.905, 0.889)], stroke_width=0.254))
    
    # Pins
    p1 = pins_map.get("1", "1")
    p2 = pins_map.get("2", "2")
    
    graphics.append(build_pin("passive", "1", p1, -6.35, 0, rotation=0, length=2.54))
    graphics.append(build_pin("passive", "2", p2, 6.35, 0, rotation=180, length=2.54))
    
    properties = get_common_properties(params, name, default_reference="C")
    
    return wrap_symbol(name, graphics, properties,
                       hide_pin_names=True, hide_pin_numbers=True)


def build_inductor_symbol(params: Dict[str, Any]) -> str:
    """
    Build an inductor symbol (coil style with humps).
    
    Args:
        params: Dictionary containing:
            - name (str): Symbol name (required)
            - pins (dict): Pin mapping, e.g., {"1": "1", "2": "2"}
            - footprint (str): Footprint reference
            - properties (dict): Additional properties
    """
    name = params.get("name")
    if not name:
        raise KeyError("'name' is required for inductor symbol")
    
    pins_map = params.get("pins", {"1": "1", "2": "2"})
    
    graphics = []
    
    # Four arcs for coil (humps)
    # Simplified with polylines approximating arcs
    graphics.append(build_arc((-2.54, 0), (-1.905, 0.635), (-1.27, 0)))
    graphics.append(build_arc((-1.27, 0), (-0.635, 0.635), (0, 0)))
    graphics.append(build_arc((0, 0), (0.635, 0.635), (1.27, 0)))
    graphics.append(build_arc((1.27, 0), (1.905, 0.635), (2.54, 0)))
    
    # Connection lines
    graphics.append(build_polyline([(-3.81, 0), (-2.54, 0)]))
    graphics.append(build_polyline([(2.54, 0), (3.81, 0)]))
    
    # Pins
    p1 = pins_map.get("1", "1")
    p2 = pins_map.get("2", "2")
    
    graphics.append(build_pin("passive", "1", p1, -6.35, 0, rotation=0, length=2.54))
    graphics.append(build_pin("passive", "2", p2, 6.35, 0, rotation=180, length=2.54))
    
    properties = get_common_properties(params, name, default_reference="L")
    
    return wrap_symbol(name, graphics, properties,
                       hide_pin_names=True, hide_pin_numbers=True)
