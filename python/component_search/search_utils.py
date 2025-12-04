from __future__ import annotations

import re
from typing import Dict, Optional, Sequence

from .constants import _IMPORTANT_ATTRIBUTE_KEYS, _STOPWORD_TOKENS


def _fts_token(term: str, *, prefix: bool = True) -> str:
    cleaned = term.replace('"', "").strip()
    if not cleaned:
        return ""

    if "-" in cleaned:
        cleaned = cleaned.replace("-", " ")

    normalized = re.sub(r"\s+", " ", cleaned)
    if not normalized:
        return ""

    if " " in normalized:
        return f'"{normalized}"'

    contains_punctuation = bool(re.search(r"[^\w]", normalized))
    if contains_punctuation:
        quoted = f'"{normalized}"'
        if prefix and len(normalized) >= 3 and not normalized.isdigit():
            return f"{quoted}*"
        return quoted

    if prefix and len(normalized) >= 3 and not normalized.isdigit():
        return f"{normalized}*"

    return normalized


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
