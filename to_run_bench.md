> **Superseded by [`PAPER_ALIGNMENT.md`](PAPER_ALIGNMENT.md).** That document is the
> living runbook with status board, exact commands, parallelism guidance,
> and per-experiment scripts under `scripts/paper_alignment/`.
> The notes below are kept for historical context.

- Full MARITA batch on all available converted relational DBs, or a fixed 54-dataset subset.
- Peak RSS recording for the 10 reported datasets and YAGO.
- Reproducible disjoint vs non-disjoint ablation artifacts.
- Either rerun FK-only vs full-joinability ablation with saved results, or remove/soften that section.
- Optional YAGO ontology-lite ablation if you want the “representation sensitivity” claim to be stronger.




to run:

Two config files created. Here are the exact commands for each experiment:

  ---
  Critical note before running anything

  Do not use marita batch for any of these experiments. It constructs DatabaseProcessor without passing the config dict, so algorithm.parameters (walk_length, disjoint_semantics, etc.)
  silently reverts to defaults (walk_length=4, disjoint_semantics=false). Use marita paper-benchmark or marita run instead.

  ---
  Experiment 1 — Full MARITA batch on all 83 available DBs

  # Preview first (generates configs but doesn't execute)
  uv run marita paper-benchmark \
    --database-dir data/relational/ \
    --output results/full_83/ \
    --logs logs/full_83/ \
    --algorithms MARITA \
    --databases all \
    --timeout 7200 --memory-gb 15 \
    --dry-run

  # Full run (sequential, ~83 × up to 2 h worst-case)
  uv run marita paper-benchmark \
    --database-dir data/relational/ \
    --output results/full_83/ \
    --logs logs/full_83/ \
    --algorithms MARITA \
    --databases all \
    --timeout 7200 --memory-gb 15

  Results: results/full_83/MARITA/<db_stem>/ + results/full_83/summary.json.

  On the 54-dataset subset: There is no enumerated list of which 54 the paper counts out of the 83 currently on disk. Running all 83 is the conservative choice. If you isolate a specific 54
  into a subdirectory later, pass --database-dir <that-dir>.

  ---
  Experiment 2 — Peak RSS for the 10 reported datasets + YAGO

  paper-benchmark enforces the memory limit but does not write peak RSS to summary.json. Use dry-run to generate individual configs, then wrap each marita run with /usr/bin/time -v:

  # Generate per-database configs only
  uv run marita paper-benchmark \
    --database-dir data/relational/ \
    --output results/peak_rss/ \
    --logs logs/peak_rss/ \
    --algorithms MARITA \
    --databases paper \
    --timeout 7200 \
    --dry-run

  # Run each config under /usr/bin/time -v
  mkdir -p results/peak_rss/
  for cfg in results/peak_rss/configs/marita_*.yaml; do
    db=$(basename "$cfg" .yaml | sed 's/^marita_//')
    { /usr/bin/time -v uv run marita run --config "$cfg"; } 2>&1 \
      | grep "Maximum resident set size" \
      | awk -v db="$db" '{printf "%s\t%s kB\n", db, $NF}' \
      >> results/peak_rss/peak_rss_summary.txt
  done

  # YAGO core
  { /usr/bin/time -v uv run marita run --config configs/config.yago-core.yaml; } 2>&1 \
    | grep "Maximum resident set size" \
    | awk '{printf "yago_tiny_core\t%s kB\n", $NF}' \
    >> results/peak_rss/peak_rss_summary.txt

  ---
  Experiment 3 — Disjoint vs non-disjoint ablation

  The paper sweeps walk_length N ∈ {1..10} on Biodegradability with a 600 s timeout. paper-benchmark can't sweep walk_length or toggle disjoint_semantics, so use a shell loop generating
  inline configs:

  mkdir -p results/ablation_disjoint results/ablation_nondisjoint \
           logs/ablation_disjoint logs/ablation_nondisjoint

  for N in 1 2 3 4 5 6 7 8 9 10; do
    cat > /tmp/ablation_disjoint_N${N}.yaml <<YAMEOF
  monitor:
    memory_threshold: 16106127360
    timeout: 7200
  database:
    path: data/relational
    name: Biodegradability.db
  logging:
    log_dir: logs/ablation_disjoint/N${N}
  results:
    output_dir: results/ablation_disjoint/N${N}
  algorithm:
    name: MARITA
    parameters:
      walk_length: ${N}
      max_tables: 3
      max_variables: 3
      disjoint_semantics: true
      split_mean_threshold: 0.0
      timeout: 600
  mlflow:
    use: false
  YAMEOF
    uv run marita run --config /tmp/ablation_disjoint_N${N}.yaml

    cat > /tmp/ablation_nondisjoint_N${N}.yaml <<YAMEOF
  monitor:
    memory_threshold: 16106127360
    timeout: 7200
  database:
    path: data/relational
    name: Biodegradability.db
  logging:
    log_dir: logs/ablation_nondisjoint/N${N}
  results:
    output_dir: results/ablation_nondisjoint/N${N}
  algorithm:
    name: MARITA
    parameters:
      walk_length: ${N}
      max_tables: 3
      max_variables: 3
      disjoint_semantics: false
      split_mean_threshold: 0.0
      timeout: 600
  mlflow:
    use: false
  YAMEOF
    uv run marita run --config /tmp/ablation_nondisjoint_N${N}.yaml
  done

  Artifacts: 20 result directories under results/ablation_{disjoint,nondisjoint}/N{1..10}/.

  ---
  Experiment 4 — FK-only vs full-joinability

  Full-joinability is not implemented. constraint_graph.py:54–57 returns immediately after the FK check; lines 64–116 (value-overlap / domain-overlap) are unreachable dead code with no config
   knob to activate them.

  Recommendation: remove or soften the section. State in the paper that the joinability scope is declared FK edges only. The FK-only numbers are already produced by Experiment 1 — no separate
   command needed.

  ---
  Experiment 5 — YAGO ontology-lite ablation

  Config file configs/config.yago-ontology-lite.yaml was just created (points to data/yago/yago_tiny_ontology_lite.db).

  # Predicates-only core (baseline)
  uv run marita run --config configs/config.yago-core.yaml

  # Predicate schema + class hierarchy (representation-sensitive)
  uv run marita run --config configs/config.yago-ontology-lite.yaml

  Results go to results/yago_core/ and results/yago_ontology_lite/. Compare rule counts and runtimes to support the "representation sensitivity" claim.