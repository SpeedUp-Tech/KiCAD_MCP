import unittest
from sexpdata import Symbol

from python.commands.schematic import SchematicManager
from python.commands.component_schematic import ComponentManager
from python.commands.connection_schematic import ConnectionManager
from python.commands.schematic_state import get_schematic_state


class SchematicStateConnectionMapTests(unittest.TestCase):
    def test_pin_label_edges_and_net_names(self):
        sch = SchematicManager.create_schematic('ConnMapLabels')

        # Components
        ComponentManager.add_component(sch, {'type': 'R', 'reference': 'R1', 'x': 50, 'y': 50})
        ComponentManager.add_component(sch, {'type': 'R', 'reference': 'R2', 'x': 150, 'y': 50})

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
        sch.tree.append(scl_left)
        sch.tree.append(scl_right)

        # Connect label -> pin on both sides so label coordinates are endpoints
        ConnectionManager.connect_pins(sch, {'label': 'SCL'}, {'reference': 'R1', 'pin': '1'})
        ConnectionManager.connect_pins(sch, {'label': 'SCL'}, {'reference': 'R2', 'pin': '1'})

        # Also add an unlabeled net between R1.2 and R2.2
        ConnectionManager.connect_pins(sch, {'reference': 'R1', 'pin': '2'}, {'reference': 'R2', 'pin': '2'})

        state_text = get_schematic_state(sch, show_details=False, output_format="text")
        lines = [ln.strip() for ln in state_text.splitlines() if ln.strip().startswith(('R','S','G'))]

        # Expect explicit pin-label edges with proper delimiter and net names
        self.assertTrue(any('R1.1 - SCL [SCL]' in ln for ln in lines), state_text)
        self.assertTrue(any('R2.1 - SCL [SCL]' in ln for ln in lines), state_text)
        # Expect unlabeled net between pins to have synthetic name
        self.assertTrue(any('R1.2 - R2.2 [Net-(' in ln for ln in lines), state_text)


if __name__ == '__main__':
    unittest.main()
