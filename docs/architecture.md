# Architecture

## Active Package Layout

- `src/marita/cli/`: command entrypoints (`run`, `benchmark`, `batch`, `smoke`, `test-db`, `mlflow` helpers)
- `src/marita/algorithms/`: `MARITA` implementation and shared algorithm base classes
- `src/marita/algorithms/marita_core/`: internal discovery pipeline primitives
- `src/marita/database/`: DB inspection and conversion helpers
- `src/marita/evaluation/baselines/`: baseline adapters (`AMIE3`, `SPIDER`, `POPPER`)
- `src/marita/utils/`: config loading, logging setup, subprocess wrapper, monitor, and rule serialization

## Entrypoint Wiring

- Top-level CLI: `src/marita/cli/main.py`
- Single run orchestrator: `src/marita/cli/run.py` (`DatabaseProcessor`)
- Baseline runner: `src/marita/cli/benchmark.py` (`BaselineProcessor`)
- Batch runner: `src/marita/cli/batch.py` (parallel fan-out over `.db` files)

`marita run` is restricted to `MARITA`; baseline execution is routed through `marita benchmark`.

## Runtime Data Flow

1. Parse CLI args and load typed config via `load_typed_config()`.
2. Resolve and create runtime directories (`results`, `logs`) through shared CLI runtime helpers.
3. Configure logging and optional MLflow context.
4. Open database through `AlchemyUtility`.
5. Execute selected algorithm/baseline adapter and stream rules.
6. Persist artifacts (JSON results + markdown reports + optional timing metadata).
7. Clean temporary runtime directories.

## Canonical Generated Artifacts

- `logs/`: command logs (`global.log`, `query_time.log`, `query_results.log`)
  - Batch workers write per-database logs under `<logging.log_dir>/<db_stem>/`
- `results/`: single-run and benchmark artifacts
  - `results/<ALGORITHM>_<db_stem>/<ALGORITHM>_<db_stem>_results.json`
  - `results/report_<ALGORITHM>_<db_stem>.md`
- `results/batch/`: batch outputs and `summary.txt`
- `mlruns/`: local MLflow tracking backend

## Scope Boundaries

- Vendored baseline assets: `src/marita/evaluation/third_party/` (kept as external code, not first-party cleanup targets)
- Archived historical code: `legacy/` and `research/`
