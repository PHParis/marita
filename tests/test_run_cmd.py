from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from mahilda.utils import run_cmd as run_cmd_module


class DummyLogger:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def info(self, msg, *args):
        self.messages.append(("info", str(msg % args if args else msg)))

    def error(self, msg, *args):
        self.messages.append(("error", str(msg % args if args else msg)))

    def exception(self, msg, *args):
        self.messages.append(("exception", str(msg % args if args else msg)))

    def debug(self, msg, *args, **kwargs):
        self.messages.append(("debug", str(msg % args if args else msg)))


class FakeProcess:
    def __init__(self, *, returncode=0, stderr="", timeout=False):
        self.pid = 123
        self.returncode = returncode
        self._stderr = stderr
        self._timeout = timeout
        self.terminated = False

    def communicate(self, timeout=None):
        if self._timeout:
            raise subprocess.TimeoutExpired(cmd="x", timeout=timeout)
        return "", self._stderr

    def terminate(self):
        self.terminated = True

    def poll(self):
        return self.returncode


def test_normalise_command_parsing_and_redirect() -> None:
    args, out = run_cmd_module._normalise_command("python -V > out.txt", None)
    assert args == ["python", "-V"]
    assert out == "out.txt"

    args2, out2 = run_cmd_module._normalise_command(["python", "-V"], Path("x.txt"))
    assert args2 == ["python", "-V"]
    assert str(out2) == "x.txt"

    args3, out3 = run_cmd_module._normalise_command(["python", ">"], None)
    assert args3 == []
    assert out3 is None


def test_run_cmd_rejects_empty_command() -> None:
    logger = DummyLogger()
    ok = run_cmd_module.run_cmd([], logger=logger)
    assert ok is False
    assert any(level == "error" and "empty command" in msg.lower() for level, msg in logger.messages)


def test_run_cmd_success(monkeypatch) -> None:
    logger = DummyLogger()

    def fake_popen(*args, **kwargs):
        return FakeProcess(returncode=0)

    monkeypatch.setattr(run_cmd_module.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(run_cmd_module, "_start_memory_monitor", lambda **kwargs: None)
    monkeypatch.setattr(run_cmd_module.os, "killpg", lambda *args, **kwargs: (_ for _ in ()).throw(OSError()))

    assert run_cmd_module.run_cmd("python -V", logger=logger) is True


def test_run_cmd_failure_with_stderr(monkeypatch) -> None:
    logger = DummyLogger()

    def fake_popen(*args, **kwargs):
        return FakeProcess(returncode=2, stderr="bad things")

    monkeypatch.setattr(run_cmd_module.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(run_cmd_module, "_start_memory_monitor", lambda **kwargs: None)

    assert run_cmd_module.run_cmd("python -V", logger=logger) is False
    assert any(level == "error" and "bad things" in msg for level, msg in logger.messages)


def test_run_cmd_timeout(monkeypatch) -> None:
    logger = DummyLogger()
    proc = FakeProcess(returncode=0, timeout=True)

    def fake_popen(*args, **kwargs):
        return proc

    monkeypatch.setattr(run_cmd_module.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(run_cmd_module, "_start_memory_monitor", lambda **kwargs: None)
    monkeypatch.setattr(run_cmd_module.os, "killpg", lambda *args, **kwargs: (_ for _ in ()).throw(OSError()))

    assert run_cmd_module.run_cmd("python -V", timeout=1, logger=logger) is False
    assert proc.terminated is True


def test_run_cmd_writes_redirected_stdout_file(monkeypatch, tmp_path) -> None:
    logger = DummyLogger()
    out = tmp_path / "nested" / "stdout.txt"

    def fake_popen(*args, **kwargs):
        return FakeProcess(returncode=0)

    monkeypatch.setattr(run_cmd_module.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(run_cmd_module, "_start_memory_monitor", lambda **kwargs: None)

    assert run_cmd_module.run_cmd("python -V", stdout_path=out, logger=logger) is True
    assert out.exists()


def test_start_memory_monitor_none_limit() -> None:
    thread = run_cmd_module._start_memory_monitor(
        pid=1,
        process=FakeProcess(returncode=0),
        memory_limit_gb=None,
        logger=logging.getLogger("test.run_cmd"),
    )
    assert thread is None


def test_start_memory_monitor_terminates_process(monkeypatch) -> None:
    logger = DummyLogger()

    class FakePsProc:
        def memory_info(self):
            class M:
                rss = 2 * (1024**3)

            return M()

        def children(self, recursive=False):
            del recursive
            return []

        def is_running(self):
            return True

    monkeypatch.setattr(run_cmd_module.psutil, "Process", lambda pid: FakePsProc())
    monkeypatch.setattr(run_cmd_module.os, "killpg", lambda *args, **kwargs: (_ for _ in ()).throw(OSError()))
    monkeypatch.setattr(run_cmd_module.time, "sleep", lambda _x: None)

    proc = FakeProcess(returncode=None)
    call_count = {"n": 0}

    def fake_poll():
        call_count["n"] += 1
        return None if call_count["n"] == 1 else 0

    proc.poll = fake_poll  # type: ignore[assignment]

    thread = run_cmd_module._start_memory_monitor(
        pid=1,
        process=proc,
        memory_limit_gb=1.0,
        logger=logger,  # type: ignore[arg-type]
    )
    assert thread is not None
    thread.join(timeout=1)
    assert proc.terminated is True


def test_start_memory_monitor_psutil_error(monkeypatch) -> None:
    logger = DummyLogger()

    def boom(_pid):
        raise run_cmd_module.psutil.Error("cannot inspect")

    monkeypatch.setattr(run_cmd_module.psutil, "Process", boom)
    proc = FakeProcess(returncode=0)

    thread = run_cmd_module._start_memory_monitor(
        pid=1,
        process=proc,
        memory_limit_gb=1.0,
        logger=logger,  # type: ignore[arg-type]
    )
    assert thread is not None
    thread.join(timeout=1)
    assert any(level == "debug" for level, _msg in logger.messages)
