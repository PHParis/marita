import argparse
import datetime
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
from mahilda.cli.runtime import initialize_directories, mlflow_run_context
from mahilda.database.alchemy_utility import AlchemyUtility
from mahilda.utils.config_loader import load_config
from mahilda.utils.logging_utils import configure_global_logger
from mahilda.utils.monitor import ResourceMonitor
from mahilda.utils.rules import RuleIO

try:
    from colorama import Fore as ColorFore
    from colorama import Style as ColorStyle
    from colorama import init

    init(autoreset=True)
    COLORS_AVAILABLE = True
    Fore: Any = ColorFore
    Style: Any = ColorStyle
except ImportError:
    COLORS_AVAILABLE = False

    class _FallbackFore:
        GREEN = YELLOW = BLUE = CYAN = RED = MAGENTA = WHITE = RESET = ""

    class _FallbackStyle:
        BRIGHT = DIM = NORMAL = RESET_ALL = ""

    Fore = _FallbackFore()
    Style = _FallbackStyle()


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
        if algorithm_name.upper() != "MAHILDA":
            msg = (
                "`mahilda run` only supports the MAHILDA algorithm. "
                "Use `mahilda benchmark --baseline ...` for competitor baselines."
            )
            raise ValueError(msg)
        self.algorithm_name = "MAHILDA"
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

        unique_results_dir = self.results_dir / f"{self.algorithm_name}_{self.database_name.stem}"
        unique_results_dir.mkdir(parents=True, exist_ok=True)

        db_file_path = self.database_path / self.database_name
        db_uri = f"sqlite:///{db_file_path}"
        try:
            if not quiet:
                self.logger.info(f"Using database URI: {db_uri}")
            start = time.time()

            # Disable CSV/TSV generation for MAHILDA (not needed, saves time and disk space)
            is_mahilda = self.algorithm_name.upper() == "MAHILDA"
            with AlchemyUtility(
                db_uri,
                database_path=str(self.database_path),
                create_index=False,
                create_csv=not is_mahilda,
                create_tsv=not is_mahilda,
            ) as db_util:
                if selected_algorithm is MAHILDA:
                    algo = selected_algorithm(db_util, config=self.config)
                else:
                    algo = selected_algorithm(db_util)
                rules = []

                if not quiet:
                    self.logger.debug("Starting rule discovery...")
                    # DEBUG: log settings for MAHILDA
                    if self.algorithm_name.upper() == "MAHILDA":
                        self.logger.info("MAHILDA settings: nb_occurrence=3, max_table=3, max_vars=6")

                for rule_count, rule in enumerate(
                    algo.discover_rules(results_dir=str(unique_results_dir), should_stop=self.should_stop),
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

                json_file_name = f"{self.algorithm_name}_{self.database_name.stem}_results.json"
                result_path = unique_results_dir / json_file_name

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
        temp_dirs = temp_dirs or [
            self.database_path / "prolog_tmp",
            self.database_path / "SPIDER_temp",
            self.database_path / "popper",
        ]
        for directory in temp_dirs:
            if directory.exists() and directory.is_dir():
                shutil.rmtree(directory)
                self.logger.info(f"Cleaned up temporary directory: {directory}")

    def generate_report(
        self, number_of_rules: int, result_path: Path, top_rules: list[Any], execution_time: float | None = None
    ) -> None:
        """Generates a report of the run."""
        # Format execution time
        time_str = "N/A"
        if execution_time is not None:
            if execution_time < 1.0:
                time_str = f"{execution_time * 1000:.2f} ms"
            elif execution_time < 60:
                time_str = f"{execution_time:.3f} seconds"
            elif execution_time < 3600:
                minutes = int(execution_time // 60)
                seconds = execution_time % 60
                time_str = f"{minutes}m {seconds:.1f}s"
            else:
                hours = int(execution_time // 3600)
                minutes = int((execution_time % 3600) // 60)
                time_str = f"{hours}h {minutes}m"

        report_content = f"""
# Run Report

**Date:** {datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
**Algorithm:** {self.algorithm_name}
**Database:** {self.database_name.name}
**Number of Rules Discovered:** {number_of_rules}
**Execution Time:** {time_str}
**Results Path:** {result_path}

## Summary
- **Algorithm:** {self.algorithm_name}
- **Database:** {self.database_name.name}
- **Number of Rules Discovered:** {number_of_rules}
- **Execution Time:** {time_str}
- **Results Path:** {result_path}

## Top 5 Best Rules
Below are the top-5 best rules discovered based on their scores:

| Rank | Rule Description | Support  | Confidence |
|------|------------------|----------| -----------|
"""

        # Add top-5 rules to the report
        for idx, rule in enumerate(top_rules, start=1):
            rule_desc = rule.display.replace("\n", " ").replace("|", "\\|")  # Escape pipes for markdown tables
            report_content += f"| {idx} | {rule_desc} | {rule.accuracy:.3f} | {rule.confidence:.3f} |\n"

        report_content += """

## Details
The rule discovery process was completed successfully. The discovered rules have been saved to the specified results path.

    """

        report_file_name = f"report_{self.algorithm_name}_{self.database_name.stem}.md"
        report_path = self.results_dir / report_file_name

        with report_path.open("w") as report_file:
            report_file.write(report_content)

        self.logger.info(f"Generated report: {report_path}")

        # Save execution time metrics to JSON file
        if execution_time is not None:
            import json

            time_metrics_file = (
                self.results_dir
                / f"MAHILDA_{self.database_name.stem}"
                / f"execution_time_{self.database_name.stem}.json"
            )
            time_metrics = {
                "database": self.database_name.stem,
                "execution_time_seconds": execution_time,
                "execution_time_ms": execution_time * 1000,
                "timestamp": datetime.datetime.now().isoformat(),
                "algorithm": self.algorithm_name,
                "rules_count": number_of_rules,
            }

            try:
                with open(time_metrics_file, "w") as f:
                    json.dump(time_metrics, f, indent=2)
                self.logger.info(f"Saved execution time metrics: {time_metrics_file}")
            except Exception as e:
                self.logger.warning(f"Failed to save time metrics: {e}")

        if self.use_mlflow:
            mlflow.log_artifact(str(report_path))
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
    config = load_config(args.config)

    if not config:
        return 1

    # Check verbose/quiet mode from environment
    quiet = os.environ.get("MAHILDA_QUIET") == "1"

    # Extract configuration with defaults
    threshold = config.get("monitor", {}).get("memory_threshold", 15 * 1024 * 1024 * 1024)  # 15GB
    timeout = config.get("monitor", {}).get("timeout", 3600)  # 1 hour
    database_path = Path(config.get("database", {}).get("path", "test_data"))
    database_name = Path(config.get("database", {}).get("name", "test.db"))
    log_dir = Path(config.get("logging", {}).get("log_dir", "logs/"))
    results_dir = Path(config.get("results", {}).get("output_dir", "results/"))
    algorithm_name = str(config.get("algorithm", {}).get("name", "MAHILDA")).upper()

    # Initialize directories
    initialize_directories(results_dir, log_dir)
    previous_log_dir = os.environ.get("MAHILDA_LOG_DIR")
    os.environ["MAHILDA_LOG_DIR"] = str(log_dir)

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
        use_mlflow = config.get("mlflow", {}).get("use", False)
        if use_mlflow and not quiet:
            logger.info("MLflow is enabled.")
    else:
        if config.get("mlflow", {}).get("use", False):
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
        config=config,
        should_stop=lambda: monitor.should_stop,
    )

    if not quiet:
        logger.info(f"{Fore.CYAN}{Style.BRIGHT}Starting rule discovery process.{Style.RESET_ALL}")

    try:
        with mlflow_run_context(use_mlflow, config):
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
        if previous_log_dir is None:
            os.environ.pop("MAHILDA_LOG_DIR", None)
        else:
            os.environ["MAHILDA_LOG_DIR"] = previous_log_dir
        if use_mlflow and MLFLOW_AVAILABLE:
            try:
                import mlflow

                if mlflow.active_run():
                    mlflow.end_run()
                    if not quiet:
                        logger.info("MLflow run ended.")
            except Exception:
                logger.warning("Could not end MLflow run cleanly.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
