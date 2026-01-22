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

# Debug helpers for inspecting ELK routing quality.
# - Disable the post-route fallback so we can see ELK's own bendpoints.
# - Mark any "unsafe" grid points (would cause shorts / overlaps) in red.
DEBUG_DISABLE_UNSAFE_FALLBACK = True
DEBUG_MARK_UNSAFE_POINTS = False
DEBUG_UNSAFE_MARKER_SIZE_MM = KICAD_SCHEMATIC_GRID_MM * 0.9
DEBUG_UNSAFE_MARKER_STROKE_MM = 0.254

# Primary wire routing configuration (grid router).
# We use ELK for placement, then route wires ourselves on the KiCad schematic grid
# to guarantee "no accidental junctions" between different nets.
ROUTING_BOUNDS_MARGIN_MM = 80.0
ROUTING_WIRE_CLEARANCE_GRID = 1  # Radius (in grid steps) for "keep away" penalty near existing wires.
ROUTING_ENDPOINT_RELAX_GRID = 1  # Allow tighter spacing very near endpoints so nets can fan out.
ROUTING_TURN_PENALTY = 5  # Extra A* cost for changing direction (reduces zig-zag).
ROUTING_CROSS_WIRE_PENALTY = 50  # Penalty for crossing an existing wire (allowed, but discouraged).
ROUTING_NEAR_WIRE_PENALTY = 5  # Penalty for routing near existing wires (within ROUTING_WIRE_CLEARANCE_GRID).

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


def _segment_point_keys(a: tuple[int, int], b: tuple[int, int]) -> list[tuple[int, int]]:
    """Return all grid points on the closed segment a-b (axis aligned only)."""
    if not _is_axis_aligned(a, b):
        return []
    ax, ay = a
    bx, by = b
    keys: list[tuple[int, int]] = []
    if ay == by:
        step = 1 if bx >= ax else -1
        for x in range(ax, bx + step, step):
            keys.append((x, ay))
        return keys
    step = 1 if by >= ay else -1
    for y in range(ay, by + step, step):
        keys.append((ax, y))
    return keys


def _unsafe_grid_points_for_path(
    points: list[list[float]],
    *,
    forbidden: set[tuple[int, int]],
    start_key: tuple[int, int],
    end_key: tuple[int, int],
) -> set[tuple[int, int]]:
    """Return the set of forbidden grid points hit by this routed path."""
    keys = [_grid_key(x, y) for x, y in points]
    keys = [k for i, k in enumerate(keys) if i == 0 or k != keys[i - 1]]
    if len(keys) < 2:
        return set()

    unsafe: set[tuple[int, int]] = set()

    forbidden_internal = forbidden - {start_key, end_key}
    unsafe |= set(keys[1:-1]) & forbidden_internal

    ignore = {start_key, end_key}
    for a, b in zip(keys, keys[1:]):
        if not _is_axis_aligned(a, b):
            # Non-orthogonal is considered unsafe; mark both endpoints so it's visible.
            unsafe.add(a)
            unsafe.add(b)
            continue
        for key in _segment_point_keys(a, b):
            if key in ignore or key in {a, b}:
                continue
            if key in forbidden:
                unsafe.add(key)

    return unsafe


def _add_debug_x_marker(
    schematic: Schematic,
    *,
    x: float,
    y: float,
    size_mm: float,
    stroke_mm: float,
    color: tuple[int, int, int, int] = (255, 0, 0, 0),
) -> None:
    """Add a small red 'X' marker as a graphic polyline (non-electrical)."""
    half = float(size_mm) / 2.0
    x1, y1 = x - half, y - half
    x2, y2 = x + half, y + half
    x3, y3 = x - half, y + half
    x4, y4 = x + half, y - half

    def add_line(ax: float, ay: float, bx: float, by: float) -> None:
        node: list[Any] = [
            SSymbol("polyline"),
            [
                SSymbol("pts"),
                [SSymbol("xy"), round(ax, 6), round(ay, 6)],
                [SSymbol("xy"), round(bx, 6), round(by, 6)],
            ],
            [
                SSymbol("stroke"),
                [SSymbol("width"), round(float(stroke_mm), 6)],
                [SSymbol("type"), SSymbol("solid")],
                [SSymbol("color"), int(color[0]), int(color[1]), int(color[2]), int(color[3])],
            ],
            [SSymbol("fill"), [SSymbol("type"), SSymbol("none")]],
            [SSymbol("uuid"), str(uuid.uuid4())],
        ]
        schematic.tree.append(node)

    add_line(x1, y1, x2, y2)
    add_line(x3, y3, x4, y4)


