from __future__ import annotations

from typing import Any, AsyncContextManager

from .. import StdioServerParameters


def stdio_client(server_params: StdioServerParameters) -> AsyncContextManager[tuple[Any, Any]]: ...
