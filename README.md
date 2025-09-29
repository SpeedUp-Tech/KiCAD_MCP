# KiCAD MCP – Headless ECAD Automation Toolkit

KiCAD MCP is a Model Context Protocol (MCP) server that lets an LLM agent drive the entire KiCad design toolchain without opening the GUI. It puppeteers KiCad’s Python API and `kicad-cli` utilities to create projects, modify schematics and layouts, run ERC/DRC, generate manufacturing data, and even author new symbols/footprints – all through structured MCP tool calls.

---
## Table of Contents
1. [What You Can Do](#what-you-can-do)
2. [System Requirements](#system-requirements)
3. [Architecture Overview](#architecture-overview)
4. [Installation & Build](#installation--build)
5. [Configuration & Environment](#configuration--environment)
6. [Available MCP Tools](#available-mcp-tools)
7. [Headless Workflow Examples](#headless-workflow-examples)
8. [Logs & Troubleshooting](#logs--troubleshooting)
9. [Development Notes](#development-notes)

---
## What You Can Do
The server exposes a rich set of MCP tools so an agent can orchestrate the ECAD workflow end-to-end:

- **Project lifecycle** – create/open/save KiCad projects, archive them, or generate timestamped backups.
- **Schematic capture** – author new schematics, add symbols & wires, run ERC, export PDFs/netlists/BOMs via `kicad-cli`.
- **Custom symbol & footprint creation** – generate `.kicad_sym` and `.kicad_mod` assets on the fly when the requested part is missing.
- **Board layout** – adjust board outline/size, manage layers, place & edit footprints, align or duplicate parts.
- **Routing** – create nets, route traces/differential pairs, drop vias, add copper pours, and query net lists.
- **Design rules** – configure global/net-class rules and run DRC; inspect violations programmatically.
- **Manufacturing outputs** – export Gerbers, drill files, 2D snapshots, 3D STEP/VRML, and board/component PDFs.
- **Context sharing** – expose project/board/component metadata as MCP resources for grounding prompts.

All responses are structured JSON; no human-in-the-loop parsing is required.

---
## System Requirements
| Component | Notes |
| --- | --- |
| KiCad 9.0+ | Provides `pcbnew` Python modules and `kicad-cli`. Must be installed locally. |
| Node.js 18+ | Runs the TypeScript MCP server. |
| Python 3.8+ | Bundled with KiCad; used to execute `python/kicad_interface.py`. |
| npm | Handles TypeScript build/test scripts. |
| MCP-capable client | e.g. [Claude’s Cline extension](https://github.com/saoudrizwan/claude-dev). |

---
## Architecture Overview
```
┌──────────────────────────────────────┐
│  MCP Client (LLM agent / Cline)      │
│  • issues tool calls                 │
│  • consumes structured responses     │
└────────────────────┬─────────────────┘
                     │ stdio transport (JSON RPC)
┌────────────────────▼─────────────────┐
│  Node MCP Server (`src/`)            │
│  • loads config & logger             │
│  • spawns KiCad Python bridge        │
│  • queues requests, enforces timeout │
│  • registers tool/resource prompts   │
└────────────────────┬─────────────────┘
                     │ stdin/stdout (JSON lines)
┌────────────────────▼─────────────────┐
│  Python Bridge (`python/`)           │
│  • dispatches commands to modules    │
│  • talks to `pcbnew`, kicad-skip     │
│  • shells out to `kicad-cli`         │
│  • writes headless assets            │
└──────────────────────────────────────┘
```

- **TypeScript server** (`src/`) owns MCP protocol handling, logging, and process management. Tools live in modular files under `src/tools/` (board, schematic, library, etc.).
- **Python command modules** (`python/commands/`) isolate domain logic for projects, schematics, routing, symbols, footprints, exports, and more. The central router is `python/kicad_interface.py`.
- **Resources & prompts** (`src/resources/`, `src/prompts/`) provide contextual info and guided prompt templates to the agent.

---
## Installation & Build
```bash
# clone & install
npm install

# build TypeScript once (outputs to dist/)
npm run build
```

You can point an MCP-capable client at `dist/index.js` (see client-specific documentation). During development `npm run dev` runs `tsc -w` + `nodemon`.

---
## Configuration & Environment
Configuration is read from `config/default-config.json` (or a file passed with `--config`). Key fields:

| Field | Purpose |
| --- | --- |
| `pythonExecutable` | Absolute path to the KiCad Python interpreter. Defaults to `python3` / `python.exe` when resolvable. |
| `pythonPath` | Extra entries appended to `PYTHONPATH` so `pcbnew` & plugins resolve. |
| `kicadPath` | Optional helper environment variable propagated to the Python process. |
| `logDir` | Directory for daily log files (default: `~/.kicad-mcp/logs`). |
| `logLevel` | `error`, `warn`, `info`, or `debug`. |
| `responseTimeoutMs` | Per-command timeout (default 60 000 ms). |

Useful environment variables:

- `KICAD_CLI` – Explicit path to `kicad-cli` for ERC/BOM/netlist/PDF exports.
- `KICAD_PYTHON`, `PYTHON_EXECUTABLE` – Alternative Python interpreter discovery.
- `KICAD_PYTHONPATH` – Additional colon/semicolon-separated module search paths.

Logs from the Python bridge go to `~/.kicad-mcp/logs/kicad_interface.log`.

---
## Available MCP Tools
Below is a condensed summary; see the TypeScript sources under `src/tools/` for parameter schemas.

### Project (`project.ts`)
- `create_project`, `open_project`, `save_project`, `get_project_info`
- `set_project_properties` (title block fields)
- `create_backup` (zip with timestamp) & `archive_project` (full directory snapshot)
- `import_project` (placeholder warning for unsupported formats)

### Schematic (`schematic.ts`)
- `create_schematic`, `load_schematic`
- `add_schematic_component`, `add_schematic_wire`
- `list_schematic_libraries`
- `export_schematic_pdf` *(kicad-cli)*
- `run_erc` *(kicad-cli)*
- `export_schematic_netlist` *(kicad-cli)*
- `export_schematic_bom` *(kicad-cli)*

### Library Authoring (`library.ts`)
- `create_symbol` – writes a KiCad `.kicad_sym` file (creates or appends symbol). You provide library path, symbol name, pins, and optional metadata.
- `create_footprint` – writes a `.kicad_mod` into a `.pretty` directory, given pad geometry, outlines, and attributes.

### Board Layout (`board.ts`)
- `set_board_size`, `add_board_outline`, `add_mounting_hole`, `add_board_text`
- `add_layer`, `set_active_layer`, `get_layer_list`
- `get_board_info`, `get_board_extents`, `get_board_2d_view`

### Component Placement (`component.ts`)
- `place_component`, `move_component`, `rotate_component`, `delete_component`, `edit_component`
- `get_component_properties`, `get_component_list`
- `place_component_array`, `align_components`, `duplicate_component`

### Routing (`routing.ts`)
- `add_net`, `get_nets_list`
- `route_trace`, `add_via`, `delete_trace`
- `add_copper_pour`, `route_differential_pair`
- `create_netclass`

### Design Rules (`design-rules.ts`)
- `set_design_rules`, `get_design_rules`
- `run_drc`, `get_drc_violations`
- `add_net_class`, `assign_net_to_class`, `set_layer_constraints`, `check_clearance`

### Exports (`export.ts`)
- `export_gerber` *(Gerber + optional drill/map files)*
- `export_pdf`, `export_svg`, `export_3d`, `export_bom`

Resources (`src/resources/…`) expose project, board, library, and component data via URIs like `kicad://board/info`. Prompts under `src/prompts/` give the agent instructions for common design operations.

---
## Headless Workflow Examples
These scenarios assume the MCP server is running and the agent can invoke tools.

### 1. Create a Project & Base Schematic
1. `create_project` → specify name/path.
2. `create_schematic` → generate a blank `.kicad_sch` in the project directory.
3. `add_schematic_component` → drop symbols with coordinates/rotation.
4. `add_schematic_wire` → connect nets.
5. `run_erc` → check for open nets or rule violations via `kicad-cli`.
6. `export_schematic_netlist` / `export_schematic_bom` → produce manufacturing data.

### 2. Create a Custom Symbol & Footprint, Then Place It
1. `create_symbol` with a target `.kicad_sym` path, pin definitions, and metadata.
2. `create_footprint` targeting a `.pretty/` directory with pad geometry.
3. `list_schematic_libraries` (optional) to confirm the library path.
4. `add_schematic_component` referencing the new library/symbol combo, plus `footprint` property.
5. `place_component` on the PCB by referencing the associated footprint.

### 3. PCB Layout & DRC
1. `set_board_size`, `add_board_outline`, optional `add_mounting_hole`/`add_board_text`.
2. `place_component` or `duplicate_component` to populate footprints.
3. `add_net` + `route_trace` / `route_differential_pair` to connect nets.
4. `add_copper_pour` for ground planes.
5. `set_design_rules` & `create_netclass` to tune clearances.
6. `run_drc` → returns success/error with violation list; optional `get_drc_violations` for details.
7. `export_gerber` & `export_3d` to prep fabrication outputs.

Every step returns structured JSON summaries so the agent can loop over results and adapt.

---
## Logs & Troubleshooting
- **Node server logs** – go to stdout/stderr and optional daily log files (`logDir`).
- **Python bridge logs** – `~/.kicad-mcp/logs/kicad_interface.log` includes all command dispatches and errors.

Common remedies:
- Missing `pcbnew`: ensure KiCad is installed and `pythonPath` includes KiCad’s `site-packages`.
- `kicad-cli` not found: either add it to `PATH` or set `KICAD_CLI`.
- Permission errors writing libraries/outputs: confirm targets are writable and absolute paths are used.
- Timeouts: raise `responseTimeoutMs` for long exports or large DRC runs.

---
## Development Notes
- Source TypeScript resides under `src/`; compiled JS goes to `dist/` via `npm run build`.
- Python modules live under `python/`; add new command handlers there and register through `kicad_interface.py`.
- When adding MCP tools, update `src/tools/shared.ts` or relevant wrappers so responses remain consistent.
- CI/CD tip: run the server in “never ask for approval” environments with the `danger-full-access` sandbox to automate regression designs.

Feel free to open issues or submit PRs with additional commands, bug fixes, or documentation improvements.
