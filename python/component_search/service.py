"""
Free-form component search backed by SQLite FTS5.

This module accepts natural-language spec strings (for example,
``"P-MOSFET 30V Rds_on<=20mΩ Id>=10A"``), normalizes them, issues an FTS query
against ``v_components_search_fts``, and ranks the candidates using light
numeric matching heuristics. No field-specific mappings are required—everything
operates on the raw text already present in the database.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

from .constants import FILTERED_FTS_TABLE
from .db import _default_db_path, _get_connection
from .normalize import (
    NormalizedQueryTokens,
    _normalize_query,
    _normalize_text_for_match,
    _numeric_token_variants,
)
from .search_utils import _compose_fts_query, _extract_key_attributes, _value_or_none

logger = logging.getLogger("component_search")


@dataclass(slots=True)
class ComponentSearchConfig:
    """Configuration controlling the search behaviour."""

    db_path: Path = field(default_factory=_default_db_path)
    default_limit: int = 10
    max_limit: int = 100
    highlight_radius: int = 80
    candidate_multiplier: float = 1.8  # widen the net without exploding work


@dataclass(slots=True)
class SearchMPNResult:
    """Represents a single search result."""

    lcsc: str
    mpn: Optional[str]
    package: Optional[str]
    footprint: Optional[str]
    library: Optional[str]
    class_name: Optional[str]
    datasheet: Optional[str]
    joints: Optional[int]
    score: Optional[float]
    snippet: Optional[str]
    attributes: Dict[str, object]
    key_attributes: Dict[str, object]
    numeric_matches: List[str]

    def to_dict(self) -> Dict[str, object]:
        return {
            "mpn": self.mpn,
            "package": self.package,
            "footprint": self.footprint,
            "library": self.library,
            "class": self.class_name,
            "datasheet": self.datasheet,
            "joints": self.joints,
            "score": self.score,
            "attributes": self.attributes,
        }


def _normalize_library_input(library: Optional[str]) -> Tuple[str, List[str]]:
    """Normalize a library name and return possible match values."""

    raw = (library or "").strip()
    if not raw:
        return ("", [""])

    if raw.lower() == "uncategorized":
        return ("Uncategorized", ["", "Uncategorized"])

    return (raw, [raw])


def search_datasheet(
    mpn: str,
    library: str,
    *,
    db_path: Optional[Union[Path, str]] = None,
    config: Optional[ComponentSearchConfig] = None,
) -> Dict[str, object]:
    """Return the datasheet URL and specs for an exact MPN/library pair with a KiCad symbol."""

    if not isinstance(mpn, str) or not mpn.strip():
        return {
            "success": False,
            "message": "mpn parameter is required and must be a string",
            "errorDetails": "No valid MPN was provided",
        }

    if not isinstance(library, str):
        return {
            "success": False,
            "message": "library parameter is required and must be a string",
            "errorDetails": "Library name was missing or invalid",
        }

    normalized_mpn = mpn.strip()
    normalized_library, library_candidates = _normalize_library_input(library)

    settings = config or ComponentSearchConfig()
    resolved_db_path = Path(db_path).expanduser() if db_path else settings.db_path

    if not resolved_db_path.exists():
        message = f"Component database not found: {resolved_db_path}"
        logger.error(message)
        return {
            "success": False,
            "message": message,
            "errorDetails": "Ensure the database has been generated with setup_component_search.py",
        }

    conn = _get_connection(resolved_db_path)
    placeholders = ", ".join("?" for _ in library_candidates)

    sql = f"""
        SELECT
            datasheet,
            specs,
            mpn,
            COALESCE(library, '') AS library
        FROM v_components_search
        WHERE symbol_lib = 1
          AND lower(mpn) = lower(?)
          AND COALESCE(library, '') IN ({placeholders})
        ORDER BY lcsc ASC
        LIMIT 1
    """

    try:
        row = conn.execute(
            sql,
            (
                normalized_mpn,
                *library_candidates,
            ),
        ).fetchone()
    except sqlite3.Error as err:
        logger.error("SQLite error during datasheet lookup: %s", err)
        return {
            "success": False,
            "message": "Datasheet lookup failed",
            "errorDetails": str(err),
        }

    if not row:
        return {
            "success": False,
            "message": "Component not found",
            "errorDetails": (
                "No component with a symbol matched the provided MPN and library"
            ),
            "mpn": normalized_mpn,
            "library": normalized_library,
        }

    resolved_library = row["library"] or ""
    datasheet = row["datasheet"] or None
    specs = row["specs"] or ""

    return {
        "success": True,
        "mpn": row["mpn"] or normalized_mpn,
        "library": resolved_library or "Uncategorized",
        "datasheet": datasheet,
        "specs": specs,
        "dbPath": str(resolved_db_path),
    }


def search_mpn_part(
    query: str,
    *,
    limit: Optional[int] = None,
    offset: int = 0,
    db_path: Optional[Union[Path, str]] = None,
    config: Optional[ComponentSearchConfig] = None,
) -> Dict[str, object]:
    """
    Execute a free-form search using SQLite FTS5.
    """

    if not query or not query.strip():
        return {
            "success": False,
            "message": "Query text is required",
            "errorDetails": "The search query was empty",
        }

    settings = config or ComponentSearchConfig()
    resolved_db_path = Path(db_path).expanduser() if db_path else settings.db_path

    if not resolved_db_path.exists():
        message = f"Component database not found: {resolved_db_path}"
        logger.error(message)
        return {
            "success": False,
            "message": message,
            "errorDetails": "Ensure the database has been generated with setup_component_search.py",
        }

    normalized = _normalize_query(query)
    if not normalized.tokens:
        return {
            "success": False,
            "message": "Unable to derive search terms from query",
            "errorDetails": "After normalization, no tokens remained to search",
        }

    core_terms = normalized.primary_tokens or []
    optional_terms = normalized.tokens

    fts_query = _compose_fts_query(core_terms, optional_terms)

    requested_limit = limit if limit is not None else settings.default_limit
    try:
        requested_limit_int = int(requested_limit)
    except (TypeError, ValueError):
        requested_limit_int = settings.default_limit
    requested_limit_int = max(1, min(settings.max_limit, requested_limit_int))

    try:
        offset_int = int(offset)
    except (TypeError, ValueError):
        offset_int = 0
    offset_int = max(0, offset_int)

    candidate_limit = max(
        requested_limit_int + offset_int,
        int((requested_limit_int + offset_int) * settings.candidate_multiplier),
    )

    conn = _get_connection(resolved_db_path)

    sql = f"""
        WITH ranked AS (
            SELECT
                lcsc,
                bm25({FILTERED_FTS_TABLE}) AS score
            FROM {FILTERED_FTS_TABLE}
            WHERE {FILTERED_FTS_TABLE} MATCH ?
            ORDER BY score ASC, lcsc ASC
            LIMIT ?
        )
        SELECT
            v.lcsc AS lcsc,
            v.mpn AS mpn,
            v.package AS package,
            c.footprint AS footprint,
            v.joints AS joints,
            v.datasheet AS datasheet,
            v.library AS library,
            v.class AS class,
            v.attributes AS attributes,
            v.specs AS specs,
            ranked.score AS score
        FROM ranked
        JOIN v_components_search v
            ON v.lcsc = ranked.lcsc
        JOIN components c
            ON c.lcsc = ranked.lcsc
        ORDER BY ranked.score ASC, v.lcsc ASC
        LIMIT ? OFFSET ?
    """

    try:
        rows = conn.execute(
            sql,
            (
                fts_query,
                candidate_limit,
                requested_limit_int,
                offset_int,
            ),
        ).fetchall()
    except sqlite3.Error as err:
        logger.error("SQLite error during component search: %s", err)
        return {
            "success": False,
            "message": "Component search failed",
            "errorDetails": str(err),
        }

    numeric_variant_map = {
        token: _numeric_token_variants(token) for token in normalized.numeric_tokens
    }

    results: List[SearchMPNResult] = []

    for row in rows:
        attributes_json = row["attributes"] or ""
        try:
            raw_attributes = json.loads(attributes_json) if attributes_json else {}
        except (json.JSONDecodeError, TypeError):
            raw_attributes = {}

        specs_text = row["specs"] or ""
        search_blob = _normalize_text_for_match(
            " ".join(
                [
                    specs_text,
                    attributes_json or "",
                    row["mpn"] or "",
                    row["package"] or "",
                    row["library"] or "",
                    row["class"] or "",
                ]
            )
        )

        numeric_matches: List[str] = []
        for token, variants in numeric_variant_map.items():
            norm_variants = [variant for variant in variants if variant]
            if any(variant in search_blob for variant in norm_variants):
                numeric_matches.append(token)

        snippet = None
        if specs_text:
            sample = specs_text.strip()
            snippet = sample[: settings.highlight_radius * 2]

        score_val = row["score"]
        score = float(score_val) if score_val is not None else None

        result = SearchMPNResult(
            lcsc=str(row["lcsc"]),
            mpn=row["mpn"],
            package=row["package"],
            footprint=row["footprint"],
            library=row["library"],
            class_name=row["class"],
            datasheet=row["datasheet"],
            joints=row["joints"],
            score=score,
            snippet=_value_or_none(snippet),
            attributes=raw_attributes,
            key_attributes=_extract_key_attributes(raw_attributes, normalized.tokens),
            numeric_matches=sorted(set(numeric_matches)),
        )
        results.append(result)

    results.sort(
        key=lambda item: (
            len(item.numeric_matches),
            -(item.score if item.score is not None else 0.0),
        ),
        reverse=True,
    )

    # Post-process libraries as requested:
    # 1) For MPNs that appear multiple times in the search results, if some
    #    entries have library == "" and others have a non-empty library,
    #    drop the entries with an empty library.
    # 2) For MPNs that appear exactly once and have library == "",
    #    treat them as belonging to the "Uncategorized" library.
    if results:
        mpn_counts = Counter(item.mpn for item in results)

        # Determine which MPNs have at least one non-empty library entry.
        mpn_has_non_empty_library: Dict[Optional[str], bool] = {}
        for item in results:
            if not item.mpn:
                continue
            if mpn_counts[item.mpn] <= 1:
                continue
            lib = (item.library or "").strip()
            if lib:
                mpn_has_non_empty_library[item.mpn] = True

        # First pass: remove empty-library entries when there is at least one
        # non-empty entry for the same MPN and that MPN appears multiple times.
        filtered_results: List[SearchMPNResult] = []
        for item in results:
            mpn = item.mpn
            lib = (item.library or "").strip()
            if (
                mpn
                and mpn_counts.get(mpn, 0) > 1
                and mpn_has_non_empty_library.get(mpn, False)
                and not lib
            ):
                continue
            filtered_results.append(item)

        # Second pass: for MPNs that now appear exactly once and still have an
        # empty library, mark them as belonging to the "Uncategorized" library.
        if filtered_results:
            mpn_counts_after = Counter(item.mpn for item in filtered_results)
            for item in filtered_results:
                mpn = item.mpn
                if not mpn:
                    continue
                if mpn_counts_after.get(mpn, 0) == 1:
                    lib = (item.library or "").strip()
                    if not lib:
                        item.library = "Uncategorized"

        results = filtered_results

    return {
        "success": True,
        "query": query,
        "terms": normalized.tokens,
        "coreTerms": core_terms,
        "optionalTerms": optional_terms,
        "limit": requested_limit_int,
        "offset": offset_int,
        "count": len(results),
        "dbPath": str(resolved_db_path),
        "results": [item.to_dict() for item in results],
    }
