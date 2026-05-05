#!/usr/bin/env bash
# C-1: capture host hardware/kernel info to replace the hardcoded
# "Intel Xeon E5-2650 v2 @ 2.60 GHz" claim in the paper.
# Output: results/paper_table2/host_info_<hostname>.txt
set -euo pipefail
REPO="${REPO:-$(git -C "$(dirname "$0")" rev-parse --show-toplevel)}"
cd "$REPO"
mkdir -p results/paper_table2
{
    echo "# Host info captured $(date -Iseconds)"
    uname -a
    echo
    lscpu
    echo
    head -5 /proc/meminfo
} > "results/paper_table2/host_info_$(hostname -s).txt"
echo "wrote results/paper_table2/host_info_$(hostname -s).txt"
