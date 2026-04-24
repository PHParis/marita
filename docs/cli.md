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

```bash
uv run mahilda benchmark --config configs/config.example.yaml --baseline AMIE3
uv run mahilda benchmark --config configs/config.example.yaml --baseline SPIDER
uv run mahilda benchmark --config configs/config.example.yaml --baseline POPPER
```

- Allowed baselines: `AMIE3`, `SPIDER`, `POPPER` (`ILP` is normalized to `POPPER`).
- Baseline selection comes from `--baseline` first, then `benchmark.baseline`, then `algorithm.name`.

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

- `start`: initializes local file-backed tracking setup.
- `ui`: launches MLflow web UI for local experiments.
