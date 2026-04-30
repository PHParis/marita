from __future__ import annotations

import logging
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from html import unescape
from importlib import resources
from pathlib import Path
from shutil import which
from typing import Any

from tqdm import tqdm

LOGGER = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://relational.fel.cvut.cz"
DEFAULT_HOST = "relational.fel.cvut.cz"
DEFAULT_PORT = 3306
DEFAULT_USER = "guest"
DEFAULT_PASSWORD = "ctu-relational"


@dataclass(frozen=True)
class RelationalDownloadSettings:
    output_dir: Path
    base_url: str = DEFAULT_BASE_URL
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    user: str = DEFAULT_USER
    password: str = DEFAULT_PASSWORD
    dump_command: str = "mysqldump"
    timeout: int = 300

    @classmethod
    def from_env(cls, output_dir: Path, *, timeout: int = 300) -> RelationalDownloadSettings:
        return cls(
            output_dir=output_dir,
            base_url=os.getenv("MAHILDA_RELATIONAL_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
            host=os.getenv("MAHILDA_RELATIONAL_HOST", DEFAULT_HOST),
            port=int(os.getenv("MAHILDA_RELATIONAL_PORT", str(DEFAULT_PORT))),
            user=os.getenv("MAHILDA_RELATIONAL_USER", DEFAULT_USER),
            password=os.getenv("MAHILDA_RELATIONAL_PASSWORD", DEFAULT_PASSWORD),
            dump_command=os.getenv("MAHILDA_RELATIONAL_DUMP_COMMAND", "mysqldump"),
            timeout=timeout,
        )


@dataclass
class DatasetPreparationReport:
    discovered: list[str] = field(default_factory=list)
    dumped: list[str] = field(default_factory=list)
    converted: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)

    @property
    def success_count(self) -> int:
        return len(self.dumped) + len(self.converted) + len(self.skipped)


def create_requests_session() -> Any:
    try:
        import requests
        from requests.adapters import HTTPAdapter, Retry
    except ImportError as exc:  # pragma: no cover - exercised through CLI dependency message
        msg = "Install dataset dependencies with `uv sync --extra datasets`."
        raise RuntimeError(msg) from exc

    session = requests.Session()
    retries = Retry(total=5, backoff_factor=0.3, status_forcelist=[500, 502, 504])
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def extract_database_names(search_html: str, fetch_dataset_html: Any, *, base_url: str) -> list[str]:
    try:
        from bs4 import BeautifulSoup
    except ImportError as exc:  # pragma: no cover - exercised through CLI dependency message
        msg = "Install dataset dependencies with `uv sync --extra datasets`."
        raise RuntimeError(msg) from exc

    soup = BeautifulSoup(search_html, "html.parser")
    db_links = soup.find_all("a", href=re.compile(r"/dataset/"))
    databases: list[str] = []
    for link in db_links:
        href = link.get("href")
        if not href:
            continue
        href_text = str(href)
        dataset_url = href_text if href_text.startswith("http") else f"{base_url}{href_text}"
        dataset_html = fetch_dataset_html(dataset_url)
        name = extract_export_name(dataset_html)
        if name:
            databases.append(name)
        else:
            LOGGER.warning("Database export name not found for dataset page: %s", dataset_url)
    return databases


def extract_export_name(dataset_html: str) -> str | None:
    html_patterns = [
        r'Export\s+"\s*</span>\s*<span[^>]*>\s*([^<]+?)\s*</span>',
        r"Export\s+&quot;\s*</span>\s*<span[^>]*>\s*([^<]+?)\s*</span>",
        r"Export\s+&quot;\s*([^<&]+)",
    ]
    for pattern in html_patterns:
        match = re.search(pattern, dataset_html)
        if match is not None:
            return match.group(1).strip()

    text = re.sub(r"<[^>]+>", " ", dataset_html)
    text = unescape(text)
    text = re.sub(r"\s+", " ", text)
    fallback = re.search(r'Export\s+"\s*([^"]+?)\s*"\s+database', text, flags=re.IGNORECASE)
    if fallback is not None:
        return fallback.group(1).strip()
    return None


