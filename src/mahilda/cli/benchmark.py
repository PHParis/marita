import argparse
import logging
from pathlib import Path

try:
    MLFLOW_AVAILABLE = True
    import mlflow  # noqa: F401 — checked at runtime via MLFLOW_AVAILABLE
except ImportError:
    MLFLOW_AVAILABLE = False

from mahilda.cli.processors import BaselineProcessor, normalise_baseline_name
from mahilda.cli.runtime import initialize_directories, mlflow_run_context, scoped_env_vars
from mahilda.utils.config_loader import load_typed_config
from mahilda.utils.logging_utils import configure_global_logger


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a MAHILDA benchmark baseline on a single database.")
    parser.add_argument(
        "-c",
        "--config",
        default="configs/config.example.yaml",
        help="Path to the configuration file (default: configs/config.example.yaml)",
    )
    parser.add_argument(
        "--baseline",
        help="Baseline to run (AMIE3, SPIDER, POPPER, MATILDA). Overrides config algorithm.name.",
    )
    parser.add_argument(
        "--input-tsv",
        help="Prebuilt TSV input for AMIE3; skips relational triple export when provided.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_arguments(argv)
    try:
        config = load_typed_config(args.config)
    except ValueError as exc:
        logging.getLogger(__name__).error("%s", exc)
        return 1

    baseline_from_config = config.benchmark.baseline or config.algorithm.name
    requested_baseline = args.baseline if args.baseline else baseline_from_config
    baseline_name = normalise_baseline_name(requested_baseline)
    if baseline_name not in {"AMIE3", "SPIDER", "POPPER", "MATILDA"}:
        logging.getLogger(__name__).error("Benchmark baseline must be one of: AMIE3, SPIDER, POPPER, MATILDA")
        return 1

    database_path = config.database.path
    database_name = config.database.name
    log_dir = config.logging.log_dir
    results_dir = config.results.output_dir
    input_tsv = Path(args.input_tsv) if args.input_tsv else config.benchmark.input_tsv

    initialize_directories(results_dir, log_dir)
    logger = configure_global_logger(str(log_dir))

    use_mlflow = False
    if MLFLOW_AVAILABLE:
        use_mlflow = config.mlflow.use
    elif config.mlflow.use:
        logger.warning("MLflow is not available. Proceeding without MLflow.")

    processor = BaselineProcessor(
        baseline_name=baseline_name,
        database_name=database_name,
        database_path=database_path,
        results_dir=results_dir,
        logger=logger,
        use_mlflow=use_mlflow,
        input_tsv=input_tsv,
        timeout=config.benchmark.timeout,
        memory_gb=config.benchmark.memory_gb,
        java_heap_gb=config.benchmark.java_heap_gb,
        popper_command=config.benchmark.popper_command,
        matilda_path=getattr(config.benchmark, "matilda_path", None),
    )

    try:
        with scoped_env_vars({"MAHILDA_LOG_DIR": str(log_dir)}), mlflow_run_context(use_mlflow, config.raw):
            processor.discover_rules()
            processor.clean_up()
    except Exception:
        logger.error("An error occurred during baseline benchmark execution.", exc_info=True)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
