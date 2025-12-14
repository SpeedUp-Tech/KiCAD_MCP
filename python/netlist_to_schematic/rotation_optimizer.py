"""
Rotation Optimizer for Schematic Layout

Finds optimal rotations for non-primitive components by running ELK
layout with all rotation combinations and selecting the best result.

Non-primitives are components whose reference prefix is NOT in the 
PRIMITIVE_PREFIXES set (ICs, MOSFETs, specialized components).
"""

import json
import math
import subprocess
from itertools import product
from pathlib import Path
from typing import Any

# Primitive component names - these don't need rotation optimization
# These are checked against part.name (e.g., "R", "C", "LED"), not reference
PRIMITIVE_NAMES = frozenset([
    "R",     # Resistor
    "C",     # Capacitor  
    "L",     # Inductor
    "D",     # Diode
    "LED",   # LED (also a diode)
    "Q",     # Transistor
    "M",     # MOSFET
    "J",     # Jack/Connector
    "S",     # Switch
    "B",     # Battery
    "T",     # Transformer
    "V",     # Voltage source
    "I",     # Current source
    "E",     # Voltage-controlled voltage source
    "F",     # Current-controlled current source
    "G",     # Voltage-controlled current source
    "H",     # Current-controlled voltage source
    "NTC_10K", # NTC thermistor (specific part name)
])

# Maximum non-primitives before skipping rotation optimization
MAX_NON_PRIMITIVES = 4

# Scoring weights
WIRE_LENGTH_WEIGHT = 1.0
BEND_PENALTY = 10.0
CROSSING_PENALTY = 50.0


def get_ref_prefix(ref: str) -> str:
    """Extract the letter prefix from a reference designator (e.g., 'U1' -> 'U')."""
    prefix = ""
    for char in ref:
        if char.isalpha():
            prefix += char
        else:
            break
    return prefix.upper()


def is_non_primitive(part) -> bool:
    """
    Check if a part requires rotation optimization.
    
    Primitives are determined by part.name (e.g., 'R', 'C', 'LED')
    not by the reference prefix.
    """
    name = getattr(part, "name", "").upper()
    return name not in PRIMITIVE_NAMES


def is_power_symbol_id(node_id: str) -> bool:
    """Check if a node ID represents a power symbol (not rotated)."""
    return node_id.startswith("GND_") or node_id.startswith("VCC_") or node_id.startswith("PWR_")


def rotate_point(x: float, y: float, angle_deg: float) -> tuple[float, float]:
    """
    Rotate a point around origin by angle_deg degrees.
    
    KiCad convention: 0=right, 90=up, 180=left, 270=down
    """
    angle_rad = math.radians(angle_deg)
    cos_a = math.cos(angle_rad)
    sin_a = math.sin(angle_rad)
    new_x = x * cos_a - y * sin_a
    new_y = x * sin_a + y * cos_a
    return (new_x, new_y)


def apply_rotation_to_pins(pins: dict[str, Any], rotation: int) -> dict[str, Any]:
    """
    Apply rotation to all pin positions.
    
    Args:
        pins: Dict of pin_num -> {x, y, rot, length, conn_x, conn_y}
        rotation: Rotation in degrees (0, 90, 180, 270)
        
    Returns:
        New pins dict with rotated coordinates
    """
    if rotation == 0:
        return pins
    
    rotated = {}
    for pin_num, geo in pins.items():
        # Rotate the base position
        new_x, new_y = rotate_point(geo["x"], geo["y"], rotation)
        # Rotate the connection point
        new_conn_x, new_conn_y = rotate_point(geo["conn_x"], geo["conn_y"], rotation)
        # Add rotation to the pin's own rotation
        new_rot = (geo.get("rot", 0) + rotation) % 360
        
        rotated[pin_num] = {
            "x": new_x,
            "y": new_y,
            "rot": new_rot,
            "length": geo.get("length", 2.54),
            "conn_x": new_conn_x,
            "conn_y": new_conn_y,
        }
    return rotated


