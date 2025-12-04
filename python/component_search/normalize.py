from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Iterable, List

from .constants import (
    _REGEX_REPLACEMENTS,
    _STOPWORD_TOKENS,
    _TOKEN_EXPANSIONS,
    _VALUE_UNIT_RE,
)


@dataclass(slots=True)
class NormalizedQueryTokens:
    """Tokens derived from the user query."""

    tokens: List[str]
    primary_tokens: List[str]
    numeric_tokens: List[str]


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
