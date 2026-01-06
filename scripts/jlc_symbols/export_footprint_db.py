#!/usr/bin/env python3
"""
Build an incremental SQLite index of KiCad footprints grouped by library.

The script scans one or more directories for `.pretty` folders, ingests every
`.kicad_mod` footprint it finds, and stores the raw S-expression in a SQLite
database. This allows downstream tooling to look up footprints by library/name
without re-reading the individual files each time.
"""

from __future__ import annotations

import argparse
import logging
import re
import sqlite3
import sys
from pathlib import Path
from typing import List, Sequence, Tuple

LOGGER = logging.getLogger("export_footprint_db")
PROJECT_ROOT = Path(__file__).resolve().parents[2]
PYTHON_ROOT = PROJECT_ROOT / "python"
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from kicad_catalog.workdir import resolve_write_db_path
from kicad_catalog.sqlite import connect_sqlite

_FOOTPRINT_HEADER_REGEX = re.compile(
    r'\((?:footprint|module)\s+(?:"([^"]+)"|([^\s()]+))',
    re.IGNORECASE,
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Index KiCad footprints into a SQLite database."
    )
    parser.add_argument(
        "--footprints-dir",
        action="append",
        dest="footprint_dirs",
        required=True,
        help="Root directory containing .pretty folders (can be repeated).",
    )
    parser.add_argument(
        "dest",
        type=Path,
        help="Destination SQLite database path.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress progress logs.",
    )
    return parser.parse_args(argv)


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS footprint_index (
            name TEXT NOT NULL,
            library TEXT NOT NULL,
            sexp TEXT NOT NULL,
            PRIMARY KEY (name, library)
        )
        """
    )
    conn.execute("PRAGMA journal_mode=WAL;")


def _extract_footprint_name(text: str, source: Path) -> str | None:
    match = _FOOTPRINT_HEADER_REGEX.search(text)
    if match:
        quoted, bare = match.groups()
        name = quoted or bare
        if name:
            cleaned = name.strip()
            if cleaned.startswith("easyeda2kicad:"):
                cleaned = cleaned.split("easyeda2kicad:", 1)[1].strip()
            return cleaned
    LOGGER.warning("Unable to determine footprint name in %s", source)
    return None


def process_footprint_file(
    path: Path,
    conn: sqlite3.Connection,
) -> Tuple[int, int]:
    """
    Insert a single .kicad_mod footprint into the database.

    Returns:
        (inserted, skipped_existing)
    """
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = path.read_text(encoding="utf-8", errors="ignore")

    library = path.parent.stem
    footprint_name = _extract_footprint_name(text, path)
    if not footprint_name:
        return 0, 0

    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO footprint_index (name, library, sexp)
        VALUES (?, ?, ?)
        """,
        (footprint_name, library, text.strip()),
    )
    if cursor.rowcount == 1:
        return 1, 0
    return 0, 1


def _gather_footprint_files(roots: Sequence[Path]) -> List[Path]:
    files: List[Path] = []
    for root in roots:
        if not root.is_dir():
            LOGGER.warning("Footprints directory not found: %s", root)
            continue
        for pretty_dir in sorted(root.rglob("*.pretty")):
            footprint_files = sorted(pretty_dir.glob("*.kicad_mod"))
            for fp in footprint_files:
                files.append(fp)
    return files


def _print_progress(done: int, total: int) -> None:
    if total == 0:
        summary = "0/0 (  0.0%)"
    else:
        percent = (done / total) * 100
        summary = f"{done}/{total} ({percent:5.1f}%)"
    print(f"\rFootprints: {summary}", end="", flush=True)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.ERROR if args.quiet else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    roots = [Path(p).resolve() for p in args.footprint_dirs]
    dest = args.dest.resolve()
    safe_dest = resolve_write_db_path(dest, prefix="footprint", repo_root=PROJECT_ROOT)
    if safe_dest != dest:
        print(f"Destination DB is protected; writing to working copy instead: {safe_dest}")
    dest = safe_dest
    dest.parent.mkdir(parents=True, exist_ok=True)

    footprint_files = _gather_footprint_files(roots)
    total_files = len(footprint_files)
    LOGGER.info("Found %d footprints to process.", total_files)

    conn = connect_sqlite(dest)
    try:
        ensure_schema(conn)
        inserted = 0
        skipped = 0
        for index, fp_path in enumerate(footprint_files, start=1):
            add, exist = process_footprint_file(fp_path, conn)
            inserted += add
            skipped += exist
            if not args.quiet:
                _print_progress(index, total_files)
        conn.commit()
        if not args.quiet and total_files:
            print()  # newline after progress
        LOGGER.info(
            "Done. inserted=%d, skipped_existing=%d",
            inserted,
            skipped,
        )
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
