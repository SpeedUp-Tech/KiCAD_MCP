from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional


def connect_sqlite(
    path: Path,
    *,
    readonly: bool = False,
    row_factory: bool = True,
    pragmas: Optional[dict[str, str]] = None,
) -> sqlite3.Connection:
    """
    Open a SQLite connection with common defaults used across KiCAD_MCP.

    - `readonly=True` uses SQLite URI mode (`mode=ro`) to prevent accidental writes.
    - `row_factory=True` sets `sqlite3.Row`.
    - `pragmas` allows call-site overrides (e.g., foreign_keys, journal_mode).
    """

    if readonly:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    else:
        conn = sqlite3.connect(str(path))

    if row_factory:
        conn.row_factory = sqlite3.Row

    merged = {"foreign_keys": "OFF"}
    if pragmas:
        merged.update({str(k): str(v) for k, v in pragmas.items()})
    for key, value in merged.items():
        conn.execute(f"PRAGMA {key} = {value}")

    return conn


__all__ = ["connect_sqlite"]

