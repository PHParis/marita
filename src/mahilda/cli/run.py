import argparse
import logging
import os
import shutil
import signal
import sys
import threading
from collections.abc import Callable
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
from mahilda.cli.runtime import initialize_directories, mlflow_run_context, scoped_env_vars
from mahilda.database.alchemy_utility import AlchemyUtility
from mahilda.utils.config_loader import load_typed_config
from mahilda.utils.logging_utils import configure_global_logger
from mahilda.utils.monitor import ResourceMonitor
from mahilda.utils.rules import RuleIO


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    """
    Parses command-line arguments.
    """
    parser = argparse.ArgumentParser(description="Run rule discovery on a specified database with a given algorithm.")
    parser.add_argument(
        "-c",
        "--config",
        default="configs/config.example.yaml",
        help="Path to the configuration file (default: configs/config.example.yaml)",
    )
    return parser.parse_args(argv)


class DatabaseProcessor:
    """Handles database rule discovery and result logging."""

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

        # Check verbose/quiet mode
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

                    # Beautiful rule display
                    if verbose:
                        accuracy_color = (
                            Fore.GREEN if rule.accuracy >= 0.8 else Fore.YELLOW if rule.accuracy >= 0.5 else Fore.RED
                        )
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
                            f"  {Style.DIM}Accuracy:{Style.RESET_ALL} {accuracy_color}{rule.accuracy:.3f}{Style.RESET_ALL}  "
                            f"{Style.DIM}Confidence:{Style.RESET_ALL} {conf_color}{rule.confidence:.3f}{Style.RESET_ALL}"
                        )
                    elif not quiet and rule_count % 10 == 0:
                        # Show progress every 10 rules in normal mode
                        self.logger.info(f"{Fore.CYAN}Discovered {rule_count} rules so far...{Style.RESET_ALL}")

                result_path = artifacts.result_json

                if not quiet:
                    self.logger.debug(f"Saving rules to {result_path}")
                number_of_rules = RuleIO.save_rules_to_json(rules, str(result_path))

                if not quiet:
                    self.logger.info(f"{Fore.GREEN}{Style.BRIGHT}Discovered {number_of_rules} rules.{Style.RESET_ALL}")

                elapsed = time.time() - start

                top_rules = sorted(rules, key=lambda x: -x.accuracy)[:5]
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
        """Cleans up temporary directories synchronously."""
        if temp_dirs is None:
            temp_dirs = [self.database_path / "prolog_tmp"]
        for directory in temp_dirs:
            if directory.exists() and directory.is_dir():
                shutil.rmtree(directory)
                self.logger.info(f"Cleaned up temporary directory: {directory}")

    def generate_report(
        self, number_of_rules: int, result_path: Path, top_rules: list[Any], execution_time: float | None = None
    ) -> None:
        """Generates a report of the run."""
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


def setup_signal_handlers(monitor: ResourceMonitor, logger: logging.Logger) -> None:
    """
    Sets up signal handlers for graceful shutdown.
    """

    def handle_signal(signum, frame):
        logger.info(f"Received signal {signum}. Shutting down gracefully...")
        monitor.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)


def main(argv: list[str] | None = None) -> int:
    """Main entry point of the script."""
    args = parse_arguments(argv)
    try:
        config = load_typed_config(args.config)
    except ValueError as exc:
        logging.getLogger(__name__).error("%s", exc)
        return 1

    # Check verbose/quiet mode from environment
    quiet = os.environ.get("MAHILDA_QUIET") == "1"

    # Extract configuration with defaults
    threshold = config.monitor.memory_threshold  # 15GB
    timeout = config.monitor.timeout  # 1 hour
    database_path = config.database.path
    database_name = config.database.name
    log_dir = config.logging.log_dir
    results_dir = config.results.output_dir
    algorithm_name = config.algorithm.name

    # Initialize directories
    initialize_directories(results_dir, log_dir)
    # Configure logger
    logger = configure_global_logger(str(log_dir))

    if algorithm_name != "MAHILDA":
        logger.error(
            "`mahilda run` only supports MAHILDA. Use `mahilda benchmark --baseline %s` for baselines.",
            algorithm_name,
        )
        return 1

    # Determine MLflow usage
    use_mlflow = False
    if MLFLOW_AVAILABLE:
        use_mlflow = config.mlflow.use
        if use_mlflow and not quiet:
            logger.info("MLflow is enabled.")
    else:
        if config.mlflow.use:
            if not quiet:
                logger.warning("MLflow is not available. Proceeding without MLflow.")
            use_mlflow = False

    # Initialize Resource Monitor
    monitor = ResourceMonitor(threshold, timeout)  # Changed to positional arguments
    monitor_thread = threading.Thread(target=monitor.monitor, daemon=True)
    monitor_thread.start()
    if not quiet:
        logger.debug("Resource monitor started.")

    # Setup signal handlers for graceful shutdown
    setup_signal_handlers(monitor, logger)

    # Initialize DatabaseProcessor
    processor = DatabaseProcessor(
        algorithm_name=algorithm_name,
        database_name=database_name,
        database_path=database_path,
        results_dir=results_dir,
        logger=logger,
        use_mlflow=use_mlflow,
        config=config.raw,
        should_stop=lambda: monitor.should_stop,
    )

    if not quiet:
        logger.info(f"{Fore.CYAN}{Style.BRIGHT}Starting rule discovery process.{Style.RESET_ALL}")

    try:
        with scoped_env_vars({"MAHILDA_LOG_DIR": str(log_dir)}), mlflow_run_context(use_mlflow, config.raw):
            if use_mlflow and not quiet:
                logger.info("MLflow run started.")

            processor.discover_rules()

            processor.clean_up()

            if not quiet:
                logger.info(f"{Fore.GREEN}{Style.BRIGHT}Process completed successfully.{Style.RESET_ALL}")

    except Exception:
        logger.error("An error occurred during the rule discovery process.", exc_info=True)
        return 1
    finally:
        monitor.stop()
        monitor_thread.join(timeout=2)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
