"""
Helpers for persisting/retrieving SPICE models from the shared database.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional, Dict

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL_DB = REPO_ROOT / "spice_lib" / "spice_models.db"


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS part_models (
            name TEXT NOT NULL,
            library TEXT NOT NULL,
            model_content TEXT NOT NULL,
            vendor_provided INTEGER NOT NULL DEFAULT 0
        )
        """
    )


def save_part_model(
    *,
    name: str,
    library: str,
    model_content: str,
    vendor_provided: bool = False,
    db_path: Optional[Path | str] = None,
) -> None:
    """Insert or update a model entry."""

    target_db = Path(db_path) if db_path else DEFAULT_MODEL_DB
    target_db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target_db)
    try:
        _ensure_table(conn)
        conn.execute(
            "DELETE FROM part_models WHERE name = ? AND library = ?",
            (name, library),
        )
        conn.execute(
            """
            INSERT INTO part_models (name, library, model_content, vendor_provided)
            VALUES (?, ?, ?, ?)
            """,
            (name, library, model_content, int(bool(vendor_provided))),
        )
        conn.commit()
    finally:
        conn.close()


def search_spice_model(
    *,
    name: str,
    library: str,
    db_path: Optional[Path | str] = None,
) -> Optional[Dict[str, object]]:
    """Return matching model entry or None if missing."""

    target_db = Path(db_path) if db_path else DEFAULT_MODEL_DB
    if not target_db.exists():
        return None

    conn = sqlite3.connect(target_db)
    conn.row_factory = sqlite3.Row
    try:
        _ensure_table(conn)
        cur = conn.execute(
            """
            SELECT name, library, model_content, vendor_provided
            FROM part_models
            WHERE name = ? AND library = ?
            """,
            (name, library),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return {
            "name": row["name"],
            "library": row["library"],
            "model_content": row["model_content"],
            "vendor_provided": bool(row["vendor_provided"]),
        }
    finally:
        conn.close()


__all__ = ["save_part_model", "search_spice_model", "DEFAULT_MODEL_DB"]
