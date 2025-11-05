#!/usr/bin/env python3
"""
Update component search metadata with symbol/SPICE availability flags.

This script reads two newline-separated lists of MPNs (one for KiCad symbols,
one for SPICE models) and updates `jlcpcb-components.sqlite3` so that the
`components` table exposes `symbol_lib` and `spice_model` boolean columns.
It also rebuilds the `v_components_search` view to surface the flags without
adding runtime JOINs.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import List, Sequence, Tuple

DEFAULT_DB = Path("part_lib/jlcpcb-components.sqlite3")

NEW_VIEW_SQL = """
CREATE VIEW v_components_search AS
    SELECT
        c.lcsc AS lcsc,
        c.mfr AS mpn,
        c.joints AS joints,
        c.package AS package,
        COALESCE(
            json_extract(c.extra, '$.datasheet.pdf'),
            c.datasheet
        ) AS datasheet,
        json_extract(c.extra, '$.attributes') AS attributes,
        cat.category AS family,
        cat.subcategory AS class,
        CASE
            WHEN json_extract(c.extra, '$.attributes') IS NOT NULL
            THEN json_extract(c.extra, '$.attributes')
            ELSE NULL
        END AS specs,
        c.symbol_lib AS symbol_lib,
        c.spice_model AS spice_model
    FROM components c
    LEFT JOIN categories cat ON c.category_id = cat.id;
""".strip()


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Flag components that have KiCad symbols and SPICE models."
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB,
        help=f"Path to the SQLite database (default: {DEFAULT_DB})",
    )
    parser.add_argument(
        "--symbol-list",
        type=Path,
        required=True,
        help="Plain-text file with one MPN per line for available KiCad symbols.",
    )
    parser.add_argument(
        "--spice-list",
        type=Path,
        required=True,
        help="Plain-text file with one MPN per line for available SPICE models.",
    )
    parser.add_argument(
        "--normalize-lowercase",
        action="store_true",
        help="Lowercase all MPNs before matching (default: preserve input).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Execute the workflow but roll back at the end (no data changes).",
    )
    return parser.parse_args(argv)


def load_mpn_list(path: Path, to_lower: bool) -> List[str]:
    if not path.is_file():
        raise FileNotFoundError(f"MPN list not found: {path}")

    values: List[str] = []
    with path.open("r", encoding="utf-8") as handle:
        for raw in handle:
            mpn = raw.strip()
            if not mpn or mpn.startswith("#"):
                continue
            values.append(mpn.lower() if to_lower else mpn)

    # Deduplicate while keeping first-seen order
    seen = set()
    ordered: List[str] = []
    for mpn in values:
        if mpn in seen:
            continue
        seen.add(mpn)
        ordered.append(mpn)
    return ordered


def ensure_component_columns(conn: sqlite3.Connection) -> None:
    cur = conn.execute("PRAGMA table_info(components);")
    existing = {row[1] for row in cur.fetchall()}

    if "symbol_lib" not in existing:
        conn.execute(
            "ALTER TABLE components ADD COLUMN symbol_lib INTEGER NOT NULL DEFAULT 0"
        )
    if "spice_model" not in existing:
        conn.execute(
            "ALTER TABLE components ADD COLUMN spice_model INTEGER NOT NULL DEFAULT 0"
        )

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_components_mfr ON components(mfr)"
    )


def populate_temp_table(
    conn: sqlite3.Connection, table_name: str, mpns: Sequence[str]
) -> None:
    conn.execute(f"DROP TABLE IF EXISTS {table_name}")
    conn.execute(f"CREATE TEMP TABLE {table_name} (mpn TEXT PRIMARY KEY)")
    if mpns:
        conn.executemany(
            f"INSERT INTO {table_name} (mpn) VALUES (?)",
            ((mpn,) for mpn in mpns),
        )


def rebuild_view(conn: sqlite3.Connection) -> None:
    conn.execute("DROP VIEW IF EXISTS v_components_search")
    conn.execute(NEW_VIEW_SQL)


def summarize_missing(
    conn: sqlite3.Connection, temp_table: str, limit: int = 5
) -> Tuple[int, int, List[str]]:
    total = conn.execute(
        f"SELECT COUNT(*) FROM {temp_table}"
    ).fetchone()[0]

    count = conn.execute(
        f"""
        SELECT COUNT(*)
        FROM {temp_table} t
        WHERE EXISTS (
            SELECT 1
            FROM components c
            WHERE c.mfr = t.mpn
        )
        """
    ).fetchone()[0]

    missing_rows = conn.execute(
        f"""
        SELECT t.mpn
        FROM {temp_table} t
        WHERE NOT EXISTS (
            SELECT 1
            FROM components c
            WHERE c.mfr = t.mpn
        )
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    missing = [row[0] for row in missing_rows]
    missing_total = max(total - count, 0)
    return count, missing_total, missing


