from __future__ import annotations

import logging
import os
import shlex
import signal
import subprocess
import threading
import time
import uuid
from collections.abc import Sequence
from contextlib import suppress
from pathlib import Path
from shutil import which
from typing import Any

import psutil

MEMORY_BYTES_PER_GB = 1024**3
TERMINATION_GRACE_SECONDS = 10


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
    process: subprocess.Popen[str] | None = None
    try:
        if redirected_stdout is not None:
            output_file = Path(redirected_stdout)
            output_file.parent.mkdir(parents=True, exist_ok=True)
            stdout_handle = output_file.open("w", encoding="utf-8")

        command_args = _wrap_with_systemd_scope(
            command_args,
            timeout=timeout,
            memory_limit_gb=memory_limit_gb,
            logger=active_logger,
        )
        memory_exceeded = threading.Event()
        process = subprocess.Popen(
            command_args,
            stdout=stdout_handle if stdout_handle else subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=str(cwd) if cwd else None,
            start_new_session=True,
            preexec_fn=_prepare_child_process if os.name == "posix" else None,
            env=_external_command_env(),
        )

        monitor_thread = _start_memory_monitor(
            pid=process.pid,
            process=process,
            memory_limit_gb=memory_limit_gb,
            logger=active_logger,
            memory_exceeded=memory_exceeded,
        )

        try:
            _, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            active_logger.error("Command timed out after %s seconds", timeout)
            _terminate_process(process)
            return False
        finally:
            if monitor_thread is not None:
                monitor_thread.join(timeout=1)

        if memory_exceeded.is_set():
            active_logger.error("Command exceeded %.2fGB memory limit.", memory_limit_gb)
            return False

        if process.returncode == 0:
            active_logger.info("Command executed successfully.")
            return True

        stderr_text = (stderr or "").strip()
        if stderr_text:
            active_logger.error("Command failed with return code %s: %s", process.returncode, stderr_text)
        else:
            active_logger.error("Command failed with return code %s", process.returncode)
        return False
    except Exception as exc:
        if process is not None and exc.__class__.__name__ == "BenchmarkTimeoutError":
            _terminate_process(process)
            raise
        active_logger.exception("Unexpected error while executing command")
        return False
    finally:
        if stdout_handle is not None:
            stdout_handle.close()


def _logger(logger: logging.Logger | None) -> logging.Logger:
    return logger or logging.getLogger(__name__)


def _external_command_env() -> dict[str, str]:
    env = os.environ.copy()
    path_parts = [str(Path.home() / ".local" / "bin")]
    existing_path = env.get("PATH")
    if existing_path:
        path_parts.append(existing_path)
    env["PATH"] = os.pathsep.join(path_parts)
    return env


def _wrap_with_systemd_scope(
    command_args: list[str],
    *,
    timeout: int | None,
    memory_limit_gb: float | None,
    logger: logging.Logger,
) -> list[str]:
    if os.name != "posix" or os.environ.get("MAHILDA_DISABLE_SYSTEMD_SCOPE") == "1":
        return command_args
    if memory_limit_gb is None and timeout is None:
        return command_args
    if which("systemd-run") is None:
        return command_args
    if not _systemd_user_manager_active():
        logger.debug("systemd user manager is not active; using process-tree monitoring only.")
        return command_args

    properties = ["--property", "CollectMode=inactive-or-failed"]
    if memory_limit_gb is not None:
        properties.extend(["--property", f"MemoryMax={int(memory_limit_gb * MEMORY_BYTES_PER_GB)}"])
        properties.extend(["--property", "MemoryAccounting=yes"])
    if timeout is not None and timeout > 0:
        properties.extend(["--property", f"RuntimeMaxSec={timeout}"])

    unit = f"mahilda-ext-{uuid.uuid4().hex[:12]}"
    wrapped = [
        "systemd-run",
        "--user",
        "--scope",
        "--quiet",
        "--wait",
        "--collect",
        "--unit",
        unit,
        *properties,
        *command_args,
    ]
    logger.info("Executing external command in systemd scope %s.", unit)
    return wrapped


def _systemd_user_manager_active() -> bool:
    try:
        result = subprocess.run(
            ["systemctl", "--user", "is-active", "default.target"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        return False
    return result.returncode == 0


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
    memory_exceeded: threading.Event | None = None,
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
                    if memory_exceeded is not None:
                        memory_exceeded.set()
                    logger.error(
                        "Memory usage exceeded %.2fGB (current: %.2fGB). Terminating command.",
                        memory_limit_gb,
                        memory_usage_gb,
                    )
                    _terminate_process(process)
                    return
                time.sleep(0.5)
        except psutil.Error:
            logger.debug("Could not monitor command memory usage.", exc_info=True)

    thread = threading.Thread(target=monitor, daemon=True)
    thread.start()
    return thread


def _prepare_child_process() -> None:
    try:
        import ctypes

        libc = ctypes.CDLL(None)
        libc.prctl(1, signal.SIGTERM)
    except Exception:
        return


def _terminate_process(process: subprocess.Popen[Any]) -> None:
    _signal_process_tree(process, signal.SIGTERM)
    try:
        process.wait(timeout=TERMINATION_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        _signal_process_tree(process, signal.SIGKILL)
        process.wait()


def _signal_process_tree(process: subprocess.Popen[Any], sig: int) -> None:
    with_context_processes: list[psutil.Process] = []
    try:
        root = psutil.Process(process.pid)
        with_context_processes = root.children(recursive=True)
    except psutil.Error:
        with_context_processes = []
    try:
        os.killpg(process.pid, sig)
    except ProcessLookupError:
        pass
    except OSError:
        with suppress(ProcessLookupError):
            process.send_signal(sig)
    for child in with_context_processes:
        with suppress(psutil.Error):
            child.send_signal(sig)


def _terminate_process_group(process: subprocess.Popen[str]) -> None:
    _terminate_process(process)
