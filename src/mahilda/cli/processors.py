"""Shared orchestration classes for CLI commands.

DatabaseProcessor and BaselineProcessor are used by run.py, benchmark.py,
and batch.py. They live here so commands don't depend on each other's internals.
"""

import logging
import os
import shutil
import signal
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import Any

try:
    import mlflow

    MLFLOW_AVAILABLE = True
except ImportError:
    MLFLOW_AVAILABLE = False

from mahilda.algorithms.mahilda import MAHILDA
from mahilda.cli._color import Fore, Style
from mahilda.cli.artifacts import build_command_artifacts, write_execution_time_metrics, write_markdown_report
from mahilda.database.alchemy_utility import AlchemyUtility
from mahilda.evaluation.baselines import Amie3, Matilda, Popper, Spider
from mahilda.utils.rule_io import RuleIO

BENCHMARK_TIMEOUT_EXIT_CODE = 124


class BenchmarkTimeoutError(TimeoutError):
    """Raised when a benchmark baseline exceeds its wall-clock limit."""


def normalise_baseline_name(name: str) -> str:
    cleaned = name.strip().upper()
    if cleaned == "ILP":
        return "POPPER"
    return cleaned


def _top_report_rules(rules: list[Any]) -> list[Any]:
    scored_rules: list[tuple[float, Any]] = []
    unscored_rules: list[Any] = []
    for rule in rules:
        score = getattr(rule, "support", getattr(rule, "accuracy", None))
        if score is None:
            unscored_rules.append(rule)
            continue
        try:
            scored_rules.append((float(score), rule))
        except (TypeError, ValueError):
            unscored_rules.append(rule)

    sorted_scored_rules = [rule for _, rule in sorted(scored_rules, key=lambda item: -item[0])]
    return (sorted_scored_rules + unscored_rules)[:5]


@contextmanager
def benchmark_timeout(seconds: int):
    if seconds <= 0 or not hasattr(signal, "SIGALRM") or threading.current_thread() is not threading.main_thread():
        yield
        return

    def timeout_handler(signum, frame):
        del signum, frame
        raise BenchmarkTimeoutError(f"Benchmark execution exceeded {seconds} seconds")

    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.setitimer(signal.ITIMER_REAL, 0)
    signal.signal(signal.SIGALRM, timeout_handler)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer[0] > 0:
            signal.setitimer(signal.ITIMER_REAL, *previous_timer)


