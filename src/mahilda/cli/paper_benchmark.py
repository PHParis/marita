from __future__ import annotations

import argparse
import ctypes
import datetime as dt
import json
import logging
import os
import re
import signal
import smtplib
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from email.message import EmailMessage
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
DEFAULT_DATABASE_DIR = "data/relational"
DEFAULT_OUTPUT_DIR = "results/iswc2026"
DEFAULT_LOG_DIR = "logs/iswc2026"
DEFAULT_ALGORITHMS = "MAHILDA"
DEFAULT_DATABASES = "paper"
DEFAULT_TIMEOUT_SECONDS = 7200
DEFAULT_MEMORY_GB = 15.0
MEMORY_BYTES_PER_GB = 1024**3
TIMEOUT_EXIT_CODE = 124
PR_SET_PDEATHSIG = 1
SUBPROCESS_CLEANUP_GRACE_SECONDS = 30
DISCOVERED_RULES_RE = re.compile(r"\bDiscovered\s+(\d+)\s+rules\b")


@dataclass(frozen=True)
class RunSpec:
    algorithm: str
    database: Path
    config_path: Path
    command: list[str]
    stdout_path: Path
    progress_path: Path | None = None


@dataclass(frozen=True)
class EmailNotificationConfig:
    to: str
    smtp_host: str
    smtp_port: int
    sender: str
    smtp_user: str | None = None
    smtp_password_env: str = "MAHILDA_SMTP_PASSWORD"
    starttls: bool = False


