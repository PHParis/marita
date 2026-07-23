from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from marita.evaluation.baselines.popper import Popper


class _DummyDatabase:
    base_name = "demo"

    def get_table_names(self) -> list[str]:
        return ["Person", "Target"]

    def get_attribute_names(self, table_name: str) -> list[str]:
        return {"Person": ["id", "name"], "Target": ["id", "name"]}.get(table_name, [])

    def _select_query(self, table_name: str, attributes: list[str]) -> list[tuple[Any, ...]]:
        del attributes
        if table_name == "Person":
            return [(1, "alice")]
        return [(1, "alice")]


def test_resolve_popper_command_prefers_config(monkeypatch) -> None:
    monkeypatch.setenv("MARITA_POPPER_CMD", "env-popper")

    assert Popper._resolve_popper_command("custom --flag") == ["custom", "--flag"]


def test_resolve_popper_command_uses_env(monkeypatch) -> None:
    monkeypatch.setenv("MARITA_POPPER_CMD", "env-popper --x")

    assert Popper._resolve_popper_command(None) == ["env-popper", "--x"]


def test_parse_popper_solution_output() -> None:
    popper = Popper(_DummyDatabase())
    popper._safe_to_original_table = {"person": "Person", "target": "Target"}
    output = """
********** SOLUTION **********
Precision:0.75 Recall:0.50 TP:3 FN:1 TN:4 FP:1 Size:2
target(V0,V1):- person(V0,V1).
******************************
"""

    rules = popper.parse_popper_output(output)

    assert len(rules) == 1
    assert rules[0].accuracy == 0.75
    assert rules[0].confidence == 0.50
    assert rules[0].display == "target(V0,V1):- person(V0,V1)."


def test_parse_popper_no_solution_output() -> None:
    assert Popper(_DummyDatabase()).parse_popper_output("NO SOLUTION\n") == []


def test_external_popper_failure_raises(monkeypatch, tmp_path: Path) -> None:
    def fake_run_cmd(*args, **kwargs) -> bool:
        del args, kwargs
        return False

    monkeypatch.setattr("marita.evaluation.baselines.popper.run_cmd", fake_run_cmd)
    popper = Popper(_DummyDatabase())

    with pytest.raises(RuntimeError, match="External Popper command failed"):
        popper.discover_rules(
            results_dir=str(tmp_path), runtime_dir=str(tmp_path / "runtime"), popper_command="fake-popper"
        )


def test_external_popper_command_and_parse(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, Any] = {}

    def fake_run_cmd(command: list[str], **kwargs) -> bool:
        captured["command"] = command
        Path(kwargs["stdout_path"]).write_text(
            "********** SOLUTION **********\n"
            "Precision:1.00 Recall:1.00 TP:1 FN:0 TN:0 FP:0 Size:2\n"
            "target(V0,V1):- person(V0,V1).\n"
            "******************************\n",
            encoding="utf-8",
        )
        return True

    monkeypatch.setattr("marita.evaluation.baselines.popper.run_cmd", fake_run_cmd)
    rules = Popper(_DummyDatabase()).discover_rules(
        results_dir=str(tmp_path),
        runtime_dir=str(tmp_path / "runtime"),
        popper_command="fake-popper",
        timeout=123,
        memory_gb=2,
    )

    assert rules
    assert captured["command"][0] == "fake-popper"
    assert "--timeout" in captured["command"]
    assert "123" in captured["command"]
