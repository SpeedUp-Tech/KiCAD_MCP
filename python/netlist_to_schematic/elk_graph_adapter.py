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
        
        # Validate required logic hints
        elk_fields = self.logic.get("elk_support_fields", {})
        if not elk_fields:
            raise ValueError("Missing required 'elk_support_fields' in logic hints")
        
        self.graph = {
            "id": "root",
            "layoutOptions": {
                "elk.algorithm": "layered",
                "elk.direction": "RIGHT",
                "elk.randomSeed": "1",  # Fixed seed for deterministic layout
                "elk.spacing.nodeNode": "5.08",
                "elk.spacing.edgeEdge": "1.27",
                "elk.spacing.edgeNode": "2.54",
                "elk.layered.spacing.baseValue": "2.54",
                "elk.layered.spacing.edgeNodeBetweenLayers": "2.54",
                "elk.layered.spacing.nodeNodeBetweenLayers": "7.62",
                "elk.padding": "[top=0,left=0,bottom=0,right=0]",
                "elk.hierarchyHandling": "INCLUDE_CHILDREN",
                "elk.layered.edgeRouting": "ORTHOGONAL",
                "elk.layered.unnecessaryBendpoints": "false",
                "elk.layered.mergeEdges": "true"
            },
            "properties": {
                # Store power symbol mappings for elk_to_kicad.py
                "power_symbols": {}
            },
            "children": [],
            "edges": []
        }
        
        # Component nodes by reference (for cluster building)
        self._component_nodes = {}

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

    def _get_elk_support_fields(self) -> Dict[str, Any]:
        """Get the elk_support_fields section, raise if missing."""
        elk_fields = self.logic.get("elk_support_fields", {})
        if not elk_fields:
            raise ValueError("Missing required 'elk_support_fields' in logic hints")
        return elk_fields

    def _build_power_symbol_nodes(self) -> None:
        """
        Build ELK nodes for power symbols declared in power_symbols array.
        
        Power symbols are first-class nodes that participate in ELK layout,
        positioned via signal_flows just like regular components.
        """
        MARGIN_X = 2.54
        MARGIN_Y = 2.54
        
        elk_fields = self._get_elk_support_fields()
        power_symbols = elk_fields.get("power_symbols", [])
        
        for ps in power_symbols:
            ps_id = ps.get("id")
            ps_type = ps.get("type", "power:GND")
            
            if not ps_id:
                continue
            
            # Parse library:symbol from type
            if ":" in ps_type:
                lib_name, symbol_name = ps_type.split(":", 1)
            else:
                lib_name, symbol_name = "power", ps_type
            
            # Fetch geometry from database
            geom_data = self.fetcher.get_part_geometry(lib_name, symbol_name)
            pins = geom_data.get("pins", {})
            bbox = geom_data.get("bbox", {
                "min_x": -2.54, "max_x": 2.54,
                "min_y": -2.54, "max_y": 2.54
            })
            
            # Calculate dimensions
            width = (bbox["max_x"] - bbox["min_x"]) + (2 * MARGIN_X)
            height = (bbox["max_y"] - bbox["min_y"]) + (2 * MARGIN_Y)
            
            if width < 5:
                width = 5.0
            if height < 5:
                height = 5.0
            
            origin_offset_x = -bbox["min_x"] + MARGIN_X
            origin_offset_y = bbox["max_y"] + MARGIN_Y
            
            node = {
                "id": ps_id,
                "width": width,
                "height": height,
                "labels": [{"text": symbol_name}],
                "ports": [],
                "layoutOptions": {
                    "elk.portConstraints": "FIXED_POS"
                },
                "properties": {
                    "lib": lib_name,
                    "symbol": symbol_name,
                    "value": symbol_name,
                    "origin_offset_x": origin_offset_x,
                    "origin_offset_y": origin_offset_y
                }
            }
            
            # Add ports from symbol geometry
            for pin_num, p_geo in pins.items():
                elk_port_x = origin_offset_x + p_geo["x"]
                elk_port_y = origin_offset_y - p_geo["y"]
                
                port = {
                    "id": f"{ps_id}.{pin_num}",
                    "width": 0,
                    "height": 0,
                    "x": elk_port_x,
                    "y": elk_port_y,
                    "properties": {
                        "kicad_offset_x": p_geo["x"],
                        "kicad_offset_y": p_geo["y"]
                    }
                }
                node["ports"].append(port)
            
            # Store in component nodes and add to graph
            self._component_nodes[ps_id] = node
            self.graph["children"].append(node)

    def _process_signal_flows(self) -> None:
        """
        Process signal_flows to create phantom edges for layout ordering.
        
        For GND flows (component -> GND_xxx), also creates real wire edges
        from the component's GND pin to the GND symbol.
        """
        elk_fields = self._get_elk_support_fields()
        signal_flows = elk_fields.get("signal_flows", [])
        power_symbol_ids = set()
        
        # Collect power symbol IDs
        for ps in elk_fields.get("power_symbols", []):
            ps_id = ps.get("id")
            if ps_id:
                power_symbol_ids.add(ps_id)
        
        for flow in signal_flows:
            flow_id = flow.get("id", "unnamed")
            path = flow.get("path", [])
            
            if len(path) < 2:
                continue
            
            # Create phantom edges between adjacent items in the path
            for i in range(len(path) - 1):
                source_spec = path[i]
                target_spec = path[i + 1]
                
                source_id = self._resolve_path_item(source_spec)
                target_id = self._resolve_path_item(target_spec)
                
                if source_id and target_id:
                    # Check if this is a GND flow (target is a power symbol)
                    is_gnd_flow = target_id in power_symbol_ids
                    
                    if is_gnd_flow:
                        # Create REAL edge from component's GND pin to power symbol
                        gnd_pin = self._find_gnd_pin_for_component(source_id)
                        if gnd_pin:
                            edge = {
                                "id": f"gnd_{source_id}_to_{target_id}",
                                "sources": [f"{source_id}.{gnd_pin}"],
                                "targets": [f"{target_id}.1"]  # Power symbols have pin 1
                            }
                            self.graph["edges"].append(edge)
                    else:
                        # Create phantom edge for layout ordering only
                        phantom_edge = {
                            "id": f"flow_{flow_id}_{i}",
                            "sources": [source_id],
                            "targets": [target_id],
                            "layoutOptions": {
                                "elk.layered.priority.direction": "10"
                            }
                        }
                        self.graph["edges"].append(phantom_edge)

    def _find_gnd_pin_for_component(self, ref: str):
        """Find the GND pin number for a component from the netlist."""
        for net in self.circuit.nets:
            if net.name == "GND":
                for pin in net.pins:
                    if pin.ref == ref:
                        return pin.num
        return None

    def _resolve_path_item(self, spec: str) -> Optional[str]:
        """
        Resolve a path item to a node ID.
        
        Args:
            spec: Path item like "port:VBUS_5V", "C1", "GND_1"
            
        Returns:
            Node ID for ELK graph, or None if not resolvable
        """
        if spec.startswith("port:"):
            # Boundary ports - skip for now (not represented as nodes)
            return None
        
        # Direct component or power symbol reference
        if spec in self._component_nodes:
            return spec
        
        return None

    def _process_constraints(self) -> None:
        """
        Process constraints to apply ELK layout options.
        
        Supported constraint types:
        - ALIGN_VERTICAL: Place components in same layer (stacked vertically)
        - ALIGN_HORIZONTAL: Place components in same row
        - ADJACENT: Keep components next to each other
        """
        elk_fields = self._get_elk_support_fields()
        constraints = elk_fields.get("constraints", [])
        
        for constraint in constraints:
            ctype = constraint.get("type")
            components = constraint.get("components", [])
            
            if ctype == "ALIGN_VERTICAL" and len(components) >= 2:
                # Create high-weight edges between components to keep them close
                # ELK will stack them vertically within the same layer
                for i in range(len(components) - 1):
                    if components[i] in self._component_nodes and components[i+1] in self._component_nodes:
                        edge = {
                            "id": f"align_{components[i]}_{components[i+1]}",
                            "sources": [components[i]],
                            "targets": [components[i+1]],
                            "layoutOptions": {
                                "elk.layered.priority.direction": "0"  # Same layer
                            }
                        }
                        self.graph["edges"].append(edge)
            
            elif ctype == "ALIGN_HORIZONTAL" and len(components) >= 2:
                # Create edges to place components in same row (horizontally aligned)
                for i in range(len(components) - 1):
                    if components[i] in self._component_nodes and components[i+1] in self._component_nodes:
                        edge = {
                            "id": f"halign_{components[i]}_{components[i+1]}",
                            "sources": [components[i]],
                            "targets": [components[i+1]],
                            "layoutOptions": {
                                "elk.layered.priority.direction": "10"
                            }
                        }
                        self.graph["edges"].append(edge)
            
            elif ctype == "ADJACENT" and len(components) >= 2:
                # Create very high-weight edge to keep components adjacent
                for i in range(len(components) - 1):
                    if components[i] in self._component_nodes and components[i+1] in self._component_nodes:
                        edge = {
                            "id": f"adjacent_{components[i]}_{components[i+1]}",
                            "sources": [components[i]],
                            "targets": [components[i+1]],
                            "layoutOptions": {
                                "elk.layered.priority.direction": "100"
                            }
                        }
                        self.graph["edges"].append(edge)


    def build_graph(self) -> Dict[str, Any]:
        """
        Build the ELK graph from the circuit.
        
        Returns:
            ELK-compatible graph dictionary ready for JSON serialization
        """
        MARGIN_X = 2.54  # mm (100 mil)
        MARGIN_Y = 2.54 
        
        # 1. Build Nodes (Parts) into _component_nodes
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
                    elk_port_x = origin_offset_x + p_geo["x"]
                    elk_port_y = origin_offset_y - p_geo["y"]
                    
                    # Set exact port position for ELK
                    port["x"] = elk_port_x
                    port["y"] = elk_port_y
                else:
                    # Fallback for hidden/power pins - place at bottom center
                    port["x"] = width / 2
                    port["y"] = height

                node["ports"].append(port)
            
            # Store node by reference (for hierarchy building)
            self._component_nodes[part.ref] = node
            # Add directly to root (flat graph - no hierarchy)
            self.graph["children"].append(node)

        # 2. Build power symbol nodes from power_symbols array
        self._build_power_symbol_nodes()
        
        # 3. Process signal flows - creates phantom edges AND real GND edges
        self._process_signal_flows()
        
        # 4. Process constraints (alignment, adjacency) - phantom edges only
        self._process_constraints()
        
        # 5. Build Edges (Nets) - skip GND (handled by signal flows)
        for net in self.circuit.nets:
            # Skip GND net - connections are defined via signal flows
            if net.name == "GND":
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

