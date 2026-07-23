import datetime as dt
from pathlib import Path
from typing import Any

import yaml

from marita.cli import paper_benchmark


def test_resolve_paper_databases_with_case_variants(tmp_path: Path) -> None:
    for name in paper_benchmark.PAPER_DATABASES:
        (tmp_path / name).touch()

    resolved = paper_benchmark._resolve_databases(tmp_path, "paper")

    assert [path.name for path in resolved] == paper_benchmark.PAPER_DATABASES


def test_resolve_named_databases_matches_stems_case_insensitively(tmp_path: Path) -> None:
    (tmp_path / "CORA.db").touch()
    (tmp_path / "nations.db").touch()

    resolved = paper_benchmark._resolve_databases(tmp_path, "Cora,Nations")

    assert [path.name for path in resolved] == ["CORA.db", "nations.db"]


def test_cli_databases_override_profile_databases(tmp_path: Path) -> None:
    db_dir = tmp_path / "dbs"
    db_dir.mkdir()
    (db_dir / "Accidents.db").touch()
    (db_dir / "Basketball_men.db").touch()
    output_dir = tmp_path / "results"
    settings = tmp_path / "settings.yaml"
    settings.write_text(
        yaml.safe_dump(
            {
                "database_dir": str(db_dir),
                "output": str(output_dir),
                "logs": str(tmp_path / "logs"),
                "databases": "all",
                "algorithms": ["POPPER", "AMIE3"],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    exit_code = paper_benchmark.main(
        [
            "--settings",
            str(settings),
            "--databases",
            "Basketball_men",
            "--dry-run",
        ]
    )

    assert exit_code == 0
    summary = paper_benchmark.json.loads((output_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["planned_runs"] == 2
    assert {(item["algorithm"], item["database"]) for item in summary["plan"]} == {
        ("POPPER", "Basketball_men.db"),
        ("AMIE3", "Basketball_men.db"),
    }
    assert sorted(path.name for path in (output_dir / "configs").glob("*.yaml")) == [
        "amie3_Basketball_men.yaml",
        "popper_Basketball_men.yaml",
    ]


def test_status_honors_cli_databases_override_profile_databases(tmp_path: Path, capsys) -> None:
    db_dir = tmp_path / "dbs"
    db_dir.mkdir()
    (db_dir / "Accidents.db").touch()
    (db_dir / "Basketball_men.db").touch()
    output_dir = tmp_path / "results"
    settings = tmp_path / "settings.yaml"
    settings.write_text(
        yaml.safe_dump(
            {
                "database_dir": str(db_dir),
                "output": str(output_dir),
                "logs": str(tmp_path / "logs"),
                "databases": "all",
                "algorithms": ["POPPER"],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    exit_code = paper_benchmark.main(
        [
            "--settings",
            str(settings),
            "--databases",
            "Basketball_men",
            "--status",
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "Overall 0/1 done" in output
    assert "POPPER: 0/1 done" in output
    assert "Accidents.db" not in output


def test_host_sharded_dry_run_writes_only_selected_shard_configs(tmp_path: Path) -> None:
    db_dir = tmp_path / "dbs"
    db_dir.mkdir()
    for name in ["A.db", "B.db", "C.db"]:
        (db_dir / name).touch()
    output_dir = tmp_path / "results"
    settings = tmp_path / "settings.yaml"
    settings.write_text(
        yaml.safe_dump(
            {
                "database_dir": str(db_dir),
                "output": str(output_dir),
                "logs": str(tmp_path / "logs"),
                "databases": "all",
                "algorithms": ["POPPER"],
                "hosts": ["h0", "h1"],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    exit_code = paper_benchmark.main(["--settings", str(settings), "--host", "h1", "--dry-run"])

    assert exit_code == 0
    summary = paper_benchmark.json.loads((output_dir / "summary_h1.json").read_text(encoding="utf-8"))
    assert summary["planned_runs"] == 1
    assert [(item["algorithm"], item["database"]) for item in summary["plan"]] == [("POPPER", "B.db")]
    assert sorted(path.name for path in (output_dir / "configs").glob("*.yaml")) == ["popper_B.yaml"]


def test_build_marita_config_uses_paper_parameters(tmp_path: Path) -> None:
    db_dir = tmp_path / "dbs"
    db_dir.mkdir()
    db = db_dir / "Biodegradability.db"
    db.touch()

    spec = paper_benchmark._build_run_spec(
        algorithm="MARITA",
        database=db,
        database_dir=db_dir,
        output_dir=tmp_path / "results",
        log_root=tmp_path / "logs",
        timeout=7200,
    )
    config = yaml.safe_load(spec.config_path.read_text(encoding="utf-8"))

    assert config["algorithm"]["name"] == "MARITA"
    assert config["algorithm"]["parameters"]["disjoint_semantics"] is True
    assert config["algorithm"]["parameters"]["walk_length"] == 3
    assert config["monitor"]["timeout"] == 7200
    assert config["monitor"]["memory_threshold"] == 15 * 1024**3


def test_build_baseline_config_uses_configured_limits(tmp_path: Path) -> None:
    db_dir = tmp_path / "dbs"
    db_dir.mkdir()
    db = db_dir / "Demo.db"
    db.touch()

    spec = paper_benchmark._build_run_spec(
        algorithm="AMIE3",
        database=db,
        database_dir=db_dir,
        output_dir=tmp_path / "results",
        log_root=tmp_path / "logs",
        timeout=3600,
        memory_gb=10,
        java_heap_gb=8,
    )
    config = yaml.safe_load(spec.config_path.read_text(encoding="utf-8"))

    assert config["monitor"]["memory_threshold"] == 10 * 1024**3
    assert config["benchmark"]["timeout"] == 3600
    assert config["benchmark"]["memory_gb"] == 10
    assert config["benchmark"]["java_heap_gb"] == 8


def test_build_popper_config_uses_external_command(tmp_path: Path) -> None:
    db_dir = tmp_path / "dbs"
    db_dir.mkdir()
    db = db_dir / "Demo.db"
    db.touch()

    spec = paper_benchmark._build_run_spec(
        algorithm="POPPER",
        database=db,
        database_dir=db_dir,
        output_dir=tmp_path / "results",
        log_root=tmp_path / "logs",
        timeout=3600,
        memory_gb=10,
        java_heap_gb=8,
    )
    config = yaml.safe_load(spec.config_path.read_text(encoding="utf-8"))

    assert config["benchmark"]["popper_command"] == "run-popper"


def test_build_matilda_config_uses_sibling_repo_path(tmp_path: Path) -> None:
    db_dir = tmp_path / "dbs"
    db_dir.mkdir()
    db = db_dir / "Demo.db"
    db.touch()

    spec = paper_benchmark._build_run_spec(
        algorithm="MATILDA",
        database=db,
        database_dir=db_dir,
        output_dir=tmp_path / "results",
        log_root=tmp_path / "logs",
        timeout=3600,
        memory_gb=10,
        java_heap_gb=8,
    )
    config = yaml.safe_load(spec.config_path.read_text(encoding="utf-8"))

    assert config["benchmark"]["matilda_path"].endswith("/MATILDA")


def test_shard_databases_is_deterministic_and_non_overlapping(tmp_path: Path) -> None:
    dbs = [tmp_path / f"db{i}.db" for i in range(8)]
    hosts = ["tipi00", "tipi01", "tipi02", "tipi04"]

    shards = [paper_benchmark._shard_databases(dbs, hosts, host) for host in hosts]
    flattened = [db for shard in shards for db in shard]

    assert sorted(flattened) == sorted(dbs)
    assert sum(len(shard) for shard in shards) == len(set(flattened))


def test_resolve_hosts_override_settings_hosts() -> None:
    profile = {"hosts": ["tipi00", "tipi01", "tipi02", "tipi04"]}

    assert paper_benchmark._resolve_hosts("tipi01,tipi02", profile) == ["tipi01", "tipi02"]


def test_two_host_override_shards_all_databases_once(tmp_path: Path) -> None:
    dbs = [tmp_path / f"db{i}.db" for i in range(83)]
    hosts = paper_benchmark._resolve_hosts("tipi01,tipi02", {"hosts": ["tipi00", "tipi01", "tipi02", "tipi04"]})

    tipi01 = paper_benchmark._shard_databases(dbs, hosts, "tipi01")
    tipi02 = paper_benchmark._shard_databases(dbs, hosts, "tipi02")

    assert len(tipi01) == 42
    assert len(tipi02) == 41
    assert sorted(tipi01 + tipi02) == sorted(dbs)
    assert set(tipi01).isdisjoint(tipi02)


def test_resolve_hosts_rejects_duplicates() -> None:
    try:
        paper_benchmark._resolve_hosts("tipi01,tipi01", {})
    except ValueError as exc:
        assert "duplicate" in str(exc)
    else:
        raise AssertionError("Expected duplicate hosts to raise")


def test_parse_all_algorithms() -> None:
    assert paper_benchmark._parse_algorithms("ALL") == ["MARITA", "AMIE3", "SPIDER", "POPPER", "MATILDA"]


def test_run_command_classifies_internal_timeout_exit_code(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    class FakeProcess:
        pid = 12345

        def __init__(self, command, **kwargs):
            captured["command"] = command
            captured.update(kwargs)

        def wait(self, timeout=None):
            captured["timeout"] = timeout
            return paper_benchmark.TIMEOUT_EXIT_CODE

        def poll(self):
            return paper_benchmark.TIMEOUT_EXIT_CODE

    monkeypatch.setattr(paper_benchmark.subprocess, "Popen", FakeProcess)
    monkeypatch.setattr(paper_benchmark, "_monitor_memory", lambda *args, **kwargs: None)

    spec = paper_benchmark.RunSpec(
        algorithm="MATILDA",
        database=tmp_path / "demo.db",
        config_path=tmp_path / "config.yaml",
        command=["marita", "benchmark"],
        stdout_path=tmp_path / "stdout.log",
    )

    result = paper_benchmark._run_command(spec, timeout=7, memory_gb=1)

    assert result["status"] == "timeout"
    assert result["error"] == "Execution exceeded 7 seconds"
    assert captured["timeout"] == 7 + paper_benchmark.SUBPROCESS_CLEANUP_GRACE_SECONDS
    assert "env" in captured
    if paper_benchmark.os.name == "posix":
        assert captured["preexec_fn"] is paper_benchmark._prepare_child_process


def test_run_command_writes_running_and_final_progress(monkeypatch, tmp_path: Path) -> None:
    class FakeProcess:
        pid = 12345

        def __init__(self, *_args, **kwargs):
            kwargs["stdout"].write("2026-07-02 [INFO] - Discovered 42 rules with SPIDER.\n")
            kwargs["stdout"].flush()

        def wait(self, timeout=None):
            return 0

        def poll(self):
            return 0

    monkeypatch.setattr(paper_benchmark.subprocess, "Popen", FakeProcess)
    monkeypatch.setattr(paper_benchmark, "_monitor_memory", lambda *args, **kwargs: None)

    progress_path = tmp_path / "progress" / "POPPER_Demo.db.json"
    spec = paper_benchmark.RunSpec(
        algorithm="POPPER",
        database=tmp_path / "Demo.db",
        config_path=tmp_path / "config.yaml",
        command=["marita", "benchmark"],
        stdout_path=tmp_path / "stdout.log",
        progress_path=progress_path,
    )

    result = paper_benchmark._run_command(spec, timeout=7, memory_gb=1)
    progress = paper_benchmark.json.loads(progress_path.read_text(encoding="utf-8"))

    assert result["status"] == "success"
    assert progress["status"] == "success"
    assert progress["algorithm"] == "POPPER"
    assert progress["database"] == "Demo.db"
    assert progress["stdout"] == str(spec.stdout_path)
    assert progress["rules_count"] == 42


def test_parse_rules_count_from_stdout(tmp_path: Path) -> None:
    stdout_path = tmp_path / "stdout.log"
    stdout_path.write_text(
        "\n".join(
            [
                "2026-07-02 [INFO] - Discovered 3 rules with SPIDER.",
                "2026-07-02 [INFO] - Discovered 7 rules with SPIDER.",
            ]
        ),
        encoding="utf-8",
    )

    assert paper_benchmark._parse_rules_count_from_stdout(stdout_path) == 7


def test_parse_rules_count_from_stdout_handles_missing_or_zero(tmp_path: Path) -> None:
    stdout_path = tmp_path / "stdout.log"
    stdout_path.write_text("2026-07-02 [INFO] - Discovered 0 rules with SPIDER.\n", encoding="utf-8")
    assert paper_benchmark._parse_rules_count_from_stdout(stdout_path) == 0

    stdout_path.write_text("2026-07-02 [INFO] - Command executed successfully.\n", encoding="utf-8")
    assert paper_benchmark._parse_rules_count_from_stdout(stdout_path) is None
    assert paper_benchmark._parse_rules_count_from_stdout(tmp_path / "missing.log") is None


def test_print_progress_status_overlays_shared_progress(tmp_path: Path, capsys) -> None:
    output_dir = tmp_path / "results"
    specs = [
        paper_benchmark.RunSpec(
            algorithm="POPPER",
            database=tmp_path / "A.db",
            config_path=tmp_path / "popper_a.yaml",
            command=["marita", "benchmark"],
            stdout_path=tmp_path / "popper_a.stdout",
            progress_path=output_dir / "progress" / "POPPER_A.db.json",
        ),
        paper_benchmark.RunSpec(
            algorithm="POPPER",
            database=tmp_path / "B.db",
            config_path=tmp_path / "popper_b.yaml",
            command=["marita", "benchmark"],
            stdout_path=tmp_path / "popper_b.stdout",
            progress_path=output_dir / "progress" / "POPPER_B.db.json",
        ),
        paper_benchmark.RunSpec(
            algorithm="POPPER",
            database=tmp_path / "C.db",
            config_path=tmp_path / "popper_c.yaml",
            command=["marita", "benchmark"],
            stdout_path=tmp_path / "popper_c.stdout",
            progress_path=output_dir / "progress" / "POPPER_C.db.json",
        ),
    ]
    paper_benchmark._write_progress(
        specs[0],
        {
            "algorithm": "POPPER",
            "database": "A.db",
            "status": "running",
            "host": "tipi01",
            "started_at": "2026-06-17T00:00:00+00:00",
        },
    )
    paper_benchmark._write_progress(
        specs[1],
        {"algorithm": "POPPER", "database": "B.db", "status": "oom", "error": "Memory limit exceeded"},
    )

    paper_benchmark.print_progress_status(output_dir, specs)
    output = capsys.readouterr().out

    assert "Overall 1/3 done  1 running  1 pending  1 failed" in output
    assert "POPPER: 1/3 done  1 running  1 failed" in output
    assert "tipi01  POPPER  A.db" in output
    assert "POPPER  B.db  oom  Memory limit exceeded" in output


def test_email_config_uses_environment(monkeypatch) -> None:
    monkeypatch.setenv("MARITA_EMAIL_TO", "to@example.com")
    monkeypatch.setenv("MARITA_EMAIL_FROM", "from@example.com")
    monkeypatch.setenv("MARITA_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("MARITA_SMTP_PORT", "587")
    monkeypatch.setenv("MARITA_SMTP_USER", "user@example.com")
    monkeypatch.setenv("MARITA_SMTP_STARTTLS", "1")

    args = paper_benchmark.parse_arguments(["--dry-run"])
    config = paper_benchmark._email_config_from_args(args)

    assert config is not None
    assert config.to == "to@example.com"
    assert config.sender == "from@example.com"
    assert config.smtp_host == "smtp.example.com"
    assert config.smtp_port == 587
    assert config.smtp_user == "user@example.com"
    assert config.starttls is True


def test_paper_benchmark_dry_run_sends_email(monkeypatch, tmp_path: Path) -> None:
    for variable in ("MARITA_SMTP_HOST", "MARITA_SMTP_PORT", "MARITA_SMTP_STARTTLS", "MARITA_SMTP_USER"):
        monkeypatch.delenv(variable, raising=False)
    db_dir = tmp_path / "dbs"
    db_dir.mkdir()
    (db_dir / "Demo.db").touch()
    sent_messages: list[Any] = []

    class FakeSMTP:
        def __init__(self, host: str, port: int, timeout: int) -> None:
            assert host == "localhost"
            assert port == 25
            assert timeout == 30

        def __enter__(self) -> "FakeSMTP":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def send_message(self, message: Any) -> None:
            sent_messages.append(message)

    monkeypatch.setattr(paper_benchmark.smtplib, "SMTP", FakeSMTP)

    exit_code = paper_benchmark.main(
        [
            "--database-dir",
            str(db_dir),
            "--output",
            str(tmp_path / "results"),
            "--logs",
            str(tmp_path / "logs"),
            "--databases",
            "Demo",
            "--dry-run",
            "--email-to",
            "to@example.com",
        ]
    )

    assert exit_code == 0
    assert len(sent_messages) == 1
    assert sent_messages[0]["To"] == "to@example.com"
    assert "success" in sent_messages[0]["Subject"]
    assert "Status: success" in sent_messages[0].get_content()


def test_notification_status_distinguishes_completed_child_failures() -> None:
    results = [
        {"status": "success"},
        {"status": "oom"},
        {"status": "timeout"},
        {"status": "error"},
    ]

    status = paper_benchmark._notification_status(exit_code=1, error=None, results=results, planned_runs=4)

    assert status == "completed with non-success runs"


def test_email_body_reports_controller_and_outcome_breakdown(tmp_path: Path) -> None:
    started_at = dt.datetime(2026, 6, 17, 14, 52, tzinfo=dt.timezone.utc)
    ended_at = dt.datetime(2026, 6, 18, 1, 0, tzinfo=dt.timezone.utc)
    results = [
        {"status": "success"},
        {"status": "oom"},
        {"status": "oom"},
        {"status": "timeout"},
        {"status": "error"},
    ]

    body = paper_benchmark._format_email_body(
        status="completed with non-success runs",
        exit_code=1,
        started_at=started_at,
        ended_at=ended_at,
        host="tipi01",
        output_dir=tmp_path / "results",
        summary_path=tmp_path / "results" / "summary_tipi01.json",
        planned_runs=5,
        results=results,
        error=None,
    )

    assert "Status: completed with non-success runs" in body
    assert "Controller: completed all planned runs" in body
    assert "Runs: 5 planned, 5 completed, 1 success, 4 non-success" in body
    assert "Outcomes: 1 success, 2 oom, 1 timeout, 1 error" in body
