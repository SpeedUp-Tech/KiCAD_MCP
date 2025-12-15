"""
Netlist Comparator - Verify schematic correctness by comparing netlists

Compares connectivity from:
1. KiCad schematic (via kicad-cli netlist export)
2. Original SKiDL circuit

Reports any mismatches in connections.

The pipeline now adds labels to all unlabeled nets before saving,
which ensures kicad-cli exports complete connectivity.
"""

import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Set, Tuple


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


def extract_kicad_connectivity(schematic_path: str) -> dict[str, set[str]]:
    """
    Extract connectivity from KiCad schematic via netlist export.
    
    Args:
        schematic_path: Path to .kicad_sch file
        
    Returns:
        Dict of {net_name: {pin_refs}} where pin_refs are "REF.PIN"
    """
    netlist_path = export_kicad_netlist(schematic_path)
    try:
        return parse_kicad_netlist(netlist_path)
    finally:
        Path(netlist_path).unlink(missing_ok=True)


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
        
        # Only include nets that have at least one pin
        if pins:
            nets[net_name] = pins
    
    return nets


def normalize_net_name(name: str) -> str:
    """Normalize net name for comparison."""
    name = name.strip()
    if name.startswith("/"):
        name = name[1:]
    return name


def to_connection_graph(nets: dict[str, set[str]]) -> set[FrozenSet[str]]:
    """
    Convert net dict to set of connected pin groups.
    
    This representation is net-name-agnostic - it only captures
    which pins are connected together, not what the net is called.
    """
    return {frozenset(pins) for pins in nets.values() if len(pins) > 1}


def compare_connectivity(
    skidl_nets: dict[str, set[str]],
    kicad_nets: dict[str, set[str]],
    verbose: bool = True
) -> tuple[bool, list[str]]:
    """
    Compare connectivity using pin co-membership.
    
    This comparison is net-name-agnostic - it checks that the same
    pins are grouped together, regardless of net naming.
    
    Args:
        skidl_nets: Nets from SKiDL circuit
        kicad_nets: Nets from KiCad schematic
        verbose: Print detailed comparison
        
    Returns:
        Tuple of (passed, list of error messages)
    """
    errors: list[str] = []
    
    # Build reverse lookup: pin -> net_name
    skidl_pin_to_net: dict[str, str] = {}
    for net_name, pins in skidl_nets.items():
        for pin in pins:
            skidl_pin_to_net[pin] = net_name
    
    kicad_pin_to_net: dict[str, str] = {}
    for net_name, pins in kicad_nets.items():
        for pin in pins:
            kicad_pin_to_net[pin] = net_name
    
    # Get all pins from both sources
    all_skidl_pins = set(skidl_pin_to_net.keys())
    all_kicad_pins = set(kicad_pin_to_net.keys())
    
    # Check for missing/extra components
    skidl_refs = {p.split('.')[0] for p in all_skidl_pins}
    kicad_refs = {p.split('.')[0] for p in all_kicad_pins}
    
    missing_in_kicad = skidl_refs - kicad_refs
    extra_in_kicad = kicad_refs - skidl_refs
    
    if missing_in_kicad:
        errors.append(f"Components missing in KiCad: {missing_in_kicad}")
    if extra_in_kicad:
        # Power symbols (GND_xxx) are expected in KiCad but not SKiDL
        non_power_extra = {r for r in extra_in_kicad if not r.startswith("GND_")}
        if non_power_extra:
            errors.append(f"Extra components in KiCad: {non_power_extra}")
    
    # Check connectivity for each SKiDL net
    for net_name, skidl_pins in skidl_nets.items():
        if len(skidl_pins) < 2:
            continue  # Single-pin nets are not meaningful
        
        # Find which KiCad net(s) contain these pins
        kicad_nets_for_pins: dict[str, set[str]] = {}
        for pin in skidl_pins:
            if pin in kicad_pin_to_net:
                kicad_net = kicad_pin_to_net[pin]
                if kicad_net not in kicad_nets_for_pins:
                    kicad_nets_for_pins[kicad_net] = set()
                kicad_nets_for_pins[kicad_net].add(pin)
        
        if not kicad_nets_for_pins:
            errors.append(f"Net '{net_name}' pins not found in KiCad: {sorted(skidl_pins)}")
            continue
        
        if len(kicad_nets_for_pins) > 1:
            # Pins from same SKiDL net are split across multiple KiCad nets
            errors.append(
                f"Net '{net_name}' is split in KiCad: {dict((k, sorted(v)) for k, v in kicad_nets_for_pins.items())}"
            )
            continue
        
        # Check that all pins are present
        kicad_net_name = list(kicad_nets_for_pins.keys())[0]
        kicad_pins = kicad_nets[kicad_net_name]
        
        missing = skidl_pins - kicad_pins
        # Extra pins might include power symbols which is expected
        extra = {p for p in (kicad_pins - skidl_pins) if not p.startswith("GND_")}
        
        if missing:
            errors.append(f"Net '{net_name}' missing pins in KiCad: {sorted(missing)}")
        if extra:
            errors.append(f"Net '{net_name}' (KiCad: {kicad_net_name}) has extra pins: {sorted(extra)}")
    
    passed = len(errors) == 0
    
    if verbose:
        print(f"Comparison: SKiDL has {len(skidl_nets)} nets, KiCad has {len(kicad_nets)} nets")
        if passed:
            print("✓ Netlists match - all connections verified!")
        else:
            print(f"✗ Found {len(errors)} issues:")
            for err in errors[:20]:
                print(f"  {err}")
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
    
    Uses kicad-cli to export the schematic's netlist, then compares
    connectivity to the original SKiDL circuit.
    
    Args:
        circuit: SKiDL Circuit object
        schematic_path: Path to the generated .kicad_sch file
        verbose: Print progress and results
        
    Returns:
        True if netlists match, False otherwise
    """
    if verbose:
        print(f"Verifying schematic: {schematic_path}")
    
    # Extract KiCad connectivity via netlist export
    if verbose:
        print("  Extracting KiCad connectivity...")
    kicad_nets = extract_kicad_connectivity(schematic_path)
    
    # Extract SKiDL connectivity
    if verbose:
        print("  Extracting SKiDL connectivity...")
    skidl_nets = extract_skidl_connectivity(circuit)
    
    # Compare
    if verbose:
        print("  Comparing netlists...")
    passed, _ = compare_connectivity(skidl_nets, kicad_nets, verbose)
    
    return passed


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
