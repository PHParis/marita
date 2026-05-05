#!/usr/bin/env bash
# B-0: count tables and rows for every available relational .db file.
set -euo pipefail
REPO="${REPO:-$(git -C "$(dirname "$0")" rev-parse --show-toplevel)}"
cd "$REPO"
mkdir -p results/paper_table1
python3 - <<'PY' | tee results/paper_table1/relational_counts.tsv
import pathlib
import sqlite3

print("database\ttables\trows")
for db in sorted(pathlib.Path("data/relational").glob("*.db"), key=lambda p: p.name.lower()):
    con = sqlite3.connect(db)
    cur = con.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    tables = [row[0] for row in cur.fetchall()]
    rows = 0
    for table in tables:
        rows += cur.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
    con.close()
    print(f"{db.stem}\t{len(tables)}\t{rows}")
PY
