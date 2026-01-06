#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
PYTHON_ROOT = REPO_ROOT / "python"
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from kicad_catalog.config import load_catalog_paths, resolve_repo_root


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _safe_filename(prefix: str, path: Path, index: int, total: int) -> str:
    base = path.name
    if total > 1:
        return f"{prefix}-{index+1}-{base}"
    return f"{prefix}-{base}"


def _copy_many(
    *,
    prefix: str,
    sources: Sequence[Path],
    dest_dir: Path,
    overwrite: bool,
) -> List[Tuple[Path, Path]]:
    copied: List[Tuple[Path, Path]] = []
    existing = {p.name for p in dest_dir.glob("*") if p.is_file()}
    for idx, src in enumerate(sources):
        if not src.exists():
            continue
        dest_name = _safe_filename(prefix, src, idx, len(sources))
        if (dest_name in existing) and not overwrite:
            continue
        dest_path = dest_dir / dest_name
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest_path, follow_symlinks=True)
        copied.append((src, dest_path))
    return copied


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Copy KiCAD_MCP SQLite databases to a writable working directory (never modify originals in-place)."
    )
    parser.add_argument(
        "--dest",
        type=Path,
        default=None,
        help="Destination directory (default: exported/db_work/active).",
    )
    parser.add_argument(
        "--timestamped",
        action="store_true",
        help="When --dest is not provided, copy into exported/db_work/<timestamp>/ instead of exported/db_work/active/.",
    )
    parser.add_argument(
        "--include",
        nargs="+",
        default=["symbol", "footprint", "spice"],
        choices=["component", "symbol", "footprint", "spice"],
        help="Which databases to copy (default: symbol footprint spice).",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing files in destination.",
    )
    parser.add_argument(
        "--write-manifest",
        action="store_true",
        help="Write a JSON manifest next to the copies.",
    )
    args = parser.parse_args()

    repo_root = resolve_repo_root()
    export_root = repo_root / "exported" / "db_work"
    dest_dir = args.dest or (export_root / (_timestamp() if args.timestamped else "active"))
    dest_dir.mkdir(parents=True, exist_ok=True)

    paths = load_catalog_paths(repo_root=repo_root)
    plan: Dict[str, Sequence[Path]] = {
        "component": [paths.component_db],
        "symbol": list(paths.symbol_dbs),
        "footprint": list(paths.footprint_dbs),
        "spice": [paths.spice_model_db],
    }

    copied_all: Dict[str, List[Dict[str, str]]] = {}
    for key in args.include:
        sources = plan.get(key, [])
        copies = _copy_many(
            prefix=key,
            sources=sources,
            dest_dir=dest_dir,
            overwrite=args.overwrite,
        )
        copied_all[key] = [{"src": str(src), "dest": str(dest)} for src, dest in copies]

    manifest = {
        "destDir": str(dest_dir),
        "copied": copied_all,
    }

    print(json.dumps(manifest, indent=2))

    if args.write_manifest:
        manifest_path = dest_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
