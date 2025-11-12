from __future__ import annotations

import heapq
import math
from dataclasses import dataclass
from typing import Any, Iterable, List, Optional, Sequence, Set, Tuple

from python.commands.grid_utils import KICAD_SCHEMATIC_GRID_MM

Point = Tuple[float, float]
Rect = Tuple[float, float, float, float]
BBox = Tuple[Rect, Any]

COORD_KEY_PRECISION = 6
COLLISION_BUFFER_MM = 0.5
DISTANCE_PENALTY_WEIGHT = 2.0
CLEARANCE_THRESHOLD_MM = 1.0 * KICAD_SCHEMATIC_GRID_MM
INSIDE_BBOX_PENALTY = 10.0
POST_PROCESS_RECT_CLEARANCE_MM = 0.5 * KICAD_SCHEMATIC_GRID_MM
EDGE_CONGESTION_WEIGHT = 1.5
CONGESTION_SATURATION = 3.0
PIN_CONGESTION_RELAX_MM = 1.0 * KICAD_SCHEMATIC_GRID_MM
BEND_PENALTY_WEIGHT = 2.0

__all__ = [
    "BBox",
    "COLLISION_BUFFER_MM",
    "COORD_KEY_PRECISION",
    "RoutingObstacles",
    "SAFE_ROUTING_DEFAULT_EXPANSIONS",
    "coord_key",
    "point_segment_distance",
    "POST_PROCESS_RECT_CLEARANCE_MM",
    "manhattan_route_core",
    "safe_manhattan_route",
    "segment_length",
]

SAFE_ROUTING_DEFAULT_EXPANSIONS = 12000


def coord_key(x: float, y: float, *, precision: int = COORD_KEY_PRECISION) -> Point:
    """Round a coordinate pair to a stable key precision."""
    return (round(float(x), precision), round(float(y), precision))


def segment_length(a: Point, b: Point) -> float:
    """Return the Euclidean distance between two points."""
    return math.hypot(b[0] - a[0], b[1] - a[1])


def point_segment_distance(point: Point, a: Point, b: Point) -> float:
    """Distance from a point to the closest point on the segment a-b."""
    px, py = point
    ax, ay = a
    bx, by = b
    dx = bx - ax
    dy = by - ay
    seg_len_sq = dx * dx + dy * dy
    if seg_len_sq <= 0.0:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / seg_len_sq
    t = max(0.0, min(1.0, t))
    nearest_x = ax + t * dx
    nearest_y = ay + t * dy
    return math.hypot(px - nearest_x, py - nearest_y)


def _distance_to_rect_edge(px: float, py: float, rect: Rect) -> float:
    """Shortest distance from a point to a rectangle edge (0 if inside)."""
    xmin, ymin, xmax, ymax = rect
    if px < xmin:
        dx = xmin - px
    elif px > xmax:
        dx = px - xmax
    else:
        dx = 0.0

    if py < ymin:
        dy = ymin - py
    elif py > ymax:
        dy = py - ymax
    else:
        dy = 0.0

    if dx == 0.0 and dy == 0.0:
        return 0.0
    return math.hypot(dx, dy)


@dataclass(frozen=True)
class RoutingObstacles:
    """Precomputed schematic geometry used during routing."""

    pin_vertices: Sequence[Point]
    node_vertices: Sequence[Point]
    symbol_bboxes: Sequence[BBox]

    def build_forbidden_sets(
        self,
        allowed_endpoints: Set[Point],
    ) -> Tuple[List[Point], List[Point]]:
        """
        Return (forbidden_vertices, pin_collision_points).

        forbidden_vertices: All pin/node vertices except the endpoints.
        pin_collision_points: Pin vertices excluded from endpoints (used for clearance).
        """
        pin_keys = {coord_key(*pt) for pt in self.pin_vertices}
        node_keys = {coord_key(*pt) for pt in self.node_vertices}
        combined = (pin_keys | node_keys) - allowed_endpoints
        forbidden_vertices = list(combined)
        pin_collisions = [pt for pt in pin_keys if pt not in allowed_endpoints]
        return forbidden_vertices, pin_collisions


