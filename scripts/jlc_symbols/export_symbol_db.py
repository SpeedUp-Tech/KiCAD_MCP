#!/usr/bin/env python3
"""
Build an incremental SQLite index of KiCad symbols grouped by MPN.

The script scans one or more directories for `.kicad_sym` files,
extracts each symbol's S-expression together with its library (file stem)
and desired MPN property, and stores them in a SQLite database so future
lookups can be served without reparsing the source libraries.
"""

from __future__ import annotations

import argparse
import logging
import re
import sqlite3
from pathlib import Path
from typing import Dict, Iterator, List, Sequence

import sexpdata

LOGGER = logging.getLogger("export_symbol_db")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Index KiCad symbols into a SQLite database."
    )
    parser.add_argument(
        "--symbols-dir",
        action="append",
        dest="symbols_dirs",
        required=True,
        help="Directory containing .kicad_sym files (can be repeated).",
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


_SYMBOL_HEADER_REGEX = re.compile(r'\(symbol\s+"([^"]+)')


def iter_symbol_blocks(text: str, *, source: Path) -> Iterator[str]:
    """Yield raw `(symbol ...)` blocks from a .kicad_sym file."""
    i = 0
    length = len(text)
    while True:
        start = text.find("(symbol ", i)
        if start == -1:
            break
        depth = 0
        in_string = False
        escape = False
        pos = start
        while pos < length:
            ch = text[pos]
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
            else:
                if ch == '"':
                    in_string = True
                elif ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth == 0:
                        pos += 1
                        yield text[start:pos]
                        i = pos
                        break
            pos += 1
        else:
            if depth > 0:
                LOGGER.warning("Unbalanced parentheses in %s; attempting to auto-close.", source)
                patched = text[start:length] + (")" * depth)
                yield patched
            else:
                LOGGER.warning("Truncated symbol definition in %s.", source)
            break


def _sexp_to_str(atom: object) -> str:
    if isinstance(atom, sexpdata.Symbol):
        return atom.value()
    return str(atom)


def _extract_symbol_name_hint(block: str) -> str | None:
    match = _SYMBOL_HEADER_REGEX.match(block)
    if match:
        return match.group(1)
    return None


def _count_symbols_in_file(path: Path) -> int:
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return 0
    count = 0
    depth = 0
    in_string = False
    escape = False
    i = 0
    length = len(text)
    while i < length:
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
            elif ch == '(':
                if depth == 0 and text.startswith("(symbol ", i):
                    count += 1
                depth += 1
            elif ch == ')':
                depth = max(depth - 1, 0)
        i += 1
    return count


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS symbol_index (
            mpn TEXT NOT NULL,
            library TEXT NOT NULL,
            sexp TEXT NOT NULL,
            PRIMARY KEY (mpn, library)
        )
        """
    )
    conn.execute("PRAGMA journal_mode=WAL;")


def process_library(
    path: Path,
    conn: sqlite3.Connection,
    on_symbol_processed,
) -> tuple[int, int, int]:
    """Insert symbols from `path` into the database; return stats."""
    text = path.read_text(encoding="utf-8")
    library = path.stem
    inserted = 0
    skipped_existing = 0
    skipped_missing_mpn = 0

    for block in iter_symbol_blocks(text, source=path):
        symbol_hint = _extract_symbol_name_hint(block) or "<unknown>"
        try:
            symbol_expr = sexpdata.loads(block)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"Failed to parse symbol '{symbol_hint}' in {path}: {exc}"
            ) from exc

        if len(symbol_expr) < 2:
            skipped_missing_mpn += 1
            on_symbol_processed()
            continue
        symbol_name = _sexp_to_str(symbol_expr[1]).strip()
        if not symbol_name:
            skipped_missing_mpn += 1
            on_symbol_processed()
            continue

        cursor = conn.execute(
            "INSERT OR IGNORE INTO symbol_index (mpn, library, sexp) VALUES (?, ?, ?)",
            (symbol_name, library, block.strip()),
        )
        if cursor.rowcount == 1:
            inserted += 1
        else:
            skipped_existing += 1
        on_symbol_processed()

    return inserted, skipped_existing, skipped_missing_mpn


def _gather_symbol_files(symbols_dirs: Sequence[Path]) -> tuple[List[Path], Dict[Path, int], int]:
    files: List[Path] = []
    counts: Dict[Path, int] = {}
    total_symbols = 0
    for directory in symbols_dirs:
        if not directory.is_dir():
            LOGGER.warning("Symbols directory not found: %s", directory)
            continue
        for sym_file in sorted(directory.rglob("*.kicad_sym")):
            files.append(sym_file)
            count = _count_symbols_in_file(sym_file)
            counts[sym_file] = count
            total_symbols += count
    return files, counts, total_symbols


def _print_dual_progress(
    files_done: int,
    files_total: int,
    symbols_done: int,
    symbols_total: int,
) -> None:
    def fmt(done: int, total: int) -> str:
        if total == 0:
            return "0/0 (  0.0%)"
        percent = (done / total) * 100
        return f"{done}/{total} ({percent:5.1f}%)"

    print(
        f"\rFiles: {fmt(files_done, files_total)} | Symbols: {fmt(symbols_done, symbols_total)}",
        end="",
        flush=True,
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.ERROR if args.quiet else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    symbols_dirs = [Path(p).resolve() for p in args.symbols_dirs]
    dest = args.dest.resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(dest)
    try:
        ensure_schema(conn)
        total_inserted = 0
        total_exists = 0
        total_missing = 0
        symbol_files, symbol_counts, total_symbols = _gather_symbol_files(symbols_dirs)
        total_files = len(symbol_files)
        processed_files = 0
        processed_symbols = 0

        def advance_symbol_progress() -> None:
            nonlocal processed_symbols
            processed_symbols += 1
            if not args.quiet:
                _print_dual_progress(processed_files, total_files, processed_symbols, total_symbols)

        LOGGER.info("Found %d symbol libraries to process.", total_files)

        for index, sym_file in enumerate(symbol_files, start=1):
            counts = process_library(sym_file, conn, advance_symbol_progress)
            total_inserted += counts[0]
            total_exists += counts[1]
            total_missing += counts[2]
            processed_files = index
            if not args.quiet:
                _print_dual_progress(processed_files, total_files, processed_symbols, total_symbols)
        conn.commit()
        if not args.quiet and (total_files or total_symbols):
            print()  # newline after progress updates

        LOGGER.info(
            "Done. inserted=%d, skipped_existing=%d, skipped_missing_mpn=%d",
            total_inserted,
            total_exists,
            total_missing,
        )
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
