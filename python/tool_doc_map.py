"""Utilities for sourcing MCP tool descriptions from command docstrings."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Dict, Iterable, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]

ToolPath = Tuple[str, ...]

# Map of MCP tool name -> (relative python file, attribute path to docstring)
TOOL_DOC_SOURCES: Dict[str, Tuple[str, ToolPath]] = {
    "create_schematic": ("python/commands/kicad_schematics/schematic.py", ("SchematicManager", "create_schematic")),
    "load_schematic": ("python/commands/kicad_schematics/schematic.py", ("SchematicManager", "load_schematic")),
    "add_schematic_component": (
        "python/commands/kicad_schematics/component_schematic.py",
        ("ComponentManager", "add_component"),
    ),
    "update_schematic_component": (
        "python/commands/kicad_schematics/component_schematic.py",
        ("ComponentManager", "update_component"),
    ),
    "remove_schematic_component": (
        "python/commands/kicad_schematics/component_schematic.py",
        ("ComponentManager", "remove_component"),
    ),
    "add_schematic_wire": ("python/commands/kicad_schematics/connection_schematic.py", ("ConnectionManager", "add_wire")),
    "remove_schematic_connection": (
        "python/commands/kicad_schematics/connection_schematic.py",
        ("ConnectionManager", "remove_connection"),
    ),
    "connect_schematic_pins": (
        "python/commands/kicad_schematics/connection_schematic.py",
        ("ConnectionManager", "connect_pins"),
    ),
    "compile_schematic": (
        "python/commands/kicad_schematics/connection_schematic.py",
        ("SchematicCompiler", "compile"),
    ),
    "run_module_erc": (
        "python/kicad_interface.py",
        ("KiCADInterface", "_handle_run_module_erc"),
    ),
    "run_skidl_erc": (
        "python/kicad_interface.py",
        ("KiCADInterface", "_handle_run_skidl_erc"),
    ),
    "execute_skidl_netlist": (
        "python/kicad_interface.py",
        ("KiCADInterface", "_handle_execute_skidl_netlist"),
    ),
    "list_schematic_libraries": (
        "python/commands/database_tools/library_schematic.py",
        ("LibraryManager", "list_available_libraries"),
    ),
    "export_schematic_pdf": ("python/commands/pcb/export.py", ("ExportCommands", "export_pdf")),
    "export_schematic_svg": ("python/commands/pcb/export.py", ("ExportCommands", "export_svg")),
    "run_erc": ("python/kicad_interface.py", ("KiCADInterface", "_handle_run_erc")),
    "export_schematic_netlist": ("python/kicad_interface.py", ("KiCADInterface", "_handle_export_netlist")),
    "export_schematic_bom": ("python/kicad_interface.py", ("KiCADInterface", "_handle_export_schematic_bom")),
    "generate_hierarchical_schematic": (
        "python/kicad_interface.py",
        ("KiCADInterface", "_handle_generate_hierarchical_schematic"),
    ),
    "get_schematic_state": ("python/commands/kicad_schematics/schematic_state.py", ("get_schematic_state",)),
    "search_mpn_part": ("python/commands/database_tools/component_search.py", ("ComponentSearchCommands", "search_mpn_part")),
    "search_datasheet": ("python/commands/database_tools/component_search.py", ("ComponentSearchCommands", "search_datasheet")),
    "run_spice_simulation_testcase": (
        "python/spice_tools/testbench_runner.py",
        ("run_use_case",),
    ),
    "convert_skidl_module": (
        "python/spice_tools/pyspice_converter.py",
        ("convert_skidl_module",),
    ),
    "run_spice_harness_sanity_check": (
        "python/spice_tools/harness_sanity.py",
        ("harness_sanity_check",),
    ),
    "validate_spice_model": (
        "python/spice_tools/utils.py",
        ("validate_spice_model",),
    ),
    "search_spice_model": (
        "python/kicad_interface.py",
        ("KiCADInterface", "_handle_search_spice_model"),
    ),
    "save_part_model": (
        "python/kicad_interface.py",
        ("KiCADInterface", "_handle_save_part_model"),
    ),
    "generate_schematic_from_netlist": (
        "python/netlist_schematic_pipeline.py",
        ("generate_schematic_from_skidl_module",),
    ),
    "build_symbol_from_template": (
        "python/kicad_interface.py",
        ("KiCADInterface", "_handle_build_symbol_from_template"),
    ),
    "search_footprint": (
        "python/kicad_interface.py",
        ("KiCADInterface", "_handle_search_footprint"),
    ),
}


def _find_child(node: ast.AST, name: str) -> ast.AST | None:
    iterable: Iterable[ast.AST]
    if isinstance(node, ast.Module):
        iterable = node.body
    elif isinstance(node, (ast.ClassDef, ast.AsyncFunctionDef, ast.FunctionDef)):
        iterable = node.body
    else:
        return None

    for child in iterable:
        if isinstance(child, ast.ClassDef) and child.name == name:
            return child
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name == name:
            return child
    return None


def _extract_from_path(module_ast: ast.Module, attr_path: ToolPath) -> str | None:
    node: ast.AST = module_ast
    for part in attr_path:
        next_node = _find_child(node, part)
        if next_node is None:
            return None
        node = next_node
    if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return ast.get_docstring(node)
    return None


def collect_tool_docs() -> Dict[str, str]:
    """Collect tool descriptions from the authoritative Python command docstrings."""
    docs: Dict[str, str] = {}
    for tool_name, (relative_path, attr_path) in TOOL_DOC_SOURCES.items():
        file_path = REPO_ROOT / relative_path
        if not file_path.exists():
            continue
        try:
            source = file_path.read_text(encoding="utf-8")
            module_ast = ast.parse(source, filename=str(file_path))
            doc = _extract_from_path(module_ast, attr_path)
        except (OSError, SyntaxError):
            doc = None
        if doc:
            docs[tool_name] = doc.strip()
    return docs


__all__ = ["collect_tool_docs", "TOOL_DOC_SOURCES"]
