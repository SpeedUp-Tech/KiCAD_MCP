#!/usr/bin/env python3
"""
Generate a mapping between JLCPCB component MPNs and SPICE model names.

The script reads:
 - part_lib/jlcpcb-components.sqlite3 (view v_components_search)
 - spice_lib/spice_models.db (table models)

and produces a separate SQLite database that stores matched (mpn, model_name)
pairs together with metadata describing how the match was found.

The original databases are not modified.
"""

from __future__ import annotations

import argparse
import dataclasses
import logging
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Sequence, Set, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYTHON_ROOT = PROJECT_ROOT / "python"
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from kicad_catalog.sqlite import connect_sqlite
from kicad_catalog.workdir import resolve_read_db_path, resolve_write_db_path

# Suffixes that commonly appear on either models or MPNs and can be safely removed
# when searching for a base part number. The list is intentionally conservative
# and only contains tokens that commonly encode packaging or vendor decorations.
SUFFIXES_TO_STRIP: Tuple[str, ...] = tuple(
    sorted(
        {
            "LT1ON",
            "LT1G",
            "LT1",
            "LT3G",
            "LT3",
            "LT",
            "PBF",
            "RPBF",
            "PWR",
            "EEL7",
            "EL7",
            "RL7",
            "WR",
            "VR",
            "PW",
            "ON",
            "TA",
            "TR",
            "TG",
            "TP",
            "TZ",
            "TX",
            "TY",
            "TW",
            "TU",
            "TV",
            "TS",
            "TT",
            "TF",
            "TE",
            "TD",
            "TC",
            "TB",
            "TQ",
            "T1",
            "T2",
            "1G",
            "2G",
            "3G",
            "7F",
            "13F",
            "M1",
            "M3",
            "M4",
            "M5",
            "N3",
            "N5",
            "N7",
            "N8",
            "DBVR",
            "BVR",
            "DZ",
            "DR",
            "DS",
            "DT",
            "DW",
            "DG",
            "F",
            "G",
            "R",
            "P",
            "Z",
            "X",
            "Y",
            "W",
            "V",
            "U",
            "S",
            "N",
            "M",
            "L",
            "K",
            "J",
            "H",
            "B",
            "C",
            "A",
            "D",
            "E",
        },
        key=len,
        reverse=True,
    )
)

MAX_PREFIX_STRIP = 4  # maximum number of leading letters stripped from an MPN token

# When multiple variants map to the same model_name, prefer the one with the
# highest priority (lower number).
VARIANT_SOURCE_PRIORITY: Dict[str, int] = {
    "original": 0,
    "strip_leading_dot": 1,
    "split_underscore": 2,
    "strip_suffix": 3,
}

# Priority order for match strategies. Lower is better.
MATCH_PRIORITY: Dict[str, int] = {
    "exact": 0,
    "sanitized": 1,
    "model_variant": 2,
    "mpn_suffix_strip": 3,
    "mpn_prefix_strip": 4,
    "digit_rotate": 5,
}


@dataclasses.dataclass(frozen=True)
class ModelVariant:
    """Represents a sanitized key associated with a model name."""

    model_name: str
    source: str  # e.g. "original", "strip_suffix", "split_underscore"
    detail: Optional[str] = None


@dataclasses.dataclass
class MatchCandidate:
    """Potential match between an MPN token and a model."""

    model_name: str
    match_type: str
    match_detail: Optional[str]
    priority: int
    source_token: str
    sanitized_token: str
    variant_priority: int = 0


@dataclasses.dataclass
class MatchResult:
    """Final unique match for an MPN."""

    model_name: str
    match_type: str
    match_detail: Optional[str]
    priority: int
    source_token: str
    sanitized_token: str
    variant_priority: int


def sanitize(text: str) -> str:
    """Return an uppercase alphanumeric-only variant of text."""
    return re.sub(r"[^A-Z0-9]", "", text.upper())


