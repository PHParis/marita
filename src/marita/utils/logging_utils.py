import logging
import os


def configure_global_logger(log_dir: str = "logs") -> logging.Logger:
    """Configure and return the shared MARITA logger."""
    os.makedirs(log_dir, exist_ok=True)
    logger = logging.getLogger("marita")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] - %(message)s")
    log_file = os.path.abspath(os.path.join(log_dir, "global.log"))

    existing_file_handlers = [h for h in logger.handlers if isinstance(h, logging.FileHandler)]
    current_log_handler = None
    for handler in existing_file_handlers:
        if os.path.abspath(handler.baseFilename) == log_file:
            current_log_handler = handler
        else:
            logger.removeHandler(handler)
            handler.close()

    if current_log_handler is None:
        current_log_handler = logging.FileHandler(log_file)
        current_log_handler.setFormatter(formatter)
        logger.addHandler(current_log_handler)

    if not any(
        isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler) for h in logger.handlers
    ):
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    return logger
