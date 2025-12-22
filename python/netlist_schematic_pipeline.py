"""
Schematic Generation Pipeline

Converts a SKiDL circuit into a KiCad schematic with auto-layout.

Pipeline stages:
1. SKiDL Circuit + Logic Hints → ELK Graph (elk_input.json)
2. ELK Graph → Laid out graph (elk_output.json)  
3. Laid out graph → KiCad Schematic (.kicad_sch)
"""

import json
import subprocess
from pathlib import Path

# Import pipeline components
from python.netlist_to_schematic.elk_graph_adapter import ElkGraphBuilder, SymbolGeometryFetcher
from python.netlist_to_schematic.elk_to_kicad import run_conversion
from python.netlist_to_schematic.rotation_optimizer import optimize_rotations


def generate_schematic(
    circuit,
    output_path: str,
    logic_hints_path: str | None = None,
    keep_intermediate: bool = False,
    verbose: bool = True,
    optimize_rotation: bool = False
) -> str:
    """
    Generate a KiCad schematic from a SKiDL circuit.
    
    Args:
        circuit: A SKiDL Circuit object with parts and nets defined
        output_path: Path for the output .kicad_sch file
        logic_hints_path: Optional path to logic hints JSON file
        keep_intermediate: If True, keep elk_input.json and elk_output.json
        verbose: If True, print progress messages
        optimize_rotation: If True, find optimal rotations for non-primitive components
        
    Returns:
        Path to the generated schematic file
        
    Example:
        from skidl import Circuit, Part
        
        circuit = Circuit()
        with circuit:
            u1 = Part("Device", "R", value="10k")
            u2 = Part("Device", "C", value="100nF")
            u1[2] += u2[1]  # Connect R to C
            
        generate_schematic(circuit, "my_circuit.kicad_sch")
    """
    output_file = Path(output_path)
    work_dir = output_file.parent
    base_name = output_file.stem
    
    elk_input_path = work_dir / f"{base_name}_elk_input.json"
    elk_output_path = work_dir / f"{base_name}_elk_output.json"
    
    # Load logic hints if provided
    logic_hints: dict = {}
    if logic_hints_path:
        with open(logic_hints_path) as f:
            logic_hints = json.load(f)
        if verbose:
            print(f"Loaded logic hints from: {logic_hints_path}")
    
    # Stage 1: Generate ELK graph
    if verbose:
        print("Stage 1: Generating ELK graph...")
    
    fetcher = SymbolGeometryFetcher()
    
    # Stage 2: Run ELK layout (with optional rotation optimization)
    if optimize_rotation:
        if verbose:
            print("Stage 2: Optimizing rotations and running ELK layout...")
        elk_output, rotation_map = optimize_rotations(
            circuit, logic_hints, fetcher, ElkGraphBuilder, work_dir, verbose
        )
        # Save the ELK output for Stage 3
        with open(elk_output_path, "w") as f:
            json.dump(elk_output, f, indent=2)
        if rotation_map and verbose:
            print(f"  Rotations applied: {rotation_map}")
    else:
        # Standard single-run ELK layout
        builder = ElkGraphBuilder(circuit, logic_hints, fetcher, {})
        elk_graph = builder.build_graph()
        
        with open(elk_input_path, "w") as f:
            json.dump(elk_graph, f, indent=2)
        
        node_count = len(elk_graph.get("children", []))
        edge_count = len(elk_graph.get("edges", []))
        if verbose:
            print(f"  Created {node_count} nodes, {edge_count} edges")
        
        if verbose:
            print("Stage 2: Running ELK layout engine...")
        
        elk_runner = Path(__file__).parent / "netlist_to_schematic" / "elk" / "elk_layout_runner.cjs"
        
        result = subprocess.run(
            ["node", str(elk_runner), str(elk_input_path), str(elk_output_path)],
            capture_output=True,
            text=True
        )
        
        if result.returncode != 0:
            raise RuntimeError(f"ELK layout failed: {result.stderr}")
        
        if verbose:
            print("  Layout complete")
    
    # Stage 3: Convert to KiCad schematic
    if verbose:
        print("Stage 3: Converting to KiCad schematic...")
    
    run_conversion(str(elk_output_path), str(output_file))
    
    if verbose:
        print(f"  Saved: {output_file}")
    
    # Cleanup intermediate files if not keeping them
    if not keep_intermediate:
        elk_input_path.unlink(missing_ok=True)
        elk_output_path.unlink(missing_ok=True)
    
    if verbose:
        print("Done!")
    
    return str(output_file)


