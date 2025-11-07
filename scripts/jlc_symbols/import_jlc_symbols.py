#!/usr/bin/env python3
"""
Import selected JLCPCB components into KiCad symbol and footprint libraries.

The script looks up rich, non-generic components from the local LCSC catalogue
database, fetches the associated EasyEDA assets, and stores them in project
scoped KiCad libraries so they can be referenced from schematics.
"""

from __future__ import annotations

import argparse
import json
import logging
import multiprocessing as mp
import os
import queue
import re
import sqlite3
import sys
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Repository paths and module setup
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EASYEDA_ROOT = PROJECT_ROOT / "easyeda2kicad.py"

if str(EASYEDA_ROOT) not in sys.path:
    sys.path.insert(0, str(EASYEDA_ROOT))

from easyeda2kicad.easyeda.easyeda_api import EasyedaApi  # type: ignore
from easyeda2kicad.easyeda.easyeda_importer import (  # type: ignore
    EasyedaFootprintImporter,
    EasyedaSymbolImporter,
)
from easyeda2kicad.helpers import (  # type: ignore
    add_component_in_symbol_lib_file,
    id_already_in_symbol_lib,
    update_component_in_symbol_lib_file,
)
from easyeda2kicad.kicad.export_kicad_footprint import ExporterFootprintKicad  # type: ignore
from easyeda2kicad.kicad.export_kicad_symbol import ExporterSymbolKicad  # type: ignore
from easyeda2kicad.kicad.parameters_kicad_symbol import KicadVersion  # type: ignore


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ComponentCandidate:
    lcsc_code: str
    category: str
    mfr: str

    def grouping_key(self) -> str:
        """Use the top-level category to drive symbol/footprint grouping."""
        return self.category or "Uncategorized"


# ---------------------------------------------------------------------------
# Constants and helpers
# ---------------------------------------------------------------------------

SYMBOL_HEADER = '(kicad_symbol_lib (version 20211014) (generator "KiCAD_MCP")\n'
NOISY_LOG_SNIPPETS = [
    "Unknow symbol designator",
    "This id is already",
]

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


def is_nonprimitive_category(category: str) -> bool:
    """Return True if the given root category should be treated as non-primitive."""
    root = (category or "").strip()
    if not root:
        return True
    return root not in PRIMITIVE_CATEGORY_ROOTS


def sanitize_identifier(raw: str, prefix: str, max_length: int = 80) -> str:
    """Convert arbitrary text into a KiCad-friendly identifier."""
    if not raw:
        raw = prefix
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", raw.strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    if not cleaned:
        cleaned = prefix
    if cleaned[0].isdigit():
        cleaned = f"{prefix}_{cleaned}"
    return cleaned[:max_length]


def ensure_symbol_library(path: Path) -> None:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(SYMBOL_HEADER, encoding="utf-8")


def ensure_footprint_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def detect_system_symbol_dirs() -> List[Path]:
    """Return the detected KiCad symbol directories (system + global)."""
    candidates = [
        Path("/usr/share/kicad/symbols"),
        Path("/usr/local/share/kicad/symbols"),
        Path("/opt/kicad/share/kicad/symbols"),
    ]

    found: List[Path] = []

    def register(path: Path) -> None:
        resolved = path.resolve()
        if resolved.exists() and resolved not in found:
            found.append(resolved)

    for candidate in candidates:
        register(candidate)

    config_root = Path.home() / ".config" / "kicad"
    if config_root.exists():
        for version_dir in sorted(config_root.glob("[0-9]*.*")):
            table = version_dir / "sym-lib-table"
            if not table.exists():
                continue
            for line in table.read_text(encoding="utf-8").splitlines():
                if '(uri "' in line:
                    uri = line.split('(uri "', 1)[1].split('"', 1)[0]
                    uri_path = Path(uri)
                    library_dir = uri_path if uri_path.is_dir() else uri_path.parent
                    register(library_dir)
    return found


def row_to_candidate(row: sqlite3.Row) -> Optional[ComponentCandidate]:
    """Convert a SQLite row into a ComponentCandidate if it meets heuristics."""
    category = (row["category"] or "").strip()
    if not is_nonprimitive_category(category):
        return None
    mfr = (row["mfr"] or "").strip()
    if not mfr:
        return None
    return ComponentCandidate(
        lcsc_code=f"C{row['lcsc']}",
        category=category or "Uncategorized",
        mfr=mfr,
    )


