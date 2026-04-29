from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class MonitorConfig:
    memory_threshold: int
    timeout: int


@dataclass(frozen=True)
class DatabaseConfig:
    path: Path
    name: Path


@dataclass(frozen=True)
class LoggingConfig:
    log_dir: Path


@dataclass(frozen=True)
class ResultsConfig:
    output_dir: Path


@dataclass(frozen=True)
class AlgorithmConfig:
    name: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class BenchmarkConfig:
    baseline: str | None
    input_tsv: Path | None
    timeout: int


@dataclass(frozen=True)
class BatchConfig:
    workers: int
    timeout: int


@dataclass(frozen=True)
class MLflowConfig:
    use: bool
    tracking_uri: str | None
    experiment_name: str | None


@dataclass(frozen=True)
class AppConfig:
    monitor: MonitorConfig
    database: DatabaseConfig
    logging: LoggingConfig
    results: ResultsConfig
    algorithm: AlgorithmConfig
    benchmark: BenchmarkConfig
    batch: BatchConfig
    mlflow: MLflowConfig
    raw: dict[str, Any]

    @staticmethod
    def _as_mapping(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        return {}

    @staticmethod
    def _require_mapping(config: dict[str, Any], key: str) -> dict[str, Any]:
        value = config.get(key)
        if not isinstance(value, dict):
            raise ValueError(f"Missing required section: '{key}'.")
        return value

    @staticmethod
    def _require_non_empty_string(section: dict[str, Any], section_name: str, key: str) -> str:
        value = section.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Missing required key: '{section_name}.{key}'.")
        return value

    @classmethod
    def from_dict(cls, config: dict[str, Any]) -> AppConfig:
        required_database = cls._require_mapping(config, "database")
        required_logging = cls._require_mapping(config, "logging")
        required_results = cls._require_mapping(config, "results")
        required_algorithm = cls._require_mapping(config, "algorithm")

        monitor = cls._as_mapping(config.get("monitor"))
        database = cls._as_mapping(required_database)
        logging_cfg = cls._as_mapping(required_logging)
        results = cls._as_mapping(required_results)
        algorithm = cls._as_mapping(required_algorithm)
        benchmark = cls._as_mapping(config.get("benchmark"))
        batch = cls._as_mapping(config.get("batch"))
        mlflow = cls._as_mapping(config.get("mlflow"))
        algorithm_parameters = cls._as_mapping(algorithm.get("parameters"))

        return cls(
            monitor=MonitorConfig(
                memory_threshold=int(monitor.get("memory_threshold", 15 * 1024 * 1024 * 1024)),
                timeout=int(monitor.get("timeout", 3600)),
            ),
            database=DatabaseConfig(
                path=Path(cls._require_non_empty_string(database, "database", "path")),
                name=Path(cls._require_non_empty_string(database, "database", "name")),
            ),
            logging=LoggingConfig(
                log_dir=Path(cls._require_non_empty_string(logging_cfg, "logging", "log_dir")),
            ),
            results=ResultsConfig(
                output_dir=Path(cls._require_non_empty_string(results, "results", "output_dir")),
            ),
            algorithm=AlgorithmConfig(
                name=cls._require_non_empty_string(algorithm, "algorithm", "name").strip().upper(),
                parameters=algorithm_parameters,
            ),
            benchmark=BenchmarkConfig(
                baseline=(str(benchmark.get("baseline")).strip().upper() if benchmark.get("baseline") else None),
                input_tsv=(Path(str(benchmark["input_tsv"])) if "input_tsv" in benchmark else None),
                timeout=int(benchmark.get("timeout", 300)),
            ),
            batch=BatchConfig(
                workers=int(batch.get("workers", 3)),
                timeout=int(batch.get("timeout", 7200)),
            ),
            mlflow=MLflowConfig(
                use=bool(mlflow.get("use", False)),
                tracking_uri=(str(mlflow["tracking_uri"]) if "tracking_uri" in mlflow else None),
                experiment_name=(str(mlflow["experiment_name"]) if "experiment_name" in mlflow else None),
            ),
            raw=config,
        )
