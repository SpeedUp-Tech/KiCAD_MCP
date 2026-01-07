#!/usr/bin/env python3
"""
Export non-primitive LCSC part numbers into manageable batch files.

This script mirrors the primitive/non-primitive split used by the downloader and
converter, so you can produce batch files directly from the local SQLite
catalogue without any manual preparation.
"""

from __future__ import annotations

import argparse
import logging
import math
import sqlite3
import sys
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PYTHON_ROOT = PROJECT_ROOT / "python"
if str(PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(PYTHON_ROOT))

from db_tools.sqlite import connect_sqlite
from db_tools.workdir import resolve_read_db_path

# Keep the primitive category list in sync with import_jlc_symbols.py
PRIMITIVE_CATEGORY_ROOTS: set[str] = {
    "Resistors",
    "Capacitors",
    "Inductors",
    "Inductors/Coils/Transformers",
    "Inductors, Coils, Chokes",
    "Bead/Filter/EMI Optimization",
    "Filters",
    "Filters/EMI Optimization",
    "Connectors",
    "Switches",
    "Key/Switch",
    "Relays",
    "Wires And Cables",
    "Wire/Cable/DataCable",
    "Terminal",
    "Hardware Fasteners",
    "Hardware/Fasteners/Sealing",
    "Hardware Fasteners/Seals",
    "Hardwares/Solders/Accessories/Batteries",
    "Solders / Accessories / Batteries",
    "Consumables",
    "Consumables And Auxiliary Materials",
    "Development Boards & Tools",
    "Educational Kits",
    "Test",
    "Global Sourcing Parts",
    "Others",
    "Other",
    "Audio Components/Vibration Motors",
    "Audio Products / Vibration Motors",
    "Audio Products/Micromotors",
    "Audio Products/Motors",
    "Buzzers & Speakers & Microphones",
    "Display Screen",
    "Display Modules / LED Drivers / Display Drivers",
    "Displays",
    "Electromechanical Devices & Components",
    "Electronic Tools/Instruments/Consumables",
}


def is_nonprimitive(category: str) -> bool:
    return (category or "").strip() not in PRIMITIVE_CATEGORY_ROOTS


def fetch_candidates(
    db_path: Path,
    limit: Optional[int],
) -> List[Tuple[int, str]]:
    db_path = resolve_read_db_path(db_path, prefix="component")
    conn = connect_sqlite(db_path, readonly=True)
    query = """
        SELECT lcsc, category
        FROM v_components
        WHERE mfr IS NOT NULL
          AND mfr != ''
        ORDER BY category, lcsc
    """
    cur = conn.cursor()
    if limit is not None:
        cur.execute(query + " LIMIT ?", (limit,))
    else:
        cur.execute(query)
    rows = cur.fetchall()
    conn.close()

    candidates: List[Tuple[int, str]] = []
    for row in rows:
        category = (row["category"] or "").strip()
        if not is_nonprimitive(category):
            continue
        candidates.append((row["lcsc"], category or "Uncategorized"))
    return candidates


def chunk(
    items: Sequence[Tuple[int, str]],
    batch_size: int,
) -> Iterable[Sequence[Tuple[int, str]]]:
    for offset in range(0, len(items), batch_size):
        yield items[offset : offset + batch_size]


def write_batches(
    batches: Iterable[Sequence[Tuple[int, str]]],
    output_dir: Path,
) -> List[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    paths: List[Path] = []
    for index, batch in enumerate(batches, start=1):
        filename = f"batch_{index:04d}.txt"
        target = output_dir / filename
        with target.open("w", encoding="utf-8") as fp:
            fp.write(f"# Non-primitive batch {index}\n")
            for lcsc, category in batch:
                fp.write(f"C{lcsc}\n")
        paths.append(target)
    return paths


def summarize(candidates: Sequence[Tuple[int, str]]) -> str:
    total = len(candidates)
    by_category: dict[str, int] = {}
    for _, category in candidates:
        by_category[category] = by_category.get(category, 0) + 1
    top_categories = sorted(by_category.items(), key=lambda kv: kv[1], reverse=True)[:10]
    summary_lines = [f"Total non-primitive candidates: {total}"]
    for category, count in top_categories:
        summary_lines.append(f"  {category}: {count}")
    return "\n".join(summary_lines)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create batch files of non-primitive LCSC IDs directly from the JLCPCB database.",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path("part_lib") / "jlcpcb-components.sqlite3",
        help="Path to the JLCPCB components SQLite database.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Directory to store the generated batch text files.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=500,
        help="Number of entries per batch file (default: 500).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional maximum number of entries to consider (for testing).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices={"DEBUG", "INFO", "WARNING", "ERROR"},
        help="Verbosity for console logging.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(levelname)s: %(message)s",
    )

    batch_size = max(1, args.batch_size)
    candidates = fetch_candidates(args.db, args.limit)
    if not candidates:
        logging.error("No non-primitive candidates found.")
        return 1

    logging.info(summarize(candidates))
    total_batches = math.ceil(len(candidates) / batch_size)
    logging.info("Writing %d batch files to %s", total_batches, args.output)

    batch_paths = write_batches(list(chunk(candidates, batch_size)), args.output)
    for path in batch_paths:
        logging.debug("Wrote %s", path)

    logging.info("Done. %d batch files generated.", len(batch_paths))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
