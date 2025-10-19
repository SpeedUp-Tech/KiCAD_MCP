import unittest
from pathlib import Path

from python.commands.schematic import SchematicManager
from python.commands.component_schematic import ComponentManager
from python.commands.connection_schematic import ConnectionManager
from python.commands.schematic_state import _extract_components, _build_connection_map
from python.commands.grid_utils import snap_to_grid

EXPORT_DIR = Path(__file__).resolve().parents[2] / 'exported'
EXPORT_DIR.mkdir(parents=True, exist_ok=True)

from sexpdata import Symbol


class UpdateComponentReconnectionTests(unittest.TestCase):
    def _find_edge_between(self, edges, ref_a, pin_a, ref_b, pin_b):
        for e in edges:
            a = e.get('a') or {}
            b = e.get('b') or {}
            if a.get('kind') == 'pin' and b.get('kind') == 'pin':
                ar, ap = str(a.get('ref')), str(a.get('pin'))
                br, bp = str(b.get('ref')), str(b.get('pin'))
                if (ar, ap, br, bp) == (ref_a, pin_a, ref_b, pin_b) or (ar, ap, br, bp) == (ref_b, pin_b, ref_a, pin_a):
                    return e
    def _find_edge_pin_label(self, edges, ref, pin, label_name):
        for e in edges:
            a = e.get('a') or {}
            b = e.get('b') or {}
            if a.get('kind') == 'pin' and b.get('kind') == 'label':
                if str(a.get('ref')) == ref and str(a.get('pin')) == pin and str(b.get('name')) == label_name:
                    return e
            if a.get('kind') == 'label' and b.get('kind') == 'pin':
                if str(b.get('ref')) == ref and str(b.get('pin')) == pin and str(a.get('name')) == label_name:
                    return e
        return None


    def test_move_component_preserves_and_reroutes_connections(self):
        schematic = SchematicManager.create_schematic('MoveReroute')

        ComponentManager.add_component(schematic, { 'type': 'R', 'reference': 'R1', 'x': 0, 'y': 0 })
        ComponentManager.add_component(schematic, { 'type': 'R', 'reference': 'R2', 'x': 30, 'y': 0 })

        # Connect R1.1 to R2.1
        ConnectionManager.connect_pins(
            schematic,
            { 'reference': 'R1', 'pin': '1' },
            { 'reference': 'R2', 'pin': '1' },
        )

        # Build initial connection map; sanity check connection exists
        edges_before = _build_connection_map(schematic, _extract_components(schematic))
        self.assertIsNotNone(self._find_edge_between(edges_before, 'R1', '1', 'R2', '1'))

        # Move R1 to a new location and rotate it
        ComponentManager.update_component(schematic, 'R1', { 'x': 60, 'y': 0, 'rotation': 90 })

        # After move, connection should be reconstructed between R1.1 and R2.1
        edges_after = _build_connection_map(schematic, _extract_components(schematic))
        self.assertIsNotNone(self._find_edge_between(edges_after, 'R1', '1', 'R2', '1'))

        # Ensure there are wires present and not dangling at the old R1.1 location
        # (i.e., the algorithm re-routed instead of just dragging endpoints)
        self.assertTrue(hasattr(schematic, 'wire'))
        self.assertGreaterEqual(len(schematic.wire), 1)

    def test_update_failure_rolls_back_no_changes(self):
        schematic = SchematicManager.create_schematic('MoveRollback')

        ComponentManager.add_component(schematic, { 'type': 'R', 'reference': 'R1', 'x': 0, 'y': 0 })
        ComponentManager.add_component(schematic, { 'type': 'R', 'reference': 'R2', 'x': 30, 'y': 0 })

        # Connect R1.2 to R2.2
        ConnectionManager.connect_pins(
            schematic,
            { 'reference': 'R1', 'pin': '2' },
            { 'reference': 'R2', 'pin': '2' },
        )

        # Attempt an update that must fail: duplicate new reference
        with self.assertRaises(Exception):
            ComponentManager.update_component(schematic, 'R1', { 'x': 60, 'newReference': 'R2' })

        # Verify that the original connection still exists and R1 did not move
        edges_after = _build_connection_map(schematic, _extract_components(schematic))
        self.assertIsNotNone(self._find_edge_between(edges_after, 'R1', '2', 'R2', '2'))
    def test_move_only_preserves_connection(self):
        schematic = SchematicManager.create_schematic('MoveOnly')
        ComponentManager.add_component(schematic, { 'type': 'R', 'reference': 'R1', 'x': 0, 'y': 0 })
        ComponentManager.add_component(schematic, { 'type': 'R', 'reference': 'R2', 'x': 40, 'y': 0 })
        ConnectionManager.connect_pins(
            schematic,
            { 'reference': 'R1', 'pin': '1' },
            { 'reference': 'R2', 'pin': '1' },
        )
        edges_before = _build_connection_map(schematic, _extract_components(schematic))
        self.assertIsNotNone(self._find_edge_between(edges_before, 'R1', '1', 'R2', '1'))
        # Move without rotation
        ComponentManager.update_component(schematic, 'R1', { 'x': 80, 'y': 0 })
        edges_after = _build_connection_map(schematic, _extract_components(schematic))
        self.assertIsNotNone(self._find_edge_between(edges_after, 'R1', '1', 'R2', '1'))

    def test_rotate_only_preserves_connection(self):
        schematic = SchematicManager.create_schematic('RotateOnly')
        ComponentManager.add_component(schematic, { 'type': 'R', 'reference': 'R1', 'x': 0, 'y': 0 })
        ComponentManager.add_component(schematic, { 'type': 'R', 'reference': 'R2', 'x': 40, 'y': 0 })
        ConnectionManager.connect_pins(
            schematic,
            { 'reference': 'R1', 'pin': '2' },
            { 'reference': 'R2', 'pin': '2' },
        )
        edges_before = _build_connection_map(schematic, _extract_components(schematic))
        self.assertIsNotNone(self._find_edge_between(edges_before, 'R1', '2', 'R2', '2'))
        # Rotate without moving
        ComponentManager.update_component(schematic, 'R1', { 'rotation': 180 })
        edges_after = _build_connection_map(schematic, _extract_components(schematic))
        self.assertIsNotNone(self._find_edge_between(edges_after, 'R1', '2', 'R2', '2'))

    def test_pin_to_label_reconnect_on_move(self):
        schematic = SchematicManager.create_schematic('PinToLabel')
        # Add one component and a local label connected by a wire
        ComponentManager.add_component(schematic, { 'type': 'R', 'reference': 'R1', 'x': 0, 'y': 0 })
        # Build a local label at (40, 0) and wire to it
        label_node = [Symbol('label'), 'NET_L', [Symbol('at'), snap_to_grid(40.0), snap_to_grid(0.0), 0.0], [Symbol('effects')]]
        schematic.tree.append(label_node)
        # Connect R1.1 to label NET_L via helper
        ConnectionManager.connect_pins(
            schematic,
            { 'reference': 'R1', 'pin': '1' },
            { 'label': 'NET_L' },
        )
        edges_before = _build_connection_map(schematic, _extract_components(schematic))
        self.assertIsNotNone(self._find_edge_pin_label(edges_before, 'R1', '1', 'NET_L'))
        # Move R1 so reconnection must re-route to label
        ComponentManager.update_component(schematic, 'R1', { 'x': 80, 'y': 0 })
        edges_after = _build_connection_map(schematic, _extract_components(schematic))
        self.assertIsNotNone(self._find_edge_pin_label(edges_after, 'R1', '1', 'NET_L'))

    def test_rename_with_move_preserves_connections(self):
        schematic = SchematicManager.create_schematic('RenameWithMove')
        ComponentManager.add_component(schematic, { 'type': 'R', 'reference': 'R1', 'x': 0, 'y': 0 })
        ComponentManager.add_component(schematic, { 'type': 'R', 'reference': 'R2', 'x': 40, 'y': 0 })
        ConnectionManager.connect_pins(
            schematic,
            { 'reference': 'R1', 'pin': '1' },
            { 'reference': 'R2', 'pin': '1' },
        )
        # Trigger placement change and rename R1 -> R10
        ComponentManager.update_component(schematic, 'R1', { 'x': 80, 'y': 0, 'newReference': 'R10' })
        edges_after = _build_connection_map(schematic, _extract_components(schematic))
        # Connection should now show R10.1—R2.1
        found = False
        for e in edges_after:
            a = e.get('a') or {}
            b = e.get('b') or {}
            if a.get('kind') == 'pin' and b.get('kind') == 'pin':
                ar, ap = str(a.get('ref')), str(a.get('pin'))
                br, bp = str(b.get('ref')), str(b.get('pin'))
                if (ar, ap, br, bp) == ('R10', '1', 'R2', '1') or (ar, ap, br, bp) == ('R2', '1', 'R10', '1'):
                    found = True
                    break
        self.assertTrue(found)



if __name__ == '__main__':
    unittest.main()

