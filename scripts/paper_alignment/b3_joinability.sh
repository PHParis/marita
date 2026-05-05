#!/usr/bin/env bash
# B-3: FK-only vs full-joinability sweep on this host's database shard.
# Output: results/ablation_join/{fk_only,full_join}/<DB>/ + comparison.tsv
set -euo pipefail
REPO="${REPO:-$(git -C "$(dirname "$0")" rev-parse --show-toplevel)}"
cd "$REPO"

host="$(hostname -s)"
hosts=(tipi00 tipi01 tipi02 tipi04)
host_index=-1
for i in "${!hosts[@]}"; do
    if [ "${hosts[$i]}" = "$host" ]; then host_index="$i"; fi
done
if [ "$host_index" = "-1" ]; then
    echo "ERROR: host $host is not one of: ${hosts[*]}"
    exit 1
fi

mkdir -p /tmp/ablation_join_cfg_"$host" results/ablation_join logs/ablation_join
rm -f /tmp/ablation_join_cfg_"$host"/*.yaml

mapfile -t dbs < <(python3 - <<'PY'
import pathlib
for path in sorted(pathlib.Path("data/relational").glob("*.db"), key=lambda p: p.name.lower()):
    print(path.stem)
PY
)

for idx in "${!dbs[@]}"; do
    if [ $((idx % ${#hosts[@]})) -ne "$host_index" ]; then
        continue
    fi
    db="${dbs[$idx]}"
    for mode in fk_only full_join; do
        case $mode in
            fk_only) join_val=fk ;;
            full_join) join_val=full ;;
        esac
        cfg=/tmp/ablation_join_cfg_"$host"/${mode}_${db}.yaml
        cat > "$cfg" <<YAML
monitor:
  memory_threshold: 10737418240
  timeout: 3600
database:
  path: $REPO/data/relational
  name: ${db}.db
logging:
  log_dir: $REPO/logs/ablation_join/${mode}/${db}
results:
  output_dir: $REPO/results/ablation_join/${mode}/${db}
algorithm:
  name: MAHILDA
  parameters:
    walk_length: 3
    max_tables: 3
    max_variables: 3
    disjoint_semantics: true
    joinability: ${join_val}
    split_mean_threshold: 0.0
    timeout: 3600
mlflow:
  use: false
YAML
    done
done

ls /tmp/ablation_join_cfg_"$host"/*.yaml | \
    xargs -n1 -P 2 -I{} bash -c '
        cfg="$1"
        out=$(grep "output_dir" "$cfg" | awk "{print \$2}")
        mkdir -p "$out"
        /usr/bin/time -v -o "${out}/time.log" \
            uv run mahilda run --config "$cfg" \
            > "${out}/stdout.log" 2>&1
    ' _ {}

MAHILDA_B3_HOST="$host" python3 - <<'PY' > "results/ablation_join/comparison_${host}.tsv"
import hashlib
import json
import os
import pathlib

hosts = ["tipi00", "tipi01", "tipi02", "tipi04"]
host = os.environ["MAHILDA_B3_HOST"]
host_index = hosts.index(host)
print("database\tfk_only_rules_hash\tfull_join_rules_hash\tequivalent\tfk_time\tfull_time")
for idx, db_path in enumerate(sorted(pathlib.Path("data/relational").glob("*.db"), key=lambda p: p.name.lower())):
    if idx % len(hosts) != host_index:
        continue
    db = db_path.stem
    fk_rules = pathlib.Path(f"results/ablation_join/fk_only/{db}/MAHILDA_{db}_results.json")
    fj_rules = pathlib.Path(f"results/ablation_join/full_join/{db}/MAHILDA_{db}_results.json")
    fk_et = pathlib.Path(f"results/ablation_join/fk_only/{db}/execution_time_{db}.json")
    fj_et = pathlib.Path(f"results/ablation_join/full_join/{db}/execution_time_{db}.json")

    def rule_set_hash(path):
        if not path.exists(): return "MISSING"
        rules = sorted(json.load(open(path)), key=lambda r: r.get("display", ""))
        h = hashlib.sha256()
        for rule in rules: h.update(rule.get("display", "").encode())
        return f"{h.hexdigest()[:12]} ({len(rules)} rules)"

    fk_t = json.load(open(fk_et)).get("execution_time_seconds", "-") if fk_et.exists() else "-"
    fj_t = json.load(open(fj_et)).get("execution_time_seconds", "-") if fj_et.exists() else "-"
    h1, h2 = rule_set_hash(fk_rules), rule_set_hash(fj_rules)
    eq = "OK" if h1 == h2 else "DIFF"
    print(f"{db}\t{h1}\t{h2}\t{eq}\t{fk_t}\t{fj_t}")
PY
