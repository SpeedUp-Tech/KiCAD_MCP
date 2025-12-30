"""
elk_graph_adapter - Step 1: Convert SKiDL Circuit to ELK Graph JSON

This module transforms a SKiDL circuit (with symbol geometry from database)
into an ELK-compatible graph JSON for automatic layout.

Classes:
    SymbolGeometryFetcher: Fetches pin positions and bounding box from symbol DB
    ElkGraphBuilder: Builds ELK graph from SKiDL circuit + logic hints
"""

import math
import re
from pathlib import Path
from typing import Dict, Any, Optional

from python.skidl_db_wrapper import SymbolDatabase


SPECIAL_PIN_THRESHOLD = 5


def _rotate_point(x: float, y: float, angle_deg: int) -> tuple[float, float]:
    """Rotate a point around origin by angle_deg degrees."""
    if angle_deg == 0:
        return (x, y)
    angle_rad = math.radians(angle_deg)
    cos_a = math.cos(angle_rad)
    sin_a = math.sin(angle_rad)
    return (x * cos_a - y * sin_a, x * sin_a + y * cos_a)


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
    
    def __init__(self, circuit, logic_data: Dict[str, Any], geometry_fetcher: SymbolGeometryFetcher, rotation_map: Dict[str, int] | None = None):
        """
        Initialize the graph builder.
        
        Args:
            circuit: SKiDL Circuit object
            logic_data: Design logic hints (e.g., nets to hide, grouping rules)
            geometry_fetcher: SymbolGeometryFetcher for pin/bbox lookup
            rotation_map: Optional dict of {ref: rotation_degrees} for components
        """
        self.circuit = circuit
        self.logic = logic_data
        self.fetcher = geometry_fetcher
        self.rotation_map = rotation_map or {}
        
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
                "elk.spacing.nodeNode": "1.27",
                "elk.spacing.edgeEdge": "2.54",
                "elk.spacing.edgeNode": "1.27",
                "elk.layered.spacing.baseValue": "2.54",
                "elk.layered.spacing.edgeNodeBetweenLayers": "5.08",
                "elk.layered.spacing.nodeNodeBetweenLayers": "2.54",
                "elk.padding": "[top=0,left=0,bottom=0,right=0]",
                "elk.hierarchyHandling": "INCLUDE_CHILDREN",
                "elk.layered.edgeRouting": "ORTHOGONAL",
                "elk.layered.unnecessaryBendpoints": "false",
                "elk.layered.mergeEdges": "true",
                # Crossing minimization configuration
                "elk.layered.thoroughness": "10",
                "elk.layered.crossingMinimization.strategy": "LAYER_SWEEP",
                "elk.layered.crossingMinimization.greedySwitch.type": "TWO_SIDED"
            },
            "properties": {
                # Store power symbol mappings for elk_to_kicad.py
                "power_symbols": {}
            },
            "children": [],
            "edges": []
        }

        if not elk_fields.get("layout_chains") and not elk_fields.get("constraints"):
            # No-order mode: avoid collapsing into a single narrow column.
            node_count = len(getattr(self.circuit, "parts", []) or [])
            node_count += len(elk_fields.get("net_labels", []))
            node_count += len(elk_fields.get("power_symbols", []))
            layer_bound = max(4, int(math.sqrt(max(node_count, 1))))
            self.graph["layoutOptions"].update({
                "elk.layered.layering.strategy": "COFFMAN_GRAHAM",
                "elk.layered.layering.coffmanGraham.layerBound": str(layer_bound),
                "elk.layered.nodePlacement.strategy": "NETWORK_SIMPLEX",
                "elk.layered.mergeEdges": "false",
            })
        
        # Component nodes by reference (for cluster building)
        self._component_nodes = {}
        connected_pins: dict[str, set[str]] = {}
        for net in getattr(self.circuit, "nets", []) or []:
            for pin in getattr(net, "pins", []) or []:
                ref = getattr(pin, "ref", None) or getattr(getattr(pin, "part", None), "ref", None)
                num = str(getattr(pin, "num", "") or "")
                if not ref or not num:
                    continue
                connected_pins.setdefault(ref, set()).add(num)

        def _pin_count(part) -> int:
            pins = getattr(part, "pins", []) or []
            try:
                total = len(pins)
            except TypeError:
                total = 0
            connected = len(connected_pins.get(getattr(part, "ref", ""), set()))
            return max(total, connected)

        self._special_refs = {
            part.ref
            for part in getattr(self.circuit, "parts", [])
            if getattr(part, "ref", None)
            and _pin_count(part) >= SPECIAL_PIN_THRESHOLD
        }
        
        # Track pins connected via labels (for insurance logic)
        self._covered_pins = set()
        # Track label pin usage per (component, net) to avoid reusing pins
        self._label_pin_usage = {}
        # Track labels adjacent to a component per net (for insurance routing)
        self._component_net_labels = {}
        # Track label side (left/right) per component net for side-aware wiring
        self._component_net_label_sides = {}
        # Track power symbol pin usage per (component, net) to avoid reusing pins
        self._power_pin_usage = {}
        # Track power symbols adjacent to a component per net (for insurance routing)
        self._component_net_powers = {}
        # Track power symbol side (left/right) per component net for side-aware wiring
        self._component_net_power_sides = {}

    def _determine_library(self, part) -> str:
        """Determine the library name for a part."""
        # First check for DB-backed library name (set by skidl_db_wrapper)
        if hasattr(part, "_db_library_name") and part._db_library_name:
            return part._db_library_name
        
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
        positioned via layout_chains just like regular components.
        """
        MARGIN_X = 1.27
        MARGIN_Y = 1.27
        MIN_SIZE = 2.54  # Keep power symbols compact (2 grid units).
        
        elk_fields = self._get_elk_support_fields()
        power_symbols = elk_fields.get("power_symbols", [])
        layout_chains = elk_fields.get("layout_chains", [])
        direct_connections = elk_fields.get("direct_connections", [])

        power_symbol_ids = {ps.get("id") for ps in power_symbols if ps.get("id")}

        # Determine preferred port side for power symbols based on connection direction.
        # This helps ELK place power symbols on the intended side without relying on
        # node-to-node phantom edges that can pull symbols toward node corners.
        power_roles: dict[str, str] = {}  # power_id -> "source" | "target" | "both"

        def merge_power_role(power_id: str, role: str) -> None:
            if role not in {"source", "target", "both"}:
                return
            if power_id in power_roles:
                if power_roles[power_id] != role:
                    power_roles[power_id] = "both"
            else:
                power_roles[power_id] = role

        for chain in layout_chains:
            path = chain.get("path", [])
            if not isinstance(path, list) or len(path) < 2:
                continue
            for left, right in zip(path, path[1:]):
                if left in power_symbol_ids:
                    merge_power_role(left, "source")
                if right in power_symbol_ids:
                    merge_power_role(right, "target")

        for conn in direct_connections:
            node_id = conn.get("node")
            if node_id not in power_symbol_ids:
                continue
            side = conn.get("side")
            if side == "left":
                merge_power_role(node_id, "source")
            elif side == "right":
                merge_power_role(node_id, "target")
        
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
            
            if width < MIN_SIZE:
                width = MIN_SIZE
            if height < MIN_SIZE:
                height = MIN_SIZE
            
            origin_offset_x = -bbox["min_x"] + MARGIN_X
            origin_offset_y = bbox["max_y"] + MARGIN_Y

            role = power_roles.get(ps_id, "both")
            
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

                port_x = elk_port_x
                # Most KiCad power symbols have a single pin "1" at (0,0).
                # Place that port on the edge facing the connected component so the
                # layered algorithm can honor left/right intent more reliably.
                if str(pin_num) == "1":
                    if role == "source":
                        port_x = width  # Right edge: edge goes RIGHT to component.
                    elif role == "target":
                        port_x = 0  # Left edge: edge comes from LEFT.
                
                port = {
                    "id": f"{ps_id}.{pin_num}",
                    "width": 0,
                    "height": 0,
                    "x": port_x,
                    "y": elk_port_y,
                    "properties": {
                        "kicad_offset_x": p_geo["x"],
                        "kicad_offset_y": p_geo["y"]
                    }
                }
                node["ports"].append(port)

            if not node["ports"]:
                # Fallback for missing/unknown power symbol geometry.
                port_x = origin_offset_x
                if role == "source":
                    port_x = width
                elif role == "target":
                    port_x = 0
                node["ports"].append({
                    "id": f"{ps_id}.1",
                    "width": 0,
                    "height": 0,
                    "x": port_x,
                    "y": origin_offset_y,
                    "properties": {
                        "kicad_offset_x": 0.0,
                        "kicad_offset_y": 0.0,
                    },
                })
            
            # Store in component nodes and add to graph
            self._component_nodes[ps_id] = node
            self.graph["children"].append(node)

    def _build_net_label_nodes(self) -> None:
        """
        Build ELK nodes for net labels declared in net_labels array.
        
        Net labels are first-class nodes that participate in ELK layout,
        positioned via layout_chains just like regular components and power symbols.
        
        Port position is determined dynamically by scanning layout_chains:
        - If label is FIRST in a path (source): port on RIGHT edge (wire goes right)
        - If label is LAST in a path (target): port on LEFT edge (wire comes from left)
        - If label appears in both positions or neither: use center
        
        Each label has:
        - id: Unique node ID for ELK graph
        - net_name: The net name to display (optional, defaults to id)
        - type: input/output/bidirectional (determines KiCad label shape)
        """
        # Label dimensions based on KiCad default font (1.27mm)
        CHAR_WIDTH = 1.27    # mm per character (KiCad default font)
        MIN_WIDTH = 2.54     # minimum width (~2 grid units)
        LABEL_HEIGHT = 1.27  # KiCad default font height
        
        elk_fields = self._get_elk_support_fields()
        net_labels = elk_fields.get("net_labels", [])
        layout_chains = elk_fields.get("layout_chains", [])
        direct_connections = elk_fields.get("direct_connections", [])
        
        # Build a set of label IDs for quick lookup
        label_ids = {nl.get("id") for nl in net_labels if nl.get("id")}
        
        # Scan layout chains to determine each label's role
        # A label can be: "source" (first in path), "target" (last in path), or "both"
        label_roles: dict[str, str] = {}  # label_id -> "source" | "target" | "both"

        def merge_label_role(label_id: str, role: str) -> None:
            if role not in {"source", "target", "both"}:
                return
            if label_id in label_roles:
                if label_roles[label_id] != role:
                    label_roles[label_id] = "both"
            else:
                label_roles[label_id] = role
        
        for chain in layout_chains:
            path = chain.get("path", [])
            if len(path) < 2:
                continue
            
            first_item = path[0]
            last_item = path[-1]
            
            # Check if first item is a label
            if first_item in label_ids:
                merge_label_role(first_item, "source")
            
            # Check if last item is a label
            if last_item in label_ids:
                merge_label_role(last_item, "target")

        for conn in direct_connections:
            node_id = conn.get("node")
            if node_id not in label_ids:
                continue
            side = conn.get("side")
            if side == "left":
                merge_label_role(node_id, "source")
            elif side == "right":
                merge_label_role(node_id, "target")
        
        for nl in net_labels:
            nl_id = nl.get("id")
            if not nl_id:
                continue
            
            # net_name defaults to id if not specified
            net_name = nl.get("net_name", nl_id)
            label_type = nl.get("type", "hierarchical")  # hierarchical/local
            label_shape = nl.get("shape", "bidirectional")  # input/output/bidirectional
            
            # Calculate label width based on text length
            # KiCad labels have connection point at one end, text extends from there
            label_width = max(MIN_WIDTH, len(net_name) * CHAR_WIDTH)
            
            # Determine port position based on role in layout chains
            # This makes path order directly control layout position
            role = label_roles.get(nl_id, "both")
            
            if role == "source":
                # Label is first in path, wire goes RIGHT to next node
                port_x = label_width  # Right edge
            elif role == "target":
                # Label is last in path, wire comes from LEFT
                port_x = 0  # Left edge
            else:
                # Label is both source and target, or not in any flow
                port_x = label_width / 2  # Center
            
            port_y = LABEL_HEIGHT / 2  # Vertically centered
            
            node = {
                "id": nl_id,
                "width": label_width,
                "height": LABEL_HEIGHT,
                "labels": [{"text": net_name}],
                "ports": [
                    {
                        "id": f"{nl_id}.1",
                        "width": 0,
                        "height": 0,
                        "x": port_x,
                        "y": port_y,
                        "properties": {
                            "kicad_offset_x": 0,
                            "kicad_offset_y": 0
                        }
                    }
                ],
                "layoutOptions": {
                    "elk.portConstraints": "FIXED_POS"
                },
                "properties": {
                    "node_type": "net_label",
                    "net_name": net_name,
                    "label_type": label_type,  # hierarchical/local
                    "label_shape": label_shape,  # input/output/bidirectional
                    "label_role": role,  # source/target/both - used for KiCad label rotation
                    "origin_offset_x": label_width / 2,
                    "origin_offset_y": LABEL_HEIGHT / 2
                }
            }
            
            # Store in component nodes and add to graph
            self._component_nodes[nl_id] = node
            self.graph["children"].append(node)

    def _process_layout_chains(self) -> None:
        """
        Process layout_chains to create phantom edges for layout ordering.
        
        For GND flows (component -> GND_xxx), creates real wire edges
        from the component's GND pin to the GND symbol.
        
        For net label flows (component -> label or label -> component),
        creates real wire edges to/from the label node.
        """
        elk_fields = self._get_elk_support_fields()
        layout_chains = elk_fields.get("layout_chains", [])
        power_symbol_ids = set()
        power_symbol_net_by_id = {}
        net_label_ids = {}  # id -> net_name mapping
        
        # Collect power symbol IDs
        for ps in elk_fields.get("power_symbols", []):
            ps_id = ps.get("id")
            ps_type = ps.get("type", "")
            if ps_id:
                power_symbol_ids.add(ps_id)
                if isinstance(ps_type, str) and ":" in ps_type:
                    power_symbol_net_by_id[ps_id] = ps_type.split(":", 1)[1]
        
        # Collect net label IDs and their net names
        for nl in elk_fields.get("net_labels", []):
            nl_id = nl.get("id")
            if nl_id:
                net_label_ids[nl_id] = nl.get("net_name", nl_id)
        
        for chain in layout_chains:
            chain_id = chain.get("id", "unnamed")
            path = chain.get("path", [])
            
            if len(path) < 2:
                continue
            
            # Create phantom edges between adjacent items in the path
            for i in range(len(path) - 1):
                source_spec = path[i]
                target_spec = path[i + 1]
                
                source_id = self._resolve_path_item(source_spec)
                target_id = self._resolve_path_item(target_spec)
                
                if source_id and target_id:
                    # Check if this is a power symbol flow (either side)
                    is_power_source = source_id in power_symbol_ids
                    is_power_target = target_id in power_symbol_ids
                    # Check if source or target is a net label
                    is_label_source = source_id in net_label_ids
                    is_label_target = target_id in net_label_ids
                    
                    if is_power_source or is_power_target:
                        power_id = source_id if is_power_source else target_id
                        comp_id = target_id if is_power_source else source_id
                        net_name = power_symbol_net_by_id.get(power_id)
                        if comp_id in self._component_nodes and net_name:
                            used_pins = self._power_pin_usage.get((comp_id, net_name), set())
                            comp_pin = self._find_pin_for_net(
                                comp_id,
                                net_name,
                                exclude=used_pins,
                            )
                            if comp_pin:
                                used_pins.add(comp_pin)
                                self._power_pin_usage[(comp_id, net_name)] = used_pins
                                if is_power_source:
                                    edge = {
                                        "id": f"power_{power_id}_to_{comp_id}",
                                        "sources": [f"{power_id}.1"],
                                        "targets": [f"{comp_id}.{comp_pin}"],
                                        "layoutOptions": {
                                            "elk.layered.priority.direction": "10",
                                            "elk.layered.priority.shortness": "10"
                                        }
                                    }
                                    order_edge = {
                                        "id": f"chain_power_{power_id}_{comp_id}",
                                        "sources": [f"{power_id}.1"],
                                        "targets": [f"{comp_id}.{comp_pin}"],
                                        "layoutOptions": {
                                            "elk.layered.priority.direction": "10"
                                        }
                                    }
                                else:
                                    edge = {
                                        "id": f"power_{comp_id}_to_{power_id}",
                                        "sources": [f"{comp_id}.{comp_pin}"],
                                        "targets": [f"{power_id}.1"],
                                        "layoutOptions": {
                                            "elk.layered.priority.direction": "10",
                                            "elk.layered.priority.shortness": "10"
                                        }
                                    }
                                    order_edge = {
                                        "id": f"chain_power_{comp_id}_{power_id}",
                                        "sources": [f"{comp_id}.{comp_pin}"],
                                        "targets": [f"{power_id}.1"],
                                        "layoutOptions": {
                                            "elk.layered.priority.direction": "10"
                                        }
                                    }
                                self.graph["edges"].append(edge)
                                self.graph["edges"].append(order_edge)
                                self._covered_pins.add(f"{comp_id}.{comp_pin}")
                                powers = self._component_net_powers.setdefault((comp_id, net_name), [])
                                if power_id not in powers:
                                    powers.append(power_id)
                                side_map = self._component_net_power_sides.setdefault((comp_id, net_name), {})
                                side_map[power_id] = "left" if is_power_source else "right"
                    elif is_label_source or is_label_target:
                        # Create REAL edge for net label connection
                        # Find the appropriate pin on the component
                        if is_label_source:
                            # Label -> Component: find pin on component for this net
                            label_id = source_id
                            comp_id = target_id
                            net_name = net_label_ids[label_id]
                            used_pins = self._label_pin_usage.get((comp_id, net_name), set())
                            comp_pin = self._find_pin_for_net(
                                comp_id,
                                net_name,
                                exclude=used_pins,
                                preferred_side="left",
                            )
                            if comp_pin is None:
                                # Allow multiple labels to share a single pin if needed.
                                comp_pin = self._find_pin_for_net(
                                    comp_id,
                                    net_name,
                                    preferred_side="left",
                                )
                            if comp_pin:
                                self._label_pin_usage.setdefault((comp_id, net_name), set()).add(comp_pin)
                                labels = self._component_net_labels.setdefault((comp_id, net_name), [])
                                if label_id not in labels:
                                    labels.append(label_id)
                                side_map = self._component_net_label_sides.setdefault((comp_id, net_name), {})
                                side_map[label_id] = "left"
                                edge = {
                                    "id": f"label_{label_id}_to_{comp_id}",
                                    "sources": [f"{label_id}.1"],
                                    "targets": [f"{comp_id}.{comp_pin}"],
                                    "layoutOptions": {
                                        "elk.layered.priority.shortness": "10"
                                    }
                                }
                                self.graph["edges"].append(edge)
                                self._covered_pins.add(f"{comp_id}.{comp_pin}")
                                self._mark_component_net_pins_covered(comp_id, net_name)
                        else:
                            # Component -> Label: find pin on component for this net
                            comp_id = source_id
                            label_id = target_id
                            net_name = net_label_ids[label_id]
                            used_pins = self._label_pin_usage.get((comp_id, net_name), set())
                            comp_pin = self._find_pin_for_net(
                                comp_id,
                                net_name,
                                exclude=used_pins,
                                preferred_side="right",
                            )
                            if comp_pin is None:
                                # Allow multiple labels to share a single pin if needed.
                                comp_pin = self._find_pin_for_net(
                                    comp_id,
                                    net_name,
                                    preferred_side="right",
                                )
                            if comp_pin:
                                self._label_pin_usage.setdefault((comp_id, net_name), set()).add(comp_pin)
                                labels = self._component_net_labels.setdefault((comp_id, net_name), [])
                                if label_id not in labels:
                                    labels.append(label_id)
                                side_map = self._component_net_label_sides.setdefault((comp_id, net_name), {})
                                side_map[label_id] = "right"
                                edge = {
                                    "id": f"{comp_id}_to_label_{label_id}",
                                    "sources": [f"{comp_id}.{comp_pin}"],
                                    "targets": [f"{label_id}.1"],
                                    "layoutOptions": {
                                        "elk.layered.priority.shortness": "10"
                                    }
                                }
                                self.graph["edges"].append(edge)
                                self._covered_pins.add(f"{comp_id}.{comp_pin}")
                                self._mark_component_net_pins_covered(comp_id, net_name)
                    else:
                        # Create phantom edge for layout ordering only
                        phantom_edge = {
                            "id": f"chain_{chain_id}_{i}",
                            "sources": [source_id],
                            "targets": [target_id],
                            "layoutOptions": {
                                "elk.layered.priority.direction": "10"
                            }
                        }
                        self.graph["edges"].append(phantom_edge)

    def _process_direct_connections(self) -> None:
        """
        Process direct_connections to create real edges without layout ordering.

        Each entry should specify a label/power node and a component to connect.
        Optional "side" hints select left/right pins and label orientation.
        """
        elk_fields = self._get_elk_support_fields()
        direct_connections = elk_fields.get("direct_connections", [])
        if not direct_connections:
            return

        power_symbol_ids = set()
        power_symbol_net_by_id = {}
        net_label_ids = {}

        for ps in elk_fields.get("power_symbols", []):
            ps_id = ps.get("id")
            ps_type = ps.get("type", "")
            if ps_id:
                power_symbol_ids.add(ps_id)
                if isinstance(ps_type, str) and ":" in ps_type:
                    power_symbol_net_by_id[ps_id] = ps_type.split(":", 1)[1]

        for nl in elk_fields.get("net_labels", []):
            nl_id = nl.get("id")
            if nl_id:
                net_label_ids[nl_id] = nl.get("net_name", nl_id)

        for conn in direct_connections:
            node_id = conn.get("node")
            comp_id = conn.get("component")
            side = conn.get("side")
            if not node_id or not comp_id:
                continue
            if comp_id not in self._component_nodes:
                continue

            preferred_side = side if side in {"left", "right"} else None

            if node_id in power_symbol_ids:
                net_name = power_symbol_net_by_id.get(node_id)
                if not net_name:
                    continue
                used_pins = self._power_pin_usage.get((comp_id, net_name), set())
                comp_pin = self._find_pin_for_net(
                    comp_id,
                    net_name,
                    exclude=used_pins,
                    preferred_side=preferred_side,
                )
                if comp_pin is None and preferred_side:
                    comp_pin = self._find_pin_for_net(
                        comp_id,
                        net_name,
                        exclude=used_pins,
                    )
                if comp_pin:
                    used_pins.add(comp_pin)
                    self._power_pin_usage[(comp_id, net_name)] = used_pins
                    if preferred_side == "left":
                        edge = {
                            "id": f"direct_power_{node_id}_to_{comp_id}_{comp_pin}",
                            "sources": [f"{node_id}.1"],
                            "targets": [f"{comp_id}.{comp_pin}"],
                            "layoutOptions": {
                                "elk.layered.priority.direction": "10",
                                "elk.layered.priority.shortness": "10"
                            }
                        }
                        order_edge = {
                            "id": f"chain_power_direct_{node_id}_{comp_id}",
                            "sources": [f"{node_id}.1"],
                            "targets": [f"{comp_id}.{comp_pin}"],
                            "layoutOptions": {
                                "elk.layered.priority.direction": "10"
                            }
                        }
                    else:
                        edge = {
                            "id": f"direct_power_{comp_id}_{node_id}_{comp_pin}",
                            "sources": [f"{comp_id}.{comp_pin}"],
                            "targets": [f"{node_id}.1"],
                            "layoutOptions": {
                                "elk.layered.priority.direction": "10",
                                "elk.layered.priority.shortness": "10"
                            }
                        }
                        order_edge = {
                            "id": f"chain_power_direct_{comp_id}_{node_id}",
                            "sources": [f"{comp_id}.{comp_pin}"],
                            "targets": [f"{node_id}.1"],
                            "layoutOptions": {
                                "elk.layered.priority.direction": "10"
                            }
                        }
                    self.graph["edges"].append(edge)
                    self.graph["edges"].append(order_edge)
                    self._covered_pins.add(f"{comp_id}.{comp_pin}")
                    powers = self._component_net_powers.setdefault((comp_id, net_name), [])
                    if node_id not in powers:
                        powers.append(node_id)
                    if preferred_side:
                        side_map = self._component_net_power_sides.setdefault((comp_id, net_name), {})
                        side_map[node_id] = preferred_side
                continue

            if node_id in net_label_ids:
                net_name = net_label_ids[node_id]
                used_pins = self._label_pin_usage.get((comp_id, net_name), set())
                comp_pin = self._find_pin_for_net(
                    comp_id,
                    net_name,
                    exclude=used_pins,
                    preferred_side=preferred_side,
                )
                if comp_pin is None:
                    comp_pin = self._find_pin_for_net(
                        comp_id,
                        net_name,
                        preferred_side=preferred_side,
                    )
                if comp_pin:
                    self._label_pin_usage.setdefault((comp_id, net_name), set()).add(comp_pin)
                    labels = self._component_net_labels.setdefault((comp_id, net_name), [])
                    if node_id not in labels:
                        labels.append(node_id)
                    if preferred_side:
                        side_map = self._component_net_label_sides.setdefault((comp_id, net_name), {})
                        side_map[node_id] = preferred_side
                    if preferred_side == "left":
                        edge = {
                            "id": f"direct_label_{node_id}_to_{comp_id}_{comp_pin}",
                            "sources": [f"{node_id}.1"],
                            "targets": [f"{comp_id}.{comp_pin}"],
                            "layoutOptions": {
                                "elk.layered.priority.shortness": "10"
                            }
                        }
                    else:
                        edge = {
                            "id": f"direct_label_{comp_id}_to_{node_id}_{comp_pin}",
                            "sources": [f"{comp_id}.{comp_pin}"],
                            "targets": [f"{node_id}.1"],
                            "layoutOptions": {
                                "elk.layered.priority.shortness": "10"
                            }
                        }
                    self.graph["edges"].append(edge)
                    self._covered_pins.add(f"{comp_id}.{comp_pin}")
                    self._mark_component_net_pins_covered(comp_id, net_name)

    def _find_gnd_pin_for_component(self, ref: str):
        """Find the GND pin number for a component from the netlist."""
        for net in self.circuit.nets:
            if net.name == "GND":
                for pin in net.pins:
                    if pin.ref == ref:
                        return pin.num
        return None

    def _get_component_pins_for_net(self, ref: str, net_name: str) -> list[str]:
        """Return sorted pin numbers for a component on a given net."""
        for net in self.circuit.nets:
            if net.name == net_name:
                pins = {str(pin.num) for pin in net.pins if pin.ref == ref}
                return sorted(pins, key=str)
        return []

    def _pin_side(self, ref: str, pin_num: str) -> str | None:
        """Return 'left' or 'right' for a pin based on symbol geometry."""
        node = self._component_nodes.get(ref)
        if not node:
            return None
        width = node.get("width")
        if width is None:
            return None
        port_id = f"{ref}.{pin_num}"
        for port in node.get("ports", []):
            if port.get("id") == port_id:
                x = port.get("x")
                if x is None:
                    return None
                return "left" if x < (width / 2) else "right"
        return None

    def _mark_component_net_pins_covered(self, ref: str, net_name: str) -> None:
        """Mark all pins on a component's net as covered to avoid pin-to-pin wires."""
        for pin_num in self._get_component_pins_for_net(ref, net_name):
            self._covered_pins.add(f"{ref}.{pin_num}")

    def _find_pin_for_net(
        self,
        ref: str,
        net_name: str,
        *,
        exclude: set[str] | None = None,
        preferred_side: str | None = None,
    ):
        """Find which pin of a component is connected to a specific net.
        
        Args:
            ref: Component reference (e.g., "C1", "U1")
            net_name: Net name to search for (e.g., "VBUS_5V", "BATT_1S")
            exclude: Optional set of pin numbers to skip.
            preferred_side: Optional "left" or "right" to bias pin selection.
            
        Returns:
            Pin number if found, None otherwise
        """
        pins = self._get_component_pins_for_net(ref, net_name)
        if not pins:
            return None
        exclude = exclude or set()
        if preferred_side:
            preferred = [p for p in pins if self._pin_side(ref, p) == preferred_side]
            remaining = [p for p in pins if p not in preferred]
            candidates = preferred + remaining
        else:
            candidates = pins
        for pin_num in candidates:
            if pin_num in exclude:
                continue
            return pin_num
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
        MARGIN_X = 1.27  # mm (50 mil)
        MARGIN_Y = 1.27
        
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
            
            # Apply rotation if specified
            rotation = self.rotation_map.get(part.ref, 0)
            # Default R and C to 270° (vertical) if no explicit rotation specified
            # This aligns their pins vertically with the horizontal ELK flow direction
            if rotation == 0 and symbol_name in ("R"):
                rotation = 270
            if rotation != 0:
                # Rotate pin positions
                rotated_pins = {}
                for pnum, pgeo in pins.items():
                    rx, ry = _rotate_point(pgeo["x"], pgeo["y"], rotation)
                    rcx, rcy = _rotate_point(pgeo["conn_x"], pgeo["conn_y"], rotation)
                    rotated_pins[pnum] = {
                        "x": rx, "y": ry,
                        "rot": (pgeo.get("rot", 0) + rotation) % 360,
                        "length": pgeo.get("length", 2.54),
                        "conn_x": rcx, "conn_y": rcy
                    }
                pins = rotated_pins
                
                # Rotate bounding box corners and recalculate
                corners = [
                    (bbox["min_x"], bbox["min_y"]),
                    (bbox["max_x"], bbox["min_y"]),
                    (bbox["max_x"], bbox["max_y"]),
                    (bbox["min_x"], bbox["max_y"]),
                ]
                rotated_corners = [_rotate_point(x, y, rotation) for x, y in corners]
                xs = [c[0] for c in rotated_corners]
                ys = [c[1] for c in rotated_corners]
                bbox = {
                    "min_x": min(xs), "max_x": max(xs),
                    "min_y": min(ys), "max_y": max(ys)
                }
            
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
                    "origin_offset_y": origin_offset_y,
                    "rotation": rotation
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
                    p_geo = pins[pin.num]  # Rotated pin positions for both ELK and KiCad

                    # Store ROTATED KiCad pin offset for wire endpoint calculation
                    # When component is rotated, wire endpoints must match the rotated pin positions
                    port["properties"] = {
                        "kicad_offset_x": p_geo["x"],
                        "kicad_offset_y": p_geo["y"]
                    }
                    
                    # Calculate port position within ELK node using ROTATED positions
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
        
        # 3. Build net label nodes from net_labels array
        self._build_net_label_nodes()
        
        # 4. Process direct connections (real edges without ordering)
        self._process_direct_connections()

        # 5. Process layout chains - creates phantom edges AND real GND edges
        self._process_layout_chains()
        
        # 6. Process constraints (alignment, adjacency) - phantom edges only
        self._process_constraints()

        
        # 7. Build Edges (Nets) - skip power nets (handled by power symbols)
        elk_fields = self._get_elk_support_fields()
        labeled_net_names = {
            nl.get("net_name", nl.get("id"))
            for nl in elk_fields.get("net_labels", [])
            if nl.get("id")
        }
        power_net_names: set[str] = set()
        for ps in elk_fields.get("power_symbols", []):
            ps_type = ps.get("type", "")
            if isinstance(ps_type, str) and ":" in ps_type:
                power_net_names.add(ps_type.split(":", 1)[1])
        special_refs = self._special_refs
        for net in self.circuit.nets:
            net_name = getattr(net, "name", None) or ""
            # Skip power nets - connections are defined via power symbol flows
            if net_name in power_net_names:
                continue
            
            # Sort pins by (ref, num) for deterministic edge ordering
            # SKiDL's net.pins uses set() internally, which has non-deterministic
            # iteration order due to Python's hash randomization
            pins = sorted(net.pins, key=lambda p: (p.ref, p.num))
            if len(pins) < 2:
                continue

            non_special_pins = [pin for pin in pins if pin.ref not in special_refs]
            if not non_special_pins:
                continue

            source = non_special_pins[0]
            for target in non_special_pins[1:]:
                source_pin_id = f"{source.ref}.{source.num}"
                target_pin_id = f"{target.ref}.{target.num}"

                # Specials only connect to labels/power symbols, never pin-to-pin wires.
                if source.ref in special_refs or target.ref in special_refs:
                    continue
                
                # Skip if BOTH pins are already connected via labels (no direct wire needed)
                if source_pin_id in self._covered_pins and target_pin_id in self._covered_pins:
                    continue
                # Avoid pin-to-pin wires inside the same component when labels exist
                if source.ref == target.ref and net_name in labeled_net_names:
                    continue
                
                edge = {
                    "id": f"e_{net_name}_{source.ref}_{target.ref}",
                    "sources": [source_pin_id],
                    "targets": [target_pin_id],
                    "properties": {
                        "net_name": net_name
                    }
                }
                self.graph["edges"].append(edge)

        # 6. Insurance: connect orphan pins to their designated targets
        # This handles both net labels and power symbols in a unified way
        
        # Collect all pins that already have edges
        connected_pins: set[str] = set()
        for edge in self.graph["edges"]:
            for src in edge.get("sources", []):
                connected_pins.add(src)
            for tgt in edge.get("targets", []):
                connected_pins.add(tgt)
        
        net_labels = elk_fields.get("net_labels", [])
        power_symbols = elk_fields.get("power_symbols", [])
        layout_chains = elk_fields.get("layout_chains", [])
        
        # Build mapping: net_name -> [label_ids] (for nets with explicit labels)
        net_name_to_labels: dict[str, list[str]] = {}
        for nl in net_labels:
            net_name = nl.get("net_name", nl.get("id"))
            label_id = nl.get("id")
            if net_name and label_id:
                net_name_to_labels.setdefault(net_name, []).append(label_id)

        # Round-robin cursors to distribute orphan pins across labels/power symbols
        label_cursors: dict[tuple[str, str, str], int] = {}
        power_cursors: dict[tuple[str, str, str], int] = {}
        
        # Build mapping: power_symbol_id -> net_name (from power symbol type)
        # e.g., {"GND_C1": "GND", "VCC_U1": "VCC"}
        power_symbol_to_net: dict[str, str] = {}
        power_symbol_ids: set[str] = set()
        for ps in power_symbols:
            ps_id = ps.get("id")
            ps_type = ps.get("type", "")  # e.g., "power:GND"
            if ps_id:
                power_symbol_ids.add(ps_id)
                if ":" in ps_type:
                    net_name = ps_type.split(":")[1]  # Extract "GND" from "power:GND"
                    power_symbol_to_net[ps_id] = net_name
        
        # Build mapping: (component, net_name) -> [power_symbol_ids] (from layout chains)
        component_net_to_power_symbols: dict[tuple[str, str], list[str]] = {}
        for key, powers in self._component_net_powers.items():
            component_net_to_power_symbols.setdefault(key, []).extend(powers)
        for chain in layout_chains:
            path = chain.get("path", [])
            if not isinstance(path, list):
                continue
            for left, right in zip(path, path[1:]):
                if not isinstance(left, str) or not isinstance(right, str):
                    continue
                comp_id = None
                power_id = None
                side = None
                if left in self._component_nodes and right in power_symbol_ids:
                    comp_id = left
                    power_id = right
                    side = "right"
                elif right in self._component_nodes and left in power_symbol_ids:
                    comp_id = right
                    power_id = left
                    side = "left"
                if not comp_id or not power_id:
                    continue
                net_name = power_symbol_to_net.get(power_id)
                if not net_name:
                    continue
                powers = component_net_to_power_symbols.setdefault((comp_id, net_name), [])
                if power_id not in powers:
                    powers.append(power_id)
                side_map = self._component_net_power_sides.setdefault((comp_id, net_name), {})
                if power_id not in side_map and side:
                    side_map[power_id] = side
        
        # Connect orphan pins to their targets
        for net in self.circuit.nets:
            net_name = net.name
            if not net_name:
                continue
            
            for pin in net.pins:
                pin_id = f"{pin.ref}.{pin.num}"
                if pin_id in connected_pins:
                    continue
                
                # Priority 1: Connect to net label if one exists
                labels = self._component_net_labels.get((pin.ref, net_name), [])
                if labels:
                    side_map = self._component_net_label_sides.get((pin.ref, net_name), {})
                    pin_side = self._pin_side(pin.ref, str(pin.num))
                    if pin_side:
                        side_labels = [lid for lid in labels if side_map.get(lid) == pin_side]
                        if side_labels:
                            labels = side_labels
                elif net_name in net_name_to_labels:
                    labels = net_name_to_labels[net_name]

                if labels:
                    pin_side = self._pin_side(pin.ref, str(pin.num)) or "any"
                    cursor_key = (pin.ref, net_name, pin_side)
                    idx = label_cursors.get(cursor_key, 0) % len(labels)
                    label_id = labels[idx]
                    label_cursors[cursor_key] = idx + 1
                    edge = {
                        "id": f"insurance_{pin.ref}_{pin.num}_to_{label_id}",
                        "sources": [pin_id],
                        "targets": [f"{label_id}.1"],
                        "layoutOptions": {
                            "elk.layered.priority.shortness": "10"
                        }
                    }
                    self.graph["edges"].append(edge)
                    connected_pins.add(pin_id)
                    print(f"[Insurance] Auto-connected orphan {pin_id} to label {label_id}")
                    continue
                
                # Priority 2: Connect to power symbols if component has any for this net
                powers = component_net_to_power_symbols.get((pin.ref, net_name), [])
                if powers:
                    side_map = self._component_net_power_sides.get((pin.ref, net_name), {})
                    pin_side = self._pin_side(pin.ref, str(pin.num))
                    if pin_side:
                        side_powers = [pid for pid in powers if side_map.get(pid) == pin_side]
                        if side_powers:
                            powers = side_powers
                    pin_side = pin_side or "any"
                    cursor_key = (pin.ref, net_name, pin_side)
                    idx = power_cursors.get(cursor_key, 0) % len(powers)
                    power_id = powers[idx]
                    power_cursors[cursor_key] = idx + 1
                    edge = {
                        "id": f"insurance_{pin.ref}_{pin.num}_to_{power_id}",
                        "sources": [pin_id],
                        "targets": [f"{power_id}.1"],
                        "layoutOptions": {
                            "elk.layered.priority.shortness": "10"
                        }
                    }
                    self.graph["edges"].append(edge)
                    connected_pins.add(pin_id)
                    print(f"[Insurance] Auto-connected orphan {pin_id} to power symbol {power_id}")

        return self.graph
