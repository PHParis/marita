import logging
import os

from logging.handlers import RotatingFileHandler


def _configure_rotating_file_handler(
    logger: logging.Logger,
    *,
    log_path: str,
    level: int,
    formatter: logging.Formatter,
) -> None:
    logger.setLevel(level)

    absolute_log_path = os.path.abspath(log_path)
    matching_handler: RotatingFileHandler | None = None

    for handler in list(logger.handlers):
        if not isinstance(handler, RotatingFileHandler):
            continue
        if os.path.abspath(handler.baseFilename) == absolute_log_path and matching_handler is None:
            matching_handler = handler
            continue
        logger.removeHandler(handler)
        handler.close()

    if matching_handler is None:
        file_handler = RotatingFileHandler(
            absolute_log_path,
            maxBytes=5 * 1024 * 1024,
            backupCount=5,
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)


def setup_loggers(log_dir: str | None = None):
    """
    Sets up two loggers:
    1. 'query_time' for logging query execution time and SQL statements.
    2. 'query_results' for logging the results of the queries.
    """
    resolved_log_dir = log_dir or os.environ.get("MAHILDA_LOG_DIR", "logs")
    os.makedirs(resolved_log_dir, exist_ok=True)

    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    logger_query_time = logging.getLogger("query_time")
    _configure_rotating_file_handler(
        logger_query_time,
        log_path=os.path.join(resolved_log_dir, "query_time.log"),
        level=logging.DEBUG,
        formatter=formatter,
    )

    logger_query_results = logging.getLogger("query_results")
    _configure_rotating_file_handler(
        logger_query_results,
        log_path=os.path.join(resolved_log_dir, "query_results.log"),
        level=logging.INFO,
        formatter=formatter,
    )
