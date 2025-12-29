"""
elk_layout - Step 2: Run ELK Layout

This project uses a clustered layout strategy (see `elk_cluster_layout.py`) to avoid
collapsed/narrow layouts for disconnected subgraphs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from python.netlist_to_schematic.elk_cluster_layout import run_clustered_layout


def run_elk_layout(
    elk_input: dict[str, Any],
    work_dir: Path,
    *,
    base_name: str = "elk",
    keep_intermediate: bool = False,
) -> dict[str, Any]:
    """Run ELK layout and return the output graph."""
    return run_clustered_layout(
        elk_input,
        work_dir,
        base_name=base_name,
        keep_intermediate=keep_intermediate,
    )

