from __future__ import annotations

from typing import Any, Iterable, Iterator, List, Sequence

class Symbol:
    at: Any
    property: Any
    unit: Any
    uuid: Any
    lib_id: Any
    Reference: Any
    Value: Any
    reference: Any
    value: Any
    raw: Any
    raw_parent: Any
    pin: Any
    in_bom: Any
    on_board: Any
    dnp: Any

    def __init__(self, *args: Any, **kwargs: Any) -> None: ...

    def __getitem__(self, key: str | int) -> Any: ...

    def setAllReferences(self, *args: Any, **kwargs: Any) -> None: ...


class SymbolCollection(Sequence[Symbol]):
    _elements: list[Symbol]

    def __init__(self, *args: Any, **kwargs: Any) -> None: ...

    def __getitem__(self, index: int) -> Symbol: ...

    def __len__(self) -> int: ...

    def __iter__(self) -> Iterator[Symbol]: ...

    def append(self, item: Symbol) -> None: ...
