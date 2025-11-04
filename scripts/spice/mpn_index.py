#!/usr/bin/env python3
"""
Index SPICE model names (MPNs) from the KiCad-Spice-Library.

The script scans `.model` and `.subckt` declarations under the Models
directory and reports the model names exactly as they appear in the source.
It is intended to provide a dependable list or mapping that downstream
pipelines can use when tagging components or assembling netlists.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
import re
from typing import Dict, Iterable, List, Sequence, Set, Tuple

MODEL_RE = re.compile(r"^\s*\.model\s+([^\s\(]+)", re.IGNORECASE)
SUBCKT_RE = re.compile(r"^\s*\.subckt\s+([^\s\(]+)", re.IGNORECASE)

BINARY_SUFFIXES = {
    ".7z",
    ".bmp",
    ".gif",
    ".gz",
    ".jpeg",
    ".jpg",
    ".pdf",
    ".png",
    ".rar",
    ".svg",
    ".zip",
}


def _read_text(path: Path) -> str:
    """Return file contents as text, tolerating mixed encodings."""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            return path.read_text(encoding="latin-1")
        except UnicodeDecodeError:
            return path.read_bytes().decode("utf-8", errors="ignore")


def _iter_model_lines(text: str) -> Iterable[str]:
    """Yield non-comment lines from the provided SPICE text."""
    for line in text.splitlines():
        stripped = line.lstrip()
        if not stripped:
            continue
        if stripped.startswith(("*", ";")):
            continue
        yield line


def index_models(models_dir: Path) -> Tuple[List[str], Dict[str, Set[str]]]:
    """
    Scan `models_dir` for SPICE models.

    Returns:
        ordered_names: list preserving first-seen order of model names
        name_sources: mapping of model name -> set of relative source paths
    """
    if not models_dir.is_dir():
        raise FileNotFoundError(f"Models directory not found: {models_dir}")

    ordered_names: List[str] = []
    name_sources: Dict[str, Set[str]] = {}

    root_hint = models_dir.parent

    for path in sorted(models_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() in BINARY_SUFFIXES:
            continue
        text = _read_text(path)
        try:
            rel_path = str(path.relative_to(root_hint))
        except ValueError:
            rel_path = str(path)

        for line in _iter_model_lines(text):
            match = MODEL_RE.match(line)
            if not match:
                match = SUBCKT_RE.match(line)
            if not match:
                continue
            name = match.group(1)
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
    """Write TSV with model name and comma-separated source paths."""
    with path.open("w", encoding="utf-8") as handle:
        for name in mapping:
            sources = ",".join(sorted(mapping[name]))
            handle.write(f"{name}\t{sources}\n")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate an index of SPICE model names (MPNs)."
    )
    parser.add_argument(
        "--models-dir",
        type=Path,
        default=None,
        help="Directory to scan (defaults to the repository's Models folder).",
    )
    parser.add_argument(
        "--output-list",
        type=Path,
        help="Optional path to write the newline-separated model name list.",
    )
    parser.add_argument(
        "--output-sources",
        type=Path,
        help="Optional path to write the name-to-source TSV mapping.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress summary output when writing to files.",
    )
    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parents[2]
    default_models_dir = repo_root / "KiCad-Spice-Library" / "Models"
    models_dir = (args.models_dir or default_models_dir).resolve()

    names, mapping = index_models(models_dir)

    if args.output_list:
        _write_list(args.output_list, names)
    else:
        for name in names:
            print(name)

    if args.output_sources:
        _write_sources(args.output_sources, mapping)

    if not args.quiet:
        destination = f"-> {args.output_list}" if args.output_list else "-> stdout"
        print(f"Indexed {len(names)} model names {destination}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
