#!/usr/bin/env python3
"""
Register custom symbol and footprint libraries in KiCad's global tables.

The script takes a root directory that mirrors the repository layout
(`symbols/` containing `.kicad_sym`, `footprints/` containing `.pretty`
folders) plus the folder that holds KiCad's global `sym-lib-table` and
`fp-lib-table` (for KiCad 9 this is typically `~/.config/kicad/9.0`).
Missing tables are created on the fly; existing entries are left untouched.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple

LIB_REGEX = re.compile(
    r"\(lib\s+\(name\s+\"?([^\")]+)\"?\).*?\(uri\s+\"?([^\")]+)\"?\)",
    re.DOTALL,
)


class TableUpdateError(RuntimeError):
    """Raised when a KiCad library table is malformed."""


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Add KiCad symbol/footprint libraries from a bundled directory "
            "to the global sym-lib-table/fp-lib-table."
        )
    )
    parser.add_argument(
        "symbol_lib_dir",
        type=Path,
        help="Directory containing 'symbols' and 'footprints' subdirectories.",
    )
    parser.add_argument(
        "table_dir",
        type=Path,
        help="Directory that holds sym-lib-table and fp-lib-table (e.g. ~/.config/kicad/9.0).",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress informational output.",
    )
    return parser.parse_args(argv)


def _read_table(path: Path, keyword: str) -> List[str]:
    if not path.exists():
        return [f"({keyword}", "  (version 7)", ")"]
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines:
        return [f"({keyword}", "  (version 7)", ")"]
    if not lines[0].startswith(f"({keyword}"):
        raise TableUpdateError(f"{path} does not start with '({keyword}'.")
    if lines[-1].strip() != ")":
        lines.append(")")
    return lines


def _write_table(path: Path, lines: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _extract_existing(lines: Sequence[str]) -> Tuple[set[str], set[str]]:
    text = "\n".join(lines)
    names: set[str] = set()
    uris: set[str] = set()
    for match in LIB_REGEX.finditer(text):
        name = match.group(1).strip()
        uri = match.group(2).strip()
        names.add(name)
        uris.add(uri)
    return names, uris


def _format_entry(name: str, uri: Path, descr: str) -> str:
    abs_uri = uri.resolve()
    return (
        f'  (lib (name "{name}")(type "KiCad")(uri "{abs_uri}")'
        f'(options "")(descr "{descr}"))'
    )


def _insert_entries(lines: List[str], entries: Iterable[str]) -> Tuple[List[str], int]:
    insertion_index = len(lines) - 1
    updated = list(lines[:insertion_index])
    count = 0
    for entry in entries:
        updated.append(entry)
        count += 1
    updated.extend(lines[insertion_index:])
    return updated, count


def _collect_symbol_entries(
    symbols_dir: Path, existing_names: set[str], existing_uris: set[str]
) -> List[str]:
    entries: List[str] = []
    for sym_path in sorted(symbols_dir.glob("*.kicad_sym")):
        nickname = sym_path.stem
        uri = sym_path.resolve()
        if nickname in existing_names or str(uri) in existing_uris:
            continue
        entries.append(_format_entry(nickname, uri, "Custom symbol library"))
    return entries


def _collect_footprint_entries(
    footprints_dir: Path, existing_names: set[str], existing_uris: set[str]
) -> List[str]:
    entries: List[str] = []
    for fp_dir in sorted(footprints_dir.glob("*.pretty")):
        nickname = fp_dir.stem
        uri = fp_dir.resolve()
        if nickname in existing_names or str(uri) in existing_uris:
            continue
        entries.append(_format_entry(nickname, uri, "Custom footprint library"))
    return entries


def _path_for_config(path: Path, project_root: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(project_root))
    except ValueError:
        return str(resolved)


def _merge_paths(existing: Iterable[str], additions: Iterable[str]) -> List[str]:
    merged: List[str] = []
    seen: set[str] = set()
    for item in list(existing) + list(additions):
        if not item:
            continue
        normalized = str(item)
        if normalized not in seen:
            seen.add(normalized)
            merged.append(normalized)
    return merged


def _update_library_paths_config(project_root: Path, symbols_dir: Path, footprints_dir: Path) -> None:
    config_path = project_root / "config" / "library-paths.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)

    data: dict[str, List[str]] = {}
    if config_path.exists():
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise TableUpdateError(f"Failed to parse {config_path}: {exc}") from exc

    symbol_entry = _path_for_config(symbols_dir, project_root)
    footprint_entry = _path_for_config(footprints_dir, project_root)

    data["symbolSearchPaths"] = _merge_paths(data.get("symbolSearchPaths", []), [symbol_entry])
    data["footprintSearchPaths"] = _merge_paths(
        data.get("footprintSearchPaths", []), [footprint_entry]
    )

    config_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    symbol_lib_dir: Path = args.symbol_lib_dir.resolve()
    table_dir: Path = args.table_dir.resolve()

    symbols_dir = symbol_lib_dir / "symbols"
    footprints_dir = symbol_lib_dir / "footprints"

    if not symbols_dir.is_dir():
        raise FileNotFoundError(f"Missing symbols directory: {symbols_dir}")
    if not footprints_dir.is_dir():
        raise FileNotFoundError(f"Missing footprints directory: {footprints_dir}")

    sym_table_path = table_dir / "sym-lib-table"
    fp_table_path = table_dir / "fp-lib-table"

    sym_lines = _read_table(sym_table_path, "sym_lib_table")
    sym_names, sym_uris = _extract_existing(sym_lines)
    sym_entries = _collect_symbol_entries(symbols_dir, sym_names, sym_uris)
    if sym_entries:
        sym_lines, added_sym = _insert_entries(sym_lines, sym_entries)
        _write_table(sym_table_path, sym_lines)
    else:
        added_sym = 0

    fp_lines = _read_table(fp_table_path, "fp_lib_table")
    fp_names, fp_uris = _extract_existing(fp_lines)
    fp_entries = _collect_footprint_entries(footprints_dir, fp_names, fp_uris)
    if fp_entries:
        fp_lines, added_fp = _insert_entries(fp_lines, fp_entries)
        _write_table(fp_table_path, fp_lines)
    else:
        added_fp = 0

    project_root = symbol_lib_dir.parent
    _update_library_paths_config(project_root, symbols_dir, footprints_dir)

    if not args.quiet:
        print(
            f"Updated {sym_table_path}: +{added_sym} entries; "
            f"updated {fp_table_path}: +{added_fp} entries.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
