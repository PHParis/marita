#!/usr/bin/env bash
# B-1b: run this host's 83-database benchmark shard using the hardened runner.
# Usage: ./b1b_table2_run.sh [POPPER|AMIE3|SPIDER,MAHILDA|ALL]
set -euo pipefail
REPO="${REPO:-$(git -C "$(dirname "$0")" rev-parse --show-toplevel)}"
cd "$REPO"

target="${1:-ALL}"
mkdir -p logs/paper_table2 results/paper_table2

nohup uv run mahilda paper-benchmark \
    --settings configs/paper/benchmark_83.yaml \
    --host auto \
    --algorithms "$target" \
    > "logs/paper_table2/runner_${target//,/_}_$(hostname -s).log" 2>&1 &

echo "Started $target on $(hostname -s); log: logs/paper_table2/runner_${target//,/_}_$(hostname -s).log"
