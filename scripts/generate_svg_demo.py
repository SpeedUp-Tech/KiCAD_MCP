from __future__ import annotations

import os
import sys
import uuid
import json
import shutil
import subprocess
from pathlib import Path

# Repo root
ROOT = Path(__file__).resolve().parents[1]
EXPORT_DIR = ROOT / 'exported'
EXPORT_DIR.mkdir(parents=True, exist_ok=True)

# Prefer configured python executable for environment parity if present
CONFIG_PATH = ROOT / 'config' / 'default-config.json'
PY_EXEC = None
try:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
            PY_EXEC = cfg.get('pythonExecutable')
except Exception:
    PY_EXEC = None

# Import project commands
sys.path.insert(0, str(ROOT))
from python.commands.kicad_schematics.schematic import SchematicManager  # type: ignore
from python.commands.kicad_schematics.component_schematic import ComponentManager  # type: ignore
from python.commands.kicad_schematics.connection_schematic import ConnectionManager  # type: ignore


def run_kicad_cli_export_svg(schematic_path: Path, svg_path: Path) -> tuple[bool, str]:
    """Run kicad-cli to export schematic to SVG."""
    # Resolve kicad-cli
    exe = shutil.which('kicad-cli')
    if not exe:
        return False, 'kicad-cli not found in PATH'
    cmd = [
        exe,
        'sch', 'export', 'svg',
        str(schematic_path),
        '--output', str(svg_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    ok = proc.returncode == 0 and svg_path.exists() and svg_path.stat().st_size > 0
    out = proc.stdout + '\n' + proc.stderr
    return ok, out.strip()


def main() -> int:
    # 1) Create schematic
    sch = SchematicManager.create_schematic('AutoRouteDemo')

    # 2) Add components positioned to exercise outward-escape + dogleg
    # R1 at (50, 60), R2 at (110, 60) same row; then connect R1.2 to R2.1
    # Also add a C1 above to create an obstacle band so router prefers up-across-down
    ComponentManager.add_component(sch, {
        'type': 'R', 'reference': 'R1', 'x': 50.0, 'y': 60.0,
    })
    ComponentManager.add_component(sch, {
        'type': 'R', 'reference': 'R2', 'x': 110.0, 'y': 60.0,
    })
    ComponentManager.add_component(sch, {
        'type': 'C', 'reference': 'C1', 'x': 80.0, 'y': 40.0,
    })

    # 3) Connect pins (this uses the updated connection logic with outward stubs)
    ConnectionManager.connect_pins(
        sch,
        {'reference': 'R1', 'pin': '2'},
        {'reference': 'R2', 'pin': '1'},
    )

    # 4) Save schematic into a temp folder under exported/
    run_id = uuid.uuid4().hex[:8]
    out_dir = EXPORT_DIR / f'route_demo_{run_id}'
    out_dir.mkdir(parents=True, exist_ok=True)
    sch_path = out_dir / 'demo.kicad_sch'
    ok_save = SchematicManager.save_schematic(sch, str(sch_path))
    if not ok_save:
        print(json.dumps({'success': False, 'message': 'Failed to save schematic', 'dir': str(out_dir)}))
        return 1

    # 5) Export SVG
    svg_path = out_dir / 'demo.svg'
    ok, logs = run_kicad_cli_export_svg(sch_path, svg_path)

    payload = {
        'success': bool(ok),
        'schematicPath': str(sch_path),
        'svgPath': str(svg_path),
        'logs': logs,
        'dir': str(out_dir),
    }
    print(json.dumps(payload))
    return 0 if ok else 2


if __name__ == '__main__':
    raise SystemExit(main())

