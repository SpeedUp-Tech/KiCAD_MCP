#!/usr/bin/env python3
"""
Example script demonstrating free-form component search.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .service import search_mpn_part


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Search JLCPCB components by free-form specs.")
    parser.add_argument("db_path", help="Path to jlcpcb-components.sqlite3")
    parser.add_argument("query", help='Search text, e.g. "P-MOSFET 30V Rds_on<=20mΩ Id>=10A"')
    parser.add_argument("--limit", type=int, default=10, help="Number of results to display")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    db_path = Path(args.db_path).expanduser()

    result = search_mpn_part(args.query, limit=args.limit, db_path=db_path)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
