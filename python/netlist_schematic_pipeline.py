"""
Schematic Generation Pipeline

Converts a SKiDL circuit into a KiCad schematic with auto-layout.

Pipeline stages:
1. SKiDL Circuit + Logic Hints → ELK Graph (elk_input.json)
2. ELK Graph → Laid out graph (elk_output.json)  
3. Laid out graph → KiCad Schematic (.kicad_sch)
"""

import json
import os
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable
from uuid import uuid4

from sexpdata import Symbol

# Import pipeline components
from python.netlist_to_schematic.elk_graph_adapter import ElkGraphBuilder, SymbolGeometryFetcher
from python.netlist_to_schematic.elk_layout import run_elk_layout
from python.netlist_to_schematic.elk_to_kicad import run_conversion
from python.netlist_to_schematic.simple_logic import build_simple_logic_hints
from python.netlist_to_schematic.rotation_optimizer import optimize_rotations
from python.commands.kicad_schematics.grid_utils import snap_to_grid
from python.commands.kicad_schematics.harness_utils import get_root_uuid, rebuild_sheet_instances_at_end
from python.commands.kicad_schematics.schematic import SchematicManager


@contextmanager
def _safe_working_dir(preferred: Path):
    """Ensure a valid working directory exists during SKiDL imports."""

    previous = None
    try:
        previous = os.getcwd()
    except OSError:
        previous = None

    fallback_dirs = [preferred, Path(__file__).resolve().parents[1], Path("/tmp")]
    for candidate in fallback_dirs:
        try:
            os.chdir(candidate)
            break
        except OSError:
            continue

    try:
        yield
    finally:
        if previous is not None:
            try:
                os.chdir(previous)
            except OSError:
                pass


