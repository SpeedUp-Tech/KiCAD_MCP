#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Any, Callable, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
PYTHON_ROOT = REPO_ROOT / "python"
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from db_tools.postgres import connect_postgres
from db_tools.settings import SettingsError, get_settings, resolve_sqlite_path

RowTransform = Callable[[Tuple[Any, ...]], Tuple[Any, ...]]


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Copy a unified catalog from SQLite into Postgres (direct DB connection; no HTTP layer).\n\n"
            "This reads from a SQLite file and writes into a Postgres database using psycopg (v3).\n"
            "It never modifies the SQLite source DB in-place."
        )
    )
    parser.add_argument(
        "--source-sqlite",
        type=str,
        default=None,
        help=(
            "Source SQLite DB path or sqlite:// URL. "
            "If omitted, uses db_tools/settings.json when it points to sqlite."
        ),
    )
    parser.add_argument(
        "--dest-postgres",
        type=str,
        default=None,
        help=(
            "Destination Postgres DSN (postgresql://...). "
            "If omitted, uses db_tools/settings.json when it points to postgres."
        ),
    )
    parser.add_argument(
        "--drop-existing",
        action="store_true",
        help="Drop existing catalog tables/views before copying.",
    )
    parser.add_argument(
        "--skip-view",
        action="store_true",
        help="Do not create the v_components_search compatibility view in Postgres.",
    )
    parser.add_argument(
        "--analyze",
        action="store_true",
        help="Run ANALYZE after copying (recommended).",
    )
    return parser.parse_args(argv)


def _resolve_source_sqlite(arg: Optional[str]) -> Path:
    if arg:
        raw = arg.strip()
        if raw.lower().startswith("sqlite:"):
            return resolve_sqlite_path(raw)
        return Path(raw).expanduser().resolve()

    settings = get_settings()
    scheme = settings.db_url.split(":", 1)[0].strip().lower()
    if scheme != "sqlite":
        raise SettingsError(
            "No --source-sqlite provided and db_tools/settings.json is not sqlite://..."
        )
    return resolve_sqlite_path(settings.db_url)


def _resolve_dest_postgres(arg: Optional[str]) -> str:
    if arg:
        return arg.strip()

    settings = get_settings()
    scheme = settings.db_url.split(":", 1)[0].strip().lower()
    if scheme not in {"postgres", "postgresql"}:
        raise SettingsError(
            "No --dest-postgres provided and db_tools/settings.json is not postgresql://..."
        )
    return settings.db_url


def _open_sqlite_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = None
    return conn


def _drop_existing(cur: Any) -> None:
    cur.execute("DROP VIEW IF EXISTS v_components_search CASCADE")
    cur.execute("DROP TABLE IF EXISTS footprint_index CASCADE")
    cur.execute("DROP TABLE IF EXISTS symbol_index CASCADE")
    cur.execute("DROP TABLE IF EXISTS part_models CASCADE")
    cur.execute("DROP TABLE IF EXISTS mpn_model_map CASCADE")
    cur.execute("DROP TABLE IF EXISTS components CASCADE")
    cur.execute("DROP TABLE IF EXISTS categories CASCADE")


