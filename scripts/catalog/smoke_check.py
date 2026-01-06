#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
PYTHON_ROOT = REPO_ROOT / "python"
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from kicad_catalog.config import load_catalog_paths, resolve_repo_root
from kicad_catalog.sqlite import connect_sqlite


def _list_sqlite_objects(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
    ).fetchall()
    return {str(r[0]) for r in rows if r and r[0]}


def _check_required_objects(
    *,
    conn: sqlite3.Connection,
    required: Sequence[str],
) -> List[str]:
    present = _list_sqlite_objects(conn)
    return [name for name in required if name not in present]


def _try_scalar(conn: sqlite3.Connection, sql: str, args: Tuple[Any, ...] = ()) -> Optional[Any]:
    try:
        row = conn.execute(sql, args).fetchone()
    except sqlite3.Error:
        return None
    if not row:
        return None
    return row[0]


def check_component_db(path: Path) -> Dict[str, Any]:
    out: Dict[str, Any] = {"path": str(path)}
    if not path.exists():
        out.update({"ok": False, "error": "missing_db"})
        return out

    conn = connect_sqlite(path, readonly=True)
    try:
        missing = _check_required_objects(
            conn=conn,
            required=[
                "components",
                "categories",
                "v_components_search",
                "v_components_search_filtered_fts",
            ],
        )
        sample = _try_scalar(conn, "SELECT lcsc FROM components LIMIT 1")
        out.update(
            {
                "ok": len(missing) == 0,
                "missingObjects": missing,
                "sampleLcsc": sample,
            }
        )
        return out
    finally:
        conn.close()


def check_symbol_db(path: Path) -> Dict[str, Any]:
    out: Dict[str, Any] = {"path": str(path)}
    if not path.exists():
        out.update({"ok": False, "error": "missing_db"})
        return out

    conn = connect_sqlite(path, readonly=True)
    try:
        missing = _check_required_objects(conn=conn, required=["symbol_index"])
        sample = _try_scalar(
            conn,
            "SELECT mpn FROM symbol_index LIMIT 1",
        )
        out.update(
            {
                "ok": len(missing) == 0,
                "missingObjects": missing,
                "sampleMpn": sample,
            }
        )
        return out
    finally:
        conn.close()


def check_footprint_db(path: Path) -> Dict[str, Any]:
    out: Dict[str, Any] = {"path": str(path)}
    if not path.exists():
        out.update({"ok": False, "error": "missing_db"})
        return out

    conn = connect_sqlite(path, readonly=True)
    try:
        missing = _check_required_objects(conn=conn, required=["footprint_index"])
        sample = _try_scalar(conn, "SELECT name FROM footprint_index LIMIT 1")
        out.update(
            {
                "ok": len(missing) == 0,
                "missingObjects": missing,
                "sampleFootprint": sample,
            }
        )
        return out
    finally:
        conn.close()


def check_spice_model_db(path: Path) -> Dict[str, Any]:
    out: Dict[str, Any] = {"path": str(path)}
    if not path.exists():
        out.update({"ok": True, "note": "db_missing_ok"})
        return out

    conn = connect_sqlite(path, readonly=True)
    try:
        missing = _check_required_objects(conn=conn, required=["part_models"])
        sample = _try_scalar(conn, "SELECT name FROM part_models LIMIT 1")
        out.update(
            {
                "ok": len(missing) == 0,
                "missingObjects": missing,
                "sampleModelName": sample,
            }
        )
        return out
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Non-destructive smoke checks for KiCAD_MCP catalog databases (read-only)."
    )
    parser.add_argument(
        "--allow-missing",
        action="store_true",
        help="Exit 0 even if databases/objects are missing (still reports in JSON).",
    )
    args = parser.parse_args()

    repo_root = resolve_repo_root()
    paths = load_catalog_paths(repo_root=repo_root)

    results: Dict[str, Any] = {
        "componentDb": check_component_db(paths.component_db),
        "symbolDbs": [check_symbol_db(path) for path in paths.symbol_dbs],
        "footprintDbs": [check_footprint_db(path) for path in paths.footprint_dbs],
        "spiceModelDb": check_spice_model_db(paths.spice_model_db),
    }

    ok = True
    if not results["componentDb"]["ok"]:
        ok = False
    if any(not entry["ok"] for entry in results["symbolDbs"]):
        ok = False
    if any(not entry["ok"] for entry in results["footprintDbs"]):
        ok = False
    if not results["spiceModelDb"]["ok"]:
        ok = False

    results["ok"] = ok
    print(json.dumps(results, indent=2))

    if args.allow_missing:
        return 0
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