class SpiceModelMatcher:
    """Encapsulates heuristics for matching MPNs to SPICE model names."""

    def __init__(self, spice_db_path: Path):
        self.spice_db_path = spice_db_path
        self.models_by_upper: Dict[str, List[str]] = defaultdict(list)
        self.variant_map: Dict[str, List[ModelVariant]] = defaultdict(list)
        self.variant_unique_models: Dict[str, Set[str]] = {}
        self._canonical_models: Dict[str, str] = {}
        self._load_models()

    def _load_models(self) -> None:
        conn = connect_sqlite(self.spice_db_path, readonly=True)
        try:
            rows = conn.execute("SELECT model_name FROM models")
            for (model_name,) in rows:
                self._register_model(model_name)
        finally:
            conn.close()

        # Pre-compute uniqueness: only variants that map to a single model
        # are considered safe for automatic matching.
        self.variant_unique_models = {
            key: {variant.model_name for variant in variants}
            for key, variants in self.variant_map.items()
        }

    def _register_model(self, model_name: str) -> None:
        upper = model_name.upper()
        canonical = self._canonical_models.get(upper)
        if canonical is None:
            self._canonical_models[upper] = model_name
        elif canonical != model_name:
            # Duplicate differing only by case or formatting; skip re-registering.
            return
        else:
            # Already processed this model.
            return

        self.models_by_upper[upper].append(model_name)

        orig_clean = sanitize(model_name)
        self._add_variant(orig_clean, ModelVariant(model_name, "original"))

        if model_name.startswith("."):
            stripped = model_name.lstrip(".")
            clean = sanitize(stripped)
            if clean:
                self._add_variant(clean, ModelVariant(model_name, "strip_leading_dot"))

        if "_" in model_name:
            base = model_name.split("_", 1)[0]
            clean = sanitize(base)
            if clean:
                self._add_variant(clean, ModelVariant(model_name, "split_underscore"))

        for suffix in SUFFIXES_TO_STRIP:
            if orig_clean.endswith(suffix) and len(orig_clean) - len(suffix) >= 3:
                base = orig_clean[: -len(suffix)]
                if base:
                    self._add_variant(
                        base,
                        ModelVariant(model_name, "strip_suffix", detail=suffix),
                    )

    def _add_variant(self, key: str, variant: ModelVariant) -> None:
        variants = self.variant_map[key]
        if variant not in variants:
            variants.append(variant)

    def match_mpn(self, mpn: str) -> Optional[MatchResult]:
        candidates: List[MatchCandidate] = []
        seen_models: Set[Tuple[str, str, Optional[str]]] = set()
        seen_clean_tokens: Set[str] = set()

        for token in self._generate_tokens(mpn):
            tok_clean = sanitize(token)
            if not tok_clean:
                continue
            if tok_clean in seen_clean_tokens:
                continue
            seen_clean_tokens.add(tok_clean)

            tok_upper = token.upper()
            if tok_upper in self.models_by_upper:
                for model_name in self.models_by_upper[tok_upper]:
                    key = (model_name, "exact", None)
                    if key in seen_models:
                        continue
                    seen_models.add(key)
                    candidates.append(
                        MatchCandidate(
                            model_name=model_name,
                            match_type="exact",
                            match_detail=None,
                            priority=MATCH_PRIORITY["exact"],
                            source_token=token,
                            sanitized_token=tok_clean,
                        )
                    )

            variant_candidates = self._variant_matches(tok_clean, token)
            for candidate in variant_candidates:
                key = (candidate.model_name, candidate.match_type, candidate.match_detail)
                if key in seen_models:
                    continue
                seen_models.add(key)
                candidates.append(candidate)

            suffix_candidates = self._mpn_suffix_matches(tok_clean, token)
            for candidate in suffix_candidates:
                key = (candidate.model_name, candidate.match_type, candidate.match_detail)
                if key in seen_models:
                    continue
                seen_models.add(key)
                candidates.append(candidate)

            prefix_candidates = self._mpn_prefix_matches(tok_clean, token)
            for candidate in prefix_candidates:
                key = (candidate.model_name, candidate.match_type, candidate.match_detail)
                if key in seen_models:
                    continue
                seen_models.add(key)
                candidates.append(candidate)

            digit_candidate = self._digit_rotation_match(tok_clean, token)
            if digit_candidate:
                key = (
                    digit_candidate.model_name,
                    digit_candidate.match_type,
                    digit_candidate.match_detail,
                )
                if key not in seen_models:
                    seen_models.add(key)
                    candidates.append(digit_candidate)

        if not candidates:
            return None

        candidates.sort(
            key=lambda c: (
                c.priority,
                c.variant_priority,
                len(c.model_name),
                c.model_name,
            )
        )

        best_priority = candidates[0].priority
        best = [c for c in candidates if c.priority == best_priority]
        best_models = {c.model_name for c in best}
        if len(best_models) != 1:
            return None

        chosen = best[0]
        return MatchResult(
            model_name=chosen.model_name,
            match_type=chosen.match_type,
            match_detail=chosen.match_detail,
            priority=chosen.priority,
            source_token=chosen.source_token,
            sanitized_token=chosen.sanitized_token,
            variant_priority=chosen.variant_priority,
        )

    def _variant_matches(self, clean_token: str, original_token: str) -> List[MatchCandidate]:
        variants = self.variant_map.get(clean_token)
        if not variants:
            return []

        model_names = {variant.model_name for variant in variants}
        if len(model_names) != 1:
            return []

        chosen_variant = min(
            variants,
            key=lambda v: (
                VARIANT_SOURCE_PRIORITY.get(v.source, 99),
                v.detail or "",
            ),
        )

        if chosen_variant.source == "original":
            match_type = "sanitized"
            variant_priority = VARIANT_SOURCE_PRIORITY["original"]
            detail = None
        else:
            match_type = "model_variant"
            variant_priority = VARIANT_SOURCE_PRIORITY.get(chosen_variant.source, 99)
            detail = chosen_variant.detail or chosen_variant.source

        return [
            MatchCandidate(
                model_name=chosen_variant.model_name,
                match_type=match_type,
                match_detail=detail,
                priority=MATCH_PRIORITY[match_type],
                source_token=original_token,
                sanitized_token=clean_token,
                variant_priority=variant_priority,
            )
        ]

    def _mpn_suffix_matches(self, clean_token: str, original_token: str) -> List[MatchCandidate]:
        candidates: List[MatchCandidate] = []
        for suffix in SUFFIXES_TO_STRIP:
            if clean_token.endswith(suffix) and len(clean_token) - len(suffix) >= 3:
                base = clean_token[: -len(suffix)]
                variants = self.variant_map.get(base)
                if not variants:
                    continue
                model_names = {variant.model_name for variant in variants}
                if len(model_names) != 1:
                    continue
                chosen_variant = min(
                    variants,
                    key=lambda v: (
                        VARIANT_SOURCE_PRIORITY.get(v.source, 99),
                        v.detail or "",
                    ),
                )
                candidates.append(
                    MatchCandidate(
                        model_name=chosen_variant.model_name,
                        match_type="mpn_suffix_strip",
                        match_detail=suffix,
                        priority=MATCH_PRIORITY["mpn_suffix_strip"],
                        source_token=original_token,
                        sanitized_token=clean_token,
                        variant_priority=VARIANT_SOURCE_PRIORITY.get(
                            chosen_variant.source, 99
                        ),
                    )
                )
        return candidates

    def _mpn_prefix_matches(self, clean_token: str, original_token: str) -> List[MatchCandidate]:
        candidates: List[MatchCandidate] = []
        for prefix_len in range(1, min(MAX_PREFIX_STRIP + 1, len(clean_token) - 1)):
            prefix = clean_token[:prefix_len]
            if not prefix.isalpha():
                continue
            rest = clean_token[prefix_len:]
            if not rest or not re.search(r"[A-Z]", rest):
                continue
            variants = self.variant_map.get(rest)
            if not variants:
                continue
            model_names = {variant.model_name for variant in variants}
            if len(model_names) != 1:
                continue
            chosen_variant = min(
                variants,
                key=lambda v: (
                    VARIANT_SOURCE_PRIORITY.get(v.source, 99),
                    v.detail or "",
                ),
            )
            candidates.append(
                MatchCandidate(
                    model_name=chosen_variant.model_name,
                    match_type="mpn_prefix_strip",
                    match_detail=prefix,
                    priority=MATCH_PRIORITY["mpn_prefix_strip"],
                    source_token=original_token,
                    sanitized_token=clean_token,
                    variant_priority=VARIANT_SOURCE_PRIORITY.get(
                        chosen_variant.source, 99
                    ),
                )
            )
        return candidates

    def _digit_rotation_match(self, clean_token: str, original_token: str) -> Optional[MatchCandidate]:
        match = re.fullmatch(r"([0-9]+)([A-Z]+)", clean_token)
        if not match:
            return None
        rotated = match.group(2) + match.group(1)
        variants = self.variant_map.get(rotated)
        if not variants:
            return None
        model_names = {variant.model_name for variant in variants}
        if len(model_names) != 1:
            return None
        chosen_variant = min(
            variants,
            key=lambda v: (
                VARIANT_SOURCE_PRIORITY.get(v.source, 99),
                v.detail or "",
            ),
        )
        return MatchCandidate(
            model_name=chosen_variant.model_name,
            match_type="digit_rotate",
            match_detail=None,
            priority=MATCH_PRIORITY["digit_rotate"],
            source_token=original_token,
            sanitized_token=clean_token,
            variant_priority=VARIANT_SOURCE_PRIORITY.get(chosen_variant.source, 99),
        )

    @staticmethod
    def _generate_tokens(mpn: str) -> Iterator[str]:
        upper = mpn.upper()
        without_parens = re.sub(r"\\(.*?\\)", " ", upper)
        tokens: Set[str] = set()
        tokens.add(without_parens.strip())
        tokens.add(sanitize(without_parens))
        for part in re.split(r"[\\s,;/]+", without_parens):
            if not part:
                continue
            tokens.add(part)
            cleaned = sanitize(part)
            if cleaned:
                tokens.add(cleaned)
            for sub in re.split(r"[-_]", part):
                if not sub:
                    continue
                tokens.add(sub)
                cleaned_sub = sanitize(sub)
                if cleaned_sub:
                    tokens.add(cleaned_sub)
        return (token for token in tokens if token)


