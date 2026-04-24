# MAHILDA Reorg Follow-Up Implementation Plan

## Recommendations

- Save this plan at `docs/implementation-plan.md` and keep a short link to it from `README.md`.
- Keep `src/mahilda/evaluation/third_party/**`, `legacy/**`, and `research/**` out of scope for refactoring unless a task explicitly says otherwise.
- Make `configs/config.example.yaml` runnable inside the repo with minimal side effects; recommended default: point it at repo-local test data and set `mlflow.use: false`.
- Make `batch` config-driven with CLI overrides; recommended behavior: error clearly if no database directory is provided via config or CLI.
- Prefer stdlib-based validation and typed config helpers first; do not add a heavy config dependency unless the existing shape becomes too hard to maintain.
- Expand Ruff and Pyright coverage gradually, starting with CLI and utils, then `algorithms/mahilda.py`, while continuing to exclude vendored code.
- Standardize all user-facing output in English and route non-interactive status and error reporting through logging instead of scattered `print()` calls.
- Preserve the current command split: `run` for `MAHILDA`, `benchmark` for baselines, `batch` for multi-database execution.

## Objectives

- Make the reorganized CLI portable and predictable on any machine.
- Remove duplicated runtime logic across commands.
- Ensure monitoring, logging, config loading, and output generation behave consistently.
- Increase confidence through validation, tests, linting, typing, and CI.
- Document the new structure and define what is active versus archived.

## Phase 1: Runtime Stabilization

- [x] Replace machine-specific defaults in `src/mahilda/cli/main.py`, `src/mahilda/cli/batch.py`, `src/mahilda/cli/test_data.py`, `README.md`, and `configs/config.example.yaml`; remove `/Volumes/backup_mac_1/...` defaults and make all documented commands runnable from a fresh checkout.
- [x] Make `configs/config.example.yaml` point at repo-local data that can actually exist, ideally `../test_data/test.db` after `uv run mahilda test-db`, and update `README.md` quick start so the documented path matches reality.
- [x] Extract shared runtime helpers from `src/mahilda/cli/run.py` and `src/mahilda/cli/benchmark.py` into a common module for config loading, directory creation, MLflow setup, logger setup, cleanup, and report/artifact handling.
- [x] Refactor `src/mahilda/cli/batch.py` to use the same shared runtime path as `run` and `benchmark` instead of maintaining a standalone execution model.
- [x] Make `src/mahilda/utils/logging_utils.py` idempotent by avoiding repeated root-handler attachment and by returning named loggers instead of mutating the global logger on every call.
- [x] Standardize command output across `src/mahilda/cli/run.py`, `src/mahilda/cli/benchmark.py`, `src/mahilda/cli/batch.py`, `src/mahilda/cli/smoke.py`, `src/mahilda/cli/test_data.py`, `src/mahilda/cli/mlflow_start.py`, and `src/mahilda/cli/mlflow_ui.py`; remove mixed English/French output and reduce ad hoc `print()` usage.
- [x] Wire `src/mahilda/utils/monitor.py` into actual cancellation by passing a stop predicate into `MAHILDA.discover_rules()` from `src/mahilda/cli/run.py`; ensure the monitor is stopped and joined on success, failure, and signal exit.
- [x] Replace the fragile shell-string parsing in `src/mahilda/utils/run_cmd.py` with a safer subprocess wrapper that uses structured argv input, explicit redirection, timeout handling, and logger-based error reporting.
- [x] Update `src/mahilda/evaluation/baselines/amie3.py`, `src/mahilda/evaluation/baselines/spider.py`, and `src/mahilda/evaluation/baselines/popper.py` to use the safer subprocess wrapper without changing vendored third-party internals.
- [x] Consolidate output and report generation so `run`, `benchmark`, and `batch` write artifacts through a shared, predictable API rather than duplicating path and serialization logic.
- [x] Normalize environment-variable handling for `MAHILDA_VERBOSE`, `MAHILDA_QUIET`, and `MAHILDA_LOG_DIR` so command chaining inside one Python process does not leak settings across invocations.
- [x] Keep `mahilda run` restricted to `MAHILDA` and `mahilda benchmark` restricted to baselines, but make the error messages and help text explicit and consistent.

## Phase 1 Exit Criteria

- [x] `uv run mahilda test-db` works from a fresh checkout without path edits.
- [x] `uv run mahilda smoke` works from a fresh checkout without path edits.
- [x] Repeated CLI invocation in one process does not duplicate log lines or handlers.
- [x] Monitor timeout and stop behavior affect discovery instead of only logging.
- [x] `batch`, `run`, and `benchmark` share the same setup conventions and artifact layout.

## Phase 2: Validation, Tests, and Tooling

