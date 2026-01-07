#!/usr/bin/env python3
"""
Build a single unified SQLite DB that embeds the legacy split DBs.

The unified DB keeps *legacy* table names/schemas to minimize migration risk:
- JLC components/search tables/views (copied wholesale from jlcpcb-components.sqlite3)
- symbol_index (from kicad_symbols.sqlite3)
- footprint_index (from kicad_footprints.sqlite3)
- models + part_models (from spice_models.db)

The destination path is taken from `db_tools/settings.json` (`db_url`).

The original databases are never modified.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path
from typing import Iterable, Optional, Sequence, Tuple
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PYTHON_ROOT = PROJECT_ROOT / "python"
for _path in (PROJECT_ROOT, PYTHON_ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from db_tools.schema import migrate as schema_migrate
from db_tools.schema.migrate import apply_migrations
from db_tools.settings import get_settings, resolve_sqlite_path
from db_tools.sqlite import connect_sqlite
from db_tools.workdir import is_protected_db_path


def _connect_ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def _backup_db(source: Path, dest: Path) -> None:
    src = _connect_ro(source)
    try:
        dst = sqlite3.connect(str(dest))
        try:
            src.backup(dst)
            dst.commit()
        finally:
            dst.close()
    finally:
        src.close()


def _iter_copy_objects(conn: sqlite3.Connection, schema: str) -> Iterable[Tuple[str, str, str]]:
    """
    Yield (type, name, sql) for objects that should be copied into main.
    """

    rows = conn.execute(
        f"""
        SELECT type, name, sql
        FROM {schema}.sqlite_master
        WHERE type IN ('table', 'index', 'trigger', 'view')
          AND name NOT LIKE 'sqlite_%'
          AND sql IS NOT NULL
        ORDER BY type, name
        """
    ).fetchall()
    for row in rows:
        yield (row[0], row[1], row[2])


def _copy_table_data(conn: sqlite3.Connection, *, schema: str, table: str) -> None:
    conn.execute(f"INSERT INTO main.{table} SELECT * FROM {schema}.{table}")


def _copy_db_objects(
    conn: sqlite3.Connection,
    *,
    schema: str,
    include_tables: Optional[set[str]] = None,
) -> None:
    for obj_type, name, sql in _iter_copy_objects(conn, schema):
        if obj_type == "table":
            if include_tables is not None and name not in include_tables:
                continue
            conn.execute(sql)
            _copy_table_data(conn, schema=schema, table=name)
        elif obj_type == "index":
            if include_tables is not None:
                # Heuristic: only copy indexes that mention included tables.
                if not any(tbl in sql for tbl in include_tables):
                    continue
            conn.execute(sql)
        elif obj_type == "view":
            # Views are only copied when copying the entire components DB; for the
            # smaller DBs we do not depend on views.
            if include_tables is not None:
                continue
            conn.execute(sql)
        elif obj_type == "trigger":
            if include_tables is not None:
                continue
            conn.execute(sql)


def _write_meta(conn: sqlite3.Connection, *, sources: dict[str, str]) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS catalog_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "INSERT OR REPLACE INTO catalog_meta (key, value) VALUES (?, ?)",
        ("created_at", str(int(time.time()))),
    )
    conn.execute(
        "INSERT OR REPLACE INTO catalog_meta (key, value) VALUES (?, ?)",
        ("schema_version", "1"),
    )
    for key, value in sorted(sources.items()):
        conn.execute(
            "INSERT OR REPLACE INTO catalog_meta (key, value) VALUES (?, ?)",
            (f"source.{key}", value),
        )


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite the destination DB if it exists.",
    )
    parser.add_argument(
        "--component-db",
        type=Path,
        default=PROJECT_ROOT / "part_lib" / "jlcpcb-components.sqlite3",
        help="Source jlcpcb-components.sqlite3 path.",
    )
    parser.add_argument(
        "--symbol-db",
        type=Path,
        default=PROJECT_ROOT / "symbol_lib" / "kicad_symbols.sqlite3",
        help="Source kicad_symbols.sqlite3 path.",
    )
    parser.add_argument(
        "--footprint-db",
        type=Path,
        default=PROJECT_ROOT / "symbol_lib" / "kicad_footprints.sqlite3",
        help="Source kicad_footprints.sqlite3 path.",
    )
    parser.add_argument(
        "--spice-db",
        type=Path,
        default=PROJECT_ROOT / "spice_lib" / "spice_models.db",
        help="Source spice_models.db path.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)

    component_db = args.component_db.expanduser().resolve()
    symbol_db = args.symbol_db.expanduser().resolve()
    footprint_db = args.footprint_db.expanduser().resolve()
    spice_db = args.spice_db.expanduser().resolve()

    missing = [str(p) for p in (component_db, symbol_db, footprint_db, spice_db) if not p.exists()]
    if missing:
        print(f"ERROR: Missing source DB(s): {missing}", file=sys.stderr)
        return 2

    settings = get_settings()
    scheme = urlparse(settings.db_url).scheme.lower()
    if scheme != "sqlite":
        print(
            "ERROR: build_unified_db.py only supports sqlite db_url.\n"
            f"Configured db_url scheme is: {scheme!r}\n"
            "Set `db_tools/settings.json` -> `db_url` to a sqlite:////... path before running.",
            file=sys.stderr,
        )
        return 2
    dest = resolve_sqlite_path(settings.db_url)
    if is_protected_db_path(dest):
        print(
            "ERROR: Refusing to write unified DB into a protected directory.\n"
            f"Configured db_url points to: {dest}\n"
            "Choose a destination outside `protected_roots` in `db_tools/settings.json`.",
            file=sys.stderr,
        )
        return 2

    if dest.exists() and not args.overwrite:
        print(f"ERROR: Destination already exists: {dest} (pass --overwrite)", file=sys.stderr)
        return 2
    if dest.exists():
        dest.unlink()
    dest.parent.mkdir(parents=True, exist_ok=True)

    print(f"Copying components DB into {dest} ...", file=sys.stderr)
    _backup_db(component_db, dest)

    conn = connect_sqlite(dest)
    try:
        conn.execute("ATTACH DATABASE ? AS sym", (str(symbol_db),))
        conn.execute("ATTACH DATABASE ? AS fp", (str(footprint_db),))
        conn.execute("ATTACH DATABASE ? AS spice", (str(spice_db),))

        print("Copying symbol_index ...", file=sys.stderr)
        _copy_db_objects(conn, schema="sym", include_tables={"symbol_index"})

        print("Copying footprint_index ...", file=sys.stderr)
        _copy_db_objects(conn, schema="fp", include_tables={"footprint_index"})

        print("Copying SPICE tables ...", file=sys.stderr)
        _copy_db_objects(conn, schema="spice", include_tables={"models", "part_models"})

        _write_meta(
            conn,
            sources={
                "components": str(component_db),
                "symbols": str(symbol_db),
                "footprints": str(footprint_db),
                "spice": str(spice_db),
            },
        )
        migrations_dir = Path(schema_migrate.__file__).resolve().parent / "migrations"
        apply_migrations(conn, migrations_dir=migrations_dir)

        conn.commit()
    finally:
        conn.close()

    print("✓ Unified catalog built successfully.", file=sys.stderr)
    print(f"  {dest}", file=sys.stderr)
    print("\nNext:", file=sys.stderr)
    print("  - Ensure `db_tools/settings.json` -> `db_url` points at the path above.", file=sys.stderr)
    print("  - Rerun smoke checks: python scripts/catalog/smoke_check.py", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
