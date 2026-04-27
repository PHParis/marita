import subprocess

from mahilda.cli import mlflow_start


def test_mlflow_start_launches_server(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("mahilda.cli.mlflow_start.importlib.util.find_spec", lambda _name: object())

    captured: dict[str, list[str]] = {}

    class FakeProcess:
        def wait(self, timeout=None) -> int:
            assert timeout is None
            return 0

    def fake_popen(cmd: list[str]):
        captured["cmd"] = cmd
        return FakeProcess()

    monkeypatch.setattr("mahilda.cli.mlflow_start.subprocess.Popen", fake_popen)

    exit_code = mlflow_start.main([])

    expected_tracking_uri = f"file://{(tmp_path / 'mlruns').absolute()}"
    expected_artifact_root = str((tmp_path / "mlruns" / "artifacts").absolute())
    assert exit_code == 0
    assert captured["cmd"] == [
        "mlflow",
        "server",
        "--backend-store-uri",
        expected_tracking_uri,
        "--default-artifact-root",
        expected_artifact_root,
        "--host",
        "127.0.0.1",
        "--port",
        "5000",
    ]


def test_mlflow_start_errors_when_mlflow_missing(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("mahilda.cli.mlflow_start.importlib.util.find_spec", lambda _name: None)

    exit_code = mlflow_start.main([])

    assert exit_code == 1


def test_mlflow_start_handles_keyboard_interrupt(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("mahilda.cli.mlflow_start.importlib.util.find_spec", lambda _name: object())

    class FakeProcess:
        def __init__(self) -> None:
            self.wait_calls = 0
            self.terminated = False

        def wait(self, timeout=None) -> int:
            self.wait_calls += 1
            if self.wait_calls == 1:
                assert timeout is None
                raise KeyboardInterrupt
            assert timeout == 10
            return 0

        def terminate(self) -> None:
            self.terminated = True

        def kill(self) -> None:
            raise AssertionError("kill should not be called")

    process = FakeProcess()

    def fake_popen(cmd: list[str]):
        del cmd
        return process

    monkeypatch.setattr("mahilda.cli.mlflow_start.subprocess.Popen", fake_popen)

    exit_code = mlflow_start.main([])

    assert exit_code == 0
    assert process.terminated is True


def test_mlflow_start_kills_after_shutdown_timeout(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("mahilda.cli.mlflow_start.importlib.util.find_spec", lambda _name: object())

    class FakeProcess:
        def __init__(self) -> None:
            self.wait_calls = 0
            self.terminated = False
            self.killed = False

        def wait(self, timeout=None) -> int:
            self.wait_calls += 1
            if self.wait_calls == 1:
                assert timeout is None
                raise KeyboardInterrupt
            if self.wait_calls == 2:
                assert timeout == 10
                raise subprocess.TimeoutExpired(cmd="mlflow server", timeout=10)
            assert timeout is None
            return 0

        def terminate(self) -> None:
            self.terminated = True

        def kill(self) -> None:
            self.killed = True

    process = FakeProcess()

    def fake_popen(cmd: list[str]):
        del cmd
        return process

    monkeypatch.setattr("mahilda.cli.mlflow_start.subprocess.Popen", fake_popen)

    exit_code = mlflow_start.main([])

    assert exit_code == 0
    assert process.terminated is True
    assert process.killed is True
