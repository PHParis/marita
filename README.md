# MARITA

MARITA is a rule-discovery toolkit for relational databases centered on the `MARITA` algorithm, with optional competitor baselines for benchmarking.

## Quick Start

```bash
uv sync
uv run marita test-db
uv run marita smoke
```

## CLI Overview

- `uv run marita run --config configs/config.example.yaml` runs `MARITA` on one database.
- `uv run marita benchmark --config configs/config.example.yaml --baseline {AMIE3|SPIDER|POPPER|MATILDA}` runs one baseline.
- `uv run marita download-databases --output data/relational` downloads relational benchmark databases and converts them to SQLite.
- `uv run marita paper-benchmark --dry-run` generates the ISWC 2026 paper benchmark rerun plan.
- `uv run marita import-rdf --input data/yago-tiny.ttl --output-dir data/yago` creates RDF benchmark artifacts.
- `uv run marita batch -c configs/config.example.yaml -d <db_dir> -o results/batch` processes many databases.
- `uv run marita mlflow start` launches a local MLflow tracking server; `uv run marita mlflow ui` launches the local MLflow UI.

## YAGO Reproducibility

For the generated YAGO core benchmark, use one of these reproducible paths.

Direct per-algorithm path:

```bash
uv sync --extra benchmark
uv run marita import-rdf --input data/yago-tiny.ttl --output-dir data/yago --variants core,ontology-lite --dataset-name yago_tiny
uv run marita run --config configs/config.yago-core.yaml
uv run marita benchmark --config configs/config.yago-core.yaml --baseline AMIE3 --input-tsv data/yago/yago_tiny_core.tsv
uv run marita benchmark --config configs/config.yago-core.yaml --baseline SPIDER
uv run marita benchmark --config configs/config.yago-core.yaml --baseline POPPER
```

Paper-runner path (for MARITA, SPIDER, POPPER) plus direct AMIE3 TSV run:

```bash
uv run marita paper-benchmark \
  --database-dir data/yago \
  --databases yago_tiny_core \
  --algorithms MARITA,SPIDER,POPPER \
  --output results/yago_core_all \
  --logs logs/yago_core_all \
  --timeout 7200 \
  --memory-gb 15
uv run marita benchmark \
  --config configs/config.yago-core.yaml \
  --baseline AMIE3 \
  --input-tsv data/yago/yago_tiny_core.tsv
```

`configs/config.yago-core.yaml` pins the YAGO core SQLite DB, logs, results, timeout, and AMIE3 TSV input. Use the direct TSV path for AMIE3 so it is compared against the same selected YAGO statements as the relational baselines.

The YAGO config is aligned to paper defaults for reproducibility (`walk_length: 3`, `max_tables: 3`, `max_variables: 3`, `disjoint_semantics: true`, `timeout: 7200`, monitor memory threshold 15 GB).

## Artifact Layout

- Logs: `logs/`
- Downloaded relational benchmark databases: `data/relational/`
- Single-run and benchmark artifacts: `results/`
- Batch artifacts: `results/batch/`
- Local MLflow data: `mlruns/`

See `docs/config.md` for full path-resolution behavior and overrides.

## Benchmark Progress

`paper-benchmark` writes live per-run progress files to `<output>/progress/`. When the output directory is on shared storage, any benchmark host can display the global state:

```bash
uv run marita paper-benchmark \
  --settings configs/paper/benchmark_83.yaml \
  --hosts tipi01,tipi02 \
  --status
```

Use the same `--settings`, `--databases`, `--algorithms`, and `--hosts` values as the running benchmark so pending counts match the intended plan.

## Email Notifications

`paper-benchmark` can send a best-effort completion email from Python when the run succeeds, fails, raises an exception, or receives `SIGINT`/`SIGTERM`. Email cannot be guaranteed after `SIGKILL`, machine crashes, network outages, or SMTP outages.

Configure SMTP with environment variables so passwords are not written in shell history:

```bash
export MARITA_EMAIL_TO="you@example.com"
export MARITA_EMAIL_FROM="you@example.com"
export MARITA_SMTP_HOST="smtp.example.com"
export MARITA_SMTP_PORT="587"
export MARITA_SMTP_USER="you@example.com"
export MARITA_SMTP_PASSWORD="your-smtp-password"
export MARITA_SMTP_STARTTLS="1"
```

Then run the benchmark normally:

```bash
uv run marita paper-benchmark --settings configs/paper/benchmark_83.yaml --email-to you@example.com
```

Equivalent CLI flags are available: `--email-to`, `--email-from`, `--smtp-host`, `--smtp-port`, `--smtp-user`, `--smtp-password-env`, and `--smtp-starttls`. If `--email-to` and `MARITA_EMAIL_TO` are both unset, no email is sent.

## Documentation

- `docs/architecture.md`: package structure, active modules, and runtime data flow
- `docs/cli.md`: command behavior and examples
- `docs/config.md`: configuration schema, defaults, normalization, and precedence
- `docs/benchmark-datasets.md`: relational benchmark dataset download, conversion workflow, and fast MARITA regression-testing guidance
- `docs/iswc2026-benchmark-extraction.md`: extracted paper parameters, reported results, and rerun notes
- `docs/archive.md`: archive notes for `legacy/` and `research/`
- `docs/yago-rdf-benchmark-protocol.md`: RDF-to-SQLite benchmark protocol for YAGO-derived datasets
- `docs/yago-rdf-test-readiness.md`: remaining steps before full YAGO benchmark runs
- `docs/implementation-plan.md`: tracked implementation phases

## Development

```bash
uv run pytest
uv run ruff check .
uv run ruff format .
uv run pyright
```

Pre-commit hooks run Ruff and Pyright automatically:

```bash
uv run pre-commit install
```

## Baseline Runtime Dependencies

- `AMIE3`: Java runtime
- `SPIDER`: Java runtime
- `POPPER`: external `run-popper` command on `PATH`; see `docs/popper-user-space-install.md`
- `MATILDA`: sibling MATILDA repo configured with `benchmark.matilda_path`, `MARITA_MATILDA_PATH`, or default `../MATILDA`

Install Python baseline extras before running Java competitor benchmarks:

```bash
uv sync --extra benchmark
```
