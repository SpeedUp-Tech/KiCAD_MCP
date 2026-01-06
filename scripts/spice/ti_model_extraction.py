#!/usr/bin/env python3
"""Create an MPN → full SPICE library catalogue for TI models.

The script scans the KiCAD_MCP `spice_lib/texas_instrument` directory,
validates the coverage of `metadata.json`, and stores the complete text of
every `.lib` file in a SQLite database so that each MPN can be restored as an
include-ready model.

Typical usage:

    python scripts/spice/ti_model_extraction.py --db part_lib/spice_model.sqlite3
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PYTHON_ROOT = PROJECT_ROOT / "python"
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from kicad_catalog.sqlite import connect_sqlite
from kicad_catalog.workdir import resolve_write_db_path


SOURCE_DEFAULT = Path("spice_lib/texas_instrument")
METADATA_DEFAULT = SOURCE_DEFAULT / "metadata.json"


@dataclass
class LibraryRecord:
    mpn: str
    lib_name: str
    source_path: str
    raw_content: str
    metadata_symbol: Optional[str]
    metadata_part_name: Optional[str]
    mpn_source: str


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Drop the previous table (if any) and create the new schema."""
    conn.execute("DROP TABLE IF EXISTS spice_models")
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


def load_metadata(path: Path) -> Dict[str, dict]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Failed to parse metadata JSON: {exc}") from exc
    result: Dict[str, dict] = {}
    for entry in data:
        symbol = entry.get("SymbolLibraryName")
        if symbol:
            result[symbol] = entry
    return result


PART_COMMENT_RE = re.compile(r"^\s*\*\s*Part:\s*(?P<part>.+?)\s*$", re.IGNORECASE)


def extract_part_from_comment(lines: Sequence[str]) -> Optional[str]:
    for line in lines[:200]:
        match = PART_COMMENT_RE.match(line)
        if match:
            return match.group("part").strip()
    return None


def extract_first_subckt(lines: Sequence[str]) -> Optional[str]:
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("*") or stripped == "":
            continue
        if stripped.upper().startswith(".SUBCKT"):
            tokens = line.split()
            if len(tokens) >= 2:
                return tokens[1]
            break
    return None


def determine_mpn(
    lib_stem: str,
    lines: Sequence[str],
    metadata_entry: Optional[dict],
) -> Tuple[str, str, Optional[str]]:
    """Return (mpn, mpn_source, metadata_part_name)."""
    if metadata_entry:
        part_name = metadata_entry.get("PartName")
        if part_name:
            return part_name.strip(), "metadata_part_name", part_name.strip()
        return metadata_entry.get("SymbolLibraryName", lib_stem), "metadata_symbol", part_name
    comment_part = extract_part_from_comment(lines)
    if comment_part:
        return comment_part, "part_comment", None
    subckt_name = extract_first_subckt(lines)
    if subckt_name:
        return subckt_name, "first_subckt", None
    return lib_stem, "file_stem", None


def gather_library_records(
    source_dir: Path, metadata: Dict[str, dict]
) -> Tuple[List[LibraryRecord], List[str], List[str], Counter]:
    library_paths = sorted(source_dir.glob("*.lib"))
    records: List[LibraryRecord] = []
    mpn_sources = Counter()
    metadata_symbols = set(metadata.keys())
    seen_symbols = set()

    for path in library_paths:
        lib_name = path.stem
        metadata_entry = metadata.get(lib_name)
        if metadata_entry:
            seen_symbols.add(lib_name)
        text = path.read_text(encoding="utf-8", errors="ignore")
        lines = text.splitlines()
        mpn, mpn_source, metadata_part_name = determine_mpn(
            lib_name, lines, metadata_entry
        )
        mpn_sources[mpn_source] += 1
        record = LibraryRecord(
            mpn=mpn,
            lib_name=lib_name,
            source_path=str(path.relative_to(source_dir)),
            raw_content=text,
            metadata_symbol=metadata_entry.get("SymbolLibraryName")
            if metadata_entry
            else None,
            metadata_part_name=metadata_part_name,
            mpn_source=mpn_source,
        )
        records.append(record)

    missing_files = sorted(metadata_symbols - seen_symbols)
    orphan_libs = sorted(set(path.stem for path in library_paths) - metadata_symbols)
    return records, missing_files, orphan_libs, mpn_sources


def store_records(conn: sqlite3.Connection, records: Iterable[LibraryRecord]) -> None:
    cursor = conn.cursor()
    cursor.executemany(
        """
        INSERT OR REPLACE INTO spice_models
            (mpn, lib_name, source_path, raw_content,
             metadata_symbol, metadata_part_name, mpn_source)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            (
                rec.mpn,
                rec.lib_name,
                rec.source_path,
                rec.raw_content,
                rec.metadata_symbol,
                rec.metadata_part_name,
                rec.mpn_source,
            )
            for rec in records
        ),
    )
    conn.commit()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Scan TI SPICE libraries and build an MPN → full-model SQLite database."
        )
    )
    parser.add_argument(
        "--db",
        default="ti_spice_models.sqlite3",
        help="Destination SQLite database (created if missing).",
    )
    parser.add_argument(
        "--source-dir",
        default=str(SOURCE_DEFAULT),
        help="Directory containing TI .lib files.",
    )
    parser.add_argument(
        "--metadata",
        default=str(METADATA_DEFAULT),
        help="Optional metadata JSON providing SymbolLibraryName → PartName mapping.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    source_dir = Path(args.source_dir)
    if not source_dir.exists():
        parser.error(f"Source directory not found: {source_dir}")

    metadata = load_metadata(Path(args.metadata))

    db_path = Path(args.db)
    safe_db_path = resolve_write_db_path(db_path, prefix="spice", repo_root=PROJECT_ROOT)
    if safe_db_path != db_path:
        print(
            f"Destination DB is protected; writing to working copy instead: {safe_db_path}",
            file=sys.stderr,
        )
    db_path = safe_db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect_sqlite(db_path)
    ensure_schema(conn)

    records, missing_files, orphan_libs, mpn_sources = gather_library_records(
        source_dir, metadata
    )
    if not records:
        print("No .lib files were found to import.", file=sys.stderr)
        return 1

    store_records(conn, records)

    unique_mpns = {rec.mpn for rec in records}
    print(
        f"Stored {len(records)} complete library files covering {len(unique_mpns)} unique MPNs into {db_path}",
        file=sys.stderr,
    )
    if metadata:
        print(
            f"Metadata entries: {len(metadata)} | matched files: {len(metadata) - len(missing_files)} | missing files: {len(missing_files)}",
            file=sys.stderr,
        )
        if missing_files:
            preview = ", ".join(missing_files[:10])
            extra = "" if len(missing_files) <= 10 else f", plus {len(missing_files) - 10} more"
            print(
                f"  Metadata referenced .lib files not found: {preview}{extra}",
                file=sys.stderr,
            )
    if orphan_libs:
        preview = ", ".join(orphan_libs[:10])
        extra = "" if len(orphan_libs) <= 10 else f", plus {len(orphan_libs) - 10} more"
        print(
            f"Library files without metadata: {len(orphan_libs)} (e.g. {preview}{extra})",
            file=sys.stderr,
        )
    if mpn_sources:
        breakdown = ", ".join(
            f"{source}:{count}" for source, count in mpn_sources.most_common()
        )
        print(f"MPN origin breakdown -> {breakdown}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
