from pathlib import Path

from marita.cli.main import main


def write_config(path: Path, algorithm_name: str) -> None:
    path.write_text(
        "\n".join(
            [
                "database:",
                "  path: ../test_data",
                "  name: test.db",
                "logging:",
                "  log_dir: ../logs",
                "results:",
                "  output_dir: ../results",
                "algorithm:",
                f"  name: {algorithm_name}",
                "mlflow:",
                "  use: false",
            ]
        ),
        encoding="utf-8",
    )


def test_run_rejects_non_marita_algorithm(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    write_config(config_path, "AMIE3")

    exit_code = main(["run", "--config", str(config_path)])

    assert exit_code == 1


def test_benchmark_rejects_invalid_baseline(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    write_config(config_path, "MARITA")

    exit_code = main(["benchmark", "--config", str(config_path), "--baseline", "UNKNOWN"])

    assert exit_code == 1