def generate_schematic_svg(
    circuit,
    output_path: str,
    logic_hints_path: str | None = None,
    keep_intermediate: bool = False,
    verbose: bool = True,
    optimize_rotation: bool = False
) -> tuple[str, str]:
    """
    Generate both KiCad schematic and SVG export.
    
    Args:
        circuit: A SKiDL Circuit object
        output_path: Path for the output .kicad_sch file
        logic_hints_path: Optional path to logic hints JSON
        keep_intermediate: If True, keep elk_input.json and elk_output.json
        verbose: Print progress messages
        optimize_rotation: If True, find optimal rotations for non-primitive components
        
    Returns:
        Tuple of (schematic_path, svg_path)
    """
    sch_path = generate_schematic(
        circuit, output_path, logic_hints_path, 
        keep_intermediate=keep_intermediate, verbose=verbose,
        optimize_rotation=optimize_rotation
    )
    
    svg_dir = Path(sch_path).parent / f"{Path(sch_path).stem}_svg"
    
    if verbose:
        print("Exporting to SVG...")
    
    result = subprocess.run(
        ["kicad-cli", "sch", "export", "svg", 
         "--output", str(svg_dir), str(sch_path)],
        capture_output=True,
        text=True
    )
    
    if result.returncode != 0:
        raise RuntimeError(f"SVG export failed: {result.stderr}")
    
    svg_path = svg_dir / f"{Path(sch_path).stem}.svg"
    
    if verbose:
        print(f"  SVG: {svg_path}")
    
    return sch_path, str(svg_path)


def generate_schematic_from_skidl_module(
    skidl_module_path: str,
    subcircuit_name: str,
    output_path: str,
    logic_hints_path: str | None = None,
    export_svg: bool = False,
    verify: bool = True,
    optimize_rotation: bool = False,
    keep_intermediate: bool = False,
) -> dict:
    """
    Generate a KiCad schematic from a SKiDL module file.
    
    This is the MCP-compatible entry point for the netlist-to-schematic pipeline.
    It loads a SKiDL module, instantiates the specified subcircuit, and generates
    a complete KiCad schematic with automatic layout using ELK.
    
    Args:
        skidl_module_path: Path to the Python file containing SKiDL subcircuit definitions.
            The file should define one or more @SubCircuit-decorated functions.
        subcircuit_name: Name of the SubCircuit function to instantiate (e.g., "IP2312_CHARGER").
            Must be a function decorated with @SubCircuit in the SKiDL module.
        output_path: Path for the output .kicad_sch file.
        logic_hints_path: Optional path to a JSON file containing layout hints.
            The hints can include layout_chains, cluster_hierarchy, power_symbols, etc.
        export_svg: If True, also export the schematic to SVG format.
        verify: If True, verify the generated schematic against the original netlist.
        optimize_rotation: If True, optimize component rotations for minimal wire bends.
        keep_intermediate: If True, keep intermediate elk_input.json and elk_output.json files.
    
    Returns:
        A dict with keys:
            - success: bool indicating if generation succeeded
            - schematic_path: path to the generated .kicad_sch file
            - svg_path: path to SVG (if export_svg=True)
            - parts_count: number of parts in the circuit
            - nets_count: number of nets in the circuit
            - verification_passed: bool (if verify=True)
            - message: error message if success=False
    
    Example:
        result = generate_schematic_from_skidl_module(
            skidl_module_path="modules/charger.py",
            subcircuit_name="USB_CHARGER",
            output_path="output/charger.kicad_sch",
            logic_hints_path="modules/charger_logic.json",
            export_svg=True,
            verify=True
        )
    """
    import importlib.util
    import inspect
    from skidl import Circuit, Net
    
    try:
        # Load the SKiDL module
        spec = importlib.util.spec_from_file_location("skidl_module", skidl_module_path)
        if spec is None or spec.loader is None:
            return {
                "success": False,
                "message": f"Could not load module from {skidl_module_path}"
            }
        
        skidl_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(skidl_module)
        
        # Get the subcircuit function
        if not hasattr(skidl_module, subcircuit_name):
            available = [n for n in dir(skidl_module) if not n.startswith("_")]
            return {
                "success": False,
                "message": f"Module does not have subcircuit '{subcircuit_name}'. Available: {available}"
            }
        
        subcircuit_func = getattr(skidl_module, subcircuit_name)
        
        # Create circuit and instantiate subcircuit
        circuit = Circuit()
        circuit.no_files = True
        
        # Introspect subcircuit to find required nets
        sig = inspect.signature(subcircuit_func)
        param_names = [p for p in sig.parameters if p != "tag"]
        
        with circuit:
            net_args = {name: Net(name) for name in param_names}
            subcircuit_func(**net_args, tag=subcircuit_name)
        
        parts_count = len(circuit.parts)
        nets_count = len(circuit.nets)
        
        # Generate schematic
        svg_path = None
        if export_svg:
            sch_path, svg_path = generate_schematic_svg(
                circuit, output_path, logic_hints_path,
                keep_intermediate=keep_intermediate,
                verbose=False,
                optimize_rotation=optimize_rotation
            )
        else:
            sch_path = generate_schematic(
                circuit, output_path, logic_hints_path,
                keep_intermediate=keep_intermediate,
                verbose=False,
                optimize_rotation=optimize_rotation
            )
        
        result: dict = {
            "success": True,
            "schematic_path": sch_path,
            "parts_count": parts_count,
            "nets_count": nets_count,
        }
        
        if svg_path:
            result["svg_path"] = svg_path
        
        # Verify if requested
        if verify:
            from python.netlist_to_schematic.netlist_comparator import verify_schematic
            verification_passed = verify_schematic(circuit, sch_path, verbose=True)
            result["verification_passed"] = verification_passed
            if not verification_passed:
                result["verification_warning"] = "Schematic connectivity does not fully match SKiDL netlist"
        
        return result
        
    except Exception as e:
        import traceback
        return {
            "success": False,
            "message": str(e),
            "traceback": traceback.format_exc()
        }


