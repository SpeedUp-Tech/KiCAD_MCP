import unittest
from sexpdata import Symbol

from python.commands.schematic import SchematicManager
from python.commands.component_schematic import ComponentManager
from python.commands.connection_schematic import ConnectionManager, _collect_symbol_bboxes
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

        # Get bbox using the same method as the routing code
        bboxes = _collect_symbol_bboxes(sch)
        rmid_bbox = None
        for (bbox_rect, sym) in bboxes:
            if hasattr(sym, 'property') and hasattr(sym.property, 'Reference'):
                if sym.property.Reference.value == 'Rmid':
                    rmid_bbox = bbox_rect
                    break
        assert rmid_bbox is not None, "Rmid bbox not found"
        bbox = rmid_bbox

        before_wires = len(getattr(sch, 'wire', []))
        ConnectionManager.connect_pins(
            sch,
            {'label': 'A'},
            {'label': 'B'},
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

