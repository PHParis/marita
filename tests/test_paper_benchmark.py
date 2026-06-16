from pathlib import Path

import yaml

from mahilda.cli import paper_benchmark


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


def test_build_mahilda_config_uses_paper_parameters(tmp_path: Path) -> None:
    db_dir = tmp_path / "dbs"
    db_dir.mkdir()
    db = db_dir / "Biodegradability.db"
    db.touch()

    spec = paper_benchmark._build_run_spec(
        algorithm="MAHILDA",
        database=db,
        database_dir=db_dir,
        output_dir=tmp_path / "results",
        log_root=tmp_path / "logs",
        timeout=7200,
    )
    config = yaml.safe_load(spec.config_path.read_text(encoding="utf-8"))

    assert config["algorithm"]["name"] == "MAHILDA"
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


def test_parse_all_algorithms() -> None:
    assert paper_benchmark._parse_algorithms("ALL") == ["MAHILDA", "AMIE3", "SPIDER", "POPPER", "MATILDA"]


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
        command=["mahilda", "benchmark"],
        stdout_path=tmp_path / "stdout.log",
    )

    result = paper_benchmark._run_command(spec, timeout=7, memory_gb=1)

    assert result["status"] == "timeout"
    assert result["error"] == "Execution exceeded 7 seconds"
    assert captured["timeout"] == 7
    if paper_benchmark.os.name == "posix":
        assert captured["preexec_fn"] is paper_benchmark._prepare_child_process
