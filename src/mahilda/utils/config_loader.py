import logging
from pathlib import Path
from typing import Any

import yaml


def _resolve_path(base_dir: Path, value: Any) -> Any:
    if not isinstance(value, str):
        return value

    candidate = Path(value).expanduser()
    if candidate.is_absolute():
        return str(candidate)
    return str((base_dir / candidate).resolve())


def _resolve_file_uri(base_dir: Path, tracking_uri: Any) -> Any:
    if not isinstance(tracking_uri, str):
        return tracking_uri

    if not tracking_uri.startswith("file:"):
        return tracking_uri

    if tracking_uri.startswith("file://"):
        return tracking_uri

    path_part = tracking_uri[len("file:") :]
    resolved = _resolve_path(base_dir, path_part)
    return f"file:{resolved}"


def _normalise_relative_paths(config: dict, config_dir: Path) -> dict:
    database_config = config.setdefault("database", {})
    if isinstance(database_config, dict) and "path" in database_config:
        database_config["path"] = _resolve_path(config_dir, database_config["path"])

    logging_config = config.setdefault("logging", {})
    if isinstance(logging_config, dict) and "log_dir" in logging_config:
        logging_config["log_dir"] = _resolve_path(config_dir, logging_config["log_dir"])

    results_config = config.setdefault("results", {})
    if isinstance(results_config, dict) and "output_dir" in results_config:
        results_config["output_dir"] = _resolve_path(config_dir, results_config["output_dir"])

    mlflow_config = config.setdefault("mlflow", {})
    if isinstance(mlflow_config, dict) and "tracking_uri" in mlflow_config:
        mlflow_config["tracking_uri"] = _resolve_file_uri(config_dir, mlflow_config["tracking_uri"])

    return config


def load_config(config_path: str) -> dict:
    """Loads and normalises configuration from a YAML file."""
    logger = logging.getLogger(__name__)
    resolved_config_path = Path(config_path).expanduser().resolve()

    try:
        with resolved_config_path.open("r", encoding="utf-8") as file:
            loaded = yaml.safe_load(file) or {}
    except FileNotFoundError:
        logger.error("Configuration file not found: %s", resolved_config_path)
        return {}
    except yaml.YAMLError as exc:
        raise ValueError(f"Error parsing configuration file: {exc}") from exc

    if not isinstance(loaded, dict):
        raise ValueError("Configuration root must be a YAML mapping.")

    return _normalise_relative_paths(loaded, resolved_config_path.parent)
