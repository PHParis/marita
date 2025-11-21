import logging
from typing import Optional, List, Tuple
import os
import json
import re
import sqlite3
import requests
from requests.adapters import HTTPAdapter, Retry  # Added imports for retries
from bs4 import BeautifulSoup
from tqdm import tqdm
import subprocess
import colorama
from colorama import Fore, Style
import tempfile  # Added missing import
from logging.handlers import RotatingFileHandler

colorama.init(autoreset=True)
# Set up logging with colors
class ColoredFormatter(logging.Formatter):
    LEVEL_COLORS = {
        logging.DEBUG: Fore.CYAN,
        logging.INFO: Fore.GREEN,
        logging.WARNING: Fore.YELLOW,
        logging.ERROR: Fore.RED,
        logging.CRITICAL: Fore.MAGENTA,
    }

    def format(self, record):
        color = self.LEVEL_COLORS.get(record.levelno, Fore.WHITE)
        record.msg = f"{color}{record.msg}{Style.RESET_ALL}"
        return super().format(record)

handler = logging.StreamHandler()
formatter = ColoredFormatter('%(asctime)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)

# Configure the logger once
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.addHandler(handler)

# Configure logging to a file with rotation and access restrictions
file_handler = RotatingFileHandler('download_databases.log', maxBytes=5*1024*1024, backupCount=5)
file_handler.setFormatter(formatter)
file_handler.setLevel(logging.INFO)
file_handler.baseFilename = 'download_databases.log'
file_handler.mode = 'a'
file_handler.stream = open('download_databases.log', 'a')
logger.addHandler(file_handler)

# Constants for the primary URL
BASE_URL_PRIMARY = os.getenv("BASE_URL_PRIMARY", "https://relational-data.org")
HOSTNAME_PRIMARY = os.getenv("HOSTNAME_PRIMARY", "db.relational-data.org")
USERNAME_PRIMARY = os.getenv("USERNAME_PRIMARY", "guest")
PASSWORD_PRIMARY = os.getenv("PASSWORD_PRIMARY", "relational")

# Constants for the secondary URL
BASE_URL = os.getenv("BASE_URL", "https://relational.fel.cvut.cz")
HOSTNAME = os.getenv("HOSTNAME", "relational.fel.cvut.cz")
USERNAME = os.getenv("USERNAME", "guest")
PASSWORD = os.getenv("PASSWORD", "ctu-relational")
PORT = int(os.getenv("PORT", "3306"))

def run_cmd(cmd: List[str], output_file: str, timeout: int = 300) -> bool:
    """Run a shell command and log its output."""
    try:
        with open(output_file, 'w') as file_out:
            result = subprocess.run(cmd, check=True, stdout=file_out, stderr=subprocess.PIPE, text=True, timeout=timeout)
        masked_cmd = ' '.join([part if not part.startswith('-p') else '-p****' for part in cmd])
        logger.info(f"Executed command: {masked_cmd}")
        
        # Log the stdout content if needed
        if result.stdout:
            logger.debug(f"mysqldump stdout: {result.stdout}")
        
        return True
    except subprocess.TimeoutExpired:
        logger.error(f"Command timed out after {timeout} seconds: {' '.join(cmd)}")
        return False
    except subprocess.CalledProcessError as e:
        # Masquer les informations sensibles dans les erreurs
        error_message = e.stderr
        error_message = re.sub(r'(-p)[^\s]+', r'\1****', error_message)
        logger.error(f"Command failed: {error_message}")
        return False

def check_mysqldump_version() -> Optional[str]:
    """Check the mysqldump version."""
    try:
        result = subprocess.run("mysqldump --version", shell=True, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        version_output = result.stdout.decode()
        logger.info(f"mysqldump version: {version_output}")
        version_match = re.search(r"Ver (\d+\.\d+\.\d+)", version_output)
        if version_match:
            return version_match.group(1)
        else:
            logger.warning("Unable to parse mysqldump version.")
            return None
    except Exception as e:
        logger.error(f"Failed to check mysqldump version: {e}")
        return None

def dump_database(host: str, port: int, user: str, password: str, database: str, out_file: str) -> bool:
    """Use mysqldump to dump the database into an SQL file."""
    mysqldump_version = check_mysqldump_version()
    if mysqldump_version:
        major, minor, patch = map(int, mysqldump_version.split('.'))
        if major < 8 or (major == 8 and minor <= 4):
            logger.info("mysqldump version is compatible.")
        else:
            logger.error("mysqldump version must be 8.4 or lower to ensure compatibility.")
            return False
    else:
        logger.error("Unable to determine mysqldump version. Aborting.")
        return False

    # Construire la commande en tant que liste pour éviter les problèmes d'échappement
    cmd = [
        "mysqldump",
        "--single-transaction",
        "--no-tablespaces",
        "--skip-lock-tables",
        "--set-gtid-purged=OFF",
        "-h", host,
        "-P", str(port),
        "-u", user,
        f"-p{password}",
        database
    ]
    # Passer la commande et le fichier de sortie à run_cmd
    if run_cmd(cmd, out_file, timeout=300):  # Timeout de 5 minutes
        # Vérifier si le fichier SQL est vide
        if os.path.getsize(out_file) == 0:
            logger.error(f"The SQL dump file is empty: {out_file}")
            
            # Lire les premières lignes du fichier pour diagnostic
            try:
                with open(out_file, 'r') as f:
                    sample = ''.join([next(f) for _ in range(5)])
                logger.debug(f"First lines of empty SQL dump:\n{sample}")
            except Exception as e:
                logger.error(f"Failed to read dump file for diagnostics: {e}")
            
            return False
        logger.info(f"Database dumped successfully: {out_file}")
        return True
    else:
        logger.error(f"Failed to dump database: {database}")
        return False

class DatabaseDownloader:
    def __init__(self, download_path: str):
        self.download_path = download_path
        self._validate_download_path()
        # Configure a session with retry mechanisms
        self.session = requests.Session()
        retries = Retry(total=5, backoff_factor=0.3, status_forcelist=[500, 502, 504])
        adapter = HTTPAdapter(max_retries=retries)
        self.session.mount('http://', adapter)
        self.session.mount('https://', adapter)

    def _validate_download_path(self):
        """Ensure the download path exists."""
        if not os.path.exists(self.download_path):
            try:
                os.makedirs(self.download_path)
                logger.info(f"Created download path: {self.download_path}")
            except Exception as e:
                logger.error(f"Failed to create download path: {e}")
                raise

    def fetch_available_databases(self) -> List[str]:  # Corrected type annotation
        """Fetch the list of available databases and their actual names from the website."""
        try:
            response = self.session.get(f"{BASE_URL}/search", auth=(USERNAME, PASSWORD), timeout=10)  # Updated to use self.session
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            db_links = soup.find_all("a", href=re.compile("/dataset/"))
            databases = []

            # Wrap the iteration with tqdm for progress tracking
            for link in tqdm(db_links, desc="Fetching database names from relational-databases.org", unit="database"):
                display_name = link.text.strip()
                dataset_url = f"{BASE_URL}{link['href']}"
                # Fetch the dataset page to find the actual database name
                dataset_response = self.session.get(dataset_url, auth=(USERNAME, PASSWORD), timeout=10)  # Updated to use self.session
                dataset_response.raise_for_status()
                soup = BeautifulSoup(dataset_response.text, 'html.parser')
                export_span = soup.find('span', string='Export "')

                if not export_span:
                    # Try finding the span by the data-reactid attribute as a fallback
                    export_span = soup.find('span', attrs={'data-reactid': True}, string='Export "')

                    if not export_span:
                        logger.warning(f"'Export \"' span not found for {display_name}. Skipping database.")
                        continue  # Skip to the next database

                actual_name_element = export_span.find_next_sibling('span')

                if actual_name_element:
                    actual_name = actual_name_element.text.strip()
                    databases.append(actual_name)
                else:
                    logger.warning(f"Actual database name not found for {display_name}")

            logger.info(f"Fetched databases: {databases}")
            return databases
        except requests.exceptions.RequestException as e:  # Handle specific request exceptions
            logger.error(f"HTTP request failed: {e}")
            return []
        except Exception as e:
            logger.error(f"Failed to fetch database list: {e}")
            return []

    def download_database(self, db_name: str) -> Optional[str]:
        """Dump the database using mysqldump.

        Args:
            db_name (str): The name of the database.

        Returns:
            Optional[str]: The full path to the dumped database file, or None on failure.
        """
        try:
            logger.info(f"Starting dump for database: {db_name}")

            db_file = os.path.join(self.download_path, f"{db_name}.db")
            if os.path.exists(db_file):
                logger.info(f"SQLite database already exists for {db_name}: {db_file}. Skipping download and conversion.")
                return None

            file_path = os.path.join(self.download_path, f"{db_name}.sql")
            if os.path.exists(file_path):
                logger.info(f"SQL file already exists for {db_name}: {file_path}. Skipping download.")
                return file_path

            if dump_database(HOSTNAME, PORT, USERNAME, PASSWORD, db_name, file_path):
                logger.info(f"Database dumped successfully: {file_path}")
                return file_path
            else:
                logger.error(f"Failed to dump database: {db_name}")
                return None
        except Exception as e:
            logger.error(f"Failed to dump database {db_name}: {e}")
            return None

class DatabaseConverter:
    @staticmethod
    def sanitize_file_content(file_path: str) -> str:
        """
        Sanitize the content of the file by removing dashes and save it to a temporary file.
        """
        try:
            with open(file_path, "r", encoding="latin1") as original_file:  # Changed encoding
                content = original_file.read()

            # Remove dashes
            sanitized_content = re.sub(r"-", "_", content)

            # Write sanitized content to a temporary file using a context manager
            with tempfile.NamedTemporaryFile(delete=False, mode="w", suffix=".sql") as temp_file:
                temp_file.write(sanitized_content)
                temp_file_path = temp_file.name

            logger.info(f"Sanitized file content saved to temporary file: {temp_file_path}")
            return temp_file_path
        except Exception as e:
            logger.error(f"Error sanitizing file content: {str(e)}")
            raise

    @staticmethod
    def convert_with_shell_script(mysql_input_file: str, sqlite_output_file: str, script: str) -> bool:
        """
        Convert using a provided shell script.
        """
        temp_file_path = None
        try:
            if not os.path.isfile(script):
                logger.error(f"Shell script not found: {script}")
                return False
            if not os.access(script, os.X_OK):
                logger.error(f"Shell script is not executable: {script}")
                return False

            # Validate SQL file before conversion
            if not DatabaseConverter._validate_sql_file(mysql_input_file):
                logger.error(f"SQL file validation failed: {mysql_input_file}")
                return False

            # Sanitize the content of the MySQL input file
            temp_file_path = DatabaseConverter.sanitize_file_content(mysql_input_file)

            cmd = [script, temp_file_path]
            sqlite_cmd = ["sqlite3", sqlite_output_file]

            logger.info(f"Running script: {cmd} | {sqlite_cmd}")

            # Exécuter le script shell
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            stdout, stderr = proc.communicate()

            if proc.returncode != 0:
                logger.error(f"Shell script failed: {stderr}")
                return False

            # Exécuter la commande sqlite3 sans redirection shell
            with open(temp_file_path, "r") as infile:
                sqlite_proc = subprocess.run(
                    sqlite_cmd,
                    stdin=infile,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True
                )

            if sqlite_proc.returncode != 0:
                logger.error(f"SQLite conversion failed: {sqlite_proc.stderr}")
                return False

            logger.info("Conversion successful using shell script.")
            return DatabaseConverter._verify_sqlite_database(sqlite_output_file)

        except subprocess.SubprocessError as e:
            logger.error(f"Subprocess error during shell script conversion: {str(e)}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error during shell script conversion: {str(e)}")
            return False
        finally:
            # Clean up the temporary file
            if temp_file_path and os.path.exists(temp_file_path):
                try:
                    os.remove(temp_file_path)
                    logger.info(f"Temporary sanitized file removed: {temp_file_path}")
                except OSError as e:
                    logger.error(f"Error removing temporary file {temp_file_path}: {str(e)}")

    @staticmethod
    def _validate_sql_file(sql_file: str) -> bool:
        """
        Validate that the SQL file is not empty and contains at least one CREATE statement.
        """
        try:
            with open(sql_file, "r", encoding="latin1") as file:  # Changed encoding
                content = file.read()
                if not content.strip():
                    logger.error("SQL file is empty.")
                    return False
                if "CREATE TABLE" not in content.upper():
                    logger.error("SQL file does not contain any CREATE TABLE statements.")
                    return False
            logger.info("SQL file validation successful.")
            return True
        except PermissionError:
            logger.error(f"Permission denied while accessing the SQL file: {sql_file}")
            return False
        except Exception as e:
            logger.error(f"Error during SQL file validation: {e}")
            return False

    @staticmethod
    def _verify_sqlite_database(sqlite_output_file: str) -> bool:
        """
        Verify that the SQLite database is not empty and contains at least one table.
        """
        try:
            cmd = f"sqlite3 {sqlite_output_file} \"SELECT name FROM sqlite_master WHERE type='table' LIMIT 1;\""
            logger.debug(f"Verifying database with command: {cmd}")

            result = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            output = result.stdout.strip()

            if result.returncode != 0:
                logger.error(f"Failed to verify database: {result.stderr}")
                return False

            if output:  # If there's at least one table name returned
                logger.info("Database verification successful: at least one table found.")
                return True
            else:
                logger.error("Database verification failed: no tables found in the SQLite database.")
                return False
        except Exception as e:
            logger.error(f"Error during database verification: {str(e)}")
            return False

    @staticmethod
    def convert_with_manual_adjustments(mysql_input_file: str, sqlite_output_file: str) -> bool:
        """
        Convert manually by adjusting the SQL dump for SQLite compatibility.
        """
        try:
            adjusted_file = "adjusted_dump.sql"

            # Validate SQL file before adjustments
            if not DatabaseConverter._validate_sql_file(mysql_input_file):
                logger.error(f"SQL file validation failed: {mysql_input_file}")
                return False

            logger.info("Adjusting MySQL dump for SQLite compatibility.")

            with open(mysql_input_file, "r") as infile, open(adjusted_file, "w") as outfile:
                for line in infile:
                    line = line.replace("ENGINE=InnoDB", "")
                    line = line.replace("AUTO_INCREMENT", "AUTOINCREMENT")
                    line = line.replace("`", "\"")
                    # Supprimer les instructions LOCK TABLES et UNLOCK TABLES
                    line = re.sub(r'LOCK TABLES.*?;', '', line, flags=re.IGNORECASE)
                    line = re.sub(r'UNLOCK TABLES;', '', line, flags=re.IGNORECASE)
                    # Supprimer le mot-clé 'unsigned'
                    line = re.sub(r'\bunsigned\b', '', line, flags=re.IGNORECASE)
                    # Supprimer les définitions KEY
                    line = re.sub(r',\s*KEY\s+"[^"]+",?', '', line, flags=re.IGNORECASE)
                    outfile.write(line)

            logger.info(f"Adjusted SQL dump written to {adjusted_file}")

            cmd = ["sqlite3", sqlite_output_file]
            with open(adjusted_file, "r") as infile:
                result = subprocess.run(
                    cmd,
                    stdin=infile,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True
                )
            
            if result.returncode != 0:
                logger.error(f"SQLite command failed: {result.stderr}")
                return False

            logger.info("Conversion successful using manual adjustments.")
            return DatabaseConverter._verify_sqlite_database(sqlite_output_file)

        except subprocess.CalledProcessError as e:
            logger.error(f"SQLite command failed during manual adjustments: {e.stderr}")
            return False
        except Exception as e:
            logger.error(f"Error during manual adjustment conversion: {str(e)}")
            return False
        finally:
            # Clean up the adjusted file if necessary
            if os.path.exists(adjusted_file):
                try:
                    os.remove(adjusted_file)
                    logger.info(f"Removed adjusted SQL dump file: {adjusted_file}")
                except OSError as e:
                    logger.error(f"Error removing adjusted dump file {adjusted_file}: {str(e)}")

    @staticmethod
    def convert_with_regex_adjustments(mysql_input_file: str, sqlite_output_file: str) -> bool:
        """
        Convert by applying regex-based adjustments to the SQL dump before loading it into SQLite.
        """
        try:
            adjusted_file = "regex_adjusted_dump.sql"

            # Validate SQL file before regex adjustments
            if not DatabaseConverter._validate_sql_file(mysql_input_file):
                logger.error(f"SQL file validation failed: {mysql_input_file}")
                return False

            logger.info("Adjusting MySQL dump for SQLite compatibility using regex substitutions.")

            with open(mysql_input_file, "r") as infile, open(adjusted_file, "w") as outfile:
                content = infile.read()

                # Supprimer les lignes de commentaire spécifiques à mysqldump
                content = re.sub(r'^__.*\n', '', content, flags=re.MULTILINE)

                # Remove ENGINE settings (e.g., ENGINE=InnoDB)
                content = re.sub(r'ENGINE=\w+', '', content, flags=re.IGNORECASE)

                # Replace AUTO_INCREMENT with AUTOINCREMENT
                content = re.sub(r'AUTO_INCREMENT\s*=\s*\d+', 'AUTOINCREMENT', content, flags=re.IGNORECASE)

                # Convert backticks to double quotes
                content = re.sub(r'`', '"', content)

                # Supprimer les instructions LOCK TABLES et UNLOCK TABLES
                content = re.sub(r'LOCK TABLES.*?;', '', content, flags=re.IGNORECASE)
                content = re.sub(r'UNLOCK TABLES;', '', content, flags=re.IGNORECASE)

                # Supprimer le mot-clé 'unsigned'
                content = re.sub(r'\bunsigned\b', '', content, flags=re.IGNORECASE)

                # Corriger les définitions de PRIMARY KEY avec multiples colonnes
                content = re.sub(
                    r'PRIMARY KEY\s*\(([^)]+)\)\s*\(([^)]+)\)',
                    r'PRIMARY KEY (\1, \2)',
                    content,
                    flags=re.IGNORECASE
                )

                # Gérer les apostrophes échappées
                content = re.sub(r"\\'", "'", content)

                # Supprimer les lignes de commandes MySQL spécifiques
                content = re.sub(r'^UN\s*/\*!40103 SET TIME_ZONE=@OLD_TIME_ZONE \*/;', '', content, flags=re.MULTILINE)

                # Supprimer les définitions DEFAULT CHARSET et COLLATE
                content = re.sub(r'DEFAULT CHARSET=\w+mb\d+ COLLATE=\w+', '', content, flags=re.IGNORECASE)

                outfile.write(content)

            logger.info(f"Regex adjusted SQL dump written to {adjusted_file}")

            cmd = ["sqlite3", sqlite_output_file]
            with open(adjusted_file, "r") as infile:
                result = subprocess.run(
                    cmd,
                    stdin=infile,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True
                )
            
            if result.returncode != 0:
                logger.error(f"SQLite command failed: {result.stderr}")
                return False

            logger.info("Conversion successful using regex adjustments.")
            return DatabaseConverter._verify_sqlite_database(sqlite_output_file)

        except subprocess.CalledProcessError as e:
            logger.error(f"SQLite command failed during regex adjustments: {e.stderr}")
            return False
        except Exception as e:
            logger.error(f"Error during regex adjustment conversion: {str(e)}")
            return False
        finally:
            # Clean up the adjusted file if necessary
            if os.path.exists(adjusted_file):
                try:
                    os.remove(adjusted_file)
                    logger.info(f"Removed regex adjusted SQL dump file: {adjusted_file}")
                except OSError as e:
                    logger.error(f"Error removing regex adjusted dump file {adjusted_file}: {str(e)}")

    @staticmethod
    def convert_mysql_to_sqlite(mysql_input_file: str, sqlite_output_file: str) -> bool:
        """
        Attempt multiple methods to convert a MySQL dump to SQLite.
        """
        current_file_absolutepath = os.path.dirname(os.path.abspath(__file__))

        methods = [
            lambda: DatabaseConverter.convert_with_shell_script(
                mysql_input_file, sqlite_output_file, os.path.join(current_file_absolutepath, "convert_sql_sqlite3.sh")
            ),
            lambda: DatabaseConverter.convert_with_manual_adjustments(mysql_input_file, sqlite_output_file),
            lambda: DatabaseConverter.convert_with_regex_adjustments(mysql_input_file, sqlite_output_file)
        ]

        for i, method in enumerate(methods, 1):
            logger.info(f"Attempting conversion method {i}...")
            try:
                if method():
                    logger.info(f"Conversion successful using method {i}.")
                    return True
            except Exception as e:
                logger.error(f"Conversion method {i} failed with error: {e}")
        
        logger.error("All conversion methods failed.")
        # If we reach here, no method worked. Attempt to remove the .db file.
        if os.path.exists(sqlite_output_file):
            try:
                os.remove(sqlite_output_file)
                logger.info(f"Removed {sqlite_output_file} due to failed conversion attempts.")
            except OSError as e:
                logger.error(f"Error removing file {sqlite_output_file}: {str(e)}")

        return False

    @staticmethod
    def _check_dependency(executable: str) -> bool:
        """
        Check if an executable is available in the system PATH.
        """
        from shutil import which
        if which(executable) is None:
            logger.error(f"Dependency not found: {executable}. Please install it and ensure it's in your PATH.")
            return False
        logger.info(f"Dependency found: {executable}.")
        return True

    @classmethod
    def check_all_dependencies(cls) -> bool:
        """
        Check all required dependencies before starting the workflow.
        """
        dependencies = ['mysqldump', 'sqlite3']
        all_found = True
        for dep in dependencies:
            if not cls._check_dependency(dep):
                all_found = False
        return all_found

class Workflow:
    def __init__(self, download_path: str):
        self.downloader = DatabaseDownloader(download_path)
        if not DatabaseConverter.check_all_dependencies():  # Added dependency check
            logger.error("Missing dependencies. Aborting workflow.")
            sys.exit(1)
        self.successes = []
        self.failures = []

    def run(self):
        """Run the workflow to download and convert databases."""
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
                    logger.info(f"Conversion réussie pour {db_name}.")
                    self.successes.append(db_name)
                else:
                    logger.warning(f"Conversion échouée pour {db_name}.")
                    self.failures.append((db_name, "Échec de la conversion"))
            except Exception as e:
                # Avoid logging sensitive information in exceptions
                logger.error(f"Error processing {db_name}: An unknown error occurred.")
                self.failures.append((db_name, "Erreur inconnue"))

        # Générer le rapport
        report = (
            f"Rapport de conversion des bases de données\n\n"
            f"Succès ({len(self.successes)}):\n" +
            "\n".join(self.successes) +
            f"\n\nÉchecs ({len(self.failures)}):\n" +
            "\n".join([f"{db}: {reason}" for db, reason in self.failures])
        )
        report_path = os.path.join(self.downloader.download_path, "conversion_report.txt")
        with open(report_path, "w") as report_file:
            report_file.write(report)
        logger.info(f"Rapport de conversion généré: {report_path}")

if __name__ == "__main__":
    import sys  # noqa
    if len(sys.argv) != 2:
        download_path = "databases/"
    else:
        download_path = sys.argv[1]
    workflow = Workflow(download_path)
    workflow.run()
