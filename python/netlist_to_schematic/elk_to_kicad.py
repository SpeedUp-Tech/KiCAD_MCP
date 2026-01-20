"""
elk_to_kicad - Step 3: Convert ELK Layout Output to KiCad Schematic

This module takes the layouted ELK graph JSON and generates a proper
KiCad schematic file with correctly positioned components and wires.
"""

import json
import logging
import math
import uuid
from heapq import heappop, heappush
from pathlib import Path
from typing import Union, Dict, Any, Tuple, List

from python.commands.kicad_schematics.schematic import SchematicManager, Schematic
from python.commands.kicad_schematics.component_schematic import ComponentManager
from python.commands.kicad_schematics.connection_schematic import ConnectionManager, SchematicCompiler
from python.commands.kicad_schematics.grid_utils import snap_to_grid, KICAD_SCHEMATIC_GRID_MM
from sexpdata import Symbol as SSymbol

logger = logging.getLogger(__name__)


# KiCad 6+ uses millimeters. ELK adapter outputs millimeters.
SCALE_FACTOR = 1.0

# Paper size dimensions (width, height in mm)
PAPER_SIZES = {
    "A4": (297.0, 210.0),
    "A3": (420.0, 297.0),
}

# Default centering offsets (A4)
OFFSET_X = 148.5  # A4 Center X (297/2)
OFFSET_Y = 105.0  # A4 Center Y (210/2)


def _grid_key(x: float, y: float, grid: float = KICAD_SCHEMATIC_GRID_MM) -> tuple[int, int]:
    return (int(round(x / grid)), int(round(y / grid)))


def _is_axis_aligned(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] == b[0] or a[1] == b[1]


def _segment_hits_points(
    a: tuple[int, int],
    b: tuple[int, int],
    points: set[tuple[int, int]],
    *,
    ignore: set[tuple[int, int]] | None = None,
) -> bool:
    """Return True if any point lies on the closed segment a-b (grid coords)."""
    ignore = ignore or set()
    if not _is_axis_aligned(a, b):
        return True
    ax, ay = a
    bx, by = b
    if ay == by:
        step = 1 if bx >= ax else -1
        for x in range(ax, bx + step, step):
            key = (x, ay)
            if key in ignore:
                continue
            if key in points:
                return True
        return False
    x = ax
    step = 1 if by >= ay else -1
    for y in range(ay, by + step, step):
        key = (ax, y)
        if key in ignore:
            continue
        if key in points:
            return True
    return False


