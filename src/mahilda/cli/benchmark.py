import argparse
import logging
import shutil
from pathlib import Path
from typing import Any

try:
    import mlflow

    MLFLOW_AVAILABLE = True
except ImportError:
    MLFLOW_AVAILABLE = False

from mahilda.cli.artifacts import build_command_artifacts, write_markdown_report
from mahilda.cli.runtime import initialize_directories, mlflow_run_context, scoped_env_vars
from mahilda.database.alchemy_utility import AlchemyUtility
from mahilda.evaluation.baselines import Amie3, Popper, Spider
from mahilda.utils.config_loader import load_typed_config
from mahilda.utils.logging_utils import configure_global_logger
from mahilda.utils.rules import RuleIO


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
        help="Baseline to run (AMIE3, SPIDER, POPPER). Overrides config algorithm.name.",
    )
    parser.add_argument(
        "--input-tsv",
        help="Prebuilt TSV input for AMIE3; skips relational triple export when provided.",
    )
    return parser.parse_args(argv)


def normalise_baseline_name(name: str) -> str:
    cleaned = name.strip().upper()
    if cleaned == "ILP":
        return "POPPER"
    return cleaned


class BaselineProcessor:
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
    ):
        self.baseline_name = normalise_baseline_name(baseline_name)
        self.database_name = database_name
        self.database_path = database_path
        self.results_dir = results_dir
        self.logger = logger
        self.use_mlflow = use_mlflow
        self.input_tsv = input_tsv
        self.timeout = timeout

    def discover_rules(self) -> int:
        baseline_map = {
            "AMIE3": Amie3,
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
            if self.baseline_name == "AMIE3":
                discover_kwargs["timeout"] = self.timeout
            raw_rules = algo.discover_rules(**discover_kwargs)

            if isinstance(raw_rules, dict):
                rules = list(raw_rules.keys())
            else:
                rules = list(raw_rules)

            result_path = artifacts.result_json
            number_of_rules = RuleIO.save_rules_to_json(rules, str(result_path))

            top_rules = [rule for rule in rules if hasattr(rule, "accuracy") and hasattr(rule, "confidence")]
            top_rules_sorted = sorted(top_rules, key=lambda rule: -float(rule.accuracy))[:5]
            self.generate_report(number_of_rules, result_path, top_rules_sorted)

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

    def generate_report(self, number_of_rules: int, result_path: Path, top_rules: list[Any]) -> None:
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
        )

        self.logger.info("Generated report: %s", artifacts.report_md)

        if self.use_mlflow:
            mlflow.log_artifact(str(artifacts.report_md))


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
    if baseline_name not in {"AMIE3", "SPIDER", "POPPER"}:
        logging.getLogger(__name__).error("Benchmark baseline must be one of: AMIE3, SPIDER, POPPER")
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
