"""
Netlist Comparator - Verify schematic correctness by comparing netlists

Compares connectivity from:
1. KiCad schematic (via kicad-cli netlist export)
2. Original SKiDL circuit

Reports any mismatches in connections.
"""

import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any


def parse_kicad_netlist(netlist_path: str) -> dict[str, set[str]]:
    """
    Parse KiCad S-expression netlist file.
    
    Args:
        netlist_path: Path to .net file
        
    Returns:
        Dict of {net_name: {pin_refs}} where pin_refs are "REF.PIN"
    """
    with open(netlist_path) as f:
        content = f.read()
    
    nets: dict[str, set[str]] = {}
    
    # Find all (net ...) blocks
    # Pattern: (net (code "N") (name "NET_NAME") (class "...")
    #           (node (ref "REF") (pin "PIN") ...)
    #           (node (ref "REF") (pin "PIN") ...)
    #         )
    net_pattern = re.compile(
        r'\(net\s+\(code\s+"[^"]*"\)\s+\(name\s+"([^"]*)"\).*?\n((?:\s+\(node[^)]+\)[^\n]*\n?)+)',
        re.MULTILINE | re.DOTALL
    )
    
    node_pattern = re.compile(r'\(node\s+\(ref\s+"([^"]+)"\)\s+\(pin\s+"([^"]+)"\)')
    
    for net_match in net_pattern.finditer(content):
        net_name = net_match.group(1)
        nodes_block = net_match.group(2)
        
        pins: set[str] = set()
        for node_match in node_pattern.finditer(nodes_block):
            ref = node_match.group(1)
            pin = node_match.group(2)
            pins.add(f"{ref}.{pin}")
        
        # Normalize net name (remove leading /)
        if net_name.startswith("/"):
            net_name = net_name[1:]
        
        nets[net_name] = pins
    
    return nets


def extract_skidl_connectivity(circuit) -> dict[str, set[str]]:
    """
    Extract connectivity from SKiDL circuit object.
    
    Args:
        circuit: SKiDL Circuit object
        
    Returns:
        Dict of {net_name: {pin_refs}} where pin_refs are "REF.PIN"
    """
    nets: dict[str, set[str]] = {}
    
    for net in circuit.nets:
        net_name = net.name
        if not net_name:
            continue
        
        pins: set[str] = set()
        for pin in net.pins:
            ref = pin.part.ref
            pin_num = pin.num
            pins.add(f"{ref}.{pin_num}")
        
        nets[net_name] = pins
    
    return nets


def export_kicad_netlist(schematic_path: str, output_path: str | None = None) -> str:
    """
    Export netlist from KiCad schematic using kicad-cli.
    
    Args:
        schematic_path: Path to .kicad_sch file
        output_path: Optional output path for netlist
        
    Returns:
        Path to the exported netlist file
    """
    if output_path is None:
        output_path = tempfile.mktemp(suffix=".net")
    
    result = subprocess.run(
        ["kicad-cli", "sch", "export", "netlist",
         schematic_path, "--format", "kicadsexpr", "-o", output_path],
        capture_output=True,
        text=True
    )
    
    if result.returncode != 0:
        raise RuntimeError(f"kicad-cli netlist export failed: {result.stderr}")
    
    return output_path


def normalize_net_name(name: str) -> str:
    """Normalize net name for comparison."""
    # Remove special prefixes
    name = name.strip()
    if name.startswith("/"):
        name = name[1:]
    # Handle common aliases
    return name


