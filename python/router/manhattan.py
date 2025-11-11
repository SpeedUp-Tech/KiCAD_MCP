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

__all__ = [
    "BBox",
    "COLLISION_BUFFER_MM",
    "COORD_KEY_PRECISION",
    "RoutingObstacles",
    "SAFE_ROUTING_DEFAULT_EXPANSIONS",
    "coord_key",
    "point_segment_distance",
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


def _manhattan_astar_route(
    start: Point,
    end: Point,
    step: float,
    rects: Sequence[Rect],
    forbidden_points: Iterable[Point],
    *,
    max_expansions: int,
    bend_penalty: float,
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
    margin_distance = margin_steps * step

    xmin = min(sx, ex) - margin_distance
    xmax = max(sx, ex) + margin_distance
    ymin = min(sy, ey) - margin_distance
    ymax = max(sy, ey) + margin_distance

    def blocked(px: float, py: float) -> bool:
        if px < xmin or px > xmax or py < ymin or py > ymax:
            return True
        return coord_key(px, py) in forb_set

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


def safe_manhattan_route(
    start: Point,
    end: Point,
    *,
    obstacles: RoutingObstacles,
    grid_step: float = KICAD_SCHEMATIC_GRID_MM,
    max_astar_expansions: int = SAFE_ROUTING_DEFAULT_EXPANSIONS,
    bend_penalty: float = 1.0,
    collision_buffer: float = COLLISION_BUFFER_MM,
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
    astar_route = _manhattan_astar_route(
        s,
        e,
        grid_step,
        rects,
        forbidden_vertices,
        max_expansions=max_astar_expansions,
        bend_penalty=bend_penalty,
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
