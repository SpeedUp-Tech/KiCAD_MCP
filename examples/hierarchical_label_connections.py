#!/usr/bin/env python3
"""
Example: Connecting Component Pins to Hierarchical Labels

This example demonstrates the extended connect_schematic_pins functionality
that supports connecting component pins to hierarchical labels in hierarchical
schematics.

The connect_schematic_pins tool now supports:
1. Pin-to-pin connections (original functionality)
2. Pin-to-hierarchical-label connections (new)
3. Hierarchical-label-to-pin connections (new)
4. Hierarchical-label-to-hierarchical-label connections (new)
"""

import sys
import os
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from python.commands.schematic import SchematicManager
from python.commands.connection_schematic import ConnectionManager
from python.commands.component_schematic import ComponentManager
from sexpdata import Symbol


def create_example_hierarchical_schematic():
    """Create an example hierarchical schematic with labels and components"""
    
    print("=" * 70)
    print("Creating Example Hierarchical Schematic")
    print("=" * 70)
    
    # Create a new schematic
    schematic = SchematicManager.create_schematic('HierarchicalExample')
    print("\n✓ Created schematic")
    
    # Add hierarchical labels (these would typically be on the edges of a hierarchical sheet)
    # Input power label on the left
    power_in_label = [
        Symbol('hierarchical_label'),
        'VCC_IN',
        [Symbol('shape'), Symbol('input')],
        [Symbol('at'), 20.0, 50.0, 0],
        [Symbol('fields_autoplaced')],
        [Symbol('effects'), [Symbol('font'), [Symbol('size'), 1.27, 1.27]], [Symbol('justify'), Symbol('right')]],
        [Symbol('uuid'), Symbol('label-vcc-in')]
    ]
    schematic.tree.append(power_in_label)
    print("✓ Added hierarchical label: VCC_IN (input)")
    
    # Output power label on the right
    power_out_label = [
        Symbol('hierarchical_label'),
        'VCC_OUT',
        [Symbol('shape'), Symbol('output')],
        [Symbol('at'), 180.0, 50.0, 0],
        [Symbol('fields_autoplaced')],
        [Symbol('effects'), [Symbol('font'), [Symbol('size'), 1.27, 1.27]], [Symbol('justify'), Symbol('left')]],
        [Symbol('uuid'), Symbol('label-vcc-out')]
    ]
    schematic.tree.append(power_out_label)
    print("✓ Added hierarchical label: VCC_OUT (output)")
    
    # Ground label at the bottom
    gnd_label = [
        Symbol('hierarchical_label'),
        'GND',
        [Symbol('shape'), Symbol('passive')],
        [Symbol('at'), 100.0, 120.0, 0],
        [Symbol('fields_autoplaced')],
        [Symbol('effects'), [Symbol('font'), [Symbol('size'), 1.27, 1.27]], [Symbol('justify'), Symbol('left')]],
        [Symbol('uuid'), Symbol('label-gnd')]
    ]
    schematic.tree.append(gnd_label)
    print("✓ Added hierarchical label: GND (passive)")
    
    # Add components
    # Input capacitor
    ComponentManager.add_component(
        schematic,
        {
            'type': 'C',
            'reference': 'C1',
            'value': '10uF',
            'x': 50,
            'y': 50,
        },
    )
    print("✓ Added component: C1 (10uF capacitor)")
    
    # Resistor
    ComponentManager.add_component(
        schematic,
        {
            'type': 'R',
            'reference': 'R1',
            'value': '100',
            'x': 100,
            'y': 50,
        },
    )
    print("✓ Added component: R1 (100Ω resistor)")
    
    # Output capacitor
    ComponentManager.add_component(
        schematic,
        {
            'type': 'C',
            'reference': 'C2',
            'value': '10uF',
            'x': 150,
            'y': 50,
        },
    )
    print("✓ Added component: C2 (10uF capacitor)")
    
    return schematic


