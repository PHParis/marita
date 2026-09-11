# Reproducing our experiments

We reproduce here the benchmarks and the commands to run MARITA and the competitors on these benchmarks. 

## Documentation 

The following files document the experiments and benchmarks:

- `docs/benchmark-datasets.md`: relational benchmark dataset download, conversion workflow, and fast MARITA regression-testing guidance
- `docs/iswc2026-benchmark-extraction.md`: extracted paper parameters, reported results, and rerun notes
- `docs/archive.md`: archive notes for `legacy/` and `research/`
- `docs/yago-rdf-benchmark-protocol.md`: RDF-to-SQLite benchmark protocol for YAGO-derived datasets
- `docs/yago-rdf-test-readiness.md`: remaining steps before full YAGO benchmark runs
- `docs/implementation-plan.md`: tracked implementation phases

## Baseline Runtime Dependencies

To run the competitors, install the following dependencies:

- `AMIE3`: Java runtime
- `SPIDER`: Java runtime
- `POPPER`: external `run-popper` command on `PATH`; see `docs/popper-user-space-install.md`
- `MATILDA`: sibling MATILDA repo configured with `benchmark.matilda_path`, `MARITA_MATILDA_PATH`, or default `../MATILDA`

Install Python baseline extras before running Java competitor benchmarks:

```bash
uv sync --extra benchmark
```

## Basic commands

The basic commands to run MARITA on the benchmarks are:
- `uv run marita benchmark --config configs/config.example.yaml --baseline {AMIE3|SPIDER|POPPER|MATILDA}` runs one baseline.
- `uv run marita download-databases --output data/relational` downloads relational benchmark databases and converts them to SQLite.
- `uv run marita paper-benchmark --dry-run` generates the ISWC 2026 paper benchmark rerun plan.
- `uv run marita import-rdf --input data/yago-tiny.ttl --output-dir data/yago` creates RDF benchmark artifacts.
- `uv run marita batch -c configs/config.example.yaml -d <db_dir> -o results/batch` processes many databases.
- `uv run marita mlflow start` launches a local MLflow tracking server; `uv run marita mlflow ui` launches the local MLflow UI.

To run all systems (MARITA, SPIDER, POPPER) plus AMIE3 TSV, run:

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
