from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import psutil
import yaml

from mahilda.cli.artifacts import format_duration

PAPER_DATABASES = [
    "Biodegradability.db",
    "CDESchools.db",
    "Chess.db",
    "CORA.db",
    "CraftBeer.db",
    "Countries.db",
    "Dunur.db",
    "nations.db",
    "PTE.db",
    "SAT.db",
]

ALGORITHMS = ("MAHILDA", "AMIE3", "SPIDER", "POPPER", "MATILDA")
MEMORY_BYTES_PER_GB = 1024**3


@dataclass(frozen=True)
class RunSpec:
    algorithm: str
    database: Path
    config_path: Path
    command: list[str]
    stdout_path: Path


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the extracted ISWC 2026 paper benchmark protocol across selected databases and algorithms."
    )
    parser.add_argument(
        "--database-dir",
        default="data/relational",
        help="Directory containing relational benchmark .db files (default: data/relational).",
    )
    parser.add_argument(
        "--output",
        default="results/iswc2026",
        help="Output directory for generated configs, run artifacts, and summary (default: results/iswc2026).",
    )
    parser.add_argument(
        "--logs",
        default="logs/iswc2026",
        help="Log root for generated configs (default: logs/iswc2026).",
    )
    parser.add_argument(
        "--algorithms",
        default="MAHILDA",
        help="Comma-separated algorithms to run: MAHILDA, AMIE3, SPIDER, POPPER, MATILDA, or ALL (default: MAHILDA).",
    )
    parser.add_argument(
        "--databases",
        default="paper",
        help="Comma-separated database names, 'paper' for the 10 reported DBs, or 'all' for every .db (default: paper).",
    )
    parser.add_argument("--timeout", type=int, default=7200, help="Per-run wall-clock timeout in seconds.")
    parser.add_argument("--memory-gb", type=float, default=15.0, help="Per-run RSS memory limit in GB.")
    parser.add_argument("--java-heap-gb", type=int, default=None, help="Java -Xmx heap size for Java baselines.")
    parser.add_argument("--settings", default=None, help="YAML benchmark profile with hosts, limits, and workers.")
    parser.add_argument("--host", default=None, help="Host shard to run, or 'auto' to use hostname -s.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate configs and summary plan without executing benchmark commands.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_arguments(argv)
    logger = logging.getLogger(__name__)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    try:
        profile = _load_profile(args.settings)
        _apply_profile_defaults(args, profile)
        host = _resolve_host(args.host, profile)
    except ValueError as exc:
        logger.error("%s", exc)
        return 1

    database_dir = Path(args.database_dir).expanduser().resolve()
    output_dir = Path(args.output).expanduser().resolve()
    log_root = Path(args.logs).expanduser().resolve()
    java_heap_gb = args.java_heap_gb if args.java_heap_gb is not None else max(1, int(args.memory_gb) - 2)

    try:
        algorithms = _parse_algorithms(args.algorithms)
        databases = _resolve_databases(database_dir, args.databases)
        databases = _shard_databases(databases, profile.get("hosts", []), host)
    except ValueError as exc:
        logger.error("%s", exc)
        return 1

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "configs").mkdir(parents=True, exist_ok=True)
    log_root.mkdir(parents=True, exist_ok=True)

    specs = [
        _build_run_spec(
            algorithm=algorithm,
            database=db,
            database_dir=database_dir,
            output_dir=output_dir,
            log_root=log_root,
            timeout=args.timeout,
            memory_gb=args.memory_gb,
            java_heap_gb=java_heap_gb,
        )
        for algorithm in algorithms
        for db in databases
    ]

    if args.dry_run:
        _write_summary(output_dir, specs, [], dry_run=True, host=host)
        for spec in specs:
            logger.info("DRY RUN: %s", " ".join(spec.command))
        logger.info("Wrote dry-run plan to %s", _summary_path(output_dir, host, ".json"))
        return 0

    results: list[dict[str, Any]] = []
    workers = _profile_workers(profile)
    for algorithm in algorithms:
        algorithm_specs = [spec for spec in specs if spec.algorithm == algorithm]
        parallelism = workers.get(algorithm, 1)
        logger.info("Running %s: %s jobs with %s worker(s)", algorithm, len(algorithm_specs), parallelism)
        results.extend(_run_specs(algorithm_specs, timeout=args.timeout, memory_gb=args.memory_gb, workers=parallelism))

    _write_summary(output_dir, specs, results, dry_run=False, host=host)
    failed = [result for result in results if result["status"] != "success"]
    if failed:
        logger.warning("Completed with %s non-successful runs. See %s", len(failed), _summary_path(output_dir, host, ".json"))
        return 1
    logger.info("All runs completed successfully. See %s", _summary_path(output_dir, host, ".json"))
    return 0


