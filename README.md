# MAHILDA

MAHILDA is a rule-discovery project for relational databases with multiple algorithm backends:

- `MAHILDA` (default)
- `AMIE3` (Java jar)
- `SPIDER` (Java jars)
- `ILP` / `POPPER` (Popper + clingo + SWI-Prolog)

This repository uses `uv` + `pyproject.toml` for reproducible Python environments.

## Prerequisites

### Required for Python setup

- Python `>=3.10`
- `uv`

### Required by specific algorithms/tools

- Java (required for `AMIE3` and `SPIDER`)
- SWI-Prolog runtime (`swipl`) for `ILP` / `POPPER`
- `sqlite3` for SQL-to-SQLite conversion workflows
- `mysqldump` for `download_databases.py`

## Quick Start (uv)

```bash
uv sync
```

Run commands inside the managed environment:

```bash
uv run python3 create_test_database.py
uv run python3 run_test.py
```

Verbose mode:

```bash
uv run python3 run_test.py -v
```

Quiet mode:

```bash
uv run python3 run_test.py -q
```

## Main Entrypoints

### 1) Fast smoke test

```bash
uv run python3 create_test_database.py
uv run python3 run_test.py
```

### 2) Single database run

```bash
uv run python3 src/main.py --config config.yaml
```

### 3) Batch processing

```bash
uv run python3 run_all_databases.py -d <db_dir> -o <results_dir> [--workers N --timeout SEC --max-databases K --start-from I]
```

Note: the batch script default input directory is `/Volumes/backup_mac_1/data_mahilda_3`; override it on other machines.

## Testing

The project uses `pytest` for tests and `coverage.py` (via `pytest-cov`) for coverage reporting.

```bash
# Run all tests
uv run pytest

# Run tests with verbose output
uv run pytest -v

# Run tests with coverage output (terminal + XML + HTML)
uv run pytest --cov=src --cov-report=term-missing --cov-report=xml --cov-report=html
```

Default pytest configuration is defined in `pyproject.toml` and includes:

- strict marker/config validation
- coverage collection from `src`
- missing-line reporting in terminal
- generated reports at `coverage.xml` and `htmlcov/index.html`

## Code Quality Tools

The project uses `ruff` for linting and formatting, and `pyright` for static type checking.

### Linting & Formatting

```bash
# Run ruff linter (auto-fixes fixable issues)
uv run ruff check .

# Format code
uv run ruff format .

# Check without applying fixes
uv run ruff check . --diff
uv run ruff format . --diff
```

### Type Checking

```bash
# Run pyright type checker
uv run pyright
```

### Pre-commit Hooks

Install pre-commit hooks to run linting and formatting automatically before each commit:

```bash
# Install pre-commit (from dev deps)
uv sync

# Install the git hooks
uv run pre-commit install

# Run all hooks on all files
uv run pre-commit run --all-files
```

The pre-commit config runs `ruff check`, `ruff format`, and `pyright` on every commit.

## Configuration Notes

- `src/main.py` reads config from `--config` (`config.yaml` by default).
- If `database.name` is omitted, it defaults to `test.db`.
- `config.yaml` enables MLflow by default with local file backend: `file:./mlruns`.
- Verbosity is controlled by environment variables:
  - `MAHILDA_VERBOSE=1`
  - `MAHILDA_QUIET=1`

## Outputs

- Rules JSON: `<results.output_dir>/<ALGORITHM>_<db_stem>/<ALGORITHM>_<db_stem>_results.json`
- Markdown report: `<results.output_dir>/report_<ALGORITHM>_<db_stem>.md`
- Batch summary: `<output>/summary.txt`
- Logs: `logs/global.log`, `logs/query_time.log`, `logs/query_results.log`

## Known Limitation

`src/main_all.py` is currently not runnable (indentation error). Use `run_all_databases.py` for multi-database execution.
