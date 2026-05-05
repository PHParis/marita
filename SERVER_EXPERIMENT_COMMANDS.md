# Four-Server Experiment Commands

This runbook assumes the repo path and result directories are shared across:

- `tipi00`
- `tipi01`
- `tipi02`
- `tipi04`

Run commands from the repo root on each server:

```bash
cd /home/paris/dev/py/mahilda
```

## 0. Before Running

These commands use the repo's distributed benchmark support:

- `--settings configs/paper/benchmark_83.yaml`
- `--host auto`
- host sharding across `tipi00`, `tipi01`, `tipi02`, `tipi04`
- hard per-run timeout and memory enforcement
- host-specific summary files under `results/paper_table2/`

The shared profile is `configs/paper/benchmark_83.yaml`.

## 1. One-Time Setup

Run on each server:

```bash
cd /home/paris/dev/py/mahilda
uv sync
mkdir -p logs/paper_table2 results/paper_table2
```

Verify the host name is one of the expected names:

```bash
hostname -s
```

Expected output must be one of:

```text
tipi00
tipi01
tipi02
tipi04
```

## 2. Dry Run On Each Server

Run this on each server before launching long jobs:

```bash
cd /home/paris/dev/py/mahilda
uv run mahilda paper-benchmark \
  --settings configs/paper/benchmark_83.yaml \
  --host auto \
  --dry-run
```

Check that each server writes or reports only its own shard and that the union across the four servers covers all 83 `.db` files exactly once.

## 3. Capture Host Info

Run on each server:

```bash
cd /home/paris/dev/py/mahilda
mkdir -p results/paper_table2
{
  echo "# Host info captured $(date -Iseconds)"
  echo "hostname: $(hostname -s)"
  uname -a
  lscpu
  echo
  head -5 /proc/meminfo
} > "results/paper_table2/host_info_$(hostname -s).txt"
```

## 3b. Table 1 Counts

Run once, from any one server:

```bash
cd /home/paris/dev/py/mahilda
scripts/paper_alignment/b0_table1_counts.sh
```

Expected artifact:

```text
results/paper_table1/relational_counts.tsv
```

## 4. Start POPPER First

POPPER is expected to be the bottleneck and should run at one worker per server.

Run on each server:

```bash
cd /home/paris/dev/py/mahilda
nohup uv run mahilda paper-benchmark \
  --settings configs/paper/benchmark_83.yaml \
  --host auto \
  --algorithms POPPER \
  > "logs/paper_table2/runner_popper_$(hostname -s).log" 2>&1 &
```

Check progress:

```bash
tail -f "logs/paper_table2/runner_popper_$(hostname -s).log"
```

## 5. Start AMIE3

AMIE3 should run at one worker per server under the 10 GB RSS cap. The Java heap should be lower than the RSS cap, for example `-Xmx8G`, through `configs/paper/benchmark_83.yaml`.

Run on each server:

```bash
cd /home/paris/dev/py/mahilda
nohup uv run mahilda paper-benchmark \
  --settings configs/paper/benchmark_83.yaml \
  --host auto \
  --algorithms AMIE3 \
  > "logs/paper_table2/runner_amie3_$(hostname -s).log" 2>&1 &
```

Check progress:

```bash
tail -f "logs/paper_table2/runner_amie3_$(hostname -s).log"
```

## 6. Start SPIDER And MAHILDA

SPIDER and MAHILDA are faster. They can run after POPPER/AMIE3 are safely underway, or immediately if the configured worker counts keep the server below the admin limit.

Run on each server:

```bash
cd /home/paris/dev/py/mahilda
nohup uv run mahilda paper-benchmark \
  --settings configs/paper/benchmark_83.yaml \
  --host auto \
  --algorithms SPIDER,MAHILDA \
  > "logs/paper_table2/runner_spider_mahilda_$(hostname -s).log" 2>&1 &
```

Check progress:

```bash
tail -f "logs/paper_table2/runner_spider_mahilda_$(hostname -s).log"
```

## 7. Monitor Running Jobs

On each server:

```bash
ps -u "$USER" -f | grep 'mahilda paper-benchmark' | grep -v grep
```

Check server load and memory:

```bash
uptime
free -h
```

Check all runner logs from the shared filesystem:

```bash
cd /home/paris/dev/py/mahilda
ls -lh logs/paper_table2/runner_*.log
```

## 8. Aggregate B-1 Main Results

Run once, from any one server, after the four hosts finish B-1:

```bash
cd /home/paris/dev/py/mahilda
scripts/paper_alignment/b1c_table2_aggregate.sh
```

Expected final artifact:

```text
results/paper_table2/table2.tsv
```

Before trusting the TSV, verify that there is exactly one status for every algorithm/database pair:

```bash
cd /home/paris/dev/py/mahilda
python3 - <<'PY'
import json
from pathlib import Path

root = Path("results/paper_table2")
summaries = sorted(root.glob("summary_*.json"))
seen = {}
for summary in summaries:
    payload = json.loads(summary.read_text())
    for run in payload.get("runs", []):
        key = (run.get("algorithm"), run.get("database"))
        seen.setdefault(key, []).append(summary.name)

duplicates = {k: v for k, v in seen.items() if len(v) != 1}
print(f"summary files: {len(summaries)}")
print(f"algorithm/database pairs: {len(seen)}")
print(f"duplicates_or_missing_from_seen: {len(duplicates)}")
for key, files in sorted(duplicates.items()):
    print(key, files)
PY
```

For all 83 databases and 4 algorithms, the target is:

```text
algorithm/database pairs: 332
duplicates_or_missing_from_seen: 0
```

## 9. Run B-2 Disjointness Ablation

Keep B-2 on `Biodegradability` only for the deadline-critical paper figure.

Run once, from any one server:

```bash
cd /home/paris/dev/py/mahilda
scripts/paper_alignment/b2_disjointness.sh
```

Expected artifact:

```text
results/ablation_disjoint/figure_coords.tsv
```

Do not expand B-2 to all 83 databases before the deadline. That would create 1660 MAHILDA runs.

## 10. Run B-3 Joinability Ablation

Run this on each server. The script automatically shards the 83 databases by `hostname -s`:

```bash
cd /home/paris/dev/py/mahilda
mkdir -p logs/ablation_join
nohup scripts/paper_alignment/b3_joinability.sh \
  > "logs/ablation_join/runner_joinability_$(hostname -s).log" 2>&1 &
```

Expected artifacts:

```text
results/ablation_join/comparison_tipi00.tsv
results/ablation_join/comparison_tipi01.tsv
results/ablation_join/comparison_tipi02.tsv
results/ablation_join/comparison_tipi04.tsv
```

## 11. Final Summary

Run once, from any one server, after B-0/B-1/B-2/B-3 are complete:

```bash
cd /home/paris/dev/py/mahilda
scripts/paper_alignment/final_summary.sh
```

Expected artifact:

```text
results/paper_alignment_<YYYY-MM-DD>.md
```

## Emergency Stop

To stop only the runner started on the current server:

```bash
pkill -TERM -f 'mahilda paper-benchmark'
```

If a process does not exit after a short wait:

```bash
pkill -KILL -f 'mahilda paper-benchmark'
```

Use `SIGKILL` only as a last resort.