class DatabaseProcessor:
    """Orchestrates single-database MAHILDA rule discovery."""

    def __init__(
        self,
        algorithm_name: str,
        database_name: Path,
        database_path: Path,
        results_dir: Path,
        logger: logging.Logger,
        use_mlflow: bool = False,
        config: dict | None = None,
        should_stop: Callable[[], bool] | None = None,
    ):
        normalized_algorithm = algorithm_name.upper()
        if normalized_algorithm != "MAHILDA":
            msg = (
                "`mahilda run` only supports the MAHILDA algorithm. "
                "Use `mahilda benchmark --baseline ...` for competitor baselines."
            )
            raise ValueError(msg)
        self.algorithm_name = normalized_algorithm
        self.database_name = database_name
        self.database_path = database_path
        self.results_dir = results_dir
        self.logger = logger
        self.use_mlflow = use_mlflow
        self.config = config or {}
        self.should_stop = should_stop

    def discover_rules(self) -> int:
        """Runs the rule discovery algorithm synchronously."""
        import time

        verbose = os.environ.get("MAHILDA_VERBOSE") == "1"
        quiet = os.environ.get("MAHILDA_QUIET") == "1"

        selected_algorithm = MAHILDA

        artifacts = build_command_artifacts(self.results_dir, self.algorithm_name, self.database_name)
        artifacts.run_dir.mkdir(parents=True, exist_ok=True)

        db_file_path = self.database_path / self.database_name
        db_uri = f"sqlite:///{db_file_path}"
        try:
            if not quiet:
                self.logger.info(f"Using database URI: {db_uri}")
            start = time.time()

            with AlchemyUtility(
                db_uri,
                database_path=str(self.database_path),
                create_index=False,
                create_csv=False,
                create_tsv=False,
            ) as db_util:
                algo = selected_algorithm(db_util, config=self.config)
                rules = []

                if not quiet:
                    self.logger.debug("Starting rule discovery...")

                for rule_count, rule in enumerate(
                    algo.discover_rules(results_dir=str(artifacts.run_dir), should_stop=self.should_stop),
                    start=1,
                ):
                    rules.append(rule)

                    if verbose:
                        score = getattr(rule, "support", getattr(rule, "accuracy", 0))
                        conf_color = (
                            Fore.GREEN
                            if rule.confidence >= 0.8
                            else Fore.YELLOW
                            if rule.confidence >= 0.5
                            else Fore.RED
                        )

                        self.logger.info(
                            f"{Fore.CYAN}Rule #{rule_count}:{Style.RESET_ALL} "
                            f"{Fore.WHITE}{rule.display}{Style.RESET_ALL}"
                        )
                        self.logger.info(
                            f"  {Style.DIM}Support:{Style.RESET_ALL} {Fore.GREEN}{score}{Style.RESET_ALL}  "
                            f"{Style.DIM}Confidence:{Style.RESET_ALL} {conf_color}{rule.confidence:.3f}{Style.RESET_ALL}"
                        )
                    elif not quiet and rule_count % 10 == 0:
                        self.logger.info(f"{Fore.CYAN}Discovered {rule_count} rules so far...{Style.RESET_ALL}")

                result_path = artifacts.result_json

                if not quiet:
                    self.logger.debug(f"Saving rules to {result_path}")
                number_of_rules = RuleIO.save_rules_to_json(rules, str(result_path))

                if not quiet:
                    self.logger.info(f"{Fore.GREEN}{Style.BRIGHT}Discovered {number_of_rules} rules.{Style.RESET_ALL}")

                elapsed = time.time() - start

                top_rules = _top_report_rules(rules)
                self.generate_report(number_of_rules, result_path, top_rules, elapsed)

                if self.use_mlflow:
                    mlflow.log_param("algorithm", self.algorithm_name)
                    mlflow.log_param("database", self.database_name.name)
                    mlflow.log_metric("number_of_rules", number_of_rules)
                    mlflow.log_metric("execution_time_seconds", elapsed)
                if not quiet:
                    self.logger.info(
                        f"{Fore.MAGENTA}{Style.BRIGHT}{self.algorithm_name} finished in {elapsed:.2f} seconds "
                        f"with {number_of_rules} rules.{Style.RESET_ALL}"
                    )

                if self.should_stop and self.should_stop():
                    raise RuntimeError("Rule discovery stopped because resource limits were reached.")
                return number_of_rules

        except Exception as e:
            self.logger.error(f"An error occurred during rule discovery: {e}", exc_info=True)
            if self.use_mlflow:
                mlflow.log_param("error", str(e))
            raise

    def clean_up(self, temp_dirs: list[Path] | None = None) -> None:
        """Cleans up temporary directories."""
        if temp_dirs is None:
            temp_dirs = [self.database_path / "prolog_tmp"]
        for directory in temp_dirs:
            if directory.exists() and directory.is_dir():
                shutil.rmtree(directory)
                self.logger.info(f"Cleaned up temporary directory: {directory}")

    def generate_report(
        self, number_of_rules: int, result_path: Path, top_rules: list[Any], execution_time: float | None = None
    ) -> None:
        """Generates a markdown report and execution-time metrics."""
        artifacts = build_command_artifacts(self.results_dir, self.algorithm_name, self.database_name)
        write_markdown_report(
            report_path=artifacts.report_md,
            report_title="Run Report",
            subject_label="Algorithm",
            subject_name=self.algorithm_name,
            database_name=self.database_name.name,
            number_of_rules=number_of_rules,
            result_path=result_path,
            top_rules=top_rules,
            execution_time=execution_time,
        )

        self.logger.info("Generated report: %s", artifacts.report_md)

        if execution_time is not None:
            try:
                write_execution_time_metrics(
                    metrics_path=artifacts.execution_time_json,
                    database_stem=self.database_name.stem,
                    execution_time=execution_time,
                    status="success",
                    rules_count=number_of_rules,
                    algorithm_name=self.algorithm_name,
                )
                self.logger.info("Saved execution time metrics: %s", artifacts.execution_time_json)
            except Exception as e:
                self.logger.warning("Failed to save time metrics: %s", e)

        if self.use_mlflow:
            mlflow.log_artifact(str(artifacts.report_md))
            self.logger.info("Logged report as MLflow artifact.")