- [x] Add explicit config validation in `src/mahilda/utils/config_loader.py` for required keys, allowed algorithm and baseline names, path fields, worker counts, timeouts, and MLflow configuration shape.
- [x] Introduce a typed config representation using stdlib typing or dataclasses so command code stops passing unvalidated `dict` values everywhere.
- [x] Add tests for config validation success and failure paths in `tests/`, including missing files, malformed YAML, invalid algorithms, invalid baselines, invalid worker counts, and invalid timeout values.
- [x] Add tests for CLI routing and argument behavior across `run`, `benchmark`, `batch`, `smoke`, and `test-db`.
- [x] Add tests for logger idempotence and repeated command execution so handler duplication is caught automatically.
- [x] Add tests for monitor cancellation wiring so timeout and stop predicates are exercised without requiring long-running real workloads.
- [x] Add tests for output and report path generation so all commands write to the expected locations and filenames.
- [x] Add tests for `MAHILDA` parameter normalization in `src/mahilda/algorithms/mahilda.py`, including legacy key aliases such as `max_table`, `max_vars`, `nb_occurrence`, and `recursivity`.
- [x] Add tests for baseline normalization in `src/mahilda/cli/benchmark.py`, including alias handling such as `ILP -> POPPER`.
- [x] Add optional integration tests for Java- and Prolog-dependent baselines behind pytest markers and skips so local fast tests stay fast while integration coverage is still possible.
- [x] Reduce Ruff exclusions in `pyproject.toml` by first bringing `src/mahilda/cli/batch.py`, `src/mahilda/utils/logging_utils.py`, `src/mahilda/utils/run_cmd.py`, `src/mahilda/utils/rules.py`, and `src/mahilda/algorithms/mahilda.py` under linting.
- [x] Expand Pyright coverage in `pyproject.toml` from the current narrow include list to active first-party non-vendored code, while keeping frozen competitor adapters and explicitly listed legacy exceptions out of scope.
- [x] Add a CI workflow in `.github/workflows/` to run `uv sync`, `uv run ruff check .`, `uv run pyright`, and `uv run pytest` on pull requests and pushes.
- [x] Optionally add a lightweight local task runner such as `justfile` or `Makefile`; recommendation: only do this if the team wants shorter aliases for the existing `uv` commands.

## Phase 2 Exit Criteria

- [x] Invalid configs fail early with clear, user-facing messages.
- [x] Fast local tests cover CLI routing, config validation, monitor behavior, and output paths.
- [x] Ruff and Pyright cover the active first-party codebase except explicitly vendored/archive areas plus frozen competitor adapters and explicit tool-config exceptions.
- [x] CI runs on every PR and blocks regressions in lint, types, and tests.

## Phase 3: Documentation and Repository Hygiene

- [x] Simplify `.gitignore` so it ignores generated directories and known artifacts instead of broadly ignoring valuable asset types like `*.json`, `*.csv`, `*.db`, and `*.sql`.
- [x] Decide on one canonical artifact layout for generated logs, reports, metrics, and run outputs; reflect it consistently in config, code, README, and ignore rules.
- [x] Remove duplicate abstraction layers by consolidating `src/mahilda/algorithms/base_algorithm.py` and `src/mahilda/algorithms/rule_discovery_algorithm.py` if they remain functionally identical after Phase 1 and Phase 2.
- [x] Audit non-vendored first-party code for stray `print()` calls and replace them with logging or explicit CLI presentation only where appropriate.
- [x] Add `docs/architecture.md` describing the current package layout, active modules, command entrypoints, and data flow.
- [x] Add `docs/cli.md` describing `mahilda run`, `mahilda benchmark`, `mahilda batch`, `mahilda smoke`, `mahilda test-db`, and MLflow helper behavior.
- [x] Add `docs/config.md` describing config resolution, defaults, path normalization, MLflow settings, and CLI override behavior.
- [x] Update `README.md` so it stays high level and links to the more detailed docs instead of duplicating operational detail in too many places.
- [x] Add a short archive note for `legacy/` and `research/` so readers know these directories are preserved history rather than active implementation targets.
- [x] Keep vendored baseline assets documented but do not attempt to lint, type-check, or reformat them as part of normal first-party cleanup.

## Phase 3 Exit Criteria

- [x] The repo has a clear separation between active code, vendored code, archived code, and generated artifacts.
- [x] README quick start matches actual behavior.
- [x] The docs explain the reorganized layout without needing to inspect source files.
- [x] `.gitignore` no longer hides potentially important tracked assets by accident.

## Recommended Execution Order

1. Finish Phase 1 runtime and path fixes first.
2. Land shared runtime and logging changes before touching broader test and tool coverage.
3. Add config validation before expanding CLI and behavior tests.
4. Expand test coverage before tightening Ruff and Pyright scope.
5. Add CI after the first stable lint/type/test baseline exists.
6. Do repository hygiene and docs cleanup after behavior stabilizes.

## Verification Commands

- [x] Run `uv sync`.
- [x] Run `uv run pytest`.
- [x] Run `uv run ruff check .`.
- [x] Run `uv run ruff format .`.
- [x] Run `uv run pyright`.
- [x] Run `uv run mahilda test-db`.
- [x] Run `uv run mahilda smoke`.
- [x] Run `uv run mahilda run --config configs/config.example.yaml`.
- [x] Run `uv run mahilda benchmark --config configs/config.example.yaml --baseline AMIE3` only in environments with Java available.
- [x] Run `uv run mahilda benchmark --config configs/config.example.yaml --baseline SPIDER` only in environments with Java available.
- [ ] Run `uv run mahilda benchmark --config configs/config.example.yaml --baseline POPPER` only in environments with `clingo` and SWI-Prolog available.

## Explicit Non-Goals

- [x] Do not refactor or restyle vendored code in `src/mahilda/evaluation/third_party/**`.
- [x] Do not revive `legacy/main_all.py`; treat it as archive material unless a migration task explicitly extracts something from it.
- [x] Do not broaden compatibility layers unless there is a confirmed external consumer that still depends on them.
- [x] Do not add new framework dependencies for configuration or CLI formatting unless the existing stdlib approach proves insufficient.

## Definition of Done

- [x] No machine-specific defaults remain in active commands or docs.
- [x] `run`, `benchmark`, and `batch` share one execution model for setup, logging, config, and outputs.
- [x] Logging is deterministic and non-duplicative.
- [x] Config errors fail fast and clearly.
- [x] Core first-party code is covered by tests, linting, and type checking, with frozen competitor adapters excluded from refactoring/typing scope.
- [x] CI enforces the new baseline.
- [x] Docs accurately describe the reorganized repository.
