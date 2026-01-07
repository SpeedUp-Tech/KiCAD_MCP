"""
Compatibility shim for `component_search` imports.

The canonical implementation now lives under `db_tools.component_search`
as part of the extracted catalog library. This shim keeps historical imports
working when the repository root is on `sys.path`.
"""

from __future__ import annotations

import importlib
import sys

from db_tools.component_search import *  # noqa: F403
from db_tools.component_search import __all__  # noqa: F401

for _name in (
    "constants",
    "create_filtered_fts",
    "db",
    "normalize",
    "search_example",
    "search",
    "search_utils",
    "setup",
):
    sys.modules[f"{__name__}.{_name}"] = importlib.import_module(
        f"db_tools.component_search.{_name}"
    )
