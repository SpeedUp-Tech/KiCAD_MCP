import unittest
from collections import defaultdict

from python.commands.kicad_schematics.grid_utils import KICAD_SCHEMATIC_GRID_MM
from python.netlist_to_schematic.elk_to_kicad import (
    _grid_key,
    _is_points_path_safe,
    _segment_point_keys,
    _segment_unit_edges,
)


def _mm(x: int, y: int) -> list[float]:
    grid = KICAD_SCHEMATIC_GRID_MM
    return [x * grid, y * grid]


def _add_existing_wire(
    *,
    net: str,
    keys: list[tuple[int, int]],
    wire_points: dict[tuple[int, int], set[str]],
    wire_vertices: dict[tuple[int, int], set[str]],
    wire_edges: dict[tuple[tuple[int, int], tuple[int, int]], set[str]],
) -> None:
    for key in keys:
        wire_vertices[key].add(net)
    for a, b in zip(keys, keys[1:]):
        for key in _segment_point_keys(a, b):
            wire_points[key].add(net)
        for edge in _segment_unit_edges(a, b):
            wire_edges[edge].add(net)


class ElkToKicadUnsafeDetectionTests(unittest.TestCase):
    def test_crossing_mid_segment_different_nets_is_safe(self) -> None:
        occupied: set[tuple[int, int]] = set()
        wire_points: dict[tuple[int, int], set[str]] = defaultdict(set)
        wire_vertices: dict[tuple[int, int], set[str]] = defaultdict(set)
        wire_edges: dict[tuple[tuple[int, int], tuple[int, int]], set[str]] = defaultdict(set)

        _add_existing_wire(
            net="A",
            keys=[(0, 0), (4, 0)],
            wire_points=wire_points,
            wire_vertices=wire_vertices,
            wire_edges=wire_edges,
        )

        points = [_mm(2, -2), _mm(2, 2)]
        start_key = _grid_key(*points[0])
        end_key = _grid_key(*points[-1])
        self.assertTrue(
            _is_points_path_safe(
                points,
                net_name="B",
                occupied=occupied,
                wire_points=wire_points,
                wire_vertices=wire_vertices,
                wire_edges=wire_edges,
                start_key=start_key,
                end_key=end_key,
            )
        )

    def test_crossing_existing_vertex_different_nets_is_unsafe(self) -> None:
        occupied: set[tuple[int, int]] = set()
        wire_points: dict[tuple[int, int], set[str]] = defaultdict(set)
        wire_vertices: dict[tuple[int, int], set[str]] = defaultdict(set)
        wire_edges: dict[tuple[tuple[int, int], tuple[int, int]], set[str]] = defaultdict(set)

        _add_existing_wire(
            net="A",
            keys=[(0, 0), (2, 0), (2, 2)],
            wire_points=wire_points,
            wire_vertices=wire_vertices,
            wire_edges=wire_edges,
        )

        points = [_mm(2, -2), _mm(2, 4)]
        start_key = _grid_key(*points[0])
        end_key = _grid_key(*points[-1])
        self.assertFalse(
            _is_points_path_safe(
                points,
                net_name="B",
                occupied=occupied,
                wire_points=wire_points,
                wire_vertices=wire_vertices,
                wire_edges=wire_edges,
                start_key=start_key,
                end_key=end_key,
            )
        )

    def test_turnpoint_lands_on_other_wire_interior_is_unsafe(self) -> None:
        occupied: set[tuple[int, int]] = set()
        wire_points: dict[tuple[int, int], set[str]] = defaultdict(set)
        wire_vertices: dict[tuple[int, int], set[str]] = defaultdict(set)
        wire_edges: dict[tuple[tuple[int, int], tuple[int, int]], set[str]] = defaultdict(set)

        _add_existing_wire(
            net="A",
            keys=[(0, 0), (4, 0)],
            wire_points=wire_points,
            wire_vertices=wire_vertices,
            wire_edges=wire_edges,
        )

        points = [_mm(2, -2), _mm(2, 0), _mm(3, 0)]
        start_key = _grid_key(*points[0])
        end_key = _grid_key(*points[-1])
        self.assertFalse(
            _is_points_path_safe(
                points,
                net_name="B",
                occupied=occupied,
                wire_points=wire_points,
                wire_vertices=wire_vertices,
                wire_edges=wire_edges,
                start_key=start_key,
                end_key=end_key,
            )
        )

    def test_overlap_different_nets_is_unsafe(self) -> None:
        occupied: set[tuple[int, int]] = set()
        wire_points: dict[tuple[int, int], set[str]] = defaultdict(set)
        wire_vertices: dict[tuple[int, int], set[str]] = defaultdict(set)
        wire_edges: dict[tuple[tuple[int, int], tuple[int, int]], set[str]] = defaultdict(set)

        _add_existing_wire(
            net="A",
            keys=[(0, 0), (4, 0)],
            wire_points=wire_points,
            wire_vertices=wire_vertices,
            wire_edges=wire_edges,
        )

        points = [_mm(2, 0), _mm(6, 0)]
        start_key = _grid_key(*points[0])
        end_key = _grid_key(*points[-1])
        self.assertFalse(
            _is_points_path_safe(
                points,
                net_name="B",
                occupied=occupied,
                wire_points=wire_points,
                wire_vertices=wire_vertices,
                wire_edges=wire_edges,
                start_key=start_key,
                end_key=end_key,
            )
        )

    def test_overlap_same_net_is_safe(self) -> None:
        occupied: set[tuple[int, int]] = set()
        wire_points: dict[tuple[int, int], set[str]] = defaultdict(set)
        wire_vertices: dict[tuple[int, int], set[str]] = defaultdict(set)
        wire_edges: dict[tuple[tuple[int, int], tuple[int, int]], set[str]] = defaultdict(set)

        _add_existing_wire(
            net="A",
            keys=[(0, 0), (4, 0)],
            wire_points=wire_points,
            wire_vertices=wire_vertices,
            wire_edges=wire_edges,
        )

        points = [_mm(2, 0), _mm(6, 0)]
        start_key = _grid_key(*points[0])
        end_key = _grid_key(*points[-1])
        self.assertTrue(
            _is_points_path_safe(
                points,
                net_name="A",
                occupied=occupied,
                wire_points=wire_points,
                wire_vertices=wire_vertices,
                wire_edges=wire_edges,
                start_key=start_key,
                end_key=end_key,
            )
        )


if __name__ == "__main__":
    unittest.main()

