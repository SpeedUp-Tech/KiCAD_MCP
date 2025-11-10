from __future__ import annotations

import copy
import json
import logging
import os
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from sexpdata import Symbol as SSymbol

from skip import Schematic
from skip.sexp.util import writeTree

LOGGER = logging.getLogger("library_export")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LIBRARY_PATHS_CONFIG = PROJECT_ROOT / "config" / "library-paths.json"
DEFAULT_SYMBOL_VERSION = 20211014
SYMBOL_GENERATOR = "KiCAD-MCP-ProjectLibs"
DEFAULT_FOOTPRINT_DB = PROJECT_ROOT / "symbol_lib" / "kicad_footprints.sqlite3"
DEFAULT_FOOTPRINT_DIR = PROJECT_ROOT / "symbol_lib" / "footprints"
DEFAULT_KICAD_MAJOR_VERSION = os.environ.get("KICAD_MAJOR_VERSION", "9.0")

_CONFIG_CACHE: Optional[dict] = None
_FOOTPRINT_DB_PATHS: Optional[List[Path]] = None
_FOOTPRINT_SEARCH_PATHS: Optional[List[Path]] = None
_FOOTPRINT_DB_CONNECTIONS: Dict[Path, sqlite3.Connection] = {}


def _load_library_paths_config() -> dict:
    global _CONFIG_CACHE
    if _CONFIG_CACHE is not None:
        return _CONFIG_CACHE
    if LIBRARY_PATHS_CONFIG.exists():
        try:
            _CONFIG_CACHE = json.loads(LIBRARY_PATHS_CONFIG.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Failed to parse %s: %s", LIBRARY_PATHS_CONFIG, exc)
            _CONFIG_CACHE = {}
    else:
        _CONFIG_CACHE = {}
    return _CONFIG_CACHE


def _resolve_path(entry: str) -> Optional[Path]:
    if not entry:
        return None
    path_obj = Path(entry).expanduser()
    if not path_obj.is_absolute():
        path_obj = (PROJECT_ROOT / path_obj).resolve()
    return path_obj


def _get_footprint_db_paths() -> List[Path]:
    global _FOOTPRINT_DB_PATHS
    if _FOOTPRINT_DB_PATHS is not None:
        return _FOOTPRINT_DB_PATHS

    data = _load_library_paths_config()
    paths: List[Path] = []

    single = data.get("footprintDbPath")
    if isinstance(single, str):
        resolved = _resolve_path(single)
        if resolved:
            paths.append(resolved)

    multi = data.get("footprintDbPaths")
    if isinstance(multi, Sequence):
        for entry in multi:
            if not isinstance(entry, str):
                continue
            resolved = _resolve_path(entry)
            if resolved and resolved not in paths:
                paths.append(resolved)

    if not paths:
        paths.append(DEFAULT_FOOTPRINT_DB)

    _FOOTPRINT_DB_PATHS = paths
    return paths


def _get_footprint_search_paths() -> List[Path]:
    global _FOOTPRINT_SEARCH_PATHS
    if _FOOTPRINT_SEARCH_PATHS is not None:
        return _FOOTPRINT_SEARCH_PATHS

    data = _load_library_paths_config()
    paths: List[Path] = []

    configured = data.get("footprintSearchPaths")
    if isinstance(configured, Sequence):
        for entry in configured:
            if not isinstance(entry, str):
                continue
            resolved = _resolve_path(entry)
            if resolved and resolved not in paths:
                paths.append(resolved)

    defaults = [
        DEFAULT_FOOTPRINT_DIR,
        Path("/usr/share/kicad/footprints"),
        Path("/usr/local/share/kicad/footprints"),
    ]
    for candidate in defaults:
        if candidate and candidate not in paths:
            paths.append(candidate)

    env_vars = (
        "KICAD6_FOOTPRINT_DIR",
        "KICAD7_FOOTPRINT_DIR",
        "KICAD8_FOOTPRINT_DIR",
    )
    for var in env_vars:
        value = os.environ.get(var)
        if not value:
            continue
        resolved = Path(value).expanduser()
        if resolved not in paths:
            paths.append(resolved)

    _FOOTPRINT_SEARCH_PATHS = paths
    return paths


def _atom_to_str(atom: object) -> str:
    try:
        if isinstance(atom, SSymbol):
            return atom.value()
        return str(atom)
    except Exception:  # noqa: BLE001
        return str(atom)


def _is_entry(node: object, name: str) -> bool:
    if not isinstance(node, list) or not node:
        return False
    head = node[0]
    return isinstance(head, SSymbol) and head.value() == name


def _sanitize_filename(name: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "._- " else "_" for ch in name)
    return safe or "library"


def _make_library_path(
    mapping: Dict[str, Path],
    used: Dict[Path, str],
    base_dir: Path,
    library: str,
    suffix: str,
) -> Path:
    cached = mapping.get(library)
    if cached:
        return cached

    safe = _sanitize_filename(library)
    candidate = base_dir / f"{safe}{suffix}"
    index = 1
    while candidate in used:
        index += 1
        candidate = base_dir / f"{safe}_{index}{suffix}"
    mapping[library] = candidate
    used[candidate] = library
    return candidate


def _collect_symbol_entries(schematic: Schematic) -> Dict[str, List[List[object]]]:
    container = None
    for node in getattr(schematic, "tree", []):
        if _is_entry(node, "lib_symbols"):
            container = node
            break
    if container is None:
        return {}

    libraries: Dict[str, List[List[object]]] = {}
    for entry in container[1:]:
        if not _is_entry(entry, "symbol") or len(entry) < 2:
            continue
        symbol_id = _atom_to_str(entry[1])
        if not symbol_id:
            continue
        library_name = symbol_id.split(":", 1)[0] if ":" in symbol_id else "Project"
        libraries.setdefault(library_name, []).append(copy.deepcopy(entry))
    return libraries


def _extract_symbol_property(symbol: object, key: str) -> Optional[str]:
    try:
        props = getattr(symbol, "property", None)
        field = getattr(props, key, None)
        value = getattr(field, "value", None)
        if value:
            return str(value)
    except Exception:  # noqa: BLE001
        pass

    raw = getattr(symbol, "raw", None)
    if isinstance(raw, list):
        for entry in raw:
            if _is_entry(entry, "property") and len(entry) >= 3 and _atom_to_str(entry[1]) == key:
                return _atom_to_str(entry[2])
    return None


@dataclass(frozen=True)
class FootprintSpec:
    raw: str
    library: str
    candidates: Tuple[str, ...]


def _parse_footprint_value(value: str) -> Optional[FootprintSpec]:
    if not value:
        return None
    trimmed = value.strip()
    if not trimmed or trimmed == "~":
        return None
    if ":" not in trimmed:
        return FootprintSpec(trimmed, "ProjectFootprints", (trimmed,))
    parts = trimmed.split(":")
    library = parts[0].strip() or "ProjectFootprints"
    remainder = ":".join(parts[1:]).strip()
    candidates: List[str] = []
    if remainder:
        candidates.append(remainder)
    last = parts[-1].strip()
    if last and last not in candidates:
        candidates.append(last)
    if not candidates:
        return None
    return FootprintSpec(trimmed, library, tuple(candidates))


def _collect_footprint_specs(schematic: Schematic) -> List[FootprintSpec]:
    specs: List[FootprintSpec] = []
    seen: set[str] = set()
    if not hasattr(schematic, "symbol") or schematic.symbol is None:
        return specs

    for symbol in schematic.symbol:
        footprint_value = _extract_symbol_property(symbol, "Footprint")
        spec = _parse_footprint_value(footprint_value) if footprint_value else None
        if spec and spec.raw not in seen:
            seen.add(spec.raw)
            specs.append(spec)
    return specs


def _reset_output_directory(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def _locate_project_root(schematic_path: Path) -> Path:
    """
    Walk up from `schematic_path` to find the enclosing project root (directory that contains 'kicad').
    """
    resolved = schematic_path.resolve()
    for ancestor in [resolved.parent, *resolved.parents]:
        if ancestor.name == "kicad":
            return ancestor.parent
    raise RuntimeError(f"Unable to locate 'kicad' directory for schematic: {schematic_path}")


def _determine_module_output_root(
    schematic_path: Path,
    module_name: str,
    output_root: Optional[str],
) -> Tuple[Path, Path, Path]:
    """
    Determine where the exported libraries and KiCad config should live.

    Returns:
        Tuple of (project_root, module_root, module_config_dir)
    """
    if output_root:
        project_root = Path(output_root).expanduser().resolve()
    else:
        project_root = _locate_project_root(schematic_path)

    kicad_root = project_root / "kicad"
    libraries_root = kicad_root / "libraries"
    module_root = libraries_root / module_name
    module_config_dir = module_root / DEFAULT_KICAD_MAJOR_VERSION
    return project_root, module_root, module_config_dir


def _write_symbol_library(library: str, entries: List[List[object]], destination: Path) -> None:
    tree: List[object] = [
        SSymbol("kicad_symbol_lib"),
        [SSymbol("version"), DEFAULT_SYMBOL_VERSION],
        [SSymbol("generator"), SYMBOL_GENERATOR],
    ]
    tree.extend(entries)
    writeTree(str(destination), tree)


def _fetch_footprint_text(library: str, candidates: Sequence[str]) -> Tuple[Optional[str], Optional[str]]:
    for db_path in _get_footprint_db_paths():
        if not db_path or not Path(db_path).exists():
            continue
        conn = _FOOTPRINT_DB_CONNECTIONS.get(db_path)
        if conn is None:
            try:
                conn = sqlite3.connect(str(db_path))
                conn.row_factory = sqlite3.Row
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("Unable to open footprint DB %s: %s", db_path, exc)
                continue
            _FOOTPRINT_DB_CONNECTIONS[db_path] = conn
        for candidate in candidates:
            try:
                cursor = conn.execute(
                    "SELECT sexp FROM footprint_index WHERE library = ? AND name = ?",
                    (library, candidate),
                )
                row = cursor.fetchone()
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("Footprint DB query failed (%s:%s) in %s: %s", library, candidate, db_path, exc)
                continue
            if row:
                sexp = row["sexp"] if isinstance(row, sqlite3.Row) else row[0]
                return candidate, sexp
    # fallback to filesystem search
    dir_name = f"{library}.pretty"
    for root in _get_footprint_search_paths():
        candidate_dir = root / dir_name
        for candidate in candidates:
            file_path = candidate_dir / f"{candidate}.kicad_mod"
            if file_path.exists():
                try:
                    return candidate, file_path.read_text(encoding="utf-8")
                except Exception as exc:  # noqa: BLE001
                    LOGGER.warning("Failed to read footprint file %s: %s", file_path, exc)
    return None, None


def _write_sym_lib_table(entries: Dict[str, Path], output_path: Path) -> None:
    lines = ["(sym_lib_table", "  (version 7)"]
    for library in sorted(entries.keys()):
        uri = entries[library].resolve()
        lines.append(
            f'  (lib (name "{library}")(type "KiCad")(uri "{uri}")'
            '(options "")(descr "Generated by KiCAD-MCP"))'
        )
    lines.append(")")
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_fp_lib_table(entries: Dict[str, Path], output_path: Path) -> None:
    lines = ["(fp_lib_table", "  (version 7)"]
    for library in sorted(entries.keys()):
        uri = entries[library].resolve()
        lines.append(
            f'  (lib (name "{library}")(type "KiCad")(uri "{uri}")'
            '(options "")(descr "Generated by KiCAD-MCP"))'
        )
    lines.append(")")
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_placeholder_footprint(destination_dir: Path, name: str) -> Path:
    destination_dir.mkdir(parents=True, exist_ok=True)
    file_path = destination_dir / f"{name}.kicad_mod"
    placeholder = [
        '(footprint "{name}")',
        '  (layer "F.Cu")',
        '  (attr through_hole)',
        '  (fp_text reference "REF**" (at 0 0) (layer "F.SilkS")',
        '    (effects (font (size 1 1) (thickness 0.15)))',
        "  )",
        f'  (fp_text value "{name}" (at 0 -1.5) (layer "F.Fab")',
        '    (effects (font (size 1 1) (thickness 0.15)))',
        "  )",
        ")",
    ]
    file_path.write_text("\n".join(line.format(name=name) for line in placeholder) + "\n", encoding="utf-8")
    return file_path


def export_project_libraries(
    schematic: Schematic,
    schematic_path: str,
    *,
    output_root: Optional[str] = None,
) -> Dict[str, object]:
    """
    Export the symbols and footprints referenced by `schematic` into a project-local library bundle.

    Args:
        schematic: The loaded schematic object (already compiled/update in-memory).
        schematic_path: Path to the schematic file being processed.
        output_root: Optional override for the output directory.

    Returns:
        Dict containing paths to the generated library assets.
    """
    resolved = Path(schematic_path).resolve()
    module_name = resolved.stem
    project_root, module_root, module_config_dir = _determine_module_output_root(
        resolved,
        module_name,
        output_root,
    )

    module_root.parent.mkdir(parents=True, exist_ok=True)
    _reset_output_directory(module_root)

    symbols_dir = module_root / "symbols"
    footprints_dir = module_root / "footprints"

    symbols_dir.mkdir(parents=True, exist_ok=True)
    footprints_dir.mkdir(parents=True, exist_ok=True)
    module_config_dir.mkdir(parents=True, exist_ok=True)

    symbol_map = _collect_symbol_entries(schematic)
    symbol_files: Dict[str, Path] = {}
    symbol_paths_used: Dict[Path, str] = {}
    for library, entries in symbol_map.items():
        if not entries:
            continue
        destination = _make_library_path(symbol_files, symbol_paths_used, symbols_dir, library, ".kicad_sym")
        _write_symbol_library(library, entries, destination)

    footprint_specs = _collect_footprint_specs(schematic)
    footprint_dirs: Dict[str, Path] = {}
    footprint_paths_used: Dict[Path, str] = {}
    missing: List[str] = []
    placeholders: List[str] = []
    exported_pairs: set[Tuple[str, str]] = set()

    for spec in footprint_specs:
        resolved_name, text = _fetch_footprint_text(spec.library, spec.candidates)
        if not resolved_name or not text:
            missing_name = spec.candidates[0] if spec.candidates else spec.raw
            missing.append(f"{spec.library}:{missing_name}")
            target_dir = _make_library_path(footprint_dirs, footprint_paths_used, footprints_dir, spec.library, ".pretty")
            placeholder_path = _write_placeholder_footprint(target_dir, missing_name)
            placeholders.append(str(placeholder_path))
            continue
        key = (spec.library, resolved_name)
        if key in exported_pairs:
            continue
        exported_pairs.add(key)

        target_dir = _make_library_path(footprint_dirs, footprint_paths_used, footprints_dir, spec.library, ".pretty")
        target_dir.mkdir(parents=True, exist_ok=True)
        footprint_file = target_dir / f"{resolved_name}.kicad_mod"
        footprint_file.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")

    sym_table_path = module_config_dir / "sym-lib-table"
    fp_table_path = module_config_dir / "fp-lib-table"
    _write_sym_lib_table(symbol_files, sym_table_path)
    _write_fp_lib_table(footprint_dirs, fp_table_path)

    config_home = module_root
    config_dir = module_config_dir

    return {
        "baseDir": str(module_root),
        "configHome": str(config_home),
        "configDir": str(config_dir),
        "symbolsDir": str(symbols_dir),
        "footprintsDir": str(footprints_dir),
        "symLibTable": str(sym_table_path),
        "fpLibTable": str(fp_table_path),
        "projectRoot": str(project_root),
        "moduleName": module_name,
        "kicadVersion": DEFAULT_KICAD_MAJOR_VERSION,
        "symbolLibraries": sorted(symbol_files.keys()),
        "footprintLibraries": sorted(footprint_dirs.keys()),
        "missingFootprints": missing,
        "placeholderFootprints": placeholders,
    }