class EmailNotifier:
    def __init__(self, config: EmailNotificationConfig | None, logger: logging.Logger) -> None:
        self.config = config
        self.logger = logger
        self._sent = False

    @property
    def enabled(self) -> bool:
        return self.config is not None

    def send_once(
        self,
        *,
        status: str,
        exit_code: int | None,
        started_at: dt.datetime,
        ended_at: dt.datetime,
        host: str | None,
        output_dir: Path | None,
        summary_path: Path | None,
        planned_runs: int | None,
        results: list[dict[str, Any]],
        error: str | None = None,
    ) -> None:
        if self.config is None or self._sent:
            return
        self._sent = True
        try:
            _send_email_notification(
                self.config,
                status=status,
                exit_code=exit_code,
                started_at=started_at,
                ended_at=ended_at,
                host=host,
                output_dir=output_dir,
                summary_path=summary_path,
                planned_runs=planned_runs,
                results=results,
                error=error,
            )
            self.logger.info("Sent benchmark notification email to %s", self.config.to)
        except Exception as exc:
            self.logger.error("Failed to send benchmark notification email: %s", exc)


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the extracted ISWC 2026 paper benchmark protocol across selected databases and algorithms."
    )
    parser.add_argument(
        "--database-dir",
        default=None,
        help="Directory containing relational benchmark .db files (default: data/relational).",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output directory for generated configs, run artifacts, and summary (default: results/iswc2026).",
    )
    parser.add_argument(
        "--logs",
        default=None,
        help="Log root for generated configs (default: logs/iswc2026).",
    )
    parser.add_argument(
        "--algorithms",
        default=None,
        help="Comma-separated algorithms to run: MAHILDA, AMIE3, SPIDER, POPPER, MATILDA, or ALL (default: MAHILDA).",
    )
    parser.add_argument(
        "--databases",
        default=None,
        help="Comma-separated database names, 'paper' for the 10 reported DBs, or 'all' for every .db (default: paper).",
    )
    parser.add_argument("--timeout", type=int, default=None, help="Per-run wall-clock timeout in seconds.")
    parser.add_argument("--memory-gb", type=float, default=None, help="Per-run RSS memory limit in GB.")
    parser.add_argument("--java-heap-gb", type=int, default=None, help="Java -Xmx heap size for Java baselines.")
    parser.add_argument("--settings", default=None, help="YAML benchmark profile with hosts, limits, and workers.")
    parser.add_argument("--host", default=None, help="Host shard to run, or 'auto' to use hostname -s.")
    parser.add_argument(
        "--hosts",
        default=None,
        help="Comma-separated hosts for sharding; overrides hosts from --settings.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate configs and summary plan without executing benchmark commands.",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Show file-backed benchmark progress from the output directory and exit.",
    )
    parser.add_argument("--email-to", default=None, help="Send a completion notification to this email address.")
    parser.add_argument("--email-from", default=None, help="Sender address for notification emails.")
    parser.add_argument("--smtp-host", default=None, help="SMTP host for notification emails.")
    parser.add_argument("--smtp-port", type=int, default=None, help="SMTP port for notification emails.")
    parser.add_argument("--smtp-user", default=None, help="SMTP username for notification emails.")
    parser.add_argument(
        "--smtp-password-env",
        default="MAHILDA_SMTP_PASSWORD",
        help="Environment variable containing the SMTP password (default: MAHILDA_SMTP_PASSWORD).",
    )
    parser.add_argument("--smtp-starttls", action="store_true", help="Use STARTTLS for SMTP notifications.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_arguments(argv)
    logger = logging.getLogger(__name__)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    notifier = EmailNotifier(None if args.status else _email_config_from_args(args), logger)
    started_at = dt.datetime.now(dt.timezone.utc)
    host: str | None = None
    output_dir: Path | None = None
    results: list[dict[str, Any]] = []
    planned_runs: int | None = None
    exit_code: int | None = None
    error: str | None = None

    def notify_interrupted(signum: int, _frame: object) -> None:
        ended_at = dt.datetime.now(dt.timezone.utc)
        notifier.send_once(
            status=f"interrupted by signal {signum}",
            exit_code=128 + signum,
            started_at=started_at,
            ended_at=ended_at,
            host=host,
            output_dir=output_dir,
            summary_path=_summary_path(output_dir, host, ".json") if output_dir else None,
            planned_runs=planned_runs,
            results=results,
            error=f"Received signal {signum}",
        )
        raise KeyboardInterrupt

    previous_handlers: dict[int, Any] = {}
    if notifier.enabled and threading.current_thread() is threading.main_thread():
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous_handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, notify_interrupted)

    def finish() -> None:
        if previous_handlers:
            for signum, handler in previous_handlers.items():
                signal.signal(signum, handler)
        ended_at = dt.datetime.now(dt.timezone.utc)
        status = _notification_status(exit_code=exit_code, error=error, results=results, planned_runs=planned_runs)
        notifier.send_once(
            status=status,
            exit_code=exit_code,
            started_at=started_at,
            ended_at=ended_at,
            host=host,
            output_dir=output_dir,
            summary_path=_summary_path(output_dir, host, ".json") if output_dir else None,
            planned_runs=planned_runs,
            results=results,
            error=error,
        )

    try:
        profile = _load_profile(args.settings)
        _apply_profile_defaults(args, profile)
        _apply_builtin_defaults(args)
        hosts = _resolve_hosts(args.hosts, profile)
        host = _resolve_host(args.host, hosts)
    except ValueError as exc:
        logger.error("%s", exc)
        exit_code = 1
        error = str(exc)
        finish()
        return exit_code
    except Exception as exc:
        exit_code = 1
        error = repr(exc)
        logger.exception("Paper benchmark setup failed")
        finish()
        return exit_code

    database_dir = Path(args.database_dir).expanduser().resolve()
    output_dir = Path(args.output).expanduser().resolve()
    log_root = Path(args.logs).expanduser().resolve()
    java_heap_gb = args.java_heap_gb if args.java_heap_gb is not None else max(1, int(args.memory_gb) - 2)

    try:
        algorithms = _parse_algorithms(args.algorithms)
        databases = _resolve_databases(database_dir, args.databases)
    except ValueError as exc:
        logger.error("%s", exc)
        exit_code = 1
        error = str(exc)
        finish()
        return exit_code
    except Exception as exc:
        exit_code = 1
        error = repr(exc)
        logger.exception("Paper benchmark setup failed")
        finish()
        return exit_code

    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "configs").mkdir(parents=True, exist_ok=True)
        log_root.mkdir(parents=True, exist_ok=True)
        status_databases = _shard_databases(databases, hosts, host) if host else databases
        run_databases = _shard_databases(databases, hosts, host)

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
                write_config=not args.status,
            )
            for algorithm in algorithms
            for db in status_databases
        ]

        if args.status:
            print_progress_status(output_dir, specs)
            exit_code = 0
            finish()
            return exit_code

        if run_databases != status_databases:
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
                for db in run_databases
            ]
        planned_runs = len(specs)

        if args.dry_run:
            _write_summary(output_dir, specs, [], dry_run=True, host=host)
            for spec in specs:
                logger.info("DRY RUN: %s", " ".join(spec.command))
            logger.info("Wrote dry-run plan to %s", _summary_path(output_dir, host, ".json"))
            exit_code = 0
            finish()
            return exit_code
    except KeyboardInterrupt:
        exit_code = 130
        error = "Interrupted"
        logger.error("Interrupted")
        finish()
        return exit_code
    except Exception as exc:
        exit_code = 1
        error = repr(exc)
        logger.exception("Paper benchmark setup failed")
        finish()
        return exit_code

    workers = _profile_workers(profile)
    try:
        for algorithm in algorithms:
            algorithm_specs = [spec for spec in specs if spec.algorithm == algorithm]
            parallelism = workers.get(algorithm, 1)
            logger.info("Running %s: %s jobs with %s worker(s)", algorithm, len(algorithm_specs), parallelism)
            results.extend(
                _run_specs(algorithm_specs, timeout=args.timeout, memory_gb=args.memory_gb, workers=parallelism)
            )

        _write_summary(output_dir, specs, results, dry_run=False, host=host)
        failed = [result for result in results if result["status"] != "success"]
        if failed:
            logger.warning(
                "Completed with %s non-successful runs. See %s", len(failed), _summary_path(output_dir, host, ".json")
            )
            exit_code = 1
            return exit_code
        logger.info("All runs completed successfully. See %s", _summary_path(output_dir, host, ".json"))
        exit_code = 0
        return exit_code
    except KeyboardInterrupt:
        exit_code = 130
        error = "Interrupted"
        logger.error("Interrupted")
        return exit_code
    except Exception as exc:
        exit_code = 1
        error = repr(exc)
        logger.exception("Paper benchmark failed")
        return exit_code
    finally:
        finish()


