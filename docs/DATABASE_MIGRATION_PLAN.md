# Database Consolidation & Cloud Migration Plan

## Progress (living section)
- **Status**: in progress (Phase 3 cutover: all known Python + scripts SQLite access routed through `kicad_catalog` helpers with copy-on-write; standalone sub-repo + unified/cloud DB still pending)
- **Last updated**: 2026-01-06
- **Rule**: do not modify the original DB files in-place; any DB writes must target a copied/derived DB.

### Checklist
- [x] Migration plan drafted
- [x] DB access inventory completed (Python + scripts)
- [x] DB access inventory completed (notebooks)
- [x] Non-destructive smoke checks added (baseline parity)
- [x] Extracted catalog package scaffolded (standalone-ready)
- [x] Safe “copy DB then modify” workflow scripted and documented
- [x] `kicad_catalog` import shim added (supports `python.*` package mode + tests)
- [x] Symbols/footprints/SPICE moved onto `kicad_catalog` helpers
- [x] Component DB write paths are copy-on-write
- [ ] KiCAD_MCP cut over to catalog client (legacy backend)

### Progress log
- 2026-01-06: Plan created; migration work started.
- 2026-01-06: Added `python/kicad_catalog/` scaffold and `scripts/catalog/copy_dbs.py`.
- 2026-01-06: Added non-destructive DB smoke checks (`scripts/catalog/smoke_check.py`).
- 2026-01-06: Wired `python/component_search/db.py` to use `kicad_catalog` helpers; smoke checks pass in this workspace.
- 2026-01-06: Added working-copy support (`python/kicad_catalog/workdir.py`) and migrated symbol/footprint/SPICE DB access to it.
- 2026-01-06: Migrated component DB write paths (`add_searchable_part`, `component_search.setup`, `create_filtered_fts`) to working copies; hardened key JLC scripts to avoid in-place DB writes.
- 2026-01-06: Migrated remaining ad-hoc SQLite callers (including `component_schematic`, vendor SPICE importers, and mapping/export scripts) onto `kicad_catalog` helpers; `pytest` now collects with the `kicad_catalog` shim.
- 2026-01-06: Updated notebooks to use read-only SQLite helpers for datasheet tracker access.

## Summary
This repository currently accesses **multiple independent datasets** for:
- Component catalog/search (JLCPCB/LCSC parts DB)
- KiCad symbols (symbol S-expressions stored in SQLite)
- Footprints (SQLite index + filesystem search paths)
- SPICE models (SQLite DB)
- Pinouts (derived from symbol S-expression at multiple call sites)

Managing these separately is hard and unsafe (inconsistent configuration, schema drift, unclear source-of-truth, and SQLite write/concurrency hazards). The plan below migrates to a **single logical “catalog”** with a stable API, extracted into a **standalone sub-repo**, and later backed by a **cloud database** that clients can use via **simple configuration**.

This document is a **plan + progress tracker** (implementation ongoing).

---

## Goals
- Provide a **single logical catalog API** for components, symbols, footprints, pinouts, and SPICE models.
- Extract all DB/data-access logic into a **standalone sub-repo** (library + tooling; optional cloud service).
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
| Component catalog + search (JLCPCB/LCSC) | `part_lib/jlcpcb-components.sqlite3` (SQLite + FTS/views) | `python/component_search/*`, `python/commands/database_tools/component_search.py`, `src/tools/component-search.ts` | `JLCPCB_DB_PATH`, MCP `dbPath` args (some tools), default path in `python/component_search/db.py` |
| Symbol DB | `symbol_lib/kicad_symbols.sqlite3` (expects `symbol_index(library, mpn, sexp)`) | `python/skidl_db_wrapper.py`, `python/commands/database_tools/library_schematic.py`, `python/commands/database_tools/symbol_visualizer.py` | `config/library-paths.json` (`symbolDbPath`, `symbolDbPaths`, search paths) |
| Footprint DB / index | `symbol_lib/kicad_footprints.sqlite3` (expects `footprint_index(...)`), plus filesystem search paths | `python/commands/database_tools/library_export.py`, `python/kicad_interface.py` (`_handle_search_footprint`) | `config/library-paths.json` (`footprintSearchPaths`), env vars for KiCad footprint dirs |
| SPICE model DB | `spice_lib/spice_models.db` (table `part_models(...)`) | `python/spice_tools/model_db.py`, `python/kicad_interface.py` (save/search) | MCP args `modelDbPath`, default in `python/spice_tools/model_db.py` |
| Pinouts | Derived from symbol S-expression | `python/commands/database_tools/library_schematic.py` (`get_symbol_pinout`), SKiDL wrapper | Implicit: depends on symbol DB availability |

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

