from __future__ import annotations

import os
import sqlite3
from functools import lru_cache
from pathlib import Path


def _resolve_repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _default_db_path() -> Path:
    env_path = os.environ.get("JLCPCB_DB_PATH")
    if env_path:
        return Path(env_path).expanduser()
    return _resolve_repo_root() / "part_lib" / "jlcpcb-components.sqlite3"


@lru_cache(maxsize=4)
def _get_connection(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = OFF")
    return conn
