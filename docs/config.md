# Configuration

## Resolution Rules

- Config files are YAML mappings.
- CLI commands default to `configs/config.example.yaml` (except smoke uses `configs/config.test.yaml`).
- Relative paths are resolved against the config file directory.
- Validation runs before command execution; invalid configs fail with clear errors.

## Core Sections

## `database`

- `path` (required): directory containing DB files
- `name` (required): database filename for single-run and benchmark commands

## `logging`

- `log_dir` (required): log output directory

## `results`

- `output_dir` (required): base results directory for `run` and `benchmark`

## `algorithm`

- `name` (required): one of `MAHILDA`, `AMIE3`, `SPIDER`, `POPPER`, `ILP`
- `parameters` (optional mapping): algorithm-specific parameters

## `benchmark`

- `baseline` (optional): one of `AMIE3`, `SPIDER`, `POPPER`, `ILP`
- `input_tsv` (optional): prebuilt TSV path for AMIE3 direct graph input
- `timeout` (optional positive number): AMIE3 subprocess timeout in seconds, default `300`

## `batch`

- `workers` (optional positive int): parallel workers, default `3`
- `timeout` (optional positive number): per-db timeout in seconds, default `7200`

## `monitor`

- `memory_threshold` (optional positive int)
- `timeout` (optional positive number)

## `mlflow`

- `use` (optional bool)
- `tracking_uri` (required when `use: true`)
- `experiment_name` (required when `use: true`)

`file:` URIs are normalized relative to the config directory.

## CLI Override Precedence

Command-line flags override config values when provided:

- `benchmark --baseline` overrides `benchmark.baseline` / `algorithm.name`
- `benchmark --input-tsv` overrides `benchmark.input_tsv`
- `batch --directory` overrides `database.path`
- `batch --workers` overrides `batch.workers`
- `batch --timeout` overrides `batch.timeout`
- `batch --output` sets batch output root (default `results/batch`)

## Canonical Local Layout

- Logs: `logs/`
- Single-run and benchmark outputs: `results/`
- Batch outputs: `results/batch/`
- MLflow tracking: `mlruns/`
