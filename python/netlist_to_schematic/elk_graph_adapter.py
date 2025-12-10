"""
elk_graph_adapter - Step 1: Convert SKiDL Circuit to ELK Graph JSON

This module transforms a SKiDL circuit (with symbol geometry from database)
into an ELK-compatible graph JSON for automatic layout.

Classes:
    SymbolGeometryFetcher: Fetches pin positions and bounding box from symbol DB
    ElkGraphBuilder: Builds ELK graph from SKiDL circuit + logic hints
"""

import re
from pathlib import Path
from typing import Dict, Any, Optional

from python.skidl_db_wrapper import SymbolDatabase


class SymbolGeometryFetcher:
    """Fetches symbol geometry (pins, bounding box) from the KiCad symbol database."""
    
    def __init__(self, db: Optional[SymbolDatabase] = None):
        """
        Initialize the geometry fetcher.
        
        Args:
            db: Optional SymbolDatabase instance. If not provided, creates a new one.
        """
        self.db = db if db is not None else SymbolDatabase()
        
    def get_part_geometry(self, lib_name: str, symbol_name: str) -> Dict[str, Any]:
        """
        Fetch geometry data for a symbol.
        
        Args:
            lib_name: Library name (e.g., "Device", "Power_Management_ICs")
            symbol_name: Symbol name (e.g., "R", "IP2312U_VSET")
            
        Returns:
            Dict with 'pins' (dict of pin geometries) and 'bbox' (bounding box)
        """
        sexp = self.db.fetch_symbol_sexp(lib_name, symbol_name)
        if not sexp:
            # Fallback for missing symbols
            return {
                "pins": {}, 
                "bbox": {"min_x": -2.54, "max_x": 2.54, "min_y": -2.54, "max_y": 2.54}
            }
        
        return self._parse_sexp_pins(sexp)

    def _parse_sexp_pins(self, sexp: str) -> Dict[str, Any]:
        """
        Parse the S-expression to extract pin info and bounding box.
        
        Returns:
            Dict with:
                - "pins": {pin_num: {"x": float, "y": float, "rot": float, "conn_x": float, "conn_y": float}}
                - "bbox": {"min_x", "max_x", "min_y", "max_y"}
                
        Note: The pin 'at' position is where the pin starts inside the symbol body.
        The actual wire connection point is at the END of the pin, calculated as:
        conn_x = x + length * cos(angle_rad)  # for angle 0, extends right
        conn_y = y + length * sin(angle_rad)  # for angle 90, extends up
        
        KiCad angles: 0=right, 90=up, 180=left, 270=down
        """
        import math
        pins = {}
        sexp = ' '.join(sexp.split())
        
        # Regex for pins: (pin type shape (at X Y R) (length L) ... (number "N")
        # The pattern needs to capture: at position, length, and pin number
        pattern = re.compile(
            r'\(pin\s+\w+\s+\w+\s+\(at\s+([\d.\-]+)\s+([\d.\-]+)\s+([\d.\-]+)\)\s+\(length\s+([\d.\-]+)\).*?\(number\s+"([^"]+)"',
            re.DOTALL
        )
        matches = pattern.findall(sexp)
        
        if not matches:
            return {
                "pins": {}, 
                "bbox": {"min_x": -2.54, "max_x": 2.54, "min_y": -2.54, "max_y": 2.54}
            }

        xs = []
        ys = []
        
        for x_str, y_str, rot_str, length_str, num in matches:
            x, y, rot, length = float(x_str), float(y_str), float(rot_str), float(length_str)
            
            # Calculate the actual connection point at the end of the pin
            # KiCad angles: 0=right, 90=up, 180=left, 270=down
            angle_rad = math.radians(rot)
            conn_x = x + length * math.cos(angle_rad)
            conn_y = y + length * math.sin(angle_rad)
            
            pins[num] = {
                "x": x, 
                "y": y, 
                "rot": rot,
                "length": length,
                "conn_x": conn_x,  # Actual wire connection point
                "conn_y": conn_y
            }
            # Use connection point for bounding box (where wires attach)
            xs.append(conn_x)
            ys.append(conn_y)
            
        return {
            "pins": pins,
            "bbox": {
                "min_x": min(xs),
                "max_x": max(xs),
                "min_y": min(ys),
                "max_y": max(ys)
            }
        }


