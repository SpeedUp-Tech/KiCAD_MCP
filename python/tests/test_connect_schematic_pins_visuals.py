import shutil
import subprocess
import unittest
import uuid
from pathlib import Path
from typing import Dict, List, Tuple

from sexpdata import Symbol

from python.commands.schematic import SchematicManager
from python.commands.component_schematic import ComponentManager
from python.commands.connection_schematic import ConnectionManager
from python.commands.grid_utils import KICAD_SCHEMATIC_GRID_MM, snap_to_grid


# Root export directory for visual artifacts
EXPORT_ROOT = Path(__file__).resolve().parents[2] / 'exported'
EXPORT_ROOT.mkdir(parents=True, exist_ok=True)

# Center of A4 page in mm (approximate). Components will be placed near this.
CENTER_X = 105.0
CENTER_Y = 148.5


def _add_hlabel(schematic, name: str, x: float, y: float):
    """Add a hierarchical label to the schematic with grid-snapped coordinates."""
    # Snap coordinates to grid for KiCAD compliance
    x_snapped = snap_to_grid(x)
    y_snapped = snap_to_grid(y)

    node = [
        Symbol('hierarchical_label'),
        name,
        [Symbol('shape'), Symbol('input')],
        [Symbol('at'), x_snapped, y_snapped, 0],
        [Symbol('fields_autoplaced')],
        [Symbol('effects'), [Symbol('font'), [Symbol('size'), 1.27, 1.27]]],
        [Symbol('uuid'), Symbol(f'label-{name}-{uuid.uuid4().hex[:6]}')],
    ]
    schematic.tree.append(node)


