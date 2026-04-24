from pathlib import Path

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
                "logging:",
                "  log_dir: ../logs",
                "results:",
                "  output_dir: ../results",
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
