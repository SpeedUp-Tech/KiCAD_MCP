#!/usr/bin/env python3
"""Generate MCP tool description metadata from Python command docstrings."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from tool_doc_map import collect_tool_docs

DEFAULT_OUTPUTS = (
    Path("src/generated/tool_descriptions.json"),
    Path("dist/generated/tool_descriptions.json"),
)


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Export MCP tool descriptions for TypeScript registration.")
    parser.add_argument(
        "--out",
        action="append",
        default=None,
        help="Output path for the generated JSON. May be provided multiple times.",
    )
    args = parser.parse_args()

    outputs = [Path(p) for p in args.out] if args.out else list(DEFAULT_OUTPUTS)
    docs = collect_tool_docs()
    payload = json.dumps(docs, indent=2, ensure_ascii=False)

    for path in outputs:
        _ensure_parent(path)
        path.write_text(payload, encoding="utf-8")

    print(f"Wrote tool descriptions for {len(docs)} tool(s) to: {', '.join(str(p) for p in outputs)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