def _build_wire_segments_from_nodes(node_vertices: Sequence[Point]) -> List[Tuple[Point, Point]]:
    """Heuristically reconstruct wire segments from schematic wire nodes."""
    segments: List[Tuple[Point, Point]] = []
    pts = list(node_vertices)
    idx = 0
    total = len(pts)
    while idx + 1 < total:
        start = pts[idx]
        end = pts[idx + 1]
        idx += 2
        if start == end:
            continue
        if not (math.isclose(start[0], end[0]) or math.isclose(start[1], end[1])):
            continue
        segments.append((start, end))
    return segments


def _edge_key(a: Point, b: Point) -> Tuple[Point, Point]:
    """Return a stable key for an undirected grid edge."""
    pa = coord_key(*a)
    pb = coord_key(*b)
    return (pa, pb) if pa <= pb else (pb, pa)


def _iter_segment_edges(a: Point, b: Point, grid_step: float) -> Iterable[Tuple[Point, Point]]:
    """Yield edge keys that cover the Manhattan segment a-b."""
    ax, ay = a
    bx, by = b
    if math.isclose(ax, bx):
        direction = 1.0 if by > ay else -1.0
        cur = ay
        while not math.isclose(cur, by):
            step = direction * min(grid_step, abs(by - cur))
            nxt = cur + step
            yield _edge_key((ax, cur), (ax, nxt))
            cur = nxt
    elif math.isclose(ay, by):
        direction = 1.0 if bx > ax else -1.0
        cur = ax
        while not math.isclose(cur, bx):
            step = direction * min(grid_step, abs(bx - cur))
            nxt = cur + step
            yield _edge_key((cur, ay), (nxt, ay))
            cur = nxt


def _build_edge_congestion_map(
    node_vertices: Sequence[Point],
    grid_step: float,
) -> dict[Tuple[Point, Point], int]:
    """Convert existing wires into a congestion counter per edge."""
    congestion: dict[Tuple[Point, Point], int] = {}
    for start, end in _build_wire_segments_from_nodes(node_vertices):
        for edge in _iter_segment_edges(start, end, grid_step):
            congestion[edge] = congestion.get(edge, 0) + 1
    return congestion


def _iter_symbol_pin_candidates(symbol: Any) -> List[Any]:
    """Return best-effort list of pin-like objects from a symbol."""
    pins_attr = getattr(symbol, "pin", None)
    if pins_attr is None:
        return []

    if isinstance(pins_attr, list):
        return list(pins_attr)

    elements = getattr(pins_attr, "_elements", None)
    if isinstance(elements, list) and elements:
        return list(elements)

    if getattr(pins_attr, "entity_type", None) == "pin":
        return [pins_attr]

    collected: List[Any] = []
    try:
        for candidate in pins_attr:
            if getattr(candidate, "entity_type", None) == "pin":
                collected.append(candidate)
    except Exception:
        collected = []

    if collected:
        return collected

    if isinstance(pins_attr, (str, bytes)):
        return []

    return [pins_attr]


def _pin_locations_from_symbol(symbol: Any) -> List[Point]:
    """Extract absolute pin tip coordinates from a KiCad symbol wrapper."""
    if symbol is None:
        return []
    points: List[Point] = []
    for pin in _iter_symbol_pin_candidates(symbol):
        loc = getattr(pin, "location", None)
        if loc is None:
            loc = getattr(pin, "_mcp_location", None)
        if loc is None:
            continue
        try:
            points.append((float(loc.x), float(loc.y)))
        except Exception:
            continue
    return points


def _expand_bbox_with_pin_locations(rect: Rect, symbol: Any) -> Rect:
    """Grow the symbol body bbox so that all pin tips fall inside."""
    xmin, ymin, xmax, ymax = rect
    expanded_xmin = xmin
    expanded_ymin = ymin
    expanded_xmax = xmax
    expanded_ymax = ymax

    for px, py in _pin_locations_from_symbol(symbol):
        if px < expanded_xmin:
            expanded_xmin = px
        if px > expanded_xmax:
            expanded_xmax = px
        if py < expanded_ymin:
            expanded_ymin = py
        if py > expanded_ymax:
            expanded_ymax = py

    return (expanded_xmin, expanded_ymin, expanded_xmax, expanded_ymax)


