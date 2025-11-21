import logging
import os

def configure_global_logger(log_dir: str = "logs") -> logging.Logger:
    """Configures a global logger."""
    os.makedirs(log_dir, exist_ok=True)
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    log_file = os.path.join(log_dir, "global.log")
    file_handler = logging.FileHandler(log_file)
    file_formatter = logging.Formatter('%(asctime)s [%(levelname)s] - %(message)s')
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_formatter = logging.Formatter('%(asctime)s [%(levelname)s] - %(message)s')
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)

    return logger