def mask_command(cmd: list[str]) -> str:
    return " ".join(part if not part.startswith("-p") else "-p****" for part in cmd)


def build_dump_command(settings: RelationalDownloadSettings, database_name: str) -> list[str]:
    return [
        settings.dump_command,
        "--single-transaction",
        "--no-tablespaces",
        "--skip-lock-tables",
        "-h",
        settings.host,
        "-P",
        str(settings.port),
        "-u",
        settings.user,
        f"-p{settings.password}",
        database_name,
    ]


class RelationalDatasetPreparer:
    def __init__(self, settings: RelationalDownloadSettings, *, session: Any | None = None) -> None:
        self.settings = settings
        self.session = session
        self.settings.output_dir.mkdir(parents=True, exist_ok=True)

    def discover_databases(self, *, progress: bool = True) -> list[str]:
        session = self.session or create_requests_session()
        search_response = session.get(
            f"{self.settings.base_url}/search",
            auth=(self.settings.user, self.settings.password),
            timeout=10,
        )
        search_response.raise_for_status()

        def fetch_dataset_html(url: str) -> str:
            response = session.get(url, auth=(self.settings.user, self.settings.password), timeout=10)
            response.raise_for_status()
            return response.text

        names = extract_database_names(search_response.text, fetch_dataset_html, base_url=self.settings.base_url)
        iterable = tqdm(names, desc="Discovered relational datasets", unit="database", disable=not progress)
        return list(iterable)

    def prepare(
        self,
        database_names: list[str] | None = None,
        *,
        max_databases: int | None = None,
        dump_only: bool = False,
        convert_only: bool = False,
        progress: bool = True,
    ) -> DatasetPreparationReport:
        report = DatasetPreparationReport()
        if convert_only:
            names = sorted(path.stem for path in self.settings.output_dir.glob("*.sql"))
        else:
            names = database_names or self.discover_databases(progress=progress)

        if max_databases is not None:
            names = names[:max_databases]
        report.discovered = names

        iterable = tqdm(names, desc="Preparing relational datasets", unit="database", disable=not progress)
        for name in iterable:
            self._prepare_one(name, report, dump_only=dump_only, convert_only=convert_only)

        self.write_report(report)
        return report

    def _prepare_one(
        self,
        database_name: str,
        report: DatasetPreparationReport,
        *,
        dump_only: bool,
        convert_only: bool,
    ) -> None:
        sqlite_file = self.settings.output_dir / f"{database_name}.db"
        sql_file = self.settings.output_dir / f"{database_name}.sql"

        if sqlite_file.exists() and not dump_only:
            report.skipped.append(database_name)
            LOGGER.info("SQLite database already exists for %s: %s", database_name, sqlite_file)
            return

        if not convert_only and (not sql_file.exists() or sql_file.stat().st_size == 0):
            if not self.dump_database(database_name, sql_file):
                report.failed.append((database_name, "dump failed"))
                return
            report.dumped.append(database_name)

        if dump_only:
            return

        if not sql_file.exists():
            report.failed.append((database_name, "SQL dump missing"))
            return

        if convert_mysql_to_sqlite(sql_file, sqlite_file):
            report.converted.append(database_name)
        else:
            report.failed.append((database_name, "conversion failed"))

    def dump_database(self, database_name: str, output_file: Path) -> bool:
        if which(self.settings.dump_command) is None:
            LOGGER.error("Dump command not found in PATH: %s", self.settings.dump_command)
            return False

        cmd = build_dump_command(self.settings, database_name)
        temp_file_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile("w", dir=output_file.parent, delete=False) as file_out:
                temp_file_path = Path(file_out.name)
                result = subprocess.run(
                    cmd,
                    stdout=file_out,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=self.settings.timeout,
                    check=False,
                )
        except subprocess.TimeoutExpired:
            LOGGER.error("Dump timed out after %s seconds: %s", self.settings.timeout, mask_command(cmd))
            if temp_file_path is not None and temp_file_path.exists():
                temp_file_path.unlink()
            return False

        if result.returncode != 0:
            if temp_file_path is not None and temp_file_path.exists():
                temp_file_path.unlink()
            if output_file.exists() and output_file.stat().st_size == 0:
                output_file.unlink()
            LOGGER.error("Dump command failed: %s", re.sub(r"(?<!\S)-p\S+", "-p****", result.stderr or ""))
            return False
        if temp_file_path is None or temp_file_path.stat().st_size == 0:
            if temp_file_path is not None and temp_file_path.exists():
                temp_file_path.unlink()
            if output_file.exists() and output_file.stat().st_size == 0:
                output_file.unlink()
            LOGGER.error("Dump command wrote an empty SQL file: %s", output_file)
            return False

        temp_file_path.replace(output_file)
        LOGGER.info("Dumped %s with command: %s", database_name, mask_command(cmd))
        return True

    def write_report(self, report: DatasetPreparationReport) -> Path:
        report_path = self.settings.output_dir / "conversion_report.txt"
        lines = [
            "Relational benchmark dataset preparation report",
            "",
            f"Discovered/selected ({len(report.discovered)}):",
            *report.discovered,
            "",
            f"Dumped ({len(report.dumped)}):",
            *report.dumped,
            "",
            f"Converted ({len(report.converted)}):",
            *report.converted,
            "",
            f"Skipped ({len(report.skipped)}):",
            *report.skipped,
            "",
            f"Failed ({len(report.failed)}):",
            *(f"{name}: {reason}" for name, reason in report.failed),
        ]
        report_path.write_text("\n".join(lines) + "\n")
        return report_path


