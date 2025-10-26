from __future__ import annotations

from typing import Any, Iterable, Sequence

from .eeschema.schematic.symbol import SymbolCollection

__all__ = ["Schematic"]


class Schematic:
    symbol: SymbolCollection | Any
    wire: Any
    sheet: Any
    title_block: Any
    version: Any
    generator: Any
    tree: Any
    _added_attribs: Any

    def __init__(self, *args: Any, **kwargs: Any) -> None: ...

    def add_symbol(self, *args: Any, **kwargs: Any) -> Any: ...

    def add_wire(self, *args: Any, **kwargs: Any) -> Any: ...

    def add_path(self, *args: Any, **kwargs: Any) -> Any: ...

    def wrap(self, value: Any) -> Any: ...

    def save(self, *args: Any, **kwargs: Any) -> None: ...

    def write(self, *args: Any, **kwargs: Any) -> None: ...
