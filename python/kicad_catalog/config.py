from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Sequence

from .workdir import resolve_read_db_path


def resolve_repo_root() -> Path:
    """
    Resolve the KiCAD_MCP repository root.

    Note: this is a temporary convenience during extraction; the standalone
    catalog repo should not assume it lives inside KiCAD_MCP.
    """

    return Path(__file__).resolve().parents[2]


def _resolve_path(entry: str, *, repo_root: Path) -> Optional[Path]:
    if not entry:
        return None
    path_obj = Path(entry).expanduser()
    if not path_obj.is_absolute():
        path_obj = (repo_root / path_obj).resolve()
    return path_obj


def _unique_paths(paths: Iterable[Path]) -> list[Path]:
    seen: set[Path] = set()
    out: list[Path] = []
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        out.append(path)
    return out


@dataclass(frozen=True, slots=True)
class CatalogPaths:
    component_db: Path
    symbol_dbs: tuple[Path, ...]
    footprint_dbs: tuple[Path, ...]
    footprint_search_paths: tuple[Path, ...]
    spice_model_db: Path


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def load_catalog_paths(
    *,
    repo_root: Optional[Path] = None,
    library_paths_config: Optional[Path] = None,
) -> CatalogPaths:
    """
    Resolve all catalog-related paths using current KiCAD_MCP conventions.

    Precedence (per path type):
    - explicit environment variables (where historically present)
    - `config/library-paths.json`
    - repo-relative defaults

    This will be replaced by `KICAD_CATALOG_URL` once the standalone catalog
    repo is introduced.
    """

    root = repo_root or resolve_repo_root()
    config_path = library_paths_config or (root / "config" / "library-paths.json")
    data = _load_json(config_path)

    component_db = Path(
        os.environ.get("JLCPCB_DB_PATH", str(root / "part_lib" / "jlcpcb-components.sqlite3"))
    ).expanduser()
    component_db = resolve_read_db_path(component_db, prefix="component", repo_root=root)

    symbol_db_paths: list[Path] = []
    symbol_single = data.get("symbolDbPath")
    if isinstance(symbol_single, str):
        resolved = _resolve_path(symbol_single, repo_root=root)
        if resolved:
            symbol_db_paths.append(resolved)
    symbol_multi = data.get("symbolDbPaths")
    if isinstance(symbol_multi, Sequence):
        for entry in symbol_multi:
            if not isinstance(entry, str):
                continue
            resolved = _resolve_path(entry, repo_root=root)
            if resolved:
                symbol_db_paths.append(resolved)
    symbol_search_paths = data.get("symbolSearchPaths")
    if isinstance(symbol_search_paths, Sequence):
        for entry in symbol_search_paths:
            if not isinstance(entry, str):
                continue
            lowered = entry.strip().lower()
            if not lowered.endswith((".db", ".sqlite", ".sqlite3")):
                continue
            resolved = _resolve_path(entry, repo_root=root)
            if resolved:
                symbol_db_paths.append(resolved)
    if not symbol_db_paths:
        symbol_db_paths.append(root / "symbol_lib" / "kicad_symbols.sqlite3")
    symbol_db_paths = [
        resolve_read_db_path(
            path,
            prefix="symbol",
            index=index,
            total=len(symbol_db_paths),
            repo_root=root,
        )
        for index, path in enumerate(symbol_db_paths)
    ]

    footprint_db_paths: list[Path] = []
    footprint_single = data.get("footprintDbPath")
    if isinstance(footprint_single, str):
        resolved = _resolve_path(footprint_single, repo_root=root)
        if resolved:
            footprint_db_paths.append(resolved)
    footprint_multi = data.get("footprintDbPaths")
    if isinstance(footprint_multi, Sequence):
        for entry in footprint_multi:
            if not isinstance(entry, str):
                continue
            resolved = _resolve_path(entry, repo_root=root)
            if resolved:
                footprint_db_paths.append(resolved)
    if not footprint_db_paths:
        footprint_db_paths.append(root / "symbol_lib" / "kicad_footprints.sqlite3")
    footprint_db_paths = [
        resolve_read_db_path(
            path,
            prefix="footprint",
            index=index,
            total=len(footprint_db_paths),
            repo_root=root,
        )
        for index, path in enumerate(footprint_db_paths)
    ]

    footprint_search_paths: list[Path] = []
    configured_search = data.get("footprintSearchPaths")
    if isinstance(configured_search, Sequence):
        for entry in configured_search:
            if not isinstance(entry, str):
                continue
            resolved = _resolve_path(entry, repo_root=root)
            if resolved:
                footprint_search_paths.append(resolved)
    if not footprint_search_paths:
        footprint_search_paths.append(root / "symbol_lib" / "footprints")

    spice_model_db_env = os.environ.get("KICAD_SPICE_MODEL_DB_PATH")
    spice_model_db = (
        Path(spice_model_db_env).expanduser()
        if spice_model_db_env
        else (root / "spice_lib" / "spice_models.db")
    )
    spice_model_db = resolve_read_db_path(spice_model_db, prefix="spice", repo_root=root)

    return CatalogPaths(
        component_db=component_db,
        symbol_dbs=tuple(_unique_paths(symbol_db_paths)),
        footprint_dbs=tuple(_unique_paths(footprint_db_paths)),
        footprint_search_paths=tuple(_unique_paths(footprint_search_paths)),
        spice_model_db=spice_model_db,
    )


__all__ = ["CatalogPaths", "load_catalog_paths", "resolve_repo_root"]