def _route_manhattan_avoiding(
    start: tuple[float, float],
    end: tuple[float, float],
    *,
    occupied: set[tuple[int, int]],
    used_vertices: set[tuple[int, int]],
) -> list[list[float]] | None:
    """
    Route a short orthogonal path while avoiding accidental electrical merges.

    Avoid:
    - Passing through other connection points (pins/labels/power ports).
    - Touching existing wire points (junction shorts / misleading overlaps).
    """
    start_x, start_y = start
    end_x, end_y = end
    start_key = _grid_key(start_x, start_y)
    end_key = _grid_key(end_x, end_y)

    if start_key == end_key:
        return [[start_x, start_y]]

    forbidden = set(occupied) | set(used_vertices)

    def is_path_safe(path: list[tuple[float, float]]) -> bool:
        keys = [_grid_key(x, y) for x, y in path]
        keys = [k for i, k in enumerate(keys) if i == 0 or k != keys[i - 1]]
        if len(keys) < 2:
            return True
        if any(not _is_axis_aligned(a, b) for a, b in zip(keys, keys[1:])):
            return False

        internal_vertices = set(keys[1:-1])
        if internal_vertices & forbidden:
            return False

        for a, b in zip(keys, keys[1:]):
            if _segment_hits_points(a, b, forbidden, ignore={a, b, start_key, end_key}):
                return False
        return True

    direct = [(start_x, start_y), (end_x, end_y)]
    if is_path_safe(direct):
        return [[start_x, start_y], [end_x, end_y]]

    candidates: list[list[tuple[float, float]]] = [
        [(start_x, start_y), (end_x, start_y), (end_x, end_y)],
        [(start_x, start_y), (start_x, end_y), (end_x, end_y)],
    ]
    for cand in candidates:
        if is_path_safe(cand):
            return [[x, y] for x, y in cand]

    def _compress_keys(keys: list[tuple[int, int]]) -> list[tuple[int, int]]:
        if len(keys) <= 2:
            return keys
        out: list[tuple[int, int]] = [keys[0]]
        prev_dx = keys[1][0] - keys[0][0]
        prev_dy = keys[1][1] - keys[0][1]
        for i in range(1, len(keys) - 1):
            dx = keys[i + 1][0] - keys[i][0]
            dy = keys[i + 1][1] - keys[i][1]
            if (dx, dy) != (prev_dx, prev_dy):
                out.append(keys[i])
                prev_dx, prev_dy = dx, dy
        out.append(keys[-1])
        return out

    def _astar(bounds: tuple[int, int, int, int]) -> list[tuple[int, int]] | None:
        min_x, max_x, min_y, max_y = bounds

        def in_bounds(k: tuple[int, int]) -> bool:
            x, y = k
            return min_x <= x <= max_x and min_y <= y <= max_y

        def passable(k: tuple[int, int]) -> bool:
            if k == start_key or k == end_key:
                return True
            return k not in forbidden

        def heuristic(k: tuple[int, int]) -> int:
            return abs(k[0] - end_key[0]) + abs(k[1] - end_key[1])

        open_heap: list[tuple[int, int, tuple[int, int]]] = []
        heappush(open_heap, (heuristic(start_key), 0, start_key))
        came_from: dict[tuple[int, int], tuple[int, int]] = {}
        g_score: dict[tuple[int, int], int] = {start_key: 0}

        while open_heap:
            _, g, current = heappop(open_heap)
            if current == end_key:
                path: list[tuple[int, int]] = [end_key]
                while path[-1] != start_key:
                    path.append(came_from[path[-1]])
                path.reverse()
                return path

            if g != g_score.get(current):
                continue

            cx, cy = current
            for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                nxt = (nx, ny)
                if not in_bounds(nxt) or not passable(nxt):
                    continue
                tentative_g = g + 1
                if tentative_g >= g_score.get(nxt, 1_000_000_000):
                    continue
                came_from[nxt] = current
                g_score[nxt] = tentative_g
                heappush(open_heap, (tentative_g + heuristic(nxt), tentative_g, nxt))

        return None

    sx, sy = start_key
    ex, ey = end_key
    for margin in (8, 16, 32, 64, 128, 256):
        bounds = (
            min(sx, ex) - margin,
            max(sx, ex) + margin,
            min(sy, ey) - margin,
            max(sy, ey) + margin,
        )
        keys_path = _astar(bounds)
        if not keys_path:
            continue
        keys_path = _compress_keys(keys_path)
        step = KICAD_SCHEMATIC_GRID_MM
        points: list[list[float]] = []
        for idx, key in enumerate(keys_path):
            if idx == 0:
                points.append([start_x, start_y])
            elif idx == len(keys_path) - 1:
                points.append([end_x, end_y])
            else:
                points.append([key[0] * step, key[1] * step])
        return points

    return None


def _is_points_path_safe(
    points: list[list[float]],
    *,
    forbidden: set[tuple[int, int]],
    start_key: tuple[int, int],
    end_key: tuple[int, int],
) -> bool:
    keys = [_grid_key(x, y) for x, y in points]
    keys = [k for i, k in enumerate(keys) if i == 0 or k != keys[i - 1]]
    if len(keys) < 2:
        return True
    if any(not _is_axis_aligned(a, b) for a, b in zip(keys, keys[1:])):
        return False

    internal_vertices = set(keys[1:-1])
    forbidden_internal = forbidden - {start_key, end_key}
    if internal_vertices & forbidden_internal:
        return False

    ignore = {start_key, end_key}
    for a, b in zip(keys, keys[1:]):
        if _segment_hits_points(a, b, forbidden, ignore=ignore | {a, b}):
            return False
    return True