def compare_netlists(
    skidl_nets: dict[str, set[str]],
    kicad_nets: dict[str, set[str]],
    verbose: bool = True
) -> tuple[bool, list[str]]:
    """
    Compare two netlists for connectivity equivalence.
    
    Args:
        skidl_nets: Nets from SKiDL circuit
        kicad_nets: Nets from KiCad schematic
        verbose: Print detailed comparison
        
    Returns:
        Tuple of (passed, list of error messages)
    """
    errors: list[str] = []
    
    # Normalize net names
    skidl_normalized: dict[str, set[str]] = {
        normalize_net_name(k): v for k, v in skidl_nets.items()
    }
    kicad_normalized: dict[str, set[str]] = {
        normalize_net_name(k): v for k, v in kicad_nets.items()
    }
    
    # Filter out single-pin nets (not meaningful for comparison)
    skidl_normalized = {k: v for k, v in skidl_normalized.items() if len(v) > 1}
    kicad_normalized = {k: v for k, v in kicad_normalized.items() if len(v) > 1}
    
    # Build reverse lookup: pin -> net_name
    skidl_pin_to_net: dict[str, str] = {}
    for net_name, pins in skidl_normalized.items():
        for pin in pins:
            skidl_pin_to_net[pin] = net_name
    
    kicad_pin_to_net: dict[str, str] = {}
    for net_name, pins in kicad_normalized.items():
        for pin in pins:
            kicad_pin_to_net[pin] = net_name
    
    # Check that all pin connections match
    all_pins = set(skidl_pin_to_net.keys()) | set(kicad_pin_to_net.keys())
    
    # Group pins by which other pins they connect to
    def get_connected_pins(pin: str, pin_to_net: dict[str, str], nets: dict[str, set[str]]) -> frozenset[str]:
        if pin not in pin_to_net:
            return frozenset()
        net_name = pin_to_net[pin]
        return frozenset(nets.get(net_name, set()) - {pin})
    
    mismatches: list[tuple[str, frozenset[str], frozenset[str]]] = []
    
    for pin in all_pins:
        skidl_connected = get_connected_pins(pin, skidl_pin_to_net, skidl_normalized)
        kicad_connected = get_connected_pins(pin, kicad_pin_to_net, kicad_normalized)
        
        if skidl_connected != kicad_connected:
            mismatches.append((pin, skidl_connected, kicad_connected))
    
    if mismatches:
        for pin, skidl_conn, kicad_conn in mismatches:
            only_in_skidl = skidl_conn - kicad_conn
            only_in_kicad = kicad_conn - skidl_conn
            
            if only_in_skidl:
                errors.append(f"  {pin}: missing KiCad connections to {only_in_skidl}")
            if only_in_kicad:
                errors.append(f"  {pin}: extra KiCad connections to {only_in_kicad}")
    
    passed = len(errors) == 0
    
    if verbose:
        print(f"Comparison: SKiDL has {len(skidl_normalized)} nets, KiCad has {len(kicad_normalized)} nets")
        if passed:
            print("✓ Netlists match - all connections verified!")
        else:
            print(f"✗ Found {len(errors)} connection mismatches:")
            for err in errors[:20]:  # Limit output
                print(err)
            if len(errors) > 20:
                print(f"  ... and {len(errors) - 20} more")
    
    return passed, errors


def verify_schematic(
    circuit,
    schematic_path: str,
    verbose: bool = True
) -> bool:
    """
    Verify that a generated schematic matches the original SKiDL circuit.
    
    Args:
        circuit: SKiDL Circuit object
        schematic_path: Path to the generated .kicad_sch file
        verbose: Print progress and results
        
    Returns:
        True if netlists match, False otherwise
    """
    if verbose:
        print(f"Verifying schematic: {schematic_path}")
    
    # Export netlist from schematic
    if verbose:
        print("  Exporting KiCad netlist...")
    netlist_path = export_kicad_netlist(schematic_path)
    
    try:
        # Parse KiCad netlist
        if verbose:
            print("  Parsing KiCad netlist...")
        kicad_nets = parse_kicad_netlist(netlist_path)
        
        # Extract SKiDL connectivity
        if verbose:
            print("  Extracting SKiDL connectivity...")
        skidl_nets = extract_skidl_connectivity(circuit)
        
        # Compare
        if verbose:
            print("  Comparing netlists...")
        passed, errors = compare_netlists(skidl_nets, kicad_nets, verbose)
        
        return passed
        
    finally:
        # Cleanup temp file
        Path(netlist_path).unlink(missing_ok=True)


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Compare SKiDL and KiCad netlists")
    parser.add_argument("--kicad-netlist", required=True, help="Path to KiCad netlist file")
    parser.add_argument("--skidl-module", help="Path to SKiDL module (for generating reference)")
    
    args = parser.parse_args()
    
    # Parse and display KiCad netlist
    nets = parse_kicad_netlist(args.kicad_netlist)
    print(f"Found {len(nets)} nets:")
    for net_name, pins in sorted(nets.items()):
        print(f"  {net_name}: {sorted(pins)}")
