from __future__ import annotations

import os
from typing import TYPE_CHECKING

from marita.cli.runtime import initialize_directories, mlflow_run_context, scoped_env_vars

if TYPE_CHECKING:
    from pathlib import Path


def test_initialize_directories_creates_paths(tmp_path: Path) -> None:
    results = tmp_path / "results"
    logs = tmp_path / "logs"
    initialize_directories(results, logs)
    assert results.exists()
    assert logs.exists()


def test_scoped_env_vars_sets_and_restores(monkeypatch) -> None:
    monkeypatch.setenv("EXISTING_KEY", "old")
    os.environ.pop("NEW_KEY", None)

    with scoped_env_vars({"EXISTING_KEY": "new", "NEW_KEY": "value", "REMOVE_ME": None}):
        assert os.environ["EXISTING_KEY"] == "new"
        assert os.environ["NEW_KEY"] == "value"
        assert "REMOVE_ME" not in os.environ

    assert os.environ["EXISTING_KEY"] == "old"
    assert "NEW_KEY" not in os.environ


def test_mlflow_run_context_disabled_noop() -> None:
    with mlflow_run_context(False, {}):
        assert True


def test_mlflow_run_context_enabled_calls_mlflow(monkeypatch) -> None:
    calls: list[str] = []

    class FakeMlflow:
        def set_tracking_uri(self, uri: str) -> None:
            calls.append(f"uri:{uri}")

        def set_experiment(self, experiment: str) -> None:
            calls.append(f"exp:{experiment}")

        def start_run(self) -> None:
            calls.append("start")

        def end_run(self) -> None:
            calls.append("end")

    monkeypatch.setitem(__import__("sys").modules, "mlflow", FakeMlflow())

    cfg = {"mlflow": {"tracking_uri": "http://x", "experiment_name": "Demo"}}
    with mlflow_run_context(True, cfg):
        calls.append("inside")

    assert calls == ["uri:http://x", "exp:Demo", "start", "inside", "end"]


def test_mlflow_run_context_always_ends_run_on_error(monkeypatch) -> None:
    calls: list[str] = []

    class FakeMlflow:
        def set_tracking_uri(self, uri: str) -> None:
            del uri

        def set_experiment(self, experiment: str) -> None:
            del experiment

        def start_run(self) -> None:
            calls.append("start")

        def end_run(self) -> None:
            calls.append("end")

    monkeypatch.setitem(__import__("sys").modules, "mlflow", FakeMlflow())

    try:
        with mlflow_run_context(True, {}):
            raise RuntimeError("boom")
    except RuntimeError:
        pass

    assert calls == ["start", "end"]
