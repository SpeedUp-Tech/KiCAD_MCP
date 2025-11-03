#!/usr/bin/env python3
"""
Setup script for creating the v_components_search view and FTS table
for the JLCPCB components database.

This script:
1. Creates a view v_components_search with essential component information
2. Creates an FTS5 table for full-text search on component specifications

Usage:
    python setup_component_search.py <db_path>

Example:
    python setup_component_search.py /path/to/jlcpcb-components.sqlite3

View Columns:
    - lcsc: LCSC part number (e.g., C2559)
    - mpn: Manufacturer part number (e.g., IRF1405PBF)
    - joints: Number of pins/joints (e.g., 3)
    - package: Package type (e.g., TO-220AB)
    - datasheet: Datasheet PDF URL
    - specs: Attributes JSON as searchable text (for FTS)
    - attributes: Full attributes JSON object (for LLM parsing)
    - family: Main category (e.g., Transistors/Thyristors)
    - class: Subcategory (e.g., MOSFETs)

Search Examples:
    # Search for P-channel MOSFETs
    SELECT v.* FROM v_components_search v
    WHERE v.lcsc IN (
        SELECT lcsc FROM v_components_search_fts
        WHERE v_components_search_fts MATCH '"P-channel"'
    )

    # Search for components with specific specs
    SELECT v.* FROM v_components_search v
    WHERE v.lcsc IN (
        SELECT lcsc FROM v_components_search_fts
        WHERE v_components_search_fts MATCH '"30V" AND "MOSFET"'
    )
"""

import sqlite3
import json
import sys
from pathlib import Path


def create_components_search_view(db_path: str) -> None:
    """
    Create the v_components_search view in the database.
    
    Args:
        db_path: Path to the SQLite database file
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Drop existing view if it exists
    cursor.execute("DROP VIEW IF EXISTS v_components_search")
    
    # Create the view
    view_sql = """
    CREATE VIEW v_components_search AS
    SELECT
        c.lcsc AS lcsc,
        c.mfr AS mpn,
        c.joints AS joints,
        c.package AS package,
        COALESCE(
            json_extract(c.extra, '$.datasheet.pdf'),
            c.datasheet
        ) AS datasheet,
        json_extract(c.extra, '$.attributes') AS attributes,
        cat.category AS family,
        cat.subcategory AS class,
        -- Convert attributes JSON to searchable text
        -- Simply use the JSON text representation for FTS
        CASE
            WHEN json_extract(c.extra, '$.attributes') IS NOT NULL
            THEN json_extract(c.extra, '$.attributes')
            ELSE NULL
        END AS specs
    FROM components c
    LEFT JOIN categories cat ON c.category_id = cat.id
    """
    
    cursor.execute(view_sql)
    conn.commit()
    print(f"✓ Created view v_components_search")
    
    conn.close()


def create_fts_search_table(db_path: str) -> None:
    """
    Create the FTS5 table for full-text search on component specs.

    Args:
        db_path: Path to the SQLite database file
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Drop existing FTS table if it exists
    cursor.execute("DROP TABLE IF EXISTS v_components_search_fts")

    # Create FTS5 table without content table (standalone)
    fts_sql = """
    CREATE VIRTUAL TABLE v_components_search_fts USING fts5(
        lcsc UNINDEXED,
        mpn,
        package,
        family,
        class,
        specs
    )
    """

    cursor.execute(fts_sql)
    print(f"✓ Created FTS table v_components_search_fts")

    # Populate the FTS table
    print("Populating FTS table (this may take a while)...")
    cursor.execute("""
        INSERT INTO v_components_search_fts(lcsc, mpn, package, family, class, specs)
        SELECT lcsc, mpn, package, family, class, specs
        FROM v_components_search
    """)

    conn.commit()
    print(f"✓ Populated FTS table with {cursor.rowcount} entries")

    conn.close()


def verify_setup(db_path: str) -> None:
    """
    Verify that the view and FTS table were created successfully.
    
    Args:
        db_path: Path to the SQLite database file
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Check view
    cursor.execute("""
        SELECT COUNT(*) FROM v_components_search
    """)
    view_count = cursor.fetchone()[0]
    print(f"✓ View contains {view_count} components")
    
    # Check FTS table
    cursor.execute("""
        SELECT COUNT(*) FROM v_components_search_fts
    """)
    fts_count = cursor.fetchone()[0]
    print(f"✓ FTS table contains {fts_count} entries")
    
    # Test FTS search
    cursor.execute("""
        SELECT lcsc, mpn, package, family, class
        FROM v_components_search_fts
        WHERE v_components_search_fts MATCH 'MOSFET'
        LIMIT 5
    """)
    results = cursor.fetchall()
    print(f"✓ FTS search test returned {len(results)} results")

    if results:
        print("\nSample search results:")
        for row in results:
            print(f"  LCSC: {row[0]}, MPN: {row[1]}, Package: {row[2]}, Family: {row[3]}, Class: {row[4]}")
    
    conn.close()


def main():
    """Main entry point for the script."""
    if len(sys.argv) != 2:
        print("Usage: python setup_component_search.py <db_path>")
        print("Example: python setup_component_search.py /path/to/jlcpcb-components.sqlite3")
        sys.exit(1)
    
    db_path = sys.argv[1]
    
    # Validate database path
    if not Path(db_path).exists():
        print(f"Error: Database file not found: {db_path}")
        sys.exit(1)
    
    print(f"Setting up component search for database: {db_path}\n")
    
    try:
        # Create view
        create_components_search_view(db_path)
        
        # Create FTS table
        create_fts_search_table(db_path)
        
        # Verify setup
        print("\nVerifying setup...")
        verify_setup(db_path)
        
        print("\n✓ Setup completed successfully!")
        print("\nYou can now search components using:")
        print("\n  # Search FTS and get full component data:")
        print("  SELECT v.* FROM v_components_search v")
        print("  WHERE v.lcsc IN (")
        print("    SELECT lcsc FROM v_components_search_fts")
        print("    WHERE v_components_search_fts MATCH '\"your search terms\"'")
        print("  )")
        print("\n  # Example: Search for P-channel MOSFETs with 30V:")
        print("  SELECT v.* FROM v_components_search v")
        print("  WHERE v.lcsc IN (")
        print("    SELECT lcsc FROM v_components_search_fts")
        print("    WHERE v_components_search_fts MATCH '\"P-channel\" AND \"30V\"'")
        print("  )")
        
    except Exception as e:
        print(f"\n✗ Error during setup: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()

