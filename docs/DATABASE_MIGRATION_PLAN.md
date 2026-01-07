# Database Consolidation & Cloud Migration Plan

## Progress (living section)
- **Status**: in progress (Phase 3 complete. Phase 5 started: direct Postgres backend + SQLite→Postgres migration tooling added. Remaining: install Postgres driver in `kicad` env, run end-to-end migration to a real cloud DB, and complete rollout.)
- **Last updated**: 2026-01-07
- **Rule**: do not modify the original DB files in-place; any DB writes must target a copied/derived DB.

### Checklist
- [x] Migration plan drafted
- [x] DB access inventory completed (Python + scripts)
- [x] DB access inventory completed (notebooks)
- [x] Non-destructive smoke checks added (baseline parity)
- [x] Extracted `db_tools` package scaffolded (standalone-ready)
- [x] Centralized DB configuration (`db_tools/settings.json` -> `db_url`)
- [x] Safe “copy DB then modify” workflow scripted and documented
- [x] Compatibility shims added (`python/db_tools`, `component_search`)
- [x] Unified SQLite catalog builder added (`scripts/catalog/build_unified_db.py`)
- [x] HTTP/service layer removed (direct DB access only)
- [x] Symbols/footprints/SPICE moved onto `db_tools` helpers
- [x] Component DB write paths are copy-on-write
- [x] KiCAD_MCP cut over to `db_tools` (no per-tool DB paths)
- [x] Direct Postgres backend supported (`db_url=postgresql://...`, no HTTP layer)
- [x] SQLite → Postgres migration script added
- [x] Smoke checks support Postgres

### Progress log
- 2026-01-06: Plan created; migration work started.
- 2026-01-06: Added `python/db_tools/` shim and `scripts/catalog/copy_dbs.py`.
- 2026-01-06: Added non-destructive DB smoke checks (`scripts/catalog/smoke_check.py`).
- 2026-01-06: Wired component search to use shared DB helpers; smoke checks pass in this workspace.
- 2026-01-06: Added working-copy support (`db_tools/workdir.py`) and migrated symbol/footprint/SPICE DB access to it.
- 2026-01-06: Migrated component DB write paths (`add_searchable_part`, `component_search.setup`, `create_filtered_fts`) to working copies; hardened key JLC scripts to avoid in-place DB writes.
- 2026-01-06: Migrated remaining ad-hoc SQLite callers (including `component_schematic`, vendor SPICE importers, and mapping/export scripts) onto shared helpers.
- 2026-01-06: Updated notebooks to use read-only SQLite helpers for datasheet tracker access.
- 2026-01-06: Extracted DB tooling into repo-root `db_tools/` (standalone-ready) and moved component search under it (`db_tools/component_search/`) with compatibility shims.
- 2026-01-07: Centralized configuration into `db_tools/settings.json`; removed all legacy naming and all service/HTTP assumptions across the repo.
- 2026-01-07: Added direct Postgres backend branches for core APIs + SQLite→Postgres migration tooling + Postgres-aware smoke checks.

## Summary
This repository currently accesses **multiple independent datasets** for:
- Component catalog/search (JLCPCB/LCSC parts DB)
- KiCad symbols (symbol S-expressions stored in SQLite)
- Footprints (SQLite index + filesystem search paths)
- SPICE models (SQLite DB)
- Pinouts (derived from symbol S-expression at multiple call sites)

Managing these separately is hard and unsafe (inconsistent configuration, schema drift, unclear source-of-truth, and SQLite write/concurrency hazards). The plan below migrates to a **single logical DB** with a stable API, extracted into a **standalone repo/package**, and later backed by a **cloud database** that clients can use via **simple configuration**.

This document is a **plan + progress tracker** (implementation ongoing).

---

## Goals
- Provide a **single logical catalog API** for components, symbols, footprints, pinouts, and SPICE models.
- Extract all DB/data-access logic into a **standalone repo/package** (library + tooling; no HTTP/service layer).
- Enable switching **local ↔ cloud** via configuration (no code changes in KiCAD_MCP callers).
- Improve safety: explicit config precedence, schema migrations, validation, test coverage, and operational controls.

## Non-goals (for the first migration)
- Renaming or redesigning MCP tool names/schemas in `src/tools/*` unless required for compatibility.
- Fixing all search relevance issues (keep behavior first; improve after stabilization).
- Replacing KiCad file-authoring features unless they must move due to ownership or dependencies.

---

## Current State Inventory (repo-specific)

