"""
Centralized catalog/data-access utilities (migration in progress).

This package is intended to become the extracted "catalog" sub-repo that owns
database access, schema, and ETL tooling. During migration it can be used as a
single place for path/config resolution and shared SQLite helpers.
"""

from .config import (  # noqa: F401
    CatalogPaths,
    load_catalog_paths,
    resolve_repo_root,
)
from .sqlite import (  # noqa: F401
    connect_sqlite,
)
from .workdir import (  # noqa: F401
    ensure_db_work_copy,
    get_db_work_dir,
    is_protected_db_path,
    resolve_read_db_path,
    resolve_write_db_path,
)

__all__ = [
    "CatalogPaths",
    "connect_sqlite",
    "ensure_db_work_copy",
    "get_db_work_dir",
    "is_protected_db_path",
    "load_catalog_paths",
    "resolve_read_db_path",
    "resolve_repo_root",
    "resolve_write_db_path",
]
