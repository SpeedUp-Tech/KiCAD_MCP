"""
netlist_to_schematic - SKiDL to KiCad Schematic Generation Pipeline

This module provides a 3-step pipeline for converting non-geometric SKiDL netlists
into well-organized, human-readable KiCad schematics.

Pipeline Steps:
    1. elk_graph_adapter: (SKiDL Circuit + Symbol DB) -> elk_input.json
    2. elk_layout_runner.cjs: elk_input.json -> elk_output.json (via ELK engine)
    3. elk_to_kicad: elk_output.json -> .kicad_sch file
"""

from .elk_graph_adapter import SymbolGeometryFetcher, ElkGraphBuilder
from .elk_to_kicad import run_conversion

__all__ = [
    "SymbolGeometryFetcher",
    "ElkGraphBuilder",
    "run_conversion",
]
