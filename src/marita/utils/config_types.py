from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

_DEFAULT_MEMORY_THRESHOLD_BYTES = 15 * 1024 * 1024 * 1024  # 15 GB
_DEFAULT_MONITOR_TIMEOUT_SECONDS = 3600  # 1 hour


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
    memory_gb: float
    java_heap_gb: int
    popper_command: str | None
    matilda_path: Path | None


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
                memory_threshold=int(monitor.get("memory_threshold", _DEFAULT_MEMORY_THRESHOLD_BYTES)),
                timeout=int(monitor.get("timeout", _DEFAULT_MONITOR_TIMEOUT_SECONDS)),
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
                memory_gb=float(benchmark.get("memory_gb", 15.0)),
                java_heap_gb=int(benchmark.get("java_heap_gb", 13)),
                popper_command=(str(benchmark["popper_command"]) if "popper_command" in benchmark else None),
                matilda_path=(Path(str(benchmark["matilda_path"])) if "matilda_path" in benchmark else None),
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
