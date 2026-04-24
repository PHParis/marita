from pathlib import Path

import pytest

from mahilda.utils.config_loader import load_config


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
