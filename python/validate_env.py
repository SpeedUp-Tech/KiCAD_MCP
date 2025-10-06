#!/usr/bin/env python3
"""
Environment validator for KiCAD MCP server.

Checks:
- Config file presence and keys
- Python executable/path correctness
- Importability of required Python modules (pcbnew, kicad-skip, sexpdata, PIL, cairosvg, mcp)
- Availability of kicad-cli
- Node build presence (dist/kicad-server.js)

Exit code is non-zero if any critical checks fail.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "default-config.json"


def run(cmd: List[str], env: Optional[Dict[str, str]] = None, timeout: int = 10) -> Tuple[int, str, str]:
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=timeout)
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def load_config(path: Path) -> Dict[str, object]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def check_python_import(py_exec: str, module: str, pythonpath: str = "") -> Tuple[bool, str]:
    code = (
        "import os,sys;"
        "pp=os.environ.get('PYTHONPATH','');"
        "paths=[p for p in pp.split(os.pathsep) if p];"
        "sys.path[:0]=[p for p in paths if p not in sys.path];"
        "ok='';err='';\n"
        "try:\n"
        "    import importlib;"
        "    importlib.invalidate_caches();"
    )
    if module == "PIL":
        code += "    import PIL as _m; ok='PIL'\n"
    else:
        code += f"    import {module} as _m; ok='{module}'\n"
    code += (
        "except Exception as e:\n"
        "    err=str(e)\n"
        "import json; print(json.dumps({'ok':ok,'err':err}))"
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = pythonpath
    rc, out, err = run([py_exec, "-c", code], env=env)
    if rc != 0:
        return False, f"subprocess failed: {err or out}"
    try:
        payload = json.loads(out.splitlines()[-1])
    except Exception:
        return False, f"unexpected output: {out[:200]} {err[:200]}"
    if payload.get("ok"):
        return True, "ok"
    return False, payload.get("err", "unknown import error")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate KiCAD MCP environment")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Path to config JSON")
    parser.add_argument("--python-exec", default=None, help="Override Python executable for pcbnew/modules checks")
    parser.add_argument("--python-path", default=None, help="Override PYTHONPATH entries (pathsep-separated)")
    args = parser.parse_args()

    cfg_path = Path(args.config)
    cfg = load_config(cfg_path)

    # Resolve Python executable and path
    py_exec = (
        args.python_exec
        or str(cfg.get("pythonExecutable") or "python3")
    )
    python_path = args.python_path if args.python_path is not None else str(cfg.get("pythonPath") or "")

    # Check that the Python executable exists (or is resolvable in PATH)
    resolved_py = shutil.which(py_exec) if os.sep not in py_exec else py_exec
    checks: List[Tuple[str, bool, str]] = []

    if os.sep in py_exec:
        checks.append((f"python exec exists: {py_exec}", Path(py_exec).exists(), py_exec))
    else:
        checks.append((f"python exec in PATH: {py_exec}", resolved_py is not None, resolved_py or ""))

    # Module import checks
    modules = ["pcbnew", "skip", "sexpdata", "PIL", "cairosvg", "mcp"]
    for mod in modules:
        ok, msg = check_python_import(resolved_py or py_exec, mod, python_path)
        checks.append((f"import {mod}", ok, msg))

    # kicad-cli availability
    kicad_cli = os.environ.get("KICAD_CLI") or shutil.which("kicad-cli")
    checks.append(("kicad-cli available", kicad_cli is not None, kicad_cli or ""))

    # Node build presence
    dist_server = ROOT / "dist" / "kicad-server.js"
    checks.append(("dist/kicad-server.js present", dist_server.exists(), str(dist_server)))

    # Summarize
    print("KiCAD MCP environment validation:")
    failures = 0
    for name, ok, detail in checks:
        status = "OK" if ok else "FAIL"
        print(f" - {name:<35} : {status} {('('+str(detail)+')') if detail else ''}")
        if not ok:
            failures += 1

    if failures:
        print("\nOne or more checks failed. Hints:")
        print(" - Ensure KiCad is installed: pcbnew and kicad-cli come from KiCad.")
        print(" - If pcbnew import failed: pass --python-exec pointing to KiCad's Python, and --python-path to its site-packages.")
        print(" - Set KICAD_CLI env var or add kicad-cli to PATH for export/ERC tools.")
        print(" - Run 'npm install && npm run build' to generate dist/ if missing.")
    else:
        print("\nAll checks passed.")

    return 0 if failures == 0 else 2


if __name__ == "__main__":
    sys.exit(main())

