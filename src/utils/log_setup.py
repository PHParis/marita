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


import logging
from logging.handlers import RotatingFileHandler

def setup_loggers():
    """
    Sets up two loggers:
    1. 'query_time' for logging query execution time and SQL statements.
    2. 'query_results' for logging the results of the queries.
    """
    if not os.path.exists("logs"):
        os.makedirs("logs")

    # Logger for query execution time and SQL statements
    logger_query_time = logging.getLogger('query_time')
    logger_query_time.setLevel(logging.DEBUG)
    file_handler_query_time = RotatingFileHandler('logs/query_time.log', maxBytes=5 * 1024 * 1024, backupCount=5)
    formatter_query_time = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    file_handler_query_time.setFormatter(formatter_query_time)
    logger_query_time.addHandler(file_handler_query_time)

    # Logger for query results
    logger_query_results = logging.getLogger('query_results')
    logger_query_results.setLevel(logging.INFO)
    file_handler_query_results = RotatingFileHandler('logs/query_results.log', maxBytes=5 * 1024 * 1024, backupCount=5)
    formatter_query_results = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    file_handler_query_results.setFormatter(formatter_query_results)
    logger_query_results.addHandler(file_handler_query_results)
