from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Optional

_DEFAULT_WORK_SUBDIR = Path("exported") / "db_work" / "active"
_WORK_DIR_ENV = "KICAD_DB_WORK_DIR"
_ALLOW_INPLACE_WRITES_ENV = "KICAD_ALLOW_INPLACE_DB_WRITES"
_PROTECTED_DIR_NAMES = ("part_lib", "symbol_lib", "spice_lib")


def _resolve_repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def get_db_work_dir(*, repo_root: Optional[Path] = None) -> Path:
    root = repo_root or _resolve_repo_root()
    configured = os.environ.get(_WORK_DIR_ENV)
    if configured:
        return Path(configured).expanduser()
    return root / _DEFAULT_WORK_SUBDIR


def _safe_filename(prefix: str, filename: str, *, index: int, total: int) -> str:
    if total > 1:
        return f"{prefix}-{index+1}-{filename}"
    return f"{prefix}-{filename}"


def _work_copy_path(
    original: Path,
    *,
    prefix: str,
    index: int = 0,
    total: int = 1,
    repo_root: Optional[Path] = None,
) -> Path:
    work_dir = get_db_work_dir(repo_root=repo_root)
    return work_dir / _safe_filename(prefix, original.name, index=index, total=total)


def is_protected_db_path(path: Path, *, repo_root: Optional[Path] = None) -> bool:
    """
    Return True when a DB path should be treated as read-only/original.

    In this repo, shared datasets are typically mounted under `/mnt/shared` and
    exposed via symlinks like `part_lib/`, `symbol_lib/`, and `spice_lib/`.
    """

    root = repo_root or _resolve_repo_root()
    resolved = path.expanduser().resolve(strict=False)

    if str(resolved).startswith("/mnt/shared/"):
        return True

    for name in _PROTECTED_DIR_NAMES:
        protected_dir = (root / name).resolve(strict=False)
        if resolved.is_relative_to(protected_dir):
            return True

    return False


def resolve_read_db_path(
    original: Path,
    *,
    prefix: str,
    index: int = 0,
    total: int = 1,
    repo_root: Optional[Path] = None,
) -> Path:
    """
    Prefer a working copy when present, otherwise fall back to the original.
    """

    candidate = _work_copy_path(
        original,
        prefix=prefix,
        index=index,
        total=total,
        repo_root=repo_root,
    )
    return candidate if candidate.exists() else original


def _allow_inplace_writes() -> bool:
    value = (os.environ.get(_ALLOW_INPLACE_WRITES_ENV) or "").strip().lower()
    return value in {"1", "true", "yes", "y"}


def ensure_db_work_copy(
    original: Path,
    *,
    prefix: str,
    index: int = 0,
    total: int = 1,
    repo_root: Optional[Path] = None,
) -> Path:
    """
    Ensure a stable writable working copy exists for a DB file.

    Copies `original` into `exported/db_work/active/` (or `$KICAD_DB_WORK_DIR`)
    using a deterministic filename based on `(prefix, index, total, original.name)`.
    """

    target = _work_copy_path(
        original,
        prefix=prefix,
        index=index,
        total=total,
        repo_root=repo_root,
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        return target
    if original.exists():
        shutil.copy2(original, target, follow_symlinks=True)
    return target


def resolve_write_db_path(
    original: Path,
    *,
    prefix: str,
    index: int = 0,
    total: int = 1,
    repo_root: Optional[Path] = None,
) -> Path:
    """
    Resolve a writable DB path without mutating the original.

    By default this returns a working copy path (and ensures it exists). Set
    `$KICAD_ALLOW_INPLACE_DB_WRITES=1` to opt into in-place modifications.
    """

    if _allow_inplace_writes() or not is_protected_db_path(original, repo_root=repo_root):
        return original
    return ensure_db_work_copy(
        original,
        prefix=prefix,
        index=index,
        total=total,
        repo_root=repo_root,
    )


__all__ = [
    "ensure_db_work_copy",
    "get_db_work_dir",
    "is_protected_db_path",
    "resolve_read_db_path",
    "resolve_write_db_path",
]