def _expand_symbol_bboxes(symbol_bboxes: Sequence[BBox]) -> List[BBox]:
    """Return new bbox list whose rectangles include the protruding pins."""
    expanded: List[BBox] = []
    for rect, symbol in symbol_bboxes:
        expanded.append((_expand_bbox_with_pin_locations(rect, symbol), symbol))
    return expanded


def _manhattan_astar_route(
    start: Point,
    end: Point,
    step: float,
    rects: Sequence[Rect],
    forbidden_points: Iterable[Point],
    *,
    max_expansions: int,
    bend_penalty: float,
    rect_blocking: bool = False,
    edge_congestion: Optional[dict[Tuple[Point, Point], int]] = None,
) -> Optional[List[Point]]:
    """A* expansion over a Manhattan grid with soft penalties for symbol bodies."""
    sx, sy = start
    ex, ey = end

    allowed_endpoints = {coord_key(sx, sy), coord_key(ex, ey)}
    forb_set = {
        coord_key(px, py)
        for (px, py) in set(forbidden_points)
        if coord_key(px, py) not in allowed_endpoints
    }

    span_steps_x = abs(sx - ex) / step if step else 0.0
    span_steps_y = abs(sy - ey) / step if step else 0.0
    margin_steps = max(10.0, max(span_steps_x, span_steps_y) + 6.0)
    if rect_blocking:
        margin_steps = max(margin_steps, max(span_steps_x, span_steps_y) + 40.0)
    margin_distance = margin_steps * step

    xmin = min(sx, ex) - margin_distance
    xmax = max(sx, ex) + margin_distance
    ymin = min(sy, ey) - margin_distance
    ymax = max(sy, ey) + margin_distance

    rect_list = list(rects)

    def _inside_any_rect(px: float, py: float) -> bool:
        for xmin_r, ymin_r, xmax_r, ymax_r in rect_list:
            if (xmin_r <= px <= xmax_r) and (ymin_r <= py <= ymax_r):
                return True
        return False

    def blocked(px: float, py: float) -> bool:
        if px < xmin or px > xmax or py < ymin or py > ymax:
            return True
        key = coord_key(px, py)
        if key in forb_set:
            return True
        if rect_blocking and key not in allowed_endpoints and _inside_any_rect(px, py):
            return True
        return False

    dirs = [(1, 0), (-1, 0), (0, 1), (0, -1)]

    def heuristic(px: float, py: float) -> float:
        return abs(px - ex) + abs(py - ey)

    def state_key(px: float, py: float, direction: int) -> Tuple[Point, int]:
        return (coord_key(px, py), direction)

    start_state = (sx, sy, -1)
    open_heap: List[Tuple[float, float, Tuple[float, float, int]]] = []
    heapq.heappush(open_heap, (heuristic(sx, sy), 0.0, start_state))
    came_from: dict[Tuple[Point, int], Tuple[float, float, int]] = {}
    gscore: dict[Tuple[Point, int], float] = {state_key(*start_state): 0.0}

    expansions = 0
    goal_eps = 1e-6
    end_key = coord_key(ex, ey)

    while open_heap and expansions < max_expansions:
        f_cost, g_cost, (x, y, dprev) = heapq.heappop(open_heap)
        expansions += 1

        current_key = coord_key(x, y)
        if current_key == end_key:
            path: List[Point] = [(x, y)]
            state = (x, y, dprev)
            skey = state_key(*state)
            while skey in came_from:
                state = came_from[skey]
                path.append((state[0], state[1]))
                skey = state_key(*state)
            path.reverse()

            simplified: List[Point] = []
            for pt in path:
                if not simplified:
                    simplified.append(pt)
                    continue
                simplified.append(pt)
                if len(simplified) >= 3:
                    a, b, c = simplified[-3], simplified[-2], simplified[-1]
                    if (
                        (abs(a[0] - b[0]) <= goal_eps and abs(b[0] - c[0]) <= goal_eps)
                        or (abs(a[1] - b[1]) <= goal_eps and abs(b[1] - c[1]) <= goal_eps)
                    ):
                        simplified.pop(-2)

            if coord_key(*simplified[0]) != coord_key(sx, sy):
                simplified.insert(0, (sx, sy))
            if coord_key(*simplified[-1]) != end_key:
                simplified.append((ex, ey))

            return simplified

        for i, (dx, dy) in enumerate(dirs):
            candidates: List[Point] = []

            if dx != 0:
                nx = round(x + dx * step, COORD_KEY_PRECISION)
                ny = round(y, COORD_KEY_PRECISION)
                candidates.append((nx, ny))

                remaining_x = ex - x
                if remaining_x != 0.0 and (remaining_x > 0) == (dx > 0):
                    if abs(remaining_x) <= step + goal_eps:
                        candidates.append((round(ex, COORD_KEY_PRECISION), ny))
            else:
                nx = round(x, COORD_KEY_PRECISION)
                ny = round(y + dy * step, COORD_KEY_PRECISION)
                candidates.append((nx, ny))

                remaining_y = ey - y
                if remaining_y != 0.0 and (remaining_y > 0) == (dy > 0):
                    if abs(remaining_y) <= step + goal_eps:
                        candidates.append((nx, round(ey, COORD_KEY_PRECISION)))

            for nx, ny in candidates:
                if blocked(nx, ny):
                    continue

                cost = 1.0
                if dprev != -1 and dprev != i:
                    cost += bend_penalty

                min_distance = float("inf")
                inside = False
                for rect in rects:
                    dist = _distance_to_rect_edge(nx, ny, rect)
                    min_distance = min(min_distance, dist)
                    xmin_r, ymin_r, xmax_r, ymax_r = rect
                    if (xmin_r <= nx <= xmax_r) and (ymin_r <= ny <= ymax_r):
                        inside = True

                if inside:
                    cost += INSIDE_BBOX_PENALTY
                elif min_distance < CLEARANCE_THRESHOLD_MM:
                    distance_penalty = (CLEARANCE_THRESHOLD_MM - min_distance) * DISTANCE_PENALTY_WEIGHT
                    cost += distance_penalty
                if edge_congestion:
                    edge = _edge_key((x, y), (nx, ny))
                    base = edge_congestion.get(edge, 0)
                    if base > 0:
                        edge_mid = ((edge[0][0] + edge[1][0]) * 0.5, (edge[0][1] + edge[1][1]) * 0.5)
                        scale = EDGE_CONGESTION_WEIGHT * min(base, CONGESTION_SATURATION)
                        if (
                            segment_length(edge_mid, start) <= PIN_CONGESTION_RELAX_MM
                            or segment_length(edge_mid, end) <= PIN_CONGESTION_RELAX_MM
                        ):
                            scale *= 0.5
                        cost += scale

                tentative_g = g_cost + cost
                ns = state_key(nx, ny, i)
                if tentative_g < gscore.get(ns, float("inf")):
                    gscore[ns] = tentative_g
                    came_from[ns] = (x, y, dprev)
                    heapq.heappush(open_heap, (tentative_g + heuristic(nx, ny), tentative_g, (nx, ny, i)))

    return None


