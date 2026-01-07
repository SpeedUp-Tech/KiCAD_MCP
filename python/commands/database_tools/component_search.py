"""
Command adapter for the component search functionality.

This module keeps the MCP command wiring isolated from the portable search
logic so the feature can be moved to another project without touching unrelated
files.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from db_tools.client import DbToolsClient

logger = logging.getLogger("kicad_interface")


class ComponentSearchCommands:
    """Expose component search as MCP commands."""

    def __init__(self) -> None:
        self._client = DbToolsClient.from_settings()

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

        try:
            result = self._client.search_mpn_part(
                query,
                limit=limit,
                offset=offset,
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
            return self._client.search_datasheet(
                mpn,
                library,
            )
        except Exception as exc:  # pragma: no cover - defensive guard
            logger.error("Unhandled error during datasheet lookup: %s", exc, exc_info=True)
            return {
                "success": False,
                "message": "Datasheet lookup encountered an unexpected error",
                "errorDetails": str(exc),
            }

    def add_searchable_part(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Add a component entry that will be searchable via search_mpn_part().

        Required parameters:
            mpn: Manufacturer part number (primary search field)
            library: Library/category name for grouping
            package: Physical package type (e.g., "SOT-23", "QFN-24")

        Optional parameters:
            footprint: KiCad footprint path
            datasheet: URL to datasheet PDF
            attributes: Dict of searchable specs (key-value pairs)
        """
        mpn = params.get("mpn")
        library = params.get("library")
        package = params.get("package")
        footprint = params.get("footprint", "")
        datasheet = params.get("datasheet", "")
        attributes = params.get("attributes")

        if not isinstance(mpn, str) or not mpn.strip():
            return {
                "success": False,
                "message": "mpn parameter is required and must be a non-empty string",
                "errorDetails": "The 'mpn' parameter was missing or empty",
            }

        if not isinstance(library, str) or not library.strip():
            return {
                "success": False,
                "message": "library parameter is required and must be a non-empty string",
                "errorDetails": "The 'library' parameter was missing or empty",
            }

        if not isinstance(package, str) or not package.strip():
            return {
                "success": False,
                "message": "package parameter is required and must be a non-empty string",
                "errorDetails": "The 'package' parameter was missing or empty",
            }

        # Validate attributes if provided
        if attributes is not None and not isinstance(attributes, dict):
            return {
                "success": False,
                "message": "attributes must be a dict if provided",
                "errorDetails": "The 'attributes' parameter was not a dictionary",
            }

        try:
            request: Dict[str, Any] = {
                "mpn": mpn,
                "library": library,
                "package": package,
                "footprint": footprint if isinstance(footprint, str) else "",
                "datasheet": datasheet if isinstance(datasheet, str) else "",
                "attributes": attributes,
            }
            return self._client.add_searchable_part(request)
        except Exception as exc:  # pragma: no cover - defensive guard
            logger.error("Unhandled error adding searchable part: %s", exc, exc_info=True)
            return {
                "success": False,
                "message": "Failed to add searchable part",
                "errorDetails": str(exc),
            }
