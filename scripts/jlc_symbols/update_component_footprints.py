#!/usr/bin/env python3
"""
Populate the `footprint` column in part_lib/jlcpcb-components.sqlite3.

The script extracts each symbol's Footprint property from
symbol_lib/kicad_symbols.sqlite3 and bulk-updates the components table so the
component search API can return the exact footprint string for every MPN.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
from pathlib import Path
from typing import Dict, Iterator, Optional, Tuple, List
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PYTHON_ROOT = PROJECT_ROOT / "python"
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from db_tools.sqlite import connect_sqlite
from db_tools.workdir import resolve_read_db_path, resolve_write_db_path

DEFAULT_COMPONENTS_DB = PROJECT_ROOT / "part_lib" / "jlcpcb-components.sqlite3"
DEFAULT_SYMBOL_DB = PROJECT_ROOT / "symbol_lib" / "kicad_symbols.sqlite3"

FOOTPRINT_REGEX = re.compile(r'\(property\s+"Footprint"\s+"([^"]*)"', re.IGNORECASE | re.MULTILINE)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill component footprints from the symbol database.")
    parser.add_argument(
        "--components-db",
        type=Path,
        default=DEFAULT_COMPONENTS_DB,
        help="Path to jlcpcb-components.sqlite3 (default: %(default)s)",
    )
    parser.add_argument(
        "--symbol-db",
        type=Path,
        default=DEFAULT_SYMBOL_DB,
        help="Path to kicad_symbols.sqlite3 (default: %(default)s)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=5000,
        help="Number of rows to insert per transaction batch (default: %(default)s)",
    )
    return parser.parse_args()


def _normalize_footprint(value: str) -> str:
    value = value.strip()
    if not value:
        return value
    parts = value.split(':')
    if len(parts) >= 3 and parts[0] == parts[1]:
        return ':'.join([parts[0]] + parts[2:])
    return value


def _extract_footprint(sexp: str) -> Optional[str]:
    match = FOOTPRINT_REGEX.search(sexp)
    if not match:
        return None
    value = match.group(1)
    if not value:
        return None
    normalized = _normalize_footprint(value)
    return normalized or None


def iter_symbol_footprints(symbol_conn: sqlite3.Connection) -> Iterator[Tuple[str, str]]:
    cursor = symbol_conn.execute("SELECT mpn, sexp FROM symbol_index")
    for mpn, sexp in cursor:
        if not mpn or not sexp:
            continue
        footprint = _extract_footprint(sexp)
        if footprint:
            yield mpn, footprint


def ensure_components_schema(conn: sqlite3.Connection) -> None:
    columns = [row[1] for row in conn.execute("PRAGMA table_info(components)")]
    if "footprint" not in columns:
        conn.execute("ALTER TABLE components ADD COLUMN footprint TEXT")


def backfill_footprints(
    components_conn: sqlite3.Connection,
    symbol_conn: sqlite3.Connection,
    batch_size: int,
) -> Dict[str, int]:
    stats = {"symbols_seen": 0, "footprints_found": 0, "components_updated": 0}

    ensure_components_schema(components_conn)
    components_conn.execute("DROP TABLE IF EXISTS temp_mpn_footprints")
    components_conn.execute("CREATE TEMP TABLE temp_mpn_footprints (mpn TEXT PRIMARY KEY, footprint TEXT)")

    insert_sql = "INSERT OR REPLACE INTO temp_mpn_footprints (mpn, footprint) VALUES (?, ?)"
    batch: List[Tuple[str, str]] = []

    cursor = symbol_conn.execute("SELECT COUNT(*) FROM symbol_index")
    total_symbols = cursor.fetchone()[0] or 0

    progress_interval = max(total_symbols // 100, batch_size)
    for mpn, footprint in iter_symbol_footprints(symbol_conn):
        stats["symbols_seen"] += 1
        stats["footprints_found"] += 1
        batch.append((mpn, footprint))
        if len(batch) >= batch_size:
            components_conn.executemany(insert_sql, batch)
            batch.clear()
        if progress_interval and (
            stats["symbols_seen"] % progress_interval == 0 or stats["symbols_seen"] == total_symbols
        ):
            percent = (stats["symbols_seen"] / total_symbols * 100) if total_symbols else 0
            sys.stdout.write(f"\rSymbols processed: {stats['symbols_seen']}/{total_symbols} ({percent:5.1f}%)")
            sys.stdout.flush()
    if batch:
        components_conn.executemany(insert_sql, batch)
    if total_symbols:
        sys.stdout.write("\n")
        sys.stdout.flush()
    update_sql = """
        UPDATE components
        SET footprint = (
            SELECT footprint
            FROM temp_mpn_footprints
            WHERE temp_mpn_footprints.mpn = components.mfr
        )
        WHERE EXISTS (
            SELECT 1
            FROM temp_mpn_footprints
            WHERE temp_mpn_footprints.mpn = components.mfr
        )
    """
    cursor = components_conn.execute(update_sql)
    stats["components_updated"] = cursor.rowcount if cursor.rowcount is not None else 0
    components_conn.commit()
    return stats


def main() -> int:
    args = parse_args()

    components_db = args.components_db.expanduser()
    if not components_db.is_absolute():
        components_db = (PROJECT_ROOT / components_db).resolve()
    symbol_db = args.symbol_db.expanduser()
    if not symbol_db.is_absolute():
        symbol_db = (PROJECT_ROOT / symbol_db).resolve()

    if not components_db.exists():
        raise FileNotFoundError(f"Components database not found: {components_db}")
    if not symbol_db.exists():
        raise FileNotFoundError(f"Symbol database not found: {symbol_db}")

    effective_components_db = resolve_write_db_path(
        components_db,
        prefix="component",
    )
    effective_symbol_db = resolve_read_db_path(symbol_db, prefix="symbol")

    if effective_components_db != components_db:
        print(f"Using working copy for updates (original is protected): {effective_components_db}")

    components_conn = connect_sqlite(effective_components_db)
    symbol_conn = connect_sqlite(effective_symbol_db, readonly=True)
    try:
        stats = backfill_footprints(components_conn, symbol_conn, args.batch_size)
    finally:
        symbol_conn.close()
        components_conn.close()

    print(
        f"Symbols processed: {stats['symbols_seen']}, "
        f"footprints found: {stats['footprints_found']}, "
        f"components updated: {stats['components_updated']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