def _route_respects_clearance(
    points: Sequence[Point],
    forbidden_vertices: Sequence[Point],
    pin_collision_points: Sequence[Point],
    *,
    collision_buffer: float,
) -> bool:
    """Validate that a route avoids forbidden vertices and keeps clearance."""
    if len(points) < 2:
        return False

    forbidden_set = list(forbidden_vertices)
    for pt in points[1:-1]:
        for fv in forbidden_set:
            if segment_length(pt, fv) < collision_buffer:
                return False

    for a, b in zip(points, points[1:]):
        seg_len = segment_length(a, b)
        if seg_len <= 0.0:
            return False
        for pc in pin_collision_points:
            if point_segment_distance(pc, a, b) < collision_buffer:
                return False

    return True


def manhattan_route_core(
    start: Point,
    end: Point,
    *,
    obstacles: RoutingObstacles,
    grid_step: float = KICAD_SCHEMATIC_GRID_MM,
    max_astar_expansions: int = SAFE_ROUTING_DEFAULT_EXPANSIONS,
    bend_penalty: float = BEND_PENALTY_WEIGHT,
    collision_buffer: float = COLLISION_BUFFER_MM,
    edge_congestion: Optional[dict[Tuple[Point, Point], int]] = None,
    rect_blocking: bool = False,
) -> List[Point]:
    """
    Route a Manhattan wire between start and end while respecting schematic obstacles.

    Args:
        start, end: Millimetre coordinates.
        obstacles: Precomputed schematic geometry describing pins, wire nodes, and symbol bboxes.
        grid_step: Routing step in millimetres (defaults to KiCAD grid).
        max_astar_expansions: Expansion cap for the primary A* search.
        bend_penalty: Penalty applied when the direction changes.
        collision_buffer: Clearance radius around forbidden vertices.
        edge_congestion: Optional precomputed congestion map for existing wires.
        rect_blocking: If True, treat symbol rectangles as hard obstacles.

    Returns:
        List of points forming the routed polyline (includes original endpoints).

    Raises:
        ValueError: If no valid route can be found.
    """
    s = (float(start[0]), float(start[1]))
    e = (float(end[0]), float(end[1]))

    if s == e:
        raise ValueError("Pins share the same coordinates; cannot connect")

    allowed_endpoints = {coord_key(*s), coord_key(*e)}
    forbidden_vertices, pin_collision_points = obstacles.build_forbidden_sets(allowed_endpoints)

    rects = [bbox[0] for bbox in obstacles.symbol_bboxes]
    if edge_congestion is None:
        edge_congestion = _build_edge_congestion_map(obstacles.node_vertices, grid_step)
    astar_route = _manhattan_astar_route(
        s,
        e,
        grid_step,
        rects,
        forbidden_vertices,
        max_expansions=max_astar_expansions,
        bend_penalty=bend_penalty,
        rect_blocking=rect_blocking,
        edge_congestion=edge_congestion,
    )
    if astar_route is not None and _route_respects_clearance(astar_route, forbidden_vertices, pin_collision_points, collision_buffer=collision_buffer):
        return astar_route

    forbidden_set = set(forbidden_vertices)
    escape_vectors = [(0, grid_step), (0, -grid_step), (grid_step, 0), (-grid_step, 0)]

    def try_escape(from_start: bool) -> Optional[List[Point]]:
        for dx, dy in escape_vectors:
            esc = (
                (s[0] + dx, s[1] + dy)
                if from_start
                else (e[0] + dx, e[1] + dy)
            )
            esc = (
                round(esc[0], COORD_KEY_PRECISION),
                round(esc[1], COORD_KEY_PRECISION),
            )

            if coord_key(*esc) in forbidden_set:
                continue

            if from_start:
                escape_route = _manhattan_astar_route(
                    esc,
                    e,
                    grid_step,
                    rects,
                    forbidden_vertices,
                    max_expansions=8000,
                    bend_penalty=bend_penalty,
                    rect_blocking=rect_blocking,
                    edge_congestion=edge_congestion,
                )
                if escape_route is not None:
                    full_route = [s] + escape_route
                    if _route_respects_clearance(full_route, forbidden_vertices, pin_collision_points, collision_buffer=collision_buffer):
                        return full_route
            else:
                escape_route = _manhattan_astar_route(
                    s,
                    esc,
                    grid_step,
                    rects,
                    forbidden_vertices,
                    max_expansions=8000,
                    bend_penalty=bend_penalty,
                    rect_blocking=rect_blocking,
                    edge_congestion=edge_congestion,
                )
                if escape_route is not None:
                    full_route = escape_route + [e]
                    if _route_respects_clearance(full_route, forbidden_vertices, pin_collision_points, collision_buffer=collision_buffer):
                        return full_route
        return None

    route = try_escape(True) or try_escape(False)
    if route is not None:
        return route

    raise ValueError(
        "Routing failed: could not find a Manhattan path that avoids pin endpoints/nodes and symbol bodies. "
        "Try repositioning or rotating one of the components to provide clearance."
    )


