#!/usr/bin/env python3
"""
Fetch EasyEDA component JSON in controlled batches.

This downloader intentionally keeps the scope minimal:
* Reads a flat list of LCSC part numbers (one `C1234` per line, comments allowed)
* Skips entries that are already cached unless `--force` is provided
* Limits concurrency and adds a configurable delay between requests to avoid rate limits
* Emits a status summary plus a failure list for follow-up retries

The script does **not** touch KiCad assets; it only stores the EasyEDA API payloads
so that symbol/footprint conversion can run locally without hammering the network.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

import requests

# EasyEDA endpoint is the same one used inside easyeda2kicad.
EASYEDA_API_URL = "https://easyeda.com/api/products/{lcsc_id}/components?version=6.4.19.5"

LCSC_PATTERN = re.compile(r"^C\d+$", re.IGNORECASE)


def parse_lcsc_list(path: Path) -> List[str]:
    cleaned: List[str] = []
    raw_lines = path.read_text(encoding="utf-8").splitlines()
    for idx, line in enumerate(raw_lines, start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        part = stripped.upper()
        if not LCSC_PATTERN.match(part):
            logging.warning("Skipping malformed LCSC id on line %d: %s", idx, stripped)
            continue
        cleaned.append(part)
    return cleaned


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


class DownloadStats:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.fetched: int = 0
        self.skipped: int = 0
        self.failed: List[Tuple[str, str]] = []

    def mark_fetched(self) -> None:
        with self.lock:
            self.fetched += 1

    def mark_skipped(self) -> None:
        with self.lock:
            self.skipped += 1

    def mark_failed(self, lcsc: str, reason: str) -> None:
        with self.lock:
            self.failed.append((lcsc, reason))


def fetch_single(
    lcsc_id: str,
    output_dir: Path,
    force: bool,
    retries: int,
    delay: float,
    timeout: float,
    stats: DownloadStats,
    session_factory: threading.local,
) -> None:
    target_path = output_dir / f"{lcsc_id}.json"
    if target_path.exists() and not force:
        logging.debug("Skipping %s (already cached)", lcsc_id)
        stats.mark_skipped()
        return

    # Each thread holds its own Session instance to avoid cross-thread issues.
    session: Optional[requests.Session] = getattr(session_factory, "session", None)
    if session is None:
        session = requests.Session()
        session.headers.update(
            {
                "User-Agent": "KiCAD-MCP-cache/1.0",
                "Accept": "application/json",
            }
        )
        session_factory.session = session

    for attempt in range(1, retries + 1):
        try:
            url = EASYEDA_API_URL.format(lcsc_id=lcsc_id)
            logging.debug("Fetching %s (attempt %d)", lcsc_id, attempt)
            response = session.get(url, timeout=timeout)
            if response.status_code != 200:
                reason = f"http {response.status_code}"
                logging.debug("Non-OK response for %s: %s", lcsc_id, reason)
                raise RuntimeError(reason)
            payload = response.json()
            ensure_dir(output_dir)
            with target_path.open("w", encoding="utf-8") as fp:
                json.dump(payload, fp, ensure_ascii=False, indent=2)
            stats.mark_fetched()
            return
        except Exception as exc:  # noqa: BLE001 - we want to capture all transient failures
            reason = f"{type(exc).__name__}: {exc}"
            if attempt == retries:
                logging.error("Failed to fetch %s after %d attempts: %s", lcsc_id, retries, reason)
                stats.mark_failed(lcsc_id, reason)
            else:
                sleep_for = delay * attempt
                logging.warning(
                    "Error fetching %s (attempt %d/%d): %s -> retrying in %.2fs",
                    lcsc_id,
                    attempt,
                    retries,
                    reason,
                    sleep_for,
                )
                time.sleep(sleep_for)
        else:
            # Should never reach here because we either return or raise.
            break
    # Respect rate limiting between ids.
    time.sleep(delay)


def download_batch(
    ids: Iterable[str],
    output_dir: Path,
    workers: int,
    force: bool,
    retries: int,
    delay: float,
    timeout: float,
) -> DownloadStats:
    stats = DownloadStats()
    ensure_dir(output_dir)

    session_factory = threading.local()
    ids_list = list(ids)
    total = len(ids_list)
    completed = 0

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                fetch_single,
                lcsc_id,
                output_dir,
                force,
                retries,
                delay,
                timeout,
                stats,
                session_factory,
            ): lcsc_id
            for lcsc_id in ids_list
        }

        for future in as_completed(futures):
            lcsc = futures[future]
            try:
                future.result()
            except Exception as exc:  # noqa: BLE001
                logging.error("Unhandled exception while fetching %s: %s", lcsc, exc)
                stats.mark_failed(lcsc, str(exc))
            finally:
                completed += 1
                percent = (completed / total) * 100 if total else 100.0
                logging.debug("Batch progress: %d/%d (%.1f%%)", completed, total, percent)
    return stats


def write_failures(failure_path: Path, failed: List[Tuple[str, str]]) -> None:
    if not failed:
        if failure_path.exists():
            failure_path.unlink()
        return
    lines = [f"{lcsc} | {reason}" for lcsc, reason in failed]
    failure_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Cache EasyEDA component JSON for offline symbol/footprint conversion.",
    )
    parser.add_argument(
        "--input",
        required=True,
        type=Path,
        help="Text file with one LCSC part number per line (comments with # are allowed).",
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="Directory where JSON cache files will be stored.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Maximum concurrent downloads (default: 4).",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.25,
        help="Base delay in seconds between requests per worker (default: 0.25).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=15.0,
        help="HTTP timeout in seconds for each request (default: 15.0).",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=3,
        help="Number of retry attempts per LCSC id before marking as failed (default: 3).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Redownload JSON even if the target file already exists.",
    )
    parser.add_argument(
        "--failed-log",
        type=Path,
        default=None,
        help="Optional path to write the list of failed LCSC ids.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices={"DEBUG", "INFO", "WARNING", "ERROR"},
        help="Verbosity for console logging.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(levelname)s: %(message)s",
    )

    lcsc_ids = parse_lcsc_list(args.input)
    if not lcsc_ids:
        logging.error("No valid LCSC ids found in %s", args.input)
        return 1

    logging.debug("Preparing to cache %d entries into %s", len(lcsc_ids), args.output)
    stats = download_batch(
        lcsc_ids,
        args.output,
        workers=max(1, args.workers),
        force=args.force,
        retries=max(1, args.retries),
        delay=max(0.0, args.delay),
        timeout=max(1.0, args.timeout),
    )

    logging.debug(
        "Completed batch: fetched=%d skipped=%d failed=%d",
        stats.fetched,
        stats.skipped,
        len(stats.failed),
    )
    if args.failed_log:
        write_failures(args.failed_log, stats.failed)
        if stats.failed:
            logging.debug("Wrote %d failures to %s", len(stats.failed), args.failed_log)
    return 0 if not stats.failed else 2


if __name__ == "__main__":
    raise SystemExit(main())