def _run_kicad_cli_export_svg(schematic_path: Path, svg_path: Path) -> Tuple[bool, str]:
    exe = shutil.which('kicad-cli')
    if not exe:
        return False, 'kicad-cli not found in PATH'
    cmd = [exe, 'sch', 'export', 'svg', str(schematic_path), '--output', str(svg_path)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    ok = proc.returncode == 0 and svg_path.exists() and svg_path.stat().st_size > 0
    out = (proc.stdout or '') + '\n' + (proc.stderr or '')
    return ok, out.strip()




class ConnectSchematicPinsVisualTests(unittest.TestCase):
    """
    Visual test suite for connect_schematic_pins auto-routing.
    Generates SVGs for various scenarios under exported/connect_schematic_pins/<run>.
    """

    @classmethod
    def setUpClass(cls):
        cls.run_id = uuid.uuid4().hex[:8]
        cls.out_dir = EXPORT_ROOT / 'connect_schematic_pins' / cls.run_id
        cls.out_dir.mkdir(parents=True, exist_ok=True)

    def _save_and_export(self, sch, basename: str) -> Path:
        sch_path = self.out_dir / f"{basename}.kicad_sch"
        svg_path = self.out_dir / f"{basename}.svg"
        ok_save = SchematicManager.save_schematic(sch, str(sch_path))
        self.assertTrue(ok_save, f"Failed to save schematic for {basename}")
        ok, logs = _run_kicad_cli_export_svg(sch_path, svg_path)
        if not ok:
            self.skipTest(f"kicad-cli unavailable or export failed: {logs}")
        self.assertTrue(svg_path.exists(), f"SVG not created for {basename}")
        self.assertGreater(svg_path.stat().st_size, 0, f"Empty SVG for {basename}")
        # Clean schematic file after exporting SVG (keep SVGs for review)
        try:
            if sch_path.exists():
                sch_path.unlink()
        except Exception:
            pass
        return svg_path

    def test_visual_various_routes(self):
        grid = KICAD_SCHEMATIC_GRID_MM

        scenarios: List[Tuple[str, Dict]] = []

        # A) Straight horizontal pin-to-pin, medium spacing
        def build_A():
            sch = SchematicManager.create_schematic('A_Straight')
            ComponentManager.add_component(sch, { 'type': 'R', 'reference': 'R1', 'x': CENTER_X - 30.0, 'y': CENTER_Y })
            ComponentManager.add_component(sch, { 'type': 'R', 'reference': 'R2', 'x': CENTER_X + 30.0, 'y': CENTER_Y })
            ConnectionManager.connect_pins(sch, {'reference': 'R1', 'pin': '2'}, {'reference': 'R2', 'pin': '1'})
            # No obstacle in this scenario; we only verify export succeeds.
            return sch

        scenarios.append(("A_straight_pin2pin_hv", { 'builder': build_A }))

        # B) Diagonal alignment requiring corner (hv), no obstacles
        def build_B():
            sch = SchematicManager.create_schematic('B_Corner')
            ComponentManager.add_component(sch, { 'type': 'R', 'reference': 'R1', 'x': CENTER_X - 40.0, 'y': CENTER_Y - 20.0 })
            ComponentManager.add_component(sch, { 'type': 'R', 'reference': 'R2', 'x': CENTER_X + 40.0, 'y': CENTER_Y + 20.0 })
            ConnectionManager.connect_pins(sch, {'reference': 'R1', 'pin': '2'}, {'reference': 'R2', 'pin': '1'})
            return sch

        scenarios.append(("B_corner_pin2pin_hv", { 'builder': build_B }))

        # C) Obstacle avoidance: third symbol between endpoints
        def build_C():
            sch = SchematicManager.create_schematic('C_Obstacle')
            ComponentManager.add_component(sch, { 'type': 'R', 'reference': 'R1', 'x': CENTER_X - 60.0, 'y': CENTER_Y })
            ComponentManager.add_component(sch, { 'type': 'R', 'reference': 'R2', 'x': CENTER_X + 60.0, 'y': CENTER_Y })
            # Place obstacle slightly above the straight path to encourage a mild detour
            ComponentManager.add_component(sch, { 'type': 'R', 'reference': 'Rmid', 'x': CENTER_X, 'y': CENTER_Y + 2*grid })
            ConnectionManager.connect_pins(sch, {'reference': 'R1', 'pin': '2'}, {'reference': 'R2', 'pin': '1'})
            # Visual-only scenario; export will let us inspect any detour taken.
            return sch

        scenarios.append(("C_obstacle_avoidance_hv", { 'builder': build_C }))

        # D) Rotation effects: rotate target 90 degrees
        def build_D():
            sch = SchematicManager.create_schematic('D_Rotation')
            ComponentManager.add_component(sch, { 'type': 'R', 'reference': 'R1', 'x': CENTER_X - 40.0, 'y': CENTER_Y, 'rotation': 0 })
            ComponentManager.add_component(sch, { 'type': 'R', 'reference': 'R2', 'x': CENTER_X + 40.0, 'y': CENTER_Y, 'rotation': 90 })
            ConnectionManager.connect_pins(sch, {'reference': 'R1', 'pin': '2'}, {'reference': 'R2', 'pin': '1'})
            return sch

        scenarios.append(("D_rotation_pin2pin_hv", { 'builder': build_D }))

        # E) Label to pin, vertical then horizontal (vh)
        def build_E():
            sch = SchematicManager.create_schematic('E_LabelToPin')
            _add_hlabel(sch, 'NET1', CENTER_X - 50.0, CENTER_Y)
            ComponentManager.add_component(sch, { 'type': 'R', 'reference': 'R1', 'x': CENTER_X + 30.0, 'y': CENTER_Y + 20.0 })
            ConnectionManager.connect_pins(sch, {'label': 'NET1'}, {'reference': 'R1', 'pin': '1'})
            return sch

        scenarios.append(("E_label_to_pin_vh", { 'builder': build_E }))

        # F) Label to label across diagonal
        def build_F():
            sch = SchematicManager.create_schematic('F_LabelToLabel')
            _add_hlabel(sch, 'L_A', CENTER_X - 40.0, CENTER_Y - 10.0)
            _add_hlabel(sch, 'L_B', CENTER_X + 40.0, CENTER_Y + 30.0)
            ConnectionManager.connect_pins(sch, {'label': 'L_A'}, {'label': 'L_B'})
            return sch

        scenarios.append(("F_label_to_label_hv", { 'builder': build_F }))

        # G) Tight spacing near bodies
        def build_G():
            sch = SchematicManager.create_schematic('G_TightSpacing')
            # Place two resistors with small horizontal gap near the center
            ComponentManager.add_component(sch, { 'type': 'R', 'reference': 'R1', 'x': CENTER_X - 15.0, 'y': CENTER_Y })
            ComponentManager.add_component(sch, { 'type': 'R', 'reference': 'R2', 'x': CENTER_X - 15.0 + 6*grid, 'y': CENTER_Y })
            ConnectionManager.connect_pins(sch, {'reference': 'R1', 'pin': '2'}, {'reference': 'R2', 'pin': '1'})
            return sch

        scenarios.append(("G_tight_spacing_hv", { 'builder': build_G }))

        # H) Multiple connections in one canvas with mixed rotations and patterns
        def build_H():
            sch = SchematicManager.create_schematic('H_Multi')
            ComponentManager.add_component(sch, { 'type': 'R', 'reference': 'R1', 'x': CENTER_X - 40.0, 'y': CENTER_Y, 'rotation': 0 })
            ComponentManager.add_component(sch, { 'type': 'R', 'reference': 'R2', 'x': CENTER_X + 40.0, 'y': CENTER_Y, 'rotation': 180 })
            ComponentManager.add_component(sch, { 'type': 'R', 'reference': 'R3', 'x': CENTER_X, 'y': CENTER_Y + 30.0, 'rotation': 270 })
            _add_hlabel(sch, 'TOP', CENTER_X, CENTER_Y - 30.0)
            _add_hlabel(sch, 'RIGHT', CENTER_X + 70.0, CENTER_Y + 10.0)
            ConnectionManager.connect_pins(sch, {'reference': 'R1', 'pin': '2'}, {'reference': 'R2', 'pin': '1'})
            ConnectionManager.connect_pins(sch, {'reference': 'R3', 'pin': '1'}, {'label': 'TOP'})
            ConnectionManager.connect_pins(sch, {'reference': 'R2', 'pin': '2'}, {'label': 'RIGHT'})
            return sch

        scenarios.append(("H_multi_mixed", { 'builder': build_H }))

        # I) LED horizontal - tests LED with rotation 0 (horizontal orientation)
        def build_I():
            sch = SchematicManager.create_schematic('I_LED_Horizontal')
            ComponentManager.add_component(sch, { 'type': 'R', 'reference': 'R1', 'x': CENTER_X - 30.0, 'y': CENTER_Y, 'rotation': 0 })
            ComponentManager.add_component(sch, { 'type': 'LED', 'reference': 'D1', 'x': CENTER_X + 10.0, 'y': CENTER_Y, 'rotation': 0 })
            ConnectionManager.connect_pins(sch, {'reference': 'R1', 'pin': '2'}, {'reference': 'D1', 'pin': '2'})
            return sch

        scenarios.append(("I_LED_horizontal", { 'builder': build_I }))

        # J) LED vertical - tests LED with rotation 90 (vertical orientation)
        def build_J():
            sch = SchematicManager.create_schematic('J_LED_Vertical')
            ComponentManager.add_component(sch, { 'type': 'R', 'reference': 'R1', 'x': CENTER_X - 30.0, 'y': CENTER_Y, 'rotation': 0 })
            ComponentManager.add_component(sch, { 'type': 'LED', 'reference': 'D1', 'x': CENTER_X + 10.0, 'y': CENTER_Y, 'rotation': 90 })
            ConnectionManager.connect_pins(sch, {'reference': 'R1', 'pin': '2'}, {'reference': 'D1', 'pin': '2'})
            return sch

        scenarios.append(("J_LED_vertical", { 'builder': build_J }))

        # K) LED circuit - complete LED with current limiting resistor (the failing test case scenario)
        def build_K():
            sch = SchematicManager.create_schematic('K_LED_Circuit')
            # Resistor vertical (rotation 0)
            ComponentManager.add_component(sch, { 'type': 'R', 'reference': 'R1', 'value': '330', 'x': CENTER_X - 20.0, 'y': CENTER_Y, 'rotation': 0 })
            # LED vertical (rotation 90)
            ComponentManager.add_component(sch, { 'type': 'LED', 'reference': 'D1', 'x': CENTER_X + 20.0, 'y': CENTER_Y, 'rotation': 90 })
            # Connect R1.2 to D1.2 (should be simple horizontal line)
            ConnectionManager.connect_pins(sch, {'reference': 'R1', 'pin': '2'}, {'reference': 'D1', 'pin': '2'})
            # Connect R1.1 to D1.1 (should be simple horizontal line at different Y)
            ConnectionManager.connect_pins(sch, {'reference': 'R1', 'pin': '1'}, {'reference': 'D1', 'pin': '1'})
            return sch

        scenarios.append(("K_LED_circuit", { 'builder': build_K }))

        # L) LED with different rotations - tests all 4 rotations
        def build_L():
            sch = SchematicManager.create_schematic('L_LED_Rotations')
            ComponentManager.add_component(sch, { 'type': 'LED', 'reference': 'D1', 'x': CENTER_X - 30.0, 'y': CENTER_Y - 30.0, 'rotation': 0 })
            ComponentManager.add_component(sch, { 'type': 'LED', 'reference': 'D2', 'x': CENTER_X + 30.0, 'y': CENTER_Y - 30.0, 'rotation': 90 })
            ComponentManager.add_component(sch, { 'type': 'LED', 'reference': 'D3', 'x': CENTER_X - 30.0, 'y': CENTER_Y + 30.0, 'rotation': 180 })
            ComponentManager.add_component(sch, { 'type': 'LED', 'reference': 'D4', 'x': CENTER_X + 30.0, 'y': CENTER_Y + 30.0, 'rotation': 270 })
            # Connect D1 to D2
            ConnectionManager.connect_pins(sch, {'reference': 'D1', 'pin': '1'}, {'reference': 'D2', 'pin': '2'})
            # Connect D3 to D4
            ConnectionManager.connect_pins(sch, {'reference': 'D3', 'pin': '2'}, {'reference': 'D4', 'pin': '1'})
            return sch

        scenarios.append(("L_LED_rotations", { 'builder': build_L }))

        # Execute scenarios
        for name, cfg in scenarios:
            sch = cfg['builder']()
            _ = self._save_and_export(sch, name)


if __name__ == '__main__':
    unittest.main()