def convert_mysql_to_sqlite(mysql_input_file: Path, sqlite_output_file: Path) -> bool:
    methods = [
        lambda: convert_with_shell_script(mysql_input_file, sqlite_output_file),
        lambda: convert_with_regex_adjustments(mysql_input_file, sqlite_output_file),
    ]
    for index, method in enumerate(methods, start=1):
        LOGGER.info("Attempting MySQL-to-SQLite conversion method %s", index)
        if sqlite_output_file.exists():
            sqlite_output_file.unlink()
        if method():
            LOGGER.info("Conversion succeeded with method %s", index)
            return True

    if sqlite_output_file.exists():
        sqlite_output_file.unlink()
    return False


def convert_with_shell_script(mysql_input_file: Path, sqlite_output_file: Path) -> bool:
    if which("sqlite3") is None:
        LOGGER.error("sqlite3 not found in PATH")
        return False
    if which("awk") is None:
        LOGGER.error("awk not found in PATH")
        return False
    if not validate_sql_file(mysql_input_file):
        return False

    script = resources.files("mahilda.evaluation.datasets").joinpath("convert_sql_sqlite3.sh")
    script_proc = subprocess.run(
        ["awk", "-f", str(script), str(mysql_input_file)],
        capture_output=True,
        text=True,
        check=False,
    )
    if script_proc.returncode != 0:
        LOGGER.error("SQL conversion script failed: %s", script_proc.stderr)
        return False

    sqlite_proc = subprocess.run(
        ["sqlite3", str(sqlite_output_file)],
        input=script_proc.stdout,
        capture_output=True,
        text=True,
        check=False,
    )
    if sqlite_proc.returncode != 0:
        LOGGER.error("sqlite3 import failed: %s", sqlite_proc.stderr)
        return False
    return verify_sqlite_database(sqlite_output_file)


