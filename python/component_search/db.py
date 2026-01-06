from __future__ import annotations

import os
import sqlite3
from functools import lru_cache
from pathlib import Path

from kicad_catalog.config import load_catalog_paths, resolve_repo_root
from kicad_catalog.sqlite import connect_sqlite


def _resolve_repo_root() -> Path:
    return resolve_repo_root()


def _default_db_path() -> Path:
    # Keep historical precedence: explicit env var wins.
    env_path = os.environ.get("JLCPCB_DB_PATH")
    if env_path:
        return Path(env_path).expanduser()
    return load_catalog_paths(repo_root=_resolve_repo_root()).component_db


@lru_cache(maxsize=4)
def _get_connection(db_path: Path) -> sqlite3.Connection:
    return connect_sqlite(db_path, readonly=True)
