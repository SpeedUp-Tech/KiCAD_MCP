# Blueprint to Hierarchical Schematic Generator

## Overview

`blueprint_to_hierarchical.py` is a clean, robust generator that creates hierarchical KiCAD schematics from blueprint JSON files. It replaces the previous `block_diagram_macro.py` implementation with a simpler, more reliable approach based on direct tree manipulation.

## Key Features

- **No Hardcoding**: All module information, connections, and structure are derived from the blueprint JSON
- **Correct Element Ordering**: Ensures `sheet_instances` is placed at the END of the tree (critical for SVG rendering)
- **Proper Path Format**: Uses correct hierarchical paths (`/root_uuid/sheet_uuid`)
- **Hierarchical Labels**: Generates proper `hierarchical_label` elements (not `global_label`)
- **Extensible**: Generated schematics can be enhanced with `ComponentManager` and `ConnectionManager`

## Architecture

### Core Principle

The generator follows the working pattern discovered through manual hierarchical builds:

1. Use `SchematicManager` for creating and saving schematics
2. Direct tree manipulation for hierarchical elements (no complex helper functions)
3. Strict element ordering: `sheet_instances` MUST be at the END
4. Correct path format: `/root_uuid/sheet_uuid` (not just `/sheet_uuid`)

### Why This Approach Works

KiCAD's SVG exporter requires specific structural constraints for hierarchical schematics:

- **Element Order Matters**: If `sheet_instances` appears before sheet definitions, the SVG export will only show page frames without actual schematic content
- **Path Format Matters**: Sheet paths must include both root UUID and sheet UUID for proper hierarchical label rendering
- **Label Type Matters**: Child sheets must use `hierarchical_label`, not `global_label`

## Function Reference

### Main Function

#### `generate_hierarchical_schematic(blueprint_path: str, output_dir: str) -> Dict`

Generates a complete hierarchical KiCAD schematic project from a blueprint JSON file.

**Parameters:**
- `blueprint_path` (str): Path to the blueprint JSON file
- `output_dir` (str): Directory where the project will be created

**Returns:**
- Dictionary containing:
  - `top_schematic` (str): Path to the top-level schematic file
  - `module_sheets` (dict): Mapping of module_id to schematic file paths
  - `output_dir` (str): Path to the output directory

**Example:**
```python
from python.commands.blueprint_to_hierarchical import generate_hierarchical_schematic

result = generate_hierarchical_schematic(
    "test_cases/3A充电方案/blueprint.json",
    "output/my_project"
)

print(f"Top schematic: {result['top_schematic']}")
print(f"Module sheets: {result['module_sheets']}")
```

### Internal Functions

#### `_analyze_module_connections(modules, signals, rails) -> Dict`

Analyzes the blueprint to determine which signals and rails each module uses, and their directions (input/output).

**Returns:** Dictionary mapping module_id to connection information:
```python
{
    "module_id": {
        "power_inputs": set(),
        "power_outputs": set(),
        "signal_inputs": set(),
        "signal_outputs": set()
    }
}
```

#### `_add_hierarchical_labels_to_tree(tree, connections) -> None`

Adds hierarchical label elements to a schematic tree based on connection analysis.

**Parameters:**
- `tree` (List): The schematic S-expression tree
- `connections` (Dict): Connection information from `_analyze_module_connections`

#### `_make_hierarchical_label(name, shape, x, y, angle) -> List`

Creates a hierarchical label S-expression element.

**Parameters:**
- `name` (str): Label name (e.g., "R_USB_5V_IN")
- `shape` (str): Label shape ("input", "output", "bidirectional", "passive")
- `x` (float): X coordinate
- `y` (float): Y coordinate
- `angle` (int): Rotation angle (0, 90, 180, 270)

**Returns:** S-expression list representing the hierarchical label

#### `_add_text_to_tree(tree, text, x, y, font_size) -> None`

Adds a text annotation to the schematic tree.

#### `_remove_sheet_instances(tree) -> None`

Removes the `sheet_instances` section from child sheets (only the top schematic should have it).

#### `_get_root_uuid(tree) -> str`

Extracts the root UUID from a schematic tree.

#### `_add_sheet_symbols_to_tree(tree, module_data, sheets_dir) -> Dict`

Adds sheet symbols to the top schematic tree, with pins matching the hierarchical labels in child sheets.

**Returns:** Dictionary mapping module_id to sheet_uuid

#### `_add_wires_to_tree(tree, module_data, sheet_uuids, signals, rails) -> None`

Placeholder for adding wires connecting sheets. Currently not implemented (can be enhanced).

#### `_rebuild_sheet_instances_at_end(tree, root_uuid, sheet_uuids) -> None`

**CRITICAL FUNCTION**: Removes existing `sheet_instances` and rebuilds it at the END of the tree with correct paths.

This function ensures:
1. `sheet_instances` is at the end (required for SVG rendering)
2. Paths use correct format: `/root_uuid/sheet_uuid`

## Blueprint JSON Format

The generator expects a blueprint JSON with the following structure:

