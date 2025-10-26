from __future__ import annotations

from typing import Any, Iterable, Sequence


class ParsedValue:
    def __init__(self, *args: Any, **kwargs: Any) -> None: ...

    value: Any
