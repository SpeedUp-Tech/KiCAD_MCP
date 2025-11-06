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
import os
import re
import sqlite3
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple, Union

logger = logging.getLogger("component_search")


def _resolve_repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _default_db_path() -> Path:
    env_path = os.environ.get("JLCPCB_DB_PATH")
    if env_path:
        return Path(env_path).expanduser()
    return _resolve_repo_root() / "part_lib" / "jlcpcb-components.sqlite3"


@dataclass(slots=True)
class ComponentSearchConfig:
    """Configuration controlling the search behaviour."""

    db_path: Path = field(default_factory=_default_db_path)
    default_limit: int = 25
    max_limit: int = 100
    highlight_radius: int = 80
    candidate_multiplier: float = 1.8  # widen the net without exploding work


@dataclass(slots=True)
class SearchMPNResult:
    """Represents a single search result."""

    lcsc: str
    mpn: Optional[str]
    package: Optional[str]
    family: Optional[str]
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
            "lcsc": self.lcsc,
            "mpn": self.mpn,
            "package": self.package,
            "family": self.family,
            "class": self.class_name,
            "datasheet": self.datasheet,
            "joints": self.joints,
            "score": self.score,
            "attributes": self.attributes,
        }


@dataclass(slots=True)
class NormalizedQueryTokens:
    """Tokens derived from the user query."""

    tokens: List[str]
    primary_tokens: List[str]
    numeric_tokens: List[str]


_REGEX_REPLACEMENTS: Tuple[Tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"pmosfet", re.IGNORECASE), "p channel mosfet"),
    (re.compile(r"p[\s\-]*mos\b", re.IGNORECASE), "p channel mosfet"),
    (re.compile(r"nmosfet", re.IGNORECASE), "n channel mosfet"),
    (re.compile(r"n[\s\-]*mos\b", re.IGNORECASE), "n channel mosfet"),
    (re.compile(r"rds[\s_\-]*on", re.IGNORECASE), "rds on"),
    (re.compile(r"v[\s_\-]*dss", re.IGNORECASE), "vdss"),
)

_TOKEN_EXPANSIONS: Dict[str, Tuple[str, ...]] = {
    "pmos": ("p-channel", "mosfet"),
    "p": ("p-channel",),
    "p-channel": ("mosfet",),
    "nmos": ("n-channel", "mosfet"),
    "n": ("n-channel",),
    "n-channel": ("mosfet",),
    "rds": ("resistance",),
    "vdss": ("voltage",),
    "id": ("current",),
}

_STOPWORD_TOKENS: frozenset[str] = frozenset(
    [
        "transistor",
        "channel",
        "type",
        "resistance",
        "voltage",
        "current",
        "continuous",
        "drain",
        "source",
        "gate",
        "and",
        "or",
        "the",
        "of",
        "for",
    ]
)

_EXCLUDED_FAMILIES: Tuple[str, ...] = ("Resistors", "Capacitors")

_IMPORTANT_ATTRIBUTE_KEYS: Tuple[str, ...] = (
    "Type",
    "Drain Source Voltage (Vdss)",
    "Drain Source On Resistance (RDS(on)@Vgs,Id)",
    "Continuous Drain Current (Id)",
    "Power Dissipation (Pd)",
    "Gate Threshold Voltage (Vgs(th)@Id)",
)

_VALUE_UNIT_RE = re.compile(r"^([0-9]+(?:\.[0-9]+)?)([a-z\u00b5\u03bc\u03a9]+)$", re.IGNORECASE)


def _replace_symbols(text: str) -> str:
    if not text:
        return ""
    return (
        text.replace("μ", "u")
        .replace("µ", "u")
        .replace("Ω", "ohm")
        .replace("ω", "ohm")
        .replace("±", " ")
    )


def _normalize_text_for_match(text: str) -> str:
    return _replace_symbols(text).lower()


def _is_numeric_token(token: str) -> bool:
    return any(ch.isdigit() for ch in token)


def _numeric_token_variants(token: str) -> List[str]:
    base = token.lower().strip()
    variants = {base}
    compact = base.replace(" ", "").replace("_", "")
    variants.add(compact)
    variants.add(compact.replace("-", ""))
    variants.add(compact.replace(",", ""))

    variants.add(re.sub(r"(?<=\d)([a-z\u00b5\u03bc\u03a9]+)", r" \1", compact))
    variants.add(re.sub(r"[^0-9a-zA-Z]+", " ", compact))

    variants.add(compact.replace("ω", "ohm").replace("Ω", "ohm"))

    letters_only = re.sub(r"[^a-zA-Z]+", "", compact)
    if letters_only:
        variants.add(letters_only)

    cleaned = {val.strip() for val in variants if val.strip()}
    return list(cleaned)


