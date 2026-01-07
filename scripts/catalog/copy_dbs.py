#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Sequence, Tuple
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parents[2]
PYTHON_ROOT = REPO_ROOT / "python"
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from db_tools.settings import get_settings, resolve_sqlite_path


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
        description="Copy the configured SQLite DB to a writable working directory (never modify originals in-place)."
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

    repo_root = REPO_ROOT
    export_root = repo_root / "exported" / "db_work"
    dest_dir = args.dest or (export_root / (_timestamp() if args.timestamped else "active"))
    dest_dir.mkdir(parents=True, exist_ok=True)

    settings = get_settings()
    scheme = urlparse(settings.db_url).scheme.lower()
    if scheme != "sqlite":
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": f"copy_dbs.py only supports sqlite db_url (got {scheme!r})",
                },
                indent=2,
            )
        )
        return 2
    source_db = resolve_sqlite_path(settings.db_url)

    copies = _copy_many(
        prefix="db",
        sources=[source_db],
        dest_dir=dest_dir,
        overwrite=args.overwrite,
    )
    copied_all: Dict[str, List[Dict[str, str]]] = {
        "db": [{"src": str(src), "dest": str(dest)} for src, dest in copies]
    }

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
