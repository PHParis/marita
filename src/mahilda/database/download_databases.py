from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import tempfile
from logging.handlers import RotatingFileHandler
from pathlib import Path
from shutil import which

import colorama
import requests
from bs4 import BeautifulSoup
from colorama import Fore, Style
from requests.adapters import HTTPAdapter, Retry
from tqdm import tqdm

colorama.init(autoreset=True)


class ColoredFormatter(logging.Formatter):
    LEVEL_COLORS = {
        logging.DEBUG: Fore.CYAN,
        logging.INFO: Fore.GREEN,
        logging.WARNING: Fore.YELLOW,
        logging.ERROR: Fore.RED,
        logging.CRITICAL: Fore.MAGENTA,
    }

    def format(self, record: logging.LogRecord) -> str:
        color = self.LEVEL_COLORS.get(record.levelno, Fore.WHITE)
        record.msg = f"{color}{record.msg}{Style.RESET_ALL}"
        return super().format(record)


def _build_logger() -> logging.Logger:
    logger = logging.getLogger(__name__)
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    formatter = ColoredFormatter("%(asctime)s - %(levelname)s - %(message)s")

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    file_handler = RotatingFileHandler("download_databases.log", maxBytes=5 * 1024 * 1024, backupCount=5)
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.INFO)
    logger.addHandler(file_handler)
    return logger


logger = _build_logger()

BASE_URL = os.getenv("BASE_URL", "https://relational.fel.cvut.cz")
HOSTNAME = os.getenv("HOSTNAME", "relational.fel.cvut.cz")
USERNAME = os.getenv("USERNAME", "guest")
PASSWORD = os.getenv("PASSWORD", "ctu-relational")
PORT = int(os.getenv("PORT", "3306"))


def run_cmd(cmd: list[str], output_file: str, timeout: int = 300) -> bool:
    """Run command and write stdout to file."""
    try:
        with open(output_file, "w") as file_out:
            result = subprocess.run(
                cmd,
                check=True,
                stdout=file_out,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout,
            )

        masked_cmd = " ".join(part if not part.startswith("-p") else "-p****" for part in cmd)
        logger.info(f"Executed command: {masked_cmd}")
        if result.stderr:
            logger.debug(result.stderr)
        return True
    except subprocess.TimeoutExpired:
        logger.error(f"Command timed out after {timeout} seconds: {' '.join(cmd)}")
        return False
    except subprocess.CalledProcessError as err:
        error_message = re.sub(r"(-p)\S+", r"\1****", err.stderr or "")
        logger.error(f"Command failed: {error_message}")
        return False


def check_mysqldump_version() -> str | None:
    """Return mysqldump version string (major.minor.patch) if available."""
    try:
        result = subprocess.run(
            ["mysqldump", "--version"],
            check=True,
            capture_output=True,
            text=True,
        )
        version_output = result.stdout
        logger.info(f"mysqldump version: {version_output.strip()}")
        version_match = re.search(r"Ver (\d+\.\d+\.\d+)", version_output)
        if version_match:
            return version_match.group(1)
        logger.warning("Unable to parse mysqldump version")
        return None
    except Exception as err:
        logger.error(f"Failed to check mysqldump version: {err}")
        return None


def dump_database(host: str, port: int, user: str, password: str, database: str, out_file: str) -> bool:
    """Use mysqldump to export one database into an SQL file."""
    version = check_mysqldump_version()
    if not version:
        logger.error("Unable to determine mysqldump version. Aborting.")
        return False

    major, minor, _patch = map(int, version.split("."))
    if major > 8 or (major == 8 and minor > 4):
        logger.error("mysqldump version must be 8.4 or lower to ensure compatibility.")
        return False

    cmd = [
        "mysqldump",
        "--single-transaction",
        "--no-tablespaces",
        "--skip-lock-tables",
        "--set-gtid-purged=OFF",
        "-h",
        host,
        "-P",
        str(port),
        "-u",
        user,
        f"-p{password}",
        database,
    ]
    if not run_cmd(cmd, out_file, timeout=300):
        logger.error(f"Failed to dump database: {database}")
        return False

    if os.path.getsize(out_file) == 0:
        logger.error(f"The SQL dump file is empty: {out_file}")
        try:
            with open(out_file) as file_in:
                sample = "".join(next(file_in) for _ in range(5))
            logger.debug(f"First lines of empty SQL dump:\n{sample}")
        except Exception:
            logger.debug("Unable to read SQL dump sample for diagnostics")
        return False

    logger.info(f"Database dumped successfully: {out_file}")
    return True


