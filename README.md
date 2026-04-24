# MAHILDA

MAHILDA is a rule-discovery toolkit for relational databases centered on the `MAHILDA` algorithm, with optional competitor baselines for benchmarking.

## Quick Start

```bash
uv sync
uv run mahilda test-db
uv run mahilda smoke
```

## CLI Overview

- `uv run mahilda run --config configs/config.example.yaml` runs `MAHILDA` on one database.
- `uv run mahilda benchmark --config configs/config.example.yaml --baseline {AMIE3|SPIDER|POPPER}` runs one baseline.
- `uv run mahilda batch -c configs/config.example.yaml -d <db_dir> -o results/batch` processes many databases.
- `uv run mahilda mlflow start` and `uv run mahilda mlflow ui` start local MLflow helpers.

## Artifact Layout

- Logs: `logs/`
- Single-run and benchmark artifacts: `results/`
- Batch artifacts: `results/batch/`
- Local MLflow data: `mlruns/`

See `docs/config.md` for full path-resolution behavior and overrides.

## Documentation

- `docs/architecture.md`: package structure, active modules, and runtime data flow
- `docs/cli.md`: command behavior and examples
- `docs/config.md`: configuration schema, defaults, normalization, and precedence
- `docs/archive.md`: archive notes for `legacy/` and `research/`
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
- `POPPER`: `clingo` and SWI-Prolog (`swipl`)
