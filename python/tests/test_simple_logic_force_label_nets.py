from __future__ import annotations

import unittest
from dataclasses import dataclass

from python.netlist_to_schematic.simple_logic import build_simple_logic_hints


@dataclass
class _Pin:
    ref: str
    num: str


@dataclass
class _Net:
    name: str
    pins: list[_Pin]


@dataclass
class _Part:
    ref: str
    name: str
    pins: list[object]


@dataclass
class _Circuit:
    parts: list[_Part]
    nets: list[_Net]


class TestSimpleLogicForceLabelNets(unittest.TestCase):
    def test_force_label_net_names(self) -> None:
        parts = [
            _Part(ref="R1", name="R", pins=[object(), object()]),
            _Part(ref="R2", name="R", pins=[object(), object()]),
        ]
        nets = [
            _Net(name="SIG", pins=[_Pin("R1", "1"), _Pin("R2", "1")]),
            _Net(name="OK", pins=[_Pin("R1", "2"), _Pin("R2", "2")]),
        ]
        circuit = _Circuit(parts=parts, nets=nets)

        forced = build_simple_logic_hints(
            circuit,
            use_direct_connections=True,
            force_label_net_names={"SIG"},
        )
        elk_fields = forced["elk_support_fields"]

        labels = elk_fields["net_labels"]
        conns = elk_fields["direct_connections"]
        self.assertEqual({entry["net_name"] for entry in labels}, {"SIG"})
        self.assertEqual({entry["type"] for entry in labels}, {"local"})
        self.assertEqual(len(labels), 2)
        self.assertEqual(len(conns), 2)
        self.assertEqual(elk_fields["layout_chains"], [])
