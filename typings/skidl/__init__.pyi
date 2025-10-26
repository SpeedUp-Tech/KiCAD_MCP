from __future__ import annotations

from typing import Any, Callable, Iterable, Iterator, Mapping, MutableMapping, Sequence

__all__ = [
    "Alias",
    "Circuit",
    "NET",
    "Net",
    "Part",
    "Pin",
    "SKIDL",
    "SchLib",
    "TEMPLATE",
    "POWER",
    "default_circuit",
    "subcircuit",
]


class Pin:
    name: str
    num: str

    def __init__(self, *args: Any, **kwargs: Any) -> None: ...

    def __iadd__(self, other: Any) -> Pin: ...

    def __add__(self, other: Any) -> Any: ...

    def __radd__(self, other: Any) -> Any: ...


class Part:
    ref: str

    def __init__(self, *args: Any, **kwargs: Any) -> None: ...

    def __getitem__(self, key: str | int) -> Pin: ...

    def clone(self, *args: Any, **kwargs: Any) -> Part: ...

    def __setitem__(self, key: str | int, value: Any) -> None: ...


class Net:
    name: str
    drive: Any

    def __init__(self, name: str | None = ...) -> None: ...

    def __iadd__(self, pins: Iterable[Any]) -> Net: ...

    def __iter__(self) -> Iterator[Any]: ...

    def append(self, pin: Any) -> None: ...

    def get_pins(self) -> Sequence[Any]: ...


NET = Net


class Circuit:
    parts: Sequence[Part]
    nets: Sequence[Net]
    NC: Net

    def __init__(self, *args: Any, **kwargs: Any) -> None: ...

    def __iadd__(self, parts: Iterable[Part]) -> Circuit: ...

    def add_parts(self, *parts: Part, **kwargs: Any) -> None: ...

    def __enter__(self) -> Circuit: ...

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: Any) -> None: ...

    def generate_netlist(self, *args: Any, **kwargs: Any) -> None: ...

    def ERC(self, *args: Any, **kwargs: Any) -> None: ...


class Alias:
    def __init__(self, aliases: Iterable[str]) -> None: ...


class SchLib:
    def __init__(self, *args: Any, **kwargs: Any) -> None: ...

    def add_parts(self, *parts: Part) -> SchLib: ...


class SKIDL:
    ...


class TEMPLATE:
    ...


default_circuit: Circuit


POWER: Any


def subcircuit(func: Callable[..., Any]) -> Callable[..., Any]: ...
