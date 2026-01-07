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

import logging
import sqlite3
import site
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from db_tools.settings import get_sqlite_db_path
from db_tools.sqlite import connect_sqlite

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


class SymbolDatabase:
    """
    Lazy SQLite-backed symbol provider.

    The DB is expected to expose a ``symbol_index`` table with (library, mpn, sexp).
    """

    def __init__(self) -> None:
        self._connections: Dict[Path, sqlite3.Connection] = {}
        self._sexp_cache: Dict[Tuple[str, str], Optional[str]] = {}

    @property
    def db_paths(self) -> List[Path]:
        # Re-evaluate each time so newly created working copies are picked up.
        return [get_sqlite_db_path(for_write=False)]

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
            conn = connect_sqlite(path, readonly=True)
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
        # Always use kicad9 for parsing database sexp, regardless of global skidl.config.tool
        self._tool = tool or 'kicad9'
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
        # Store the library name on the part for later retrieval by elk_graph_adapter
        part._db_library_name = self.name
        self._schlib.add_parts(part)
        self._parts[symbol_name] = self._schlib[symbol_name]
        # Also store library name on the cached part
        self._parts[symbol_name]._db_library_name = self.name
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
        # Always use kicad9 for database symbols, regardless of global skidl.config.tool
        tool_name = tool or 'kicad9'
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

    if not isinstance(lib, str):
        raise TypeError(
            f"Part 'lib' must be a string, got {type(lib).__name__}. "
        )
    if not isinstance(name, str):
        raise TypeError(
            f"Part 'name' must be a string, got {type(name).__name__}. "
        )
    
    db_lib = _LIB_REGISTRY.resolve(lib, name, kwargs.get("tool"))
    if db_lib is None:
        raise ValueError(
            f"Symbol '{name}' not found in library '{lib}'"
        )
    
    if len(args_list) >= 1:
        args_list[0] = db_lib
    else:
        kwargs["lib"] = db_lib
    # Force kicad9 tool for database-backed parts to ensure correct pin parsing
    if "tool" not in kwargs:
        kwargs["tool"] = "kicad9"
    
    return _SkidlPart(*tuple(args_list), **kwargs)


__all__ = ["Part", "SymbolDatabase", "DatabaseLibraryRegistry"]
