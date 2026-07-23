from pathlib import Path

import yaml

from marita.cli import main as main_cli
from marita.cli import paper_pipeline


def _settings_file(tmp_path: Path, db_count: int = 3) -> Path:
    db_dir = tmp_path / "dbs"
    db_dir.mkdir()
    for index in range(db_count):
        db = db_dir / f"db{index}.db"
        db.write_bytes(b"0" * (index + 1))
    settings = {
        "database_dir": str(db_dir),
        "output": str(tmp_path / "results" / "paper_table2"),
        "logs": str(tmp_path / "logs" / "paper_table2"),
        "expected_databases": db_count,
        "hosts": ["tipi00", "tipi01"],
        "limits": {"timeout_seconds": 60, "memory_gb": 1, "java_heap_gb": 1},
        "pipeline": {
            "queue_dir": str(tmp_path / "pipeline"),
            "coordinator_host": "tipi00",
            "heartbeat_seconds": 1,
            "stale_after_seconds": 60,
            "stages": [
                {"id": "010_setup", "experiment": "setup", "workers_per_host": 1},
                {
                    "id": "020_b2_disjointness",
                    "experiment": "B2",
                    "databases": ["db0"],
                    "workers_per_host": 1,
                    "timeout_seconds": 10,
                },
                {"id": "030_b1_marita", "experiment": "B1", "algorithms": ["MARITA"], "databases": "all"},
                {"id": "040_b1_spider", "experiment": "B1", "algorithms": ["SPIDER"], "databases": "all"},
                {"id": "050_b3_joinability", "experiment": "B3", "databases": "all"},
            ],
        },
    }
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump(settings, sort_keys=False), encoding="utf-8")
    return path


def test_pipeline_plans_expected_job_counts(tmp_path: Path) -> None:
    settings = paper_pipeline.load_settings(_settings_file(tmp_path, db_count=3))
    databases = paper_pipeline.resolve_databases(settings.database_dir, settings.expected_databases)

    jobs = paper_pipeline.plan_jobs(settings, databases)
    by_stage = {stage.id: sum(1 for job in jobs if job["stage"] == stage.id) for stage in settings.stages}

    assert len(jobs) == 1 + 20 + 3 + 3 + 6
    assert by_stage["010_setup"] == 1
    assert by_stage["020_b2_disjointness"] == 20
    assert by_stage["030_b1_marita"] == 3
    assert by_stage["040_b1_spider"] == 3
    assert by_stage["050_b3_joinability"] == 6


def test_pipeline_initialise_queue_is_idempotent(tmp_path: Path) -> None:
    settings = paper_pipeline.load_settings(_settings_file(tmp_path, db_count=2))
    databases = paper_pipeline.resolve_databases(settings.database_dir, settings.expected_databases)

    paper_pipeline.initialise_queue(settings, databases)
    paper_pipeline.initialise_queue(settings, databases)

    pending = list((settings.queue_dir / "queue" / "030_b1_marita" / "pending").glob("*.json"))
    assert len(pending) == 2


def test_claim_job_moves_only_one_pending_file(tmp_path: Path) -> None:
    settings = paper_pipeline.load_settings(_settings_file(tmp_path, db_count=1))
    databases = paper_pipeline.resolve_databases(settings.database_dir, settings.expected_databases)
    paper_pipeline.initialise_queue(settings, databases)
    stage = next(stage for stage in settings.stages if stage.id == "030_b1_marita")

    claim1 = paper_pipeline.claim_job(settings, stage, "tipi00")
    claim2 = paper_pipeline.claim_job(settings, stage, "tipi01")

    assert claim1 is not None
    assert claim2 is None
    assert len(list((settings.queue_dir / "queue" / stage.id / "running").glob("*.json"))) == 1


def test_recover_stale_jobs_ignores_vanished_running_file(tmp_path: Path, monkeypatch) -> None:
    settings = paper_pipeline.load_settings(_settings_file(tmp_path, db_count=1))
    databases = paper_pipeline.resolve_databases(settings.database_dir, settings.expected_databases)
    paper_pipeline.initialise_queue(settings, databases)
    stage = next(stage for stage in settings.stages if stage.id == "030_b1_marita")
    claim = paper_pipeline.claim_job(settings, stage, "tipi00")
    assert claim is not None
    running_path, _ = claim
    original_stat = Path.stat

    def stat_with_race(self, *args, **kwargs):
        if self == running_path:
            raise FileNotFoundError(running_path)
        return original_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", stat_with_race)

    paper_pipeline.recover_stale_jobs(settings, stage)


def test_recover_stale_jobs_ignores_orphan_heartbeat_file(tmp_path: Path, monkeypatch) -> None:
    settings = paper_pipeline.load_settings(_settings_file(tmp_path, db_count=1))
    databases = paper_pipeline.resolve_databases(settings.database_dir, settings.expected_databases)
    paper_pipeline.initialise_queue(settings, databases)
    stage = next(stage for stage in settings.stages if stage.id == "030_b1_marita")
    running_dir = settings.queue_dir / "queue" / stage.id / "running"
    heartbeat_path = running_dir / "orphan.heartbeat.json"
    heartbeat_path.write_text('{"host": "tipi00"}', encoding="utf-8")

    monkeypatch.setattr(
        paper_pipeline.time,
        "time",
        lambda: heartbeat_path.stat().st_mtime + settings.stale_after_seconds + 1,
    )

    paper_pipeline.recover_stale_jobs(settings, stage)

    assert heartbeat_path.exists()


def test_resolve_databases_enforces_expected_count(tmp_path: Path) -> None:
    settings = paper_pipeline.load_settings(_settings_file(tmp_path, db_count=2))

    try:
        paper_pipeline.resolve_databases(settings.database_dir, 3)
    except ValueError as exc:
        assert "Expected 3" in str(exc)
    else:
        raise AssertionError("Expected count mismatch to raise")


def test_main_exposes_paper_pipeline(monkeypatch) -> None:
    called = {}

    def fake_pipeline_main(args):
        called["args"] = args
        return 0

    monkeypatch.setattr(main_cli.paper_pipeline, "main", fake_pipeline_main)

    assert main_cli.main(["paper-pipeline", "--settings", "settings.yaml", "--host", "tipi00", "--dry-run"]) == 0
    assert called["args"] == ["--settings", "settings.yaml", "--host", "tipi00", "--dry-run"]
