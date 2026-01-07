#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from urllib.parse import urlparse
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
PYTHON_ROOT = REPO_ROOT / "python"
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from db_tools.postgres import connect_postgres
from db_tools.settings import get_settings, get_sqlite_db_path
from db_tools.sqlite import connect_sqlite


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


def check_unified_sqlite_db(path: Path) -> Dict[str, Any]:
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
                "symbol_index",
                "footprint_index",
                "part_models",
            ],
        )

        out.update(
            {
                "ok": len(missing) == 0,
                "missingObjects": missing,
                "sampleLcsc": _try_scalar(conn, "SELECT lcsc FROM components LIMIT 1"),
                "sampleMpn": _try_scalar(conn, "SELECT mpn FROM symbol_index LIMIT 1"),
                "sampleFootprint": _try_scalar(conn, "SELECT name FROM footprint_index LIMIT 1"),
                "sampleModelName": _try_scalar(conn, "SELECT name FROM part_models LIMIT 1"),
            }
        )
        return out
    finally:
        conn.close()


def _try_scalar_pg(cur: Any, sql: str, args: Tuple[Any, ...] = ()) -> Optional[Any]:
    try:
        cur.execute(sql, args)
        row = cur.fetchone()
    except Exception:  # noqa: BLE001
        return None
    if not row:
        return None
    if isinstance(row, dict):
        return next(iter(row.values()), None)
    return row[0]


def _pg_missing(cur: Any, required: Sequence[str]) -> List[str]:
    missing: List[str] = []
    for name in required:
        cur.execute("SELECT to_regclass(%s)", (name,))
        row = cur.fetchone()
        value = row.get("to_regclass") if isinstance(row, dict) else row[0]
        if value is None:
            missing.append(name)
    return missing


def check_unified_postgres_db(dsn: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {"dsn": dsn}

    required = [
        "components",
        "categories",
        "symbol_index",
        "footprint_index",
        "part_models",
        "mpn_model_map",
    ]

    try:
        with connect_postgres(dsn, readonly=True) as conn:
            with conn.cursor() as cur:
                missing = _pg_missing(cur, required)
                out.update(
                    {
                        "ok": len(missing) == 0,
                        "missingObjects": missing,
                        "sampleLcsc": _try_scalar_pg(cur, "SELECT lcsc FROM components LIMIT 1"),
                        "sampleMpn": _try_scalar_pg(cur, "SELECT mpn FROM symbol_index LIMIT 1"),
                        "sampleFootprint": _try_scalar_pg(
                            cur, "SELECT name FROM footprint_index LIMIT 1"
                        ),
                        "sampleModelName": _try_scalar_pg(cur, "SELECT name FROM part_models LIMIT 1"),
                    }
                )
    except Exception as exc:  # noqa: BLE001
        out.update({"ok": False, "error": str(exc)})

    return out


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

    scheme = urlparse(get_settings().db_url).scheme.lower()

    results: Dict[str, Any] = {}
    if scheme == "sqlite":
        db_path = get_sqlite_db_path(for_write=False)
        results["db"] = check_unified_sqlite_db(db_path)
    elif scheme in {"postgres", "postgresql"}:
        results["db"] = check_unified_postgres_db(get_settings().db_url)
    else:
        results["db"] = {"ok": False, "error": f"Unsupported db_url scheme: {scheme!r}"}

    ok = True
    if not results["db"]["ok"]:
        ok = False

    results["ok"] = ok
    print(json.dumps(results, indent=2))

    if args.allow_missing:
        return 0
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
