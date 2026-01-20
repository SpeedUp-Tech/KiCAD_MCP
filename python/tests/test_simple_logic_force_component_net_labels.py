from __future__ import annotations

import unittest
from dataclasses import dataclass

from python.netlist_to_schematic.simple_logic import (
    append_forced_component_net_labels,
    build_simple_logic_hints,
)


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


def _count_labels_for_net(logic: dict, net_name: str) -> int:
    elk_fields = logic.get("elk_support_fields", {}) or {}
    labels = elk_fields.get("net_labels", []) or []
    return sum(
        1
        for entry in labels
        if isinstance(entry, dict) and str(entry.get("net_name") or entry.get("id") or "").strip() == net_name
    )


class TestSimpleLogicForceComponentNetLabels(unittest.TestCase):
    def test_does_not_duplicate_existing_interface_label_direct(self) -> None:
        parts = [
            _Part(ref="C1", name="C", pins=[object(), object()]),
            _Part(ref="U1", name="U", pins=[object(), object()]),
        ]
        nets = [
            _Net(name="VBAT", pins=[_Pin("C1", "1"), _Pin("U1", "1")]),
        ]
        circuit = _Circuit(parts=parts, nets=nets)

        logic = build_simple_logic_hints(
            circuit,
            interface_nets={"VBAT"},
            use_direct_connections=True,
        )
        before = _count_labels_for_net(logic, "VBAT")

        append_forced_component_net_labels(
            logic,
            circuit,
            {("C1", "VBAT")},
            interface_nets={"VBAT"},
            use_direct_connections=True,
        )
        after = _count_labels_for_net(logic, "VBAT")

        self.assertEqual(before, 1)
        self.assertEqual(after, 1)
        elk_fields = logic["elk_support_fields"]
        label_ids = {entry.get("id") for entry in elk_fields.get("net_labels", []) if isinstance(entry, dict)}
        self.assertFalse(any(isinstance(i, str) and i.endswith("_cut") for i in label_ids))

    def test_does_not_duplicate_existing_interface_label_chain(self) -> None:
        parts = [
            _Part(ref="C1", name="C", pins=[object(), object()]),
            _Part(ref="U1", name="U", pins=[object(), object()]),
        ]
        nets = [
            _Net(name="VBAT", pins=[_Pin("C1", "1"), _Pin("U1", "1")]),
        ]
        circuit = _Circuit(parts=parts, nets=nets)

        logic = build_simple_logic_hints(
            circuit,
            interface_nets={"VBAT"},
            use_direct_connections=False,
        )
        before = _count_labels_for_net(logic, "VBAT")

        append_forced_component_net_labels(
            logic,
            circuit,
            {("C1", "VBAT")},
            interface_nets={"VBAT"},
            use_direct_connections=False,
        )
        after = _count_labels_for_net(logic, "VBAT")

        self.assertEqual(before, 1)
        self.assertEqual(after, 1)