class DatabaseDownloader:
    def __init__(self, download_path: str):
        self.download_path = download_path
        self._validate_download_path()

        self.session = requests.Session()
        retries = Retry(total=5, backoff_factor=0.3, status_forcelist=[500, 502, 504])
        adapter = HTTPAdapter(max_retries=retries)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    def _validate_download_path(self) -> None:
        path = Path(self.download_path)
        path.mkdir(parents=True, exist_ok=True)

    def fetch_available_databases(self) -> list[str]:
        """Fetch available databases from relational-data website."""
        try:
            response = self.session.get(f"{BASE_URL}/search", auth=(USERNAME, PASSWORD), timeout=10)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            db_links = soup.find_all("a", href=re.compile("/dataset/"))
            databases: list[str] = []

            for link in tqdm(db_links, desc="Fetching database names", unit="database"):
                href = link.get("href")
                if not href:
                    continue
                display_name = link.text.strip()
                dataset_url = f"{BASE_URL}{href}"

                dataset_response = self.session.get(dataset_url, auth=(USERNAME, PASSWORD), timeout=10)
                dataset_response.raise_for_status()
                dataset_html = dataset_response.text

                name_match = re.search(r'Export\s+"\s*</span>\s*<span[^>]*>\s*([^<]+?)\s*</span>', dataset_html)
                if name_match is None:
                    logger.warning(f"Actual database name not found for {display_name}")
                    continue

                actual_name = name_match.group(1).strip()
                if actual_name:
                    databases.append(actual_name)

            logger.info(f"Fetched {len(databases)} databases")
            return databases
        except requests.RequestException as err:
            logger.error(f"HTTP request failed: {err}")
            return []
        except Exception as err:
            logger.error(f"Failed to fetch database list: {err}")
            return []

    def download_database(self, db_name: str) -> str | None:
        """Download one database as an SQL dump."""
        try:
            db_file = os.path.join(self.download_path, f"{db_name}.db")
            if os.path.exists(db_file):
                logger.info(f"SQLite database already exists for {db_name}: {db_file}. Skipping.")
                return None

            sql_file = os.path.join(self.download_path, f"{db_name}.sql")
            if os.path.exists(sql_file):
                logger.info(f"SQL file already exists for {db_name}: {sql_file}. Skipping download.")
                return sql_file

            if dump_database(HOSTNAME, PORT, USERNAME, PASSWORD, db_name, sql_file):
                return sql_file
            return None
        except Exception as err:
            logger.error(f"Failed to dump database {db_name}: {err}")
            return None


