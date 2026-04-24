import logging
import os

# Create a logs directory if it doesn't exist

# logging.basicConfig(
#     level=logging.DEBUG,
#     format="%(asctime)s [%(levelname)s] - %(message)s",
#     datefmt="%Y-%m-%d %H:%M:%S",
#     handlers=[
#         logging.FileHandler("logs/project.log", mode="w"),
#         # logging.StreamHandler(),
#     ],
# )


from logging.handlers import RotatingFileHandler

def setup_loggers(log_dir: str | None = None):
    """
    Sets up two loggers:
    1. 'query_time' for logging query execution time and SQL statements.
    2. 'query_results' for logging the results of the queries.
    """
    resolved_log_dir = log_dir or os.environ.get("MAHILDA_LOG_DIR", "logs")
    os.makedirs(resolved_log_dir, exist_ok=True)

    # Logger for query execution time and SQL statements
    logger_query_time = logging.getLogger('query_time')
    logger_query_time.setLevel(logging.DEBUG)
    if logger_query_time.handlers:
        return

    file_handler_query_time = RotatingFileHandler(
        os.path.join(resolved_log_dir, "query_time.log"),
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
    )
    formatter_query_time = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    file_handler_query_time.setFormatter(formatter_query_time)
    logger_query_time.addHandler(file_handler_query_time)

    # Logger for query results
    logger_query_results = logging.getLogger('query_results')
    logger_query_results.setLevel(logging.INFO)
    file_handler_query_results = RotatingFileHandler(
        os.path.join(resolved_log_dir, "query_results.log"),
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
    )
    formatter_query_results = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    file_handler_query_results.setFormatter(formatter_query_results)
    logger_query_results.addHandler(file_handler_query_results)