def generate_schematic(
    circuit,
    output_path: str,
    logic_hints_path: str | None = None,
    keep_intermediate: bool = False,
    verbose: bool = True,
    optimize_rotation: bool = False,
    interface_nets: set[str] | None = None,
    use_direct_connections: bool = False,
    label_high_fanout_nets: bool = False,
    high_fanout_threshold: int = 3,
    cluster_components: bool = False,
    cluster_max_connections: int = 12,
    cluster_min_size: int = 4,
    cluster_max_size: int = 0,
    cluster_ignore_fanout_ge: int = 8,
    cluster_ignore_nets: set[str] | None = None,
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
        interface_nets: Optional set of interface net names for hierarchical labels
        use_direct_connections: If True, emit direct label/power connections with no ordering hints
        label_high_fanout_nets: If True, labelize high-fanout nets to reduce crossings
        high_fanout_threshold: Minimum number of connected component refs for a net to be treated as high-fanout
        cluster_components: If True, split dense circuits into visually separated clusters on one sheet
        cluster_max_connections: Max "connection complexity" per cluster (branching + cycles; used with cluster_components)
        cluster_min_size: Merge clusters smaller than this (used with cluster_components)
        cluster_max_size: Optional hard cap on parts per cluster (0 disables; used with cluster_components)
        cluster_ignore_fanout_ge: Ignore nets with fanout >= this when computing clusters (used with cluster_components)
        cluster_ignore_nets: Optional set of net names to ignore when computing clusters (used with cluster_components)
        
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
    output_file = Path(output_path).resolve()
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
    if not logic_hints.get("elk_support_fields"):
        if verbose:
            print("Using auto-generated layout hints (satellite rules).")
        logic_hints = build_simple_logic_hints(
            circuit,
            interface_nets=interface_nets,
            use_direct_connections=use_direct_connections,
            label_high_fanout_nets=label_high_fanout_nets,
            high_fanout_threshold=high_fanout_threshold,
            cluster_components=cluster_components,
            cluster_max_connections=cluster_max_connections,
            cluster_min_size=cluster_min_size,
            cluster_max_size=cluster_max_size,
            cluster_ignore_fanout_ge=cluster_ignore_fanout_ge,
            cluster_ignore_nets=cluster_ignore_nets,
        )
    
    # Stage 1: Generate ELK graph
    if verbose:
        print("Stage 1: Generating ELK graph...")
    
    fetcher = SymbolGeometryFetcher()
    
    # Stage 2: Run ELK layout (with optional rotation optimization)
    if optimize_rotation:
        if verbose:
            print("Stage 2: Optimizing rotations and running ELK layout...")
        elk_output, paper_size, rotation_map = optimize_rotations(
            circuit, logic_hints, fetcher, ElkGraphBuilder, work_dir, verbose
        )
        # Save the ELK output for Stage 3
        with open(elk_output_path, "w") as f:
            json.dump(elk_output, f, indent=2)
        if verbose:
            print(f"  Paper size: {paper_size}")
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
            print("Stage 2: Running clustered ELK layout...")

        elk_output, paper_size = run_elk_layout(
            elk_graph,
            work_dir,
            base_name=base_name,
            keep_intermediate=keep_intermediate,
        )

        with open(elk_output_path, "w") as f:
            json.dump(elk_output, f, indent=2)

        if verbose:
            print(f"  Layout complete (paper: {paper_size})")
    
    # Stage 3: Convert to KiCad schematic
    if verbose:
        print("Stage 3: Converting to KiCad schematic...")
    
    run_conversion(str(elk_output_path), str(output_file), paper_size=paper_size)
    
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
    optimize_rotation: bool = False,
    interface_nets: set[str] | None = None,
    use_direct_connections: bool = False,
    label_high_fanout_nets: bool = False,
    high_fanout_threshold: int = 3,
    cluster_components: bool = False,
    cluster_max_connections: int = 12,
    cluster_min_size: int = 4,
    cluster_max_size: int = 0,
    cluster_ignore_fanout_ge: int = 8,
    cluster_ignore_nets: set[str] | None = None,
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
        interface_nets: Optional set of interface net names for hierarchical labels
        use_direct_connections: If True, emit direct label/power connections with no ordering hints
        label_high_fanout_nets: If True, labelize high-fanout nets to reduce crossings
        high_fanout_threshold: Minimum number of connected component refs for a net to be treated as high-fanout
        cluster_components: If True, split dense circuits into visually separated clusters on one sheet
        cluster_max_connections: Max "connection complexity" per cluster (branching + cycles; used with cluster_components)
        cluster_min_size: Merge clusters smaller than this (used with cluster_components)
        cluster_max_size: Optional hard cap on parts per cluster (0 disables; used with cluster_components)
        cluster_ignore_fanout_ge: Ignore nets with fanout >= this when computing clusters (used with cluster_components)
        cluster_ignore_nets: Optional set of net names to ignore when computing clusters (used with cluster_components)
        
    Returns:
        Tuple of (schematic_path, svg_path)
    """
    sch_path = generate_schematic(
        circuit, output_path, logic_hints_path, 
        keep_intermediate=keep_intermediate, verbose=verbose,
        optimize_rotation=optimize_rotation,
        interface_nets=interface_nets,
        use_direct_connections=use_direct_connections,
        label_high_fanout_nets=label_high_fanout_nets,
        high_fanout_threshold=high_fanout_threshold,
        cluster_components=cluster_components,
        cluster_max_connections=cluster_max_connections,
        cluster_min_size=cluster_min_size,
        cluster_max_size=cluster_max_size,
        cluster_ignore_fanout_ge=cluster_ignore_fanout_ge,
        cluster_ignore_nets=cluster_ignore_nets,
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


def generate_top_schematic_from_contract(
    *,
    module_sheets: Dict[str, str],
    signals: Iterable[Dict[str, Any]] = (),
    rails: Iterable[Dict[str, Any]] = (),
    output_path: str,
    export_svg: bool = False,
    relative_sheet_paths: bool = True,
    paper: str = "A3",
    columns: int = 3,
    origin_x: float = 40.0,
    origin_y: float = 40.0,
    x_spacing: float = 80.0,
    y_spacing: float = 60.0,
    sheet_width: float = 50.0,
    sheet_height: float = 40.0,
) -> Dict[str, Any]:
    """
    Generate a top-level *hierarchical* KiCad schematic from a pre-extracted contract.

    Contract (source of truth):
    - `module_sheets`: module_id -> module schematic path (stored verbatim in "Sheet file")
    - `signals`: items with keys:
        - signal_id: str (pin/net name)
        - source: str (module_id)
        - sinks: list[str] (module_id list)
        - direction: "source->sink" | "bidirectional" (controls pin type only)
    - `rails`: items with keys:
        - rail_id: str (pin/net name)
        - primary_source_kind: usually "module" or "external"
        - primary_source_ref: module_id if primary_source_kind == "module"
        - consumers: list[str] (module_id list)

    Output:
    - One sheet symbol per module_id (a box).
    - Sheet pins are placed on left/right:
        - rails/signals sourced by the module -> right side
        - rails/signals consumed by the module -> left side
    - No wires or extra labels are drawn; sheet pins display the net names.

    Notes:
    - This function does not read/validate module `.kicad_sch` contents; it only references paths.
    - For portable KiCad projects, prefer relative sheet file paths (e.g. `modules/{module_id}.kicad_sch`).
      If you pass absolute paths and want them rewritten relative to the output schematic folder, set
      `relative_sheet_paths=True`.
    """

    def _normalise_direction(value: Any) -> str:
        text = str(value or "").strip().lower()
        if text == "source->sink":
            return "source->sink"
        if text == "bidirectional":
            return "bidirectional"
        return "bidirectional"

    def _add_pin(
        side_map: Dict[str, str],
        opposite_map: Dict[str, str],
        name: str,
        direction: str,
    ) -> None:
        if not name:
            return
        if name in opposite_map:
            opposite_map.pop(name, None)
        existing = side_map.get(name)
        if existing is None:
            side_map[name] = direction
            return
        if existing == direction:
            return
        if existing == "bidirectional" or direction == "bidirectional":
            side_map[name] = "bidirectional"

    def _create_sheet_node(
        module_id: str,
        sheet_file: str,
        x: float,
        y: float,
        width: float,
        height: float,
        left_pins: Dict[str, str],
        right_pins: Dict[str, str],
    ) -> tuple[list, str, list]:
        sheet_uuid = str(uuid4())

        x_snapped = snap_to_grid(x)
        y_snapped = snap_to_grid(y)
        w_snapped = snap_to_grid(width)
        h_snapped = snap_to_grid(height)

        sheet_node: list = [
            Symbol("sheet"),
            [Symbol("at"), x_snapped, y_snapped],
            [Symbol("size"), w_snapped, h_snapped],
            [Symbol("stroke"), [Symbol("width"), 0], [Symbol("type"), Symbol("solid")], [Symbol("color"), 0, 0, 0, 0]],
            [Symbol("fill"), [Symbol("color"), 0, 0, 0, 0.0]],
            [Symbol("uuid"), Symbol(sheet_uuid)],
            [
                Symbol("property"),
                "Sheet name",
                module_id,
                [Symbol("id"), 0],
                [Symbol("at"), x_snapped, y_snapped - 2.5, 0],
                [Symbol("effects"), [Symbol("font"), [Symbol("size"), 1.27, 1.27]], [Symbol("justify"), Symbol("left"), Symbol("bottom")]],
            ],
            [
                Symbol("property"),
                "Sheet file",
                sheet_file,
                [Symbol("id"), 1],
                [Symbol("at"), x_snapped, y_snapped + h_snapped + 0.5, 0],
                [
                    Symbol("effects"),
                    [Symbol("font"), [Symbol("size"), 1.27, 1.27]],
                    [Symbol("justify"), Symbol("left"), Symbol("top")],
                    [Symbol("hide"), Symbol("yes")],
                ],
            ],
        ]

        labels: list = []
        pin_offset = 10.0
        pin_step = 5.0

        for name in sorted(left_pins):
            pin_y = snap_to_grid(y_snapped + pin_offset)
            sheet_node.append([
                Symbol("pin"),
                name,
                Symbol(left_pins[name]),
                [Symbol("at"), x_snapped + w_snapped, pin_y, 180],
                [
                    Symbol("effects"),
                    [Symbol("font"), [Symbol("size"), 1.27, 1.27]],
                    [Symbol("justify"), Symbol("right")],
                ],
                [Symbol("uuid"), Symbol(str(uuid4()))],
            ])
            pin_offset += pin_step

        for name in sorted(right_pins):
            pin_y = snap_to_grid(y_snapped + pin_offset)
            sheet_node.append([
                Symbol("pin"),
                name,
                Symbol(right_pins[name]),
                [Symbol("at"), x_snapped, pin_y, 0],
                [
                    Symbol("effects"),
                    [Symbol("font"), [Symbol("size"), 1.27, 1.27]],
                    [Symbol("justify"), Symbol("left")],
                ],
                [Symbol("uuid"), Symbol(str(uuid4()))],
            ])
            pin_offset += pin_step

        return sheet_node, sheet_uuid, labels

    try:
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)

        schematic = SchematicManager.create_schematic(
            output_file.stem or "Top",
            metadata={
                "paper": paper,
                "title": output_file.stem or "Top",
            },
        )

        if not isinstance(schematic.tree, list):
            raise RuntimeError("Top schematic tree is not a list")

        tree = schematic.tree
        root_uuid = get_root_uuid(tree)

        left_by_module: Dict[str, Dict[str, str]] = {mid: {} for mid in module_sheets}
        right_by_module: Dict[str, Dict[str, str]] = {mid: {} for mid in module_sheets}

        for rail in rails or []:
            rail_id = str(rail.get("rail_id") or "").strip()
            if not rail_id:
                continue

            source_kind = str(rail.get("primary_source_kind") or "").strip().lower()
            source_ref = str(rail.get("primary_source_ref") or "").strip()
            consumers = rail.get("consumers") or []

            if source_kind == "module" and source_ref in module_sheets:
                _add_pin(
                    right_by_module[source_ref],
                    left_by_module[source_ref],
                    rail_id,
                    "bidirectional",
                )

            for consumer in consumers:
                consumer_id = str(consumer or "").strip()
                if consumer_id in module_sheets:
                    _add_pin(
                        left_by_module[consumer_id],
                        right_by_module[consumer_id],
                        rail_id,
                        "bidirectional",
                    )

        for sig in signals or []:
            sig_id = str(sig.get("signal_id") or "").strip()
            if not sig_id:
                continue

            direction = _normalise_direction(sig.get("direction"))
            source = str(sig.get("source") or "").strip()
            sinks = sig.get("sinks") or []

            if direction == "source->sink":
                if source in module_sheets:
                    _add_pin(
                        right_by_module[source],
                        left_by_module[source],
                        sig_id,
                        "output",
                    )
                for sink in sinks:
                    sink_id = str(sink or "").strip()
                    if sink_id in module_sheets:
                        _add_pin(
                            left_by_module[sink_id],
                            right_by_module[sink_id],
                            sig_id,
                            "input",
                        )
            else:
                if source in module_sheets:
                    _add_pin(
                        right_by_module[source],
                        left_by_module[source],
                        sig_id,
                        "bidirectional",
                    )
                for sink in sinks:
                    sink_id = str(sink or "").strip()
                    if sink_id in module_sheets:
                        _add_pin(
                            left_by_module[sink_id],
                            right_by_module[sink_id],
                            sig_id,
                            "bidirectional",
                        )

        sheet_uuids: Dict[str, str] = {}

        for index, (module_id, sheet_file_raw) in enumerate(module_sheets.items()):
            col = index % max(1, int(columns))
            row = index // max(1, int(columns))

            x = origin_x + col * x_spacing
            y = origin_y + row * y_spacing

            sheet_file = str(sheet_file_raw)
            sheet_file_path = Path(sheet_file)
            if relative_sheet_paths and sheet_file_path.is_absolute():
                sheet_file = Path(os.path.relpath(sheet_file, start=output_file.parent)).as_posix()
            else:
                sheet_file = sheet_file_path.as_posix()

            sheet_node, sheet_uuid, labels = _create_sheet_node(
                module_id=module_id,
                sheet_file=sheet_file,
                x=x,
                y=y,
                width=sheet_width,
                height=sheet_height,
                left_pins=left_by_module.get(module_id, {}),
                right_pins=right_by_module.get(module_id, {}),
            )

            tree.append(sheet_node)
            tree.extend(labels)
            sheet_uuids[module_id] = sheet_uuid

        rebuild_sheet_instances_at_end(tree, root_uuid, sheet_uuids)

        saved = SchematicManager.save_schematic(schematic, str(output_file))
        if not saved:
            raise RuntimeError(f"Failed to save schematic to {output_file}")

        result: Dict[str, Any] = {
            "success": True,
            "schematic_path": str(output_file),
            "modules_count": len(module_sheets),
        }

        if export_svg:
            svg_dir = output_file.parent / f"{output_file.stem}_svg"
            svg_dir.mkdir(parents=True, exist_ok=True)

            proc = subprocess.run(
                ["kicad-cli", "sch", "export", "svg", "--output", f"{svg_dir}/", str(output_file)],
                capture_output=True,
                text=True,
            )
            if proc.returncode != 0:
                raise RuntimeError(f"SVG export failed: {proc.stderr}")

            top_svg_path = svg_dir / f"{output_file.stem}.svg"
            result["svg_path"] = str(top_svg_path)

        return result
    except Exception as e:
        import traceback

        return {
            "success": False,
            "message": str(e),
            "traceback": traceback.format_exc(),
        }


def generate_schematic_from_skidl_module(
    skidl_module_path: str,
    subcircuit_name: str,
    output_path: str,
    logic_hints_path: str | None = None,
    export_svg: bool = False,
    verify: bool = True,
    optimize_rotation: bool = False,
    keep_intermediate: bool = False,
    use_direct_connections: bool = False,
    label_high_fanout_nets: bool = False,
    high_fanout_threshold: int = 3,
    cluster_components: bool = False,
    cluster_max_connections: int = 12,
    cluster_min_size: int = 4,
    cluster_max_size: int = 0,
    cluster_ignore_fanout_ge: int = 8,
    cluster_ignore_nets: set[str] | None = None,
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
        use_direct_connections: If True, emit direct label/power connections with no ordering hints.
        label_high_fanout_nets: If True, labelize high-fanout nets to reduce crossings.
        high_fanout_threshold: Minimum number of connected component refs for a net to be treated as high-fanout.
        cluster_components: If True, split dense circuits into visually separated clusters on one sheet.
        cluster_max_connections: Max "connection complexity" per cluster (branching + cycles; used with cluster_components).
        cluster_min_size: Merge clusters smaller than this (used with cluster_components).
        cluster_max_size: Optional hard cap on parts per cluster (0 disables; used with cluster_components).
        cluster_ignore_fanout_ge: Ignore nets with fanout >= this when computing clusters (used with cluster_components).
        cluster_ignore_nets: Optional set of net names to ignore when computing clusters (used with cluster_components).
    
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
    try:
        if skidl_module_path:
            path = Path(skidl_module_path).expanduser()
            if not path.is_absolute():
                path = Path(__file__).resolve().parents[1] / path
            safe_dir = path.parent
        else:
            safe_dir = Path(__file__).resolve().parents[1]
    except Exception:
        safe_dir = Path(__file__).resolve().parents[1]
    guard = _safe_working_dir(safe_dir)
    try:
        guard.__enter__()
        try:
            from python.spice_tools.utils import disable_skidl_file_logging
            disable_skidl_file_logging()
        except Exception:
            pass

        import importlib.util
        import inspect
        from skidl import Circuit, Net

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
                optimize_rotation=optimize_rotation,
                interface_nets=set(param_names),
                use_direct_connections=use_direct_connections,
                label_high_fanout_nets=label_high_fanout_nets,
                high_fanout_threshold=high_fanout_threshold,
                cluster_components=cluster_components,
                cluster_max_connections=cluster_max_connections,
                cluster_min_size=cluster_min_size,
                cluster_max_size=cluster_max_size,
                cluster_ignore_fanout_ge=cluster_ignore_fanout_ge,
                cluster_ignore_nets=cluster_ignore_nets,
            )
        else:
            sch_path = generate_schematic(
                circuit, output_path, logic_hints_path,
                keep_intermediate=keep_intermediate,
                verbose=False,
                optimize_rotation=optimize_rotation,
                interface_nets=set(param_names),
                use_direct_connections=use_direct_connections,
                label_high_fanout_nets=label_high_fanout_nets,
                high_fanout_threshold=high_fanout_threshold,
                cluster_components=cluster_components,
                cluster_max_connections=cluster_max_connections,
                cluster_min_size=cluster_min_size,
                cluster_max_size=cluster_max_size,
                cluster_ignore_fanout_ge=cluster_ignore_fanout_ge,
                cluster_ignore_nets=cluster_ignore_nets,
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
            verification_passed, verification_errors = verify_schematic(circuit, sch_path)
            result["verification_passed"] = verification_passed
            if not verification_passed:
                result["verification_warning"] = "Schematic connectivity does not fully match SKiDL netlist"
                result["verification_errors"] = verification_errors
        
        return result
        
    except Exception as e:
        import traceback
        return {
            "success": False,
            "message": str(e),
            "traceback": traceback.format_exc()
        }
    finally:
        try:
            guard.__exit__(None, None, None)
        except Exception:
            pass


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
        "--direct-connections",
        action="store_true",
        help="Use direct label/power connections without ordering constraints"
    )
    parser.add_argument(
        "--label-high-fanout-nets",
        action="store_true",
        help="Labelize high-fanout nets to reduce crossings"
    )
    parser.add_argument(
        "--high-fanout-threshold",
        type=int,
        default=3,
        help="Minimum number of connected component refs for a net to be treated as high-fanout (used with --label-high-fanout-nets)",
    )
    parser.add_argument(
        "--cluster-components",
        action="store_true",
        help="Split dense circuits into visually separated clusters on one sheet by cutting inter-cluster nets into labels",
    )
    parser.add_argument(
        "--cluster-max-connections",
        type=int,
        default=12,
        help="Max connection complexity per cluster (branching + cycles; used with --cluster-components)",
    )
    parser.add_argument(
        "--cluster-min-size",
        type=int,
        default=4,
        help="Merge clusters smaller than this (used with --cluster-components)",
    )
    parser.add_argument(
        "--cluster-max-size",
        type=int,
        default=0,
        help="Optional hard cap on parts per cluster; 0 disables (used with --cluster-components)",
    )
    parser.add_argument(
        "--cluster-ignore-fanout-ge",
        type=int,
        default=8,
        help="Ignore nets with fanout >= this when computing clusters (used with --cluster-components)",
    )
    parser.add_argument(
        "--cluster-ignore-net",
        action="append",
        default=[],
        help="Net name to ignore when computing clusters (repeatable; used with --cluster-components)",
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
            optimize_rotation=args.optimize_rotation,
            interface_nets=set(param_names),
            use_direct_connections=args.direct_connections,
            label_high_fanout_nets=args.label_high_fanout_nets,
            high_fanout_threshold=args.high_fanout_threshold,
            cluster_components=args.cluster_components,
            cluster_max_connections=args.cluster_max_connections,
            cluster_min_size=args.cluster_min_size,
            cluster_max_size=args.cluster_max_size,
            cluster_ignore_fanout_ge=args.cluster_ignore_fanout_ge,
            cluster_ignore_nets=set(args.cluster_ignore_net or []),
        )
        print(f"\nOutput schematic: {sch_path}")
        print(f"Output SVG: {svg_path}")
    else:
        sch_path = generate_schematic(
            circuit, args.output, args.logic_hints,
            keep_intermediate=args.keep_intermediate,
            optimize_rotation=args.optimize_rotation,
            interface_nets=set(param_names),
            use_direct_connections=args.direct_connections,
            label_high_fanout_nets=args.label_high_fanout_nets,
            high_fanout_threshold=args.high_fanout_threshold,
            cluster_components=args.cluster_components,
            cluster_max_connections=args.cluster_max_connections,
            cluster_min_size=args.cluster_min_size,
            cluster_max_size=args.cluster_max_size,
            cluster_ignore_fanout_ge=args.cluster_ignore_fanout_ge,
            cluster_ignore_nets=set(args.cluster_ignore_net or []),
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
