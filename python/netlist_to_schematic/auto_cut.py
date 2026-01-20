"""Layout-first analysis helpers for cutting problematic nets into labels.

This module analyzes an ELK laid-out graph (with routed edge sections) and
suggests nets to "labelize" (replace long/crossing pin-to-pin wiring with net
labels + short local wires).

The goal is to improve readability on dense schematics by:
- reducing cross-net wire crossings,
- cutting very long wires, and
- cutting wires that backtrack against the main layout direction.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable


_PHANTOM_PREFIXES = ("flow_", "chain_", "align_", "adjacent_", "halign_")


@dataclass
class NetLayoutStats:
    edge_count: int = 0
    total_length_mm: float = 0.0
    max_length_mm: float = 0.0
    total_bends: int = 0
    max_bends: int = 0
    total_backtrack_mm: float = 0.0
    max_backtrack_mm: float = 0.0
    crossings: int = 0


@dataclass
class EdgeLayoutStats:
    net_name: str
    source_port: str = ""
    target_port: str = ""
    length_mm: float = 0.0
    bends: int = 0
    backtrack_mm: float = 0.0
    crossings: int = 0


@dataclass(frozen=True)
class _AxisSegment:
    net_name: str
    edge_id: str
    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def is_horizontal(self) -> bool:
        return math.isclose(self.y1, self.y2, abs_tol=1e-9)

    @property
    def is_vertical(self) -> bool:
        return math.isclose(self.x1, self.x2, abs_tol=1e-9)

    @property
    def h_y(self) -> float:
        return self.y1

    @property
    def v_x(self) -> float:
        return self.x1

    @property
    def x_min(self) -> float:
        return min(self.x1, self.x2)

    @property
    def x_max(self) -> float:
        return max(self.x1, self.x2)

    @property
    def y_min(self) -> float:
        return min(self.y1, self.y2)

    @property
    def y_max(self) -> float:
        return max(self.y1, self.y2)

    def is_endpoint(self, x: float, y: float) -> bool:
        return (math.isclose(self.x1, x, abs_tol=1e-9) and math.isclose(self.y1, y, abs_tol=1e-9)) or (
            math.isclose(self.x2, x, abs_tol=1e-9) and math.isclose(self.y2, y, abs_tol=1e-9)
        )


def _is_phantom_edge(edge_id: str) -> bool:
    return bool(edge_id) and edge_id.startswith(_PHANTOM_PREFIXES)


def _iter_section_points(section: dict[str, Any]) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    start = section.get("startPoint")
    if isinstance(start, dict):
        x = start.get("x")
        y = start.get("y")
        if isinstance(x, (int, float)) and isinstance(y, (int, float)):
            points.append((float(x), float(y)))

    for bp in section.get("bendPoints", []) or []:
        if not isinstance(bp, dict):
            continue
        x = bp.get("x")
        y = bp.get("y")
        if isinstance(x, (int, float)) and isinstance(y, (int, float)):
            points.append((float(x), float(y)))

    end = section.get("endPoint")
    if isinstance(end, dict):
        x = end.get("x")
        y = end.get("y")
        if isinstance(x, (int, float)) and isinstance(y, (int, float)):
            points.append((float(x), float(y)))

    # Deduplicate consecutive identical points.
    deduped: list[tuple[float, float]] = []
    for pt in points:
        if not deduped or pt != deduped[-1]:
            deduped.append(pt)
    return deduped


def analyze_net_layout(elk_output: dict[str, Any]) -> dict[str, NetLayoutStats]:
    """Return per-net routing stats for a laid-out ELK graph."""

    stats: dict[str, NetLayoutStats] = {}
    horizontals: list[_AxisSegment] = []
    verticals: list[_AxisSegment] = []

    edges: Iterable[Any] = elk_output.get("edges", []) or []
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        edge_id = str(edge.get("id") or "")
        if _is_phantom_edge(edge_id):
            continue

        props = edge.get("properties", {}) or {}
        if not isinstance(props, dict):
            props = {}
        net_name = props.get("net_name")
        if not isinstance(net_name, str) or not net_name.strip():
            continue
        net_name = net_name.strip()

        edge_length = 0.0
        edge_bends = 0
        edge_backtrack = 0.0
        observed_any_section = False

        sections = edge.get("sections", []) or []
        for section in sections:
            if not isinstance(section, dict):
                continue
            pts = _iter_section_points(section)
            if len(pts) < 2:
                continue
            observed_any_section = True

            edge_bends += len(section.get("bendPoints", []) or [])

            start_x, _ = pts[0]
            end_x, _ = pts[-1]
            desired_sign = 0
            dx_total = end_x - start_x
            if dx_total > 1e-9:
                desired_sign = 1
            elif dx_total < -1e-9:
                desired_sign = -1

            for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
                dx = x2 - x1
                dy = y2 - y1
                seg_len = math.hypot(dx, dy)
                if seg_len <= 1e-12:
                    continue
                edge_length += seg_len

                # Backtracking only makes sense for horizontal moves.
                if desired_sign and abs(dy) <= 1e-9 and dx * desired_sign < 0:
                    edge_backtrack += abs(dx)

                # Track orthogonal segments for crossing detection.
                if abs(dy) <= 1e-9 and abs(dx) > 1e-9:
                    horizontals.append(_AxisSegment(net_name, edge_id, x1, y1, x2, y2))
                elif abs(dx) <= 1e-9 and abs(dy) > 1e-9:
                    verticals.append(_AxisSegment(net_name, edge_id, x1, y1, x2, y2))

        if not observed_any_section:
            continue

        net_stat = stats.setdefault(net_name, NetLayoutStats())
        net_stat.edge_count += 1
        net_stat.total_length_mm += edge_length
        net_stat.max_length_mm = max(net_stat.max_length_mm, edge_length)
        net_stat.total_bends += edge_bends
        net_stat.max_bends = max(net_stat.max_bends, edge_bends)
        net_stat.total_backtrack_mm += edge_backtrack
        net_stat.max_backtrack_mm = max(net_stat.max_backtrack_mm, edge_backtrack)

    # Count cross-net crossings: intersections between a vertical and horizontal segment.
    for v in verticals:
        vx = v.v_x
        for h in horizontals:
            if v.net_name == h.net_name:
                continue
            hy = h.h_y
            if vx <= h.x_min + 1e-9 or vx >= h.x_max - 1e-9:
                continue
            if hy <= v.y_min + 1e-9 or hy >= v.y_max - 1e-9:
                continue
            if v.is_endpoint(vx, hy) or h.is_endpoint(vx, hy):
                continue
            stats[v.net_name].crossings += 1
            stats[h.net_name].crossings += 1

    return stats


def analyze_edge_layout(elk_output: dict[str, Any]) -> dict[str, EdgeLayoutStats]:
    """Return per-edge routing stats for a laid-out ELK graph."""

    stats: dict[str, EdgeLayoutStats] = {}
    horizontals: list[_AxisSegment] = []
    verticals: list[_AxisSegment] = []

    edges: Iterable[Any] = elk_output.get("edges", []) or []
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        edge_id = str(edge.get("id") or "")
        if _is_phantom_edge(edge_id):
            continue

        props = edge.get("properties", {}) or {}
        if not isinstance(props, dict):
            props = {}
        net_name = props.get("net_name")
        if not isinstance(net_name, str) or not net_name.strip():
            continue
        net_name = net_name.strip()

        sources = edge.get("sources", []) or []
        targets = edge.get("targets", []) or []
        source_port = str(sources[0]) if sources else ""
        target_port = str(targets[0]) if targets else ""

        edge_length = 0.0
        edge_bends = 0
        edge_backtrack = 0.0
        observed_any_section = False

        sections = edge.get("sections", []) or []
        for section in sections:
            if not isinstance(section, dict):
                continue
            pts = _iter_section_points(section)
            if len(pts) < 2:
                continue
            observed_any_section = True

            edge_bends += len(section.get("bendPoints", []) or [])

            start_x, _ = pts[0]
            end_x, _ = pts[-1]
            desired_sign = 0
            dx_total = end_x - start_x
            if dx_total > 1e-9:
                desired_sign = 1
            elif dx_total < -1e-9:
                desired_sign = -1

            for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
                dx = x2 - x1
                dy = y2 - y1
                seg_len = math.hypot(dx, dy)
                if seg_len <= 1e-12:
                    continue
                edge_length += seg_len

                if desired_sign and abs(dy) <= 1e-9 and dx * desired_sign < 0:
                    edge_backtrack += abs(dx)

                if abs(dy) <= 1e-9 and abs(dx) > 1e-9:
                    horizontals.append(_AxisSegment(net_name, edge_id, x1, y1, x2, y2))
                elif abs(dx) <= 1e-9 and abs(dy) > 1e-9:
                    verticals.append(_AxisSegment(net_name, edge_id, x1, y1, x2, y2))

        if not observed_any_section:
            continue

        stats[edge_id] = EdgeLayoutStats(
            net_name=net_name,
            source_port=source_port,
            target_port=target_port,
            length_mm=edge_length,
            bends=edge_bends,
            backtrack_mm=edge_backtrack,
        )

    for v in verticals:
        vx = v.v_x
        for h in horizontals:
            if v.net_name == h.net_name:
                continue
            hy = h.h_y
            if vx <= h.x_min + 1e-9 or vx >= h.x_max - 1e-9:
                continue
            if hy <= v.y_min + 1e-9 or hy >= v.y_max - 1e-9:
                continue
            if v.is_endpoint(vx, hy) or h.is_endpoint(vx, hy):
                continue
            if v.edge_id in stats:
                stats[v.edge_id].crossings += 1
            if h.edge_id in stats:
                stats[h.edge_id].crossings += 1

    return stats


def net_fanout_by_name(circuit) -> dict[str, int]:
    """Compute fanout (#unique refs) per net name from a SKiDL-like circuit."""

    fanout: dict[str, int] = {}
    for net in getattr(circuit, "nets", []) or []:
        name = getattr(net, "name", None)
        if not isinstance(name, str) or not name.strip():
            continue
        refs: set[str] = set()
        for pin in getattr(net, "pins", []) or []:
            ref = getattr(pin, "ref", None)
            if isinstance(ref, str) and ref:
                refs.add(ref)
        fanout[name.strip()] = len(refs)
    return fanout


def select_problem_edges(
    stats: dict[str, EdgeLayoutStats],
    *,
    exclude_edges: set[str] | None = None,
    exclude_nets: set[str] | None = None,
    fanout: dict[str, int] | None = None,
    max_edges: int = 8,
    min_crossings: int = 1,
    min_length_mm: float | None = None,
    min_backtrack_mm: float | None = None,
) -> list[str]:
    """
    Choose edges (connections) to cut based on crossings/length/backtracking.

    If thresholds are None, derive them from the current layout distribution.
    """

    exclude_edges = exclude_edges or set()
    exclude_nets = exclude_nets or set()
    fanout = fanout or {}

    candidates: list[tuple[tuple, str]] = []
    lengths = [s.length_mm for eid, s in stats.items() if eid not in exclude_edges and s.net_name not in exclude_nets]
    backtracks = [
        s.backtrack_mm for eid, s in stats.items() if eid not in exclude_edges and s.net_name not in exclude_nets
    ]

    if min_length_mm is None and lengths:
        lengths.sort()
        min_length_mm = max(100.0, lengths[int(0.9 * (len(lengths) - 1))])
    if min_backtrack_mm is None and backtracks:
        backtracks.sort()
        min_backtrack_mm = max(30.0, backtracks[int(0.9 * (len(backtracks) - 1))])

    min_length_mm = float(min_length_mm or 0.0)
    min_backtrack_mm = float(min_backtrack_mm or 0.0)
    min_crossings = max(0, int(min_crossings))
    max_edges = max(0, int(max_edges))

    for edge_id, s in stats.items():
        if edge_id in exclude_edges:
            continue
        if s.net_name in exclude_nets:
            continue
        qualifies = (
            s.crossings >= min_crossings
            or (min_length_mm > 0 and s.length_mm >= min_length_mm)
            or (min_backtrack_mm > 0 and s.backtrack_mm >= min_backtrack_mm)
        )
        if not qualifies:
            continue

        key = (
            -int(s.crossings),
            -int(fanout.get(s.net_name, 0)),
            -float(s.length_mm),
            -float(s.backtrack_mm),
            s.net_name,
            edge_id,
        )
        candidates.append((key, edge_id))

    candidates.sort(key=lambda item: item[0])
    return [edge_id for _, edge_id in candidates[:max_edges]]


def select_problem_nets(
    stats: dict[str, NetLayoutStats],
    *,
    exclude: set[str] | None = None,
    fanout: dict[str, int] | None = None,
    max_nets: int = 8,
    min_crossings: int = 1,
    min_max_length_mm: float | None = None,
    min_max_backtrack_mm: float | None = None,
) -> list[str]:
    """
    Choose nets to labelize based on crossings/length/backtracking.

    The defaults are intentionally conservative. If no absolute thresholds are
    provided, this function derives dynamic thresholds from the current layout
    distribution and then selects the worst offenders.
    """

    exclude = exclude or set()
    fanout = fanout or {}

    candidates: list[tuple[tuple, str]] = []
    max_lengths = [s.max_length_mm for n, s in stats.items() if n not in exclude]
    max_backtracks = [s.max_backtrack_mm for n, s in stats.items() if n not in exclude]

    if min_max_length_mm is None and max_lengths:
        max_lengths.sort()
        min_max_length_mm = max(100.0, max_lengths[int(0.9 * (len(max_lengths) - 1))])
    if min_max_backtrack_mm is None and max_backtracks:
        max_backtracks.sort()
        min_max_backtrack_mm = max(30.0, max_backtracks[int(0.9 * (len(max_backtracks) - 1))])

    min_max_length_mm = float(min_max_length_mm or 0.0)
    min_max_backtrack_mm = float(min_max_backtrack_mm or 0.0)
    min_crossings = max(0, int(min_crossings))
    max_nets = max(0, int(max_nets))

    for net_name, s in stats.items():
        if net_name in exclude:
            continue
        qualifies = (
            s.crossings >= min_crossings
            or (min_max_length_mm > 0 and s.max_length_mm >= min_max_length_mm)
            or (min_max_backtrack_mm > 0 and s.max_backtrack_mm >= min_max_backtrack_mm)
        )
        if not qualifies:
            continue

        # Rank: crossings first, then fanout, then long/backtracking wiring.
        key = (
            -int(s.crossings),
            -int(fanout.get(net_name, 0)),
            -float(s.max_length_mm),
            -float(s.max_backtrack_mm),
            -float(s.total_length_mm),
            net_name,
        )
        candidates.append((key, net_name))

    candidates.sort(key=lambda item: item[0])
    return [name for _, name in candidates[:max_nets]]
