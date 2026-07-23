from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import logging
import os
import shutil
import socket
import sys
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import yaml

from marita.cli import paper_benchmark

LOGGER = logging.getLogger(__name__)
DONE_STATUSES = {"success", "timeout", "oom", "error"}


@dataclass(frozen=True)
class StageSettings:
    id: str
    experiment: str
    algorithms: tuple[str, ...]
    databases: str | tuple[str, ...]
    workers_per_host: int
    timeout_seconds: int
    memory_gb: float
    java_heap_gb: int


@dataclass(frozen=True)
class PipelineSettings:
    database_dir: Path
    output_dir: Path
    logs_dir: Path
    queue_dir: Path
    coordinator_host: str
    hosts: tuple[str, ...]
    expected_databases: int | None
    heartbeat_seconds: int
    stale_after_seconds: int
    stages: tuple[StageSettings, ...]


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the full paper experiment pipeline from a shared queue.")
    parser.add_argument("--settings", default="configs/paper/benchmark_83.yaml")
    parser.add_argument("--host", default="auto")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--reset", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s", force=True)
    args = parse_arguments(argv)

    try:
        settings = load_settings(Path(args.settings))
        host = resolve_host(args.host, settings.hosts)
        databases = resolve_databases(settings.database_dir, settings.expected_databases)
    except ValueError as exc:
        LOGGER.error("%s", exc)
        return 1

    if args.status:
        print_status(settings)
        return 0

    if args.reset:
        reset_pipeline(settings)
        LOGGER.info("Reset pipeline queue at %s", settings.queue_dir)
        return 0

    if args.dry_run:
        jobs = plan_jobs(settings, databases, write_configs=False)
        LOGGER.info("Host: %s", host)
        LOGGER.info("Databases: %s", len(databases))
        LOGGER.info("Planned jobs: %s", len(jobs))
        for stage in settings.stages:
            LOGGER.info("%s: %s jobs", stage.id, sum(1 for job in jobs if job["stage"] == stage.id))
        return 0

    capture_host_info(settings, host)
    initialise_queue(settings, databases)
    run_pipeline(settings, host)
    if host == settings.coordinator_host:
        aggregate_pipeline(settings)
    else:
        wait_for_pipeline_complete(settings)
    LOGGER.info("Pipeline complete on %s", host)
    return 0


