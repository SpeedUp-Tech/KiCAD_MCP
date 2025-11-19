"""
Test routing comparison with Battery_Protection schematic as test case.

This test recreates the Battery_Protection module connections and compares
the routing quality between the old manhattan router and the improved router.
"""

import unittest
from pathlib import Path
import tempfile
import time
from contextlib import contextmanager, nullcontext
from typing import List

from python.commands.kicad_schematics.component_schematic import ComponentManager
from python.commands.kicad_schematics.connection_schematic import ConnectionManager, _collect_symbol_bboxes
from python.commands.kicad_schematics.schematic import SchematicManager
from python.router.manhattan import RoutingObstacles, coord_key, safe_manhattan_route
from python.router.manhattan_improved import (
    COLLISION_BUFFER_MM,
    POST_PROCESS_RECT_CLEARANCE_MM,
    _can_connect_directly,
    _expand_symbol_bboxes,
    _prepare_rects_for_clearance,
    _remove_unnecessary_turns,
    safe_manhattan_route,
)
from sexpdata import Symbol
from uuid import uuid4


class BatteryProtectionRoutingTest(unittest.TestCase):
    """Test routing quality on Battery_Protection schematic."""

    def setUp(self):
        """Set up test schematic with Battery_Protection components."""
        self.sch = SchematicManager.create_schematic(
            'Battery_Protection_Test',
            metadata={
                'title': 'Battery_Protection',
                'description': 'Pack protection between charger BAT (VBAT_CHG) and pack positive (VBAT_PACK)',
            }
        )

        # Add components matching Battery_Protection schematic
        # LTC4365 protection controller
        ComponentManager.add_component(self.sch, {
            'type': 'LTC4365CTS8#TRMPBF',
            'reference': 'U1',
            'x': 148.59,
            'y': 88.9,
            'library': 'Interface_ICs',
        })
        
        # Q1 - MOSFET (left side)
        ComponentManager.add_component(self.sch, {
            'type': 'SI7884DP-T1-GE3-VB',
            'reference': 'Q1',
            'x': 120.65,
            'y': 60.96,
            'library': 'Transistors_Thyristors',
        })
        
        # Q2 - MOSFET (right side, rotated 180)
        ComponentManager.add_component(self.sch, {
            'type': 'SI7884DP-T1-GE3-VB',
            'reference': 'Q2',
            'x': 176.53,
            'y': 60.96,
            'rotation': 180,
            'library': 'Transistors_Thyristors',
        })
        
        # Resistors for voltage dividers
        ComponentManager.add_component(self.sch, {
            'type': 'R',
            'reference': 'R1',
            'value': '750k 1%',
            'x': 126.62,
            'y': 81.44,
        })
        
        ComponentManager.add_component(self.sch, {
            'type': 'R',
            'reference': 'R2',
            'value': '301k 1%',
            'x': 126.62,
            'y': 103.98,
        })
        
        ComponentManager.add_component(self.sch, {
            'type': 'R',
            'reference': 'R3',
            'value': '118k 1%',
            'x': 170.56,
            'y': 81.44,
        })
        
        ComponentManager.add_component(self.sch, {
            'type': 'R',
            'reference': 'R4',
            'value': '100k 1%',
            'x': 170.56,
            'y': 103.98,
        })
        
        # Add hierarchical labels for interface
        self._add_hlabel('VBAT_CHG', 139.7, 20.32, 'input')
        self._add_hlabel('VBAT_PACK', 139.7, 180.34, 'output')
        
        # Add GND power symbol
        self._add_gnd(148.59, 106.68)
    
    def _add_hlabel(self, name: str, x: float, y: float, shape: str):
        """Add hierarchical label to schematic."""
        from sexpdata import Symbol
        from uuid import uuid4
        
        label = [
            Symbol('hierarchical_label'),
            name,
            [Symbol('shape'), Symbol(shape)],
            [Symbol('at'), x, y, 0],
            [Symbol('fields_autoplaced')],
            [Symbol('effects'), [Symbol('font'), [Symbol('size'), 1.27, 1.27]]],
            [Symbol('uuid'), Symbol(str(uuid4()))],
        ]
        self.sch.tree.append(label)
    
    def _add_gnd(self, x: float, y: float):
        """Add GND power symbol."""
        from sexpdata import Symbol
        from uuid import uuid4
        
        gnd = [
            Symbol('symbol'),
            [Symbol('lib_id'), 'power:GND'],
            [Symbol('at'), x, y, 0],
            [Symbol('unit'), 1],
            [Symbol('exclude_from_sim'), Symbol('no')],
            [Symbol('in_bom'), Symbol('yes')],
            [Symbol('on_board'), Symbol('yes')],
            [Symbol('dnp'), Symbol('no')],
            [Symbol('uuid'), Symbol(str(uuid4()))],
            [
                Symbol('property'),
                'Reference',
                '#PWR01',
                [Symbol('at'), x, y - 2.54, 0],
                [Symbol('effects'), [Symbol('font'), [Symbol('size'), 1.27, 1.27]]]
            ],
            [
                Symbol('property'),
                'Value',
                'GND',
                [Symbol('at'), x, y + 2.54, 0],
                [Symbol('effects'), [Symbol('font'), [Symbol('size'), 1.27, 1.27]]]
            ],
            [Symbol('pin'), '1', [Symbol('uuid'), Symbol(str(uuid4()))]],
        ]
        self.sch.tree.append(gnd)
    
    def test_old_routing_creates_connections(self):
        """Test that old routing system can create connections."""
        # Test a simple connection using old system
        result = ConnectionManager.connect_pins(
            self.sch,
            {'reference': 'R1', 'pin': '1'},
            {'reference': 'R2', 'pin': '1'},
        )

        self.assertIsNotNone(result)
        self.assertIn('created', result)

        # Count wires created
        wire_count = len(getattr(self.sch, 'wire', []))
        self.assertGreater(wire_count, 0)

    def test_improved_routing_basic(self):
        """Test that improved routing works on basic connection."""
        bboxes = _collect_symbol_bboxes(self.sch)
        obstacles = RoutingObstacles([], [], bboxes)

        # Simple route
        start = (126.62, 67.47)  # R1 pin 1
        end = (126.62, 100.17)    # R2 pin 1

        route = safe_manhattan_route(
            start, end,
            obstacles=obstacles,
        )

        self.assertGreaterEqual(len(route), 2)
        self.assertEqual(route[0], start)
        self.assertEqual(route[-1], end)

    def test_wire_overlap_penalty_creates_detour(self):
        """Routes should avoid sitting directly on top of an existing wire."""
        node_vertices = [
            (10.0, 10.0),
            (30.0, 10.0),
        ]
        obstacles = RoutingObstacles([], node_vertices, [])
        start = (10.0, 10.0)
        end = (30.0, 10.0)

        route = safe_manhattan_route(
            start,
            end,
            obstacles=obstacles,
        )

        self.assertGreater(len(route), 2, "Route should detour around the existing wire")
        self.assertTrue(
            any(abs(pt[1] - 10.0) > 1e-6 for pt in route[1:-1]),
            "Intermediate points should leave the overlapping row",
        )

    def test_pin_length_expansion_extends_symbol_bbox(self):
        """Pin tips must be absorbed into the symbol bbox used for routing."""

        class DummyLoc:
            def __init__(self, x, y):
                self.x = x
                self.y = y

        class DummyPin:
            def __init__(self, x, y):
                self.location = DummyLoc(x, y)

        class DummySymbol:
            def __init__(self, pins):
                self.pin = pins

        symbol = DummySymbol(
            [
                DummyPin(15.0, 5.0),   # Right-facing pin
                DummyPin(-3.0, 4.0),   # Left-facing pin
                DummyPin(5.0, 14.0),   # Top-facing pin
            ]
        )
        original_rect = (0.0, 0.0, 10.0, 10.0)

        expanded = _expand_symbol_bboxes([(original_rect, symbol)])
        expanded_rect = expanded[0][0]

        self.assertEqual(expanded_rect, (-3.0, 0.0, 15.0, 14.0))
    
    def test_turn_minimization(self):
        """Test that turn minimization reduces unnecessary bends."""
        from python.router.manhattan_improved import _remove_unnecessary_turns

        obstacles = RoutingObstacles([], [], [])

        # Create a route with unnecessary turns
        route_with_turns = [
            (10.0, 10.0),
            (20.0, 10.0),
            (20.0, 15.0),  # Unnecessary point
            (20.0, 20.0),
            (30.0, 20.0),
        ]

        optimized = _remove_unnecessary_turns(
            route_with_turns,
            rects=[],
            forbidden_points=set(),
        )

        # Should remove the middle point
        self.assertLess(len(optimized), len(route_with_turns))

        # Count turns
        turns = self._count_turns_from_points(optimized)
        self.assertLessEqual(turns, 2, "Should minimize turns")

    def test_turn_minimization_respects_existing_route_endpoints(self):
        """Ensure redundant points are only removed when no other endpoint is crossed."""

        route = [
            (0.0, 0.0),
            (20.0, 0.0),
            (20.0, 10.0),
            (0.0, 10.0),
            (0.0, 5.0),  # Candidate point that lines up with the next segment
            (0.0, -10.0),
        ]

        optimized = _remove_unnecessary_turns(
            route,
            rects=[],
            forbidden_points=set(),
        )

        self.assertIn((0.0, 5.0), optimized, "Optimizer should not pass through another endpoint")

    def test_direct_connection_enforces_body_clearance(self):
        """Direct connection checks must honor the strict body clearance margin."""

        rect = (0.0, 0.0, 10.0, 10.0)
        buffered_rects = _prepare_rects_for_clearance([rect], POST_PROCESS_RECT_CLEARANCE_MM)

        p1 = (-5.0, 10.9)
        p2 = (15.0, 10.9)

        self.assertFalse(
            _can_connect_directly(
                p1,
                p2,
                buffered_rects,
                forbidden_points=set(),
                collision_buffer=COLLISION_BUFFER_MM,
            ),
            "Direct connection should fail when hugging a symbol body",
        )

    def _wire_length_from_points(self, points) -> float:
        """Calculate total wire length from points."""
        length = 0.0
        for i in range(len(points) - 1):
            p1 = points[i]
            p2 = points[i + 1]
            length += ((p2[0] - p1[0])**2 + (p2[1] - p1[1])**2)**0.5
        return length

    def _count_turns_from_points(self, points) -> int:
        """Count number of turns in wire from points."""
        if len(points) < 3:
            return 0

        turns = 0
        for i in range(1, len(points) - 1):
            p_prev = points[i - 1]
            p_curr = points[i]
            p_next = points[i + 1]

            dx1 = p_curr[0] - p_prev[0]
            dy1 = p_curr[1] - p_prev[1]
            dx2 = p_next[0] - p_curr[0]
            dy2 = p_next[1] - p_curr[1]

            # Check if direction changes
            if abs(dx1) > abs(dy1):
                dir1 = 'H'
            else:
                dir1 = 'V'

            if abs(dx2) > abs(dy2):
                dir2 = 'H'
            else:
                dir2 = 'V'

            if dir1 != dir2:
                turns += 1

        return turns

    def test_compare_old_vs_improved_routing(self):
        """Compare old manhattan routing vs improved routing on realistic Battery_Protection schematic."""
        # Create schematics for both routing systems
        sch_old = self._create_full_battery_protection_schematic('Battery_Protection_Old')
        sch_improved = self._create_full_battery_protection_schematic('Battery_Protection_Improved')

        # Define key connections from actual Battery_Protection schematic
        # Focus on the parallel wire problem: Q1 to Q2 connections
        test_connections = [
            # Q1 to Q2 parallel connections - THE MAIN PROBLEM!
            # These 3 wires run parallel and create visual clutter
            ({'reference': 'Q1', 'pin': '1'}, {'reference': 'Q2', 'pin': '1'}),
            ({'reference': 'Q1', 'pin': '2'}, {'reference': 'Q2', 'pin': '2'}),
            ({'reference': 'Q1', 'pin': '3'}, {'reference': 'Q2', 'pin': '3'}),

            # U1 GATE to Q1 G
            ({'reference': 'U1', 'pin': '8'}, {'reference': 'Q1', 'pin': '4'}),

            # U1 VOUT to Q2 G
            ({'reference': 'U1', 'pin': '7'}, {'reference': 'Q2', 'pin': '4'}),

            # Voltage divider R1-R2 for UV
            ({'reference': 'R1', 'pin': '2'}, {'reference': 'U1', 'pin': '2'}),
            ({'reference': 'R2', 'pin': '1'}, {'reference': 'U1', 'pin': '2'}),
            ({'reference': 'R2', 'pin': '2'}, {'reference': 'U1', 'pin': '4'}),

            # Voltage divider R3-R4 for OV
            ({'reference': 'R3', 'pin': '2'}, {'reference': 'U1', 'pin': '3'}),
            ({'reference': 'R4', 'pin': '1'}, {'reference': 'U1', 'pin': '3'}),
            ({'reference': 'R4', 'pin': '2'}, {'reference': 'U1', 'pin': '4'}),

            # Additional Q1 D pins (parallel to each other - same net)
            ({'reference': 'Q1', 'pin': '9'}, {'reference': 'Q1', 'pin': '8'}),
            ({'reference': 'Q1', 'pin': '8'}, {'reference': 'Q1', 'pin': '7'}),
            ({'reference': 'Q1', 'pin': '7'}, {'reference': 'Q1', 'pin': '6'}),
            ({'reference': 'Q1', 'pin': '6'}, {'reference': 'Q1', 'pin': '5'}),

            # Additional Q2 D pins (parallel to each other - same net)
            ({'reference': 'Q2', 'pin': '9'}, {'reference': 'Q2', 'pin': '8'}),
            ({'reference': 'Q2', 'pin': '8'}, {'reference': 'Q2', 'pin': '7'}),
            ({'reference': 'Q2', 'pin': '7'}, {'reference': 'Q2', 'pin': '6'}),
            ({'reference': 'Q2', 'pin': '6'}, {'reference': 'Q2', 'pin': '5'}),
        ]

        # Route with old system
        print("\n" + "="*60)
        print("OLD ROUTING SYSTEM (Original Manhattan)")
        print("="*60)
        old_success, old_times, old_total_time = self._run_connection_set(
            sch_old,
            test_connections,
        )

        # Collect old routes for analysis
        old_wires = self._extract_wires_from_schematic(sch_old)
        old_metrics = self._analyze_wire_routes(old_wires)
        old_violations = self._check_wire_pin_collisions(sch_old, test_connections)

        print(f"Successfully routed: {old_success}/{len(test_connections)}")
        print(f"Total wires: {len(old_wires)}")
        print(f"Total length: {old_metrics['total_length']:.2f} mm")
        print(f"Total turns: {old_metrics['total_turns']}")
        print(f"Avg turns/wire: {old_metrics['avg_turns']:.2f}")
        print(f"Total time: {old_total_time:.3f}s")
        print(f"Average per connection: {old_total_time/len(test_connections):.4f}s")
        print(f"Pin collisions: {len(old_violations)}")
        if old_violations:
            print("  ⚠️  VIOLATIONS:")
            for v in old_violations[:5]:  # Show first 5
                print(f"    Wire {v['wire'][0]} -> {v['wire'][1]} passes through {v['pin_ref']} (dist: {v['distance']:.3f}mm)")

        # Route with improved system using the same ConnectionManager but swapped router
        print("\n" + "="*60)
        print("IMPROVED ROUTING SYSTEM (Enhanced Manhattan)")
        print("="*60)

        improved_success, improved_times, improved_total_time = self._run_connection_set(
            sch_improved,
            test_connections,
            router_override=safe_manhattan_route,
        )

        # Collect improved routes for analysis
        improved_wires = self._extract_wires_from_schematic(sch_improved)
        improved_metrics = self._analyze_wire_routes(improved_wires)
        improved_violations = self._check_wire_pin_collisions(sch_improved, test_connections)

        print(f"Successfully routed: {improved_success}/{len(test_connections)}")
        print(f"Total wires: {len(improved_wires)}")
        print(f"Total length: {improved_metrics['total_length']:.2f} mm")
        print(f"Total turns: {improved_metrics['total_turns']}")
        print(f"Avg turns/wire: {improved_metrics['avg_turns']:.2f}")
        print(f"Total time: {improved_total_time:.3f}s")
        print(f"Average per connection: {improved_total_time/len(test_connections):.4f}s")
        print(f"Pin collisions: {len(improved_violations)}")
        if improved_violations:
            print("  ⚠️  VIOLATIONS:")
            for v in improved_violations[:5]:  # Show first 5
                print(f"    Wire {v['wire'][0]} -> {v['wire'][1]} passes through {v['pin_ref']} (dist: {v['distance']:.3f}mm)")

        # Performance comparison
        print("\n" + "="*60)
        print("COMPARISON SUMMARY")
        print("="*60)

        # Time comparison
        speedup = old_total_time / improved_total_time if improved_total_time > 0 else 0
        print(f"\n⏱️  TIME:")
        print(f"  Old system:      {old_total_time:.3f}s")
        print(f"  Improved system: {improved_total_time:.3f}s")
        print(f"  Speedup: {speedup:.2f}x {'(IMPROVED FASTER)' if speedup > 1 else '(OLD FASTER)'}")

        # Quality comparison
        print(f"\n📊 QUALITY:")
        print(f"  Total Length:")
        print(f"    Old:      {old_metrics['total_length']:.2f} mm")
        print(f"    Improved: {improved_metrics['total_length']:.2f} mm")
        length_improvement = ((old_metrics['total_length'] - improved_metrics['total_length']) / old_metrics['total_length'] * 100) if old_metrics['total_length'] > 0 else 0
        print(f"    Change: {length_improvement:+.1f}%")

        print(f"  Total Turns:")
        print(f"    Old:      {old_metrics['total_turns']}")
        print(f"    Improved: {improved_metrics['total_turns']}")
        turn_improvement = old_metrics['total_turns'] - improved_metrics['total_turns']
        print(f"    Reduction: {turn_improvement} turns")

        print(f"  Avg Turns/Wire:")
        print(f"    Old:      {old_metrics['avg_turns']:.2f}")
        print(f"    Improved: {improved_metrics['avg_turns']:.2f}")

        print(f"  Pin Collisions (CRITICAL):")
        print(f"    Old:      {len(old_violations)}")
        print(f"    Improved: {len(improved_violations)}")
        if len(improved_violations) > 0:
            print(f"    ❌ IMPROVED SYSTEM HAS VIOLATIONS!")

        # Save schematics for visual inspection
        output_dir = Path('exported/test_routing_comparison')
        output_dir.mkdir(parents=True, exist_ok=True)

        old_sch_path = str(output_dir / 'battery_protection_old.kicad_sch')
        improved_sch_path = str(output_dir / 'battery_protection_improved.kicad_sch')

        SchematicManager.save_schematic(sch_old, old_sch_path)
        SchematicManager.save_schematic(sch_improved, improved_sch_path)

        print(f"\n" + "="*60)
        print("EXPORT")
        print("="*60)
        print(f"📁 Schematics saved to: {output_dir}")
        print(f"  Old:      {old_sch_path}")
        print(f"  Improved: {improved_sch_path}")

        # Export to SVG using kicad-cli
        import subprocess

        try:
            # Export old schematic to SVG
            old_svg_path = str(output_dir / 'battery_protection_old.svg')
            subprocess.run([
                'kicad-cli', 'sch', 'export', 'svg',
                '--output', old_svg_path,
                old_sch_path
            ], check=True, capture_output=True)

            # Export improved schematic to SVG
            improved_svg_path = str(output_dir / 'battery_protection_improved.svg')
            subprocess.run([
                'kicad-cli', 'sch', 'export', 'svg',
                '--output', improved_svg_path,
                improved_sch_path
            ], check=True, capture_output=True)

            print(f"✅ SVG exports created successfully!")
            print(f"  Old:      {old_svg_path}")
            print(f"  Improved: {improved_svg_path}")

        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            print(f"⚠️  SVG export failed (kicad-cli may not be available)")

        print("\n" + "="*60)

        # Basic assertions
        self.assertGreater(len(improved_wires), 0, "Should create wires")
        # Improved system should route at least as many as old system
        self.assertGreaterEqual(improved_success, old_success, "Improved system should route at least as many connections as old system")

        # CRITICAL: Check for pin collisions
        self.assertEqual(len(improved_violations), 0,
                        f"Improved system has {len(improved_violations)} pin collision violations! "
                        f"Wires should NEVER pass through pins they don't connect to.")

    def _create_full_battery_protection_schematic(self, name: str):
        """Create full Battery_Protection schematic with all components."""
        sch = SchematicManager.create_schematic(name, metadata={
            'title': 'Battery_Protection',
            'description': 'Pack protection between charger BAT (VBAT_CHG) and pack positive (VBAT_PACK)',
        })

        # Add all components matching actual Battery_Protection schematic
        ComponentManager.add_component(sch, {
            'type': 'LTC4365CTS8#TRMPBF',
            'reference': 'U1',
            'x': 148.59,
            'y': 88.9,
            'library': 'Interface_ICs',
        })

        ComponentManager.add_component(sch, {
            'type': 'SI7884DP-T1-GE3-VB',
            'reference': 'Q1',
            'x': 120.65,
            'y': 60.96,
            'library': 'Transistors_Thyristors',
        })

        ComponentManager.add_component(sch, {
            'type': 'SI7884DP-T1-GE3-VB',
            'reference': 'Q2',
            'x': 176.53,
            'y': 60.96,
            'rotation': 180,
            'library': 'Transistors_Thyristors',
        })

        ComponentManager.add_component(sch, {
            'type': 'R',
            'reference': 'R1',
            'value': '750k 1%',
            'x': 126.62,
            'y': 81.44,
        })

        ComponentManager.add_component(sch, {
            'type': 'R',
            'reference': 'R2',
            'value': '301k 1%',
            'x': 126.62,
            'y': 103.98,
        })

        ComponentManager.add_component(sch, {
            'type': 'R',
            'reference': 'R3',
            'value': '118k 1%',
            'x': 170.56,
            'y': 81.44,
        })

        ComponentManager.add_component(sch, {
            'type': 'R',
            'reference': 'R4',
            'value': '100k 1%',
            'x': 170.56,
            'y': 103.98,
        })

        # Add hierarchical labels
        self._add_hlabel('VBAT_CHG', 139.7, 20.32, 'input')
        self._add_hlabel('VBAT_PACK', 139.7, 180.34, 'output')

        # Add GND power symbol
        self._add_gnd(148.59, 106.68)

        return sch

    def _resolve_connection_point(self, sch, spec):
        """Resolve a connection specification to a point coordinate."""
        if 'x' in spec and 'y' in spec:
            # Direct coordinate
            return (spec['x'], spec['y'])

        if 'label' in spec:
            # Hierarchical label
            label_name = spec['label']
            if label_name == 'VBAT_CHG':
                return (139.7, 20.32)
            elif label_name == 'VBAT_PACK':
                return (139.7, 180.34)

        if 'power' in spec:
            # Power symbol
            if spec['power'] == 'GND':
                return (148.59, 106.68)

        if 'reference' in spec and 'pin' in spec:
            # Component pin - need to calculate actual pin location
            # This is simplified - in reality would need to look up pin positions
            ref = spec['reference']
            pin = spec['pin']

            # Hardcoded pin locations based on actual schematic
            # U1 at (148.59, 88.9)
            if ref == 'U1':
                u1_pins = {
                    '1': (137.16, 92.71),   # VIN
                    '2': (137.16, 90.17),   # UV
                    '3': (137.16, 87.63),   # OV
                    '4': (137.16, 85.09),   # GND
                    '5': (160.02, 85.09),   # SHDN
                    '6': (160.02, 87.63),   # FAULT
                    '7': (160.02, 90.17),   # VOUT
                    '8': (160.02, 92.71),   # GATE
                }
                return u1_pins.get(pin, (148.59, 88.9))

            # Q1 at (120.65, 60.96)
            elif ref == 'Q1':
                q1_pins = {
                    '1': (113.03, 63.5),    # S
                    '2': (113.03, 60.96),   # S
                    '3': (113.03, 58.42),   # S
                    '4': (113.03, 81.44),   # G
                    '5': (128.27, 81.44),   # D
                    '6': (128.27, 58.42),   # D
                    '7': (128.27, 60.96),   # D
                    '8': (128.27, 63.5),    # D
                    '9': (128.27, 66.04),   # D
                }
                return q1_pins.get(pin, (120.65, 60.96))

            # Q2 at (176.53, 60.96) rotated 180
            elif ref == 'Q2':
                q2_pins = {
                    '1': (184.15, 58.42),   # S (mirrored)
                    '2': (184.15, 60.96),   # S
                    '3': (184.15, 63.5),    # S
                    '4': (184.15, 66.04),   # G
                    '5': (168.91, 66.04),   # D
                    '6': (168.91, 63.5),    # D
                    '7': (168.91, 60.96),   # D
                    '8': (168.91, 58.42),   # D
                    '9': (168.91, 81.44),   # D
                }
                return q2_pins.get(pin, (176.53, 60.96))

            # Resistors
            elif ref == 'R1':
                return (126.62, 67.47) if pin == '1' else (126.62, 75.09)
            elif ref == 'R2':
                return (126.62, 100.17) if pin == '1' else (126.62, 107.79)
            elif ref == 'R3':
                return (170.56, 67.47) if pin == '1' else (170.56, 75.09)
            elif ref == 'R4':
                return (170.56, 100.17) if pin == '1' else (170.56, 107.79)

        # Fallback
        return (100.0, 100.0)

    def _run_connection_set(self, sch, connections, router_override=None):
        """Run ConnectionManager.connect_pins for a batch, optionally swapping the router."""
        ctx = self._router_override(router_override) if router_override else nullcontext()
        times: List[float] = []
        success = 0
        start_total = time.time()
        with ctx:
            for source, target in connections:
                try:
                    conn_start = time.time()
                    ConnectionManager.connect_pins(sch, source, target)
                    times.append(time.time() - conn_start)
                    success += 1
                except Exception as e:
                    print(f"  ⚠️  Failed: {e}")
                    times.append(0)
        total_time = time.time() - start_total
        return success, times, total_time

    @contextmanager
    def _router_override(self, router_fn):
        """Temporarily replace the global Manhattan router used by ConnectionManager."""
        import python.router as router_pkg
        from python.router import manhattan as manhattan_module
        import python.commands.kicad_schematics.connection_schematic as connection_module

        original_router_pkg = router_pkg.safe_manhattan_route
        original_manhattan = manhattan_module.safe_manhattan_route
        original_connection = connection_module.safe_manhattan_route
        router_pkg.safe_manhattan_route = router_fn
        manhattan_module.safe_manhattan_route = router_fn
        connection_module.safe_manhattan_route = router_fn
        try:
            yield
        finally:
            router_pkg.safe_manhattan_route = original_router_pkg
            manhattan_module.safe_manhattan_route = original_manhattan
            connection_module.safe_manhattan_route = original_connection

    def _extract_wires_from_schematic(self, sch):
        """Extract all wire polylines from schematic."""
        wires = []
        wire_segments = []

        # Collect all wire segments
        for elem in sch.tree:
            if isinstance(elem, list) and len(elem) > 0:
                if elem[0] == Symbol('wire'):
                    # Extract points from wire
                    for sub in elem:
                        if isinstance(sub, list) and sub[0] == Symbol('pts'):
                            points = []
                            for pt in sub[1:]:
                                if isinstance(pt, list) and pt[0] == Symbol('xy'):
                                    points.append((pt[1], pt[2]))
                            if len(points) == 2:
                                wire_segments.append(points)

        # Convert segments to polylines (simplified - just return segments as individual wires)
        for seg in wire_segments:
            wires.append(seg)

        return wires

    def _analyze_wire_routes(self, wires):
        """Analyze wire routes."""
        total_length = sum(self._wire_length_from_points(wire) for wire in wires)
        total_turns = sum(self._count_turns_from_points(wire) for wire in wires)

        return {
            'total_length': total_length,
            'total_turns': total_turns,
            'avg_turns': total_turns / max(len(wires), 1),
        }

    def _check_wire_pin_collisions(self, sch, test_connections):
        """
        Check if any wire passes through a pin it shouldn't connect to.

        Returns list of violations: [(wire_segment, pin_location, pin_ref)]
        """
        from python.router.manhattan_improved import point_segment_distance

        violations = []

        # Get all pin locations
        all_pins = {}
        pin_locations = {}

        for source, target in test_connections:
            if 'reference' in source and 'pin' in source:
                key = (source['reference'], source['pin'])
                loc = self._resolve_connection_point(sch, source)
                all_pins[key] = loc
                pin_locations[loc] = key
            if 'reference' in target and 'pin' in target:
                key = (target['reference'], target['pin'])
                loc = self._resolve_connection_point(sch, target)
                all_pins[key] = loc
                pin_locations[loc] = key

        # Get all wire segments
        wire_segments = self._extract_wires_from_schematic(sch)

        # For each wire segment, check if it passes through any pin
        for wire in wire_segments:
            if len(wire) != 2:
                continue

            p1, p2 = wire

            # Check against all pins
            for pin_loc, pin_key in pin_locations.items():
                # Check if this pin is an endpoint of this wire (allowed)
                eps = 1e-6
                if (abs(p1[0] - pin_loc[0]) < eps and abs(p1[1] - pin_loc[1]) < eps):
                    continue
                if (abs(p2[0] - pin_loc[0]) < eps and abs(p2[1] - pin_loc[1]) < eps):
                    continue

                # Check if pin is on or very close to the wire segment
                dist = point_segment_distance(pin_loc, p1, p2)
                if dist < 0.1:  # Very close threshold
                    violations.append({
                        'wire': (p1, p2),
                        'pin_location': pin_loc,
                        'pin_ref': f"{pin_key[0]} pin {pin_key[1]}",
                        'distance': dist,
                    })

        return violations


if __name__ == '__main__':
    unittest.main()
