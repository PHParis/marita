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
uv run mahilda benchmark --config configs/config.example.yaml --baseline AMIE3 --input-tsv data/yago/yago_tiny_core.tsv
```

- Allowed baselines: `AMIE3`, `SPIDER`, `POPPER` (`ILP` is normalized to `POPPER`).
- Baseline selection comes from `--baseline` first, then `benchmark.baseline`, then `algorithm.name`.
- `--input-tsv` lets AMIE3 consume a prebuilt RDF benchmark TSV directly.

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

Install benchmark extras first when running Java baselines (`AMIE3`, `SPIDER`). POPPER uses an external `run-popper` command; see `docs/popper-user-space-install.md`.

```bash
uv sync --extra benchmark
```

```bash
uv run mahilda paper-benchmark --dry-run
uv run mahilda paper-benchmark --algorithms MAHILDA --databases paper
uv run mahilda paper-benchmark --algorithms MAHILDA,AMIE3 --databases Biodegradability,CORA
uv run mahilda paper-benchmark --algorithms ALL --databases paper --timeout 7200 --memory-gb 15
```

Important flags:

- `--database-dir`: directory containing `.db` files, default `data/relational`
- `--databases`: `paper`, `all`, or a comma-separated list of database names
- `--algorithms`: comma-separated list from `MAHILDA`, `AMIE3`, `SPIDER`, `POPPER`, or `ALL`
- `--output`: output root, default `results/iswc2026`
- `--logs`: log root, default `logs/iswc2026`
- `--timeout`: per-run timeout in seconds, default `7200`
- `--memory-gb`: per-run RSS limit, default `15`
- `--dry-run`: generate configs and `summary.json` / `summary.md` without executing commands

The generated MAHILDA configs set `disjoint_semantics: true` and keep that setting editable under `algorithm.parameters`.

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