def _add_net_label(
    schematic: Schematic,
    name: str,
    x: float,
    y: float,
    label_type: str = "hierarchical",
    label_shape: str = "bidirectional",
    label_role: str = "both"
) -> None:
    """
    Add a net label (local label or hierarchical label) to the schematic.
    
    Args:
        schematic: The schematic object
        name: The net name to display
        x: X coordinate in mm
        y: Y coordinate in mm
        label_type: One of 'hierarchical', 'local' - determines label type
        label_shape: One of 'input', 'output', 'bidirectional' - determines arrow direction (hierarchical only)
        label_role: One of 'source', 'target', 'both' - determines rotation
    """
    snapped_x = snap_to_grid(x)
    snapped_y = snap_to_grid(y)
    
    # Determine label shape based on type (only used for hierarchical labels)
    if label_shape == "input":
        shape = "input"
    elif label_shape == "output":
        shape = "output"
    else:
        shape = "bidirectional"
    
    # Determine rotation based on role
    # Source labels: text should extend LEFT (rotation=180)
    # Target labels: text should extend RIGHT (rotation=0)
    if label_role == "source":
        rotation = 180
        justify = "right"
    else:
        rotation = 0
        justify = "left"
    
    effects_node = [
        SSymbol('effects'),
        [SSymbol('font'), [SSymbol('size'), 1.27, 1.27]],
        [SSymbol('justify'), SSymbol(justify)],
    ]
    
    if label_type == "local":
        # Local label - simple net name without direction arrows
        label_node = [
            SSymbol('label'),
            name,
            [SSymbol('at'), snapped_x, snapped_y, rotation],
            [SSymbol('fields_autoplaced')],
            effects_node,
            [SSymbol('uuid'), str(uuid.uuid4())],
        ]
    else:
        # Hierarchical label - has direction shape for sheet connections
        label_node = [
            SSymbol('hierarchical_label'),
            name,
            [SSymbol('shape'), SSymbol(shape)],
            [SSymbol('at'), snapped_x, snapped_y, rotation],
            [SSymbol('fields_autoplaced')],
            effects_node,
            [SSymbol('uuid'), str(uuid.uuid4())],
        ]
    
    schematic.tree.append(label_node)


def _collect_component_nodes(
    children: List[Dict[str, Any]], 
    parent_x: float = 0, 
    parent_y: float = 0
) -> List[Tuple[Dict[str, Any], float, float]]:
    """
    Recursively collect component nodes from a hierarchical ELK graph.
    
    Cluster nodes (like input_stage, output_stage) are containers - they have
    children but no 'lib' or 'symbol' properties. We extract their children
    and apply the parent's offset.
    
    Also collects net_label nodes which are identified by their node_type property.
    
    Returns:
        List of (node, absolute_x, absolute_y) tuples
    """
    result = []
    
    for node in children:
        node_x = node.get("x", 0) + parent_x
        node_y = node.get("y", 0) + parent_y
        
        # Check if this is a component (has lib/symbol), net_label, or a cluster
        meta = node.get("properties", {})
        
        if "lib" in meta and "symbol" in meta:
            # This is a component - collect it with its absolute position
            result.append((node, node_x, node_y))
        elif meta.get("node_type") == "net_label":
            # This is a net label node - collect it
            result.append((node, node_x, node_y))
        
        # Recurse into children (for clusters or any other container nodes)
        children_nodes = node.get("children", [])
        if children_nodes:
            result.extend(_collect_component_nodes(children_nodes, node_x, node_y))
    
    return result


