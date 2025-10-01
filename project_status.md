# KiCAD MCP Project Status

Last updated: 2025-10-01

Overview
- This document tracks all MCP tools exposed by the server and their implementation status in the Python backend. Items are grouped by function area and status for quick scanning.

Legend
- ✅ Fully working
- ⚠️ Working with issues or caveats
- 🔌 Not wired (defined in TS, no Python route)
- ❌ Not implemented (explicitly returns error)

Quick Stats
- Total tools defined: 70
- Fully working: 57
- Working with issues: 5
- Not wired: 7
- Not implemented: 1

Session Management (✅ 3)
- ✅ create_session — Create isolated KiCAD worker session
- ✅ close_session — Terminate a session
- ✅ list_sessions — List all active sessions

Project (✅ 7, ❌ 1)
- ✅ create_project — Create new project and board file
- ✅ open_project — Open .kicad_pro or .kicad_pcb
- ✅ save_project — Save current project/board
- ✅ get_project_info — Title block, file paths, metadata
- ✅ set_project_properties — Title/company/revision/date/comments
- ✅ create_backup — Zip board + project files
- ✅ archive_project — Zip full project directory
- ❌ import_project — Placeholder; requires external CAD converters

Board (✅ 7, ⚠️ 1, 🔌 3)
- ✅ set_board_size — Set PCB dimensions
- ✅ add_layer — Add copper/technical/user layers
- ✅ set_active_layer — Switch active layer
- ✅ get_board_info — Dimensions, layers, title, active layer
- ✅ get_layer_list — Enumerate enabled layers
- ✅ add_board_outline — Rectangle/circle/polygon/rounded rectangle
- ✅ add_mounting_hole — Hole with optional pad
- ⚠️ get_board_2d_view — Vector via SVG; PNG/JPG need cairosvg + PIL
- 🔌 add_board_text — TS calls add_board_text; Python route is add_text
- 🔌 add_zone — Zone exists as routing.add_copper_pour; no board.add_zone route
- 🔌 get_board_extents — Not exposed; use get_board_info instead

Components (PCB) (✅ 7, ⚠️ 3)
- ✅ place_component — Place by componentId with position/rotation/layer
- ✅ move_component — Move by reference
- ✅ rotate_component — Rotate by absolute angle
- ✅ delete_component — Remove by reference
- ✅ edit_component — Update reference/value/footprint
- ✅ get_component_properties — Inspect footprint details
- ✅ get_component_list — List all footprints on board
- ⚠️ place_component_array — Schema mismatch: TS omits componentId and uses spacing {dx,dy}/orientation; Python expects pattern/grid params
- ⚠️ align_components — TS uses direction; Python expects alignment + optional distribution
- ⚠️ duplicate_component — TS provides count/offset; Python expects newReference/position

Routing (✅ 8)
- ✅ add_net — Create net, optional class
- ✅ route_trace — Route segment with optional via
- ✅ add_via — Create via with size/drill/net/layers
- ✅ delete_trace — Delete by UUID or nearby position
- ✅ get_nets_list — List nets
- ✅ create_netclass — Create/assign properties to net class
- ✅ add_copper_pour — Create and fill zone
- ✅ route_differential_pair — Parallel pair with width/gap

Design Rules (✅ 4, 🔌 4)
- ✅ set_design_rules — Board-level defaults and minima
- ✅ get_design_rules — Read back settings
- ✅ run_drc — Execute DRC, optional report
- ✅ get_drc_violations — Return markers
- 🔌 add_net_class — TS-defined; not routed (routing.create_netclass exists)
- 🔌 assign_net_to_class — No Python route
- 🔌 set_layer_constraints — No Python route
- 🔌 check_clearance — No Python route

Export (Board) (✅ 4, ⚠️ 1)
- ✅ export_gerber — Plot Gerbers + drills (optional job/map)
- ✅ export_pdf — Plot layers to PDF
- ✅ export_svg — Plot layers to SVG
- ✅ export_bom — CSV/XML/HTML/JSON BOM
- ⚠️ export_3d — Uses 3D viewer; may fail headless

Schematic (Create/Edit/Connect) (✅ 11)
- ✅ create_schematic — Blank KiCad 9 schematic via kicad-skip
- ✅ load_schematic — Load .kicad_sch
- ✅ add_schematic_component — Insert symbol by type/library/ref
- ✅ update_schematic_component — Move/rotate/rename/value/footprint/etc
- ✅ remove_schematic_component — Remove by reference/unit
- ✅ add_schematic_wire — Straight wire between points
- ✅ update_schematic_connection — Edit wire geometry/style by UUID
- ✅ remove_schematic_connection — Remove wire(s) by UUID
- ✅ connect_schematic_pins — Route HV/VH wire between pins
- ✅ list_schematic_libraries — Glob known symbol paths
- ✅ run_erc — kicad-cli ERC

Schematic Export (✅ 4)
- ✅ export_schematic_pdf — kicad-cli sch export pdf
- ✅ export_schematic_svg — kicad-cli sch export svg
- ✅ export_schematic_netlist — kicad-cli sch export netlist
- ✅ export_schematic_bom — kicad-cli sch export bom

Library (✅ 2)
- ✅ create_symbol — Append symbol to .kicad_sym
- ✅ create_footprint — Write .kicad_mod in .pretty dir

Schema/Route Mismatch Notes
- Board text: TS tool is add_board_text; Python route is add_text. Wire up or alias.
- Board zone: TS add_zone vs Python add_copper_pour (routing). Decide one canonical tool and route accordingly.
- Board extents: Provide a get_board_extents route that returns bounding box from pcbnew.
- Components array/duplicate/align: Unify parameters. TS currently uses reference/spacing {dx,dy}/orientation; Python expects componentId/pattern/grid vs circular, newReference, alignment/distribution.
- Design rules extras: Implement add_net_class/assign_net_to_class/set_layer_constraints/check_clearance in Python or remove from TS.

Dependencies and Caveats
- pcbnew (KiCad) — required for all board/pcb operations
- kicad-cli — required for schematic exports (PDF/SVG/Netlist/BOM) and ERC
- kicad-skip — used for schematic create/edit/wire operations
- cairosvg + Pillow (PIL) — required for rasterizing board 2D view (PNG/JPG); SVG path works without
- Headless environments — export_3d may fail without GUI 3D viewer

Recommendations
- Wire missing routes (7): add_board_text, add_zone, get_board_extents, add_net_class, assign_net_to_class, set_layer_constraints, check_clearance
- Fix component schema mismatches (array/align/duplicate) to match Python or update Python handlers
- Document optional dependencies (cairosvg, PIL) and headless limitations for export_3d
- Consider aliasing routing.create_netclass to a design-rules tool for consistency

Testing
- Add integration tests for each TS tool → Python handler path
- Add schematic round‑trip tests using files in test/schematic_test_output
- Validate board 2D view in environments with/without cairosvg/PIL

Summary
- Core workflows are in good shape (57/70 fully working). The remaining gaps are mostly route wiring and a few schema mismatches that can be resolved with small, targeted changes.

