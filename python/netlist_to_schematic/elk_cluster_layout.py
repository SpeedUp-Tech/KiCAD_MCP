"""
elk_cluster_layout - Helpers for clustered / hierarchical ELK layout

This module implements a two-stage layout strategy:
1) Split the ELK graph into connected components (clusters) using REAL (port-to-port)
   edges only, then run ELK layout for each cluster to keep it compact.
2) Place the clusters relative to each other using compact rectangle packing
   (each cluster becomes one box). The resulting cluster positions are applied
   as offsets to the per-cluster layouts.

The output is a single ELK graph with absolute coordinates and routed edge sections,
ready for conversion to KiCad.
"""

from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path
from typing import Any, Iterable


def _elk_runner_path() -> Path:
    return Path(__file__).parent / "elk" / "elk_layout_runner.cjs"


def _run_elk_layout_to_file(graph: dict[str, Any], input_path: Path, output_path: Path) -> dict[str, Any]:
    input_path.parent.mkdir(parents=True, exist_ok=True)
    input_path.write_text(json.dumps(graph, indent=2))

    result = subprocess.run(
        ["node", str(_elk_runner_path()), str(input_path), str(output_path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        stdout = (result.stdout or "").strip()
        details = stderr or stdout or "unknown error"
        raise RuntimeError(f"ELK layout failed: {details}")

    return json.loads(output_path.read_text())


def _endpoint_node_id(endpoint: str) -> str:
    return endpoint.split(".", 1)[0]


def _edge_uses_ports(edge: dict[str, Any]) -> bool:
    """Return True for real (wire) edges that connect ports (contain a '.')"""
    endpoints: Iterable[Any] = list(edge.get("sources", []) or []) + list(edge.get("targets", []) or [])
    for ep in endpoints:
        if isinstance(ep, str) and "." in ep:
            return True
    return False


def _edge_nodes(edge: dict[str, Any]) -> set[str]:
    nodes: set[str] = set()
    for ep in list(edge.get("sources", []) or []) + list(edge.get("targets", []) or []):
        if not isinstance(ep, str):
            continue
        nodes.add(_endpoint_node_id(ep))
    return nodes


def _connected_components(node_ids: list[str], edges: list[dict[str, Any]]) -> list[set[str]]:
    """Connected components on node-level adjacency built from REAL (port) edges only."""
    ids = set(node_ids)
    adj: dict[str, set[str]] = {nid: set() for nid in ids}

    for edge in edges:
        if not _edge_uses_ports(edge):
            continue
        sources = [ep for ep in (edge.get("sources", []) or []) if isinstance(ep, str)]
        targets = [ep for ep in (edge.get("targets", []) or []) if isinstance(ep, str)]
        for s in sources:
            for t in targets:
                ns = _endpoint_node_id(s)
                nt = _endpoint_node_id(t)
                if ns not in ids or nt not in ids or ns == nt:
                    continue
                adj[ns].add(nt)
                adj[nt].add(ns)

    comps: list[set[str]] = []
    seen: set[str] = set()
    for nid in node_ids:
        if nid not in ids or nid in seen:
            continue
        stack = [nid]
        seen.add(nid)
        comp: set[str] = {nid}
        while stack:
            cur = stack.pop()
            for nb in adj.get(cur, set()):
                if nb in seen:
                    continue
                seen.add(nb)
                comp.add(nb)
                stack.append(nb)
        comps.append(comp)

    # Deterministic: biggest first, then by smallest id in the set.
    def _sort_key(c: set[str]) -> tuple[int, str]:
        return (-len(c), min(c) if c else "")

    comps.sort(key=_sort_key)
    return comps


def _iter_edge_points(edge: dict[str, Any]):
    for section in edge.get("sections", []) or []:
        for pt in [section.get("startPoint"), section.get("endPoint")] + list(section.get("bendPoints", []) or []):
            if not isinstance(pt, dict):
                continue
            x = pt.get("x")
            y = pt.get("y")
            if isinstance(x, (int, float)) and isinstance(y, (int, float)):
                yield pt


def _normalize_graph_coords(graph: dict[str, Any]) -> dict[str, Any]:
    """Shift graph so its bounding box starts at (0,0); recompute width/height."""
    min_x = math.inf
    min_y = math.inf
    max_x = -math.inf
    max_y = -math.inf

    for node in graph.get("children", []) or []:
        x = node.get("x")
        y = node.get("y")
        w = node.get("width")
        h = node.get("height")
        if not all(isinstance(v, (int, float)) for v in (x, y, w, h)):
            continue
        min_x = min(min_x, float(x))
        min_y = min(min_y, float(y))
        max_x = max(max_x, float(x) + float(w))
        max_y = max(max_y, float(y) + float(h))

    for edge in graph.get("edges", []) or []:
        for pt in _iter_edge_points(edge):
            min_x = min(min_x, float(pt["x"]))
            min_y = min(min_y, float(pt["y"]))
            max_x = max(max_x, float(pt["x"]))
            max_y = max(max_y, float(pt["y"]))

    if min_x is math.inf or min_y is math.inf:
        graph["x"] = 0
        graph["y"] = 0
        graph["width"] = 0
        graph["height"] = 0
        return graph

    dx = -min_x
    dy = -min_y

    for node in graph.get("children", []) or []:
        if isinstance(node.get("x"), (int, float)):
            node["x"] = float(node["x"]) + dx
        if isinstance(node.get("y"), (int, float)):
            node["y"] = float(node["y"]) + dy

    for edge in graph.get("edges", []) or []:
        for pt in _iter_edge_points(edge):
            pt["x"] = float(pt["x"]) + dx
            pt["y"] = float(pt["y"]) + dy

    graph["x"] = 0
    graph["y"] = 0
    graph["width"] = max_x - min_x
    graph["height"] = max_y - min_y
    return graph


def _offset_edge_sections(edge: dict[str, Any], dx: float, dy: float) -> None:
    for pt in _iter_edge_points(edge):
        pt["x"] = float(pt["x"]) + dx
        pt["y"] = float(pt["y"]) + dy


_A4_LANDSCAPE_WIDTH_MM = 297.0
_A4_LANDSCAPE_HEIGHT_MM = 210.0
_A4_LANDSCAPE_ASPECT = _A4_LANDSCAPE_WIDTH_MM / _A4_LANDSCAPE_HEIGHT_MM


def _pack_clusters_grid(
    rects: list[tuple[str, float, float]],
    *,
    columns: int,
    gap_mm: float,
) -> tuple[dict[str, tuple[float, float]], float, float]:
    """
    Place rectangles into a columns-based grid (row-major).

    Returns:
        (positions, total_width, total_height)
    """
    if not rects:
        return {}, 0.0, 0.0
    columns = max(1, min(columns, len(rects)))
    rows = int(math.ceil(len(rects) / columns))

    col_widths = [0.0] * columns
    row_heights = [0.0] * rows

    for idx, (_, w, h) in enumerate(rects):
        r = idx // columns
        c = idx % columns
        col_widths[c] = max(col_widths[c], float(w))
        row_heights[r] = max(row_heights[r], float(h))

    x_offsets = [0.0] * columns
    y_offsets = [0.0] * rows

    x = 0.0
    for c in range(columns):
        x_offsets[c] = x
        x += col_widths[c]
        if c != columns - 1:
            x += gap_mm

    y = 0.0
    for r in range(rows):
        y_offsets[r] = y
        y += row_heights[r]
        if r != rows - 1:
            y += gap_mm

    positions: dict[str, tuple[float, float]] = {}
    for idx, (cid, _, _) in enumerate(rects):
        r = idx // columns
        c = idx % columns
        positions[cid] = (x_offsets[c], y_offsets[r])

    total_width = sum(col_widths) + gap_mm * (columns - 1)
    total_height = sum(row_heights) + gap_mm * (rows - 1)
    return positions, total_width, total_height


def run_clustered_layout(
    elk_graph: dict[str, Any],
    work_dir: Path,
    *,
    base_name: str = "elk",
    keep_intermediate: bool = False,
    cluster_spacing_mm: float = 5.08,
) -> dict[str, Any]:
    """
    Run clustered layout and return a single, laid out ELK graph.

    - If the graph has only one real connected component, falls back to a single ELK run.
    - Otherwise, layouts each component independently, then places the components using
      compact packing to constrain the overall canvas size.
    """
    children: list[dict[str, Any]] = list(elk_graph.get("children", []) or [])
    edges: list[dict[str, Any]] = list(elk_graph.get("edges", []) or [])
    node_ids = [n.get("id") for n in children if isinstance(n, dict) and isinstance(n.get("id"), str)]

    comps = _connected_components([nid for nid in node_ids if nid is not None], edges)
    if len(comps) <= 1:
        input_path = work_dir / f"{base_name}_elk_input.json"
        output_path = work_dir / f"{base_name}_elk_output.json"
        if not keep_intermediate:
            input_path = work_dir / f"_{base_name}_elk_input.json"
            output_path = work_dir / f"_{base_name}_elk_output.json"
        try:
            laid_out = _run_elk_layout_to_file(elk_graph, input_path, output_path)
        finally:
            if not keep_intermediate:
                input_path.unlink(missing_ok=True)
                output_path.unlink(missing_ok=True)
        return laid_out

    # Stage A: Layout each cluster independently.
    cluster_layouts: list[dict[str, Any]] = []
    cluster_ids: list[str] = []

    for idx, comp in enumerate(comps):
        cluster_id = f"cluster_{idx}"
        cluster_ids.append(cluster_id)

        cluster_children = [n for n in children if isinstance(n, dict) and n.get("id") in comp]
        cluster_edges = [e for e in edges if _edge_nodes(e).issubset(comp)]

        cluster_graph = {
            "id": cluster_id,
            "layoutOptions": dict(elk_graph.get("layoutOptions", {}) or {}),
            "properties": dict(elk_graph.get("properties", {}) or {}),
            "children": cluster_children,
            "edges": cluster_edges,
        }

        if keep_intermediate:
            input_path = work_dir / f"{base_name}_{cluster_id}_elk_input.json"
            output_path = work_dir / f"{base_name}_{cluster_id}_elk_output.json"
        else:
            input_path = work_dir / f"_{base_name}_{cluster_id}_elk_input.json"
            output_path = work_dir / f"_{base_name}_{cluster_id}_elk_output.json"

        try:
            laid_out = _run_elk_layout_to_file(cluster_graph, input_path, output_path)
        finally:
            if not keep_intermediate:
                input_path.unlink(missing_ok=True)
                output_path.unlink(missing_ok=True)

        laid_out = _normalize_graph_coords(laid_out)
        cluster_layouts.append(laid_out)

    # Stage B: Place clusters relative using compact grid packing.
    rects: list[tuple[str, float, float]] = []
    for cid, cl in zip(cluster_ids, cluster_layouts, strict=True):
        rects.append((cid, float(cl.get("width", 0) or 0), float(cl.get("height", 0) or 0)))

    gap_mm = float(cluster_spacing_mm)
    best_positions: dict[str, tuple[float, float]] = {}
    best_score = math.inf

    for cols in range(1, len(rects) + 1):
        positions, total_w, total_h = _pack_clusters_grid(rects, columns=cols, gap_mm=gap_mm)
        overflow = max(0.0, total_w - _A4_LANDSCAPE_WIDTH_MM) + max(0.0, total_h - _A4_LANDSCAPE_HEIGHT_MM)
        area = total_w * total_h
        ratio = (total_w / total_h) if total_h > 1e-9 else math.inf
        aspect_penalty = abs(math.log(ratio / _A4_LANDSCAPE_ASPECT)) if ratio > 0 and math.isfinite(ratio) else 0.0

        score = overflow * 1_000_000.0 + area + aspect_penalty * 1_000.0
        if score < best_score:
            best_score = score
            best_positions = positions

    cluster_positions = best_positions

    # Stage C: Apply cluster offsets to nodes and edge routes and merge.
    merged_children: list[dict[str, Any]] = []
    merged_edges: list[dict[str, Any]] = []

    for cid, cl in zip(cluster_ids, cluster_layouts, strict=True):
        ox, oy = cluster_positions.get(cid, (0.0, 0.0))

        for node in cl.get("children", []) or []:
            if isinstance(node.get("x"), (int, float)):
                node["x"] = float(node["x"]) + ox
            if isinstance(node.get("y"), (int, float)):
                node["y"] = float(node["y"]) + oy
            merged_children.append(node)

        for edge in cl.get("edges", []) or []:
            _offset_edge_sections(edge, ox, oy)
            merged_edges.append(edge)

    merged_graph: dict[str, Any] = {
        "id": elk_graph.get("id", "root"),
        "layoutOptions": dict(elk_graph.get("layoutOptions", {}) or {}),
        "properties": dict(elk_graph.get("properties", {}) or {}),
        "children": merged_children,
        "edges": merged_edges,
    }
    return _normalize_graph_coords(merged_graph)
