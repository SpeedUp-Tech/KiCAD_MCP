"""
Command adapter for the component search functionality.

This module keeps the MCP command wiring isolated from the portable search
logic so the feature can be moved to another project without touching unrelated
files.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional, Union, List

from component_search import (
    ComponentSearchConfig,
    search_datasheet as lookup_datasheet,
    search_mpn_part as execute_search,
)

logger = logging.getLogger("kicad_interface")


class ComponentSearchCommands:
    """Expose component search as MCP commands."""

    def __init__(self, default_db_path: Optional[Union[str, Path]] = None) -> None:
        if default_db_path:
            resolved = Path(default_db_path).expanduser()
            self._config = ComponentSearchConfig(db_path=resolved)
        else:
            self._config = ComponentSearchConfig()

    def _resolve_db_path(self, override: Optional[Union[str, Path]]) -> Optional[Path]:
        if override:
            return Path(override).expanduser()
        return None

    def search_mpn_part(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Search the component catalog using free-form part specs.

        Accepts natural-language queries plus optional limit/offset arguments
        and returns the best matching producable parts with datasheets/mpn/family/class and key attributes ranked via SQLite FTS5/BM25.
        """

        query = params.get("query")
        if not isinstance(query, str):
            return {
                "success": False,
                "message": "Query parameter is required and must be a string",
                "errorDetails": "The 'query' parameter was missing or not a string",
            }

        limit = params.get("limit")
        offset = params.get("offset", 0)
        db_path = params.get("dbPath")

        try:
            result = execute_search(
                query,
                limit=limit,
                offset=offset,
                db_path=self._resolve_db_path(db_path),
                config=self._config,
            )
            if isinstance(result, dict) and result.get("results"):
                enriched: List[Dict[str, Any]] = []
                results_list = result["results"]
                if not isinstance(results_list, list):
                    return result
                for item in results_list:
                    record: Dict[str, Any] = dict(item)
                    record["footprint"] = item.get("footprint") or ""
                    enriched.append(record)
                result["results"] = enriched
            return result
        except Exception as exc:  # pragma: no cover - defensive guard
            logger.error("Unhandled error during component search: %s", exc, exc_info=True)
            return {
                "success": False,
                "message": "Component search encountered an unexpected error",
                "errorDetails": str(exc),
            }

    def search_datasheet(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Return datasheet URL and specs for an exact MPN/library match that has a KiCad symbol."""

        mpn = params.get("mpn")
        library = params.get("library")
        db_path = params.get("dbPath")

        if not isinstance(mpn, str) or not mpn.strip():
            return {
                "success": False,
                "message": "mpn parameter is required and must be a string",
                "errorDetails": "The 'mpn' parameter was missing or empty",
            }

        if not isinstance(library, str):
            return {
                "success": False,
                "message": "library parameter is required and must be a string",
                "errorDetails": "The 'library' parameter was missing or invalid",
            }

        try:
            return lookup_datasheet(
                mpn,
                library,
                db_path=self._resolve_db_path(db_path),
                config=self._config,
            )
        except Exception as exc:  # pragma: no cover - defensive guard
            logger.error("Unhandled error during datasheet lookup: %s", exc, exc_info=True)
            return {
                "success": False,
                "message": "Datasheet lookup encountered an unexpected error",
                "errorDetails": str(exc),
            }
