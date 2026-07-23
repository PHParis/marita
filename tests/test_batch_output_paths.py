import json
import os
from pathlib import Path

from marita.cli import batch


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

    metrics_path = results_base / "MARITA_demo" / "execution_time_demo.json"
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
    os.environ["MARITA_LOG_DIR"] = previous_log_dir
    os.environ["MARITA_QUIET"] = previous_quiet

    try:
        db_path = tmp_path / "demo.db"
        db_path.write_text("", encoding="utf-8")
        results_base = tmp_path / "results"
        batch.run_database(db_path, "demo", results_base, timeout=10, log_root=tmp_path / "logs")
    finally:
        assert os.environ.get("MARITA_LOG_DIR") == previous_log_dir
        assert os.environ.get("MARITA_QUIET") == previous_quiet
        os.environ.pop("MARITA_LOG_DIR", None)
        os.environ.pop("MARITA_QUIET", None)


def test_run_database_timeout_fallback_without_sigalrm(monkeypatch, tmp_path: Path) -> None:
    observed: dict[str, bool] = {"cleaned": False, "cancelled": False}

    class FakeProcessor:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

        def discover_rules(self) -> int:
            raise KeyboardInterrupt

        def clean_up(self) -> None:
            observed["cleaned"] = True

    class FakeTimer:
        def __init__(self, interval: float, callback) -> None:
            del interval
            self._callback = callback
            self.daemon = False

        def start(self) -> None:
            self._callback()

        def cancel(self) -> None:
            observed["cancelled"] = True

    monkeypatch.setattr(batch, "DatabaseProcessor", FakeProcessor)
    monkeypatch.delattr(batch.signal, "SIGALRM", raising=False)
    monkeypatch.setattr(batch.threading, "Timer", FakeTimer)
    monkeypatch.setattr(batch._thread, "interrupt_main", lambda: None)

    db_path = tmp_path / "demo.db"
    db_path.write_text("", encoding="utf-8")
    results_base = tmp_path / "results"

    result = batch.run_database(db_path, "demo", results_base, timeout=10, log_root=tmp_path / "logs")

    assert result["status"] == "timeout"
    assert result["error"] == "Execution exceeded 10 seconds"
    assert observed["cleaned"] is True
    assert observed["cancelled"] is True