def _create_tables(cur: Any) -> None:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY,
            category TEXT NOT NULL,
            subcategory TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS components (
            lcsc BIGINT PRIMARY KEY,
            category_id INTEGER NOT NULL REFERENCES categories(id),
            mfr TEXT NOT NULL,
            package TEXT NOT NULL,
            joints INTEGER NOT NULL,
            manufacturer_id INTEGER NOT NULL,
            basic INTEGER NOT NULL,
            description TEXT NOT NULL,
            datasheet TEXT NOT NULL,
            stock INTEGER NOT NULL,
            price TEXT NOT NULL,
            last_update BIGINT NOT NULL,
            extra JSONB,
            flag INTEGER NOT NULL DEFAULT 0,
            last_on_stock INTEGER NOT NULL DEFAULT 0,
            preferred INTEGER NOT NULL DEFAULT 0,
            symbol_lib INTEGER NOT NULL DEFAULT 0,
            spice_model INTEGER NOT NULL DEFAULT 0,
            footprint TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS symbol_index (
            mpn TEXT NOT NULL,
            library TEXT NOT NULL,
            sexp TEXT NOT NULL,
            PRIMARY KEY (mpn, library)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS footprint_index (
            name TEXT NOT NULL,
            library TEXT NOT NULL,
            sexp TEXT NOT NULL,
            PRIMARY KEY (name, library)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS part_models (
            name TEXT NOT NULL,
            library TEXT NOT NULL,
            model_content TEXT NOT NULL,
            vendor_provided BOOLEAN NOT NULL DEFAULT FALSE,
            PRIMARY KEY (name, library)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS mpn_model_map (
            lcsc INTEGER,
            mpn TEXT,
            model_name TEXT,
            match_type TEXT,
            match_detail TEXT,
            source_token TEXT,
            sanitized_token TEXT,
            match_priority INTEGER,
            variant_priority INTEGER
        )
        """
    )


def _create_indexes(cur: Any) -> None:
    cur.execute("CREATE INDEX IF NOT EXISTS idx_mpn_model_map_mpn ON mpn_model_map(mpn)")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_mpn_model_map_model ON mpn_model_map(model_name)"
    )


def _create_view(cur: Any) -> None:
    cur.execute("DROP VIEW IF EXISTS v_components_search")
    cur.execute(
        """
        CREATE VIEW v_components_search AS
        SELECT
            c.lcsc AS lcsc,
            c.mfr AS mpn,
            c.joints AS joints,
            c.package AS package,
            COALESCE(
                (c.extra #>> '{datasheet,pdf}'),
                c.datasheet
            ) AS datasheet,
            (c.extra -> 'attributes') AS attributes,
            cat.category AS family,
            REPLACE(REPLACE(cat.category, '/', '_'), ' ', '_') AS library,
            cat.subcategory AS class,
            CASE
                WHEN (c.extra -> 'attributes') IS NOT NULL
                THEN (c.extra -> 'attributes')
                ELSE NULL
            END AS specs,
            c.symbol_lib AS symbol_lib,
            map.model_name AS spice_model
        FROM components c
        LEFT JOIN categories cat ON c.category_id = cat.id
        LEFT JOIN mpn_model_map map ON map.mpn = c.mfr
        """
    )


def _copy_table(
    *,
    sqlite_conn: sqlite3.Connection,
    pg_conn: Any,
    table: str,
    columns: Sequence[str],
    select_sql: str,
    transform: Optional[RowTransform] = None,
) -> int:
    count = 0
    copy_sql = f"COPY {table} ({', '.join(columns)}) FROM STDIN"
    with pg_conn.cursor() as cur:
        with cur.copy(copy_sql) as copy:
            for row in sqlite_conn.execute(select_sql):
                values = tuple(row)
                if transform is not None:
                    values = transform(values)
                copy.write_row(values)
                count += 1
    return count


def _normalize_extra_row(row: Tuple[Any, ...]) -> Tuple[Any, ...]:
    extra = row[12]
    if extra is None:
        extra_out = None
    else:
        extra_text = str(extra).strip()
        extra_out = extra_text or None
    return (*row[:12], extra_out, *row[13:])


def _normalize_vendor_provided(row: Tuple[Any, ...]) -> Tuple[Any, ...]:
    vendor = row[3]
    return (row[0], row[1], row[2], bool(vendor))


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)

    source_sqlite = _resolve_source_sqlite(args.source_sqlite)
    if not source_sqlite.exists():
        raise SettingsError(f"SQLite source DB not found: {source_sqlite}")

    dest_postgres = _resolve_dest_postgres(args.dest_postgres)

    sqlite_conn = _open_sqlite_ro(source_sqlite)
    try:
        with connect_postgres(dest_postgres, readonly=False, autocommit=False) as pg_conn:
            with pg_conn.cursor() as cur:
                if args.drop_existing:
                    _drop_existing(cur)
                _create_tables(cur)
            pg_conn.commit()

            _copy_table(
                sqlite_conn=sqlite_conn,
                pg_conn=pg_conn,
                table="categories",
                columns=["id", "category", "subcategory"],
                select_sql="SELECT id, category, subcategory FROM categories",
            )
            pg_conn.commit()

            _copy_table(
                sqlite_conn=sqlite_conn,
                pg_conn=pg_conn,
                table="components",
                columns=[
                    "lcsc",
                    "category_id",
                    "mfr",
                    "package",
                    "joints",
                    "manufacturer_id",
                    "basic",
                    "description",
                    "datasheet",
                    "stock",
                    "price",
                    "last_update",
                    "extra",
                    "flag",
                    "last_on_stock",
                    "preferred",
                    "symbol_lib",
                    "spice_model",
                    "footprint",
                ],
                select_sql=(
                    "SELECT lcsc, category_id, mfr, package, joints, manufacturer_id, basic, "
                    "description, datasheet, stock, price, last_update, extra, flag, "
                    "last_on_stock, preferred, symbol_lib, spice_model, footprint "
                    "FROM components"
                ),
                transform=_normalize_extra_row,
            )
            pg_conn.commit()

            _copy_table(
                sqlite_conn=sqlite_conn,
                pg_conn=pg_conn,
                table="mpn_model_map",
                columns=[
                    "lcsc",
                    "mpn",
                    "model_name",
                    "match_type",
                    "match_detail",
                    "source_token",
                    "sanitized_token",
                    "match_priority",
                    "variant_priority",
                ],
                select_sql=(
                    "SELECT lcsc, mpn, model_name, match_type, match_detail, source_token, "
                    "sanitized_token, match_priority, variant_priority "
                    "FROM mpn_model_map"
                ),
            )
            pg_conn.commit()

            _copy_table(
                sqlite_conn=sqlite_conn,
                pg_conn=pg_conn,
                table="symbol_index",
                columns=["mpn", "library", "sexp"],
                select_sql="SELECT mpn, library, sexp FROM symbol_index",
            )
            pg_conn.commit()

            _copy_table(
                sqlite_conn=sqlite_conn,
                pg_conn=pg_conn,
                table="footprint_index",
                columns=["name", "library", "sexp"],
                select_sql="SELECT name, library, sexp FROM footprint_index",
            )
            pg_conn.commit()

            _copy_table(
                sqlite_conn=sqlite_conn,
                pg_conn=pg_conn,
                table="part_models",
                columns=["name", "library", "model_content", "vendor_provided"],
                select_sql="SELECT name, library, model_content, vendor_provided FROM part_models",
                transform=_normalize_vendor_provided,
            )
            pg_conn.commit()

            with pg_conn.cursor() as cur:
                _create_indexes(cur)
                if not args.skip_view:
                    _create_view(cur)
                if args.analyze:
                    cur.execute("ANALYZE")
            pg_conn.commit()

    finally:
        sqlite_conn.close()

    print("✓ SQLite → Postgres migration completed.")
    print(f"  source: {source_sqlite}")
    print("  dest:   postgresql://… (see --dest-postgres)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

