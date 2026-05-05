from __future__ import annotations

import logging
import os
import shlex
import signal
import subprocess
import threading
import time
from collections.abc import Sequence
from pathlib import Path

import psutil


def run_cmd(
    command: str | Sequence[str],
    *,
    timeout: int | None = None,
    memory_limit_gb: float | None = 30,
    stdout_path: str | Path | None = None,
    cwd: str | Path | None = None,
    logger: logging.Logger | None = None,
) -> bool:
    """Run a command with optional timeout, output redirection, and memory monitoring."""
    command_args, redirected_stdout = _normalise_command(command, stdout_path)
    if not command_args:
        _logger(logger).error("Cannot execute an empty command.")
        return False

    active_logger = _logger(logger)
    active_logger.info("Executing command: %s", " ".join(command_args))

    stdout_handle = None
    try:
        if redirected_stdout is not None:
            output_file = Path(redirected_stdout)
            output_file.parent.mkdir(parents=True, exist_ok=True)
            stdout_handle = output_file.open("w", encoding="utf-8")

        process = subprocess.Popen(
            command_args,
            stdout=stdout_handle if stdout_handle else subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=str(cwd) if cwd else None,
            start_new_session=True,
        )

        monitor_thread = _start_memory_monitor(
            pid=process.pid,
            process=process,
            memory_limit_gb=memory_limit_gb,
            logger=active_logger,
        )

        try:
            _, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            active_logger.error("Command timed out after %s seconds", timeout)
            _terminate_process_group(process)
            return False
        finally:
            if monitor_thread is not None:
                monitor_thread.join(timeout=1)

        if process.returncode == 0:
            active_logger.info("Command executed successfully.")
            return True

        stderr_text = (stderr or "").strip()
        if stderr_text:
            active_logger.error("Command failed with return code %s: %s", process.returncode, stderr_text)
        else:
            active_logger.error("Command failed with return code %s", process.returncode)
        return False
    except Exception:
        active_logger.exception("Unexpected error while executing command")
        return False
    finally:
        if stdout_handle is not None:
            stdout_handle.close()


def _logger(logger: logging.Logger | None) -> logging.Logger:
    return logger or logging.getLogger(__name__)


def _normalise_command(
    command: str | Sequence[str],
    stdout_path: str | Path | None,
) -> tuple[list[str], str | Path | None]:
    if isinstance(command, str):
        command_args = shlex.split(command)
    else:
        command_args = [str(arg) for arg in command]

    redirected_stdout = stdout_path
    if redirected_stdout is None and ">" in command_args:
        redirect_index = command_args.index(">")
        if redirect_index + 1 >= len(command_args):
            return [], None
        redirected_stdout = command_args[redirect_index + 1]
        command_args = command_args[:redirect_index]

    return command_args, redirected_stdout


def _start_memory_monitor(
    *,
    pid: int,
    process: subprocess.Popen[str],
    memory_limit_gb: float | None,
    logger: logging.Logger,
) -> threading.Thread | None:
    if memory_limit_gb is None:
        return None

    def monitor() -> None:
        try:
            monitored_process = psutil.Process(pid)
            while process.poll() is None:
                processes = [monitored_process] + monitored_process.children(recursive=True)
                memory_usage_gb = sum(child.memory_info().rss for child in processes if child.is_running()) / (1024**3)
                if memory_usage_gb > memory_limit_gb:
                    logger.error(
                        "Memory usage exceeded %.2fGB (current: %.2fGB). Terminating command.",
                        memory_limit_gb,
                        memory_usage_gb,
                    )
                    _terminate_process_group(process)
                    return
                time.sleep(0.5)
        except psutil.Error:
            logger.debug("Could not monitor command memory usage.", exc_info=True)

    thread = threading.Thread(target=monitor, daemon=True)
    thread.start()
    return thread


def _terminate_process_group(process: subprocess.Popen[str]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except OSError:
        process.terminate()
