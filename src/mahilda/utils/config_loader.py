import logging
from pathlib import Path
from typing import Any

import yaml

from mahilda.utils.config_types import AppConfig

ALLOWED_ALGORITHMS = {"MAHILDA", "AMIE3", "SPIDER", "POPPER", "ILP"}
ALLOWED_BASELINES = {"AMIE3", "SPIDER", "POPPER", "ILP"}


def _is_mapping(value: Any) -> bool:
    return isinstance(value, dict)


def _add_error(errors: list[str], message: str) -> None:
    errors.append(message)


def _expect_mapping(
    config: dict[str, Any], key: str, errors: list[str], *, required: bool = True
) -> dict[str, Any] | None:
    value = config.get(key)
    if value is None:
        if required:
            _add_error(errors, f"Missing required section: '{key}'.")
        return None
    if not _is_mapping(value):
        _add_error(errors, f"Section '{key}' must be a mapping.")
        return None
    return value


def _expect_non_empty_string(
    section: dict[str, Any],
    section_name: str,
    key: str,
    errors: list[str],
    *,
    required: bool = True,
) -> str | None:
    value = section.get(key)
    if value is None:
        if required:
            _add_error(errors, f"Missing required key: '{section_name}.{key}'.")
        return None
    if not isinstance(value, str) or not value.strip():
        _add_error(errors, f"Key '{section_name}.{key}' must be a non-empty string.")
        return None
    return value


def _expect_positive_number(section: dict[str, Any], section_name: str, key: str, errors: list[str]) -> None:
    value = section.get(key)
    if value is None:
        return
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        _add_error(errors, f"Key '{section_name}.{key}' must be a positive number.")


def _expect_positive_int(section: dict[str, Any], section_name: str, key: str, errors: list[str]) -> None:
    value = section.get(key)
    if value is None:
        return
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        _add_error(errors, f"Key '{section_name}.{key}' must be a positive integer.")


def validate_config(config: dict[str, Any]) -> None:
    errors: list[str] = []

    database = _expect_mapping(config, "database", errors)
    if database is not None:
        _expect_non_empty_string(database, "database", "path", errors)
        _expect_non_empty_string(database, "database", "name", errors)

    logging_section = _expect_mapping(config, "logging", errors)
    if logging_section is not None:
        _expect_non_empty_string(logging_section, "logging", "log_dir", errors)

    results = _expect_mapping(config, "results", errors)
    if results is not None:
        _expect_non_empty_string(results, "results", "output_dir", errors)

    algorithm = _expect_mapping(config, "algorithm", errors)
    if algorithm is not None:
        algorithm_name = _expect_non_empty_string(algorithm, "algorithm", "name", errors)
        if algorithm_name is not None and algorithm_name.strip().upper() not in ALLOWED_ALGORITHMS:
            allowed = ", ".join(sorted(ALLOWED_ALGORITHMS))
            _add_error(errors, f"Key 'algorithm.name' must be one of: {allowed}.")
        parameters = algorithm.get("parameters")
        if parameters is not None:
            if not _is_mapping(parameters):
                _add_error(errors, "Section 'algorithm.parameters' must be a mapping when provided.")
            else:
                _expect_positive_number(parameters, "algorithm.parameters", "timeout", errors)

    benchmark = _expect_mapping(config, "benchmark", errors, required=False)
    if benchmark is not None:
        baseline_name = benchmark.get("baseline")
        if baseline_name is not None:
            if not isinstance(baseline_name, str) or not baseline_name.strip():
                _add_error(errors, "Key 'benchmark.baseline' must be a non-empty string.")
            elif baseline_name.strip().upper() not in ALLOWED_BASELINES:
                allowed = ", ".join(sorted(ALLOWED_BASELINES))
                _add_error(errors, f"Key 'benchmark.baseline' must be one of: {allowed}.")
        _expect_non_empty_string(benchmark, "benchmark", "input_tsv", errors, required=False)
        _expect_positive_number(benchmark, "benchmark", "timeout", errors)

    monitor = _expect_mapping(config, "monitor", errors, required=False)
    if monitor is not None:
        _expect_positive_int(monitor, "monitor", "memory_threshold", errors)
        _expect_positive_number(monitor, "monitor", "timeout", errors)

    batch = _expect_mapping(config, "batch", errors, required=False)
    if batch is not None:
        _expect_positive_int(batch, "batch", "workers", errors)
        _expect_positive_number(batch, "batch", "timeout", errors)

    mlflow = _expect_mapping(config, "mlflow", errors, required=False)
    if mlflow is not None:
        use_value = mlflow.get("use")
        if use_value is not None and not isinstance(use_value, bool):
            _add_error(errors, "Key 'mlflow.use' must be a boolean.")
        if use_value is True:
            _expect_non_empty_string(mlflow, "mlflow", "tracking_uri", errors)
            _expect_non_empty_string(mlflow, "mlflow", "experiment_name", errors)

    if errors:
        details = "\n".join(f"- {message}" for message in errors)
        raise ValueError(f"Invalid configuration:\n{details}")


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
    database_config = config.get("database")
    if isinstance(database_config, dict) and "path" in database_config:
        database_config["path"] = _resolve_path(config_dir, database_config["path"])

    logging_config = config.get("logging")
    if isinstance(logging_config, dict) and "log_dir" in logging_config:
        logging_config["log_dir"] = _resolve_path(config_dir, logging_config["log_dir"])

    results_config = config.get("results")
    if isinstance(results_config, dict) and "output_dir" in results_config:
        results_config["output_dir"] = _resolve_path(config_dir, results_config["output_dir"])

    mlflow_config = config.get("mlflow")
    if isinstance(mlflow_config, dict) and "tracking_uri" in mlflow_config:
        mlflow_config["tracking_uri"] = _resolve_file_uri(config_dir, mlflow_config["tracking_uri"])

    benchmark_config = config.get("benchmark")
    if isinstance(benchmark_config, dict) and "input_tsv" in benchmark_config:
        benchmark_config["input_tsv"] = _resolve_path(config_dir, benchmark_config["input_tsv"])

    return config


def load_config(config_path: str) -> dict:
    """Loads and normalises configuration from a YAML file."""
    logger = logging.getLogger(__name__)
    resolved_config_path = Path(config_path).expanduser().resolve()

    try:
        with resolved_config_path.open("r", encoding="utf-8") as file:
            loaded = yaml.safe_load(file) or {}
    except FileNotFoundError:
        raise ValueError(f"Configuration file not found: {resolved_config_path}") from None
    except yaml.YAMLError as exc:
        raise ValueError(f"Error parsing configuration file: {exc}") from exc

    if not isinstance(loaded, dict):
        raise ValueError("Configuration root must be a YAML mapping.")

    validated = _normalise_relative_paths(loaded, resolved_config_path.parent)
    validate_config(validated)
    logger.debug("Loaded and validated configuration from %s", resolved_config_path)
    return validated


def load_typed_config(config_path: str) -> AppConfig:
    """Load, validate, and convert configuration into typed dataclasses."""
    raw_config = load_config(config_path)
    return AppConfig.from_dict(raw_config)
