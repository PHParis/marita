from pathlib import Path

from marita.cli import smoke


def test_smoke_returns_error_for_missing_config(tmp_path: Path) -> None:
    missing_config = tmp_path / "missing.yaml"

    exit_code = smoke.main(["--config", str(missing_config)])

    assert exit_code == 1


def test_smoke_sets_verbose_env_temporarily(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("database:\n  path: ./data\n", encoding="utf-8")

    observed = {"verbose": None, "quiet": None}

    def fake_run_main(argv):
        del argv
        import os

        observed["verbose"] = os.environ.get("MARITA_VERBOSE")
        observed["quiet"] = os.environ.get("MARITA_QUIET")
        return 0

    monkeypatch.setattr("marita.cli.run.main", fake_run_main)

    exit_code = smoke.main(["--config", str(config_path), "--verbose"])

    assert exit_code == 0
    assert observed["verbose"] == "1"
    assert observed["quiet"] is None
