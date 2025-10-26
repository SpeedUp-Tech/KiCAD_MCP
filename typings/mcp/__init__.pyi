from __future__ import annotations

from typing import Any, Iterable, Sequence

__all__ = ["ImageContent", "AudioContent", "EmbeddedResource", "ResourceLink", "StdioServerParameters"]


class ContentBase:
    text: str


class ImageContent(ContentBase):
    data: Any


class AudioContent(ContentBase):
    data: Any


class EmbeddedResource(ContentBase):
    data: Any


class ResourceLink(ContentBase):
    uri: str


class StdioServerParameters:
    command: str
    args: Sequence[str]
    cwd: str | None

    def __init__(self, *, command: str, args: Sequence[str] | None = ..., cwd: str | None = ...) -> None: ...
