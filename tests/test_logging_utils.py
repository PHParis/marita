import logging
import os
from contextlib import nullcontext
from pathlib import Path

from marita.cli import run as run_cli
from marita.utils.config_loader import load_typed_config
from marita.utils.log_setup import setup_loggers
from marita.utils.logging_utils import configure_global_logger


def _reset_logger() -> None:
    logger = logging.getLogger("marita")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()


def _reset_query_loggers() -> None:
    for logger_name in ("query_time", "query_results"):
        logger = logging.getLogger(logger_name)
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()


def test_configure_global_logger_is_idempotent(tmp_path: Path) -> None:
    _reset_logger()
    log_dir = tmp_path / "logs"

    logger = configure_global_logger(str(log_dir))
    configure_global_logger(str(log_dir))
    configure_global_logger(str(log_dir))

    file_handlers = [handler for handler in logger.handlers if isinstance(handler, logging.FileHandler)]
    stream_handlers = [
        handler
        for handler in logger.handlers
        if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler)
    ]

    assert len(file_handlers) == 1
    assert len(stream_handlers) == 1


def test_configure_global_logger_replaces_file_handler_for_new_directory(tmp_path: Path) -> None:
    _reset_logger()
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"

    logger = configure_global_logger(str(first_dir))
    logger = configure_global_logger(str(second_dir))

    file_handlers = [handler for handler in logger.handlers if isinstance(handler, logging.FileHandler)]

    assert len(file_handlers) == 1
    assert Path(file_handlers[0].baseFilename).parent == second_dir


def test_repeated_run_command_does_not_duplicate_handlers(monkeypatch, tmp_path: Path) -> None:
    _reset_logger()
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "\n".join(
            [
                "database:",
                "  path: ./db",
                "  name: test.db",
                "logging:",
                "  log_dir: ./logs",
                "results:",
                "  output_dir: ./results",
                "algorithm:",
                "  name: MARITA",
                "mlflow:",
                "  use: false",
            ]
        ),
        encoding="utf-8",
    )

    typed_config = load_typed_config(str(config_path))

    class FakeMonitor:
        should_stop = False

        def __init__(self, threshold: int, timeout: int) -> None:
            del threshold, timeout

        def monitor(self) -> None:
            return

        def stop(self) -> None:
            return

    class FakeProcessor:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

        def discover_rules(self) -> int:
            return 0

        def clean_up(self) -> None:
            return

    monkeypatch.setattr(run_cli, "load_typed_config", lambda _: typed_config)
    monkeypatch.setattr(run_cli, "ResourceMonitor", FakeMonitor)
    monkeypatch.setattr(run_cli, "DatabaseProcessor", FakeProcessor)
    monkeypatch.setattr(run_cli, "mlflow_run_context", lambda *_args, **_kwargs: nullcontext())

    assert run_cli.main(["--config", str(config_path)]) == 0
    logger = logging.getLogger("marita")
    first_count = len(logger.handlers)
    assert first_count > 0

    assert run_cli.main(["--config", str(config_path)]) == 0
    assert len(logger.handlers) == first_count


def test_setup_loggers_is_idempotent_for_same_directory(tmp_path: Path) -> None:
    _reset_query_loggers()
    log_dir = tmp_path / "logs"

    setup_loggers(str(log_dir))
    setup_loggers(str(log_dir))

    query_time = logging.getLogger("query_time")
    query_results = logging.getLogger("query_results")

    query_time_files = [handler for handler in query_time.handlers if isinstance(handler, logging.FileHandler)]
    query_results_files = [handler for handler in query_results.handlers if isinstance(handler, logging.FileHandler)]

    assert len(query_time_files) == 1
    assert len(query_results_files) == 1


def test_setup_loggers_rebinds_handlers_when_log_dir_changes(tmp_path: Path) -> None:
    _reset_query_loggers()
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"

    setup_loggers(str(first_dir))
    setup_loggers(str(second_dir))

    query_time = logging.getLogger("query_time")
    query_results = logging.getLogger("query_results")

    query_time_files = [handler for handler in query_time.handlers if isinstance(handler, logging.FileHandler)]
    query_results_files = [handler for handler in query_results.handlers if isinstance(handler, logging.FileHandler)]

    assert len(query_time_files) == 1
    assert len(query_results_files) == 1
    assert Path(query_time_files[0].baseFilename).parent == second_dir
    assert Path(query_results_files[0].baseFilename).parent == second_dir


def test_setup_loggers_follows_env_log_dir_between_invocations(tmp_path: Path) -> None:
    _reset_query_loggers()
    first_dir = tmp_path / "env_first"
    second_dir = tmp_path / "env_second"
    previous = os.environ.get("MARITA_LOG_DIR")

    try:
        os.environ["MARITA_LOG_DIR"] = str(first_dir)
        setup_loggers()

        os.environ["MARITA_LOG_DIR"] = str(second_dir)
        setup_loggers()
    finally:
        if previous is None:
            os.environ.pop("MARITA_LOG_DIR", None)
        else:
            os.environ["MARITA_LOG_DIR"] = previous

    query_time = logging.getLogger("query_time")
    query_time_files = [handler for handler in query_time.handlers if isinstance(handler, logging.FileHandler)]
    assert len(query_time_files) == 1
    assert Path(query_time_files[0].baseFilename).parent == second_dir
