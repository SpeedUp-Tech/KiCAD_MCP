#!/usr/bin/env python3
"""
Create a compact SQLite cache from a directory of EasyEDA JSON files.

Each JSON file is presumed to contain a full EasyEDA response for a single
LCSC part (as produced by scripts/cache_easyeda_json.py). This tool ingests all
`C*.json` files under the provided directory and stores them in a SQLite
database so downstream tooling can look up symbol/footprint data directly from
the database without scanning the filesystem.
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import time
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PYTHON_ROOT = PROJECT_ROOT / "python"
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from kicad_catalog.sqlite import connect_sqlite
from kicad_catalog.workdir import resolve_write_db_path

LCSC_SUFFIX = ".json"

SCHEMA = """
CREATE TABLE IF NOT EXISTS easyeda_cache (
    lcsc TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    cached_at INTEGER NOT NULL
);
"""


def discover_json_files(root: Path) -> List[Path]:
    files = sorted(p for p in root.glob("C*.json") if p.is_file())
    return files


def read_payload(path: Path) -> Tuple[str, str]:
    lcsc = path.stem.upper()
    raw = path.read_text(encoding="utf-8")

    # Optional sanity check: ensure the JSON has matching lcsc number.
    try:
        data = json.loads(raw)
        number = (
            data.get("result", {})
            .get("lcsc", {})
            .get("number", "")
        )
        if number and number.upper() != lcsc:
            logging.warning(
                "File %s reports lcsc %s; storing with filename-derived key %s",
                path,
                number,
                lcsc,
            )
    except Exception as exc:  # noqa: BLE001
        logging.debug("Skipping JSON validation for %s (%s)", path, exc)

    return lcsc, raw


def ensure_schema(db_path: Path) -> sqlite3.Connection:
    conn = connect_sqlite(
        db_path,
        pragmas={
            "journal_mode": "WAL",
            "synchronous": "NORMAL",
        },
    )
    conn.execute(SCHEMA)
    return conn


def insert_payloads(
    conn: sqlite3.Connection,
    entries: Iterable[Tuple[str, str]],
    total: int,
    chunk_size: int = 500,
) -> None:
    cursor = conn.cursor()
    now = int(time.time())
    batch: List[Tuple[str, str, int]] = []
    processed = 0

    for lcsc, raw in entries:
        batch.append((lcsc, raw, now))
        if len(batch) >= chunk_size:
            cursor.executemany(
                "INSERT OR REPLACE INTO easyeda_cache (lcsc, payload, cached_at) VALUES (?, ?, ?)",
                batch,
            )
            conn.commit()
            processed += len(batch)
            percent = (processed / total) * 100 if total else 100.0
            logging.info("Inserted %d/%d records (%.1f%%)...", processed, total, percent)
            batch.clear()

    if batch:
        cursor.executemany(
            "INSERT OR REPLACE INTO easyeda_cache (lcsc, payload, cached_at) VALUES (?, ?, ?)",
            batch,
        )
        conn.commit()
        processed += len(batch)
        percent = (processed / total) * 100 if total else 100.0
        logging.info("Inserted %d/%d records (final batch, %.1f%%).", processed, total, percent)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Load EasyEDA JSON files into a SQLite cache.",
    )
    parser.add_argument(
        "--input",
        required=True,
        type=Path,
        help="Directory containing cached EasyEDA JSON files (named Cxxxx.json).",
    )
    parser.add_argument(
        "--db",
        required=True,
        type=Path,
        help="Path to the output SQLite database (will be created if missing).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices={"DEBUG", "INFO", "WARNING", "ERROR"},
        help="Verbosity for console logging.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(levelname)s: %(message)s",
    )

    if not args.input.exists():
        logging.error("Input directory %s does not exist.", args.input)
        return 1

    files = discover_json_files(args.input)
    if not files:
        logging.error("No C*.json files found in %s", args.input)
        return 1

    logging.info("Found %d JSON files under %s", len(files), args.input)
    db_path = resolve_write_db_path(args.db, prefix="cache", repo_root=PROJECT_ROOT)
    if db_path != args.db:
        logging.warning(
            "Output DB path is protected; writing to working copy instead: %s",
            db_path,
        )
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = ensure_schema(db_path)

    try:
        entries = (read_payload(path) for path in files)
        insert_payloads(conn, entries, total=len(files))
    finally:
        conn.close()
    logging.info("All records inserted into %s", db_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
