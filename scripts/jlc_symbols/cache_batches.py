#!/usr/bin/env python3
"""
Iterate over all batch files and cache EasyEDA JSON in a single command.

Assumes you already generated batch text files (one `Cxxxx` per line) using
`export_nonprimitive_batches.py`. This wrapper walks the directory, invokes
`cache_easyeda_json.py` on each batch in turn, and keeps track of successes and
failures so you can resume later if needed.
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from pathlib import Path
from typing import List, Optional


def run_cache_script(
    batch_file: Path,
    cache_dir: Path,
    failed_dir: Path,
    workers: int,
    delay: float,
    retries: int,
    timeout: float,
    force: bool,
    log_level: str,
) -> int:
    failed_log = failed_dir / f"{batch_file.stem}_failed.txt"
    cmd: List[str] = [
        sys.executable,
        str(Path(__file__).parent / "cache_easyeda_json.py"),
        "--input",
        str(batch_file),
        "--output",
        str(cache_dir),
        "--workers",
        str(workers),
        "--delay",
        str(delay),
        "--retries",
        str(retries),
        "--timeout",
        str(timeout),
        "--failed-log",
        str(failed_log),
        "--log-level",
        log_level,
    ]
    if force:
        cmd.append("--force")

    logging.info("Caching batch %s", batch_file.name)
    result = subprocess.run(cmd, check=False)
    if result.returncode == 0:
        logging.info("Batch %s completed successfully", batch_file.name)
    elif result.returncode == 2:
        logging.warning("Batch %s completed with failures (see %s)", batch_file.name, failed_log)
    else:
        logging.error("Batch %s exited with code %d", batch_file.name, result.returncode)
    return result.returncode


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Process every batch file in a directory and cache EasyEDA JSON sequentially.",
    )
    parser.add_argument(
        "--batches",
        required=True,
        type=Path,
        help="Directory containing batch text files (e.g., batch_0001.txt).",
    )
    parser.add_argument(
        "--cache",
        required=True,
        type=Path,
        help="Directory where JSON cache files should be stored.",
    )
    parser.add_argument(
        "--failed-dir",
        type=Path,
        default=None,
        help="Directory to store failure logs (defaults to <cache>/failed).",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Maximum concurrent downloads per batch (default: 4).",
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
        help="Retry attempts per LCSC id before marking as failed (default: 3).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Redownload JSON even if cache files already exist.",
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

    batch_dir = args.batches
    cache_dir = args.cache
    failed_dir = args.failed_dir or (cache_dir / "failed")

    if not batch_dir.exists():
        logging.error("Batch directory %s does not exist", batch_dir)
        return 1

    batch_files = sorted(batch_dir.glob("batch_*.txt"))
    if not batch_files:
        logging.error("No batch_*.txt files found in %s", batch_dir)
        return 1

    cache_dir.mkdir(parents=True, exist_ok=True)
    failed_dir.mkdir(parents=True, exist_ok=True)

    exit_code = 0
    total_batches = len(batch_files)
    for index, batch_file in enumerate(batch_files, start=1):
        logging.info("Batch %d/%d: %s", index, total_batches, batch_file.name)
        result = run_cache_script(
            batch_file,
            cache_dir,
            failed_dir,
            max(1, args.workers),
            max(0.0, args.delay),
            max(1, args.retries),
            max(1.0, args.timeout),
            args.force,
            args.log_level,
        )
        if result != 0:
            exit_code = result
    logging.info("Completed processing %d batch files.", total_batches)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
