# KiCad ERC “Dangling Wire” Investigation

## Problem Statement
The KiCad ERC report for `test_cases/3A充电方案/kicad/modules/Battery_Protection/harness.kicad_sch` flags two wires as `wire_dangling`. These wires originate from resistor R1’s pins and were routed automatically by the `connect_schematic_pins` tool.

## Observations
- **Wires exist graphically** – they start at R1 pins, travel through intermediate nodes, and terminate on Q1 pins. No graphical breaks are visible.
- **ERC still warns** – despite continuous geometry, ERC indicates the wires are “dangling”.

## Analysis Steps & Findings
1. **S-expression inspection**: The schematic contains 53 `wire` entries in `sheet.kicad_sch`. R1’s connections are represented by distinct wire segments with unique UUIDs (e.g., `eee326f3…`, `9baea1a0…`).

2. **Wire graph construction**: Building a graph from wire segment endpoints revealed multiple connected components. Components containing R1’s pins showed degree-1 nodes only where a single wire met a pin, but each such node also hosted a component pin (e.g., `R1.1`, `R1.2`).

3. **Pins and labels mapping**:
   - 19 unique pin coordinate groups were extracted; R1’s pins map to `(170.18,77.47)` and `(170.18,69.85)`.
   - Hierarchical labels only appear on `/VBAT_CHG` and `/VBAT_PACK`; there are no labels on the R1 branches.

4. **Net-level grouping (with pins/labels)**: Augmenting the wire graph with component pins and labels yielded five components. Component #1 (holding R1.2) and Component #2 (holding R1.1) have degree-1 nodes at the R1 pin coordinates, each featuring exactly one wire and one pin, no labels.

5. **Endpoint inspection**:
   - `(170.18,69.85)` (R1.2) connects to wire `eee326f3…`, pin `R1.2`, and nothing else.
   - `(170.18,77.47)` (R1.1) connects to wires `9baea1a0…` & `927c672e…`, pin `R1.1`, plus an internal spur; still, no labels or sheet-exported pins.

6. **Netlist export**: `kicad-cli sch export netlist` shows only three nets: `/VBAT_CHG`, `/VBAT_PACK`, `GND`. R1’s branches do not appear in the netlist at all.

7. **Tool behavior**: Reviewing `connect_schematic_pins` confirms it never writes net names. The helper `_build_net_info` calculates a temporary name (e.g., `Net-(R1-Pad1)`) for return values but does not update the schematic.

## Root Cause
The R1 connections form internal nets that have **no net labels and no hierarchical/export pins**. Although wires visually connect R1 to Q1, KiCad’s ERC treats these nets as “anonymous” and local, so they appear as dangling during ERC.

## Recommended Next Steps
1. **Assign net labels** to the R1 branches (e.g., `R1_GATE`, `R1_SOURCE`) at appropriate points on each branch.
2. Alternatively, **connect the branches to hierarchical sheet pins** so the nets are exported.
3. Re-run ERC to confirm the warnings clear.
4. Optionally, update the automation pipeline to:
   - Insert net labels automatically after `connect_schematic_pins`, or
   - Expose helpers for downstream scripts to label nets programmatically.

## Summary
No wiring geometry errors were found. ERC uses net labeling/export rules to detect functional connections; unlabeled internal nets remain anonymous and trigger `wire_dangling` warnings. Applying explicit net labels (or adding hierarchical pins) to R1’s branches brings the schematic in line with ERC expectations.
