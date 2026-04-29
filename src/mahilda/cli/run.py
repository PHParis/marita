import argparse
import logging
import os
import signal
import sys
import threading

try:
    import mlflow as _mlflow  # noqa: F401 — presence checked via MLFLOW_AVAILABLE

    MLFLOW_AVAILABLE = True
except ImportError:
    MLFLOW_AVAILABLE = False

from mahilda.cli._color import Fore, Style
from mahilda.cli.processors import DatabaseProcessor
from mahilda.cli.runtime import initialize_directories, mlflow_run_context, scoped_env_vars
from mahilda.utils.config_loader import load_typed_config
from mahilda.utils.logging_utils import configure_global_logger
from mahilda.utils.monitor import ResourceMonitor


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run rule discovery on a specified database with a given algorithm.")
    parser.add_argument(
        "-c",
        "--config",
        default="configs/config.example.yaml",
        help="Path to the configuration file (default: configs/config.example.yaml)",
    )
    return parser.parse_args(argv)


def setup_signal_handlers(monitor: ResourceMonitor, logger: logging.Logger) -> None:
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

    quiet = os.environ.get("MAHILDA_QUIET") == "1"

    threshold = config.monitor.memory_threshold
    timeout = config.monitor.timeout
    database_path = config.database.path
    database_name = config.database.name
    log_dir = config.logging.log_dir
    results_dir = config.results.output_dir
    algorithm_name = config.algorithm.name

    initialize_directories(results_dir, log_dir)
    logger = configure_global_logger(str(log_dir))

    if algorithm_name != "MAHILDA":
        logger.error(
            "`mahilda run` only supports MAHILDA. Use `mahilda benchmark --baseline %s` for baselines.",
            algorithm_name,
        )
        return 1

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

    monitor = ResourceMonitor(threshold, timeout)
    monitor_thread = threading.Thread(target=monitor.monitor, daemon=True)
    monitor_thread.start()
    if not quiet:
        logger.debug("Resource monitor started.")

    setup_signal_handlers(monitor, logger)

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
