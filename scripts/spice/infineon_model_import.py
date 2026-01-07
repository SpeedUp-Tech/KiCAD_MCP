#!/usr/bin/env python3
"""Import Infineon LTspice NMOS/PMOS models into the shared SQLite catalog.

The repository `spice_lib/infineon/LTspiceInfineonNMOSLibrary-master` contains
many `.lib` files grouped under `sub/` (plus `sub/pmos`). Each `.lib` file
defines multiple `.SUBCKT` entries – this script extracts every unique
subcircuit definition (optionally keeping the leading comments) and writes the
complete block into the `spice_models` table (same schema used by other import
scripts).

Usage:
    python scripts/spice/infineon_model_import.py --db part_lib/spice_model.sqlite3
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PYTHON_ROOT = PROJECT_ROOT / "python"
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from db_tools.sqlite import connect_sqlite
from db_tools.workdir import resolve_write_db_path


DEFAULT_ROOT = Path("spice_lib/infineon/LTspiceInfineonNMOSLibrary-master")
DEFAULT_SUBDIR = DEFAULT_ROOT / "sub"


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Ensure the destination table/index exists."""
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
    start = index
    while start > 0:
        prev = lines[start - 1]
        stripped = prev.strip()
        if stripped.startswith("*"):
            start -= 1
            continue
        if stripped == "":
            start -= 1
            continue
        break
    return start


def extract_subckts(text: str) -> Tuple[List[Tuple[str, str]], Counter]:
    lines = text.splitlines()
    length = len(lines)
    records = {}
    duplicates = Counter()
    i = 0
    while i < length:
        stripped = lines[i].strip()
        upper = stripped.upper()
        if upper.startswith(".SUBCKT"):
            tokens = lines[i].split()
            if len(tokens) < 2:
                i += 1
                continue
            name = tokens[1]
            start = detect_block_start(lines, i)
            search = i + 1
            end_line = None
            while search < length:
                next_line = lines[search].strip()
                next_upper = next_line.upper()
                if next_upper.startswith(".SUBCKT"):
                    break
                if next_upper.startswith(".ENDS"):
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
    ordered = sorted(records.items(), key=lambda x: x[0])
    return ordered, duplicates


def gather_library_blocks(root: Path) -> Tuple[List[Tuple[str, str, str]], Counter]:
    lib_files = sorted(root.rglob("*.lib"))
    all_blocks: List[Tuple[str, str, str]] = []
    duplicate_counter = Counter()
    for lib_path in lib_files:
        text = lib_path.read_text(encoding="utf-8", errors="ignore")
        blocks, duplicates = extract_subckts(text)
        duplicate_counter.update(duplicates)
        rel_path = lib_path.relative_to(root.parent)
        lib_name = lib_path.stem
        for mpn, raw in blocks:
            all_blocks.append((mpn, lib_name, str(rel_path), raw))
    return all_blocks, duplicate_counter


def store_blocks(
    conn: sqlite3.Connection,
    blocks: Iterable[Tuple[str, str, str, str]],
    mpn_source: str = "infineon_subckt",
) -> int:
    cursor = conn.cursor()
    rows = [
        (
            mpn,
            lib_name,
            source_path,
            raw,
            None,
            None,
            mpn_source,
        )
        for mpn, lib_name, source_path, raw in blocks
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
        description="Import Infineon LTspice NMOS/PMOS libraries into SQLite."
    )
    parser.add_argument(
        "--db",
        default="infineon_spice_models.sqlite3",
        help="Destination SQLite database.",
    )
    parser.add_argument(
        "--root",
        default=str(DEFAULT_SUBDIR),
        help="Directory containing Infineon `.lib` files (defaults to `sub/`).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    root = Path(args.root)
    if not root.exists():
        parser.error(f"Library directory not found: {root}")

    blocks, duplicates = gather_library_blocks(root)
    if not blocks:
        print("No subcircuits found in the provided directory.", file=sys.stderr)
        return 1

    db_path = Path(args.db)
    safe_db_path = resolve_write_db_path(db_path, prefix="spice")
    if safe_db_path != db_path:
        print(
            f"Destination DB is protected; writing to working copy instead: {safe_db_path}",
            file=sys.stderr,
        )
    db_path = safe_db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = connect_sqlite(db_path)
    ensure_schema(conn)
    inserted = store_blocks(conn, blocks)

    unique_mpns = {mpn for mpn, _, _, _ in blocks}
    print(
        f"Imported {len(unique_mpns)} unique Infineon models "
        f"(from {inserted} .SUBCKT definitions) into {db_path}",
        file=sys.stderr,
    )
    if duplicates:
        top = duplicates.most_common(10)
        summary = ", ".join(f"{name}×{count+1}" for name, count in top)
        extra = (
            ""
            if len(duplicates) <= 10
            else f", plus {len(duplicates) - 10} more duplicates"
        )
        print(f"Duplicate definitions encountered: {summary}{extra}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
