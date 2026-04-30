from __future__ import annotations

import subprocess
from importlib import resources
from typing import TYPE_CHECKING

from mahilda.evaluation.datasets import relational
from mahilda.evaluation.datasets.relational import (
    DatasetPreparationReport,
    RelationalDatasetPreparer,
    RelationalDownloadSettings,
    adjust_mysql_dump_for_sqlite,
    build_dump_command,
    extract_export_name,
    mask_command,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_extract_export_name_from_dataset_html() -> None:
    html = '<span>Export "</span><span data-reactid="42">Mondial</span>'

    assert extract_export_name(html) == "Mondial"


def test_extract_export_name_from_current_dataset_markup() -> None:
    html = (
        '<span data-reactid="x">Export &quot;</span>'
        '<span data-reactid="y">Mondial</span>'
        '<span data-reactid="z">&quot; database</span>'
    )

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
    assert "--set-gtid-purged=OFF" not in command
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


def test_prepare_redumps_empty_sql_files(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / "one.sql").write_text("", encoding="latin1")
    settings = RelationalDownloadSettings(output_dir=tmp_path)
    preparer = RelationalDatasetPreparer(settings)

    def fake_dump_database(database_name: str, output_file: Path) -> bool:
        output_file.write_text("CREATE TABLE one(id INTEGER);\n", encoding="latin1")
        return database_name == "one"

    def fake_convert_mysql_to_sqlite(sql_file: Path, sqlite_file: Path) -> bool:
        sqlite_file.write_text(sql_file.stem, encoding="utf-8")
        return True

    monkeypatch.setattr(preparer, "dump_database", fake_dump_database)
    monkeypatch.setattr(relational, "convert_mysql_to_sqlite", fake_convert_mysql_to_sqlite)

    report = preparer.prepare(database_names=["one"], progress=False)

    assert report.dumped == ["one"]
    assert report.converted == ["one"]
    assert (tmp_path / "one.sql").stat().st_size > 0


def test_dump_database_cleans_failed_empty_dump_and_masks_password(
    monkeypatch, tmp_path: Path, caplog
) -> None:
    settings = RelationalDownloadSettings(output_dir=tmp_path, password="secret")
    preparer = RelationalDatasetPreparer(settings)
    output_file = tmp_path / "demo.sql"
    output_file.write_text("", encoding="latin1")

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args[0],
            1,
            stderr="mysqldump: unknown variable 'set-gtid-purged=OFF'\nAccess denied for -psecret\n",
        )

    monkeypatch.setattr(relational, "which", lambda command: f"/usr/bin/{command}")
    monkeypatch.setattr(relational.subprocess, "run", fake_run)

    assert preparer.dump_database("demo", output_file) is False

    assert not output_file.exists()
    assert "set-gtid-purged" in caplog.text
    assert "set-gtid-p****" not in caplog.text
    assert "-psecret" not in caplog.text
    assert "-p****" in caplog.text


def test_adjust_mysql_dump_for_sqlite_removes_mysql_only_statements() -> None:
    mysql_dump = """/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;
SET @saved_cs_client = @@character_set_client;
DROP TABLE IF EXISTS `demo`;
CREATE TABLE `demo` (
  `id` int(11) unsigned NOT NULL AUTO_INCREMENT,
  `name` varchar(20) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci DEFAULT NULL,
  KEY `ix_demo_name` (`name`) USING BTREE,
  CONSTRAINT `demo_ibfk_1` FOREIGN KEY (`id`) REFERENCES `other` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
LOCK TABLES `demo` WRITE;
INSERT INTO `demo` VALUES (1,'one');
UNLOCK TABLES;
"""

    sqlite_sql = adjust_mysql_dump_for_sqlite(mysql_dump)

    assert "SET " not in sqlite_sql
    assert "DROP TABLE" not in sqlite_sql
    assert "KEY " not in sqlite_sql
    assert "CONSTRAINT" not in sqlite_sql
    assert "ENGINE=" not in sqlite_sql
    assert 'CREATE TABLE "demo"' in sqlite_sql
    assert "INSERT INTO" in sqlite_sql


def test_shell_conversion_preserves_hyphenated_values(monkeypatch, tmp_path: Path) -> None:
    sql_file = tmp_path / "demo.sql"
    sqlite_file = tmp_path / "demo.db"
    sql_file.write_text(
        "CREATE TABLE demo(id INTEGER, amount INTEGER, created_at TEXT);\n"
        "INSERT INTO demo VALUES (1,-42,'2024-01-02');\n",
        encoding="latin1",
    )
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[0] == "awk":
            assert command[-1] == str(sql_file)
            return subprocess.CompletedProcess(command, 0, stdout=sql_file.read_text(encoding="latin1"), stderr="")
        if command[0] == "sqlite3":
            assert "-42" in kwargs["input"]
            assert "2024-01-02" in kwargs["input"]
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(relational, "which", lambda command: f"/usr/bin/{command}")
    monkeypatch.setattr(relational.subprocess, "run", fake_run)
    monkeypatch.setattr(relational, "verify_sqlite_database", lambda output_file: output_file == sqlite_file)

    assert relational.convert_with_shell_script(sql_file, sqlite_file) is True
    assert calls[0][0] == "awk"
    assert calls[1][0] == "sqlite3"


def test_shell_converter_skips_versioned_multiline_create_view(tmp_path: Path) -> None:
    sql_file = tmp_path / "view.sql"
    sql_file.write_text(
        "CREATE TABLE `Products` (`ProductID` int(11) NOT NULL);\n"
        "/*!50001 CREATE VIEW `Product View` AS SELECT\n"
        " 1 AS `ProductID`,\n"
        " 1 AS `ProductName` */;\n"
        "INSERT INTO `Products` VALUES (1);\n",
        encoding="latin1",
    )
    script = resources.files("mahilda.evaluation.datasets").joinpath("convert_sql_sqlite3.sh")

    result = subprocess.run(
        ["awk", "-f", str(script), str(sql_file)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "Product View" not in result.stdout
    assert "ProductName" not in result.stdout
    assert "INSERT INTO" in result.stdout
