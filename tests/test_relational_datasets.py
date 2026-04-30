from __future__ import annotations

from typing import TYPE_CHECKING

from mahilda.evaluation.datasets import relational
from mahilda.evaluation.datasets.relational import (
    DatasetPreparationReport,
    RelationalDatasetPreparer,
    RelationalDownloadSettings,
    build_dump_command,
    extract_export_name,
    mask_command,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_extract_export_name_from_dataset_html() -> None:
    html = '<span>Export "</span><span data-reactid="42">Mondial</span>'

    assert extract_export_name(html) == "Mondial"


def test_build_dump_command_masks_password(tmp_path: Path) -> None:
    settings = RelationalDownloadSettings(
        output_dir=tmp_path,
        host="db.example.test",
        port=3307,
        user="alice",
        password="secret",
        dump_command="mariadb-dump",
    )

    command = build_dump_command(settings, "Mondial")

    assert command[-1] == "Mondial"
    assert "-psecret" in command
    assert "-psecret" not in mask_command(command)
    assert "-p****" in mask_command(command)


def test_prepare_uses_requested_database_and_writes_report(monkeypatch, tmp_path: Path) -> None:
    settings = RelationalDownloadSettings(output_dir=tmp_path)
    preparer = RelationalDatasetPreparer(settings)

    def fake_dump_database(database_name: str, output_file: Path) -> bool:
        output_file.write_text("CREATE TABLE demo(id INTEGER);\n", encoding="latin1")
        return database_name == "demo"

    def fake_convert_mysql_to_sqlite(sql_file: Path, sqlite_file: Path) -> bool:
        sqlite_file.write_text("sqlite", encoding="utf-8")
        return sql_file.name == "demo.sql"

    monkeypatch.setattr(preparer, "dump_database", fake_dump_database)
    monkeypatch.setattr(relational, "convert_mysql_to_sqlite", fake_convert_mysql_to_sqlite)

    report = preparer.prepare(database_names=["demo"], progress=False)

    assert report == DatasetPreparationReport(discovered=["demo"], dumped=["demo"], converted=["demo"])
    assert (tmp_path / "demo.sql").exists()
    assert (tmp_path / "demo.db").exists()
    assert "Converted (1):" in (tmp_path / "conversion_report.txt").read_text(encoding="utf-8")


def test_prepare_convert_only_uses_existing_sql_files(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / "one.sql").write_text("CREATE TABLE one(id INTEGER);\n", encoding="latin1")
    settings = RelationalDownloadSettings(output_dir=tmp_path)
    preparer = RelationalDatasetPreparer(settings)

    def fake_convert_mysql_to_sqlite(sql_file: Path, sqlite_file: Path) -> bool:
        sqlite_file.write_text(sql_file.stem, encoding="utf-8")
        return True

    monkeypatch.setattr(relational, "convert_mysql_to_sqlite", fake_convert_mysql_to_sqlite)

    report = preparer.prepare(convert_only=True, progress=False)

    assert report.discovered == ["one"]
    assert report.converted == ["one"]
    assert (tmp_path / "one.db").read_text(encoding="utf-8") == "one"
