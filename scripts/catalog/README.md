# Catalog DB Working Copies

During the catalog migration we treat the shared SQLite databases as **read-only originals**. Any workflow that writes to a DB must operate on a **copied working DB**.

## Copy DBs to a writable directory
Run with the conda env `kicad` Python:

```bash
/root/miniconda3/envs/kicad/bin/python scripts/catalog/copy_dbs.py --write-manifest
```

By default this copies the **configured** unified DB (from `db_tools/settings.json` -> `db_url`) into `exported/db_work/active/`.

Create a timestamped snapshot instead:

```bash
/root/miniconda3/envs/kicad/bin/python scripts/catalog/copy_dbs.py --timestamped --write-manifest
```

## Rule
Never modify the original DB files in-place (e.g. anything under `part_lib/`, `symbol_lib/`, `spice_lib/` when those point to shared storage). Always copy first, then write to the copy.

## Non-destructive smoke check
Verify that the expected tables/views exist (read-only):

```bash
/root/miniconda3/envs/kicad/bin/python scripts/catalog/smoke_check.py
```

## Build a single unified SQLite catalog (Phase 4)
Create one `catalog.sqlite3` file that embeds the legacy DBs (components + symbols + footprints + SPICE):

```bash
/root/miniconda3/envs/kicad/bin/python scripts/catalog/build_unified_db.py --overwrite
```

The output path is taken from `db_tools/settings.json` -> `db_url`. Set that first, then run the builder and re-run smoke checks.

## Migrate unified SQLite → Postgres (Phase 5 / cloud DB)
After you have a unified `catalog.sqlite3`, copy it into a Postgres database (direct DB connection; no HTTP layer):

```bash
/root/miniconda3/envs/kicad/bin/python scripts/catalog/migrate_sqlite_to_postgres.py \
  --source-sqlite "sqlite:////mnt/shared/catalog/catalog.sqlite3" \
  --dest-postgres "postgresql://USER:PASSWORD@HOST:5432/DBNAME" \
  --drop-existing \
  --analyze
```

Then switch runtime configuration to Postgres by editing `db_tools/settings.json` → `db_url` to the `postgresql://...` DSN and re-run the smoke check:

```bash
/root/miniconda3/envs/kicad/bin/python scripts/catalog/smoke_check.py
```
