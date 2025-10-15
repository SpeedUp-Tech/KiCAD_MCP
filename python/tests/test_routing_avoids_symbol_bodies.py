import unittest
from sexpdata import Symbol

from python.commands.schematic import SchematicManager
from python.commands.component_schematic import ComponentManager
from python.commands.connection_schematic import ConnectionManager
from python.commands.grid_utils import KICAD_SCHEMATIC_GRID_MM


def add_hlabel(schematic, name: str, x: float, y: float):
    node = [
        Symbol('hierarchical_label'),
        name,
        [Symbol('shape'), Symbol('input')],
        [Symbol('at'), float(x), float(y), 0],
        [Symbol('fields_autoplaced')],
        [Symbol('effects'), [Symbol('font'), [Symbol('size'), 1.27, 1.27]]],
        [Symbol('uuid'), Symbol(f'label-{name}')]
    ]
    schematic.tree.append(node)


def compute_symbol_bbox_from_pins(schematic, reference: str):
    # Find symbol by reference, collect pin positions, inflate by grid
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
        if y < ymin or y > ymax:
            return False
        lo, hi = (x1, x2) if x1 <= x2 else (x2, x1)
        return not (hi < xmin or lo > xmax)
    if x1 == x2:
        x = x1
        if x < xmin or x > xmax:
            return False
        lo, hi = (y1, y2) if y1 <= y2 else (y2, y1)
        return not (hi < ymin or lo > ymax)
    return False


class RoutingAvoidsSymbolBodiesTests(unittest.TestCase):
    def test_route_does_not_cross_middle_component_body(self):
        sch = SchematicManager.create_schematic('AvoidSymbolBody')
        # Endpoints as labels to focus purely on routing field
        add_hlabel(sch, 'A', 10.0, 10.0)
        add_hlabel(sch, 'B', 80.0, 10.0)

        # Place a resistor roughly in the middle along the straight path
        ComponentManager.add_component(sch, { 'type': 'R', 'reference': 'Rmid', 'x': 45.0, 'y': 10.0 })
        bbox = compute_symbol_bbox_from_pins(sch, 'Rmid')

        before_wires = len(getattr(sch, 'wire', []))
        ConnectionManager.connect_pins(
            sch,
            {'label': 'A'},
            {'label': 'B'},
            routing={'pattern': 'hv'},
        )
        after_wires = len(sch.wire)
        self.assertGreater(after_wires, before_wires)
        new_segments = sch.wire[before_wires:]

        # Flatten the new polyline points
        flat = []
        for seg in new_segments:
            for p in seg.points:
                flat.append((float(p.value[0]), float(p.value[1])))
        # Dedupe consecutive duplicates
        uniq = []
        for pt in flat:
            if not uniq or uniq[-1] != pt:
                uniq.append(pt)
        # No segment should intersect Rmid's bbox
        for a, b in zip(uniq, uniq[1:]):
            self.assertFalse(seg_intersects_rect(a, b, bbox), f"Segment {a}->{b} should not cross Rmid bbox {bbox}")


if __name__ == '__main__':
    unittest.main()

