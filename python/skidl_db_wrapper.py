"""
DB-backed wrapper that feeds SKiDL libraries with symbol S-expressions stored in SQLite.

Usage:
    from python.skidl_db_wrapper import Part
    r1 = Part("Device", "R", value="10K")

The wrapper keeps the familiar SKiDL API, but when a requested library/name pair
exists in the component database it injects a virtual SchLib populated from the
stored S-expression instead of forcing SKiDL to scan .kicad_sym files.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import site
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# -----------------------------------------------------------------------------
# Make sure the pip-installed SKiDL package is imported even when running from
# a source checkout that also contains a ``skidl`` directory. We simply push
# every site-packages path to the front of sys.path once and then rely on the
# standard import machinery.
# -----------------------------------------------------------------------------
try:
    for _path in reversed(site.getsitepackages()):
        if _path in sys.path:
            sys.path.insert(0, sys.path.pop(sys.path.index(_path)))
        else:
            sys.path.insert(0, _path)
except Exception:  # pragma: no cover - extremely defensive.
    pass

import skidl  
from skidl.schlib import SchLib  
from skidl.part import Part as _SkidlPart  
from skidl.part import LIBRARY as _DEST_LIBRARY  
from skidl.tools.kicad9.lib import Sexp  

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LIB_CONFIG = PROJECT_ROOT / "config" / "library-paths.json"
DEFAULT_DB_PATH = PROJECT_ROOT / "symbol_lib" / "kicad_symbols.sqlite3"


def _resolve_config_path(entry: str) -> Optional[Path]:
    """Resolve a path entry from the library config file."""
    if not entry:
        return None
    try:
        path_obj = Path(entry).expanduser()
        if not path_obj.is_absolute():
            path_obj = (PROJECT_ROOT / path_obj).resolve()
        return path_obj
    except Exception as exc:
        logger.warning("Invalid library path %s: %s", entry, exc)
        return None


class SymbolDatabase:
    """
    Lazy SQLite-backed symbol provider.

    The DB is expected to expose a ``symbol_index`` table with (library, mpn, sexp).
    """

    def __init__(
        self,
        config_path: Path = DEFAULT_LIB_CONFIG,
        default_db: Path = DEFAULT_DB_PATH,
    ) -> None:
        self._config_path = config_path
        self._default_db = default_db
        self._db_paths: Optional[List[Path]] = None
        self._connections: Dict[Path, sqlite3.Connection] = {}
        self._sexp_cache: Dict[Tuple[str, str], Optional[str]] = {}

    # ------------------------------------------------------------------ #
    # Configuration helpers.
    # ------------------------------------------------------------------ #
    def _collect_db_entry(self, entry: str, paths: List[Path]) -> None:
        resolved = _resolve_config_path(entry)
        if resolved and resolved not in paths:
            paths.append(resolved)

    def _load_configured_paths(self) -> List[Path]:
        paths: List[Path] = []
        if self._config_path.exists():
            try:
                config_data = json.loads(self._config_path.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("Failed to parse %s: %s", self._config_path, exc)
            else:
                db_entry = config_data.get("symbolDbPath")
                if isinstance(db_entry, str):
                    self._collect_db_entry(db_entry, paths)
                db_list = config_data.get("symbolDbPaths")
                if isinstance(db_list, list):
                    for entry in db_list:
                        if isinstance(entry, str):
                            self._collect_db_entry(entry, paths)
                extra_search_paths = config_data.get("symbolSearchPaths", [])
                if isinstance(extra_search_paths, list):
                    for entry in extra_search_paths:
                        if not isinstance(entry, str):
                            continue
                        lowered = entry.strip().lower()
                        if lowered.endswith((".db", ".sqlite", ".sqlite3")):
                            self._collect_db_entry(entry, paths)
        if not paths:
            paths.append(self._default_db)
        return paths

    @property
    def db_paths(self) -> List[Path]:
        if self._db_paths is None:
            self._db_paths = self._load_configured_paths()
        return self._db_paths

    # ------------------------------------------------------------------ #
    # DB querying.
    # ------------------------------------------------------------------ #
    def _get_connection(self, path: Path) -> Optional[sqlite3.Connection]:
        if not path.exists():
            return None
        conn = self._connections.get(path)
        if conn:
            return conn
        try:
            conn = sqlite3.connect(str(path))
            conn.row_factory = sqlite3.Row
        except Exception as exc:
            logger.warning("Unable to open symbol DB %s: %s", path, exc)
            return None
        self._connections[path] = conn
        return conn

    def fetch_symbol_sexp(self, library: str, symbol: str) -> Optional[str]:
        cache_key = (library, symbol)
        if cache_key in self._sexp_cache:
            return self._sexp_cache[cache_key]
        for db_path in self.db_paths:
            conn = self._get_connection(db_path)
            if conn is None:
                continue
            try:
                cursor = conn.execute(
                    "SELECT sexp FROM symbol_index WHERE library = ? AND mpn = ?",
                    (library, symbol),
                )
                row = cursor.fetchone()
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning(
                    "Symbol query failed for %s:%s in %s: %s",
                    library,
                    symbol,
                    db_path,
                    exc,
                )
                continue
            if row:
                sexp_text = row["sexp"] if isinstance(row, sqlite3.Row) else row[0]
                self._sexp_cache[cache_key] = sexp_text
                return sexp_text
        self._sexp_cache[cache_key] = None
        return None


class DatabaseSchLib:
    """Minimal SchLib-like adapter that lazily populates parts from the symbol DB."""

    def __init__(self, name: str, symbol_db: SymbolDatabase, tool: Optional[str]) -> None:
        self.name = name
        self._symbol_db = symbol_db
        self._tool = tool or getattr(skidl, 'config').tool
        self._schlib = SchLib(tool=self._tool)
        self._parts: Dict[str, object] = {}

    def ensure_part(self, symbol_name: str) -> bool:
        if symbol_name in self._parts:
            return True
        sexp_text = self._symbol_db.fetch_symbol_sexp(self.name, symbol_name)
        if not sexp_text:
            return False
        try:
            sexp_obj = Sexp(sexp_text)
        except Exception as exc:
            logger.error(
                "Failed to parse symbol %s:%s into S-expression: %s",
                self.name,
                symbol_name,
                exc,
            )
            return False
        part = _SkidlPart(
            part_defn=sexp_obj,
            tool=self._tool,
            dest=_DEST_LIBRARY,
            filename=str(self.name),
            name=symbol_name,
        )
        self._schlib.add_parts(part)
        self._parts[symbol_name] = self._schlib[symbol_name]
        return True

    def __getitem__(self, symbol_name: str):
        if not self.ensure_part(symbol_name):
            raise KeyError(f"Symbol {symbol_name} not found in {self.name}.")
        return self._schlib[symbol_name]


class DatabaseLibraryRegistry:
    """Caches DatabaseSchLib objects for reuse across Part calls."""

    def __init__(self, symbol_db: SymbolDatabase) -> None:
        self._symbol_db = symbol_db
        self._libraries: Dict[Tuple[str, str], DatabaseSchLib] = {}

    def resolve(self, lib_name: str, symbol_name: str, tool: Optional[str]) -> Optional[DatabaseSchLib]:
        tool_name = tool or getattr(skidl, 'config').tool
        key = (lib_name, tool_name)
        db_lib = self._libraries.get(key)
        if db_lib is None:
            db_lib = DatabaseSchLib(lib_name, self._symbol_db, tool_name)
            self._libraries[key] = db_lib
        if db_lib.ensure_part(symbol_name):
            return db_lib
        return None


_SYMBOL_DB = SymbolDatabase()
_LIB_REGISTRY = DatabaseLibraryRegistry(_SYMBOL_DB)


def Part(*args, **kwargs):
    """
    Proxy that keeps SKiDL's original API but injects DB-backed libraries.

    Args mirror ``skidl.Part``. When ``lib`` is a string and the requested
    symbol exists in the SQLite DB, a virtual SchLib is spliced in so SKiDL
    never touches KiCad symbol files. Otherwise this simply forwards the call.
    """

    args_list = list(args)
    lib = kwargs.get("lib")
    name = kwargs.get("name")

    if len(args_list) >= 1:
        lib = args_list[0]
    if len(args_list) >= 2:
        name = args_list[1]

    if isinstance(lib, str) and isinstance(name, str):
        db_lib = _LIB_REGISTRY.resolve(lib, name, kwargs.get("tool"))
        if db_lib:
            if len(args_list) >= 1:
                args_list[0] = db_lib
            else:
                kwargs["lib"] = db_lib
    return _SkidlPart(*tuple(args_list), **kwargs)


__all__ = ["Part", "SymbolDatabase", "DatabaseLibraryRegistry"]