def _normalize_query_text(query: str) -> str:
    text = query.strip()
    for pattern, replacement in _REGEX_REPLACEMENTS:
        text = pattern.sub(replacement, text)
    text = _replace_symbols(text)
    text = re.sub(r"[<>=,@:;\\/\|\[\]\{\}\(\)\?\!]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip().lower()


def _token_variants(token: str) -> Iterable[str]:
    yield token
    expansion = _TOKEN_EXPANSIONS.get(token)
    if expansion:
        for item in expansion:
            yield item.lower()

    match = _VALUE_UNIT_RE.match(token)
    if match:
        value, unit = match.groups()
        yield value
        yield unit.lower()

    if "ohm" not in token and ("ω" in token or "Ω" in token):
        yield token.replace("ω", "ohm").replace("Ω", "ohm")

    if token.endswith(("v", "a", "w")) and len(token) > 1:
        yield token[:-1]
        yield token[-1]


def _normalize_query(query: str) -> NormalizedQueryTokens:
    normalized = _normalize_query_text(query)
    if not normalized:
        return NormalizedQueryTokens(tokens=[], primary_tokens=[], numeric_tokens=[])

    base_tokens = [token for token in normalized.split(" ") if token]

    tokens: List[str] = []
    numeric_tokens: List[str] = []
    primary_tokens: List[str] = []

    seen_tokens: set[str] = set()
    seen_primary: set[str] = set()

    def add_token(value: str) -> None:
        if not value:
            return
        if value not in seen_tokens:
            tokens.append(value)
            seen_tokens.add(value)

    for base in base_tokens:
        add_token(base)

        if _is_numeric_token(base):
            if base not in numeric_tokens:
                numeric_tokens.append(base)
        else:
            if (
                len(base) >= 3
                and base not in _STOPWORD_TOKENS
                and "_" not in base
                and "-" not in base
                and base.isalpha()
            ):
                if base not in seen_primary:
                    primary_tokens.append(base)
                    seen_primary.add(base)

        for variant in _token_variants(base):
            cleaned = variant.strip()
            if not cleaned:
                continue
            add_token(cleaned)
            if _is_numeric_token(cleaned):
                if cleaned not in numeric_tokens:
                    numeric_tokens.append(cleaned)
            else:
                if (
                    len(cleaned) >= 3
                    and cleaned not in _STOPWORD_TOKENS
                    and "_" not in cleaned
                    and "-" not in cleaned
                    and cleaned.isalpha()
                ):
                    if cleaned not in seen_primary:
                        primary_tokens.append(cleaned)
                        seen_primary.add(cleaned)

    numeric_variant_tokens: set[str] = set()
    for numeric in numeric_tokens:
        numeric_variant_tokens.update(
            variant
            for variant in _numeric_token_variants(numeric)
            if variant and not _is_numeric_token(variant)
        )

    filtered_primary = [token for token in primary_tokens if token not in numeric_variant_tokens]

    return NormalizedQueryTokens(
        tokens=tokens,
        primary_tokens=filtered_primary,
        numeric_tokens=numeric_tokens,
    )


def _fts_token(term: str, *, prefix: bool = True) -> str:
    cleaned = term.replace('"', "").strip()
    if not cleaned:
        return ""

    if "-" in cleaned:
        cleaned = cleaned.replace("-", " ")

    if " " in cleaned:
        normalized = re.sub(r"\s+", " ", cleaned)
        return f'"{normalized}"'

    if prefix and len(cleaned) >= 3 and not cleaned.isdigit():
        return f"{cleaned}*"

    return cleaned


def _compose_fts_query(primary: Sequence[str], optional: Sequence[str]) -> str:
    tokens = [token for term in optional if (token := _fts_token(term, prefix=True))]
    if not tokens:
        return "*"
    return " OR ".join(tokens)


def _extract_key_attributes(
    attributes: Dict[str, object], tokens: Sequence[str], limit: int = 8
) -> Dict[str, object]:
    if not attributes:
        return {}

    selected: Dict[str, object] = {}
    for key in _IMPORTANT_ATTRIBUTE_KEYS:
        if key in attributes and attributes[key] not in (None, "", "null"):
            selected[key] = attributes[key]
            if len(selected) >= limit:
                return selected

    meaningful_tokens = [
        item for item in tokens if len(item) >= 3 and item not in _STOPWORD_TOKENS
    ]
    for key, value in attributes.items():
        if key in selected:
            continue
        key_l = key.lower()
        value_text = str(value).lower()
        if any(token in key_l or token in value_text for token in meaningful_tokens):
            selected[key] = value
            if len(selected) >= limit:
                break

    return selected


def _value_or_none(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


@lru_cache(maxsize=4)
def _get_connection(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = OFF")
    return conn


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

    sql = """
        WITH ranked AS (
            SELECT
                lcsc,
                bm25(v_components_search_fts) AS score
            FROM v_components_search_fts
            WHERE v_components_search_fts MATCH ?
            ORDER BY score ASC
            LIMIT ?
        )
        SELECT
            v.lcsc AS lcsc,
            v.mpn AS mpn,
            v.package AS package,
            v.joints AS joints,
            v.datasheet AS datasheet,
            v.family AS family,
            v.class AS class,
            v.attributes AS attributes,
            v.specs AS specs,
            ranked.score AS score
        FROM ranked
        JOIN v_components_search v
            ON v.lcsc = ranked.lcsc
        WHERE v.symbol_lib = 1
          AND (v.family IS NULL OR v.family NOT IN (?, ?))
        ORDER BY ranked.score ASC, v.lcsc ASC
        LIMIT ? OFFSET ?
    """

    try:
        rows = conn.execute(
            sql,
            (
                fts_query,
                candidate_limit,
                *_EXCLUDED_FAMILIES,
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
                    row["family"] or "",
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
            family=row["family"],
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