def _email_config_from_args(args: argparse.Namespace) -> EmailNotificationConfig | None:
    recipient = args.email_to or os.environ.get("MAHILDA_EMAIL_TO")
    if not recipient:
        return None

    smtp_host = args.smtp_host or os.environ.get("MAHILDA_SMTP_HOST") or "localhost"
    smtp_port = args.smtp_port or int(os.environ.get("MAHILDA_SMTP_PORT", "25"))
    sender = args.email_from or os.environ.get("MAHILDA_EMAIL_FROM") or recipient
    smtp_user = args.smtp_user or os.environ.get("MAHILDA_SMTP_USER")
    starttls = args.smtp_starttls or _env_flag("MAHILDA_SMTP_STARTTLS")

    return EmailNotificationConfig(
        to=recipient,
        smtp_host=smtp_host,
        smtp_port=smtp_port,
        sender=sender,
        smtp_user=smtp_user,
        smtp_password_env=args.smtp_password_env,
        starttls=starttls,
    )


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _send_email_notification(
    config: EmailNotificationConfig,
    *,
    status: str,
    exit_code: int | None,
    started_at: dt.datetime,
    ended_at: dt.datetime,
    host: str | None,
    output_dir: Path | None,
    summary_path: Path | None,
    planned_runs: int | None,
    results: list[dict[str, Any]],
    error: str | None,
) -> None:
    host_name = host or socket.gethostname().split(".")[0]
    message = EmailMessage()
    message["From"] = config.sender
    message["To"] = config.to
    message["Subject"] = f"MAHILDA paper-benchmark {status} on {host_name}"
    message.set_content(
        _format_email_body(
            status=status,
            exit_code=exit_code,
            started_at=started_at,
            ended_at=ended_at,
            host=host_name,
            output_dir=output_dir,
            summary_path=summary_path,
            planned_runs=planned_runs,
            results=results,
            error=error,
        )
    )

    password = os.environ.get(config.smtp_password_env)
    with smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=30) as smtp:
        if config.starttls:
            smtp.starttls()
        if config.smtp_user:
            smtp.login(config.smtp_user, password or "")
        smtp.send_message(message)


