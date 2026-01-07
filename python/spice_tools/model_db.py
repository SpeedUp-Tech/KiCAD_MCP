"""
Helpers for persisting/retrieving SPICE models from the configured database.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

from db_tools.settings import get_sqlite_db_path
from db_tools.spice_models import save_part_model, search_spice_model


def resolve_model_db_path(*, for_write: bool) -> Path:
    return get_sqlite_db_path(for_write=for_write)


__all__ = [
    "resolve_model_db_path",
    "save_part_model",
    "search_spice_model",
]