def _route_manhattan_avoiding(
    start: tuple[float, float],
    end: tuple[float, float],
    *,
    occupied: set[tuple[int, int]],
    used_vertices: set[tuple[int, int]],
    used_edges: set[tuple[tuple[int, int], tuple[int, int]]],
    used_points: set[tuple[int, int]],
    bounds: tuple[int, int, int, int] | None = None,
    clearance: int = ROUTING_WIRE_CLEARANCE_GRID,
    endpoint_relax: int = ROUTING_ENDPOINT_RELAX_GRID,
    turn_penalty: int = ROUTING_TURN_PENALTY,
) -> list[list[float]] | None:
    """
    Route a short orthogonal path while avoiding accidental electrical merges.

    Avoid:
    - Passing through other connection points (pins/labels/power ports).
    - Overlapping any existing routed wire segment.
    - Creating accidental junctions by landing/turning on an existing wire.

    Notes:
    - Crossing an existing wire is allowed *only* at points that are not wire vertices,
      and we discourage it via a penalty so it's used when necessary.
    """
    start_x, start_y = snap_to_grid(float(start[0])), snap_to_grid(float(start[1]))
    end_x, end_y = snap_to_grid(float(end[0])), snap_to_grid(float(end[1]))
    start_key = _grid_key(start_x, start_y)
    end_key = _grid_key(end_x, end_y)

    if start_key == end_key:
        return None

    hard_forbidden = set(occupied) | set(used_vertices)
    dense_wire_points = set(used_points)
    no_turn_points = dense_wire_points - set(used_vertices)

    def _edge_key(a: tuple[int, int], b: tuple[int, int]) -> tuple[tuple[int, int], tuple[int, int]]:
        return (a, b) if a <= b else (b, a)

    def _in_relax_zone(k: tuple[int, int]) -> bool:
        if endpoint_relax <= 0:
            return False
        x, y = k
        sx, sy = start_key
        ex, ey = end_key
        return (
            max(abs(x - sx), abs(y - sy)) <= endpoint_relax
            or max(abs(x - ex), abs(y - ey)) <= endpoint_relax
        )

    def _wire_proximity_penalty(k: tuple[int, int]) -> int:
        if not dense_wire_points or _in_relax_zone(k):
            return 0
        if k in dense_wire_points:
            return ROUTING_CROSS_WIRE_PENALTY
        if clearance <= 0:
            return 0
        x, y = k
        for dx in range(-clearance, clearance + 1):
            for dy in range(-clearance, clearance + 1):
                if max(abs(dx), abs(dy)) > clearance:
                    continue
                if (x + dx, y + dy) in dense_wire_points:
                    return ROUTING_NEAR_WIRE_PENALTY
        return 0

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
            return k not in hard_forbidden

        def heuristic(x: int, y: int) -> int:
            return abs(x - end_key[0]) + abs(y - end_key[1])

        # Direction-aware A*: state = (x, y, dir)
        # dir: 0=+x, 1=-x, 2=+y, 3=-y, -1=start
        DIRS: tuple[tuple[int, int], ...] = ((1, 0), (-1, 0), (0, 1), (0, -1))
        start_state = (start_key[0], start_key[1], -1)

        open_heap: list[tuple[int, int, tuple[int, int, int]]] = []
        heappush(open_heap, (heuristic(*start_key), 0, start_state))
        came_from: dict[tuple[int, int, int], tuple[int, int, int]] = {}
        g_score: dict[tuple[int, int, int], int] = {start_state: 0}

        while open_heap:
            _, g, current = heappop(open_heap)
            if g != g_score.get(current):
                continue
            cx, cy, cdir = current
            if (cx, cy) == end_key:
                path_states: list[tuple[int, int, int]] = [current]
                while path_states[-1] != start_state:
                    path_states.append(came_from[path_states[-1]])
                path_states.reverse()
                path: list[tuple[int, int]] = [(sx, sy) for sx, sy, _ in path_states]
                # De-dup consecutive identical vertices (can happen with dir-state reconstruction).
                dedup: list[tuple[int, int]] = []
                for k in path:
                    if not dedup or k != dedup[-1]:
                        dedup.append(k)
                return dedup

            for ndir, (dx, dy) in enumerate(DIRS):
                nx, ny = cx + dx, cy + dy
                nxt_vertex = (nx, ny)
                if not in_bounds(nxt_vertex) or not passable(nxt_vertex):
                    continue
                if _edge_key((cx, cy), nxt_vertex) in used_edges:
                    continue
                step_cost = 1
                turning = cdir != -1 and cdir != ndir
                if turning and (cx, cy) in no_turn_points:
                    continue
                if turning:
                    step_cost += int(turn_penalty)
                step_cost += int(_wire_proximity_penalty(nxt_vertex))
                nxt_state = (nx, ny, ndir)
                tentative_g = g + step_cost
                if tentative_g >= g_score.get(nxt_state, 1_000_000_000):
                    continue
                came_from[nxt_state] = current
                g_score[nxt_state] = tentative_g
                heappush(open_heap, (tentative_g + heuristic(nx, ny), tentative_g, nxt_state))

        return None

    sx, sy = start_key
    ex, ey = end_key
    global_bounds = bounds
    for margin in (8, 16, 32, 64, 128, 256, 512):
        cand_bounds = (
            min(sx, ex) - margin,
            max(sx, ex) + margin,
            min(sy, ey) - margin,
            max(sy, ey) + margin,
        )
        if global_bounds is not None:
            gb_min_x, gb_max_x, gb_min_y, gb_max_y = global_bounds
            cand_bounds = (
                max(cand_bounds[0], gb_min_x),
                min(cand_bounds[1], gb_max_x),
                max(cand_bounds[2], gb_min_y),
                min(cand_bounds[3], gb_max_y),
            )
        keys_path = _astar(cand_bounds)
        if not keys_path:
            continue
        keys_path = _compress_keys(keys_path)
        step = KICAD_SCHEMATIC_GRID_MM
        return [[k[0] * step, k[1] * step] for k in keys_path]

    if global_bounds is not None:
        keys_path = _astar(global_bounds)
        if keys_path:
            keys_path = _compress_keys(keys_path)
            step = KICAD_SCHEMATIC_GRID_MM
            return [[k[0] * step, k[1] * step] for k in keys_path]

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
    no_connect_pins = set((graph.get("properties", {}) or {}).get("no_connect_pins", []) or [])

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

    # Add KiCad no-connect markers for SKiDL `NC()` pins.
    for port_id in sorted(p for p in no_connect_pins if isinstance(p, str)):
        pos = pin_positions.get(port_id)
        if not pos:
            continue
        x, y = pos
        schematic.tree.append([
            SSymbol("no_connect"),
            [SSymbol("at"), snap_to_grid(x), snap_to_grid(y)],
            [SSymbol("uuid"), str(uuid.uuid4())],
        ])

    # Add Net Labels
    label_port_net_name: dict[str, str] = {}
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
            label_port_net_name[port_id] = net_name
        
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
    # Track routed geometry per-net so same-net wires can merge/branch,
    # while different nets avoid accidental junctions.
    used_wire_vertices_by_net: dict[str, set[tuple[int, int]]] = {}
    used_wire_edges_by_net: dict[str, set[tuple[tuple[int, int], tuple[int, int]]]] = {}
    used_wire_points_by_net: dict[str, set[tuple[int, int]]] = {}

    def _edge_key(a: tuple[int, int], b: tuple[int, int]) -> tuple[tuple[int, int], tuple[int, int]]:
        return (a, b) if a <= b else (b, a)

    def _union_except(items: dict[str, set[Any]], current: str) -> set[Any]:
        out: set[Any] = set()
        for net_id, values in items.items():
            if net_id == current:
                continue
            out |= values
        return out

    # Route wires on-grid in KiCad after placement.
    # ELK still provides placement + pin/label ports; we ignore ELK bendpoints to
    # avoid order-dependent snapping/junction artifacts.
    grid = KICAD_SCHEMATIC_GRID_MM
    routing_bounds = (
        int(math.floor((shift_x - ROUTING_BOUNDS_MARGIN_MM) / grid)),
        int(math.ceil((shift_x + root_w + ROUTING_BOUNDS_MARGIN_MM) / grid)),
        int(math.floor((shift_y - ROUTING_BOUNDS_MARGIN_MM) / grid)),
        int(math.ceil((shift_y + root_h + ROUTING_BOUNDS_MARGIN_MM) / grid)),
    )

    for edge in graph.get("edges", []):
        edge_id = edge.get("id", "unknown")

        # Skip phantom edges - they're for layout guidance only, not real wires
        if edge_id.startswith(("flow_", "chain_", "align_", "adjacent_", "halign_")):
            continue

        # Get net_name from edge properties if available
        edge_props = edge.get("properties", {})
        net_name = edge_props.get("net_name")

        # Get source and target port IDs for exact pin positions
        sources = edge.get("sources", [])
        targets = edge.get("targets", [])
        source_port_id = sources[0] if sources else None
        target_port_id = targets[0] if targets else None

        # Resolve endpoints in KiCad coordinates (must exist for real wiring edges).
        start_pos = pin_positions.get(source_port_id) if source_port_id else None
        end_pos = pin_positions.get(target_port_id) if target_port_id else None
        if start_pos is None or end_pos is None:
            continue

        start_x, start_y = start_pos
        end_x, end_y = end_pos

        # Track first endpoint for nets that need labels
        if net_name and net_name not in labeled_nets and net_name not in net_first_endpoint:
            net_first_endpoint[net_name] = (start_x, start_y)

        try:
            start_key = _grid_key(start_x, start_y)
            end_key = _grid_key(end_x, end_y)
            if start_key == end_key:
                continue

            route_net_id = None
            if isinstance(net_name, str) and net_name:
                route_net_id = net_name
            else:
                route_net_id = (
                    label_port_net_name.get(source_port_id or "")
                    or label_port_net_name.get(target_port_id or "")
                    or edge_id
                )

            other_vertices = _union_except(used_wire_vertices_by_net, route_net_id)
            other_edges = _union_except(used_wire_edges_by_net, route_net_id)
            other_points = _union_except(used_wire_points_by_net, route_net_id)

            routed = _route_manhattan_avoiding(
                (start_x, start_y),
                (end_x, end_y),
                occupied=occupied_points,
                used_vertices=other_vertices,
                used_edges=other_edges,
                used_points=other_points,
                bounds=routing_bounds,
            )
            if routed is None:
                logger.error(f"Failed to route wire {edge_id}: no path")
                continue

            points = _orthogonalize_points(routed)

            keys = [_grid_key(x, y) for x, y in points]
            net_vertices = used_wire_vertices_by_net.setdefault(route_net_id, set())
            net_edges = used_wire_edges_by_net.setdefault(route_net_id, set())
            net_points = used_wire_points_by_net.setdefault(route_net_id, set())

            for k in keys:
                net_vertices.add(k)

            for a, b in zip(keys, keys[1:]):
                ax, ay = a
                bx, by = b
                if ax == bx:
                    step = 1 if by >= ay else -1
                    y = ay
                    while y != by:
                        u = (ax, y)
                        v = (ax, y + step)
                        net_edges.add(_edge_key(u, v))
                        net_points.add(u)
                        net_points.add(v)
                        y += step
                elif ay == by:
                    step = 1 if bx >= ax else -1
                    x = ax
                    while x != bx:
                        u = (x, ay)
                        v = (x + step, ay)
                        net_edges.add(_edge_key(u, v))
                        net_points.add(u)
                        net_points.add(v)
                        x += step

            ConnectionManager.add_wire(
                schematic,
                start_point=None,
                end_point=None,
                properties={"points": points},
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
