from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path


@contextmanager
def mlflow_run_context(use_mlflow: bool, config: dict[str, Any]):
    if not use_mlflow:
        yield
        return

    import mlflow

    mlflow_tracking_uri = config.get("mlflow", {}).get("tracking_uri", "http://localhost:5000")
    mlflow_experiment = config.get("mlflow", {}).get("experiment_name", "Rule Discovery")
    mlflow.set_tracking_uri(mlflow_tracking_uri)
    mlflow.set_experiment(mlflow_experiment)
    mlflow.start_run()
    try:
        yield
    finally:
        mlflow.end_run()


def initialize_directories(results_dir: Path, log_dir: Path) -> None:
    results_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
