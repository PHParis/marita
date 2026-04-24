import json
import os
from pathlib import Path

from mahilda.cli import batch


def test_run_database_writes_execution_time_file(monkeypatch, tmp_path: Path) -> None:
    class FakeProcessor:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

        def discover_rules(self) -> int:
            return 3

        def clean_up(self) -> None:
            return

    monkeypatch.setattr(batch, "DatabaseProcessor", FakeProcessor)

    db_path = tmp_path / "demo.db"
    db_path.write_text("", encoding="utf-8")
    results_base = tmp_path / "results"

    result = batch.run_database(db_path, "demo", results_base, timeout=10)

    metrics_path = results_base / "MAHILDA_demo" / "execution_time_demo.json"
    assert result["status"] == "success"
    assert result["rules_count"] == 3
    assert metrics_path.exists()

    payload = json.loads(metrics_path.read_text(encoding="utf-8"))
    assert payload["database"] == "demo"
    assert payload["rules_count"] == 3


def test_run_database_uses_configured_log_root_and_cleans_up(monkeypatch, tmp_path: Path) -> None:
    observed: dict[str, object] = {"log_dir": None, "cleaned": False}
    real_configure_global_logger = batch.configure_global_logger

    class FakeProcessor:
        def __init__(self, *args, **kwargs) -> None:
            del args
            del kwargs

        def discover_rules(self) -> int:
            return 1

        def clean_up(self) -> None:
            observed["cleaned"] = True

    def fake_configure_global_logger(log_dir: str):
        observed["log_dir"] = log_dir
        return real_configure_global_logger(log_dir)

    monkeypatch.setattr(batch, "DatabaseProcessor", FakeProcessor)
    monkeypatch.setattr(batch, "configure_global_logger", fake_configure_global_logger)

    db_path = tmp_path / "demo.db"
    db_path.write_text("", encoding="utf-8")
    results_base = tmp_path / "results"
    log_root = tmp_path / "configured_logs"

    result = batch.run_database(db_path, "demo", results_base, timeout=10, log_root=log_root)

    assert result["status"] == "success"
    assert observed["cleaned"] is True
    assert observed["log_dir"] == str(log_root / "demo")


def test_run_database_restores_env_vars(monkeypatch, tmp_path: Path) -> None:
    class FakeProcessor:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

        def discover_rules(self) -> int:
            return 0

        def clean_up(self) -> None:
            return

    monkeypatch.setattr(batch, "DatabaseProcessor", FakeProcessor)

    previous_log_dir = "persisted-log-dir"
    previous_quiet = "0"
    os.environ["MAHILDA_LOG_DIR"] = previous_log_dir
    os.environ["MAHILDA_QUIET"] = previous_quiet

    try:
        db_path = tmp_path / "demo.db"
        db_path.write_text("", encoding="utf-8")
        results_base = tmp_path / "results"
        batch.run_database(db_path, "demo", results_base, timeout=10, log_root=tmp_path / "logs")
    finally:
        assert os.environ.get("MAHILDA_LOG_DIR") == previous_log_dir
        assert os.environ.get("MAHILDA_QUIET") == previous_quiet
        os.environ.pop("MAHILDA_LOG_DIR", None)
        os.environ.pop("MAHILDA_QUIET", None)
