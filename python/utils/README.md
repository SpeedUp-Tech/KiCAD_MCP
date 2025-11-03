# Component Search Setup Utility

This utility sets up optimized views and full-text search (FTS) tables for the JLCPCB components database.

## Usage

```bash
python setup_component_search.py <db_path>
```

### Example

```bash
python setup_component_search.py /root/workspace/KiCAD_MCP/part_lib/jlcpcb-components.sqlite3
```

## What It Creates

### 1. View: `v_components_search`

A simplified view combining essential component information without requiring joins.

**Columns:**
- `lcsc`: LCSC part number (e.g., C2559)
- `mpn`: Manufacturer part number (e.g., IRF1405PBF)
- `joints`: Number of pins/joints (e.g., 3)
- `package`: Package type (e.g., TO-220AB)
- `datasheet`: Datasheet PDF URL
- `specs`: Attributes JSON as searchable text (for FTS indexing)
- `attributes`: Full attributes JSON object (for LLM parsing and detailed specs)
- `family`: Main category (e.g., Transistors/Thyristors)
- `class`: Subcategory (e.g., MOSFETs)

### 2. FTS Table: `v_components_search_fts`

A full-text search index on the view for fast text-based searches.

**Indexed Columns:**
- `lcsc` (UNINDEXED - for joining back to view)
- `mpn` (indexed)
- `package` (indexed)
- `family` (indexed)
- `class` (indexed)
- `specs` (indexed - contains all component attributes as text)

## Search Examples

### Basic FTS Search

```sql
-- Search for MOSFETs
SELECT lcsc, mfr, package, family, class 
FROM v_components_search_fts 
WHERE v_components_search_fts MATCH 'MOSFET'
LIMIT 10;
```

### Get Full Component Data After FTS Search

```sql
-- Search and get full component details including attributes JSON
SELECT v.* 
FROM v_components_search v
WHERE v.lcsc IN (
    SELECT lcsc FROM v_components_search_fts
    WHERE v_components_search_fts MATCH '"P-channel"'
)
LIMIT 10;
```

### Complex Search with Multiple Terms

```sql
-- Search for P-channel MOSFETs with 30V rating
SELECT v.lcsc, v.mfr, v.package, v.attributes
FROM v_components_search v
WHERE v.lcsc IN (
    SELECT lcsc FROM v_components_search_fts
    WHERE v_components_search_fts MATCH '"P-channel" AND "30V"'
)
LIMIT 10;
```

### Search by Package Type

```sql
-- Find all TO-220AB components
SELECT v.*
FROM v_components_search v
WHERE v.lcsc IN (
    SELECT lcsc FROM v_components_search_fts
    WHERE v_components_search_fts MATCH 'package:TO-220AB'
)
LIMIT 10;
```

## FTS Search Syntax

SQLite FTS5 supports various search operators:

- `"exact phrase"` - Search for exact phrase
- `term1 AND term2` - Both terms must be present
- `term1 OR term2` - Either term must be present
- `term1 NOT term2` - First term present, second term absent
- `column:term` - Search in specific column only
- `term*` - Prefix search

### Examples:

```sql
-- Exact phrase search
WHERE v_components_search_fts MATCH '"P-channel MOSFET"'

-- Multiple terms (AND is implicit)
WHERE v_components_search_fts MATCH '30V 10A MOSFET'

-- Explicit OR
WHERE v_components_search_fts MATCH 'MOSFET OR transistor'

-- Exclude terms
WHERE v_components_search_fts MATCH 'MOSFET NOT "N-channel"'

-- Search in specific column
WHERE v_components_search_fts MATCH 'family:Transistors'

-- Prefix search
WHERE v_components_search_fts MATCH 'IRF*'
```

## Design Rationale

1. **No attribute flattening**: With 1,346+ unique attribute types across all components, flattening would create extremely sparse columns. Instead, attributes are kept as JSON.

2. **FTS on specs**: The `specs` column contains the JSON attributes as text, making all specifications searchable via FTS.

3. **Separate attributes column**: The `attributes` JSON is preserved for programmatic access and LLM parsing after search results are returned.

4. **No joins needed**: The view combines all commonly needed data, eliminating the need for joins in most queries.

5. **Standalone FTS table**: The FTS table is standalone (not using content table) to avoid rowid issues with non-sequential LCSC numbers.

## Performance Notes

- Initial setup takes a few minutes to populate the FTS table with 400K+ components
- FTS searches are very fast (milliseconds) even on large result sets
- Joining back to the view for full data is efficient since it's indexed by lcsc
- The FTS table adds approximately 100-200MB to the database size

## Maintenance

To rebuild the FTS index after database updates:

```bash
python setup_component_search.py <db_path>
```

This will drop and recreate both the view and FTS table with fresh data.

