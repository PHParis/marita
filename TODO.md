# TODO List

Items derived from the codebase inspection, ordered by priority and dependency.

---

## [Done][P0] Remove Dead Code: `structure_analysis.py`

**File:** `src/mahilda/utils/structure_analysis.py` (246 lines)

**Problem:** This module is never imported by any active code path. The only import is in `legacy/main_all.py` which is archived. The file is excluded from ruff and pyright scope.

**Action:** Delete the file. The `log_structure_analysis()` and `save_pattern_2_2_rules()` functions were likely extracted for reuse but never wired into the batch pipeline.

**Steps:**
1. Verify no active imports (grep for `structure_analysis` in `src/mahilda/` — confirmed only in `legacy/`)
2. Remove `src/mahilda/utils/structure_analysis.py`
3. Remove the exclusion from `pyproject.toml` ruff scope: `src/mahilda/utils/structure_analysis.py`
4. Run `uv run ruff check . && uv run pyright && uv run pytest` to verify

---

## [Done][P0] Fix `mlflow start` — Currently a No-Op

**File:** `src/mahilda/cli/mlflow_start.py`

**Problem:** The `mahilda mlflow start` command doesn't start an MLflow tracking server. It prints instructions and enters a `while True: sleep(1)` loop. The docstring says "Uses a built-in server without gunicorn workers" but no server process is ever launched.

**Action:** Replace the sleep loop with an actual MLflow tracking server invocation using `mlflow server`.

**Steps:**
1. Replace the `while True: time.sleep(1)` block with `subprocess.run(["mlflow", "server", ...])`
2. Pass the same `--backend-store-uri` and artifact root config that's already computed
3. Keep the informative print statements but move them before the server launch
4. Add a Ctrl+C handler to gracefully shut down the server
5. Update the CLI help text to reflect actual behavior

---

## [Done][P1] Fix Config Defaults Masking Validation Errors

**File:** `src/mahilda/utils/config_types.py`

**Problem:** `AppConfig.from_dict()` applies generous defaults (e.g., `database.path` → `"test_data"`, `database.name` → `"test.db"`) *before* validation runs. This means a completely missing or malformed config section can silently pass validation with wrong defaults, leading to confusing runtime behavior.

**Action:** Validate required fields *before* applying defaults, or make defaults explicit and documented.

**Steps:**
1. In `AppConfig.from_dict()`, check for missing required sections first
2. Raise a clear `ValueError` if `database.path`, `database.name`, `logging.log_dir`, or `results.output_dir` are missing from the raw config
3. Only apply defaults for truly optional fields (monitor thresholds, batch workers, mlflow settings)
4. Add a test case in `tests/test_config_loader.py` that verifies missing required sections raise an error

---

## [Done][P1] Make MLflow UI Port Configurable

**File:** `src/mahilda/cli/mlflow_ui.py`

**Problem:** Port `5000` is hardcoded. If another process uses that port, the command fails silently (`subprocess.run` with `check=False`).

**Action:** Accept a `--port` CLI argument with a sensible default.

**Steps:**
1. Add `--port` argument to `mlflow_ui.main()`'s argparse parser (default 5000)
2. Pass the port to the `mlflow ui` subprocess command
3. Optionally read from config if present

---

## [P2] Clean Up `clean_up()` Hardcoded Temp Directories

**Files:** `src/mahilda/cli/run.py:200-210`, `src/mahilda/cli/benchmark.py:109-118`

**Problem:** `clean_up()` unconditionally tries to remove `prolog_tmp`, `SPIDER_temp`, and `popper` directories under the database path. When running MAHILDA-only (not baselines), these directories don't exist and produce noisy "directory not found" log messages despite the `directory.exists()` check — actually the check prevents removal but the log message is only emitted on success. The real issue is these dirs are baseline-specific but always attempted.

**Action:** Make the temp directory list algorithm-aware.

**Steps:**
1. In `DatabaseProcessor.__init__()`, store the algorithm name
2. In `clean_up()`, only include baseline-specific dirs when a baseline algorithm is used
3. Alternatively, extract the list into a class attribute that subclasses/variants can override
4. Same change in `BaselineProcessor.clean_up()` — keep as-is since it's always baseline

---

## [P2] Improve Signal Handler Portability in Batch Worker

**File:** `src/mahilda/cli/batch.py:146-164`

**Problem:** The timeout mechanism uses `signal.SIGALRM`, which is Unix-only. The `hasattr(signal, "SIGALRM")` check prevents crashes on Windows, but the timeout simply won't work there. Additionally, since `run_database()` runs in a child process via `ProcessPoolExecutor`, signal semantics differ from the main process.

**Action:** Add a Windows-compatible timeout fallback using `threading.Timer` or `concurrent.futures.TimeoutError`.

**Steps:**
1. In `run_database()`, detect platform and choose timeout strategy:
   - Unix: keep `signal.SIGALRM` approach
   - Windows: use `threading.Timer` to raise `TimeoutError` after N seconds
2. Wrap the `processor.discover_rules()` call in a try/except that catches both `TimeoutError` and `concurrent.futures.TimeoutError`
3. Add a test (or skip marker) for the Windows code path

---

## [P3] Refactor Bare `pass` in `_coerce_positive_int`

**File:** `src/mahilda/algorithms/mahilda.py:150`

**Problem:** The bare `pass` in the except block is functionally correct but slightly less clear than an explicit `return fallback`.

**Action:** Replace `pass` + fallthrough with explicit `return fallback`.

**Steps:**
1. Change:
   ```python
   except (TypeError, ValueError):
       pass
   return fallback
   ```
   To:
   ```python
   except (TypeError, ValueError):
       return fallback
   ```
2. Run `uv run ruff check . && uv run pyright` to verify no regressions

---

## [P3] Document Shell Script FIXMEs

**File:** `src/mahilda/database/convert_sql_sqlite3.sh:5,183,223`

**Problem:** Three FIXME comments in a vendored/third-party awk script that converts MySQL dumps to SQLite. These are informational and unlikely to be acted on since the script works for the project's use cases.

**Action:** Add a comment at the top of the file noting these are known limitations of the upstream script, and the project has accepted them. No code changes needed.

**Steps:**
1. Add a header comment block after the author lines noting: "Known limitations (upstream FIXMEs): empty input handling, ON UPDATE CURRENT_TIMESTAMP handling, CONSTRAINT foreign key handling."
2. No further action required unless a conversion failure surfaces in production.
