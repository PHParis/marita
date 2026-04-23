# AGENTS.md

## Repo Reality Check
- Python deps are managed via `uv` (`pyproject.toml` + `uv.lock`). Preferred setup is `uv sync`.
- No CI workflow or pre-commit config exists.
- Run scripts with `uv run python3 ...` (or activate `.venv` and run `python3 ...`).

## Verified Entrypoints
- Quick smoke test (fastest known working path):
  1. `uv run python3 create_test_database.py`
  2. `uv run python3 run_test.py` (`-v` for verbose rule output, `-q` for quiet mode)
- Single database run: `uv run python3 src/main.py --config <config.yaml>`
- Batch run: `uv run python3 run_all_databases.py -d <db_dir> -o <results_dir> [--workers N --timeout SEC --max-databases K --start-from I]`
  - Important: default `-d` points to `/Volumes/backup_mac_1/data_mahilda_3`; override it on non-author machines.

## Actual Wiring (Not Obvious)
- Main orchestrator is `src/main.py` (`DatabaseProcessor`).
- Supported algorithm names there: `MAHILDA` (default), `AMIE3`, `SPIDER`, `ILP`, `POPPER` (`POPPER` maps to ILP class).
- `src/main_all.py` is currently not runnable (`python3 -m py_compile src/main_all.py` fails with `IndentationError` at line 52). Use `run_all_databases.py` for multi-DB processing.

## Config Gotchas
- `src/main.py` expects `database.path` + `database.name`; if `database.name` is missing, it silently defaults to `test.db`.
- `config.yaml` enables MLflow by default (`mlflow.use: true`) with file backend `file:./mlruns`.
- In current entrypoints (`src/main.py` and `run_all_databases.py`), `algorithm.parameters` from YAML are not passed into `MAHILDA(...)`; practically, `algorithm.name` is the config field that takes effect.
- Verbosity is controlled by env vars: `MAHILDA_VERBOSE=1` or `MAHILDA_QUIET=1` (helper scripts set these).

## Algorithm-Specific Runtime Dependencies
- `MAHILDA`: Python path only (no external jar invocation).
- `AMIE3`: requires Java; runs `src/algorithms/bins/amie3/amie-milestone-intKB.jar`.
- `SPIDER`: requires Java; runs `src/algorithms/bins/metanome/metanome-cli-1.2-SNAPSHOT.jar` + `SPIDER-1.2-SNAPSHOT.jar`.
- `ILP`/`POPPER`: depends on bundled Popper code plus `clingo` and `pyswip`/SWI-Prolog runtime.
  - First ILP run copies `src/algorithms/bins/popper` into `src/popper` (new working-tree files are expected).

## Outputs and Side Effects
- Single run writes rules to `<results.output_dir>/<ALGORITHM>_<db_stem>/<ALGORITHM>_<db_stem>_results.json`.
- Single run writes report to `<results.output_dir>/report_<ALGORITHM>_<db_stem>.md`.
- Batch run writes per-DB folders under `-o` and a summary at `<output>/summary.txt`.
- Logs are emitted to `logs/global.log`, `logs/query_time.log`, and `logs/query_results.log`.
