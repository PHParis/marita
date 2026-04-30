from mahilda.cli.main import main


def test_main_routes_run_command(monkeypatch) -> None:
    captured: dict[str, list[str]] = {}

    def fake_run_main(argv: list[str]) -> int:
        captured["argv"] = argv
        return 17

    monkeypatch.setattr("mahilda.cli.run.main", fake_run_main)

    exit_code = main(["run", "--config", "cfg.yml"])

    assert exit_code == 17
    assert captured["argv"] == ["--config", "cfg.yml"]


def test_main_routes_benchmark_command(monkeypatch) -> None:
    captured: dict[str, list[str]] = {}

    def fake_benchmark_main(argv: list[str]) -> int:
        captured["argv"] = argv
        return 18

    monkeypatch.setattr("mahilda.cli.benchmark.main", fake_benchmark_main)

    exit_code = main(["benchmark", "--config", "cfg.yml", "--baseline", "SPIDER"])

    assert exit_code == 18
    assert captured["argv"] == ["--config", "cfg.yml", "--baseline", "SPIDER"]


def test_main_routes_benchmark_input_tsv(monkeypatch) -> None:
    captured: dict[str, list[str]] = {}

    def fake_benchmark_main(argv: list[str]) -> int:
        captured["argv"] = argv
        return 18

    monkeypatch.setattr("mahilda.cli.benchmark.main", fake_benchmark_main)

    exit_code = main(["benchmark", "--config", "cfg.yml", "--baseline", "AMIE3", "--input-tsv", "kg.tsv"])

    assert exit_code == 18
    assert captured["argv"] == ["--config", "cfg.yml", "--baseline", "AMIE3", "--input-tsv", "kg.tsv"]


def test_main_routes_import_rdf_command(monkeypatch) -> None:
    captured: dict[str, list[str]] = {}

    def fake_import_rdf_main(argv: list[str]) -> int:
        captured["argv"] = argv
        return 24

    monkeypatch.setattr("mahilda.cli.import_rdf.main", fake_import_rdf_main)

    exit_code = main(
        [
            "import-rdf",
            "--input",
            "data/yago-tiny.ttl",
            "--output-dir",
            "data/yago",
            "--variants",
            "core",
            "--dataset-name",
            "yago_tiny",
        ]
    )

    assert exit_code == 24
    assert captured["argv"] == [
        "--input",
        "data/yago-tiny.ttl",
        "--output-dir",
        "data/yago",
        "--variants",
        "core",
        "--dataset-name",
        "yago_tiny",
    ]


def test_main_routes_download_databases_command(monkeypatch) -> None:
    captured: dict[str, list[str]] = {}

    def fake_download_main(argv: list[str]) -> int:
        captured["argv"] = argv
        return 25

    monkeypatch.setattr("mahilda.cli.download_databases.main", fake_download_main)

    exit_code = main(
        [
            "download-databases",
            "--output",
            "data/relational",
            "--database",
            "Mondial",
            "--max-databases",
            "3",
            "--timeout",
            "9",
            "--dump-only",
            "--quiet",
        ]
    )

    assert exit_code == 25
    assert captured["argv"] == [
        "--output",
        "data/relational",
        "--timeout",
        "9",
        "--database",
        "Mondial",
        "--max-databases",
        "3",
        "--dump-only",
        "--quiet",
    ]


def test_main_routes_batch_command(monkeypatch) -> None:
    captured: dict[str, list[str]] = {}

    def fake_batch_main(argv: list[str]) -> int:
        captured["argv"] = argv
        return 19

    monkeypatch.setattr("mahilda.cli.batch.main", fake_batch_main)

    exit_code = main(
        [
            "batch",
            "--config",
            "cfg.yml",
            "--directory",
            "dbs",
            "--output",
            "out",
            "--timeout",
            "99",
            "--workers",
            "4",
            "--start-from",
            "2",
            "--max-databases",
            "3",
        ]
    )

    assert exit_code == 19
    assert captured["argv"] == [
        "--config",
        "cfg.yml",
        "--output",
        "out",
        "--timeout",
        "99",
        "--start-from",
        "2",
        "--workers",
        "4",
        "--directory",
        "dbs",
        "--max-databases",
        "3",
    ]


def test_main_routes_smoke_command(monkeypatch) -> None:
    captured: dict[str, list[str]] = {}

    def fake_smoke_main(argv: list[str]) -> int:
        captured["argv"] = argv
        return 20

    monkeypatch.setattr("mahilda.cli.smoke.main", fake_smoke_main)

    exit_code = main(["smoke", "--config", "cfg.yml", "--verbose"])

    assert exit_code == 20
    assert captured["argv"] == ["--config", "cfg.yml", "--verbose"]


def test_main_routes_test_db_command(monkeypatch) -> None:
    captured: dict[str, list[str]] = {}

    def fake_test_db_main(argv: list[str]) -> int:
        captured["argv"] = argv
        return 21

    monkeypatch.setattr("mahilda.cli.test_data.main", fake_test_db_main)

    exit_code = main(["test-db", "--output", "tmp/test.db"])

    assert exit_code == 21
    assert captured["argv"] == ["--output", "tmp/test.db"]


def test_main_routes_mlflow_start_command(monkeypatch) -> None:
    captured: dict[str, list[str]] = {}

    def fake_mlflow_start_main(argv: list[str]) -> int:
        captured["argv"] = argv
        return 22

    monkeypatch.setattr("mahilda.cli.mlflow_start.main", fake_mlflow_start_main)

    exit_code = main(["mlflow", "start"])

    assert exit_code == 22
    assert captured["argv"] == []


def test_main_routes_mlflow_ui_command(monkeypatch) -> None:
    captured: dict[str, list[str]] = {}

    def fake_mlflow_ui_main(argv: list[str]) -> int:
        captured["argv"] = argv
        return 23

    monkeypatch.setattr("mahilda.cli.mlflow_ui.main", fake_mlflow_ui_main)

    exit_code = main(["mlflow", "ui", "--port", "6001"])

    assert exit_code == 23
    assert captured["argv"] == ["--port", "6001"]
