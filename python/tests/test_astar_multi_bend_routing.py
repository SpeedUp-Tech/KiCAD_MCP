import shutil
import subprocess
import unittest
import uuid
from sexpdata import Symbol
from pathlib import Path

from python.commands.kicad_schematics.schematic import SchematicManager
from python.commands.kicad_schematics.component_schematic import ComponentManager
from python.commands.kicad_schematics.connection_schematic import ConnectionManager
from python.commands.kicad_schematics.grid_utils import KICAD_SCHEMATIC_GRID_MM, snap_to_grid


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


def compute_symbol_bbox_from_pins(schematic, reference: str):
    syms = [sym for sym in getattr(schematic, 'symbol', []) if getattr(getattr(sym, 'property', None), 'Reference', None) is not None and getattr(sym.property.Reference, 'value', None) == reference]
    assert syms, f"Symbol {reference} not found"
    sym = syms[0]
    pts = []
    if hasattr(sym, 'pin') and sym.pin is not None:
        for pin in sym.pin:
            if hasattr(pin, 'location') and pin.location is not None:
                pts.append((float(pin.location.x), float(pin.location.y)))
    assert pts, f"No pins found for {reference}"
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    margin = KICAD_SCHEMATIC_GRID_MM
    return (
        min(xs) - margin,
        min(ys) - margin,
        max(xs) + margin,
        max(ys) + margin,
    )


def seg_intersects_rect(a, b, rect):
    x1, y1 = a
    x2, y2 = b
    xmin, ymin, xmax, ymax = rect
    if y1 == y2:
        y = y1
        if y <= ymin or y >= ymax:
            return False
        lo, hi = (x1, x2) if x1 <= x2 else (x2, x1)
        return not (hi <= xmin or lo >= xmax)
    if x1 == x2:
        x = x1
        if x <= xmin or x >= xmax:
            return False
        lo, hi = (y1, y2) if y1 <= y2 else (y2, y1)
        return not (hi <= ymin or lo >= ymax)
    return False


# Export helpers ------------------------------------------------------------
EXPORT_ROOT = Path(__file__).resolve().parents[2] / 'exported'
EXPORT_ROOT.mkdir(parents=True, exist_ok=True)


def _export_svg(schematic, basename: str) -> None:
    """
    Persist the schematic and export an SVG snapshot for visual debugging.

    Creates files under exported/astar_multi_bend/<run_id>/.
    If kicad-cli is unavailable, this quietly returns after saving the schematic.
    """
    run_dir = EXPORT_ROOT / 'astar_multi_bend'
    run_dir.mkdir(parents=True, exist_ok=True)

    # Use a short random suffix so concurrent test runs don't collide.
    suffix = uuid.uuid4().hex[:6]
    sch_path = run_dir / f"{basename}_{suffix}.kicad_sch"
    svg_path = sch_path.with_suffix('.svg')

    saved = SchematicManager.save_schematic(schematic, str(sch_path))
    if not saved:
        return

    exe = shutil.which('kicad-cli')
    if not exe:
        return

    proc = subprocess.run(
        [exe, 'sch', 'export', 'svg', str(sch_path), '--output', str(svg_path)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0 or not svg_path.exists():
        return


class ManhattanAStarRoutingTests(unittest.TestCase):
    def test_multi_bend_detour_around_two_bodies(self):
        sch = SchematicManager.create_schematic('AStarMultiBend')
        # Endpoints
        add_hlabel(sch, 'A', 10.0, 10.0)
        add_hlabel(sch, 'B', 90.0, 10.0)

        # Two components forming a vertical barrier that blocks simple HV/VH and single-band detours
        ComponentManager.add_component(sch, { 'type': 'R', 'reference': 'R1', 'x': 45.0, 'y': 7.5 })
        ComponentManager.add_component(sch, { 'type': 'R', 'reference': 'R2', 'x': 45.0, 'y': 12.5 })
        bbox1 = compute_symbol_bbox_from_pins(sch, 'R1')
        bbox2 = compute_symbol_bbox_from_pins(sch, 'R2')

        before = len(getattr(sch, 'wire', []))
        ConnectionManager.connect_pins(
            sch,
            {'label': 'A'},
            {'label': 'B'},
        )
        new_wires = sch.wire[before:]
        flat = []
        for seg in new_wires:
            for p in seg.points:
                flat.append((float(p.value[0]), float(p.value[1])))
        uniq = []
        for pt in flat:
            if not uniq or uniq[-1] != pt:
                uniq.append(pt)

        _export_svg(sch, 'multi_bend_route')

        # Check: (1) at least 4 points (3+ segments -> multi-bend), (2) no segment crosses either body
        self.assertGreaterEqual(len(uniq), 4, f"Expected multi-bend path, got {uniq}")
        for a, b in zip(uniq, uniq[1:]):
            self.assertFalse(seg_intersects_rect(a, b, bbox1), f"Segment {a}->{b} crosses R1 body {bbox1}")
            self.assertFalse(seg_intersects_rect(a, b, bbox2), f"Segment {a}->{b} crosses R2 body {bbox2}")

    def test_rotated_symbol_body_is_obstacle(self):
        sch = SchematicManager.create_schematic('AStarRotated')
        add_hlabel(sch, 'A', 10.0, 10.0)
        add_hlabel(sch, 'B', 80.0, 10.0)

        # Place a capacitor rotated 90 degrees near the center
        ComponentManager.add_component(sch, { 'type': 'C', 'reference': 'C1', 'x': 45.0, 'y': 10.0, 'rotation': 90 })
        bbox = compute_symbol_bbox_from_pins(sch, 'C1')

        before = len(getattr(sch, 'wire', []))
        ConnectionManager.connect_pins(
            sch,
            {'label': 'A'},
            {'label': 'B'},
        )
        flat = []
        for seg in sch.wire[before:]:
            for p in seg.points:
                flat.append((float(p.value[0]), float(p.value[1])))
        uniq = []
        for pt in flat:
            if not uniq or uniq[-1] != pt:
                uniq.append(pt)
        for a, b in zip(uniq, uniq[1:]):
            self.assertFalse(seg_intersects_rect(a, b, bbox), f"Segment {a}->{b} crosses C1 body {bbox}")


if __name__ == '__main__':
    unittest.main()
