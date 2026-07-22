# CLI Reference

All commands are run through:

```bash
uv run mahilda <command> [options]
```

## `run`

Runs the `MAHILDA` algorithm on a single database.

```bash
uv run mahilda run --config configs/config.example.yaml
```

- Uses `database.path` + `database.name` from config.
- Fails fast if `algorithm.name` is not `MAHILDA`.
- Writes artifacts under `results/` by default.

## `benchmark`

Runs one competitor baseline on a single database.

Install benchmark extras first:

```bash
uv sync --extra benchmark
```

```bash
uv run mahilda benchmark --config configs/config.example.yaml --baseline AMIE3
uv run mahilda benchmark --config configs/config.example.yaml --baseline SPIDER
uv run mahilda benchmark --config configs/config.example.yaml --baseline POPPER
uv run mahilda benchmark --config configs/config.example.yaml --baseline MATILDA
uv run mahilda benchmark --config configs/config.example.yaml --baseline AMIE3 --input-tsv data/yago/yago_tiny_core.tsv
```

- Allowed baselines: `AMIE3`, `SPIDER`, `POPPER`, `MATILDA` (`ILP` is normalized to `POPPER`).
- Baseline selection comes from `--baseline` first, then `benchmark.baseline`, then `algorithm.name`.
- `--input-tsv` lets AMIE3 consume a prebuilt RDF benchmark TSV directly.

## `audit`

Audits competitor rule artifacts against MAHILDA outputs under formal target-class and coverage criteria.

```bash
uv run mahilda audit --results-dir results/paper_table2 --database-dir data/relational
uv run mahilda audit --competitors MATILDA --coverage subsumption
uv run mahilda audit --settings configs/paper/benchmark_83.yaml --no-progress
```

Important flags:

- `--results-dir`: benchmark result root, default `results/paper_table2`
- `--database-dir`: SQLite database root, default `data/relational`
- `--output-dir`: audit report directory, default `<results-dir>/audit`
- `--status-dir`: benchmark status directory, default `<results-dir>/progress`; aggregate `summary*.json` files are also accepted
- `--coverage`: claim criterion, one of `alpha`, `subsumption`, or `instance`
- `--settings`: optional YAML config used to load MAHILDA bounds
- `--walk-length`, `--max-tables`, `--max-variables`, `--joinability`: explicit target-class bounds
- `--include-amie-rdf`: reconstruct the RDB-to-KG mapping and audit translatable AMIE3 rules; default is to skip them
- `--allow-legacy-amie-mapping`: bypass the hash-validated TSV mapping manifest; diagnostic use only
- `--confidence-threshold`, `--support-threshold`: exactness thresholds, default `1.0` and `0`
- `--no-diagnose-unmatched`: generate counts but suppress unmatched examples in `audit_diagnosis.md`
- `--strict`: exit `2` when comparable true rules remain uncovered under the selected criterion

Outputs include `audit_summary.json`, `audit_rules.csv`, `audit_funnel.csv`, `audit_exclusions.csv`, `audit_paper_table.tex`, `audit_examples.md`, `audit_unmatched.md`, `audit_diagnosis.md`, and `audit_claims.md`. See `docs/rule-audit.md` for the exact claim semantics.

## `import-rdf`

Imports an RDF/Turtle knowledge graph into auditable benchmark artifacts.

```bash
uv run mahilda import-rdf --input data/yago-tiny.ttl --output-dir data/yago --variants core,ontology-lite
```

- Writes a full archive DB, variant SQLite DBs, AMIE3 TSV exports, manifest JSON, and report markdown.
- The `core` variant contains ABox object facts, ABox datatype facts, and instance `rdf:type` facts.
- The `ontology-lite` variant adds explicit `rdfs:subClassOf` statements.

## `download-databases`

Downloads relational benchmark databases from `relational.fel.cvut.cz`, dumps them with `mysqldump`, and converts the dumps to SQLite.

Install optional dataset dependencies first:

```bash
uv sync --extra datasets
```

```bash
uv run mahilda download-databases --output data/relational
uv run mahilda download-databases --output data/relational --list
uv run mahilda download-databases --output data/relational --database Mondial
uv run mahilda download-databases --output data/relational --max-databases 10
uv run mahilda download-databases --output data/relational --dump-only
uv run mahilda download-databases --output data/relational --convert-only
```

Environment overrides:

- `MAHILDA_RELATIONAL_BASE_URL`
- `MAHILDA_RELATIONAL_HOST`
- `MAHILDA_RELATIONAL_PORT`
- `MAHILDA_RELATIONAL_USER`
- `MAHILDA_RELATIONAL_PASSWORD`
- `MAHILDA_RELATIONAL_DUMP_COMMAND`

Outputs:

