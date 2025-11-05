#!/usr/bin/env python3
"""
Index KiCad symbol names from the JLC symbol library bundle.

The script scans `.kicad_sym` files under the symbols directory, extracts the
symbol identifiers exactly as they appear, and optionally writes them to a
plain-text list and/or a TSV mapping of symbol name to source files.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, List, Sequence, Set, Tuple

BINARY_SUFFIXES = {".zip", ".gz", ".7z"}
SYMBOL_PREFIX = '(symbol "'


def _read_text(path: Path) -> str:
    """Return file contents as text, falling back on permissive decoding."""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_bytes().decode("utf-8", errors="ignore")


def _extract_symbol_names(text: str, include_units: bool) -> List[str]:
    """
    Return symbol names found in `text`.

    When `include_units` is False, only top-level symbol names are returned,
    skipping nested unit/De Morgan variants (e.g. `_0_1`).
    """
    names: List[str] = []
    depth = 0
    in_string = False
    escape = False
    i = 0
    text_len = len(text)

    while i < text_len:
        ch = text[i]

        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            i += 1
            continue

        if ch == '"':
            in_string = True
            i += 1
            continue

        if ch == "(":
            current_depth = depth
            if text.startswith(SYMBOL_PREFIX, i):
                name_start = i + len(SYMBOL_PREFIX)
                name_end = text.find('"', name_start)
                if name_end != -1:
                    name = text[name_start:name_end]
                    if include_units or current_depth == 0:
                        names.append(name)
            depth += 1
            i += 1
            continue

        if ch == ")":
            depth = max(depth - 1, 0)
            i += 1
            continue

        i += 1

    return names


def index_symbols(symbols_dir: Path, include_units: bool = False) -> Tuple[List[str], Dict[str, Set[str]]]:
    """
    Scan `symbols_dir` for KiCad symbol declarations.

    Returns:
        ordered_names: list preserving first-seen order of symbol names
        name_sources: mapping of symbol name -> set of relative source paths
    """
    if not symbols_dir.is_dir():
        raise FileNotFoundError(f"Symbols directory not found: {symbols_dir}")

    ordered_names: List[str] = []
    name_sources: Dict[str, Set[str]] = {}

    root_hint = symbols_dir

    for path in sorted(symbols_dir.rglob("*.kicad_sym")):
        if path.suffix.lower() in BINARY_SUFFIXES:
            continue
        text = _read_text(path)
        rel_path = str(path.relative_to(root_hint))

        for name in _extract_symbol_names(text, include_units=include_units):
            if name not in name_sources:
                ordered_names.append(name)
                name_sources[name] = set()
            name_sources[name].add(rel_path)

    return ordered_names, name_sources


def _write_list(path: Path, names: Sequence[str]) -> None:
    """Write newline-separated names to `path`."""
    with path.open("w", encoding="utf-8") as handle:
        handle.write("\n".join(names))
        handle.write("\n")


def _write_sources(path: Path, mapping: Dict[str, Set[str]]) -> None:
    """Write TSV with symbol name and comma-separated source paths."""
    with path.open("w", encoding="utf-8") as handle:
        for name in mapping:
            sources = ",".join(sorted(mapping[name]))
            handle.write(f"{name}\t{sources}\n")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate an index of KiCad symbol names."
    )
    parser.add_argument(
        "--symbols-dir",
        type=Path,
        default=None,
        help="Directory to scan (defaults to the project's symbol_lib/symbols).",
    )
    parser.add_argument(
        "--output-list",
        type=Path,
        help="Optional path to write the newline-separated symbol name list.",
    )
    parser.add_argument(
        "--output-sources",
        type=Path,
        help="Optional path to write the symbol-to-source TSV mapping.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress summary output when writing to files.",
    )
    parser.add_argument(
        "--include-units",
        action="store_true",
        help="Include KiCad unit/de Morgan variant symbols (e.g. *_0_1).",
    )
    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parents[2]
    default_symbols_dir = repo_root / "symbol_lib" / "symbols"
    symbols_dir = (args.symbols_dir or default_symbols_dir).resolve()

    names, mapping = index_symbols(symbols_dir, include_units=args.include_units)

    if args.output_list:
        _write_list(args.output_list, names)
    else:
        for name in names:
            print(name)

    if args.output_sources:
        _write_sources(args.output_sources, mapping)

    if not args.quiet:
        destination = f"-> {args.output_list}" if args.output_list else "-> stdout"
        print(
            f"Indexed {len(names)} symbol names {destination}", file=sys.stderr
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
