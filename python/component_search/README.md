# Component Search Utilities

Tools for preparing and querying the JLCPCB component database.

## Offline Setup

Rebuild the view and FTS table in one step:

```bash
python -m component_search.setup /root/workspace/KiCAD_MCP/part_lib/jlcpcb-components.sqlite3
```

Run the command again whenever the database is refreshed.

## Free-Form Search Example

```bash
python python/component_search/search_example.py /root/workspace/KiCAD_MCP/part_lib/jlcpcb-components.sqlite3 "P-MOSFET 30V Rds_on<=20mΩ Id>=10A"
```

This demonstrates how to feed a natural-language string into `search_mpn_part`
and display the top results.
