#!/usr/bin/env python3
"""Extract and store Diodes Inc. SPICE models into the shared SQLite catalog.

This utility parses the monolithic `diodes-spice-models.txt` file, slices each
`.SUBCKT ... .ENDS` block, and inserts the include-ready text into the
`spice_models` table (same schema produced by `ti_model_extraction.py`).

Usage (default paths match the KiCAD_MCP repository layout):

    python scripts/spice/diodes_model_import.py --db part_lib/spice_model.sqlite3
"""

from __future__ import annotations

import argparse
import sqlite3
from collections import Counter
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PYTHON_ROOT = PROJECT_ROOT / "python"
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from kicad_catalog.sqlite import connect_sqlite
from kicad_catalog.workdir import resolve_write_db_path


DEFAULT_SOURCE = Path("spice_lib/diodes/diodes-spice-models.txt")


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the expected table/index if they do not already exist."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS spice_models (
            mpn TEXT NOT NULL,
            lib_name TEXT NOT NULL,
            source_path TEXT NOT NULL,
            raw_content TEXT NOT NULL,
            metadata_symbol TEXT,
            metadata_part_name TEXT,
            mpn_source TEXT NOT NULL,
            PRIMARY KEY (mpn, source_path)
        );
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_spice_models_mpn ON spice_models(mpn)"
    )
    conn.commit()


def detect_block_start(lines: Sequence[str], index: int) -> int:
    """Include contiguous comment lines directly above the subcircuit."""
    start = index
    while start > 0:
        prev = lines[start - 1]
        if prev.strip().startswith("*"):
            start -= 1
            continue
        if prev.strip() == "":
            # include the blank separator, but stop afterwards
            start -= 1
        break
    return start


def extract_blocks(text: str) -> Tuple[List[Tuple[str, str]], Counter]:
    """Return list of (mpn, raw_block) and stats about duplicates."""
    lines = text.splitlines()
    records: Dict[str, str] = {}
    duplicates = Counter()
    i = 0
    length = len(lines)
    while i < length:
        stripped = lines[i].strip()
        upper = stripped.upper()
        if upper.startswith(".SUBCKT"):
            parts = lines[i].split()
            if len(parts) < 2:
                i += 1
                continue
            name = parts[1]
            start = detect_block_start(lines, i)
            search = i + 1
            end_line: Optional[int] = None
            while search < length:
                nxt = lines[search].strip()
                upper_next = nxt.upper()
                if upper_next.startswith(".SUBCKT"):
                    break
                if upper_next.startswith(".ENDS"):
                    end_line = search
                    search += 1
                    break
                search += 1
            if end_line is None:
                end_line = search - 1
            while end_line + 1 < length and lines[end_line + 1].strip() == "":
                end_line += 1
            block = "\n".join(lines[start : end_line + 1]).rstrip() + "\n"
            if name in records:
                duplicates[name] += 1
            else:
                records[name] = block
            i = search if search > i else i + 1
            continue
        i += 1
    ordered_records = sorted(records.items(), key=lambda item: item[0])
    return ordered_records, duplicates


def store_blocks(
    conn: sqlite3.Connection, blocks: Iterable[Tuple[str, str]], source_rel: str
) -> int:
    cursor = conn.cursor()
    rows = [
        (
            mpn,
            "diodes-spice-models",
            source_rel,
            raw,
            None,
            None,
            "diodes_subckt",
        )
        for mpn, raw in blocks
    ]
    cursor.executemany(
        """
        INSERT OR REPLACE INTO spice_models
            (mpn, lib_name, source_path, raw_content,
             metadata_symbol, metadata_part_name, mpn_source)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()
    return len(rows)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Import Diodes Inc. SPICE models into the shared SQLite catalog."
    )
    parser.add_argument(
        "--db",
        default="ti_spice_models.sqlite3",
        help="Destination SQLite database.",
    )
    parser.add_argument(
        "--source",
        default=str(DEFAULT_SOURCE),
        help="Path to diodes-spice-models.txt file.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    source_path = Path(args.source)
    if not source_path.exists():
        parser.error(f"Source file not found: {source_path}")

    db_path = Path(args.db)
    safe_db_path = resolve_write_db_path(db_path, prefix="spice", repo_root=PROJECT_ROOT)
    if safe_db_path != db_path:
        print(
            f"Destination DB is protected; writing to working copy instead: {safe_db_path}",
            file=sys.stderr,
        )
    db_path = safe_db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)

    text = source_path.read_text(encoding="utf-8", errors="ignore")
    blocks, duplicate_stats = extract_blocks(text)

    conn = connect_sqlite(db_path)
    ensure_schema(conn)
    inserted = store_blocks(conn, blocks, str(source_path.name))

    unique_mpns = len(blocks)
    total_models = unique_mpns + sum(duplicate_stats.values())
    print(
        f"Imported {unique_mpns} unique Diodes Inc. models "
        f"(from {total_models} .SUBCKT definitions) into {db_path}",
        file=sys.stderr,
    )
    if duplicate_stats:
        top = duplicate_stats.most_common(10)
        summary = ", ".join(f"{name}×{count+1}" for name, count in top)
        extra = (
            ""
            if len(duplicate_stats) <= 10
            else f", plus {len(duplicate_stats) - 10} more duplicates"
        )
        print(f"Duplicate definitions encountered: {summary}{extra}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
