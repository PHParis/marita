import argparse
import datetime
import logging
import os
import shutil
from pathlib import Path
from typing import Any

try:
    import mlflow

    MLFLOW_AVAILABLE = True
except ImportError:
    MLFLOW_AVAILABLE = False

from mahilda.cli.runtime import initialize_directories, mlflow_run_context
from mahilda.database.alchemy_utility import AlchemyUtility
from mahilda.evaluation.baselines import Amie3, Popper, Spider
from mahilda.utils.config_loader import load_config
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
    ):
        self.baseline_name = normalise_baseline_name(baseline_name)
        self.database_name = database_name
        self.database_path = database_path
        self.results_dir = results_dir
        self.logger = logger
        self.use_mlflow = use_mlflow

    def discover_rules(self) -> int:
        baseline_map = {
            "AMIE3": Amie3,
            "SPIDER": Spider,
            "POPPER": Popper,
        }
        selected_baseline = baseline_map.get(self.baseline_name)
        if selected_baseline is None:
            raise ValueError(f"Unsupported baseline: {self.baseline_name}")

        unique_results_dir = self.results_dir / f"{self.baseline_name}_{self.database_name.stem}"
        unique_results_dir.mkdir(parents=True, exist_ok=True)

        db_file_path = self.database_path / self.database_name
        db_uri = f"sqlite:///{db_file_path}"
        self.logger.info("Using database URI: %s", db_uri)

        with AlchemyUtility(
            db_uri,
            database_path=str(self.database_path),
            create_index=False,
            create_csv=True,
            create_tsv=True,
        ) as db_util:
            algo = selected_baseline(db_util)
            raw_rules = algo.discover_rules(results_dir=str(unique_results_dir))

            if isinstance(raw_rules, dict):
                rules = list(raw_rules.keys())
            else:
                rules = list(raw_rules)

            json_file_name = f"{self.baseline_name}_{self.database_name.stem}_results.json"
            result_path = unique_results_dir / json_file_name
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
        report_content = f"""
# Baseline Run Report

**Date:** {datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
**Baseline:** {self.baseline_name}
**Database:** {self.database_name.name}
**Number of Rules Discovered:** {number_of_rules}
**Results Path:** {result_path}

## Top 5 Best Rules
Below are the top-5 best rules discovered based on their scores:

| Rank | Rule Description | Support | Confidence |
|------|------------------|---------|------------|
"""

        for idx, rule in enumerate(top_rules, start=1):
            rule_desc = rule.display.replace("\n", " ").replace("|", "\\|")
            report_content += f"| {idx} | {rule_desc} | {rule.accuracy:.3f} | {rule.confidence:.3f} |\n"

        report_file_name = f"report_{self.baseline_name}_{self.database_name.stem}.md"
        report_path = self.results_dir / report_file_name

        with report_path.open("w") as report_file:
            report_file.write(report_content)

        self.logger.info("Generated report: %s", report_path)

        if self.use_mlflow:
            mlflow.log_artifact(str(report_path))


def main(argv: list[str] | None = None) -> int:
    args = parse_arguments(argv)
    config = load_config(args.config)

    if not config:
        return 1

    baseline_from_config = str(config.get("algorithm", {}).get("name", "")).strip()
    requested_baseline = args.baseline if args.baseline else baseline_from_config
    baseline_name = normalise_baseline_name(requested_baseline)
    if baseline_name not in {"AMIE3", "SPIDER", "POPPER"}:
        print("Benchmark baseline must be one of: AMIE3, SPIDER, POPPER")
        return 1

    database_path = Path(config.get("database", {}).get("path", "test_data"))
    database_name = Path(config.get("database", {}).get("name", "test.db"))
    log_dir = Path(config.get("logging", {}).get("log_dir", "logs/"))
    results_dir = Path(config.get("results", {}).get("output_dir", "results/"))

    initialize_directories(results_dir, log_dir)
    previous_log_dir = os.environ.get("MAHILDA_LOG_DIR")
    os.environ["MAHILDA_LOG_DIR"] = str(log_dir)

    logger = configure_global_logger(str(log_dir))

    use_mlflow = False
    if MLFLOW_AVAILABLE:
        use_mlflow = config.get("mlflow", {}).get("use", False)
    elif config.get("mlflow", {}).get("use", False):
        logger.warning("MLflow is not available. Proceeding without MLflow.")

    processor = BaselineProcessor(
        baseline_name=baseline_name,
        database_name=database_name,
        database_path=database_path,
        results_dir=results_dir,
        logger=logger,
        use_mlflow=use_mlflow,
    )

    try:
        with mlflow_run_context(use_mlflow, config):
            processor.discover_rules()
            processor.clean_up()
    except Exception:
        logger.error("An error occurred during baseline benchmark execution.", exc_info=True)
        return 1
    finally:
        if previous_log_dir is None:
            os.environ.pop("MAHILDA_LOG_DIR", None)
        else:
            os.environ["MAHILDA_LOG_DIR"] = previous_log_dir

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
