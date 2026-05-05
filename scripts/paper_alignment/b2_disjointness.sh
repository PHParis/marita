#!/usr/bin/env bash
# B-2: disjoint vs non-disjoint sweep for the ablation figure.
# Recursion budget N=1..10 x {disjoint, nondisjoint} = 20 runs.
# Per-run timeout 600 s and 10 GB monitor cap. All on Biodegradability.
# Output: results/ablation_{disjoint,nondisjoint}/N{1..10}/ + figure_coords.tsv
set -euo pipefail
REPO="${REPO:-$(git -C "$(dirname "$0")" rev-parse --show-toplevel)}"
cd "$REPO"

mkdir -p results/ablation_disjoint results/ablation_nondisjoint \
         logs/ablation_disjoint   logs/ablation_nondisjoint \
         /tmp/ablation_cfg

for mode in disjoint nondisjoint; do
  case $mode in
    disjoint)    flag=true  ;;
    nondisjoint) flag=false ;;
  esac
  for N in 1 2 3 4 5 6 7 8 9 10; do
    cfg=/tmp/ablation_cfg/${mode}_N${N}.yaml
    cat > "$cfg" <<YAML
monitor:
  memory_threshold: 10737418240
  timeout: 600
database:
  path: $REPO/data/relational
  name: Biodegradability.db
logging:
  log_dir: $REPO/logs/ablation_${mode}/N${N}
results:
  output_dir: $REPO/results/ablation_${mode}/N${N}
algorithm:
  name: MAHILDA
  parameters:
    walk_length: ${N}
    max_tables: 3
    max_variables: 3
    disjoint_semantics: ${flag}
    split_mean_threshold: 0.0
    timeout: 600
mlflow:
  use: false
YAML
  done
done

# Run all 20 in parallel batches of 16 (Biodegradability is tiny)
ls /tmp/ablation_cfg/*.yaml | \
    xargs -n1 -P 16 -I{} bash -c '
        cfg="$1"
        out=$(grep "output_dir" "$cfg" | awk "{print \$2}")
        mkdir -p "$out"
        /usr/bin/time -v -o "${out}/time.log" \
            uv run mahilda run --config "$cfg" \
            > "${out}/stdout.log" 2>&1
    ' _ {}

# Aggregate into TikZ-ready coordinates
python3 - <<'PY' | tee results/ablation_disjoint/figure_coords.tsv
import json, pathlib
print("mode\tN\trules\twall_s")
for mode in ["disjoint","nondisjoint"]:
    for N in range(1,11):
        et = pathlib.Path(f"results/ablation_{mode}/N{N}/execution_time_Biodegradability.json")
        if not et.exists():
            print(f"{mode}\t{N}\tTIMEOUT_OR_MISSING\t-"); continue
        j = json.load(open(et))
        print(f"{mode}\t{N}\t{j.get('rules_count','?')}\t{j.get('execution_time_seconds','?')}")
PY