def _range_overlap(a1: float, a2: float, b1: float, b2: float) -> bool:
    """Return True if the 1D ranges overlap."""
    low = max(min(a1, a2), min(b1, b2))
    high = min(max(a1, a2), max(b1, b2))
    return low <= high


def _segment_crosses_rect(a: Point, b: Point, rect: Rect) -> bool:
    """Check if an axis-aligned segment overlaps the rectangle interior."""
    ax, ay = a
    bx, by = b
    xmin, ymin, xmax, ymax = rect

    if ax == bx:
        x = ax
        if xmin <= x <= xmax:
            return _range_overlap(ay, by, ymin, ymax)
        return False

    if ay == by:
        y = ay
        if ymin <= y <= ymax:
            return _range_overlap(ax, bx, xmin, xmax)
        return False

    return False


def _segment_crosses_any_rect(a: Point, b: Point, rects: Sequence[Rect]) -> bool:
    return any(_segment_crosses_rect(a, b, rect) for rect in rects)


def _point_on_segment(pt: Point, a: Point, b: Point, *, eps: float = 1e-9) -> bool:
    """Return True if point lies on the segment between a and b."""
    ax, ay = a
    bx, by = b
    px, py = pt

    cross = (px - ax) * (by - ay) - (py - ay) * (bx - ax)
    if abs(cross) > eps:
        return False

    min_x = min(ax, bx) - eps
    max_x = max(ax, bx) + eps
    min_y = min(ay, by) - eps
    max_y = max(ay, by) + eps

    return min_x <= px <= max_x and min_y <= py <= max_y


