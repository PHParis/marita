from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from mahilda.evaluation.baselines import spider as spider_module
from mahilda.evaluation.baselines.spider import Spider
from mahilda.utils.rules import InclusionDependency


def _spider_with_csv(tmp_path: Path) -> Spider:
    csv_dir = tmp_path / "csv"
    csv_dir.mkdir()
    (csv_dir / "parent.csv").write_text("id\n1\n2\n3\n", encoding="utf-8")
    (csv_dir / "child.csv").write_text("id\n1\n2\n", encoding="utf-8")
    return Spider(SimpleNamespace(base_csv_dir=csv_dir))


def test_spider_reads_metanome_raw_inds_from_work_dir(monkeypatch, tmp_path: Path) -> None:
    spider = _spider_with_csv(tmp_path)
    captured: dict[str, object] = {}

    def fake_run_cmd(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        work_dir = Path(kwargs["cwd"])
        raw_file = work_dir / "results" / "raw_inds"
        raw_file.parent.mkdir(parents=True)
        raw_file.write_text(
            json.dumps(
                {
                    "type": "InclusionDependency",
                    "dependant": {
                        "columnIdentifiers": [
                            {"tableIdentifier": "child.csv", "columnIdentifier": "id"},
                        ],
                    },
                    "referenced": {
                        "columnIdentifiers": [
                            {"tableIdentifier": "parent.csv", "columnIdentifier": "id"},
                        ],
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return True

    monkeypatch.setattr(spider_module, "run_cmd", fake_run_cmd)

    rules = spider.discover_rules(results_dir=tmp_path / "results", timeout=7, memory_gb=3, java_heap_gb=2)

    expected = InclusionDependency(
        table_dependant="child",
        columns_dependant=("id",),
        table_referenced="parent",
        columns_referenced=("id",),
    )
    assert rules == {expected: (1, 1)}
    assert captured["cwd"]
    assert Path(captured["cwd"]).is_dir()
    assert captured["stderr_path"] == Path(captured["cwd"]) / "stderr.log"
    command = captured["command"]
    assert "--output" in command
    assert command[command.index("--output") + 1] == "file:raw"
    assert not any(str(part).startswith("file:/") for part in command)


def test_spider_missing_raw_inds_raises_with_stderr(monkeypatch, tmp_path: Path) -> None:
    spider = _spider_with_csv(tmp_path)

    def fake_run_cmd(command, **kwargs):
        del command
        Path(kwargs["stderr_path"]).write_text("Could not open result file for writing", encoding="utf-8")
        return True

    monkeypatch.setattr(spider_module, "run_cmd", fake_run_cmd)

    with pytest.raises(RuntimeError, match="did not produce expected Metanome output file.*Could not open"):
        spider.discover_rules(results_dir=tmp_path / "results")


def test_spider_malformed_raw_result_raises(monkeypatch, tmp_path: Path) -> None:
    spider = _spider_with_csv(tmp_path)

    def fake_run_cmd(command, **kwargs):
        del command
        raw_file = Path(kwargs["cwd"]) / "results" / "raw_inds"
        raw_file.parent.mkdir(parents=True)
        raw_file.write_text("not json\n", encoding="utf-8")
        return True

    monkeypatch.setattr(spider_module, "run_cmd", fake_run_cmd)

    with pytest.raises(RuntimeError, match="malformed JSON"):
        spider.discover_rules(results_dir=tmp_path / "results")


def test_spider_command_failure_raises(monkeypatch, tmp_path: Path) -> None:
    spider = _spider_with_csv(tmp_path)
    monkeypatch.setattr(spider_module, "run_cmd", lambda command, **kwargs: False)

    with pytest.raises(RuntimeError, match="SPIDER command failed"):
        spider.discover_rules(results_dir=tmp_path / "results")


@pytest.mark.integration
@pytest.mark.java_required
def test_spider_real_metanome_discovers_tiny_ind(tmp_path: Path) -> None:
    if os.environ.get("MAHILDA_RUN_INTEGRATION") != "1":
        pytest.skip("Set MAHILDA_RUN_INTEGRATION=1 to run integration tests")
    if shutil.which("java") is None:
        pytest.skip("Java required for SPIDER integration")

    spider = _spider_with_csv(tmp_path)
    rules = spider.discover_rules(results_dir=tmp_path / "results", timeout=30, memory_gb=2, java_heap_gb=1)

    expected = InclusionDependency(
        table_dependant="child",
        columns_dependant=("id",),
        table_referenced="parent",
        columns_referenced=("id",),
    )
    assert rules == {expected: (1, 1)}