class ElkGraphBuilder:
    """
    Builds an ELK graph JSON from a SKiDL circuit.
    
    The graph includes nodes (components) with ports (pins) and edges (nets),
    with geometry data for proper schematic layout.
    """
    
    def __init__(self, circuit, logic_data: Dict[str, Any], geometry_fetcher: SymbolGeometryFetcher):
        """
        Initialize the graph builder.
        
        Args:
            circuit: SKiDL Circuit object
            logic_data: Design logic hints (e.g., nets to hide, grouping rules)
            geometry_fetcher: SymbolGeometryFetcher for pin/bbox lookup
        """
        self.circuit = circuit
        self.logic = logic_data
        self.fetcher = geometry_fetcher
        self.graph = {
            "id": "root",
            "properties": {
                "elk.algorithm": "layered",
                "elk.direction": "RIGHT",
                "elk.spacing.nodeNode": "10.0",
                "elk.spacing.edgeEdge": "2.5",
                "elk.layered.spacing.edgeNodeBetweenLayers": "10.0"
            },
            "children": [],
            "edges": []
        }

    def _determine_library(self, part) -> str:
        """Determine the library name for a part."""
        lib_name = "Power_Management_ICs"  # Default
        
        if hasattr(part, "lib") and part.lib:
            if hasattr(part.lib, "filename"):
                lib_name = part.lib.filename
            elif hasattr(part.lib, "name"):
                lib_name = part.lib.name
            else:
                lib_name = str(part.lib).split(":")[0].strip()
        
        # Clean up extensions
        if lib_name.endswith(".kicad_sym"):
            lib_name = Path(lib_name).stem
        elif lib_name.endswith(".sqlite3"):
            lib_name = "Power_Management_ICs"
        
        # Force standard mappings for common parts
        if part.name in ["R", "C", "L", "D", "LED"]:
            lib_name = "Device"
        elif "IP2312" in part.name:
            lib_name = "Power_Management_ICs"
            
        return lib_name

    def build_graph(self) -> Dict[str, Any]:
        """
        Build the ELK graph from the circuit.
        
        Returns:
            ELK-compatible graph dictionary ready for JSON serialization
        """
        MARGIN_X = 2.54  # mm (100 mil)
        MARGIN_Y = 2.54 
        
        # 1. Build Nodes (Parts)
        for part in self.circuit.parts:
            lib_name = self._determine_library(part)
            symbol_name = str(part.name)
            
            # Fetch geometry from DB
            geom_data = self.fetcher.get_part_geometry(lib_name, symbol_name)
            pins = geom_data.get("pins", {})
            bbox = geom_data.get("bbox", {
                "min_x": -2.54, "max_x": 2.54, 
                "min_y": -2.54, "max_y": 2.54
            })
            
            # ELK Node Dimensions
            width = (bbox["max_x"] - bbox["min_x"]) + (2 * MARGIN_X)
            height = (bbox["max_y"] - bbox["min_y"]) + (2 * MARGIN_Y)
            
            if width < 5:
                width = 5.0
            if height < 5:
                height = 5.0
            
            # Calculate origin offset (vector from top-left of ELK box to symbol origin)
            origin_offset_x = -bbox["min_x"] + MARGIN_X
            origin_offset_y = bbox["max_y"] + MARGIN_Y

            # Calculate symbol center for determining port sides
            symbol_center_x = (bbox["min_x"] + bbox["max_x"]) / 2
            symbol_center_y = (bbox["min_y"] + bbox["max_y"]) / 2
            
            node = {
                "id": part.ref,
                "width": width, 
                "height": height,
                "labels": [{"text": part.ref}],
                "ports": [],
                "layoutOptions": {
                    # Use FIXED_POS so ELK respects our exact port coordinates
                    "elk.portConstraints": "FIXED_POS"
                },
                "properties": {
                    "lib": lib_name,
                    "symbol": symbol_name,
                    "value": str(part.value),
                    "origin_offset_x": origin_offset_x,
                    "origin_offset_y": origin_offset_y
                }
            }
            
            # Add Ports with FIXED positions matching KiCad pin layout
            for pin in part.pins:
                pid = f"{part.ref}.{pin.num}"
                # Port size = 0 makes it a true point (ELK accepts this)
                # Wire will connect exactly at port x,y with no center-offset
                port = {
                    "id": pid,
                    "width": 0, 
                    "height": 0
                }
                
                if pin.num in pins:
                    p_geo = pins[pin.num]
                    # Store KiCad pin offset for wire endpoint calculation
                    port["properties"] = {
                        "kicad_offset_x": p_geo["x"],
                        "kicad_offset_y": p_geo["y"]
                    }
                    
                    # Calculate port position within ELK node
                    # 
                    # The goal: ELK wire endpoint must match KiCad pin position
                    # 
                    # KiCad pin position (in absolute coordinates):
                    #   pin_x = symbol_x + kicad_offset_x
                    #   pin_y = symbol_y - kicad_offset_y  (Y inverted)
                    # 
                    # Symbol is placed at:
                    #   symbol_x = (node_x + origin_offset_x) + shift_x  (then grid snapped)
                    #   symbol_y = (node_y + origin_offset_y) + shift_y  (then grid snapped)
                    # 
                    # ELK wire endpoint (from port):
                    #   wire_x = (node_x + port_x) + shift_x  (then grid snapped)
                    #   wire_y = (node_y + port_y) + shift_y  (then grid snapped)
                    # 
                    # For wire to match pin:
                    #   port_x = origin_offset_x + kicad_offset_x
                    #   port_y = origin_offset_y - kicad_offset_y
                    
                    elk_port_x = origin_offset_x + p_geo["x"]
                    elk_port_y = origin_offset_y - p_geo["y"]
                    
                    # Set exact port position for ELK
                    port["x"] = elk_port_x
                    port["y"] = elk_port_y
                    
                    # NOTE: Do NOT set elk.port.side when using FIXED_POS!
                    # Setting port.side forces ports to node edges, overriding x/y positions.
                    # With FIXED_POS, ELK should respect x/y exactly.
                else:
                    # Fallback for hidden/power pins - place at bottom center
                    port["x"] = width / 2
                    port["y"] = height

                node["ports"].append(port)
            
            self.graph["children"].append(node)

        # 2. Build Edges (Nets)
        hidden_nets = []
        if "net_presentation" in self.logic.get("elk_support_fields", {}):
            for rule in self.logic["elk_support_fields"]["net_presentation"].get("rules", []):
                if rule.get("strategy") == "disconnect_with_symbol":
                    hidden_nets.extend(rule.get("nets", []))

        for net in self.circuit.nets:
            if net.name in hidden_nets:
                continue
                
            pins = [p for p in net.pins]
            if len(pins) < 2:
                continue
            
            source = pins[0]
            for target in pins[1:]:
                edge = {
                    "id": f"e_{net.name}_{source.ref}_{target.ref}",
                    "sources": [f"{source.ref}.{source.num}"],
                    "targets": [f"{target.ref}.{target.num}"]
                }
                self.graph["edges"].append(edge)

        return self.graph
