# MAHILDA baseline capture

The tracked manifest `research/generated/mahilda_baseline_20260719.json` records
the MAHILDA/MARITA evidence captured from `tipi01` on 2026-07-19. The raw
archive is intentionally repository-local and ignored:
`results/mahilda_tipi01_20260719/`.

## Capture command

The collector performs read-only SSH commands and uses rsync only to copy data
to the local archive. It never runs MAHILDA and never copies a source database.
For the original capture, the equivalent command was:

```bash
uv run python scripts/capture_mahilda_baseline.py \
  --host tipi01 \
  --source-databases /people/paris/mahilda/data/relational \
  --source-results /people/paris/mahilda/results/paper_table2/MAHILDA \
  --source-logs /people/paris/mahilda/logs/paper_table2/MAHILDA \
  --source-timings /people/paris/mahilda/results/paper_table2/timings \
  --source-run-configs /people/paris/mahilda/results/paper_table2/configs \
  --source-config /people/paris/mahilda/configs/paper/benchmark_83.yaml \
  --source-pipeline-log /people/paris/mahilda/logs/paper_pipeline/runner_tipi01.log \
  --source-repo /people/paris/mahilda \
  --output results/mahilda_tipi01_20260719 \
  --manifest research/generated/mahilda_baseline_20260719.json
```

The source paths can be changed for a future capture. Use a new dated output
and manifest path; the collector refuses to populate a non-empty archive.

## Archive layout

```text
results/mahilda_tipi01_YYYYMMDD/
├── raw/results/MAHILDA/       # result JSON, metric JSON, and reports
├── raw/logs/MAHILDA/          # per-database global/query logs
├── raw/logs/timings/          # mahilda_*.stdout files
├── raw/logs/pipeline/         # optional host pipeline log
├── provenance/config/         # benchmark profile
├── provenance/configs/        # per-database MAHILDA configs
├── provenance/host_and_git.json
├── provenance/source_paths.json
├── derived/normalized_rules.jsonl
└── checksums.sha256
```

The raw JSON remains untouched. `normalized_rules.jsonl` provides one stable
record per rule with `database`, `rule_index`, `type`, `body`, `head`,
`display`, `support`, `confidence`, and a SHA-256 `rule_hash`.

## Manifest schema

The manifest is a compact, tracked index with these top-level sections:

- `capture`, `source`, `configuration`, and `provenance` describe where and
  under which Git/configuration state the evidence was collected.
- `summary` gives counts for databases, attempts, artifact classes, status
  classes, and checksum verification.
- `databases` contains one record per source `.db`, including its remote path,
  size, SHA-256, SQLite table/row/column/foreign-key/index statistics, every
  parsed log attempt, the latest available artifact snapshot, and cross-checks
  between logs, reports, execution JSON, and rule JSON.
- `copy_checksums` records source/archive equality for each copied file.

Log attempts are authoritative for outcomes. The execution JSON status is
preserved separately because an older successful artifact can remain after a
later timeout or interruption. Status values are explicit: `success`,
`no_joinable_indexed`, `timeout`, `memory_limit`, `interrupted`, `error`,
`partial`, and `missing`.

## Known gaps and regression use

The source snapshot has 83 database references, 60 result/execution pairs, 82
initialization/constraint-graph/compatibility metric sets, 22 metric-only
partial runs, and one database with no current result artifacts. The log tree
retains repeated attempts and failures, including the observed `tpcds`
`10.01 GB` memory event. The configured limit is 10 GB. Completed-run peak RSS
is unavailable unless explicitly present in the captured evidence; it is not
inferred from runtime or file size.

The archive is evidence, not a new benchmark input. For a future regression:

1. Compare source database hashes before comparing rule counts.
2. Compare log-derived statuses and attempt timestamps, not only execution JSON.
3. Compare normalized rule hashes for exact rule-set changes, then inspect the
   raw result JSON and reports for explanations.
4. Treat metric-only and missing-artifact databases as incomplete observations,
   not zero-rule successes.

Run the collector tests with:

```bash
uv run pytest tests/test_capture_mahilda_baseline.py
```
