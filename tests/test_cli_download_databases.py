from __future__ import annotations

from typing import TYPE_CHECKING

from marita.cli import download_databases
from marita.evaluation.datasets.relational import DatasetPreparationReport

if TYPE_CHECKING:
    from pathlib import Path


def test_download_databases_lists_names(monkeypatch, capsys) -> None:
    class FakePreparer:
        def __init__(self, settings):
            self.settings = settings

        def discover_databases(self, *, progress: bool = True) -> list[str]:
            assert progress is False
            return ["one", "two"]

    monkeypatch.setattr(download_databases, "RelationalDatasetPreparer", FakePreparer)

    exit_code = download_databases.main(["--output", "out", "--list", "--quiet", "--max-databases", "1"])

    assert exit_code == 0
    assert capsys.readouterr().out == "one\n"


def test_download_databases_runs_preparation(monkeypatch, tmp_path: Path, capsys) -> None:
    captured: dict[str, object] = {}

    class FakePreparer:
        def __init__(self, settings):
            captured["output_dir"] = settings.output_dir

        def prepare(self, **kwargs) -> DatasetPreparationReport:
            captured.update(kwargs)
            return DatasetPreparationReport(discovered=["demo"], dumped=["demo"], converted=["demo"])

    monkeypatch.setattr(download_databases, "RelationalDatasetPreparer", FakePreparer)

    exit_code = download_databases.main(
        ["--output", str(tmp_path), "--database", "demo", "--dump-only", "--quiet", "--timeout", "7"]
    )

    assert exit_code == 0
    assert captured["output_dir"] == tmp_path
    assert captured["database_names"] == ["demo"]
    assert captured["dump_only"] is True
    assert captured["convert_only"] is False
    assert captured["progress"] is False
    assert "Converted: 1" in capsys.readouterr().out


def test_download_databases_rejects_conflicting_modes() -> None:
    assert download_databases.main(["--dump-only", "--convert-only"]) == 2


def test_download_databases_fails_when_no_databases_selected(monkeypatch, tmp_path: Path) -> None:
    class FakePreparer:
        def __init__(self, settings):
            self.settings = settings

        def prepare(self, **kwargs) -> DatasetPreparationReport:
            return DatasetPreparationReport()

    monkeypatch.setattr(download_databases, "RelationalDatasetPreparer", FakePreparer)

    exit_code = download_databases.main(["--output", str(tmp_path), "--quiet"])

    assert exit_code == 1
