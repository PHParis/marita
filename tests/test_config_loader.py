from pathlib import Path

import pytest

from mahilda.utils.config_loader import load_config, load_typed_config


def test_load_config_resolves_paths_relative_to_config_file(tmp_path: Path) -> None:
    config_dir = tmp_path / "configs"
    config_dir.mkdir(parents=True)
    config_path = config_dir / "config.yaml"

    config_path.write_text(
        "\n".join(
            [
                "database:",
                "  path: ../data",
                "  name: test.db",
                "logging:",
                "  log_dir: ../logs",
                "results:",
                "  output_dir: ../results",
                "algorithm:",
                "  name: MAHILDA",
                "mlflow:",
                "  tracking_uri: file:../mlruns",
            ]
        ),
        encoding="utf-8",
    )

    loaded = load_config(str(config_path))

    assert loaded["database"]["path"] == str((config_dir / "../data").resolve())
    assert loaded["logging"]["log_dir"] == str((config_dir / "../logs").resolve())
    assert loaded["results"]["output_dir"] == str((config_dir / "../results").resolve())
    assert loaded["mlflow"]["tracking_uri"] == f"file:{(config_dir / '../mlruns').resolve()}"


def test_load_config_rejects_invalid_algorithm_name(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "database:",
                "  path: ./data",
                "  name: test.db",
                "logging:",
                "  log_dir: ./logs",
                "results:",
                "  output_dir: ./results",
                "algorithm:",
                "  name: UNKNOWN",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="algorithm.name"):
        load_config(str(config_path))


def test_load_config_rejects_non_positive_batch_workers(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "database:",
                "  path: ./data",
                "  name: test.db",
                "logging:",
                "  log_dir: ./logs",
                "results:",
                "  output_dir: ./results",
                "algorithm:",
                "  name: MAHILDA",
                "batch:",
                "  workers: 0",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="batch.workers"):
        load_config(str(config_path))


def test_load_config_rejects_invalid_mlflow_shape_when_enabled(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "database:",
                "  path: ./data",
                "  name: test.db",
                "logging:",
                "  log_dir: ./logs",
                "results:",
                "  output_dir: ./results",
                "algorithm:",
                "  name: MAHILDA",
                "mlflow:",
                "  use: true",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="mlflow.tracking_uri"):
        load_config(str(config_path))


def test_load_config_rejects_missing_required_sections(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "database:",
                "  path: ./data",
                "  name: test.db",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Missing required section"):
        load_config(str(config_path))


def test_load_config_rejects_missing_file(tmp_path: Path) -> None:
    missing = tmp_path / "missing.yaml"
    with pytest.raises(ValueError, match="Configuration file not found"):
        load_config(str(missing))


def test_load_config_rejects_malformed_yaml(tmp_path: Path) -> None:
    config_path = tmp_path / "bad.yaml"
    config_path.write_text("database: [", encoding="utf-8")

    with pytest.raises(ValueError, match="Error parsing configuration file"):
        load_config(str(config_path))


def test_load_config_rejects_invalid_baseline_name(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "database:",
                "  path: ./data",
                "  name: test.db",
                "logging:",
                "  log_dir: ./logs",
                "results:",
                "  output_dir: ./results",
                "algorithm:",
                "  name: MAHILDA",
                "benchmark:",
                "  baseline: nope",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="benchmark.baseline"):
        load_config(str(config_path))


def test_load_config_rejects_invalid_timeout_values(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "database:",
                "  path: ./data",
                "  name: test.db",
                "logging:",
                "  log_dir: ./logs",
                "results:",
                "  output_dir: ./results",
                "algorithm:",
                "  name: MAHILDA",
                "  parameters:",
                "    timeout: 0",
                "monitor:",
                "  timeout: -1",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="timeout"):
        load_config(str(config_path))


def test_load_typed_config_builds_dataclass_view(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "database:",
                "  path: ./data",
                "  name: my.db",
                "logging:",
                "  log_dir: ./logs",
                "results:",
                "  output_dir: ./results",
                "algorithm:",
                "  name: mahilda",
                "batch:",
                "  workers: 5",
                "  timeout: 88",
            ]
        ),
        encoding="utf-8",
    )

    config = load_typed_config(str(config_path))

    assert config.algorithm.name == "MAHILDA"
    assert config.batch.workers == 5
    assert config.batch.timeout == 88
    assert config.database.name.name == "my.db"
