from mahilda.cli import mlflow_ui


def test_mlflow_ui_launches_with_default_port(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("mahilda.cli.mlflow_ui.importlib.util.find_spec", lambda _name: object())

    captured: dict[str, object] = {}

    def fake_run(cmd: list[str], check: bool) -> None:
        captured["cmd"] = cmd
        captured["check"] = check
        return None

    monkeypatch.setattr("mahilda.cli.mlflow_ui.subprocess.run", fake_run)

    exit_code = mlflow_ui.main([])

    expected_tracking_uri = f"file://{(tmp_path / 'mlruns').absolute()}"
    assert exit_code == 0
    assert captured["check"] is False
    assert captured["cmd"] == [
        "mlflow",
        "ui",
        "--backend-store-uri",
        expected_tracking_uri,
        "--host",
        "127.0.0.1",
        "--port",
        "5000",
        "--app-name",
        "mlflow",
    ]


def test_mlflow_ui_launches_with_custom_port(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("mahilda.cli.mlflow_ui.importlib.util.find_spec", lambda _name: object())

    captured: dict[str, object] = {}

    def fake_run(cmd: list[str], check: bool) -> None:
        del check
        captured["cmd"] = cmd
        return None

    monkeypatch.setattr("mahilda.cli.mlflow_ui.subprocess.run", fake_run)

    exit_code = mlflow_ui.main(["--port", "6001"])

    expected_tracking_uri = f"file://{(tmp_path / 'mlruns').absolute()}"
    assert exit_code == 0
    assert captured["cmd"] == [
        "mlflow",
        "ui",
        "--backend-store-uri",
        expected_tracking_uri,
        "--host",
        "127.0.0.1",
        "--port",
        "6001",
        "--app-name",
        "mlflow",
    ]


def test_mlflow_ui_errors_when_mlflow_missing(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("mahilda.cli.mlflow_ui.importlib.util.find_spec", lambda _name: None)

    exit_code = mlflow_ui.main([])

    assert exit_code == 1
