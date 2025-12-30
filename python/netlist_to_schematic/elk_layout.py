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
) -> tuple[dict[str, Any], str]:
    """Run ELK layout and return the output graph with paper size.
    
    Returns:
        Tuple of (laid_out_graph, paper_size) where paper_size is "A4" or "A3".
    """
    return run_clustered_layout(
        elk_input,
        work_dir,
        base_name=base_name,
        keep_intermediate=keep_intermediate,
    )