def _format_email_body(
    *,
    status: str,
    exit_code: int | None,
    started_at: dt.datetime,
    ended_at: dt.datetime,
    host: str,
    output_dir: Path | None,
    summary_path: Path | None,
    planned_runs: int | None,
    results: list[dict[str, Any]],
    error: str | None,
) -> str:
    elapsed = ended_at - started_at
    success_count = sum(1 for result in results if result.get("status") == "success")
    failed_count = sum(1 for result in results if result.get("status") != "success")
    completed_count = len(results)
    outcome_counts = _outcome_counts(results)
    planned_label = str(planned_runs) if planned_runs is not None else "unknown"
    controller_status = _controller_status(status=status, planned_runs=planned_runs, completed_runs=completed_count)
    lines = [
        "MAHILDA paper benchmark notification",
        "",
        f"Status: {status}",
        f"Controller: {controller_status}",
        f"Exit code: {exit_code if exit_code is not None else 'unknown'}",
        f"Host: {host}",
        f"Started at: {started_at.isoformat()}",
        f"Ended at: {ended_at.isoformat()}",
        f"Elapsed: {format_duration(elapsed.total_seconds())}",
        f"Output directory: {output_dir if output_dir else 'unknown'}",
        f"Summary: {summary_path if summary_path else 'unknown'}",
        f"Runs: {planned_label} planned, {completed_count} completed, {success_count} success, {failed_count} non-success",
    ]
    if outcome_counts:
        lines.append(f"Outcomes: {_format_outcome_counts(outcome_counts)}")
    if error:
        lines.extend(["", f"Error: {error}"])
    return "\n".join(lines) + "\n"


def _notification_status(
    *, exit_code: int | None, error: str | None, results: list[dict[str, Any]], planned_runs: int | None
) -> str:
    if exit_code == 0:
        return "success"
    if exit_code == 130:
        return "interrupted"
    if error:
        return "failed"
    if results and planned_runs is not None and len(results) >= planned_runs:
        return "completed with non-success runs"
    if results:
        return "incomplete with non-success runs"
    return "failed"


def _controller_status(*, status: str, planned_runs: int | None, completed_runs: int) -> str:
    if status == "interrupted" or status.startswith("interrupted by signal"):
        return "interrupted"
    if status == "failed":
        return "failed"
    if planned_runs is not None and completed_runs >= planned_runs:
        return "completed all planned runs"
    if planned_runs is None:
        return "unknown"
    return "incomplete"