class DatabaseConverter:
    @staticmethod
    def sanitize_file_content(file_path: str) -> str:
        with open(file_path, encoding="latin1") as original_file:
            content = original_file.read()
        sanitized_content = content.replace("-", "_")

        with tempfile.NamedTemporaryFile(delete=False, mode="w", suffix=".sql") as temp_file:
            temp_file.write(sanitized_content)
            return temp_file.name

    @staticmethod
    def _validate_sql_file(sql_file: str) -> bool:
        try:
            with open(sql_file, encoding="latin1") as file_in:
                content = file_in.read()
            if not content.strip():
                logger.error("SQL file is empty.")
                return False
            if "CREATE TABLE" not in content.upper():
                logger.error("SQL file does not contain any CREATE TABLE statements.")
                return False
            return True
        except Exception as err:
            logger.error(f"Error during SQL file validation: {err}")
            return False

    @staticmethod
    def _verify_sqlite_database(sqlite_output_file: str) -> bool:
        try:
            result = subprocess.run(
                ["sqlite3", sqlite_output_file, "SELECT name FROM sqlite_master WHERE type='table' LIMIT 1;"],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                logger.error(f"Failed to verify database: {result.stderr}")
                return False
            return bool(result.stdout.strip())
        except Exception as err:
            logger.error(f"Error during database verification: {err}")
            return False

    @staticmethod
    def convert_with_shell_script(mysql_input_file: str, sqlite_output_file: str, script: str) -> bool:
        if not os.path.isfile(script):
            logger.error(f"Shell script not found: {script}")
            return False
        if not os.access(script, os.X_OK):
            logger.error(f"Shell script is not executable: {script}")
            return False
        if not DatabaseConverter._validate_sql_file(mysql_input_file):
            return False

        temp_file_path = None
        try:
            temp_file_path = DatabaseConverter.sanitize_file_content(mysql_input_file)
            script_proc = subprocess.run(
                [script, temp_file_path],
                capture_output=True,
                text=True,
                check=False,
            )
            if script_proc.returncode != 0:
                logger.error(f"Shell script failed: {script_proc.stderr}")
                return False

            sqlite_proc = subprocess.run(
                ["sqlite3", sqlite_output_file],
                input=script_proc.stdout,
                capture_output=True,
                text=True,
                check=False,
            )
            if sqlite_proc.returncode != 0:
                logger.error(f"SQLite conversion failed: {sqlite_proc.stderr}")
                return False

            return DatabaseConverter._verify_sqlite_database(sqlite_output_file)
        except Exception as err:
            logger.error(f"Unexpected error during shell script conversion: {err}")
            return False
        finally:
            if temp_file_path and os.path.exists(temp_file_path):
                os.remove(temp_file_path)

    @staticmethod
    def convert_with_manual_adjustments(mysql_input_file: str, sqlite_output_file: str) -> bool:
        if not DatabaseConverter._validate_sql_file(mysql_input_file):
            return False

        adjusted_file = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, mode="w", suffix=".sql") as temp_out:
                adjusted_file = temp_out.name
                with open(mysql_input_file) as infile:
                    for line in infile:
                        line = line.replace("ENGINE=InnoDB", "")
                        line = line.replace("AUTO_INCREMENT", "AUTOINCREMENT")
                        line = line.replace("`", '"')
                        line = re.sub(r"LOCK TABLES.*?;", "", line, flags=re.IGNORECASE)
                        line = re.sub(r"UNLOCK TABLES;", "", line, flags=re.IGNORECASE)
                        line = re.sub(r"\bunsigned\b", "", line, flags=re.IGNORECASE)
                        line = re.sub(r',\s*KEY\s+"[^"]+",?', "", line, flags=re.IGNORECASE)
                        temp_out.write(line)

            with open(adjusted_file) as infile:
                result = subprocess.run(
                    ["sqlite3", sqlite_output_file],
                    stdin=infile,
                    capture_output=True,
                    text=True,
                    check=False,
                )
            if result.returncode != 0:
                logger.error(f"SQLite command failed: {result.stderr}")
                return False

            return DatabaseConverter._verify_sqlite_database(sqlite_output_file)
        except Exception as err:
            logger.error(f"Error during manual adjustment conversion: {err}")
            return False
        finally:
            if adjusted_file and os.path.exists(adjusted_file):
                os.remove(adjusted_file)

    @staticmethod
    def convert_with_regex_adjustments(mysql_input_file: str, sqlite_output_file: str) -> bool:
        if not DatabaseConverter._validate_sql_file(mysql_input_file):
            return False

        adjusted_file = None
        try:
            with open(mysql_input_file) as infile:
                content = infile.read()

            content = re.sub(r"^__.*\n", "", content, flags=re.MULTILINE)
            content = re.sub(r"ENGINE=\w+", "", content, flags=re.IGNORECASE)
            content = re.sub(r"AUTO_INCREMENT\s*=\s*\d+", "AUTOINCREMENT", content, flags=re.IGNORECASE)
            content = content.replace("`", '"')
            content = re.sub(r"LOCK TABLES.*?;", "", content, flags=re.IGNORECASE)
            content = re.sub(r"UNLOCK TABLES;", "", content, flags=re.IGNORECASE)
            content = re.sub(r"\bunsigned\b", "", content, flags=re.IGNORECASE)
            content = re.sub(r"PRIMARY KEY\s*\(([^)]+)\)\s*\(([^)]+)\)", r"PRIMARY KEY (\1, \2)", content)
            content = content.replace("\\'", "'")
            content = re.sub(r"^UN\s*/\*!40103 SET TIME_ZONE=@OLD_TIME_ZONE \*/;", "", content, flags=re.MULTILINE)
            content = re.sub(r"DEFAULT CHARSET=\w+mb\d+ COLLATE=\w+", "", content, flags=re.IGNORECASE)

            with tempfile.NamedTemporaryFile(delete=False, mode="w", suffix=".sql") as temp_out:
                adjusted_file = temp_out.name
                temp_out.write(content)

            with open(adjusted_file) as infile:
                result = subprocess.run(
                    ["sqlite3", sqlite_output_file],
                    stdin=infile,
                    capture_output=True,
                    text=True,
                    check=False,
                )
            if result.returncode != 0:
                logger.error(f"SQLite command failed: {result.stderr}")
                return False

            return DatabaseConverter._verify_sqlite_database(sqlite_output_file)
        except Exception as err:
            logger.error(f"Error during regex adjustment conversion: {err}")
            return False
        finally:
            if adjusted_file and os.path.exists(adjusted_file):
                os.remove(adjusted_file)

    @staticmethod
    def convert_mysql_to_sqlite(mysql_input_file: str, sqlite_output_file: str) -> bool:
        current_file_absolutepath = os.path.dirname(os.path.abspath(__file__))
        methods = [
            lambda: DatabaseConverter.convert_with_shell_script(
                mysql_input_file,
                sqlite_output_file,
                os.path.join(current_file_absolutepath, "convert_sql_sqlite3.sh"),
            ),
            lambda: DatabaseConverter.convert_with_manual_adjustments(mysql_input_file, sqlite_output_file),
            lambda: DatabaseConverter.convert_with_regex_adjustments(mysql_input_file, sqlite_output_file),
        ]

        for idx, method in enumerate(methods, start=1):
            logger.info(f"Attempting conversion method {idx}...")
            try:
                if method():
                    logger.info(f"Conversion successful using method {idx}.")
                    return True
            except Exception as err:
                logger.error(f"Conversion method {idx} failed with error: {err}")

        logger.error("All conversion methods failed.")
        if os.path.exists(sqlite_output_file):
            try:
                os.remove(sqlite_output_file)
                logger.info(f"Removed {sqlite_output_file} due to failed conversion attempts.")
            except OSError as err:
                logger.error(f"Error removing file {sqlite_output_file}: {err}")
        return False

    @staticmethod
    def _check_dependency(executable: str) -> bool:
        if which(executable) is None:
            logger.error(f"Dependency not found: {executable}. Please install it and ensure it's in PATH.")
            return False
        return True

    @classmethod
    def check_all_dependencies(cls) -> bool:
        dependencies = ["mysqldump", "sqlite3"]
        return all(cls._check_dependency(dep) for dep in dependencies)


