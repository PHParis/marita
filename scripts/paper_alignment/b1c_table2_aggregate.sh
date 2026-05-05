#!/usr/bin/env bash
# B-1c: assemble Table 2 from execution metrics and distributed runner summaries.
# Output: results/paper_table2/table2.tsv
set -euo pipefail
REPO="${REPO:-$(git -C "$(dirname "$0")" rev-parse --show-toplevel)}"
cd "$REPO"
python3 - <<'PY' | tee results/paper_table2/table2.tsv
import json
import pathlib

ROOT = pathlib.Path("results/paper_table2")
DB_DIR = pathlib.Path("data/relational")
ALGOS = ["POPPER", "SPIDER", "AMIE3", "MAHILDA"]
DBS = sorted((p.stem for p in DB_DIR.glob("*.db")), key=str.lower)

statuses = {}
for summary in sorted(ROOT.glob("summary*.json")):
    data = json.load(open(summary))
    if data.get("dry_run"):
        continue
    for run in data.get("runs", []):
        algo = run.get("algorithm")
        db = pathlib.Path(str(run.get("database", ""))).stem
        statuses[(algo, db)] = run

def metric(algo, db):
    path = ROOT / algo / f"{algo}_{db}" / f"execution_time_{db}.json"
    if path.exists():
        return json.load(open(path))
    return None

def rss_gb(run):
    value = run.get("peak_rss_bytes") if run else None
    if isinstance(value, (int, float)):
        return f"{value / 1024**3:.2f}"
    return "-"

header = ["database"]
for algo in ALGOS:
    header += [f"{algo}_status", f"{algo}_rules", f"{algo}_time_s", f"{algo}_rss_GB"]
print("\t".join(header))

for db in DBS:
    row = [db]
    for algo in ALGOS:
        run = statuses.get((algo, db))
        status = run.get("status", "MISSING") if run else "MISSING"
        m = metric(algo, db)
        if status == "success" and m:
            rules = m.get("rules_count", "-")
            seconds = m.get("execution_time_seconds", "-")
            if isinstance(seconds, (int, float)):
                seconds = f"{seconds:.2f}"
        else:
            rules = status.upper()
            seconds = f"{float(run.get('duration_seconds', 0)):.2f}" if run else "-"
        row += [status, str(rules), str(seconds), rss_gb(run)]
    print("\t".join(row))
PY