if __name__ == "__main__":
    import argparse
    import importlib.util
    
    parser = argparse.ArgumentParser(
        description="Generate KiCad schematic from SKiDL circuit",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Generate schematic from a SKiDL module
  python -m python.netlist_schematic_pipeline \\
      --skidl-module test_cases/test_charger_3A/skidl/modules/ip2312_charger.py \\
      --subcircuit IP2312_CHARGER \\
      --logic-hints test_cases/test_charger_3A/skidl/modules/ip2312_logic.json \\
      --output output.kicad_sch \\
      --svg
"""
    )
    
    parser.add_argument(
        "--skidl-module", "-m",
        required=True,
        help="Path to Python file containing SKiDL subcircuit definition"
    )
    parser.add_argument(
        "--subcircuit", "-s",
        required=True,
        help="Name of the SubCircuit function to use (e.g., IP2312_CHARGER)"
    )
    parser.add_argument(
        "--logic-hints", "-l",
        help="Path to logic hints JSON file (optional)"
    )
    parser.add_argument(
        "--output", "-o",
        default="output.kicad_sch",
        help="Output .kicad_sch file path (default: output.kicad_sch)"
    )
    parser.add_argument(
        "--svg",
        action="store_true",
        help="Also export to SVG"
    )
    parser.add_argument(
        "--keep-intermediate",
        action="store_true",
        help="Keep intermediate elk_input.json and elk_output.json files"
    )
    parser.add_argument(
        "--optimize-rotation",
        action="store_true",
        help="Find optimal rotations for non-primitive components (ICs, etc.)"
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Verify schematic by comparing netlists with original SKiDL circuit"
    )
    
    args = parser.parse_args()
    
    # Dynamically load the SKiDL module
    spec = importlib.util.spec_from_file_location("skidl_module", args.skidl_module)
    if spec is None or spec.loader is None:
        print(f"Error: Could not load module from {args.skidl_module}")
        exit(1)
    
    skidl_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(skidl_module)
    
    # Get the subcircuit function
    if not hasattr(skidl_module, args.subcircuit):
        print(f"Error: Module does not have subcircuit '{args.subcircuit}'")
        print(f"Available: {[n for n in dir(skidl_module) if not n.startswith('_')]}")
        exit(1)
    
    subcircuit_func = getattr(skidl_module, args.subcircuit)
    
    # Create circuit
    from skidl import Circuit, Net
    circuit = Circuit()
    circuit.no_files = True
    
    # Introspect subcircuit to find required nets
    import inspect
    sig = inspect.signature(subcircuit_func)
    param_names = [p for p in sig.parameters if p != "tag"]
    
    # Create nets and call subcircuit INSIDE the circuit context
    with circuit:
        net_args = {name: Net(name) for name in param_names}
        subcircuit_func(**net_args, tag=args.subcircuit)
    
    print(f"Loaded circuit: {len(circuit.parts)} parts, {len(circuit.nets)} nets")
    
    # Generate schematic
    if args.svg:
        sch_path, svg_path = generate_schematic_svg(
            circuit, args.output, args.logic_hints,
            keep_intermediate=args.keep_intermediate,
            optimize_rotation=args.optimize_rotation
        )
        print(f"\nOutput schematic: {sch_path}")
        print(f"Output SVG: {svg_path}")
    else:
        sch_path = generate_schematic(
            circuit, args.output, args.logic_hints,
            keep_intermediate=args.keep_intermediate,
            optimize_rotation=args.optimize_rotation
        )
        print(f"\nOutput schematic: {sch_path}")
    
    # Verify schematic if requested
    if args.verify:
        from python.netlist_to_schematic.netlist_comparator import verify_schematic
        print("\n--- Verification ---")
        passed = verify_schematic(circuit, sch_path)
        if not passed:
            print("\nWARNING: Schematic verification failed!")
            exit(1)