def apply_flags(
    conn: sqlite3.Connection,
    symbol_mpns: Sequence[str],
    spice_mpns: Sequence[str],
    dry_run: bool,
) -> dict:
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("BEGIN IMMEDIATE")
    summary = {}

    try:
        populate_temp_table(conn, "tmp_symbol_index", symbol_mpns)
        populate_temp_table(conn, "tmp_spice_index", spice_mpns)

        conn.execute("UPDATE components SET symbol_lib = 0")
        conn.execute("UPDATE components SET spice_model = 0")

        if symbol_mpns:
            conn.execute(
                """
                UPDATE components
                SET symbol_lib = 1
                WHERE mfr IN (SELECT mpn FROM tmp_symbol_index)
                """
            )
        if spice_mpns:
            conn.execute(
                """
                UPDATE components
                SET spice_model = 1
                WHERE mfr IN (SELECT mpn FROM tmp_spice_index)
                """
            )

        rebuild_view(conn)

        symbol_hits, symbol_missing_total, symbol_missing = summarize_missing(
            conn, "tmp_symbol_index"
        )
        spice_hits, spice_missing_total, spice_missing = summarize_missing(
            conn, "tmp_spice_index"
        )

        total_symbol_flagged = conn.execute(
            "SELECT COUNT(*) FROM components WHERE symbol_lib = 1"
        ).fetchone()[0]
        total_spice_flagged = conn.execute(
            "SELECT COUNT(*) FROM components WHERE spice_model = 1"
        ).fetchone()[0]

        summary = {
            "symbol_hits": symbol_hits,
            "symbol_missing": symbol_missing,
            "symbol_missing_total": symbol_missing_total,
            "spice_hits": spice_hits,
            "spice_missing_total": spice_missing_total,
            "spice_missing": spice_missing,
            "total_symbol_flagged": total_symbol_flagged,
            "total_spice_flagged": total_spice_flagged,
        }

        if dry_run:
            conn.rollback()
        else:
            conn.commit()
    except Exception:
        conn.rollback()
        raise

    return summary


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)

    symbol_mpns = load_mpn_list(args.symbol_list, args.normalize_lowercase)
    spice_mpns = load_mpn_list(args.spice_list, args.normalize_lowercase)

    conn = sqlite3.connect(args.db)
    try:
        ensure_component_columns(conn)
        summary = apply_flags(conn, symbol_mpns, spice_mpns, args.dry_run)
    finally:
        conn.close()

    def _format_missing(missing: List[str]) -> str:
        if not missing:
            return "none"
        preview = ", ".join(missing[:5])
        if len(missing) > 5:
            preview += ", ..."
        return preview

    print(f"Symbol MPNs provided: {len(symbol_mpns)}")
    print(f"SPICE MPNs provided:  {len(spice_mpns)}")

    print(f"Symbol matches in DB: {summary['symbol_hits']}")
    print(
        f"Symbol MPNs missing in DB: {summary['symbol_missing_total']}"
    )
    print(
        f"Symbol flagged components total: {summary['total_symbol_flagged']}"
    )
    print(
        "Symbol MPNs missing in DB (sample): "
        f"{_format_missing(summary['symbol_missing'])}"
    )

    print(f"SPICE matches in DB: {summary['spice_hits']}")
    print(f"SPICE MPNs missing in DB: {summary['spice_missing_total']}")
    print(
        f"SPICE flagged components total: {summary['total_spice_flagged']}"
    )
    print(
        "SPICE MPNs missing in DB (sample): "
        f"{_format_missing(summary['spice_missing'])}"
    )

    if args.dry_run:
        print("Dry run complete. No database changes were persisted.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
