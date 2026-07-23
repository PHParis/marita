from __future__ import annotations

from pathlib import Path
from typing import Any

from marita.evaluation.baselines.amie3 import Amie3


class _DummyDatabase:
    database_path_tsv = "fallback-tsv-dir"


def test_amie3_uses_direct_input_tsv_when_provided(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, Any] = {}
    input_tsv = tmp_path / "input.tsv"
    input_tsv.write_text("s\tp\to\n", encoding="utf-8")

    def fake_run_cmd(command: list[str], **kwargs) -> bool:
        captured["command"] = command
        captured["timeout"] = kwargs["timeout"]
        Path(kwargs["stdout_path"]).write_text("", encoding="utf-8")
        return True

    monkeypatch.setattr("marita.evaluation.baselines.amie3.run_cmd", fake_run_cmd)

    rules = Amie3(_DummyDatabase()).discover_rules(results_dir=str(tmp_path), input_tsv=input_tsv)

    assert rules == []
    assert str(input_tsv) in captured["command"]
    assert "fallback-tsv-dir" not in captured["command"]
    assert captured["timeout"] == 300


def test_amie3_uses_configured_timeout(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, Any] = {}

    def fake_run_cmd(command: list[str], **kwargs) -> bool:
        captured["command"] = command
        captured["timeout"] = kwargs["timeout"]
        Path(kwargs["stdout_path"]).write_text("", encoding="utf-8")
        return True

    monkeypatch.setattr("marita.evaluation.baselines.amie3.run_cmd", fake_run_cmd)

    rules = Amie3(_DummyDatabase()).discover_rules(results_dir=str(tmp_path), timeout=1800)

    assert rules == []
    assert "fallback-tsv-dir" in captured["command"]
    assert captured["timeout"] == 1800
