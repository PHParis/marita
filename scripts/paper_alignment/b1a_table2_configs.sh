#!/usr/bin/env bash
# B-1a: generate per-(algorithm, database) configs for the ISWC paper batch.
# WARNING: pass --clean to remove results/paper_table2 and logs/paper_table2 first.
# Output: results/paper_table2/configs/{mahilda,amie3,spider,popper}_<db>.yaml
set -euo pipefail
REPO="${REPO:-$(git -C "$(dirname "$0")" rev-parse --show-toplevel)}"
cd "$REPO"
if [ "${1:-}" = "--clean" ]; then
    rm -rf results/paper_table2 logs/paper_table2
fi
uv run mahilda paper-benchmark \
    --settings configs/paper/benchmark_83.yaml \
    --host auto \
    --dry-run
