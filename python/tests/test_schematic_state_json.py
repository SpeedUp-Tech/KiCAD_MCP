import unittest
from sexpdata import Symbol

from python.commands.schematic import SchematicManager
from python.commands.component_schematic import ComponentManager
from python.commands.connection_schematic import ConnectionManager
from python.commands.schematic_state import get_schematic_state


class SchematicStateJsonTests(unittest.TestCase):
    def setUp(self):
        self.sch = SchematicManager.create_schematic('JsonState')

    def _add_two_resistors_with_scl_labels(self):
        # Components
        ComponentManager.add_component(self.sch, {'type': 'R', 'reference': 'R1', 'x': 50, 'y': 50})
        ComponentManager.add_component(self.sch, {'type': 'R', 'reference': 'R2', 'x': 150, 'y': 50})

        # Two hierarchical labels named SCL (positions arbitrary)
        scl_left = [
            Symbol('hierarchical_label'),
            'SCL',
            [Symbol('shape'), Symbol('bidirectional')],
            [Symbol('at'), 30.0, 50.0, 0],
        ]
        scl_right = [
            Symbol('hierarchical_label'),
            'SCL',
            [Symbol('shape'), Symbol('bidirectional')],
            [Symbol('at'), 170.0, 50.0, 0],
        ]
        self.sch.tree.append(scl_left)
        self.sch.tree.append(scl_right)

        # Connect label -> pin on both sides so label coordinates are endpoints
        ConnectionManager.connect_pins(self.sch, {'label': 'SCL'}, {'reference': 'R1', 'pin': '1'})
        ConnectionManager.connect_pins(self.sch, {'label': 'SCL'}, {'reference': 'R2', 'pin': '1'})

        # Also add an unlabeled net between R1.2 and R2.2
        ConnectionManager.connect_pins(self.sch, {'reference': 'R1', 'pin': '2'}, {'reference': 'R2', 'pin': '2'})

    def test_default_output_is_json_and_structure_is_valid(self):
        self._add_two_resistors_with_scl_labels()

        # Default should be JSON (no output_format specified)
        state = get_schematic_state(self.sch, show_details=False)
        self.assertIsInstance(state, dict)

        # Mode
        self.assertEqual(state.get('mode'), 'simple')

        # Components
        comps = state.get('components', [])
        self.assertEqual(len(comps), 2)
        refs = {c.get('reference') for c in comps}
        self.assertEqual(refs, {'R1', 'R2'})
        # In simple mode, detailed-only fields should be absent
        for c in comps:
            self.assertNotIn('position', c)
            self.assertNotIn('rotation', c)
            # footprint is detailed-only in our JSON return logic
            self.assertNotIn('footprint', c)
            self.assertIn('pins', c)
            for p in c['pins']:
                self.assertIn('number', p)
                self.assertIn('type', p)
                self.assertIn('name', p)
                self.assertNotIn('position', p)

        # Labels as flat list with kind
        lbls = state.get('labels', [])
        self.assertTrue(any(l.get('kind') == 'hierarchical' and l.get('name') == 'SCL' for l in lbls))

        # Connections structure
        conns = state.get('connections', [])
        self.assertGreaterEqual(len(conns), 2)

        # Expect at least one edge on net SCL connecting a pin endpoint and the label endpoint
        def is_pin(ep, ref, pin):
            return ep.get('kind') == 'pin' and ep.get('ref') == ref and ep.get('pin') == pin
        def is_label(ep, name):
            return ep.get('kind') == 'label' and ep.get('name') == name

        has_r1_scl = any(
            conn.get('net') == 'SCL' and (
                (is_pin(conn.get('a', {}), 'R1', '1') and is_label(conn.get('b', {}), 'SCL')) or
                (is_pin(conn.get('b', {}), 'R1', '1') and is_label(conn.get('a', {}), 'SCL'))
            )
            for conn in conns
        )
        self.assertTrue(has_r1_scl)

        has_r2_scl = any(
            conn.get('net') == 'SCL' and (
                (is_pin(conn.get('a', {}), 'R2', '1') and is_label(conn.get('b', {}), 'SCL')) or
                (is_pin(conn.get('b', {}), 'R2', '1') and is_label(conn.get('a', {}), 'SCL'))
            )
            for conn in conns
        )
        self.assertTrue(has_r2_scl)

        # Expect an unlabeled net between R1.2 and R2.2 with a synthetic Net-(Ref-Pad) name
        has_unlabeled_pair = any(
            conn.get('net', '').startswith('Net-(') and (
                (is_pin(conn.get('a', {}), 'R1', '2') and is_pin(conn.get('b', {}), 'R2', '2')) or
                (is_pin(conn.get('a', {}), 'R2', '2') and is_pin(conn.get('b', {}), 'R1', '2'))
            )
            for conn in conns
        )
        self.assertTrue(has_unlabeled_pair)

    def test_detailed_mode_includes_positions(self):
        # One component to keep assertions simple
        ComponentManager.add_component(self.sch, {'type': 'R', 'reference': 'R1', 'x': 10, 'y': 20})
        state = get_schematic_state(self.sch, show_details=True)  # default json
        self.assertIsInstance(state, dict)
        self.assertEqual(state.get('mode'), 'detailed')

        comps = state.get('components', [])
        self.assertEqual(len(comps), 1)
        c = comps[0]
        # Position should be present in detailed mode if available
        # It may be absent if underlying symbol lacks coordinates, but here we set x/y
        # so we expect a position dict
        self.assertIn('position', c)
        pos = c.get('position')
        self.assertIsInstance(pos, dict)
        self.assertIn('x', pos)
        self.assertIn('y', pos)

        # Pin positions may or may not be available depending on symbol details; don't assert them


if __name__ == '__main__':
    unittest.main()
