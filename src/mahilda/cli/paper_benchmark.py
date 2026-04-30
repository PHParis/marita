from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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

ALGORITHMS = ("MAHILDA", "AMIE3", "SPIDER", "POPPER")
MEMORY_BYTES_PER_GB = 1024**3


@dataclass(frozen=True)
class RunSpec:
    algorithm: str
    database: Path
    config_path: Path
    command: list[str]


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
        help="Comma-separated algorithms to run: MAHILDA, AMIE3, SPIDER, POPPER, or ALL (default: MAHILDA).",
    )
    parser.add_argument(
        "--databases",
        default="paper",
        help="Comma-separated database names, 'paper' for the 10 reported DBs, or 'all' for every .db (default: paper).",
    )
    parser.add_argument("--timeout", type=int, default=7200, help="Per-run wall-clock timeout in seconds.")
    parser.add_argument("--memory-gb", type=float, default=15.0, help="Per-run RSS memory limit in GB.")
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

    database_dir = Path(args.database_dir).expanduser().resolve()
    output_dir = Path(args.output).expanduser().resolve()
    log_root = Path(args.logs).expanduser().resolve()

    try:
        algorithms = _parse_algorithms(args.algorithms)
        databases = _resolve_databases(database_dir, args.databases)
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
        )
        for algorithm in algorithms
        for db in databases
    ]

    if args.dry_run:
        _write_summary(output_dir, specs, [], dry_run=True)
        for spec in specs:
            logger.info("DRY RUN: %s", " ".join(spec.command))
        logger.info("Wrote dry-run plan to %s", output_dir / "summary.json")
        return 0

    results: list[dict[str, Any]] = []
    for spec in specs:
        logger.info("Running %s on %s", spec.algorithm, spec.database.name)
        result = _run_command(spec, timeout=args.timeout, memory_gb=args.memory_gb)
        results.append(result)
        logger.info(
            "%s on %s: %s in %s",
            spec.algorithm,
            spec.database.name,
            result["status"],
            format_duration(float(result["duration_seconds"])),
        )

    _write_summary(output_dir, specs, results, dry_run=False)
    failed = [result for result in results if result["status"] != "success"]
    if failed:
        logger.warning("Completed with %s non-successful runs. See %s", len(failed), output_dir / "summary.json")
        return 1
    logger.info("All runs completed successfully. See %s", output_dir / "summary.json")
    return 0


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

    return RunSpec(algorithm=algorithm, database=database, config_path=config_path, command=command)


def _build_config(
    *,
    algorithm: str,
    database: Path,
    database_dir: Path,
    output_dir: Path,
    log_dir: Path,
    timeout: int,
) -> dict[str, Any]:
    config: dict[str, Any] = {
        "monitor": {
            "memory_threshold": int(15 * MEMORY_BYTES_PER_GB),
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
        }
    return config


def _run_command(spec: RunSpec, *, timeout: int, memory_gb: float) -> dict[str, Any]:
    start = time.time()
    started_at = dt.datetime.now(dt.timezone.utc)
    process = subprocess.Popen(spec.command)
    oom = threading.Event()
    monitor_thread = threading.Thread(
        target=_monitor_memory,
        args=(process, memory_gb, oom),
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
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    finally:
        monitor_thread.join(timeout=1)

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
    }


def _monitor_memory(process: subprocess.Popen[Any], memory_gb: float, oom: threading.Event) -> None:
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
        if usage > limit_bytes:
            oom.set()
            process.terminate()
            return
        time.sleep(0.5)


def _write_summary(output_dir: Path, specs: list[RunSpec], results: list[dict[str, Any]], *, dry_run: bool) -> None:
    summary = {
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "dry_run": dry_run,
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
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

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
    (output_dir / "summary.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
