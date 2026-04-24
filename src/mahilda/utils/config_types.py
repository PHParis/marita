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

    @classmethod
    def from_dict(cls, config: dict[str, Any]) -> AppConfig:
        monitor = cls._as_mapping(config.get("monitor"))
        database = cls._as_mapping(config.get("database"))
        logging_cfg = cls._as_mapping(config.get("logging"))
        results = cls._as_mapping(config.get("results"))
        algorithm = cls._as_mapping(config.get("algorithm"))
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
                path=Path(str(database.get("path", "test_data"))),
                name=Path(str(database.get("name", "test.db"))),
            ),
            logging=LoggingConfig(
                log_dir=Path(str(logging_cfg.get("log_dir", "logs"))),
            ),
            results=ResultsConfig(
                output_dir=Path(str(results.get("output_dir", "results"))),
            ),
            algorithm=AlgorithmConfig(
                name=str(algorithm.get("name", "MAHILDA")).strip().upper(),
                parameters=algorithm_parameters,
            ),
            benchmark=BenchmarkConfig(
                baseline=(str(benchmark.get("baseline")).strip().upper() if benchmark.get("baseline") else None),
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