def run_conversion(
    elk_input: Union[Path, str, Dict[str, Any]], 
    output_sch: Union[Path, str],
    paper_size: str = "A4",
) -> None:
    """
    Convert ELK layout output to KiCad schematic.
    
    Args:
        elk_input: Either a path to elk_output.json, or the graph dict directly
        output_sch: Path for the output .kicad_sch file
        paper_size: Paper size for schematic ("A4" or "A3")
    """
    output_sch = Path(output_sch)
    
    # Load graph
    if isinstance(elk_input, dict):
        graph = elk_input
    else:
        elk_file = Path(elk_input)
        if not elk_file.exists():
            raise FileNotFoundError(f"ELK output file not found: {elk_file}")
        with open(elk_file, 'r') as f:
            graph = json.load(f)

    # Create Schematic with appropriate paper size
    schematic = SchematicManager.create_schematic(output_sch.stem, {"paper": paper_size})
    
    # Calculate Graph Bounding Box to center on page
    root_w = graph.get("width", 0)
    root_h = graph.get("height", 0)
    
    # Get page center based on paper size
    page_w, page_h = PAPER_SIZES.get(paper_size, (297.0, 210.0))
    offset_x = page_w / 2
    offset_y = page_h / 2
    
    # Center: Page Center = offset. Graph Center = (w/2, h/2).
    shift_x = offset_x - (root_w / 2)
    shift_y = offset_y - (root_h / 2)

    # Build pin position lookup table
    # Key: port_id (e.g., "U1.1"), Value: (absolute_x, absolute_y) in KiCad coordinates
    pin_positions: Dict[str, Tuple[float, float]] = {}

    # Collect all component nodes recursively (handles clusters)
    all_nodes = _collect_component_nodes(graph.get("children", []))
    
    # Separate net label nodes from component/power symbol nodes
    component_nodes = []
    net_label_nodes = []
    
    for node, node_x, node_y in all_nodes:
        meta = node.get("properties", {})
        if meta.get("node_type") == "net_label":
            net_label_nodes.append((node, node_x, node_y))
        else:
            component_nodes.append((node, node_x, node_y))

    # Add Components and build pin position map
    for node, node_x, node_y in component_nodes:
        meta = node.get("properties", {})
        ref = node["id"]
        
        # Node dimensions
        w_raw = node.get("width", 0)
        h_raw = node.get("height", 0)
        
        # ELK Position is Top-Left of the Node Box
        # KiCad Position is the Symbol Origin (0,0)
        origin_off_x = meta.get("origin_offset_x", w_raw / 2)
        origin_off_y = meta.get("origin_offset_y", h_raw / 2)
        
        # Calculate symbol origin in KiCad coordinates
        # node_x and node_y are absolute positions (already include parent cluster offsets)
        # IMPORTANT: Snap to grid since ComponentManager will snap the symbol position
        pos_x = snap_to_grid((node_x + origin_off_x) * SCALE_FACTOR + shift_x)
        pos_y = snap_to_grid((node_y + origin_off_y) * SCALE_FACTOR + shift_y)

        # Build pin positions from port properties
        # Pin positions are exact: symbol origin + pin offset (no extra snapping)
        # The offsets come from the symbol definition which should already be grid-aligned
        for port in node.get("ports", []):
            port_id = port["id"]
            port_props = port.get("properties", {})

            if "kicad_offset_x" in port_props and "kicad_offset_y" in port_props:
                # Pin position = symbol origin + pin offset
                # Note: KiCad Y-axis is inverted (positive Y goes down)
                # The kicad_offset values are already rotated in elk_graph_adapter
                pin_x = pos_x + port_props["kicad_offset_x"]
                pin_y = pos_y - port_props["kicad_offset_y"]
                pin_positions[port_id] = (pin_x, pin_y)

        # Get rotation from node properties (set by rotation optimizer)
        rotation = meta.get("rotation", 0)

        comp_def = {
            "reference": ref,
            "value": meta.get("value", "Val"),
            "libId": f"{meta.get('lib', 'Lib')}:{meta.get('symbol', 'Sym')}",
            "x": pos_x,
            "y": pos_y,
            "rotation": rotation
        }
        
        try:
            ComponentManager.add_component(schematic, comp_def)
        except Exception as e:
            logger.error(f"Failed to add component {ref}: {e}")

    # Add Net Labels
    for node, node_x, node_y in net_label_nodes:
        meta = node.get("properties", {})
        label_id = node["id"]
        net_name = meta.get("net_name", label_id)
        label_type = meta.get("label_type", "hierarchical")  # hierarchical/local
        label_shape = meta.get("label_shape", "bidirectional")  # input/output/bidirectional
        label_role = meta.get("label_role", "both")
        
        # Get the port position - this is where wires will connect
        # In KiCad, the label's "at" position IS its connection point
        # So we must place the label at the port position, not node center
        port = node.get("ports", [{}])[0]  # Labels have one port
        port_x = port.get("x", 0)
        port_y = port.get("y", 0)
        
        # Calculate label position at the port (connection point)
        # Label positions should be on grid for clean schematic appearance
        label_x = snap_to_grid((node_x + port_x) * SCALE_FACTOR + shift_x)
        label_y = snap_to_grid((node_y + port_y) * SCALE_FACTOR + shift_y)

        # Store port position for wire connections
        # Use the same snapped position since the label IS at this position
        for p in node.get("ports", []):
            port_id = p["id"]
            # Label port positions match the label position (snapped)
            pin_positions[port_id] = (label_x, label_y)
        
        try:
            _add_net_label(schematic, net_name, label_x, label_y, label_type, label_shape, label_role)
        except Exception as e:
            logger.error(f"Failed to add net label {label_id}: {e}")

    label_port_ids: set[str] = set()
    for node, _, _ in net_label_nodes:
        for p in node.get("ports", []):
            pid = p.get("id")
            if pid:
                label_port_ids.add(pid)

    # Track which nets already have labels (from explicit net_labels in graph)
    labeled_nets: set[str] = set()
    for node, node_x, node_y in net_label_nodes:
        meta = node.get("properties", {})
        net_name = meta.get("net_name", node["id"])
        labeled_nets.add(net_name)
    
    # Also track net -> first wire endpoint (for adding labels to unlabeled nets)
    net_first_endpoint: dict[str, tuple[float, float]] = {}

    pin_point_keys = {_grid_key(x, y) for x, y in pin_positions.values()}

    # Treat symbol bodies as keep-out regions for post-routing so we don't route
    # through component graphics (e.g., wires through resistor bodies).
    component_keepouts: set[tuple[int, int]] = set()
    try:
        from python.commands.kicad_schematics.connection_schematic import _collect_symbol_bboxes

        grid = KICAD_SCHEMATIC_GRID_MM
        for (xmin, ymin, xmax, ymax), _sym in _collect_symbol_bboxes(schematic):
            gx0 = int(math.floor(min(xmin, xmax) / grid))
            gx1 = int(math.ceil(max(xmin, xmax) / grid))
            gy0 = int(math.floor(min(ymin, ymax) / grid))
            gy1 = int(math.ceil(max(ymin, ymax) / grid))
            for gx in range(gx0, gx1 + 1):
                for gy in range(gy0, gy1 + 1):
                    component_keepouts.add((gx, gy))
    except Exception:
        component_keepouts = set()

    occupied_points = pin_point_keys | component_keepouts
    used_wire_points: set[tuple[int, int]] = set()

    # Add Wires using ELK's routed sections
    # Wire endpoints must use exact pin positions from pin_positions map
    # to ensure proper connections (no snapping that could cause misalignment)
    for edge in graph.get("edges", []):
        edge_id = edge.get("id", "unknown")

        # Skip phantom edges - they're for layout guidance only, not real wires
        if edge_id.startswith(("flow_", "chain_", "align_", "adjacent_", "halign_")):
            continue

        sections = edge.get("sections", [])

        if not sections:
            continue

        # Get net_name from edge properties if available
        edge_props = edge.get("properties", {})
        net_name = edge_props.get("net_name")

        # Get source and target port IDs for exact pin positions
        sources = edge.get("sources", [])
        targets = edge.get("targets", [])
        source_port_id = sources[0] if sources else None
        target_port_id = targets[0] if targets else None

        for section in sections:
            points: List[List[float]] = []

            # Start point: use exact pin position if available, otherwise transform ELK coords
            if source_port_id and source_port_id in pin_positions:
                start_x, start_y = pin_positions[source_port_id]
            else:
                start = section.get("startPoint", {})
                start_x = start.get("x", 0) * SCALE_FACTOR + shift_x
                start_y = start.get("y", 0) * SCALE_FACTOR + shift_y
            points.append([start_x, start_y])

            # End point: use exact pin position if available, otherwise transform ELK coords
            if target_port_id and target_port_id in pin_positions:
                end_x, end_y = pin_positions[target_port_id]
            else:
                end = section.get("endPoint", {})
                end_x = end.get("x", 0) * SCALE_FACTOR + shift_x
                end_y = end.get("y", 0) * SCALE_FACTOR + shift_y

            # Bend points from ELK routing - snap these for clean routing
            for bp in section.get("bendPoints", []):
                bp_x = snap_to_grid(bp["x"] * SCALE_FACTOR + shift_x)
                bp_y = snap_to_grid(bp["y"] * SCALE_FACTOR + shift_y)
                points.append([bp_x, bp_y])
            points.append([end_x, end_y])

            # Track first endpoint for nets that need labels
            if net_name and net_name not in labeled_nets and net_name not in net_first_endpoint:
                net_first_endpoint[net_name] = (start_x, start_y)

            try:
                points = _orthogonalize_points(points)
                start_key = _grid_key(start_x, start_y)
                end_key = _grid_key(end_x, end_y)
                forbidden = occupied_points | used_wire_points

                if not _is_points_path_safe(points, forbidden=forbidden, start_key=start_key, end_key=end_key):
                    routed = _route_manhattan_avoiding(
                        (start_x, start_y),
                        (end_x, end_y),
                        occupied=occupied_points,
                        used_vertices=used_wire_points,
                    )
                    if routed is not None:
                        routed_points = _orthogonalize_points(routed)
                        if _is_points_path_safe(
                            routed_points,
                            forbidden=forbidden,
                            start_key=start_key,
                            end_key=end_key,
                        ):
                            points = routed_points

                keys = [_grid_key(x, y) for x, y in points]
                for a, b in zip(keys, keys[1:]):
                    ax, ay = a
                    bx, by = b
                    if ax == bx:
                        lo, hi = sorted((ay, by))
                        for gy in range(lo, hi + 1):
                            used_wire_points.add((ax, gy))
                    elif ay == by:
                        lo, hi = sorted((ax, bx))
                        for gx in range(lo, hi + 1):
                            used_wire_points.add((gx, ay))
                ConnectionManager.add_wire(
                    schematic,
                    start_point=None,
                    end_point=None,
                    properties={"points": points}
                )
            except Exception as e:
                logger.error(f"Failed to add wire {edge_id}: {e}")
    
    # Add local labels for unlabeled nets using their SKiDL net names
    for net_name, (label_x, label_y) in net_first_endpoint.items():
        try:
            _add_net_label(schematic, net_name, label_x, label_y, label_type="local")
        except Exception as e:
            logger.error(f"Failed to add net label {net_name}: {e}")

    # Compile: add labels to any remaining unlabeled nets (e.g., GND connections to power symbols)
    SchematicCompiler.compile(schematic)

    # Save
    SchematicManager.save_schematic(schematic, str(output_sch))


