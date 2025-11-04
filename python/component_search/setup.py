"""
Utility script for preparing the component search view and FTS table.

Run:
    python -m component_search.setup /path/to/jlcpcb-components.sqlite3
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Optional, Sequence, Union

DatabasePath = Union[str, Path]


def _normalize_db_path(db_path: DatabasePath) -> Path:
    return Path(db_path).expanduser()


def create_components_search_view(db_path: DatabasePath) -> None:
    path = _normalize_db_path(db_path)
    conn = sqlite3.connect(str(path))
    cursor = conn.cursor()

    cursor.execute("DROP VIEW IF EXISTS v_components_search")

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
    conn.close()
    print("✓ Created view v_components_search")


def create_fts_search_table(db_path: DatabasePath) -> None:
    path = _normalize_db_path(db_path)
    conn = sqlite3.connect(str(path))
    cursor = conn.cursor()

    cursor.execute("DROP TABLE IF EXISTS v_components_search_fts")

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
    print("✓ Created FTS table v_components_search_fts")

    print("Populating FTS table (this may take a while)...")
    cursor.execute(
        """
        INSERT INTO v_components_search_fts(lcsc, mpn, package, family, class, specs)
        SELECT lcsc, mpn, package, family, class, specs
        FROM v_components_search
        """
    )

    conn.commit()
    print(f"✓ Populated FTS table with {cursor.rowcount} entries")

    conn.close()


def verify_setup(db_path: DatabasePath) -> None:
    path = _normalize_db_path(db_path)
    conn = sqlite3.connect(str(path))
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM v_components_search")
    view_count = cursor.fetchone()[0]
    print(f"✓ View contains {view_count} components")

    cursor.execute("SELECT COUNT(*) FROM v_components_search_fts")
    fts_count = cursor.fetchone()[0]
    print(f"✓ FTS table contains {fts_count} entries")

    cursor.execute(
        """
        SELECT lcsc, mpn, package, family, class
        FROM v_components_search_fts
        WHERE v_components_search_fts MATCH 'MOSFET'
        LIMIT 5
        """
    )
    results = cursor.fetchall()
    print(f"✓ FTS search test returned {len(results)} results")

    if results:
        print("\nSample search results:")
        for row in results:
            print(
                f"  LCSC: {row[0]}, MPN: {row[1]}, Package: {row[2]}, "
                f"Family: {row[3]}, Class: {row[4]}"
            )

    conn.close()


def setup_component_search(db_path: DatabasePath) -> None:
    path = _normalize_db_path(db_path)
    create_components_search_view(path)
    create_fts_search_table(path)
    verify_setup(path)


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare the component search view and FTS table."
    )
    parser.add_argument("db_path", help="Path to the jlcpcb-components.sqlite3 database")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    db_path = Path(args.db_path).expanduser()

    if not db_path.exists():
        print(f"Error: Database file not found: {db_path}")
        return 1

    print(f"Setting up component search for database: {db_path}\n")

    try:
        setup_component_search(db_path)
    except Exception as exc:  # pragma: no cover - CLI guard
        print(f"\n✗ Error during setup: {exc}")
        import traceback

        traceback.print_exc()
        return 1

    print("\n✓ Setup completed successfully!")
    print(
        "\nYou can now run free-form searches via search_mpn_part or the MCP tool.\n"
        "If you update the database later, rerun this command to refresh the "
        "view and FTS table."
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    sys.exit(main())