def fetch_candidates(
    db_path: Path,
    limit: Optional[int],
    scan_limit: Optional[int] = None,
) -> List[ComponentCandidate]:
    """Load candidate parts from the database using heuristic filters."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    base_query = """
        SELECT lcsc, category, mfr
        FROM v_components
        WHERE mfr IS NOT NULL
          AND mfr != ''
        ORDER BY preferred DESC, stock DESC
    """
    if scan_limit is not None:
        base_query += " LIMIT ?"
        rows = conn.execute(base_query, (scan_limit,)).fetchall()
    else:
        rows = conn.execute(base_query).fetchall()
    conn.close()

    candidates: List[ComponentCandidate] = []
    for row in rows:
        candidate = row_to_candidate(row)
        if not candidate:
            continue
        candidates.append(candidate)
        if limit is not None and len(candidates) >= limit:
            break
    return candidates


def log_summary(system_dirs: Iterable[Path], output_dir: Path) -> None:
    system_dirs = list(system_dirs)
    logging.info(
        "Detected KiCad symbol library locations (%d total):",
        len(system_dirs),
    )
    for directory in system_dirs[:10]:
        logging.info("  - %s", directory)
    if len(system_dirs) > 10:
        logging.info("  ... %d additional entries not shown", len(system_dirs) - 10)
    logging.info("Project output will be stored in: %s", output_dir)


def group_candidates_by_category(
    candidates: Sequence[ComponentCandidate],
) -> Dict[str, List[ComponentCandidate]]:
    groups: Dict[str, List[ComponentCandidate]] = {}
    for candidate in candidates:
        key = candidate.grouping_key()
        groups.setdefault(key, []).append(candidate)
    return groups


class SuppressMessages(logging.Filter):
    def __init__(self, snippets: Sequence[str]) -> None:
        self.snippets = list(snippets)

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        return not any(snippet in message for snippet in self.snippets)


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format="%(levelname)s: %(message)s",
    )
    logging.getLogger().addFilter(SuppressMessages(NOISY_LOG_SNIPPETS))


_spinner_state = 0
_spinner_chars = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

def update_console_progress(
    current: int,
    total: int,
    prefix: str = "Progress",
    added: int = 0,
    updated: int = 0,
    skipped: int = 0,
    failed: int = 0,
    current_category: str = "",
    show_spinner: bool = False,
) -> None:
    """Update progress bar with detailed statistics."""
    global _spinner_state

    if total <= 0:
        return
    percent = (current / total) * 100

    # Build progress bar
    bar_width = 40
    filled = int(bar_width * current / total)
    bar = "█" * filled + "░" * (bar_width - filled)

    # Build status line
    status_parts = []
    if added > 0:
        status_parts.append(f"✓{added}")
    if updated > 0:
        status_parts.append(f"↻{updated}")
    if skipped > 0:
        status_parts.append(f"⊘{skipped}")
    if failed > 0:
        status_parts.append(f"✗{failed}")

    status = " ".join(status_parts) if status_parts else ""

    # Build category info with spinner
    if show_spinner:
        _spinner_state = (_spinner_state + 1) % len(_spinner_chars)
        spinner = _spinner_chars[_spinner_state]
        category_info = f" {spinner} [{current_category}]" if current_category else f" {spinner}"
    else:
        category_info = f" [{current_category}]" if current_category else ""

    # Clear line and write progress
    line = f"\r{prefix}: {bar} {current}/{total} ({percent:.1f}%) {status}{category_info}"
    # Pad with spaces to clear any leftover characters
    line = line.ljust(120)
    sys.stdout.write(line)
    sys.stdout.flush()


def process_candidate(
    api: EasyedaApi,
    candidate: ComponentCandidate,
    output_dir: Path,
    overwrite: bool,
    cache_conn: Optional[sqlite3.Connection],
) -> Optional[str]:
    """Fetch EasyEDA assets and emit KiCad symbol and footprint files."""
    asset = None
    if cache_conn is not None:
        try:
            row = cache_conn.execute(
                "SELECT payload FROM easyeda_cache WHERE lcsc = ?",
                (candidate.lcsc_code,),
            ).fetchone()
            if row:
                payload = json.loads(row[0])
                asset = payload.get("result", payload)
        except Exception as exc:  # noqa: BLE001
            logging.error("Failed to read cached payload for %s: %s", candidate.lcsc_code, exc)
    if not asset or "dataStr" not in asset or "packageDetail" not in asset:
        logging.debug("Cached payload missing CAD data for %s; skipping.", candidate.lcsc_code)
        return None

    symbol_importer = EasyedaSymbolImporter(asset)
    footprint_importer = EasyedaFootprintImporter(asset)

    # Prepare grouping and naming
    group_label = sanitize_identifier(candidate.grouping_key(), "Group", 48)
    symbol_name = candidate.mfr

    # Symbol library preparation
    symbol_dir = output_dir / "symbols"
    symbol_path = symbol_dir / f"{group_label}.kicad_sym"
    ensure_symbol_library(symbol_path)

    # Footprint library preparation
    footprint_dir = output_dir / "footprints" / f"{group_label}.pretty"
    ensure_footprint_dir(footprint_dir)
    footprint_exporter = ExporterFootprintKicad(footprint_importer.get_footprint())
    footprint_exporter.output.model_3d = None
    raw_footprint_name = footprint_exporter.output.info.name or candidate.mfr or candidate.lcsc_code
    footprint_file_stem = raw_footprint_name.replace(os.sep, "_")
    footprint_file = footprint_dir / f"{footprint_file_stem}.kicad_mod"
    footprint_exporter.export(str(footprint_file), ".")

    # Generate symbol
    symbol_exporter = ExporterSymbolKicad(symbol_importer.get_symbol(), KicadVersion.v6)
    symbol_exporter.output.info.name = symbol_name
    symbol_exporter.output.info.package = f"{group_label}:{raw_footprint_name}"
    symbol_payload = symbol_exporter.export(group_label)

    already_present = id_already_in_symbol_lib(
        str(symbol_path), symbol_name, KicadVersion.v6
    )
    if already_present and not overwrite:
        logging.debug(
            "Symbol %s already present in %s; skipping.",
            symbol_name,
            symbol_path,
        )
        return "skipped"

    if already_present:
        update_component_in_symbol_lib_file(
            str(symbol_path), symbol_name, symbol_payload, KicadVersion.v6
        )
        status = "updated"
    else:
        add_component_in_symbol_lib_file(
            str(symbol_path), symbol_payload, KicadVersion.v6
        )
        status = "added"

    return status


PROGRESS_FLUSH_INTERVAL = 8  # Update progress every 8 items for more responsive feedback


def process_category_batch(
    category: str,
    candidates: Sequence[ComponentCandidate],
    output_dir: Path,
    overwrite: bool,
    cache_db: Optional[str],
    log_level: str,
    progress_queue: Optional[Any] = None,
) -> Tuple[str, int, int, int, int, int]:
    # Suppress most logging but allow ERROR level for debugging
    logging.basicConfig(
        level=logging.ERROR,  # Show errors to help debug issues
        format="%(levelname)s: %(message)s",
    )
    api = EasyedaApi()
    output_path = Path(output_dir)
    cache_conn = sqlite3.connect(cache_db) if cache_db else None
    added = updated = skipped = 0
    total = len(candidates)
    pending_updates = 0
    try:
        for idx, candidate in enumerate(candidates):
            try:
                status = process_candidate(api, candidate, output_path, overwrite, cache_conn)
                if status == "added":
                    added += 1
                elif status == "updated":
                    updated += 1
                elif status == "skipped":
                    skipped += 1
            except Exception as exc:  # noqa: BLE001
                # Log error but continue processing
                logging.error("Failed to process %s in category %s: %s", candidate.lcsc_code, category, exc)
                # Count as processed but failed

            pending_updates += 1

            # Send progress updates more frequently (every 8 items instead of 32)
            if progress_queue is not None and (pending_updates >= 8 or idx == len(candidates) - 1):
                try:
                    # Send progress update with category name
                    progress_queue.put({"count": pending_updates, "category": category})
                except Exception:  # noqa: BLE001
                    pass
                pending_updates = 0
    finally:
        if cache_conn:
            cache_conn.close()
        if progress_queue is not None and pending_updates > 0:
            try:
                progress_queue.put({"count": pending_updates, "category": category})
            except Exception:  # noqa: BLE001
                pass
    failed = total - (added + updated + skipped)
    return category, added, updated, skipped, failed, total


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert selected EasyEDA assets from the JLCPCB catalogue into KiCad libraries."
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=PROJECT_ROOT / "part_lib" / "jlcpcb-components.sqlite3",
        help="Path to the local JLCPCB component SQLite database.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "symbol_lib",
        help="Directory where generated KiCad libraries will be written.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Number of components to process (defaults to all available).",
    )
    parser.add_argument(
        "--scan-limit",
        type=int,
        default=None,
        help="How many database rows to inspect (defaults to entire table).",
    )
    parser.add_argument(
        "--cache-db",
        type=Path,
        default=None,
        help="Optional SQLite cache of EasyEDA JSON (built via build_easyeda_cache_db.py).",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing symbols in the target library if duplicates are found.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices={"DEBUG", "INFO", "WARNING", "ERROR"},
        help="Logging verbosity for the run.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Number of parallel processes (per category); default 1 (sequential).",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    # Suppress all logging except critical errors
    configure_logging("CRITICAL")

    system_dirs = detect_system_symbol_dirs()
    # Don't log summary - just process silently
    # log_summary(system_dirs, args.output)

    candidates = fetch_candidates(args.db, args.limit, args.scan_limit)
    if not candidates:
        print("ERROR: No suitable component candidates found.", file=sys.stderr)
        return 1

    args.output.mkdir(parents=True, exist_ok=True)

    total_candidates = len(candidates)
    groups = group_candidates_by_category(candidates)
    stats = {"added": 0, "updated": 0, "skipped": 0, "failed": 0}

    progress_printed = False
    current_category = ""

    if args.workers and args.workers > 1:
        cache_db_path = Path(args.cache_db) if args.cache_db else None
        mp_context = mp.get_context()
        manager = mp_context.Manager()
        progress_queue = manager.Queue()

        processed_items = 0

        def drain_progress_queue() -> None:
            nonlocal progress_printed
            nonlocal processed_items
            nonlocal current_category
            while True:
                try:
                    msg = progress_queue.get_nowait()
                except queue.Empty:
                    break
                except (EOFError, OSError, ValueError):
                    return
                else:
                    try:
                        if isinstance(msg, dict):
                            processed_items += int(msg.get("count", 0))
                            current_category = msg.get("category", "")
                        else:
                            # Backward compatibility with old format
                            processed_items += int(msg)
                    except (TypeError, ValueError):
                        continue
                    update_console_progress(
                        processed_items,
                        total_candidates,
                        prefix="Overall progress",
                        added=stats["added"],
                        updated=stats["updated"],
                        skipped=stats["skipped"],
                        failed=stats["failed"],
                        current_category=current_category,
                    )
                    progress_printed = True
            return

        try:
            with ProcessPoolExecutor(max_workers=args.workers, mp_context=mp_context) as executor:
                futures = [
                    executor.submit(
                        process_category_batch,
                        category,
                        items,
                        args.output,
                        args.overwrite,
                        str(cache_db_path) if cache_db_path else None,
                        args.log_level,
                        progress_queue,
                    )
                    for category, items in groups.items()
                ]

                pending = set(futures)
                completed_categories = 0
                last_update_time = 0.0
                import time

                while pending:
                    done, pending = wait(pending, timeout=0.5, return_when=FIRST_COMPLETED)
                    drain_progress_queue()

                    # Force progress bar update every 1 second even if no new data (with spinner)
                    current_time = time.time()
                    if current_time - last_update_time >= 1.0:
                        update_console_progress(
                            processed_items,
                            total_candidates,
                            prefix="Overall progress",
                            added=stats["added"],
                            updated=stats["updated"],
                            skipped=stats["skipped"],
                            failed=stats["failed"],
                            current_category=current_category,
                            show_spinner=True,  # Show spinner to indicate activity
                        )
                        progress_printed = True
                        last_update_time = current_time

                    for future in done:
                        category, added, updated, skipped, failed, total = future.result()
                        stats["added"] += added
                        stats["updated"] += updated
                        stats["skipped"] += skipped
                        stats["failed"] += failed
                        completed_categories += 1
                        # Update progress bar with latest stats
                        update_console_progress(
                            processed_items,
                            total_candidates,
                            prefix="Overall progress",
                            added=stats["added"],
                            updated=stats["updated"],
                            skipped=stats["skipped"],
                            failed=stats["failed"],
                            current_category=current_category,
                        )
                        progress_printed = True
                        last_update_time = current_time
                drain_progress_queue()
        finally:
            manager.shutdown()
    else:
        # Sequential mode - also suppress logs
        api = EasyedaApi()
        cache_conn: Optional[sqlite3.Connection] = None
        if args.cache_db:
            try:
                cache_conn = sqlite3.connect(args.cache_db)
            except sqlite3.Error as exc:
                print(f"ERROR: Unable to open cache database {args.cache_db}: {exc}", file=sys.stderr)
                return 1

        processed_count = 0
        for category, items in groups.items():
            current_category = category
            for candidate in items:
                try:
                    status = process_candidate(api, candidate, args.output, args.overwrite, cache_conn)
                except Exception as exc:  # pragma: no cover - defensive
                    # Only show critical errors
                    status = None
                if status == "added":
                    stats["added"] += 1
                elif status == "updated":
                    stats["updated"] += 1
                elif status == "skipped":
                    stats["skipped"] += 1
                else:
                    stats["failed"] += 1
                processed_count += 1
                update_console_progress(
                    processed_count,
                    total_candidates,
                    prefix="Overall progress",
                    added=stats["added"],
                    updated=stats["updated"],
                    skipped=stats["skipped"],
                    failed=stats["failed"],
                    current_category=current_category,
                )
                progress_printed = True
        if cache_conn:
            cache_conn.close()

    if progress_printed:
        sys.stdout.write("\n")

    processed_total = stats["added"] + stats["updated"]
    if processed_total == 0:
        print("\nWARNING: No components were processed successfully.", file=sys.stderr)
        return 2

    # Print final summary
    print(f"\n✓ Completed: {stats['added']} added, {stats['updated']} updated, {stats['skipped']} skipped, {stats['failed']} failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
