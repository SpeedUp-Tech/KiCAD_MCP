"""
Compatibility shim for importing `kicad_catalog` from the KiCAD_MCP repo root.

KiCAD_MCP code is used in two common ways:
- As a package via `import python.*` (repo root on `sys.path`)
- As standalone scripts that add `python/` to `sys.path` (so `kicad_catalog` is top-level)

The canonical implementation lives in `python/kicad_catalog/`. This shim makes
`import kicad_catalog` and `from kicad_catalog.<mod> import ...` work in the
package-style layout without requiring callers to change their imports.
"""

from __future__ import annotations

import importlib
import sys

from python.kicad_catalog import *  # noqa: F403
from python.kicad_catalog import __all__  # noqa: F401

for _name in ("config", "sqlite", "workdir"):
    sys.modules[f"{__name__}.{_name}"] = importlib.import_module(
        f"python.kicad_catalog.{_name}"
    )

