"""
Helpers for preparing ERC-ready artifacts from standalone module sheets.

The workflow:
1. Load a module schematic.
2. Run the schematic compiler so unlabeled nets receive deterministic labels.
3. Emit a compiled copy of the module sheet.
4. Generate a lightweight harness schematic that references the compiled sheet.

The caller can then run `kicad-cli sch erc` against the harness without
touching the original design files.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

from .connection_schematic import SchematicCompiler
from .harness_utils import (
    create_harness_schematic,
    extract_hierarchical_labels_from_sheet,
    save_harness_schematic,
)
from .schematic import SchematicManager
from .library_export import export_project_libraries


def prepare_module_erc_artifacts(module_path: str, output_dir: str) -> Dict[str, Any]:
    """
    Create compiled and harness schematics for a module suitable for ERC.

    Args:
        module_path: Path to the original module `.kicad_sch` file.
        output_dir: Directory where the compiled sheet and harness should be written.

    Returns:
        Dict with keys:
            - compiledPath: The path to the compiled module sheet.
            - harnessPath: The path to the generated harness schematic.
            - compileSummary: Result dictionary returned by `SchematicCompiler.compile`.
            - labelCount: Number of hierarchical labels found on the module sheet.
    """
    module_path = os.path.abspath(os.path.expanduser(module_path))
    if not os.path.exists(module_path):
        raise FileNotFoundError(f"Schematic not found: {module_path}")

    output_dir_path = Path(output_dir)
    output_dir_path.mkdir(parents=True, exist_ok=True)

    schematic = SchematicManager.load_schematic(module_path)
    if schematic is None:
        raise RuntimeError(f"Failed to load schematic: {module_path}")

    compile_summary = SchematicCompiler.compile(schematic)
    library_export = export_project_libraries(schematic, module_path)

    module_stem = Path(module_path).stem
    compiled_filename = f"{module_stem}_compiled.kicad_sch"
    compiled_path = output_dir_path / compiled_filename
    if not SchematicManager.save_schematic(schematic, str(compiled_path)):
        raise RuntimeError(f"Failed to save compiled schematic: {compiled_path}")

    labels = extract_hierarchical_labels_from_sheet(schematic)
    harness = create_harness_schematic(
        module_id=module_stem,
        labels=labels,
        sheet_relpath=f"./{compiled_filename}",
        title=f"{module_stem} Harness",
    )

    harness_filename = f"{module_stem}_harness.kicad_sch"
    harness_path = output_dir_path / harness_filename
    save_harness_schematic(harness, str(harness_path))

    return {
        "compiledPath": str(compiled_path),
        "harnessPath": str(harness_path),
        "compileSummary": compile_summary,
        "libraryExport": library_export,
        "labelCount": len(labels),
    }
