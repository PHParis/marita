# Architecture

## Active Package Layout

- `src/mahilda/cli/`: command entrypoints (`run`, `benchmark`, `batch`, `smoke`, `test-db`, `mlflow` helpers)
- `src/mahilda/algorithms/`: `MAHILDA` implementation and shared algorithm base classes
- `src/mahilda/algorithms/mahilda_core/`: internal discovery pipeline primitives
- `src/mahilda/database/`: DB inspection and conversion helpers
- `src/mahilda/evaluation/baselines/`: baseline adapters (`AMIE3`, `SPIDER`, `POPPER`)
- `src/mahilda/utils/`: config loading, logging setup, subprocess wrapper, monitor, and rule serialization

## Entrypoint Wiring

- Top-level CLI: `src/mahilda/cli/main.py`
- Single run orchestrator: `src/mahilda/cli/run.py` (`DatabaseProcessor`)
- Baseline runner: `src/mahilda/cli/benchmark.py` (`BaselineProcessor`)
- Batch runner: `src/mahilda/cli/batch.py` (parallel fan-out over `.db` files)

`mahilda run` is restricted to `MAHILDA`; baseline execution is routed through `mahilda benchmark`.

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
- `results/`: single-run and benchmark artifacts
  - `results/<ALGORITHM>_<db_stem>/<ALGORITHM>_<db_stem>_results.json`
  - `results/report_<ALGORITHM>_<db_stem>.md`
- `results/batch/`: batch outputs and `summary.txt`
- `mlruns/`: local MLflow tracking backend

## Scope Boundaries

- Vendored baseline assets: `src/mahilda/evaluation/third_party/` (kept as external code, not first-party cleanup targets)
- Archived historical code: `legacy/` and `research/`