def _segment_passes_through_existing_point(
    a: Point,
    b: Point,
    points: Sequence[Point],
    *,
    excluded: Set[Point],
) -> bool:
    for pt in points:
        if pt in excluded:
            continue
        if _point_on_segment(pt, a, b):
            return True
    return False


def _segment_hits_forbidden_points(
    a: Point,
    b: Point,
    forbidden_points: Iterable[Point],
    *,
    collision_buffer: float,
) -> bool:
    for fp in forbidden_points:
        if fp == a or fp == b:
            continue
        if point_segment_distance(fp, a, b) < collision_buffer:
            return True
    return False


def _segment_congestion_penalty(
    a: Point,
    b: Point,
    *,
    grid_step: float,
    edge_congestion: Optional[dict[Tuple[Point, Point], int]],
    start: Point,
    end: Point,
) -> float:
    if not edge_congestion:
        return 0.0
    penalty = 0.0
    for idx, edge in enumerate(_iter_segment_edges(a, b, grid_step)):
        base = edge_congestion.get(edge, 0)
        if base <= 0:
            continue
        scale = EDGE_CONGESTION_WEIGHT * min(base, CONGESTION_SATURATION)
        mid_x = (edge[0][0] + edge[1][0]) * 0.5
        mid_y = (edge[0][1] + edge[1][1]) * 0.5
        mid_point = (mid_x, mid_y)
        if (
            segment_length(mid_point, start) <= PIN_CONGESTION_RELAX_MM
            or segment_length(mid_point, end) <= PIN_CONGESTION_RELAX_MM
        ):
            scale *= 0.5
        penalty += scale
    return penalty


def _can_connect_directly(
    start: Point,
    end: Point,
    rects: Sequence[Rect],
    forbidden_points: Iterable[Point],
    *,
    collision_buffer: float,
) -> bool:
    """
    Determine whether a single straight segment between start and end is collision-free.
    """
    if start == end:
        return False

    sx, sy = start
    ex, ey = end

    # Only consider straight Manhattan segments (already ideal)
    if not (sx == ex or sy == ey):
        return False

    rects_to_check: Sequence[Rect]
    if collision_buffer > 0.0:
        rects_to_check = [
            (
                xmin - collision_buffer,
                ymin - collision_buffer,
                xmax + collision_buffer,
                ymax + collision_buffer,
            )
            for xmin, ymin, xmax, ymax in rects
        ]
    else:
        rects_to_check = rects

    if _segment_crosses_any_rect(start, end, rects_to_check):
        return False

    if _segment_hits_forbidden_points(start, end, forbidden_points, collision_buffer=collision_buffer):
        return False

    return True