class BaselineProcessor:
    """Orchestrates single-baseline benchmark execution."""

    def __init__(
        self,
        baseline_name: str,
        database_name: Path,
        database_path: Path,
        results_dir: Path,
        logger: logging.Logger,
        use_mlflow: bool = False,
        input_tsv: Path | None = None,
        timeout: int = 300,
        memory_gb: float = 15.0,
        java_heap_gb: int = 13,
        popper_command: str | None = None,
        matilda_path: Path | None = None,
    ):
        self.baseline_name = normalise_baseline_name(baseline_name)
        self.database_name = database_name
        self.database_path = database_path
        self.results_dir = results_dir
        self.logger = logger
        self.use_mlflow = use_mlflow
        self.input_tsv = input_tsv
        self.timeout = timeout
        self.memory_gb = memory_gb
        self.java_heap_gb = java_heap_gb
        self.popper_command = popper_command
        self.matilda_path = matilda_path

    def discover_rules(self) -> int:
        baseline_map = {
            "AMIE3": Amie3,
            "MATILDA": Matilda,
            "SPIDER": Spider,
            "POPPER": Popper,
        }
        selected_baseline = baseline_map.get(self.baseline_name)
        if selected_baseline is None:
            raise ValueError(f"Unsupported baseline: {self.baseline_name}")

        artifacts = build_command_artifacts(self.results_dir, self.baseline_name, self.database_name)
        artifacts.run_dir.mkdir(parents=True, exist_ok=True)

        db_file_path = self.database_path / self.database_name
        db_uri = f"sqlite:///{db_file_path}"
        self.logger.info("Using database URI: %s", db_uri)

        direct_amie3_tsv = self.baseline_name == "AMIE3" and self.input_tsv is not None
        with AlchemyUtility(
            db_uri,
            database_path=str(self.database_path),
            create_index=False,
            create_csv=not direct_amie3_tsv,
            create_tsv=not direct_amie3_tsv,
        ) as db_util:
            algo = selected_baseline(db_util)
            discover_kwargs: dict[str, Any] = {"results_dir": str(artifacts.run_dir)}
            if self.input_tsv is not None:
                discover_kwargs["input_tsv"] = self.input_tsv
            discover_kwargs["timeout"] = self.timeout
            discover_kwargs["memory_gb"] = self.memory_gb
            discover_kwargs["java_heap_gb"] = self.java_heap_gb
            if self.baseline_name == "POPPER":
                discover_kwargs["runtime_dir"] = str(artifacts.run_dir / "_runtime")
                if self.popper_command:
                    discover_kwargs["popper_command"] = self.popper_command
            if self.baseline_name == "MATILDA" and self.matilda_path is not None:
                discover_kwargs["matilda_path"] = self.matilda_path
            start = time.time()
            try:
                with benchmark_timeout(self.timeout):
                    raw_rules = algo.discover_rules(**discover_kwargs)
            except BenchmarkTimeoutError:
                elapsed = time.time() - start
                self.logger.error("Benchmark execution exceeded %s seconds.", self.timeout)
                write_execution_time_metrics(
                    metrics_path=artifacts.execution_time_json,
                    database_stem=self.database_name.stem,
                    execution_time=elapsed,
                    status="timeout",
                    rules_count=0,
                    algorithm_name=self.baseline_name,
                )
                raise

            if isinstance(raw_rules, dict):
                rules = list(raw_rules.keys())
            else:
                rules = list(raw_rules)

            result_path = artifacts.result_json
            number_of_rules = RuleIO.save_rules_to_json(rules, str(result_path))

            top_rules_sorted = _top_report_rules(rules)
            elapsed = time.time() - start
            self.generate_report(number_of_rules, result_path, top_rules_sorted, elapsed)

            if self.use_mlflow:
                mlflow.log_param("algorithm", self.baseline_name)
                mlflow.log_param("database", self.database_name.name)
                mlflow.log_metric("number_of_rules", number_of_rules)

            self.logger.info("Discovered %s rules with %s.", number_of_rules, self.baseline_name)
            return number_of_rules

    def clean_up(self, temp_dirs: list[Path] | None = None) -> None:
        temp_dirs = temp_dirs or [
            self.database_path / "prolog_tmp",
            self.database_path / "SPIDER_temp",
            self.database_path / "popper",
        ]
        for directory in temp_dirs:
            if directory.exists() and directory.is_dir():
                shutil.rmtree(directory)
                self.logger.info("Cleaned up temporary directory: %s", directory)

    def generate_report(
        self,
        number_of_rules: int,
        result_path: Path,
        top_rules: list[Any],
        execution_time: float | None = None,
    ) -> None:
        artifacts = build_command_artifacts(self.results_dir, self.baseline_name, self.database_name)
        write_markdown_report(
            report_path=artifacts.report_md,
            report_title="Baseline Run Report",
            subject_label="Baseline",
            subject_name=self.baseline_name,
            database_name=self.database_name.name,
            number_of_rules=number_of_rules,
            result_path=result_path,
            top_rules=top_rules,
            execution_time=execution_time,
        )

        self.logger.info("Generated report: %s", artifacts.report_md)

        if execution_time is not None:
            try:
                write_execution_time_metrics(
                    metrics_path=artifacts.execution_time_json,
                    database_stem=self.database_name.stem,
                    execution_time=execution_time,
                    status="success",
                    rules_count=number_of_rules,
                    algorithm_name=self.baseline_name,
                )
                self.logger.info("Saved execution time metrics: %s", artifacts.execution_time_json)
            except Exception as e:
                self.logger.warning("Failed to save time metrics: %s", e)

        if self.use_mlflow:
            mlflow.log_artifact(str(artifacts.report_md))