class Workflow:
    def __init__(self, download_path: str):
        self.downloader = DatabaseDownloader(download_path)
        if not DatabaseConverter.check_all_dependencies():
            logger.error("Missing dependencies. Aborting workflow.")
            sys.exit(1)
        self.successes: list[str] = []
        self.failures: list[tuple[str, str]] = []

    def run(self) -> None:
        databases = self.downloader.fetch_available_databases()

        for db_name in tqdm(databases, desc="Processing databases"):
            try:
                sqlite_file = os.path.join(self.downloader.download_path, f"{db_name}.db")
                if os.path.exists(sqlite_file):
                    logger.info(f"SQLite file for {db_name} already exists. Skipping.")
                    continue

                sql_file = self.downloader.download_database(db_name)
                if not sql_file:
                    logger.warning(f"Skipping {db_name} due to dump failure.")
                    continue

                if DatabaseConverter.convert_mysql_to_sqlite(sql_file, sqlite_file):
                    logger.info(f"Conversion reussie pour {db_name}.")
                    self.successes.append(db_name)
                else:
                    logger.warning(f"Conversion echouee pour {db_name}.")
                    self.failures.append((db_name, "Echec de la conversion"))
            except Exception:
                logger.error(f"Error processing {db_name}: An unknown error occurred.")
                self.failures.append((db_name, "Erreur inconnue"))

        report = (
            "Rapport de conversion des bases de donnees\n\n"
            f"Succes ({len(self.successes)}):\n"
            + "\n".join(self.successes)
            + f"\n\nEchecs ({len(self.failures)}):\n"
            + "\n".join(f"{db}: {reason}" for db, reason in self.failures)
        )
        report_path = os.path.join(self.downloader.download_path, "conversion_report.txt")
        with open(report_path, "w") as report_file:
            report_file.write(report)
        logger.info(f"Rapport de conversion genere: {report_path}")


if __name__ == "__main__":
    download_path = "databases/" if len(sys.argv) != 2 else sys.argv[1]
    workflow = Workflow(download_path)
    workflow.run()
