from __future__ import annotations

from typing import Any, Iterable, Iterator, Sequence

class WireWrapper:
    raw: Any
    stroke: Any
    points: Sequence[Any]
    uuid: Any

    def __init__(self, *args: Any, **kwargs: Any) -> None: ...

    def append(self, point: Any) -> None: ...

    def __len__(self) -> int: ...

    def __iter__(self) -> Iterator[Any]: ...

    def __getitem__(self, index: int) -> Any: ...
