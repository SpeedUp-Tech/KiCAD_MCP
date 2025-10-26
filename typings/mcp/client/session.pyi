from __future__ import annotations

from typing import Any, Awaitable, Iterable, Sequence

from mcp import ContentBase


class ToolResult:
    content: list[ContentBase]
    isError: bool


class ClientSession:
    def __init__(self, read: Any, write: Any) -> None: ...

    async def __aenter__(self) -> ClientSession: ...

    async def __aexit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: Any) -> None: ...

    async def initialize(self) -> None: ...

    async def call_tool(self, *, name: str, arguments: dict[str, Any]) -> ToolResult: ...
