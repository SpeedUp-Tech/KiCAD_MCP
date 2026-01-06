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


def test_clustering_keeps_long_chain_intact() -> None:
    parts = [_Part(ref=f"R{i}", name="R", pins=[object(), object()]) for i in range(1, 31)]
    nets: list[_Net] = []
    for i in range(1, 30):
        nets.append(
            _Net(
                name=f"N{i}",
                pins=[_Pin(f"R{i}", "2"), _Pin(f"R{i + 1}", "1")],
            )
        )
    circuit = _Circuit(parts=parts, nets=nets)

    hints = build_simple_logic_hints(
        circuit,
        use_direct_connections=True,
        cluster_components=True,
        cluster_max_connections=1,
        cluster_min_size=1,
        cluster_max_size=0,
    )
    elk = hints["elk_support_fields"]
    assert elk["net_labels"] == []
    assert elk["direct_connections"] == []


def test_clustering_splits_multi_drop_net() -> None:
    parts = [_Part(ref=f"R{i}", name="R", pins=[object(), object()]) for i in range(1, 7)]
    bus = _Net(
        name="BUS",
        pins=[_Pin(f"R{i}", "1") for i in range(1, 7)],
    )
    circuit = _Circuit(parts=parts, nets=[bus])

    hints = build_simple_logic_hints(
        circuit,
        use_direct_connections=True,
        cluster_components=True,
        cluster_max_connections=1,
        cluster_min_size=1,
        cluster_max_size=0,
    )
    elk = hints["elk_support_fields"]

    labels = elk["net_labels"]
    conns = elk["direct_connections"]
    assert len(labels) >= 2
    assert {entry["net_name"] for entry in labels} == {"BUS"}
    assert {entry["type"] for entry in labels} == {"local"}

    label_ids = {entry["id"] for entry in labels}
    assert len(conns) == 6
    assert all(conn["node"] in label_ids for conn in conns)
    assert {conn["component"] for conn in conns} == {f"R{i}" for i in range(1, 7)}

