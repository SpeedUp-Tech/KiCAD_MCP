import unittest
from sexpdata import Symbol

from python.commands.schematic import SchematicManager
from python.commands.connection_schematic import ConnectionManager
from python.commands.grid_utils import KICAD_SCHEMATIC_GRID_MM, snap_to_grid


def add_hlabel(schematic, name: str, x: float, y: float):
    """Add a hierarchical label to the schematic with grid-snapped coordinates."""
    # Snap coordinates to grid for KiCAD compliance
    x_snapped = snap_to_grid(x)
    y_snapped = snap_to_grid(y)

    node = [
        Symbol('hierarchical_label'),
        name,
        [Symbol('shape'), Symbol('input')],
        [Symbol('at'), x_snapped, y_snapped, 0],
        [Symbol('fields_autoplaced')],
        [Symbol('effects'), [Symbol('font'), [Symbol('size'), 1.27, 1.27]]],
        [Symbol('uuid'), Symbol(f'label-{name}')]
    ]
    schematic.tree.append(node)


def add_node_at(schematic, x: float, y: float):
    # Create a tiny vertical wire so that (x, y) becomes a vertex/node in the netlist graph
    ConnectionManager.add_wire(
        schematic,
        [x, y],
        [x, y + KICAD_SCHEMATIC_GRID_MM],
        {},
    )


class SafeRoutingCollisionAvoidanceTests(unittest.TestCase):
    def test_avoids_corner_that_is_already_a_node(self):
        sch = SchematicManager.create_schematic('AvoidCornerNode')
        # Place two labels we control precisely
        sx = snap_to_grid(10.0)
        sy = snap_to_grid(10.0)
        ex = snap_to_grid(50.0)
        ey = snap_to_grid(40.0)
        add_hlabel(sch, 'A', sx, sy)
        add_hlabel(sch, 'B', ex, ey)

        # For hv pattern, the naive corner would be (ex, sy) = (50, 10)
        blocked_corner = (ex, sy)
        add_node_at(sch, *blocked_corner)

        before_wires = len(getattr(sch, 'wire', []))
        result = ConnectionManager.connect_pins(
            sch,
            {'label': 'A'},
            {'label': 'B'},
        )
        self.assertIsInstance(result, dict)

        # Find the new wire segments added by this call (last N wires)
        after_wires = len(sch.wire)
        self.assertGreater(after_wires, before_wires)
        new_segments = sch.wire[before_wires:]

        # Collect all midpoints (internal vertices) used by the new connection
        internal_vertices = []
        for w in new_segments:
            pts = [tuple(p.value) for p in w.points]
            # Collect interior points across the polyline
            # In segments representation, consecutive segments share endpoints; collect shared midpoints
            # Accumulate all except the very first and very last
            # We'll flatten, then remove first and last
            if not internal_vertices:
                flat = []
                for seg in new_segments:
                    for p in seg.points:
                        flat.append(tuple(p.value))
                if flat:
                    flat = [(round(x, 2), round(y, 2)) for (x, y) in flat]
                    # dedupe consecutive duplicates
                    uniq = []
                    for pt in flat:
                        if not uniq or uniq[-1] != pt:
                            uniq.append(pt)
                    if len(uniq) > 2:
                        internal_vertices = uniq[1:-1]
        # None of the internal vertices may equal the blocked corner
        blocked_corner_r = (round(blocked_corner[0], 2), round(blocked_corner[1], 2))
        self.assertNotIn(blocked_corner_r, internal_vertices)

    def test_raises_when_both_corners_and_escapes_blocked(self):
        sch = SchematicManager.create_schematic('BlockedEverywhere')
        sx = snap_to_grid(10.0)
        sy = snap_to_grid(10.0)
        ex = snap_to_grid(50.0)
        ey = snap_to_grid(40.0)
        add_hlabel(sch, 'A', sx, sy)
        add_hlabel(sch, 'B', ex, ey)
        grid = KICAD_SCHEMATIC_GRID_MM

        # Block both naive corners
        add_node_at(sch, ex, sy)  # hv corner
        add_node_at(sch, sx, ey)  # vh corner

        # Block escape points around start and end and the derived midpoints
        escapes = [
            (sx, sy + grid), (sx, sy - grid), (sx + grid, sy), (sx - grid, sy),
            (ex, ey + grid), (ex, ey - grid), (ex + grid, ey), (ex - grid, ey),
        ]
        for (ux, uy) in escapes:
            add_node_at(sch, ux, uy)
        # For each escape, also block the intermediate L corner it would generate
        derived = [
            (ex, sy + grid), (ex, sy - grid), (sx + grid, ey), (sx - grid, ey),
            (sx, ey + grid), (sx, ey - grid), (ex + grid, sy), (ex - grid, sy),
        ]
        for (ux, uy) in derived:
            add_node_at(sch, ux, uy)

        before_wires = len(getattr(sch, 'wire', []))
        with self.assertRaises(ValueError) as ctx:
            ConnectionManager.connect_pins(
                sch,
                {'label': 'A'},
                {'label': 'B'},
            )
        msg = str(ctx.exception).lower()
        self.assertIn('routing failed', msg)
        after_wires = len(getattr(sch, 'wire', []))
        # Ensure no wires were added on failure
        self.assertEqual(after_wires, before_wires)


if __name__ == '__main__':
    unittest.main()
