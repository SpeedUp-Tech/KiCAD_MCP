"""Utilities for sourcing MCP tool descriptions from command docstrings."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Dict, Iterable, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]

ToolPath = Tuple[str, ...]

# Map of MCP tool name -> (relative python file, attribute path to docstring)
TOOL_DOC_SOURCES: Dict[str, Tuple[str, ToolPath]] = {
    "create_schematic": ("python/commands/schematic.py", ("SchematicManager", "create_schematic")),
    "load_schematic": ("python/commands/schematic.py", ("SchematicManager", "load_schematic")),
    "add_schematic_component": (
        "python/commands/component_schematic.py",
        ("ComponentManager", "add_component"),
    ),
    "update_schematic_component": (
        "python/commands/component_schematic.py",
        ("ComponentManager", "update_component"),
    ),
    "remove_schematic_component": (
        "python/commands/component_schematic.py",
        ("ComponentManager", "remove_component"),
    ),
    "add_schematic_wire": ("python/commands/connection_schematic.py", ("ConnectionManager", "add_wire")),
    "remove_schematic_connection": (
        "python/commands/connection_schematic.py",
        ("ConnectionManager", "remove_connection"),
    ),
    "connect_schematic_pins": (
        "python/commands/connection_schematic.py",
        ("ConnectionManager", "connect_pins"),
    ),
    "list_schematic_libraries": (
        "python/commands/library_schematic.py",
        ("LibraryManager", "list_available_libraries"),
    ),
    "export_schematic_pdf": ("python/commands/export.py", ("ExportCommands", "export_pdf")),
    "export_schematic_svg": ("python/commands/export.py", ("ExportCommands", "export_svg")),
    "run_erc": ("python/kicad_interface.py", ("KiCADInterface", "_handle_run_erc")),
    "export_schematic_netlist": ("python/kicad_interface.py", ("KiCADInterface", "_handle_export_netlist")),
    "export_schematic_bom": ("python/kicad_interface.py", ("KiCADInterface", "_handle_export_schematic_bom")),
    "generate_hierarchical_schematic": (
        "python/kicad_interface.py",
        ("KiCADInterface", "_handle_generate_hierarchical_schematic"),
    ),
    "get_schematic_state": ("python/commands/schematic_state.py", ("get_schematic_state",)),
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
    return ast.get_docstring(node)


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