def _prepare_rects_for_clearance(rects: Sequence[Rect], clearance: float) -> List[Rect]:
    """Expand each rect by the provided clearance amount."""
    buffered: List[Rect] = []
    for xmin, ymin, xmax, ymax in rects:
        buffered.append(
            (
                xmin - clearance,
                ymin - clearance,
                xmax + clearance,
                ymax + clearance,
            )
        )
    return buffered


def _remove_unnecessary_turns(
    points: Sequence[Point],
    rects: Sequence[Rect],
    forbidden_points: Iterable[Point],
) -> List[Point]:
    """
    Remove intermediate waypoints that fall on straight segments, provided doing so
    will not pass through another existing waypoint along the same segment.
    """
    if len(points) <= 2:
        return list(points)

    forbidden_set: Set[Point] = set(forbidden_points)
    rect_list = list(rects)
    collision_buffer = COLLISION_BUFFER_MM

    simplified: List[Point] = [points[0]]
    for idx in range(1, len(points) - 1):
        prev_pt = simplified[-1]
        curr_pt = points[idx]
        next_pt = points[idx + 1]

        if not _point_on_segment(curr_pt, prev_pt, next_pt):
            simplified.append(curr_pt)
            continue

        exclusion = {prev_pt, curr_pt, next_pt}
        if _segment_passes_through_existing_point(prev_pt, next_pt, points, excluded=exclusion):
            simplified.append(curr_pt)
            continue

        # Avoid removing a point if the combined segment would clip obstacles/forbidden points.
        if _segment_crosses_any_rect(prev_pt, next_pt, rect_list):
            simplified.append(curr_pt)
            continue
        if _segment_hits_forbidden_points(prev_pt, next_pt, forbidden_set, collision_buffer=collision_buffer):
            simplified.append(curr_pt)
            continue

        # curr_pt is redundant; skip it.

    simplified.append(points[-1])
    return simplified


def safe_manhattan_route(
    start: Point,
    end: Point,
    *,
    obstacles: RoutingObstacles,
    grid_step: float = KICAD_SCHEMATIC_GRID_MM,
    max_astar_expansions: int = SAFE_ROUTING_DEFAULT_EXPANSIONS,
    bend_penalty: float = BEND_PENALTY_WEIGHT,
    collision_buffer: float = COLLISION_BUFFER_MM,
    post_process_clearance: float = POST_PROCESS_RECT_CLEARANCE_MM,
) -> List[Point]:
    """
    Enhanced Manhattan router that grows symbol bodies by their pin lengths and
    post-processes the solution to clean up unnecessary bends.
    """
    s = (float(start[0]), float(start[1]))
    e = (float(end[0]), float(end[1]))

    if s == e:
        raise ValueError("Pins share the same coordinates; cannot connect")

    expanded_bboxes = _expand_symbol_bboxes(obstacles.symbol_bboxes)
    expanded_obstacles = RoutingObstacles(
        pin_vertices=obstacles.pin_vertices,
        node_vertices=obstacles.node_vertices,
        symbol_bboxes=expanded_bboxes,
    )
    edge_congestion = _build_edge_congestion_map(obstacles.node_vertices, grid_step)

    allowed_endpoints = {coord_key(*s), coord_key(*e)}
    forbidden_vertices, _ = expanded_obstacles.build_forbidden_sets(allowed_endpoints)
    rects = [bbox[0] for bbox in expanded_bboxes]
    forbidden_set = set(forbidden_vertices)

    direct_congestion = _segment_congestion_penalty(
        s,
        e,
        grid_step=grid_step,
        edge_congestion=edge_congestion,
        start=s,
        end=e,
    )
    if _can_connect_directly(s, e, rects, forbidden_set, collision_buffer=collision_buffer) and direct_congestion <= 0.0:
        route = [s, e]
    else:
        route = manhattan_route_core(
            s,
            e,
            obstacles=expanded_obstacles,
            grid_step=grid_step,
            max_astar_expansions=max_astar_expansions,
            bend_penalty=bend_penalty,
            collision_buffer=collision_buffer,
            edge_congestion=edge_congestion,
            rect_blocking=True,
        )

    buffered_rects = _prepare_rects_for_clearance(rects, post_process_clearance)
    optimized = _remove_unnecessary_turns(route, buffered_rects, forbidden_set)
    return optimized