def apply_rotation_to_bbox(bbox: dict[str, float], rotation: int) -> dict[str, float]:
    """
    Apply rotation to bounding box.
    
    Args:
        bbox: {min_x, max_x, min_y, max_y}
        rotation: Rotation in degrees (0, 90, 180, 270)
        
    Returns:
        New bounding box after rotation
    """
    if rotation == 0:
        return bbox
    
    # Get the four corners
    corners = [
        (bbox["min_x"], bbox["min_y"]),
        (bbox["max_x"], bbox["min_y"]),
        (bbox["max_x"], bbox["max_y"]),
        (bbox["min_x"], bbox["max_y"]),
    ]
    
    # Rotate all corners
    rotated_corners = [rotate_point(x, y, rotation) for x, y in corners]
    
    # Find new bounding box
    xs = [c[0] for c in rotated_corners]
    ys = [c[1] for c in rotated_corners]
    
    return {
        "min_x": min(xs),
        "max_x": max(xs),
        "min_y": min(ys),
        "max_y": max(ys),
    }


def distance(p1: dict[str, float], p2: dict[str, float]) -> float:
    """Calculate Euclidean distance between two points."""
    dx = p2.get("x", 0) - p1.get("x", 0)
    dy = p2.get("y", 0) - p1.get("y", 0)
    return math.sqrt(dx * dx + dy * dy)


def count_edge_crossings(edges: list[dict]) -> int:
    """
    Count the number of edge crossings in the layout.
    
    Uses line segment intersection detection.
    """
    segments = []
    
    # Extract all line segments from edges
    for edge in edges:
        for section in edge.get("sections", []):
            start = section.get("startPoint", {})
            end = section.get("endPoint", {})
            bends = section.get("bendPoints", [])
            
            # Build segment list: start -> bends -> end
            points = [start] + bends + [end]
            for i in range(len(points) - 1):
                p1, p2 = points[i], points[i + 1]
                segments.append((
                    (p1.get("x", 0), p1.get("y", 0)),
                    (p2.get("x", 0), p2.get("y", 0))
                ))
    
    # Count crossings
    crossings = 0
    for i in range(len(segments)):
        for j in range(i + 1, len(segments)):
            if segments_intersect(segments[i], segments[j]):
                crossings += 1
    
    return crossings


def ccw(A: tuple, B: tuple, C: tuple) -> bool:
    """Check if three points are in counter-clockwise order."""
    return (C[1] - A[1]) * (B[0] - A[0]) > (B[1] - A[1]) * (C[0] - A[0])


def segments_intersect(seg1: tuple, seg2: tuple) -> bool:
    """Check if two line segments intersect (excluding shared endpoints)."""
    A, B = seg1
    C, D = seg2
    
    # Check for shared endpoints (not a crossing)
    if A == C or A == D or B == C or B == D:
        return False
    
    return ccw(A, C, D) != ccw(B, C, D) and ccw(A, B, C) != ccw(A, B, D)


def score_layout(elk_output: dict) -> float:
    """
    Score a layout - lower is better.
    
    Scoring considers:
    - Total wire length
    - Number of bends
    - Edge crossings
    """
    total_length = 0.0
    total_bends = 0
    
    edges = elk_output.get("edges", [])
    
    for edge in edges:
        for section in edge.get("sections", []):
            start = section.get("startPoint", {})
            end = section.get("endPoint", {})
            bends = section.get("bendPoints", [])
            
            # Calculate segment lengths
            points = [start] + bends + [end]
            for i in range(len(points) - 1):
                total_length += distance(points[i], points[i + 1])
            
            # Count bends
            total_bends += len(bends)
    
    # Count crossings
    crossings = count_edge_crossings(edges)
    
    score = (
        total_length * WIRE_LENGTH_WEIGHT +
        total_bends * BEND_PENALTY +
        crossings * CROSSING_PENALTY
    )
    
    return score