def convert_with_regex_adjustments(mysql_input_file: Path, sqlite_output_file: Path) -> bool:
    if which("sqlite3") is None:
        LOGGER.error("sqlite3 not found in PATH")
        return False
    if not validate_sql_file(mysql_input_file):
        return False

    adjusted_file: Path | None = None
    try:
        content = adjust_mysql_dump_for_sqlite(mysql_input_file.read_text(encoding="latin1"))

        with tempfile.NamedTemporaryFile(delete=False, mode="w", suffix=".sql") as temp_out:
            adjusted_file = Path(temp_out.name)
            temp_out.write(content)

        with adjusted_file.open() as infile:
            result = subprocess.run(
                ["sqlite3", str(sqlite_output_file)],
                stdin=infile,
                capture_output=True,
                text=True,
                check=False,
            )
        if result.returncode != 0:
            LOGGER.error("sqlite3 import failed: %s", result.stderr)
            return False
        return verify_sqlite_database(sqlite_output_file)
    finally:
        if adjusted_file and adjusted_file.exists():
            adjusted_file.unlink()


def adjust_mysql_dump_for_sqlite(content: str) -> str:
    lines: list[str] = []
    create_table_lines: list[str] = []
    in_create_table = False
    skipping_versioned_statement = False

    def flush_create_table() -> None:
        while create_table_lines and create_table_lines[-1].rstrip().endswith(","):
            create_table_lines[-1] = create_table_lines[-1].rstrip().removesuffix(",") + "\n"
        lines.extend(create_table_lines)
        create_table_lines.clear()

    for raw_line in content.splitlines(keepends=True):
        stripped = raw_line.strip()
        upper = stripped.upper()

        if skipping_versioned_statement:
            if stripped.endswith("*/;") or stripped.endswith("*/"):
                skipping_versioned_statement = False
            continue

        if not stripped or stripped.startswith("--") or stripped.startswith("/*M!"):
            continue
        if stripped.startswith("/*!"):
            if not stripped.endswith("*/;") and not stripped.endswith("*/"):
                skipping_versioned_statement = True
            continue
        if upper.startswith(("SET ", "LOCK TABLES", "UNLOCK TABLES", "DROP TABLE", "DROP VIEW")):
            continue

        line = raw_line.replace("`", '"')
        line = re.sub(r"\bunsigned\b", "", line, flags=re.IGNORECASE)
        line = re.sub(r"\bAUTO_INCREMENT\b", "", line, flags=re.IGNORECASE)
        line = re.sub(r"\s+USING\s+BTREE", "", line, flags=re.IGNORECASE)
        line = re.sub(r"\s+CHARACTER SET\s+\w+", "", line, flags=re.IGNORECASE)
        line = re.sub(r"\s+COLLATE\s+\w+", "", line, flags=re.IGNORECASE)
        line = re.sub(r"\)\s*ENGINE=.*;", ");", line, flags=re.IGNORECASE)

        if upper.startswith("CREATE TABLE"):
            in_create_table = True
            create_table_lines.append(line)
            continue

        if in_create_table:
            line_upper = line.strip().upper()
            if line_upper.startswith(("KEY ", "UNIQUE KEY", "FULLTEXT KEY", "SPATIAL KEY", "CONSTRAINT ")):
                continue
            if line_upper.startswith(");"):
                flush_create_table()
                lines.append(line)
                in_create_table = False
                continue
            create_table_lines.append(line)
            continue

        lines.append(line)

    if create_table_lines:
        flush_create_table()
    return "".join(lines)


def validate_sql_file(sql_file: Path) -> bool:
    try:
        content = sql_file.read_text(encoding="latin1")
    except OSError as exc:
        LOGGER.error("Unable to read SQL dump %s: %s", sql_file, exc)
        return False
    if not content.strip():
        LOGGER.error("SQL dump is empty: %s", sql_file)
        return False
    if "CREATE TABLE" not in content.upper():
        LOGGER.error("SQL dump does not contain CREATE TABLE statements: %s", sql_file)
        return False
    return True


def verify_sqlite_database(sqlite_output_file: Path) -> bool:
    result = subprocess.run(
        ["sqlite3", str(sqlite_output_file), "SELECT name FROM sqlite_master WHERE type='table' LIMIT 1;"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        LOGGER.error("Failed to verify SQLite database %s: %s", sqlite_output_file, result.stderr)
        return False
    return bool(result.stdout.strip())
