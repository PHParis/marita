# MAHILDA

MAHILDA is a rule-discovery toolkit for relational databases focused on the `MAHILDA` algorithm.

Competitor systems are available as benchmark baselines:

- `AMIE3` (Java jar)
- `SPIDER` (Java jars)
- `POPPER` (vendored Popper + clingo + SWI-Prolog)

This repository uses `uv` + `pyproject.toml` for reproducible Python environments.

## Prerequisites

### Required for Python setup

- Python `>=3.10`
- `uv`

### Required by benchmark baselines

- Java (required for `AMIE3` and `SPIDER`)
- SWI-Prolog runtime (`swipl`) for `POPPER`
- `sqlite3` for SQL-to-SQLite conversion workflows
- `mysqldump` for `download_databases.py`

## Quick Start (uv)

```bash
uv sync
uv run mahilda test-db
```

## Main Entrypoints

### 1) Fast smoke test

```bash
uv run mahilda test-db
uv run mahilda smoke
```

Verbose/quiet modes:

```bash
uv run mahilda smoke -v
uv run mahilda smoke -q
```

### 2) Single database run

```bash
uv run mahilda run --config configs/config.example.yaml
```

`mahilda run` is MAHILDA-only.

### 3) Baseline benchmark run

```bash
uv run mahilda benchmark --config configs/config.example.yaml --baseline AMIE3
uv run mahilda benchmark --config configs/config.example.yaml --baseline SPIDER
uv run mahilda benchmark --config configs/config.example.yaml --baseline POPPER
```

### 4) Batch processing

```bash
uv run mahilda batch -c configs/config.example.yaml -o <results_dir> [--workers N --timeout SEC --max-databases K --start-from I]
uv run mahilda batch -d <db_dir> -o <results_dir> [--workers N --timeout SEC --max-databases K --start-from I]
```

`batch` uses `database.path` from config when `-d/--directory` is not provided.

### 5) MLflow helpers

```bash
uv run mahilda mlflow start
uv run mahilda mlflow ui
```

## Configuration Notes

- Config files are stored in `configs/`.
- Relative paths in config are resolved from the config file location.
- Runner expects `database.path` + `database.name`; if `database.name` is omitted, it defaults to `test.db`.
- Verbosity can also be controlled by:
  - `MAHILDA_VERBOSE=1`
  - `MAHILDA_QUIET=1`

## Testing

```bash
# Run all tests
uv run pytest

# Run tests with verbose output
uv run pytest -v

# Run tests with coverage output (terminal + XML + HTML)
uv run pytest --cov=mahilda --cov-report=term-missing --cov-report=xml --cov-report=html
```

## Code Quality Tools

```bash
# Run ruff linter
uv run ruff check .

# Format code
uv run ruff format .

# Type-check
uv run pyright
```

### Pre-commit Hooks

```bash
uv sync
uv run pre-commit install
uv run pre-commit run --all-files
```

The pre-commit config runs `ruff check`, `ruff format`, and `pyright` on every commit.

## Outputs

- Rules JSON: `<results.output_dir>/<ALGORITHM>_<db_stem>/<ALGORITHM>_<db_stem>_results.json`
- Markdown report: `<results.output_dir>/report_<ALGORITHM>_<db_stem>.md`
- Batch summary: `<output>/summary.txt`
- Logs: `logs/global.log`, `logs/query_time.log`, `logs/query_results.log`

## Legacy and Research Artifacts

- Legacy multi-db runner is archived at `legacy/main_all.py`.
- Historical generated LaTeX outputs live in `research/generated/`.
- Historical generator script is preserved in `research/legacy/results_to_latex.py`.
