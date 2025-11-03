#!/usr/bin/env python3
"""
Example script demonstrating how to search for components using the
v_components_search view and FTS table.

This example searches for P-channel MOSFETs with specific requirements:
- Voltage: 30V or higher
- Rds(on): 20mΩ or lower
- Continuous Drain Current (Id): 10A or higher
"""

import sqlite3
import json
import sys
import re
from pathlib import Path


def parse_voltage(voltage_str):
    """Parse voltage string to numeric value in volts."""
    if not voltage_str:
        return None
    match = re.search(r'([\d.]+)\s*V', str(voltage_str))
    if match:
        return float(match.group(1))
    return None


def parse_current(current_str):
    """Parse current string to numeric value in amps."""
    if not current_str:
        return None
    match = re.search(r'([\d.]+)\s*([mM]?)A', str(current_str))
    if match:
        value = float(match.group(1))
        unit = match.group(2)
        if unit.lower() == 'm':
            value = value / 1000  # Convert mA to A
        return value
    return None


def parse_resistance(resistance_str):
    """Parse resistance string to numeric value in milliohms."""
    if not resistance_str:
        return None
    # Handle various formats like "5.3mΩ@10V,101A" or "0.02Ω@10V,38A"
    match = re.search(r'([\d.]+)\s*([mM]?)Ω', str(resistance_str))
    if match:
        value = float(match.group(1))
        unit = match.group(2)
        if unit.lower() != 'm':
            value = value * 1000  # Convert Ω to mΩ
        return value
    return None


def search_mosfets(db_path, channel_type='P-channel', min_voltage=30, max_rds_on=20, min_current=10):
    """
    Search for MOSFETs with specific requirements.
    
    Args:
        db_path: Path to the SQLite database
        channel_type: 'P-channel' or 'N-channel'
        min_voltage: Minimum drain-source voltage in volts
        max_rds_on: Maximum on-resistance in milliohms
        min_current: Minimum continuous drain current in amps
    
    Returns:
        List of matching components
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # First, use FTS to find candidates
    print(f"Searching for {channel_type} MOSFETs...")
    print(f"  Vdss >= {min_voltage}V")
    print(f"  Rds(on) <= {max_rds_on}mΩ")
    print(f"  Id >= {min_current}A")
    print()
    
    # FTS search for channel type
    fts_query = f'"{channel_type}"'
    
    query = """
    SELECT
        v.lcsc,
        v.mpn,
        v.package,
        v.joints,
        v.datasheet,
        v.family,
        v.class,
        v.attributes
    FROM v_components_search v
    WHERE v.lcsc IN (
        SELECT lcsc FROM v_components_search_fts
        WHERE v_components_search_fts MATCH ?
    )
    """

    cursor.execute(query, (fts_query,))
    candidates = cursor.fetchall()

    print(f"Found {len(candidates)} candidates from FTS search")
    print("Filtering by specifications...")
    print()

    # Filter by specifications
    results = []
    for row in candidates:
        lcsc, mpn, package, joints, datasheet, family, class_name, attributes_json = row
        
        if not attributes_json:
            continue
        
        try:
            attributes = json.loads(attributes_json)
        except:
            continue
        
        # Extract specs
        vdss = attributes.get('Drain Source Voltage (Vdss)')
        rds_on = attributes.get('Drain Source On Resistance (RDS(on)@Vgs,Id)')
        id_current = attributes.get('Continuous Drain Current (Id)')
        
        # Parse values
        vdss_val = parse_voltage(vdss)
        rds_on_val = parse_resistance(rds_on)
        id_val = parse_current(id_current)
        
        # Check if meets requirements
        if vdss_val and vdss_val >= min_voltage:
            if rds_on_val and rds_on_val <= max_rds_on:
                if id_val and id_val >= min_current:
                    results.append({
                        'lcsc': lcsc,
                        'mpn': mpn,
                        'package': package,
                        'joints': joints,
                        'datasheet': datasheet,
                        'family': family,
                        'class': class_name,
                        'vdss': vdss,
                        'rds_on': rds_on,
                        'id': id_current,
                        'attributes': attributes
                    })
    
    conn.close()
    return results


def main():
    """Main entry point."""
    if len(sys.argv) != 2:
        print("Usage: python search_components_example.py <db_path>")
        print("Example: python search_components_example.py /path/to/jlcpcb-components.sqlite3")
        sys.exit(1)
    
    db_path = sys.argv[1]
    
    if not Path(db_path).exists():
        print(f"Error: Database file not found: {db_path}")
        sys.exit(1)
    
    # Search for P-channel MOSFETs with specific requirements
    results = search_mosfets(
        db_path,
        channel_type='P-channel',
        min_voltage=30,
        max_rds_on=20,
        min_current=10
    )
    
    print(f"Found {len(results)} matching components:")
    print()
    
    # Display results
    for i, component in enumerate(results[:10], 1):  # Show first 10
        print(f"{i}. LCSC: {component['lcsc']}")
        print(f"   MPN: {component['mpn']}")
        print(f"   Package: {component['package']} ({component['joints']} pins)")
        print(f"   Vdss: {component['vdss']}")
        print(f"   Rds(on): {component['rds_on']}")
        print(f"   Id: {component['id']}")
        print(f"   Datasheet: {component['datasheet']}")
        print()
    
    if len(results) > 10:
        print(f"... and {len(results) - 10} more")
    
    # Show full attributes for first result
    if results:
        print("\nFull attributes for first result:")
        print(json.dumps(results[0]['attributes'], indent=2))


if __name__ == "__main__":
    main()