def build_mapping(
    matcher: SpiceModelMatcher,
    component_rows: Iterable[Tuple[int, str, int]],
) -> List[Tuple[int, str, str, str, Optional[str], str, str, int, int]]:
    """
    Build mapping records.

    Each returned tuple has:
        lcsc, mpn, model_name, match_type, match_detail,
        source_token, sanitized_token, priority, variant_priority
    """
    records: List[Tuple[int, str, str, str, Optional[str], str, str, int, int]] = []
    seen_pairs: Set[Tuple[str, str]] = set()

    for lcsc, mpn, symbol_lib in component_rows:
        if not mpn:
            continue
        match = matcher.match_mpn(mpn)
        if not match:
            continue

        key = (mpn.upper(), match.model_name.upper())
        if key in seen_pairs:
            continue
        seen_pairs.add(key)

        records.append(
            (
                lcsc,
                mpn,
                match.model_name,
                match.match_type,
                match.match_detail,
                match.source_token,
                match.sanitized_token,
                match.priority,
                match.variant_priority,
            )
        )

    return records


def write_output_db(
    output_path: Path,
    records: Sequence[Tuple[int, str, str, str, Optional[str], str, str, int, int]],
) -> None:
    if output_path.exists():
        output_path.unlink()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect_sqlite(output_path)
    try:
        conn.execute(
            """
            CREATE TABLE mpn_model_map (
                lcsc INTEGER,
                mpn TEXT NOT NULL,
                model_name TEXT NOT NULL,
                match_type TEXT NOT NULL,
                match_detail TEXT,
                source_token TEXT NOT NULL,
                sanitized_token TEXT NOT NULL,
                match_priority INTEGER NOT NULL,
                variant_priority INTEGER NOT NULL,
                PRIMARY KEY (mpn, model_name)
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO mpn_model_map (
                lcsc,
                mpn,
                model_name,
                match_type,
                match_detail,
                source_token,
                sanitized_token,
                match_priority,
                variant_priority
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            records,
        )
        conn.execute("CREATE INDEX idx_mpn_model_map_mpn ON mpn_model_map (mpn)")
        conn.execute(
            "CREATE INDEX idx_mpn_model_map_model ON mpn_model_map (model_name)"
        )
        conn.commit()
    finally:
        conn.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--components-db",
        default="part_lib/jlcpcb-components.sqlite3",
        type=Path,
        help="Path to jlcpcb-components.sqlite3",
    )
    parser.add_argument(
        "--spice-db",
        default="spice_lib/spice_models.db",
        type=Path,
        help="Path to spice_models.db",
    )
    parser.add_argument(
        "--output",
        default="part_lib/mpn_model_map.sqlite3",
        type=Path,
        help="Destination SQLite database for the mapping",
    )
    parser.add_argument(
        "--include-all-mpns",
        action="store_true",
        help="Include all components rather than restricting to symbol_lib = 1",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process only the first N components (useful for testing)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging",
    )
    return parser.parse_args()


def fetch_component_rows(
    conn: sqlite3.Connection, include_all: bool, limit: Optional[int]
) -> Iterable[Tuple[int, str, int]]:
    base_query = "SELECT lcsc, mpn, symbol_lib FROM v_components_search"
    params: List[object] = []
    if not include_all:
        base_query += " WHERE symbol_lib = 1"
    base_query += " ORDER BY lcsc"
    if limit is not None:
        base_query += " LIMIT ?"
        params.append(limit)
    return conn.execute(base_query, params)


def summarise(records: Sequence[Tuple[int, str, str, str, Optional[str], str, str, int, int]]) -> Dict[str, int]:
    stats: Dict[str, int] = defaultdict(int)
    for _, _, _, match_type, _, _, _, _, _ in records:
        stats[match_type] += 1
    return dict(stats)


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s",
    )

    spice_db = resolve_read_db_path(args.spice_db, prefix="spice", repo_root=PROJECT_ROOT)
    components_db = resolve_read_db_path(args.components_db, prefix="component", repo_root=PROJECT_ROOT)
    output_db = resolve_write_db_path(args.output, prefix="component", repo_root=PROJECT_ROOT)
    if output_db != args.output:
        logging.info("Output DB is protected; writing to working copy instead: %s", output_db)

    logging.info("Loading SPICE models from %s", spice_db)
    matcher = SpiceModelMatcher(spice_db)

    conn = connect_sqlite(components_db, readonly=True)
    try:
        rows = list(fetch_component_rows(conn, args.include_all_mpns, args.limit))
    finally:
        conn.close()

    logging.info("Processing %d components", len(rows))
    records = build_mapping(matcher, rows)
    stats = summarise(records)

    logging.info("Matched %d components", len(records))
    for match_type, count in sorted(stats.items(), key=lambda item: item[0]):
        logging.info("  %-18s %6d", match_type, count)

    write_output_db(output_db, records)
    logging.info("Wrote mapping to %s", output_db)


if __name__ == "__main__":
    main()