def _outcome_counts(results: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for result in results:
        status = str(result.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return counts


def _format_outcome_counts(counts: dict[str, int]) -> str:
    ordered_statuses = ["success", "oom", "timeout", "error"]
    parts = [f"{counts[status]} {status}" for status in ordered_statuses if status in counts]
    parts.extend(f"{count} {status}" for status, count in sorted(counts.items()) if status not in ordered_statuses)
    return ", ".join(parts)


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
    if args.database_dir is None and "database_dir" in profile:
        args.database_dir = str(profile["database_dir"])
    if args.output is None and "output" in profile:
        args.output = str(profile["output"])
    if args.logs is None and "logs" in profile:
        args.logs = str(profile["logs"])
    if args.databases is None and "databases" in profile:
        args.databases = str(profile["databases"])
    if args.algorithms is None and "algorithms" in profile:
        args.algorithms = ",".join(str(item) for item in profile["algorithms"])
    limits_raw = profile.get("limits")
    limits = cast("dict[str, Any]", limits_raw) if isinstance(limits_raw, dict) else {}
    if args.timeout is None and "timeout_seconds" in limits:
        args.timeout = int(limits["timeout_seconds"])
    if args.memory_gb is None and "memory_gb" in limits:
        args.memory_gb = float(limits["memory_gb"])
    if "java_heap_gb" in limits and args.java_heap_gb is None:
        args.java_heap_gb = int(limits["java_heap_gb"])


def _apply_builtin_defaults(args: argparse.Namespace) -> None:
    if args.database_dir is None:
        args.database_dir = DEFAULT_DATABASE_DIR
    if args.output is None:
        args.output = DEFAULT_OUTPUT_DIR
    if args.logs is None:
        args.logs = DEFAULT_LOG_DIR
    if args.algorithms is None:
        args.algorithms = DEFAULT_ALGORITHMS
    if args.databases is None:
        args.databases = DEFAULT_DATABASES
    if args.timeout is None:
        args.timeout = DEFAULT_TIMEOUT_SECONDS
    if args.memory_gb is None:
        args.memory_gb = DEFAULT_MEMORY_GB


def _resolve_hosts(value: str | None, profile: dict[str, Any]) -> list[str]:
    if value:
        hosts = [host.strip() for host in value.split(",") if host.strip()]
        if not hosts:
            raise ValueError("--hosts must contain at least one host.")
        if len(hosts) != len(set(hosts)):
            raise ValueError("--hosts contains duplicate host names.")
        return hosts

    hosts_raw = profile.get("hosts", [])
    if not hosts_raw:
        return []
    if not isinstance(hosts_raw, list):
        raise ValueError("Benchmark settings hosts must be a list.")
    hosts = [str(item) for item in hosts_raw]
    if len(hosts) != len(set(hosts)):
        raise ValueError("Benchmark settings hosts contains duplicate host names.")
    return hosts


def _resolve_host(value: str | None, hosts: list[str]) -> str | None:
    if not value:
        return None
    host = socket.gethostname().split(".")[0] if value == "auto" else value
    if hosts and host not in hosts:
        allowed = ", ".join(hosts)
        raise ValueError(f"Host {host!r} is not in benchmark settings hosts: {allowed}")
    return host


def _shard_databases(databases: list[Path], hosts: list[str], host: str | None) -> list[Path]:
    if not host:
        return databases
    if not hosts:
        raise ValueError("--host requires a non-empty hosts list from --hosts or --settings.")
    host_index = hosts.index(host)
    return [db for index, db in enumerate(databases) if index % len(hosts) == host_index]


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
    write_config: bool = True,
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
    if write_config:
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
    progress_path = _progress_path(output_dir, algorithm, database)
    return RunSpec(
        algorithm=algorithm,
        database=database,
        config_path=config_path,
        command=command,
        stdout_path=stdout_path,
        progress_path=progress_path,
    )


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
    host = socket.gethostname().split(".")[0]
    _write_progress(
        spec,
        {
            "algorithm": spec.algorithm,
            "database": spec.database.name,
            "status": "running",
            "host": host,
            "started_at": started_at.isoformat(),
            "config": str(spec.config_path),
            "command": spec.command,
            "stdout": str(spec.stdout_path),
        },
    )
    spec.stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stdout_handle = spec.stdout_path.open("w", encoding="utf-8")
    popen_kwargs: dict[str, Any] = {
        "stdout": stdout_handle,
        "stderr": subprocess.STDOUT,
        "start_new_session": True,
        "env": _external_command_env(),
    }
    if os.name == "posix":
        popen_kwargs["preexec_fn"] = _prepare_child_process
    process = subprocess.Popen(spec.command, **popen_kwargs)
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
        return_code = process.wait(timeout=timeout + SUBPROCESS_CLEANUP_GRACE_SECONDS)
        if oom.is_set():
            status = "oom"
            error = f"Memory limit exceeded: {memory_gb} GB"
        elif return_code == TIMEOUT_EXIT_CODE:
            status = "timeout"
            error = f"Execution exceeded {timeout} seconds"
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
    rules_count = _parse_rules_count_from_stdout(spec.stdout_path)
    result = {
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
        "host": host,
    }
    if rules_count is not None:
        result["rules_count"] = rules_count
    _write_progress(spec, result)
    return result


def _parse_rules_count_from_stdout(stdout_path: Path) -> int | None:
    try:
        text = stdout_path.read_text(encoding="utf-8")
    except OSError:
        return None
    matches = DISCOVERED_RULES_RE.findall(text)
    if not matches:
        return None
    return int(matches[-1])


def _progress_path(output_dir: Path, algorithm: str, database: Path) -> Path:
    key = _safe_progress_key(f"{algorithm}_{database.name}")
    return output_dir / "progress" / f"{key}.json"


def _safe_progress_key(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)


def _write_progress(spec: RunSpec, payload: dict[str, Any]) -> None:
    if spec.progress_path is None:
        return
    _atomic_write_json(spec.progress_path, {**payload, "updated_at": dt.datetime.now(dt.timezone.utc).isoformat()})


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def print_progress_status(output_dir: Path, specs: list[RunSpec]) -> None:
    by_key = {(spec.algorithm, spec.database.name): spec for spec in specs}
    progress = _read_progress(output_dir, specs)
    rows = []
    for key, spec in by_key.items():
        payload = progress.get(key)
        if payload is None:
            payload = {
                "algorithm": spec.algorithm,
                "database": spec.database.name,
                "status": "pending",
                "config": str(spec.config_path),
                "stdout": str(spec.stdout_path),
            }
        rows.append(payload)

    total = len(rows)
    done_statuses = {"success", "timeout", "oom", "error"}
    done = sum(1 for row in rows if row.get("status") in done_statuses)
    running = sum(1 for row in rows if row.get("status") == "running")
    pending = sum(1 for row in rows if row.get("status") == "pending")
    failed = sum(1 for row in rows if row.get("status") in {"timeout", "oom", "error"})
    percent = (done / total * 100) if total else 100.0
    print(f"Overall {done}/{total} done  {running} running  {pending} pending  {failed} failed")
    print(f"[{_progress_bar(done, total)}] {percent:.1f}%")

    algorithms = sorted({str(row.get("algorithm")) for row in rows})
    for algorithm in algorithms:
        algo_rows = [row for row in rows if row.get("algorithm") == algorithm]
        algo_done = sum(1 for row in algo_rows if row.get("status") in done_statuses)
        algo_running = sum(1 for row in algo_rows if row.get("status") == "running")
        algo_failed = sum(1 for row in algo_rows if row.get("status") in {"timeout", "oom", "error"})
        print(f"{algorithm}: {algo_done}/{len(algo_rows)} done  {algo_running} running  {algo_failed} failed")

    running_rows = sorted(
        (row for row in rows if row.get("status") == "running"),
        key=lambda row: (str(row.get("host", "")), str(row.get("algorithm", "")), str(row.get("database", ""))),
    )
    if running_rows:
        print("")
        print("Running:")
        now = dt.datetime.now(dt.timezone.utc)
        for row in running_rows:
            elapsed = _elapsed_since(row.get("started_at"), now)
            host = row.get("host") or "unknown"
            print(f"  {host}  {row.get('algorithm')}  {row.get('database')}  {elapsed}")

    failed_rows = sorted(
        (row for row in rows if row.get("status") in {"timeout", "oom", "error"}),
        key=lambda row: (str(row.get("algorithm", "")), str(row.get("database", ""))),
    )
    if failed_rows:
        print("")
        print("Failures:")
        for row in failed_rows:
            print(f"  {row.get('algorithm')}  {row.get('database')}  {row.get('status')}  {row.get('error') or ''}")


def _read_progress(output_dir: Path, specs: list[RunSpec]) -> dict[tuple[str, str], dict[str, Any]]:
    progress: dict[tuple[str, str], dict[str, Any]] = {}
    expected_paths = {spec.progress_path for spec in specs if spec.progress_path is not None}
    for path in expected_paths:
        if path is None or not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            key = (str(payload.get("algorithm")), str(payload.get("database")))
            progress[key] = payload
    return progress


def _progress_bar(done: int, total: int, width: int = 40) -> str:
    if total <= 0:
        return "#" * width
    filled = round(width * done / total)
    return "#" * filled + "-" * (width - filled)


def _elapsed_since(value: object, now: dt.datetime) -> str:
    if not isinstance(value, str):
        return "unknown"
    try:
        started = dt.datetime.fromisoformat(value)
    except ValueError:
        return "unknown"
    if started.tzinfo is None:
        started = started.replace(tzinfo=dt.timezone.utc)
    return format_duration(max(0.0, (now - started).total_seconds()))


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


def _prepare_child_process() -> None:
    if os.name != "posix":
        return
    try:
        libc = ctypes.CDLL(None)
        libc.prctl(PR_SET_PDEATHSIG, signal.SIGTERM)
    except Exception:
        return


def _external_command_env() -> dict[str, str]:
    env = os.environ.copy()
    local_bin = str(Path.home() / ".local" / "bin")
    current_path = env.get("PATH", "")
    env["PATH"] = f"{local_bin}{os.pathsep}{current_path}" if current_path else local_bin
    return env


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


def _write_summary(
    output_dir: Path, specs: list[RunSpec], results: list[dict[str, Any]], *, dry_run: bool, host: str | None
) -> None:
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
                    (run.get("algorithm"), run.get("database")): run for run in existing_runs if isinstance(run, dict)
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