def demonstrate_connections(schematic):
    """Demonstrate different types of connections"""
    
    print("\n" + "=" * 70)
    print("Demonstrating Connection Types")
    print("=" * 70)
    
    # 1. Connect hierarchical label to component pin
    print("\n1. Hierarchical Label → Component Pin")
    print("   Connecting VCC_IN to C1 pin 1...")
    wires1 = ConnectionManager.connect_pins(
        schematic,
        {'label': 'VCC_IN'},
        {'reference': 'C1', 'pin': '1'},
    )
    print(f"   ✓ Created {len(wires1)} wire segment(s)")
    
    # 2. Connect component pin to component pin (traditional)
    print("\n2. Component Pin → Component Pin (traditional)")
    print("   Connecting C1 pin 2 to R1 pin 1...")
    wires2 = ConnectionManager.connect_pins(
        schematic,
        {'reference': 'C1', 'pin': '2'},
        {'reference': 'R1', 'pin': '1'},
    )
    print(f"   ✓ Created {len(wires2)} wire segment(s)")
    
    # 3. Connect component pin to component pin
    print("\n3. Component Pin → Component Pin")
    print("   Connecting R1 pin 2 to C2 pin 1...")
    wires3 = ConnectionManager.connect_pins(
        schematic,
        {'reference': 'R1', 'pin': '2'},
        {'reference': 'C2', 'pin': '1'},
    )
    print(f"   ✓ Created {len(wires3)} wire segment(s)")
    
    # 4. Connect component pin to hierarchical label
    print("\n4. Component Pin → Hierarchical Label")
    print("   Connecting C2 pin 2 to VCC_OUT...")
    wires4 = ConnectionManager.connect_pins(
        schematic,
        {'reference': 'C2', 'pin': '2'},
        {'label': 'VCC_OUT'},
    )
    print(f"   ✓ Created {len(wires4)} wire segment(s)")
    
    # 5. Connect component pins to ground label
    print("\n5. Component Pins → Ground Label")
    print("   Connecting C1 pin 2 to GND...")
    wires5 = ConnectionManager.connect_pins(
        schematic,
        {'reference': 'C1', 'pin': '2'},
        {'label': 'GND'},
        routing={'pattern': 'vh'},  # Vertical then horizontal
    )
    print(f"   ✓ Created {len(wires5)} wire segment(s)")
    
    print("   Connecting C2 pin 1 to GND...")
    wires6 = ConnectionManager.connect_pins(
        schematic,
        {'reference': 'C2', 'pin': '1'},
        {'label': 'GND'},
        routing={'pattern': 'vh'},
    )
    print(f"   ✓ Created {len(wires6)} wire segment(s)")
    
    total_segments = sum(len(w) for w in [wires1, wires2, wires3, wires4, wires5, wires6])
    print(f"\n   Total wire segments created: {total_segments}")


def save_and_verify(schematic, output_path):
    """Save the schematic and verify it can be reloaded"""
    
    print("\n" + "=" * 70)
    print("Saving and Verifying Schematic")
    print("=" * 70)
    
    # Save the schematic
    print(f"\nSaving to: {output_path}")
    success = SchematicManager.save_schematic(schematic, output_path)
    
    if success:
        print("✓ Schematic saved successfully")
        
        # Reload to verify
        print("\nReloading schematic to verify...")
        reloaded = SchematicManager.load_schematic(output_path)
        
        if reloaded:
            print("✓ Schematic reloaded successfully")
            print(f"  - Wire count: {len(reloaded.wire)}")
            print(f"  - Component count: {len(reloaded.symbol)}")
            
            # Count hierarchical labels
            label_count = 0
            for elem in reloaded.tree:
                if isinstance(elem, list) and len(elem) > 0:
                    if hasattr(elem[0], 'value') and elem[0].value() == 'hierarchical_label':
                        label_count += 1
            print(f"  - Hierarchical label count: {label_count}")
            
            return True
        else:
            print("✗ Failed to reload schematic")
            return False
    else:
        print("✗ Failed to save schematic")
        return False


def main():
    """Main example function"""
    
    print("\n" + "=" * 70)
    print("Hierarchical Label Connection Example")
    print("=" * 70)
    print("\nThis example demonstrates the new capability to connect component")
    print("pins to hierarchical labels using the connect_schematic_pins tool.")
    
    # Create the example schematic
    schematic = create_example_hierarchical_schematic()
    
    # Demonstrate different connection types
    demonstrate_connections(schematic)
    
    # Save and verify
    output_dir = Path(__file__).parent / 'output'
    output_dir.mkdir(exist_ok=True)
    output_path = output_dir / 'hierarchical_example.kicad_sch'
    
    success = save_and_verify(schematic, str(output_path))
    
    print("\n" + "=" * 70)
    if success:
        print("✓ Example completed successfully!")
        print(f"\nOutput file: {output_path}")
        print("\nYou can open this file in KiCAD to see the connections.")
    else:
        print("✗ Example failed")
        return 1
    print("=" * 70 + "\n")
    
    return 0


if __name__ == '__main__':
    sys.exit(main())