def _orthogonalize_points(points: List[List[float]]) -> List[List[float]]:
    if len(points) < 2:
        return points
    def _is_aligned(a: float, b: float, tol: float = 1e-6) -> bool:
        return abs(a - b) <= tol

    adjusted: List[List[float]] = [points[0]]
    for point in points[1:]:
        last = adjusted[-1]
        if not _is_aligned(last[0], point[0]) and not _is_aligned(last[1], point[1]):
            bend1 = [last[0], point[1]]
            bend2 = [point[0], last[1]]
            bend = bend1 if bend1 != last and bend1 != point else bend2
            if bend != last and bend != point:
                adjusted.append(bend)
        adjusted.append(point)

    deduped: List[List[float]] = [adjusted[0]]
    for point in adjusted[1:]:
        if point != deduped[-1]:
            deduped.append(point)

    simplified: List[List[float]] = [deduped[0]]
    for point in deduped[1:]:
        if len(simplified) >= 2:
            prev = simplified[-1]
            prev2 = simplified[-2]
            if (_is_aligned(prev2[0], prev[0]) and _is_aligned(prev[0], point[0])) or (
                _is_aligned(prev2[1], prev[1]) and _is_aligned(prev[1], point[1])
            ):
                simplified[-1] = point
                continue
        simplified.append(point)
    return simplified
