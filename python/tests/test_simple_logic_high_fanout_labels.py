from __future__ import annotations

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


def test_high_fanout_labels_switch() -> None:
    parts = [
        _Part(ref="R1", name="R", pins=[object(), object()]),
        _Part(ref="R2", name="R", pins=[object(), object()]),
        _Part(ref="R3", name="R", pins=[object(), object()]),
    ]
    net = _Net(
        name="SIG",
        pins=[_Pin("R1", "1"), _Pin("R2", "1"), _Pin("R3", "1")],
    )
    circuit = _Circuit(parts=parts, nets=[net])

    base = build_simple_logic_hints(circuit, use_direct_connections=True)
    elk_fields = base["elk_support_fields"]
    assert elk_fields["net_labels"] == []
    assert elk_fields["direct_connections"] == []

    enhanced = build_simple_logic_hints(
        circuit,
        use_direct_connections=True,
        label_high_fanout_nets=True,
        high_fanout_threshold=3,
    )
    elk_fields = enhanced["elk_support_fields"]

    labels = elk_fields["net_labels"]
    conns = elk_fields["direct_connections"]
    assert len(labels) == 3
    assert len(conns) == 3

    label_ids = {entry["id"] for entry in labels}
    assert {entry["net_name"] for entry in labels} == {"SIG"}
    assert {entry["type"] for entry in labels} == {"local"}

    assert elk_fields["layout_chains"] == []
    assert all(conn["node"] in label_ids for conn in conns)
    assert {conn["component"] for conn in conns} == {"R1", "R2", "R3"}