### Primary datasets & entry points
| Dataset | Storage today | Primary code paths | Config knobs today |
|---|---|---|---|
| Unified DB (components/search + symbols + footprints + SPICE) | `db_tools/settings.json` → `db_url` (SQLite path today) | `db_tools/*` + callers in `python/commands/*`, `python/kicad_interface.py`, `scripts/*` | `db_tools/settings.json` (`db_url`, `work_dir`, `protected_roots`, `allow_inplace_writes`) |
| Footprint files (optional) | filesystem `.pretty` libraries | `python/commands/database_tools/library_export.py` | KiCad footprint dir env vars + defaults (non-DB) |
| Pinouts | Derived from stored symbol S-expression | `db_tools/symbols.py` + consumers | none (derived) |

### Secondary/auxiliary DB-related tooling
- JLC import/export/indexing scripts: `scripts/jlc_symbols/*` (SQLite, symbol/footprint indexing).
- Vendor SPICE import scripts: `scripts/spice/*` (SQLite schemas differ from `spice_models.db`).
- Notebooks use ad-hoc DBs/paths (e.g., `test_symbol_builder.ipynb` references `datasheets/datasheet_tracker`).

### Risks/pain points to address explicitly
- **Multiple configuration sources** (env vars + JSON configs + per-tool params) with inconsistent precedence.
- **Multiple schemas** with overlapping concepts (part ↔ symbol ↔ footprint ↔ model) but no enforced linkage.
- **Unsafe concurrent access** risks with SQLite (multiple processes writing; locking; partial writes).
- **Unclear source-of-truth** between DB rows, filesystem assets, and derived metadata (pinouts).
- **Hard-to-migrate callers** because DB logic is embedded in multiple modules and scripts.

---

## Target End State

### What “success” looks like
- All catalog/data operations go through a single dependency (new sub-repo), not ad-hoc SQLite calls.
- KiCAD_MCP can switch between **local** and **cloud** by setting a small number of config values.
- Data/schema changes are versioned (migrations), validated (CI), and observable (basic metrics/logging).
- Existing MCP tools keep stable behavior during the transition (compat mode), with controlled improvements later.

### Recommended architecture (single package, direct DB)
1. **`db_tools` package (client + DAL)**: a Python package that exposes a stable API and supports multiple backends (SQLite local now; direct Postgres/managed DB later).

No HTTP/service layer is part of the target architecture.

---

## Key Decisions (make early; capture as ADRs)
1. **Cloud access model**
   - Direct DB connections from clients (no service layer).
2. **Database choice**
   - SQLite for local/offline; Postgres for cloud is the typical pairing.
3. **Blob strategy**
   - Store symbols/footprints/SPICE models in DB (simple) vs object storage references for large assets (STEP/3D).
4. **Search strategy**
   - Keep SQLite FTS locally; cloud search via Postgres FTS or external search service.
5. **Data governance**
   - What is allowed to be stored (vendor SPICE license terms, datasheet redistribution, etc.) and under what access controls.

---

## Contract-First “Catalog” API (proposal)

### Core entities (conceptual)
- `Part` (canonical ID, mpn, manufacturer, package, attributes)
- `SupplierPart` (e.g., LCSC/JLC identifiers, lifecycle/availability if present)
- `Symbol` (library, name/mpn key, raw KiCad S-expression, derived pin metadata)
- `Footprint` (library, name, raw S-expression or file reference)
- `SpiceModel` (library, name, raw model text, vendor_provided flag, optional pin mapping)
- `Attachment` (datasheet URL, STEP, images, etc.)

### Core operations (minimal initial surface)
- Lookup: `get_part(...)`, `get_symbol(...)`, `get_footprint(...)`, `get_spice_model(...)`
- Search: `search_parts(query, limit, offset)` (initially compatible with current `search_mpn_part`)
- Derived: `get_symbol_pinout(library, symbol)` (initially derived from stored symbol S-exp)
- Write (guarded): `upsert_symbol`, `upsert_spice_model`, `upsert_search_entry` (whatever is used today)

### Configuration pattern (“simple configurations”)
Single centralized config file (the only supported place to set DB location):
- Edit `db_tools/settings.json`:
  - `db_url`: `sqlite:////abs/path/catalog.sqlite3` (today)
  - `db_url`: `postgresql://user:pass@host:5432/catalog` (future cloud DB; direct driver)

Safety knobs (same file):
- `work_dir`: where working copies are created for safe writes
- `protected_roots`: directories treated as read-only originals
- `allow_inplace_writes`: opt-in to in-place writes (default false)

---