### Recommended architecture (two-layer)
1. **Catalog library (client + DAL)**: a Python package that exposes a stable API and supports multiple backends.
2. **Optional catalog service (cloud API)**: an HTTP layer to front the cloud DB for auth, caching, auditing, and multi-language clients.

This preserves the current “Node MCP → Python bridge” shape while allowing future direct TypeScript access.

---

## Key Decisions (make early; capture as ADRs)
1. **Cloud access model**
   - A: Direct DB connections from clients (fast, but secrets + networking complexity)
   - B: Service in front of DB (recommended; central auth/rate limiting/audit/caching)
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
Adopt a single URL-shaped knob to select backend:
- `KICAD_CATALOG_URL=sqlite:////abs/path/catalog.sqlite3`
- `KICAD_CATALOG_URL=postgresql://user:pass@host:5432/catalog`
- `KICAD_CATALOG_URL=https://catalog.example.com` (service mode)

Optional:
- `KICAD_CATALOG_TOKEN=...`
- `KICAD_CATALOG_CACHE_DIR=...`

Back-compat: continue accepting `JLCPCB_DB_PATH`, `config/library-paths.json`, etc., but define precedence and deprecate later.

---

## New Sub-Repo Scope & Structure (proposal)

### Repo name (example)
- `kicad-catalog` (or `kicad-component-catalog`)

### Modules (Python-first)
- `catalog/` (public API + config)
- `catalog/backends/`
  - `sqlite_legacy/` (adapters over existing DBs/files; minimal behavior change)
  - `sqlite_unified/` (single local DB schema)
  - `postgres/` (cloud DB backend)
  - `http/` (optional: calls the service)
- `catalog/schema/` (migrations + schema docs)
- `catalog/etl/` (import/export jobs: JLC ingest, symbol/footprint indexing, SPICE model ingest)
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
- Create `kicad-catalog` repo with packaging/CI/test scaffolding.
- Implement `sqlite_legacy` backend that **wraps existing data stores** without reshaping them yet:
  - Read JLC search from `jlcpcb-components.sqlite3` (keep FTS/views expectations)
  - Read symbol sexp from `kicad_symbols.sqlite3`
  - Read footprint index from `kicad_footprints.sqlite3` + existing search paths
  - Read/write SPICE models from `spice_models.db`
- Provide a single config entrypoint (`KICAD_CATALOG_URL` + optional legacy fallbacks).

**Exit criteria**
- KiCAD_MCP can consume the new library in “legacy mode” with no behavior changes beyond config normalization.

### Phase 3 — Cut over KiCAD_MCP to use the new library (no cloud yet)
**Deliverables**
- Replace direct SQLite/file lookups in KiCAD_MCP with catalog client calls:
  - `python/component_search/*` callers
  - `python/skidl_db_wrapper.py` DB access paths
  - `python/commands/database_tools/*` DB calls (pinout, symbol visualizer, library export pieces)
  - `python/kicad_interface.py` footprint search path/DB calls
  - SPICE save/search helpers in `python/kicad_interface.py`
- Keep MCP tool I/O stable; maintain the same return shapes where possible.
- Add a compatibility layer so existing configs keep working, but warnings encourage the new config.

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
  - Direct Postgres driver OR HTTP service (per Phase 1 decision)
  - Connection pooling, timeouts, retry policy
  - AuthN/Z (API keys, OAuth, etc.) if service mode
- Implement caching strategy for clients (optional but recommended):
  - Local on-disk cache for symbols/footprints/SPICE models
  - Cache invalidation via version/revision fields

**Exit criteria**
- KiCAD_MCP can run against staging cloud by changing configuration only.

### Phase 6 — Rollout, dual-mode, and cutover
**Deliverables**
- Support a controlled rollout:
  - Feature flag: `KICAD_CATALOG_URL` points to cloud for selected environments/users
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
- Deprecate legacy env vars (`JLCPCB_DB_PATH`, scattered per-tool params) with a clear timeline.
- Remove direct SQLite access code from KiCAD_MCP (keep only catalog client).
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
