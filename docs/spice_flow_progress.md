# PySpice Flow – System Notes & Progress

## Core Idea
- Maintain a single **netlist source of truth** (SKiDL modules backed by DB symbols).
- Keep every SPICE model centralized in `spice_lib/spice_models.db`. Each entry stores `(library, name, raw model text, vendor_provided)`.
- Auto-convert any SKiDL module into a PySpice subcircuit by pulling the same topology and binding each `(library, name)` to its SPICE model from the DB.
- Use the generated PySpice module (`*_pyspice.py`) as the interface for all simulations and testbenches.

## System Design (current routine)
1. **Model authoring**
   - Create behavioral or vendor `.subckt/.model` text.
   - Insert into DB via `python.spice_tools.save_part_model(name=..., library=..., model_content=..., vendor_provided=bool)`.
   - Optional: `search_spice_model()` verifies presence and returns the stored content.

2. **SKiDL → PySpice conversion**
   - Call `convert_skidl_module()` (direct Python API or CLI wrapper) with:
     - `input_path`: SKiDL module file.
     - `subckt_name`: `@SubCircuit` to instantiate.
     - `output_path`: destination for the PySpice module.
   - The converter instantiates the SKiDL subcircuit, walks nets/parts, fetches SPICE models from the DB, and produces:
     - `*_pyspice.py` containing the PySpice subcircuit.
     - `*_pyspice.spice.lib` (if needed) aggregating the referenced models.
   - Any missing model raises `KeyError`, enforcing the “no dummy” rule.

3. **Simulation/testbenches**
   - Import (or `runpy`) the generated PySpice module.
   - Instantiate the subcircuit inside a PySpice `Circuit`, add stimuli, and run analyses.
   - All test/verification scripts operate on these generated modules.

## Current Progress
- Implemented `python/spice_tools` package:
  - DB helpers (`save_part_model`, `search_spice_model`, `DEFAULT_MODEL_DB`).
  - Updated converter that relies fully on the DB rather than JSON mappings.
- Added behavior models for the `Battery_Protection` module (PFET + AP9101) and stored them via `save_part_model`.
- Converter now emits modules that reference a local `.spice.lib` sibling file; CLI exposes `--model-db` override.
- Verified conversion + simulation path end-to-end for `Battery_Protection` inside `test_spice.ipynb`.

## Next Steps
- Populate DB with accurate vendor models once available (replace the current behavioral placeholders).
- Extend converter/testbench automation to additional modules (e.g., charger core, USB input).
- Integrate DB-backed model lookup into the broader agent workflow, so every new part automatically stores/retrieves its SPICE model.