## New Sub-Repo Scope & Structure (proposal)

### Repo name (example)
- `db_tools`

### Modules (Python-first)
- `db_tools/` (public API + config)
- `db_tools/backends/`
  - `sqlite/` (single local DB schema)
  - `postgres/` (future: cloud DB backend, direct driver)
- `db_tools/schema/` (migrations + schema docs)
- `db_tools/etl/` (import/export jobs: JLC ingest, symbol/footprint indexing, SPICE model ingest)
- `tests/` (unit + integration + “contract tests”)
- `docs/` (local setup, config reference, deployment runbooks)

Keep KiCAD_MCP-specific MCP wiring **out** of the sub-repo; it should remain a consumer.

---

## Migration Strategy (phased, low-risk)

### Phase 0 — Discovery & safety baseline
**Deliverables**
- Inventory all DB/dataset access points and write paths in KiCAD_MCP (Python + scripts + notebooks as needed).
- Document current schemas and implicit contracts (tables/columns used, required indexes/views).
- Create/confirm a small “data tools smoke suite” to prevent regressions:
  - `search_mpn_part`, `search_datasheet`
  - `get_symbol_pinout`
  - `search_footprint`
  - `save_part_model` / `search_spice_model`

**Exit criteria**
- A baseline set of behaviors is measurable and repeatable in CI/local runs.

### Phase 1 — Decide architecture + lock the contract
**Deliverables**
- ADR: choose cloud access model (direct DB vs service) and DB technology.
- Define catalog API shape and error model (contract doc).
- Define config precedence rules (env vs JSON vs per-call overrides) and back-compat story.

**Exit criteria**
- A stable interface exists; extraction can proceed without re-litigating design per module.

### Phase 2 — Create sub-repo skeleton + “legacy adapters”
**Deliverables**
- Create `db_tools` repo with packaging/CI/test scaffolding.
- Implement SQLite backend that **wraps existing schemas** and can be unified:
  - JLC search tables/views (FTS/views expectations)
  - `symbol_index`
  - `footprint_index`
  - `part_models`
- Provide a single config entrypoint (`db_tools/settings.json` → `db_url`).

**Exit criteria**
- KiCAD_MCP can consume the new library in “legacy mode” with no behavior changes beyond config normalization.

### Phase 3 — Cut over KiCAD_MCP to use the new library (no cloud yet)
**Deliverables**
- Replace direct SQLite/file lookups in KiCAD_MCP with `db_tools` calls:
  - `python/component_search/*` callers
  - `python/skidl_db_wrapper.py` DB access paths
  - `python/commands/database_tools/*` DB calls (pinout, symbol visualizer, library export pieces)
  - `python/kicad_interface.py` footprint search path/DB calls
  - SPICE save/search helpers in `python/kicad_interface.py`
- Keep MCP tool I/O stable where feasible.
- Remove per-tool DB path overrides; DB location comes only from `db_tools/settings.json`.

**Exit criteria**
- KiCAD_MCP passes the Phase 0 smoke suite using the catalog library as the only data access layer.

### Phase 4 — Unify schema locally (single “catalog.sqlite”)
**Purpose**
Reduce operational risk and simplify cloud migration by consolidating the current multiple SQLite files into a single, versioned schema.

**Deliverables**
- Define a unified schema (migration-managed) that can represent:
  - Parts + attributes + supplier identifiers
  - Symbols + derived pins
  - Footprints
  - SPICE models
  - Attachments (datasheets/STEP references)
- Implement ETL that imports:
  - Existing JLC DB views/tables into unified tables
  - Symbol/footprint DBs into unified tables
  - SPICE model DB into unified tables
- Add “data quality checks”:
  - Referential integrity (where applicable)
  - Unique keys (e.g., `(library, mpn)` for symbols, `(library, name)` for footprints)
  - Basic counts and spot-check queries

**Exit criteria**
- A fresh local unified DB can be built reproducibly and supports all required read paths.

### Phase 5 — Cloud backend (staging first)
**Deliverables**
- Stand up a staging environment:
  - Managed Postgres (or chosen DB) + migrations pipeline
  - Secrets management (no creds in configs)
  - Backups + restore rehearsal
- Implement cloud backend:
  - Direct Postgres driver (no HTTP/service layer)
  - Connection pooling, timeouts, retry policy
- Implement caching strategy for clients (optional but recommended):
  - Local on-disk cache for symbols/footprints/SPICE models
  - Cache invalidation via version/revision fields

**Exit criteria**
- KiCAD_MCP can run against staging cloud by changing configuration only.

