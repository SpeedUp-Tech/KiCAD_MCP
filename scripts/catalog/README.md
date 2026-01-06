# Catalog DB Working Copies

During the catalog migration we treat the shared SQLite databases as **read-only originals**. Any workflow that writes to a DB must operate on a **copied working DB**.

## Copy DBs to a writable directory
Run with the conda env `kicad` Python:

```bash
/root/miniconda3/envs/kicad/bin/python scripts/catalog/copy_dbs.py --write-manifest
```

By default this copies the DBs most likely to be written during development (`symbol`, `footprint`, `spice`) into `exported/db_work/active/`.

Create a timestamped snapshot instead:

```bash
/root/miniconda3/envs/kicad/bin/python scripts/catalog/copy_dbs.py --timestamped --write-manifest
```

Include the (potentially large) component DB only when needed:

```bash
/root/miniconda3/envs/kicad/bin/python scripts/catalog/copy_dbs.py --include component symbol footprint spice --write-manifest
```

## Rule
Never modify the original DB files in-place (e.g. anything under `part_lib/`, `symbol_lib/`, `spice_lib/` when those point to shared storage). Always copy first, then write to the copy.

## Non-destructive smoke check
Verify that the expected tables/views exist (read-only):

```bash
/root/miniconda3/envs/kicad/bin/python scripts/catalog/smoke_check.py
```
