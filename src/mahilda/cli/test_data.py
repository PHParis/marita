"""
Script to create a simple test database for MAHILDA.
This creates a SQLite database with sample relational data.
"""

import argparse
import sqlite3
from pathlib import Path


def create_test_database(db_path: str = "test_data/test.db"):
    """
    Creates a tiny test database for ultra-fast testing (< 10 seconds).

    Schema:
    - Person(id, name) - Minimal table with only 2 rows
    """
    # Create directory if it doesn't exist
    db_file = Path(db_path)
    db_file.parent.mkdir(parents=True, exist_ok=True)

    # Remove existing database if it exists
    if db_file.exists():
        db_file.unlink()

    # Create database connection
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Create single minimal table
    cursor.execute("""
        CREATE TABLE Person (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL
        )
    """)

    # Insert minimal sample data - just 2 rows
    persons = [
        (1, "Alice"),
        (2, "Bob"),
    ]
    cursor.executemany("INSERT INTO Person VALUES (?, ?)", persons)

    # Commit and close
    conn.commit()
    conn.close()

    print(f"✓ Test database created successfully at: {db_path}")
    print("  - 2 persons")
    print("\nTable created:")
    print("  - Person(id, name)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create the local smoke-test SQLite database.")
    parser.add_argument(
        "-o",
        "--output",
        default="test_data/test.db",
        help="Output SQLite path (default: test_data/test.db)",
    )
    args = parser.parse_args(argv)
    create_test_database(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