```json
{
  "modules": [
    {
      "module_id": "M_USB_Input",
      "function": "USB power input",
      "notes": "Primary source for R_USB_5V_IN",
      "produces_rails": ["R_USB_5V_IN"],
      "drives_signals": ["S_VIN_SENSE"]
    }
  ],
  "signals": [
    {
      "signal_id": "S_VIN_SENSE",
      "source": "M_USB_Input",
      "sinks": ["M_IP2312_Charger"]
    }
  ],
  "rails": [
    {
      "rail_id": "R_USB_5V_IN",
      "source": "M_USB_Input",
      "consumers": ["M_IP2312_Charger", "M_Status_LEDs"]
    }
  ]
}
```

### Module Fields

- `module_id` (required): Unique identifier for the module
- `function` (optional): Description of module function (added as text annotation)
- `notes` (optional): Additional notes (added as text annotation)
- `produces_rails` (optional): List of power rails this module produces
- `uses_rails` (optional): List of power rails this module consumes
- `drives_signals` (optional): List of signals this module drives
- `uses_signals` (optional): List of signals this module uses

### Signal Fields

- `signal_id` (required): Unique identifier for the signal
- `source` (required): Module ID that produces this signal
- `sinks` (required): List of module IDs that consume this signal

### Rail Fields

- `rail_id` (required): Unique identifier for the power rail
- `source` (required): Module ID that produces this rail
- `consumers` (required): List of module IDs that consume this rail

## Output Structure

The generator creates the following directory structure:

```
output_dir/
├── Top.kicad_sch           # Top-level schematic with sheet symbols
└── sheets/
    ├── M_Module1.kicad_sch # Module schematic with hierarchical labels
    ├── M_Module2.kicad_sch
    └── ...
```

## Usage Examples

### Basic Usage

```python
from python.commands.blueprint_to_hierarchical import generate_hierarchical_schematic

result = generate_hierarchical_schematic(
    "test_cases/3A充电方案/blueprint.json",
    "exported/my_project"
)
```

### Adding Components to Generated Schematics

```python
from python.commands.schematic import SchematicManager
from python.commands.component_schematic import ComponentManager
from python.commands.connection_schematic import ConnectionManager

# Load a generated module schematic
module_path = result["module_sheets"]["M_USB_Input"]
schematic = SchematicManager.load_schematic(module_path)

# Add a resistor
ComponentManager.add_component(schematic, {
    "type": "R",
    "reference": "R1",
    "value": "10k",
    "library": "Device",
    "x": 100,
    "y": 50
})

# Add a wire
ConnectionManager.add_wire(schematic, [105, 50], [130, 50])

# Save
SchematicManager.save_schematic(schematic, module_path)
```

### Exporting SVG

```bash
kicad-cli sch export svg --output out/ Top.kicad_sch
```

## Comparison with block_diagram_macro.py

| Aspect | block_diagram_macro.py | blueprint_to_hierarchical.py |
|--------|------------------------|------------------------------|
| Complexity | ~900 lines, many helper functions | ~300 lines, simple and direct |
| Element Ordering | ❌ Incorrect (sheet_instances in middle) | ✅ Correct (sheet_instances at end) |
| Path Format | ❌ Incorrect (`/sheet_uuid`) | ✅ Correct (`/root_uuid/sheet_uuid`) |
| Label Type | ❌ Used global_label initially | ✅ Uses hierarchical_label |
| SVG Rendering | ❌ Labels don't appear | ✅ Labels appear correctly |
| Maintainability | ❌ Complex, hard to debug | ✅ Simple, easy to understand |
| Extensibility | ❌ Tightly coupled | ✅ Works with existing tools |

## Known Limitations

1. **Wire Generation**: The `_add_wires_to_tree` function is currently a placeholder. Automatic wire routing between sheets is not implemented.
2. **Layout**: Sheet symbols are arranged in a simple grid. No automatic layout optimization.
3. **Pin Positioning**: Hierarchical labels and sheet pins are positioned sequentially without considering optimal placement.

## Future Enhancements

1. **Automatic Wire Routing**: Implement intelligent wire routing between sheet pins
2. **Layout Optimization**: Better sheet symbol placement based on connectivity
3. **Pin Grouping**: Group related pins (power, signals) on different sides of sheet symbols
4. **Component Integration**: Automatically add common components (connectors, test points) based on blueprint metadata
5. **Validation**: Add blueprint validation to catch errors before generation

## Testing

See `python/tests/test_blueprint_to_hierarchical.py` for comprehensive tests.

Run tests:
```bash
python -m pytest python/tests/test_blueprint_to_hierarchical.py -v
```

## Troubleshooting

### SVG exports are empty or show only page frames

**Cause**: Element ordering issue or incorrect path format

**Solution**: Verify that:
1. `sheet_instances` is at the END of the top schematic tree
2. Paths use format `/root_uuid/sheet_uuid`
3. Child sheets use `hierarchical_label` (not `global_label`)

### Hierarchical labels don't appear in SVG

**Cause**: Path format mismatch between sheet_instances and sheet UUIDs

**Solution**: Ensure sheet paths in `sheet_instances` match the format `/root_uuid/sheet_uuid` where `sheet_uuid` matches the UUID in the sheet symbol definition.

### Module schematics fail to load

**Cause**: Child sheets should not have `sheet_instances` section

**Solution**: Verify that `_remove_sheet_instances` is called for all child sheets.

## License

Part of the KiCAD_MCP project.

