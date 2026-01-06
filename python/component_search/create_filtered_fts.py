"""
Create a filtered FTS table for component search.

Run:
    python -m component_search.create_filtered_fts /path/to/jlcpcb-components.sqlite3
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Optional, Sequence, Union

from kicad_catalog.config import resolve_repo_root
from kicad_catalog.sqlite import connect_sqlite
from kicad_catalog.workdir import resolve_write_db_path

from .constants import FILTERED_FTS_TABLE, _EXCLUDED_FAMILIES, _IC_FAMILIES

DatabasePath = Union[str, Path]
REPO_ROOT = resolve_repo_root()


def _normalize_db_path(db_path: DatabasePath) -> Path:
    return Path(db_path).expanduser()


def create_filtered_fts(db_path: DatabasePath) -> None:
    """Create and populate the filtered FTS table."""

    path = _normalize_db_path(db_path)
    conn = connect_sqlite(path)
    cursor = conn.cursor()

    cursor.execute(f"DROP TABLE IF EXISTS {FILTERED_FTS_TABLE}")

    cursor.execute(
        f"""
        CREATE VIRTUAL TABLE {FILTERED_FTS_TABLE} USING fts5(
            lcsc UNINDEXED,
            mpn,
            package,
            family,
            class,
            specs
        )
        """
    )
    print(f"✓ Created FTS table {FILTERED_FTS_TABLE}")

    ic_placeholders = ", ".join("?" for _ in _IC_FAMILIES) or "NULL"
    excluded_placeholders = ", ".join("?" for _ in _EXCLUDED_FAMILIES) or "NULL"

    insert_sql = f"""
        INSERT INTO {FILTERED_FTS_TABLE}(lcsc, mpn, package, family, class, specs)
        SELECT lcsc, mpn, package, family, class, specs
        FROM v_components_search
        WHERE symbol_lib = 1
          AND (
                family IN ({ic_placeholders}) OR
                spice_model IS NOT NULL
              )
          AND (
                family IS NULL OR
                family NOT IN ({excluded_placeholders})
              )
          AND datasheet IS NOT NULL
          AND datasheet != ''
          AND LOWER(datasheet) != 'unknown'
    """

    cursor.execute(
        insert_sql,
        (
            *_IC_FAMILIES,
            *_EXCLUDED_FAMILIES,
        ),
    )
    conn.commit()

    cursor.execute(f"SELECT COUNT(*) FROM {FILTERED_FTS_TABLE}")
    total = cursor.fetchone()[0]
    print(f"✓ Populated filtered FTS with {total} entries")
    conn.close()


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create the filtered FTS table for component search."
    )
    parser.add_argument("db_path", help="Path to the jlcpcb-components.sqlite3 database")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    db_path = Path(args.db_path).expanduser()

    if not db_path.exists():
        print(f"Error: Database file not found: {db_path}")
        return 1

    try:
        effective_db_path = resolve_write_db_path(db_path, prefix="component", repo_root=REPO_ROOT)
        if effective_db_path != db_path:
            print(f"Using working copy (original is protected): {effective_db_path}")
        create_filtered_fts(effective_db_path)
    except Exception as exc:  # pragma: no cover - CLI guard
        print(f"\n✗ Error during filtered FTS creation: {exc}")
        import traceback

        traceback.print_exc()
        return 1

    print("\n✓ Filtered FTS setup completed successfully!")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    sys.exit(main())