def run_elk_layout(elk_input: dict, work_dir: Path) -> dict:
    """Run ELK layout and return the output graph."""
    elk_runner = Path(__file__).parent / "elk" / "elk_layout_runner.cjs"
    
    input_path = work_dir / "_rotation_opt_input.json"
    output_path = work_dir / "_rotation_opt_output.json"
    
    with open(input_path, "w") as f:
        json.dump(elk_input, f)
    
    result = subprocess.run(
        ["node", str(elk_runner), str(input_path), str(output_path)],
        capture_output=True,
        text=True
    )
    
    if result.returncode != 0:
        raise RuntimeError(f"ELK layout failed: {result.stderr}")
    
    with open(output_path) as f:
        elk_output = json.load(f)
    
    # Cleanup
    input_path.unlink(missing_ok=True)
    output_path.unlink(missing_ok=True)
    
    return elk_output


def find_non_primitive_parts(circuit) -> list:
    """Find all non-primitive parts in the circuit."""
    return [p for p in circuit.parts if is_non_primitive(p)]


def optimize_rotations(
    circuit,
    logic_hints: dict,
    fetcher,
    builder_class,
    work_dir: Path,
    verbose: bool = True
) -> tuple[dict, dict[str, int]]:
    """
    Find optimal rotations for non-primitive components.
    
    Args:
        circuit: SKiDL Circuit object
        logic_hints: Logic hints dict
        fetcher: SymbolGeometryFetcher instance
        builder_class: ElkGraphBuilder class
        work_dir: Working directory for temp files
        verbose: Print progress
        
    Returns:
        Tuple of (best_elk_output, rotation_map)
        rotation_map is {ref: rotation_degrees}
    """
    non_primitives = find_non_primitive_parts(circuit)
    
    if not non_primitives:
        if verbose:
            print("  No non-primitive components to optimize")
        # Run single layout with no rotations
        builder = builder_class(circuit, logic_hints, fetcher, {})
        elk_graph = builder.build_graph()
        elk_output = run_elk_layout(elk_graph, work_dir)
        return elk_output, {}
    
    if len(non_primitives) > MAX_NON_PRIMITIVES:
        if verbose:
            print(f"  {len(non_primitives)} non-primitives > max {MAX_NON_PRIMITIVES}, skipping optimization")
        builder = builder_class(circuit, logic_hints, fetcher, {})
        elk_graph = builder.build_graph()
        elk_output = run_elk_layout(elk_graph, work_dir)
        return elk_output, {}
    
    refs = [p.ref for p in non_primitives]
    rotations = [0, 90, 180, 270]
    combinations = list(product(rotations, repeat=len(non_primitives)))
    
    if verbose:
        print(f"  Optimizing rotations for {len(non_primitives)} components: {refs}")
        print(f"  Testing {len(combinations)} rotation combinations...")
    
    best_score = float("inf")
    best_output = None
    best_rotation_map: dict[str, int] = {}
    
    for i, combo in enumerate(combinations):
        rotation_map = {ref: rot for ref, rot in zip(refs, combo)}
        
        # Build graph with this rotation configuration
        builder = builder_class(circuit, logic_hints, fetcher, rotation_map)
        elk_graph = builder.build_graph()
        
        # Run ELK layout
        elk_output = run_elk_layout(elk_graph, work_dir)
        
        # Score the result
        score = score_layout(elk_output)
        
        if score < best_score:
            best_score = score
            best_output = elk_output
            best_rotation_map = rotation_map.copy()
        
        if verbose and (i + 1) % 16 == 0:
            print(f"    Tested {i + 1}/{len(combinations)} combinations...")
    
    if verbose:
        print(f"  Best rotation: {best_rotation_map} (score: {best_score:.1f})")
    
    return best_output, best_rotation_map