- `<output>/<database>.sql`: raw MySQL/MariaDB dump
- `<output>/<database>.db`: converted SQLite database
- `<output>/conversion_report.txt`: preparation summary

## `batch`

Runs `MAHILDA` across many `.db` files.

```bash
uv run mahilda batch -c configs/config.example.yaml -d <db_dir> -o results/batch
```

Common flags:

- `--directory`: overrides `database.path`
- `--output`: output root (default `results/batch`)
- `--workers`: overrides `batch.workers`
- `--timeout`: overrides `batch.timeout` (per DB)
- `--start-from`: start index in sorted DB list
- `--max-databases`: cap processed DB count

Behavior notes:

- Directory is required via `--directory` or config `database.path`.
- Only `.db` files are scheduled.
- Worker logs are written under `<logging.log_dir>/<db_stem>/`.
- A run summary is written to `<output>/summary.txt`.

## `paper-benchmark`

Runs the extracted ISWC 2026 paper benchmark protocol across selected databases and algorithms. This command is the preferred path for reproducing paper numbers because it generates per-run configs with the paper parameters and wraps every selected algorithm with a uniform wall-clock timeout and RSS memory limit.

Install benchmark extras first when running Java baselines (`AMIE3`, `SPIDER`). POPPER uses an external `run-popper` command; see `docs/popper-user-space-install.md`. MATILDA uses the sibling repo from `benchmark.matilda_path`, `MAHILDA_MATILDA_PATH`, or `../MATILDA`.

```bash
uv sync --extra benchmark
```

```bash
uv run mahilda paper-benchmark --dry-run
uv run mahilda paper-benchmark --algorithms MAHILDA --databases paper
uv run mahilda paper-benchmark --algorithms MAHILDA,AMIE3 --databases Biodegradability,CORA
uv run mahilda paper-benchmark --algorithms ALL --databases paper --timeout 7200 --memory-gb 15
uv run mahilda paper-benchmark --settings configs/paper/benchmark_83.yaml --hosts tipi01,tipi02 --status
```

Important flags:

- `--database-dir`: directory containing `.db` files, default `data/relational`
- `--databases`: `paper`, `all`, or a comma-separated list of database names
- `--algorithms`: comma-separated list from `MAHILDA`, `AMIE3`, `SPIDER`, `POPPER`, `MATILDA`, or `ALL`
- `--output`: output root, default `results/iswc2026`
- `--logs`: log root, default `logs/iswc2026`
- `--timeout`: per-run timeout in seconds, default `7200`
- `--memory-gb`: per-run RSS limit, default `15`
- `--dry-run`: generate configs and `summary.json` / `summary.md` without executing commands
- `--status`: read live progress from `<output>/progress/*.json`, render a progress bar, and exit
- `--email-to`: send a best-effort completion email after the run; SMTP can be configured with `--email-from`, `--smtp-host`, `--smtp-port`, `--smtp-user`, `--smtp-password-env`, and `--smtp-starttls`

The generated MAHILDA configs set `disjoint_semantics: true` and keep that setting editable under `algorithm.parameters`.

Running benchmarks write one atomic JSON progress file per planned algorithm/database pair under `<output>/progress/`. The status view rebuilds the expected plan from the same settings and overlays those files, so any server sharing the output directory can show the same global state without contacting the other servers. Use the same `--settings`, `--databases`, `--algorithms`, and `--hosts` values for `--status` that you use for the benchmark run.

Email notifications can also be configured with environment variables:

```bash
export MAHILDA_EMAIL_TO="you@example.com"
export MAHILDA_EMAIL_FROM="you@example.com"
export MAHILDA_SMTP_HOST="smtp.example.com"
export MAHILDA_SMTP_PORT="587"
export MAHILDA_SMTP_USER="you@example.com"
export MAHILDA_SMTP_PASSWORD="your-smtp-password"
export MAHILDA_SMTP_STARTTLS="1"

uv run mahilda paper-benchmark --settings configs/paper/benchmark_83.yaml --email-to you@example.com
```

The notification is attempted on success, normal failure, uncaught exceptions, `SIGINT`, and `SIGTERM`. It cannot be sent after `SIGKILL`, machine crashes, network outages, or SMTP outages.

## `smoke`

Fast local check wired to `configs/config.test.yaml`.

```bash
uv run mahilda smoke
uv run mahilda smoke -v
uv run mahilda smoke -q
```

- `-v` and `-q` are mutually exclusive.
- Internally delegates to `mahilda run` with smoke config.

## `test-db`

Creates a tiny SQLite DB for smoke tests.

```bash
uv run mahilda test-db
uv run mahilda test-db --output test_data/test.db
```

## `mlflow`

Local convenience helpers:

```bash
uv run mahilda mlflow start
uv run mahilda mlflow ui
```

- `start`: launches a local file-backed MLflow tracking server.
- `ui`: launches MLflow web UI for local experiments.
