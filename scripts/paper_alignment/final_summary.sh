#!/usr/bin/env bash
# Assemble a single Markdown alignment summary from every TSV/JSON produced.
# Output: results/paper_alignment_<YYYY-MM-DD>.md
set -euo pipefail
REPO="${REPO:-$(git -C "$(dirname "$0")" rev-parse --show-toplevel)}"
cd "$REPO"

out="results/paper_alignment_$(date -I).md"
{
    echo "# Paper alignment $(date -I)"
    echo
    echo "## Table 1 (relational row counts)"
    cat results/paper_table1/relational_counts.tsv 2>/dev/null || echo "(missing - run b0_table1_counts.sh)"
    echo
    echo "## Table 2 (main results)"
    cat results/paper_table2/table2.tsv 2>/dev/null || echo "(missing — run b1c_table2_aggregate.sh)"
    echo
    echo "## Disjointness ablation coords"
    cat results/ablation_disjoint/figure_coords.tsv 2>/dev/null || echo "(missing — run b2_disjointness.sh)"
    echo
    echo "## Joinability ablation"
    cat results/ablation_join/comparison*.tsv 2>/dev/null || echo "(missing - run b3_joinability.sh)"
    echo
    echo "## YAGO core (MAHILDA)"
    cat results/yago_core/MAHILDA_yago_tiny_core/execution_time_yago_tiny_core.json 2>/dev/null || echo "(missing)"
    echo
    echo "## Host info"
    head -20 results/paper_table2/host_info*.txt 2>/dev/null || echo "(missing - run c1_host_info.sh)"
} > "$out"
echo "wrote $out"