### Phase 6 — Rollout, dual-mode, and cutover
**Deliverables**
- Support a controlled rollout:
  - Feature flag: `db_tools/settings.json` switches `db_url` to cloud for selected environments/users
  - Fallback: local unified DB remains available for outages
- Decide write policy:
  - Reads from cloud; writes restricted to controlled ingestion jobs
  - Or dual-write during migration (only if necessary; increases complexity)
- Establish operational monitoring:
  - Request rates/latency/error rates
  - DB health, slow queries, connection pool saturation

**Exit criteria**
- Cloud becomes primary source-of-truth; local DB becomes cache/backup/dev-only.

### Phase 7 — Deprecation and cleanup
**Deliverables**
- Deprecate/remove legacy env vars and per-tool DB path params (done).
- Remove direct SQLite access code from KiCAD_MCP (keep only `db_tools`).
- Document new operational runbooks and developer setup.

**Exit criteria**
- The only supported way to access catalog data is via the extracted sub-repo.

---

## Workstreams & Ownership
Run these in parallel where possible:
1. **API/contract**: define stable interfaces + compatibility guarantees.
2. **Data modeling**: unify entities/keys; plan search + derived data (pinouts).
3. **Extraction**: move code into sub-repo; minimize KiCAD_MCP surface changes.
4. **ETL & ingestion**: build repeatable pipelines from current DBs/files to unified schema.
5. **Cloud infra**: DB provisioning, migrations, secrets, backups, monitoring.
6. **Security & governance**: access control, audit logs, license constraints for vendor models.
7. **QA & release**: tests, staging, rollout, deprecation communications.

---

## Compatibility Plan (explicit mapping)
For each existing feature/tool that touches data, define:
- Current inputs/outputs (JSON shapes)
- Required latency/availability envelope
- Error semantics (what is “not found” vs “db missing”)
- Backwards compatibility requirement (strict vs “best effort”)

Minimum set to map early:
- `search_mpn_part` (free-form)
- `search_datasheet` (exact match for symbol-backed entries)
- Symbol pinout retrieval (`get_symbol_pinout` behavior)
- Footprint search (`_handle_search_footprint`)
- SPICE model save/search (`save_part_model`, `search_spice_model`)

---

## Testing & Validation
- **Contract tests**: validate that the catalog client returns expected shapes for known fixtures.
- **Backend parity tests**: same query against legacy SQLite vs unified vs cloud should match within defined tolerances.
- **Data quality checks**: uniqueness, referential integrity (when modeled), and basic invariants.
- **Load/perf smoke**: ensure common searches and symbol/footprint fetches meet latency targets.

---

## Security, Compliance, and Safety
- **Secrets**: never store DB credentials in repo configs; use env vars/secret managers.
- **AuthZ**: define read vs write roles; restrict writes to ingestion pipelines where possible.
- **Auditability**: log who/what wrote models/symbols and when (important for “unsafe writes” concerns).
- **Licensing**: validate whether vendor SPICE models/datasheets can be stored/distributed; if not, store references or hashed pointers with access restrictions.

---

## Operational Considerations (cloud)
- Backups (automated, tested restores), retention policy, and disaster recovery.
- Migrations (forward/backward strategy, maintenance windows).
- Monitoring/alerting (DB health, API health, latency, error budgets).
- Cost controls (storage growth, egress for large assets).

---

## Milestones (suggested)
Adjust dates to team size; keep ordering.
1. Phase 0 complete: inventory + smoke suite defined.
2. Phase 1 complete: ADRs + contract + config precedence locked.
3. Phase 2 complete: `kicad-catalog` exists + legacy backend works.
4. Phase 3 complete: KiCAD_MCP uses catalog library exclusively (legacy mode).
5. Phase 4 complete: unified local DB build and parity validated.
6. Phase 5 complete: staging cloud backend operational with config-only switching.
7. Phase 6 complete: production cutover with fallback and monitoring.
8. Phase 7 complete: legacy removal and docs/runbooks finalized.

---

## Open Questions (resolve early)
- Should cloud access be **service-only**, or do we allow direct DB access from trusted environments?
- What is the canonical key for “a part”: MPN, supplier ID (LCSC), or internal UUID (recommended)?
- Do we require full **symbol/footprint blobs** in the cloud DB, or store only references and materialize on demand?
- What is the expected write workflow: interactive writes from the agent, or controlled ingestion jobs?
- How do we handle multi-tenancy (different teams/projects) and data visibility?
- What is the long-term search stack (Postgres FTS vs dedicated search engine)?