def load_settings(path: Path) -> PipelineSettings:
    profile_path = path.expanduser().resolve()
    data = yaml.safe_load(profile_path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Pipeline settings root must be a mapping: {profile_path}")

    hosts_raw = data.get("hosts")
    if not isinstance(hosts_raw, list) or not hosts_raw:
        raise ValueError("Pipeline settings must contain a non-empty hosts list.")
    hosts = tuple(str(host) for host in hosts_raw)

    limits = cast("dict[str, Any]", data.get("limits") if isinstance(data.get("limits"), dict) else {})
    pipeline = cast("dict[str, Any]", data.get("pipeline") if isinstance(data.get("pipeline"), dict) else {})
    stages_raw = pipeline.get("stages")
    if not isinstance(stages_raw, list) or not stages_raw:
        raise ValueError("Pipeline settings must contain pipeline.stages.")

    stages = tuple(_stage_from_dict(stage, limits) for stage in stages_raw)
    return PipelineSettings(
        database_dir=Path(str(data.get("database_dir", "data/relational"))).expanduser().resolve(),
        output_dir=Path(str(data.get("output", "results/paper_table2"))).expanduser().resolve(),
        logs_dir=Path(str(data.get("logs", "logs/paper_table2"))).expanduser().resolve(),
        queue_dir=Path(str(pipeline.get("queue_dir", "results/paper_pipeline"))).expanduser().resolve(),
        coordinator_host=str(pipeline.get("coordinator_host", hosts[0])),
        hosts=hosts,
        expected_databases=(int(data["expected_databases"]) if "expected_databases" in data else None),
        heartbeat_seconds=int(pipeline.get("heartbeat_seconds", 30)),
        stale_after_seconds=int(pipeline.get("stale_after_seconds", 4200)),
        stages=stages,
    )


def _stage_from_dict(stage: dict[str, Any], limits: dict[str, Any]) -> StageSettings:
    algorithms_raw = stage.get("algorithms", [])
    algorithms = tuple(str(item).upper() for item in algorithms_raw) if isinstance(algorithms_raw, list) else ()
    databases_raw = stage.get("databases", "all")
    databases: str | tuple[str, ...]
    if isinstance(databases_raw, list):
        databases = tuple(str(item) for item in databases_raw)
    else:
        databases = str(databases_raw)
    return StageSettings(
        id=str(stage["id"]),
        experiment=str(stage.get("experiment", "setup")),
        algorithms=algorithms,
        databases=databases,
        workers_per_host=max(1, int(stage.get("workers_per_host", 1))),
        timeout_seconds=int(stage.get("timeout_seconds", limits.get("timeout_seconds", 3600))),
        memory_gb=float(stage.get("memory_gb", limits.get("memory_gb", 10.0))),
        java_heap_gb=int(stage.get("java_heap_gb", limits.get("java_heap_gb", 8))),
    )


def resolve_host(value: str, hosts: tuple[str, ...]) -> str:
    host = socket.gethostname().split(".")[0] if value == "auto" else value
    if host not in hosts:
        raise ValueError(f"Host {host!r} is not in configured hosts: {', '.join(hosts)}")
    return host


def resolve_databases(database_dir: Path, expected_count: int | None) -> list[Path]:
    if not database_dir.exists():
        raise ValueError(f"Database directory not found: {database_dir}")
    databases = sorted(database_dir.glob("*.db"), key=lambda path: path.name.lower())
    if expected_count is not None and len(databases) != expected_count:
        raise ValueError(f"Expected {expected_count} .db files in {database_dir}, found {len(databases)}")
    stems = [path.stem.lower() for path in databases]
    if len(stems) != len(set(stems)):
        raise ValueError("Duplicate database stems found; output paths would collide.")
    return databases


def reset_pipeline(settings: PipelineSettings) -> None:
    shutil.rmtree(settings.queue_dir, ignore_errors=True)


def initialise_queue(settings: PipelineSettings, databases: list[Path]) -> None:
    ready = settings.queue_dir / "pipeline.ready"
    if ready.exists():
        return
    settings.queue_dir.mkdir(parents=True, exist_ok=True)
    lock = settings.queue_dir / "locks" / "init.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        lock.mkdir()
    except FileExistsError:
        while not ready.exists():
            time.sleep(2)
        return

    try:
        if ready.exists():
            return
        jobs = plan_jobs(settings, databases)
        for stage in settings.stages:
            for state in ("pending", "running", "done", "failed"):
                (settings.queue_dir / "queue" / stage.id / state).mkdir(parents=True, exist_ok=True)
        for job in jobs:
            pending = settings.queue_dir / "queue" / str(job["stage"]) / "pending" / f"{job['job_id']}.json"
            pending.write_text(json.dumps(job, indent=2), encoding="utf-8")
        write_pipeline_manifest(settings, jobs)
        ready.write_text(dt.datetime.now(dt.timezone.utc).isoformat(), encoding="utf-8")
    finally:
        with suppress(OSError):
            lock.rmdir()


def write_pipeline_manifest(settings: PipelineSettings, jobs: list[dict[str, Any]]) -> None:
    payload = {
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "jobs": len(jobs),
        "stages": [
            {"id": stage.id, "jobs": sum(1 for job in jobs if job["stage"] == stage.id)} for stage in settings.stages
        ],
    }
    (settings.queue_dir / "pipeline.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


def plan_jobs(settings: PipelineSettings, databases: list[Path], *, write_configs: bool = True) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    for stage in settings.stages:
        if stage.experiment == "setup":
            jobs.append(_setup_job(settings, stage))
        elif stage.experiment == "B2":
            jobs.extend(_b2_jobs(settings, stage, databases, write_configs=write_configs))
        elif stage.experiment == "B3":
            jobs.extend(_b3_jobs(settings, stage, databases, write_configs=write_configs))
        elif stage.experiment == "B1":
            jobs.extend(_b1_jobs(settings, stage, databases, write_configs=write_configs))
        else:
            raise ValueError(f"Unknown pipeline experiment: {stage.experiment}")
    return sorted(jobs, key=lambda job: (str(job["stage"]), -int(job.get("priority", 0)), str(job["job_id"])))


def _selected_databases(stage: StageSettings, databases: list[Path]) -> list[Path]:
    if stage.databases == "all":
        selected = databases
    elif isinstance(stage.databases, tuple):
        wanted = {name.removesuffix(".db").lower() for name in stage.databases}
        selected = [db for db in databases if db.stem.lower() in wanted]
    else:
        wanted = {str(stage.databases).removesuffix(".db").lower()}
        selected = [db for db in databases if db.stem.lower() in wanted]
    return sorted(selected, key=lambda path: (-path.stat().st_size, path.name.lower()))


def _setup_job(settings: PipelineSettings, stage: StageSettings) -> dict[str, Any]:
    stdout = settings.queue_dir / "stdout" / "setup_table_counts.stdout"
    command = [str(Path("scripts/paper_alignment/b0_table1_counts.sh").resolve())]
    return _job_payload(
        stage=stage,
        job_id=f"{stage.id}__table_counts",
        experiment="B0",
        algorithm=None,
        database=None,
        command=command,
        config_path=None,
        stdout_path=stdout,
        priority=1,
    )


def _b1_jobs(
    settings: PipelineSettings,
    stage: StageSettings,
    databases: list[Path],
    *,
    write_configs: bool,
) -> list[dict[str, Any]]:
    selected = _selected_databases(stage, databases)
    jobs: list[dict[str, Any]] = []
    for algorithm in stage.algorithms:
        for database in _stage_order(selected, stage.id, algorithm):
            if write_configs:
                spec = paper_benchmark._build_run_spec(
                    algorithm=algorithm,
                    database=database,
                    database_dir=settings.database_dir,
                    output_dir=settings.output_dir,
                    log_root=settings.logs_dir,
                    timeout=stage.timeout_seconds,
                    memory_gb=stage.memory_gb,
                    java_heap_gb=stage.java_heap_gb,
                )
                command = spec.command
                config_path = spec.config_path
                stdout_path = spec.stdout_path
            else:
                config_path = settings.output_dir / "configs" / f"{algorithm.lower()}_{database.stem}.yaml"
                command = _command_for_algorithm(algorithm, config_path)
                stdout_path = settings.output_dir / "timings" / f"{algorithm.lower()}_{database.stem}.stdout"
            jobs.append(
                _job_payload(
                    stage=stage,
                    job_id=f"{stage.id}__{algorithm}__{database.stem}",
                    experiment="B1",
                    algorithm=algorithm,
                    database=database.name,
                    command=command,
                    config_path=config_path,
                    stdout_path=stdout_path,
                    priority=database.stat().st_size,
                )
            )
    return jobs


def _command_for_algorithm(algorithm: str, config_path: Path) -> list[str]:
    if algorithm == "MARITA":
        return [sys.executable, "-m", "marita.cli.main", "run", "--config", str(config_path)]
    return [
        sys.executable,
        "-m",
        "marita.cli.main",
        "benchmark",
        "--config",
        str(config_path),
        "--baseline",
        algorithm,
    ]


def _b2_jobs(
    settings: PipelineSettings,
    stage: StageSettings,
    databases: list[Path],
    *,
    write_configs: bool,
) -> list[dict[str, Any]]:
    selected = _selected_databases(stage, databases)
    jobs: list[dict[str, Any]] = []
    for database in selected:
        for mode, flag in (("disjoint", True), ("nondisjoint", False)):
            for walk_length in range(1, 11):
                output_dir = Path(f"results/ablation_{mode}/N{walk_length}").resolve()
                log_dir = Path(f"logs/ablation_{mode}/N{walk_length}").resolve()
                config_path = settings.queue_dir / "configs" / "b2" / f"{mode}_N{walk_length}_{database.stem}.yaml"
                config = _marita_config(
                    database=settings.database_dir,
                    database_name=database.name,
                    output_dir=output_dir,
                    log_dir=log_dir,
                    timeout=stage.timeout_seconds,
                    memory_gb=stage.memory_gb,
                    parameters={"walk_length": walk_length, "disjoint_semantics": flag},
                )
                if write_configs:
                    _write_yaml(config_path, config)
                jobs.append(
                    _job_payload(
                        stage=stage,
                        job_id=f"{stage.id}__{mode}__N{walk_length}__{database.stem}",
                        experiment="B2",
                        algorithm="MARITA",
                        database=database.name,
                        command=[sys.executable, "-m", "marita.cli.main", "run", "--config", str(config_path)],
                        config_path=config_path,
                        stdout_path=output_dir / "stdout.log",
                        priority=database.stat().st_size,
                    )
                )
    return jobs


def _b3_jobs(
    settings: PipelineSettings,
    stage: StageSettings,
    databases: list[Path],
    *,
    write_configs: bool,
) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    for database in _stage_order(_selected_databases(stage, databases), stage.id, "MARITA"):
        for mode, joinability in (("fk_only", "fk"), ("full_join", "full")):
            output_dir = Path(f"results/ablation_join/{mode}/{database.stem}").resolve()
            log_dir = Path(f"logs/ablation_join/{mode}/{database.stem}").resolve()
            config_path = settings.queue_dir / "configs" / "b3" / f"{mode}_{database.stem}.yaml"
            config = _marita_config(
                database=settings.database_dir,
                database_name=database.name,
                output_dir=output_dir,
                log_dir=log_dir,
                timeout=stage.timeout_seconds,
                memory_gb=stage.memory_gb,
                parameters={"walk_length": 3, "disjoint_semantics": True, "joinability": joinability},
            )
            if write_configs:
                _write_yaml(config_path, config)
            jobs.append(
                _job_payload(
                    stage=stage,
                    job_id=f"{stage.id}__{mode}__{database.stem}",
                    experiment="B3",
                    algorithm="MARITA",
                    database=database.name,
                    command=[sys.executable, "-m", "marita.cli.main", "run", "--config", str(config_path)],
                    config_path=config_path,
                    stdout_path=output_dir / "stdout.log",
                    priority=database.stat().st_size,
                )
            )
    return jobs


def _stage_order(databases: list[Path], stage_id: str, algorithm: str) -> list[Path]:
    return sorted(
        databases,
        key=lambda path: (-path.stat().st_size, _stable_hash(f"{stage_id}:{algorithm}:{path.stem}")),
    )


def _stable_hash(value: str) -> int:
    return int(hashlib.sha256(value.encode()).hexdigest()[:12], 16)


def _marita_config(
    *,
    database: Path,
    database_name: str,
    output_dir: Path,
    log_dir: Path,
    timeout: int,
    memory_gb: float,
    parameters: dict[str, Any],
) -> dict[str, Any]:
    merged_parameters = {
        "walk_length": 3,
        "max_tables": 3,
        "max_variables": 3,
        "disjoint_semantics": True,
        "support_threshold": 1,
        "timeout": timeout,
    }
    merged_parameters.update(parameters)
    merged_parameters["timeout"] = timeout
    return {
        "monitor": {"memory_threshold": int(memory_gb * 1024**3), "timeout": timeout},
        "database": {"path": str(database), "name": database_name},
        "logging": {"log_dir": str(log_dir)},
        "results": {"output_dir": str(output_dir)},
        "algorithm": {"name": "MARITA", "parameters": merged_parameters},
        "mlflow": {"use": False},
    }


def _write_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _job_payload(
    *,
    stage: StageSettings,
    job_id: str,
    experiment: str,
    algorithm: str | None,
    database: str | None,
    command: list[str],
    config_path: Path | None,
    stdout_path: Path,
    priority: int,
) -> dict[str, Any]:
    return {
        "job_id": job_id,
        "stage": stage.id,
        "experiment": experiment,
        "algorithm": algorithm,
        "database": database,
        "command": command,
        "config_path": str(config_path) if config_path else None,
        "stdout_path": str(stdout_path),
        "timeout_seconds": stage.timeout_seconds,
        "memory_gb": stage.memory_gb,
        "java_heap_gb": stage.java_heap_gb,
        "attempt": 1,
        "max_retries": 0,
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "priority": priority,
    }


def run_pipeline(settings: PipelineSettings, host: str) -> None:
    for stage in settings.stages:
        LOGGER.info("Starting stage %s on %s", stage.id, host)
        recover_stale_jobs(settings, stage)
        run_stage(settings, stage, host)
        wait_for_stage_complete(settings, stage)
        LOGGER.info("Finished stage %s on %s", stage.id, host)


def run_stage(settings: PipelineSettings, stage: StageSettings, host: str) -> None:
    with ThreadPoolExecutor(max_workers=stage.workers_per_host) as executor:
        futures: set[Future[None]] = {
            executor.submit(worker_loop, settings, stage, host) for _ in range(stage.workers_per_host)
        }
        while futures:
            done, futures = wait(futures, return_when=FIRST_COMPLETED)
            for future in done:
                future.result()


def worker_loop(settings: PipelineSettings, stage: StageSettings, host: str) -> None:
    while True:
        recover_stale_jobs(settings, stage)
        claim = claim_job(settings, stage, host)
        if claim is None:
            return
        running_path, job = claim
        heartbeat_path = running_path.with_suffix(".heartbeat.json")
        result = run_job(job, heartbeat_path, host)
        finish_job(settings, stage, running_path, heartbeat_path, job, result)


def claim_job(settings: PipelineSettings, stage: StageSettings, host: str) -> tuple[Path, dict[str, Any]] | None:
    pending_dir = settings.queue_dir / "queue" / stage.id / "pending"
    running_dir = settings.queue_dir / "queue" / stage.id / "running"
    running_dir.mkdir(parents=True, exist_ok=True)
    for pending_path in sorted(pending_dir.glob("*.json")):
        running_path = running_dir / f"{pending_path.stem}__{host}__{os.getpid()}.json"
        try:
            pending_path.rename(running_path)
        except FileNotFoundError:
            continue
        except OSError:
            continue
        job = json.loads(running_path.read_text(encoding="utf-8"))
        job["claimed_by"] = host
        job["claimed_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        running_path.write_text(json.dumps(job, indent=2), encoding="utf-8")
        return running_path, job
    return None


def run_job(job: dict[str, Any], heartbeat_path: Path, host: str) -> dict[str, Any]:
    spec = paper_benchmark.RunSpec(
        algorithm=str(job.get("algorithm") or job.get("experiment") or "JOB"),
        database=Path(str(job.get("database") or job["job_id"])),
        config_path=Path(str(job["config_path"])) if job.get("config_path") else Path(""),
        command=[str(item) for item in job["command"]],
        stdout_path=Path(str(job["stdout_path"])),
    )
    heartbeat_path.write_text(
        json.dumps({"host": host, "updated_at": dt.datetime.now(dt.timezone.utc).isoformat()}, indent=2),
        encoding="utf-8",
    )
    result = paper_benchmark._run_command(
        spec,
        timeout=int(job["timeout_seconds"]),
        memory_gb=float(job["memory_gb"]),
    )
    result["job_id"] = job["job_id"]
    result["stage"] = job["stage"]
    result["experiment"] = job["experiment"]
    result["pipeline_host"] = host
    return result


def finish_job(
    settings: PipelineSettings,
    stage: StageSettings,
    running_path: Path,
    heartbeat_path: Path,
    job: dict[str, Any],
    result: dict[str, Any],
) -> None:
    state = "done" if result.get("status") in DONE_STATUSES else "failed"
    target = settings.queue_dir / "queue" / stage.id / state / f"{job['job_id']}.json"
    payload = {**job, "result": result, "finished_at": dt.datetime.now(dt.timezone.utc).isoformat()}
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    with suppress(FileNotFoundError):
        running_path.unlink()
    with suppress(FileNotFoundError):
        heartbeat_path.unlink()


def recover_stale_jobs(settings: PipelineSettings, stage: StageSettings) -> None:
    running_dir = settings.queue_dir / "queue" / stage.id / "running"
    pending_dir = settings.queue_dir / "queue" / stage.id / "pending"
    now = time.time()
    for running_path in running_dir.glob("*.json"):
        if running_path.name.endswith(".heartbeat.json"):
            continue
        heartbeat_path = running_path.with_suffix(".heartbeat.json")
        marker = heartbeat_path if heartbeat_path.exists() else running_path
        try:
            marker_mtime = marker.stat().st_mtime
        except FileNotFoundError:
            continue
        if now - marker_mtime < settings.stale_after_seconds:
            continue
        try:
            job = json.loads(running_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            continue
        if int(job.get("attempt", 1)) > int(job.get("max_retries", 0)):
            failed = settings.queue_dir / "queue" / stage.id / "failed" / f"{job['job_id']}.json"
            job["result"] = {"status": "error", "error": "stale running job exceeded retry budget"}
            failed.write_text(json.dumps(job, indent=2), encoding="utf-8")
        else:
            job["attempt"] = int(job.get("attempt", 1)) + 1
            (pending_dir / f"{job['job_id']}.json").write_text(json.dumps(job, indent=2), encoding="utf-8")
        running_path.unlink(missing_ok=True)
        heartbeat_path.unlink(missing_ok=True)


def wait_for_stage_complete(settings: PipelineSettings, stage: StageSettings) -> None:
    pending_dir = settings.queue_dir / "queue" / stage.id / "pending"
    running_dir = settings.queue_dir / "queue" / stage.id / "running"
    while True:
        recover_stale_jobs(settings, stage)
        if not any(pending_dir.glob("*.json")) and not any(running_dir.glob("*.json")):
            return
        time.sleep(5)


def capture_host_info(settings: PipelineSettings, host: str) -> None:
    target = settings.output_dir / f"host_info_{host}.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# Host info captured {dt.datetime.now(dt.timezone.utc).isoformat()}", f"hostname: {host}"]
    for path in ("/proc/cpuinfo", "/proc/meminfo"):
        try:
            lines.append(f"\n## {path}")
            lines.extend(Path(path).read_text(encoding="utf-8", errors="replace").splitlines()[:20])
        except OSError:
            continue
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")


def aggregate_pipeline(settings: PipelineSettings) -> None:
    lock = settings.queue_dir / "locks" / "aggregate.lock"
    try:
        lock.mkdir(parents=True)
    except FileExistsError:
        wait_for_pipeline_complete(settings)
        return
    try:
        write_b1_summary(settings)
        write_b2_coords()
        write_b3_comparison(settings.database_dir)
        _run_script("scripts/paper_alignment/b1c_table2_aggregate.sh")
        _run_script("scripts/paper_alignment/final_summary.sh")
        (settings.queue_dir / "pipeline.complete").write_text(
            dt.datetime.now(dt.timezone.utc).isoformat(), encoding="utf-8"
        )
    finally:
        with suppress(OSError):
            lock.rmdir()


def wait_for_pipeline_complete(settings: PipelineSettings) -> None:
    complete = settings.queue_dir / "pipeline.complete"
    while not complete.exists():
        time.sleep(10)


def write_b1_summary(settings: PipelineSettings) -> None:
    runs: list[dict[str, Any]] = []
    for done_path in (settings.queue_dir / "queue").glob("*/done/*.json"):
        payload = json.loads(done_path.read_text(encoding="utf-8"))
        if payload.get("experiment") != "B1":
            continue
        result = payload.get("result")
        if isinstance(result, dict):
            runs.append(result)
    summary = {
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "dry_run": False,
        "host": "pipeline",
        "planned_runs": len(runs),
        "runs": runs,
        "plan": [],
    }
    (settings.output_dir / "summary_pipeline.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")


def write_b2_coords() -> None:
    out = Path("results/ablation_disjoint/figure_coords.tsv")
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = ["mode\tN\trules\twall_s"]
    for mode in ("disjoint", "nondisjoint"):
        for walk_length in range(1, 11):
            et = Path(
                f"results/ablation_{mode}/N{walk_length}/MARITA_Biodegradability/execution_time_Biodegradability.json"
            )
            if not et.exists():
                et = Path(f"results/ablation_{mode}/N{walk_length}/execution_time_Biodegradability.json")
            if not et.exists():
                lines.append(f"{mode}\t{walk_length}\tTIMEOUT_OR_MISSING\t-")
                continue
            payload = json.loads(et.read_text(encoding="utf-8"))
            lines.append(
                f"{mode}\t{walk_length}\t{payload.get('rules_count', '?')}\t{payload.get('execution_time_seconds', '?')}"
            )
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_b3_comparison(database_dir: Path) -> None:
    out = Path("results/ablation_join/comparison.tsv")
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = ["database\tfk_only_rules_hash\tfull_join_rules_hash\tequivalent\tfk_time\tfull_time"]
    for db_path in sorted(database_dir.glob("*.db"), key=lambda path: path.name.lower()):
        db = db_path.stem
        fk_rules = Path(f"results/ablation_join/fk_only/{db}/MARITA_{db}/MARITA_{db}_results.json")
        fj_rules = Path(f"results/ablation_join/full_join/{db}/MARITA_{db}/MARITA_{db}_results.json")
        fk_et = Path(f"results/ablation_join/fk_only/{db}/MARITA_{db}/execution_time_{db}.json")
        fj_et = Path(f"results/ablation_join/full_join/{db}/MARITA_{db}/execution_time_{db}.json")
        if not fk_et.exists():
            fk_et = Path(f"results/ablation_join/fk_only/{db}/execution_time_{db}.json")
        if not fj_et.exists():
            fj_et = Path(f"results/ablation_join/full_join/{db}/execution_time_{db}.json")
        h1, h2 = _rule_set_hash(fk_rules), _rule_set_hash(fj_rules)
        fk_t = (
            json.loads(fk_et.read_text(encoding="utf-8")).get("execution_time_seconds", "-") if fk_et.exists() else "-"
        )
        fj_t = (
            json.loads(fj_et.read_text(encoding="utf-8")).get("execution_time_seconds", "-") if fj_et.exists() else "-"
        )
        lines.append(f"{db}\t{h1}\t{h2}\t{'OK' if h1 == h2 else 'DIFF'}\t{fk_t}\t{fj_t}")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _rule_set_hash(path: Path) -> str:
    if not path.exists():
        return "MISSING"
    rules = sorted(json.loads(path.read_text(encoding="utf-8")), key=lambda rule: rule.get("display", ""))
    digest = hashlib.sha256()
    for rule in rules:
        digest.update(rule.get("display", "").encode())
    return f"{digest.hexdigest()[:12]} ({len(rules)} rules)"


def _run_script(path: str) -> None:
    spec = paper_benchmark.RunSpec(
        algorithm="SCRIPT",
        database=Path(path),
        config_path=Path(""),
        command=[str(Path(path).resolve())],
        stdout_path=Path("logs/paper_pipeline") / f"{Path(path).stem}.stdout",
    )
    paper_benchmark._run_command(spec, timeout=3600, memory_gb=10)


def print_status(settings: PipelineSettings) -> None:
    if not settings.queue_dir.exists():
        print(f"Pipeline queue does not exist: {settings.queue_dir}")
        return
    for stage in settings.stages:
        base = settings.queue_dir / "queue" / stage.id
        counts = {state: len(list((base / state).glob("*.json"))) for state in ("pending", "running", "done", "failed")}
        print(
            f"{stage.id}: pending={counts['pending']} running={counts['running']} done={counts['done']} failed={counts['failed']}"
        )


if __name__ == "__main__":
    raise SystemExit(main())
