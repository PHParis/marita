#!/usr/bin/env bash
# B-1c: assemble Table 2 from per-algorithm result folders.
# Output: results/paper_table2/table2.tsv
set -euo pipefail
REPO="${REPO:-$(git -C "$(dirname "$0")" rev-parse --show-toplevel)}"
cd "$REPO"
python3 - <<'PY' > results/paper_table2/table2.tsv
from __future__ import annotations

import datetime as dt
import json
import pathlib
from typing import Any

ROOT = pathlib.Path("results/paper_table2")
DB_DIR = pathlib.Path("data/relational")
ALGOS = ["POPPER", "SPIDER", "AMIE3", "MATILDA", "MAHILDA"]


def load_json(path: pathlib.Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def timestamp(payload: dict[str, Any]) -> dt.datetime:
    for key in ("updated_at", "ended_at", "timestamp", "started_at"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            try:
                return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                pass
    return dt.datetime.min.replace(tzinfo=dt.timezone.utc)


def choose_newer(current: dict[str, Any] | None, candidate: dict[str, Any]) -> dict[str, Any]:
    if current is None or timestamp(candidate) >= timestamp(current):
        return candidate
    return current


def db_stems() -> list[str]:
    if DB_DIR.exists():
        return sorted((p.stem for p in DB_DIR.glob("*.db")), key=str.lower)

    stems: set[str] = set()
    for algo in ALGOS:
        for folder in (ROOT / algo).glob(f"{algo}_*"):
            if folder.is_dir():
                stems.add(folder.name.removeprefix(f"{algo}_"))
    return sorted(stems, key=str.lower)


run_metadata: dict[tuple[str, str], dict[str, Any]] = {}

for summary in sorted(ROOT.glob("summary*.json")):
    data = load_json(summary)
    if not data or data.get("dry_run"):
        continue
    for run in data.get("runs", []):
        if not isinstance(run, dict):
            continue
        algo = str(run.get("algorithm", "")).upper()
        db = pathlib.Path(str(run.get("database", ""))).stem
        if algo and db:
            key = (algo, db)
            run_metadata[key] = choose_newer(run_metadata.get(key), run)

for progress in sorted((ROOT / "progress").glob("*.json")):
    data = load_json(progress)
    if not data:
        continue
    algo = str(data.get("algorithm", "")).upper()
    db = pathlib.Path(str(data.get("database", ""))).stem
    if algo and db:
        key = (algo, db)
        run_metadata[key] = choose_newer(run_metadata.get(key), data)


def execution_metric(algo: str, db: str) -> dict[str, Any] | None:
    run_dir = ROOT / algo / f"{algo}_{db}"
    exact = run_dir / f"execution_time_{db}.json"
    if exact.exists():
        return load_json(exact)
    matches = sorted(run_dir.glob("execution_time_*.json"))
    if matches:
        return load_json(matches[0])
    return None


def format_seconds(value: Any) -> str:
    if isinstance(value, int | float):
        return f"{float(value):.2f}"
    if isinstance(value, str) and value:
        try:
            return f"{float(value):.2f}"
        except ValueError:
            return value
    return "-"


def rss_gb(payload: dict[str, Any] | None) -> str:
    if not payload:
        return "-"
    value = payload.get("peak_rss_bytes")
    if isinstance(value, int | float):
        return f"{float(value) / 1024**3:.2f}"
    return "-"


def row_values(algo: str, db: str) -> list[str]:
    metric = execution_metric(algo, db)
    run = run_metadata.get((algo, db))

    if metric:
        status = str(metric.get("status") or run.get("status") if run else metric.get("status") or "missing").lower()
        seconds = format_seconds(metric.get("execution_time_seconds"))
        if status == "success":
            rules = str(metric.get("rules_count", "-"))
        else:
            rules = status.upper()
        return [status, rules, seconds, rss_gb(run)]

    if run:
        status = str(run.get("status", "missing")).lower()
        seconds = format_seconds(run.get("duration_seconds"))
        rules = status.upper()
        return [status, rules, seconds, rss_gb(run)]

    return ["missing", "MISSING", "-", "-"]


header = ["database"]
for algo in ALGOS:
    header.extend([f"{algo}_status", f"{algo}_rules", f"{algo}_time_s", f"{algo}_rss_GB"])
print("\t".join(header))

for db in db_stems():
    row = [db]
    for algo in ALGOS:
        row.extend(row_values(algo, db))
    print("\t".join(row))
PY