def _load_profile(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    profile_path = Path(path).expanduser().resolve()
    with profile_path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Benchmark settings root must be a mapping: {profile_path}")
    return data


def _apply_profile_defaults(args: argparse.Namespace, profile: dict[str, Any]) -> None:
    if not profile:
        return
    if "database_dir" in profile:
        args.database_dir = str(profile["database_dir"])
    if "output" in profile:
        args.output = str(profile["output"])
    if "logs" in profile:
        args.logs = str(profile["logs"])
    if "databases" in profile:
        args.databases = str(profile["databases"])
    if "algorithms" in profile and args.algorithms == "MAHILDA":
        args.algorithms = ",".join(str(item) for item in profile["algorithms"])
    limits_raw = profile.get("limits")
    limits = cast("dict[str, Any]", limits_raw) if isinstance(limits_raw, dict) else {}
    if "timeout_seconds" in limits:
        args.timeout = int(limits["timeout_seconds"])
    if "memory_gb" in limits:
        args.memory_gb = float(limits["memory_gb"])
    if "java_heap_gb" in limits and args.java_heap_gb is None:
        args.java_heap_gb = int(limits["java_heap_gb"])


def _resolve_host(value: str | None, profile: dict[str, Any]) -> str | None:
    if not value:
        return None
    host = socket.gethostname().split(".")[0] if value == "auto" else value
    hosts = profile.get("hosts", [])
    if hosts and host not in hosts:
        allowed = ", ".join(str(item) for item in hosts)
        raise ValueError(f"Host {host!r} is not in benchmark settings hosts: {allowed}")
    return host


def _shard_databases(databases: list[Path], hosts: Any, host: str | None) -> list[Path]:
    if not host:
        return databases
    if not isinstance(hosts, list) or not hosts:
        raise ValueError("--host requires a non-empty hosts list in --settings.")
    host_names = [str(item) for item in hosts]
    host_index = host_names.index(host)
    return [db for index, db in enumerate(databases) if index % len(host_names) == host_index]


def _profile_workers(profile: dict[str, Any]) -> dict[str, int]:
    workers_raw = profile.get("workers")
    raw = cast("dict[str, Any]", workers_raw) if isinstance(workers_raw, dict) else {}
    defaults = {"MAHILDA": 1, "SPIDER": 1, "AMIE3": 1, "POPPER": 1, "MATILDA": 1}
    for key, value in raw.items():
        defaults[str(key).upper()] = max(1, int(value))
    return defaults


def _parse_algorithms(value: str) -> list[str]:
    requested = [item.strip().upper() for item in value.split(",") if item.strip()]
    if not requested:
        raise ValueError("At least one algorithm must be selected.")
    if requested == ["ALL"]:
        return list(ALGORITHMS)
    unknown = [algorithm for algorithm in requested if algorithm not in ALGORITHMS]
    if unknown:
        raise ValueError(f"Unknown algorithm(s): {', '.join(unknown)}. Allowed: {', '.join(ALGORITHMS)}, ALL.")
    return requested


def _resolve_databases(database_dir: Path, value: str) -> list[Path]:
    if not database_dir.exists():
        raise ValueError(f"Database directory not found: {database_dir}")
    available = sorted(database_dir.glob("*.db"), key=lambda path: path.name.lower())
    if not available:
        raise ValueError(f"No .db files found in {database_dir}")

    requested_raw = [item.strip() for item in value.split(",") if item.strip()]
    requested_key = value.strip().lower()
    if requested_key == "all":
        return available
    if requested_key == "paper":
        requested_raw = PAPER_DATABASES
    if not requested_raw:
        raise ValueError("At least one database must be selected.")

    by_name = {path.name.lower(): path for path in available}
    by_stem = {path.stem.lower(): path for path in available}
    selected: list[Path] = []
    missing: list[str] = []
    for raw_name in requested_raw:
        candidate_name = raw_name if raw_name.lower().endswith(".db") else f"{raw_name}.db"
        candidate = by_name.get(candidate_name.lower()) or by_stem.get(Path(raw_name).stem.lower())
        if candidate is None:
            missing.append(raw_name)
            continue
        selected.append(candidate)

    if missing:
        raise ValueError(f"Database(s) not found in {database_dir}: {', '.join(missing)}")
    return selected


def _build_run_spec(
    *,
    algorithm: str,
    database: Path,
    database_dir: Path,
    output_dir: Path,
    log_root: Path,
    timeout: int,
    memory_gb: float = 15.0,
    java_heap_gb: int = 13,
) -> RunSpec:
    config_path = output_dir / "configs" / f"{algorithm.lower()}_{database.stem}.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    algorithm_output = output_dir / algorithm
    algorithm_log_dir = log_root / algorithm / database.stem
    config = _build_config(
        algorithm=algorithm,
        database=database,
        database_dir=database_dir,
        output_dir=algorithm_output,
        log_dir=algorithm_log_dir,
        timeout=timeout,
        memory_gb=memory_gb,
        java_heap_gb=java_heap_gb,
    )
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

    if algorithm == "MAHILDA":
        command = [sys.executable, "-m", "mahilda.cli.main", "run", "--config", str(config_path)]
    else:
        command = [
            sys.executable,
            "-m",
            "mahilda.cli.main",
            "benchmark",
            "--config",
            str(config_path),
            "--baseline",
            algorithm,
        ]

    stdout_path = output_dir / "timings" / f"{algorithm.lower()}_{database.stem}.stdout"
    return RunSpec(algorithm=algorithm, database=database, config_path=config_path, command=command, stdout_path=stdout_path)


def _build_config(
    *,
    algorithm: str,
    database: Path,
    database_dir: Path,
    output_dir: Path,
    log_dir: Path,
    timeout: int,
    memory_gb: float,
    java_heap_gb: int,
) -> dict[str, Any]:
    memory_threshold = int(memory_gb * MEMORY_BYTES_PER_GB)
    config: dict[str, Any] = {
        "monitor": {
            "memory_threshold": memory_threshold,
            "timeout": timeout,
        },
        "database": {
            "path": str(database_dir),
            "name": database.name,
        },
        "logging": {
            "log_dir": str(log_dir),
        },
        "results": {
            "output_dir": str(output_dir),
        },
        "algorithm": {
            "name": algorithm,
        },
        "batch": {
            "workers": 1,
            "timeout": timeout,
        },
        "mlflow": {
            "use": False,
            "tracking_uri": "file:mlruns",
            "experiment_name": "ISWC 2026 Benchmark",
        },
    }
    if algorithm == "MAHILDA":
        config["algorithm"]["parameters"] = {
            "walk_length": 3,
            "max_tables": 3,
            "max_variables": 3,
            "disjoint_semantics": True,
            "split_mean_threshold": 0.0,
            "timeout": timeout,
        }
    else:
        config["benchmark"] = {
            "baseline": algorithm,
            "timeout": timeout,
            "memory_gb": memory_gb,
            "java_heap_gb": java_heap_gb,
        }
        if algorithm == "POPPER":
            config["benchmark"]["popper_command"] = "run-popper"
        if algorithm == "MATILDA":
            config["benchmark"]["matilda_path"] = str(Path(__file__).resolve().parents[3].parent / "MATILDA")
    return config


def _run_specs(specs: list[RunSpec], *, timeout: int, memory_gb: float, workers: int) -> list[dict[str, Any]]:
    if workers <= 1:
        return [_run_command(spec, timeout=timeout, memory_gb=memory_gb) for spec in specs]
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        pending: set[Future[dict[str, Any]]] = {
            executor.submit(_run_command, spec, timeout=timeout, memory_gb=memory_gb) for spec in specs
        }
        while pending:
            done, pending = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                results.append(future.result())
    return results


def _run_command(spec: RunSpec, *, timeout: int, memory_gb: float) -> dict[str, Any]:
    start = time.time()
    started_at = dt.datetime.now(dt.timezone.utc)
    spec.stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stdout_handle = spec.stdout_path.open("w", encoding="utf-8")
    process = subprocess.Popen(spec.command, stdout=stdout_handle, stderr=subprocess.STDOUT, start_new_session=True)
    oom = threading.Event()
    peak_rss = {"bytes": 0}
    monitor_thread = threading.Thread(
        target=_monitor_memory,
        args=(process, memory_gb, oom, peak_rss),
        daemon=True,
    )
    monitor_thread.start()

    status = "success"
    error: str | None = None
    try:
        return_code = process.wait(timeout=timeout)
        if oom.is_set():
            status = "oom"
            error = f"Memory limit exceeded: {memory_gb} GB"
        elif return_code != 0:
            status = "error"
            error = f"Command exited with code {return_code}"
    except subprocess.TimeoutExpired:
        status = "timeout"
        error = f"Execution exceeded {timeout} seconds"
        _terminate_process_group(process)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            _kill_process_group(process)
            process.wait()
    finally:
        monitor_thread.join(timeout=1)
        stdout_handle.close()

    ended_at = dt.datetime.now(dt.timezone.utc)
    return {
        "algorithm": spec.algorithm,
        "database": spec.database.name,
        "status": status,
        "error": error,
        "duration_seconds": time.time() - start,
        "started_at": started_at.isoformat(),
        "ended_at": ended_at.isoformat(),
        "config": str(spec.config_path),
        "command": spec.command,
        "stdout": str(spec.stdout_path),
        "peak_rss_bytes": peak_rss["bytes"],
        "host": socket.gethostname().split(".")[0],
    }


def _monitor_memory(
    process: subprocess.Popen[Any],
    memory_gb: float,
    oom: threading.Event,
    peak_rss: dict[str, int],
) -> None:
    limit_bytes = memory_gb * MEMORY_BYTES_PER_GB
    try:
        root = psutil.Process(process.pid)
    except psutil.Error:
        return
    while process.poll() is None:
        try:
            processes = [root] + root.children(recursive=True)
            usage = sum(child.memory_info().rss for child in processes if child.is_running())
        except psutil.Error:
            return
        peak_rss["bytes"] = max(peak_rss["bytes"], usage)
        if usage > limit_bytes:
            oom.set()
            _terminate_process_group(process)
            return
        time.sleep(0.5)


def _terminate_process_group(process: subprocess.Popen[Any]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except OSError:
        process.terminate()


def _kill_process_group(process: subprocess.Popen[Any]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    except OSError:
        process.kill()


def _summary_path(output_dir: Path, host: str | None, suffix: str) -> Path:
    name = f"summary_{host}{suffix}" if host else f"summary{suffix}"
    return output_dir / name


def _write_summary(output_dir: Path, specs: list[RunSpec], results: list[dict[str, Any]], *, dry_run: bool, host: str | None) -> None:
    summary_json = _summary_path(output_dir, host, ".json")
    if not dry_run and summary_json.exists():
        try:
            existing = json.loads(summary_json.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = {}
        if isinstance(existing, dict) and not existing.get("dry_run"):
            existing_runs = existing.get("runs", [])
            if isinstance(existing_runs, list):
                by_pair = {
                    (run.get("algorithm"), run.get("database")): run
                    for run in existing_runs
                    if isinstance(run, dict)
                }
                for run in results:
                    by_pair[(run.get("algorithm"), run.get("database"))] = run
                results = list(by_pair.values())

    summary = {
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "dry_run": dry_run,
        "host": host,
        "planned_runs": len(specs),
        "runs": results,
        "plan": [
            {
                "algorithm": spec.algorithm,
                "database": spec.database.name,
                "config": str(spec.config_path),
                "command": spec.command,
            }
            for spec in specs
        ],
    }
    summary_json.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    lines = [
        "# ISWC 2026 Benchmark Summary",
        "",
        f"Created: {summary['created_at']}",
        f"Dry run: {dry_run}",
        f"Planned runs: {len(specs)}",
        "",
        "| Algorithm | Database | Status | Duration | Config |",
        "| --- | --- | --- | ---: | --- |",
    ]
    if results:
        for result in results:
            duration = format_duration(float(result["duration_seconds"]))
            lines.append(
                f"| {result['algorithm']} | {result['database']} | {result['status']} | {duration} | {result['config']} |"
            )
    else:
        for spec in specs:
            lines.append(f"| {spec.algorithm} | {spec.database.name} | planned | N/A | {spec.config_path} |")
    lines.append("")
    _summary_path(output_dir, host, ".md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
