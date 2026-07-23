from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from marita.cli import benchmark as benchmark_cli
from marita.cli import run as run_cli


@contextmanager
def _noop_cm(*args, **kwargs):
    del args, kwargs
    yield


def _typed_config(tmp_path: Path, *, algorithm: str = "MARITA", mlflow_use: bool = False):
    return SimpleNamespace(
        monitor=SimpleNamespace(memory_threshold=80, timeout=10),
        database=SimpleNamespace(path=tmp_path, name=Path("demo.db")),
        logging=SimpleNamespace(log_dir=tmp_path / "logs"),
        results=SimpleNamespace(output_dir=tmp_path / "results"),
        algorithm=SimpleNamespace(name=algorithm),
        benchmark=SimpleNamespace(
            baseline=None,
            input_tsv=None,
            timeout=11,
            memory_gb=10,
            java_heap_gb=8,
            popper_command=None,
            workers=1,
        ),
        batch=SimpleNamespace(timeout=11, workers=1),
        mlflow=SimpleNamespace(use=mlflow_use),
        raw={"algorithm": {"name": algorithm}},
    )


def test_run_main_happy_path(monkeypatch, tmp_path: Path) -> None:
    cfg = _typed_config(tmp_path)
    called = {"discover": 0, "cleanup": 0}

    class FakeMonitor:
        should_stop = False

        def __init__(self, threshold, timeout):
            del threshold, timeout

        def monitor(self):
            return None

        def stop(self):
            return None

    class FakeThread:
        def __init__(self, target=None, daemon=None):
            del target, daemon

        def start(self):
            return None

        def join(self, timeout=None):
            del timeout
            return None

    class FakeProcessor:
        def __init__(self, **kwargs):
            del kwargs

        def discover_rules(self):
            called["discover"] += 1

        def clean_up(self):
            called["cleanup"] += 1

    monkeypatch.setattr(run_cli, "load_typed_config", lambda _p: cfg)
    monkeypatch.setattr(run_cli, "initialize_directories", lambda *args, **kwargs: None)
    monkeypatch.setattr(run_cli, "configure_global_logger", lambda _p: logging.getLogger("test.run.main"))
    monkeypatch.setattr(run_cli, "ResourceMonitor", FakeMonitor)
    monkeypatch.setattr(run_cli.threading, "Thread", FakeThread)
    monkeypatch.setattr(run_cli, "setup_signal_handlers", lambda *args, **kwargs: None)
    monkeypatch.setattr(run_cli, "DatabaseProcessor", FakeProcessor)
    monkeypatch.setattr(run_cli, "scoped_env_vars", _noop_cm)
    monkeypatch.setattr(run_cli, "mlflow_run_context", _noop_cm)

    assert run_cli.main(["--config", "cfg.yml"]) == 0
    assert called["discover"] == 1
    assert called["cleanup"] == 1


def test_run_main_rejects_non_marita(monkeypatch, tmp_path: Path) -> None:
    cfg = _typed_config(tmp_path, algorithm="SPIDER")
    monkeypatch.setattr(run_cli, "load_typed_config", lambda _p: cfg)
    monkeypatch.setattr(run_cli, "initialize_directories", lambda *args, **kwargs: None)
    monkeypatch.setattr(run_cli, "configure_global_logger", lambda _p: logging.getLogger("test.run.reject"))
    assert run_cli.main(["--config", "cfg.yml"]) == 1


def test_benchmark_main_invalid_and_happy_path(monkeypatch, tmp_path: Path) -> None:
    cfg = _typed_config(tmp_path, algorithm="MARITA")
    monkeypatch.setattr(benchmark_cli, "load_typed_config", lambda _p: cfg)
    monkeypatch.setattr(benchmark_cli, "initialize_directories", lambda *args, **kwargs: None)
    monkeypatch.setattr(benchmark_cli, "configure_global_logger", lambda _p: logging.getLogger("test.bench"))
    monkeypatch.setattr(benchmark_cli, "scoped_env_vars", _noop_cm)
    monkeypatch.setattr(benchmark_cli, "mlflow_run_context", _noop_cm)

    assert benchmark_cli.main(["--config", "cfg.yml", "--baseline", "NOTREAL"]) == 1

    called = {"discover": 0, "cleanup": 0, "baseline": ""}

    class FakeProcessor:
        def __init__(self, **kwargs):
            called["baseline"] = kwargs["baseline_name"]

        def discover_rules(self):
            called["discover"] += 1

        def clean_up(self):
            called["cleanup"] += 1

    monkeypatch.setattr(benchmark_cli, "BaselineProcessor", FakeProcessor)
    assert benchmark_cli.main(["--config", "cfg.yml", "--baseline", "amie3"]) == 0
    assert called["baseline"] == "AMIE3"
    assert called["discover"] == 1
    assert called["cleanup"] == 1
