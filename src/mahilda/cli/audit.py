from __future__ import annotations

import argparse
import socket
from pathlib import Path

import yaml

from mahilda.audit import AuditConfig, run_audit
from mahilda.audit.runner import get_audit_status


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit competitor rule coverage by MAHILDA.")
    parser.add_argument("--results-dir", default="results/paper_table2", help="Benchmark results root.")
    parser.add_argument("--database-dir", default="data/relational", help="SQLite database directory.")
    parser.add_argument("--output-dir", default=None, help="Audit output directory.")
    parser.add_argument(
        "--status-dir",
        default=None,
        help="Directory containing benchmark run-status JSON (default: <results-dir>/progress).",
    )
    parser.add_argument("--target", default="MAHILDA", help="Target algorithm to compare against.")
    parser.add_argument(
        "--competitors",
        default="AMIE3,MATILDA,SPIDER,POPPER",
        help="Comma-separated competitor algorithms to audit.",
    )
    parser.add_argument("--settings", default=None, help="Optional YAML config to read MAHILDA audit bounds from.")
    parser.add_argument("--coverage", choices=("alpha", "subsumption", "instance"), default="alpha")
    parser.add_argument("--walk-length", type=int, default=None)
    parser.add_argument("--max-tables", type=int, default=None)
    parser.add_argument("--max-variables", type=int, default=None)
    parser.add_argument("--joinability", choices=("fk", "full"), default=None)
    parser.add_argument("--no-disjoint-semantics", action="store_true")
    parser.add_argument("--no-diagnose-unmatched", action="store_true")
    parser.add_argument("--include-amie-rdf", action="store_true")
    parser.add_argument(
        "--allow-legacy-amie-mapping",
        action="store_true",
        help="Translate AMIE3 without a hash-validated TSV mapping manifest.",
    )
    parser.add_argument("--confidence-threshold", type=float, default=1.0)
    parser.add_argument("--support-threshold", type=int, default=0)
    parser.add_argument("--max-examples", type=int, default=25)
    parser.add_argument("--strict", action="store_true", help="Exit 2 if comparable true rules are unmatched.")
    parser.add_argument("--no-progress", action="store_true", help="Disable audit progress bars.")
    parser.add_argument("--workers", type=int, default=None, help="Run audits concurrently across databases.")
    parser.add_argument("--hosts", default=None, help="Comma-separated hosts participating in the shared audit queue.")
    parser.add_argument("--host", default=None, help="Host identity, or 'auto' for the local short hostname.")
    parser.add_argument("--heartbeat-seconds", type=int, default=None)
    parser.add_argument("--stale-after-seconds", type=int, default=None)
    parser.add_argument("--max-attempts", type=int, default=None)
    parser.add_argument("--resume", action="store_true", help="Resume a checkpointed audit run.")
    parser.add_argument("--reset-state", action="store_true", help="Discard checkpoint state and recompute.")
    parser.add_argument("--status", action="store_true", help="Show audit checkpoint status and exit.")
    parser.add_argument(
        "--reuse-cache",
        action="store_true",
        help="Reuse stored rule evaluations and recompute only matching and reports.",
    )
    parser.add_argument(
        "--trust-legacy-cache",
        action="store_true",
        help="Import an existing audit_rules.csv when no evaluation cache manifest exists.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_arguments(argv)
    results_dir = Path(args.results_dir)
    output_dir = Path(args.output_dir) if args.output_dir else results_dir / "audit"
    competitors = tuple(part.strip().upper() for part in args.competitors.split(",") if part.strip())
    settings = _load_audit_settings(Path(args.settings)) if args.settings else {}
    hosts = _resolve_hosts(args.hosts, settings)
    host = _resolve_host(args.host, hosts, status=args.status)
    workers = args.workers if args.workers is not None else _int_setting(settings, "workers_per_host", 1)
    heartbeat_seconds = (
        args.heartbeat_seconds
        if args.heartbeat_seconds is not None
        else _int_setting(settings, "heartbeat_seconds", 30)
    )
    stale_after_seconds = (
        args.stale_after_seconds
        if args.stale_after_seconds is not None
        else _int_setting(settings, "stale_after_seconds", 7200)
    )
    max_attempts = args.max_attempts if args.max_attempts is not None else _int_setting(settings, "max_attempts", 3)
    default_config = AuditConfig(results_dir=results_dir, database_dir=Path(args.database_dir), output_dir=output_dir)
    disjoint_setting = bool(settings.get("disjoint_semantics", default_config.disjoint_semantics))
    config = AuditConfig(
        results_dir=results_dir,
        database_dir=Path(args.database_dir),
        output_dir=output_dir,
        status_dir=Path(args.status_dir) if args.status_dir else None,
        target=args.target.strip().upper(),
        competitors=competitors,
        confidence_threshold=args.confidence_threshold,
        support_threshold=args.support_threshold,
        max_examples=args.max_examples,
        strict=args.strict,
        show_progress=not args.no_progress,
        walk_length=args.walk_length
        if args.walk_length is not None
        else _int_setting(settings, "walk_length", default_config.walk_length),
        max_tables=args.max_tables
        if args.max_tables is not None
        else _int_setting(settings, "max_tables", default_config.max_tables),
        max_variables=(
            args.max_variables
            if args.max_variables is not None
            else _int_setting(settings, "max_variables", default_config.max_variables)
        ),
        disjoint_semantics=False if args.no_disjoint_semantics else disjoint_setting,
        joinability=(args.joinability or str(settings.get("joinability", default_config.joinability))).lower(),
        coverage=args.coverage,
        diagnose_unmatched=not args.no_diagnose_unmatched,
        include_amie_rdf=args.include_amie_rdf,
        allow_legacy_amie_mapping=args.allow_legacy_amie_mapping,
        workers=workers,
        hosts=tuple(hosts),
        host=host,
        heartbeat_seconds=heartbeat_seconds,
        stale_after_seconds=stale_after_seconds,
        max_attempts=max_attempts,
        resume=args.resume,
        reset_state=args.reset_state,
        status_only=args.status,
        reuse_cache=args.reuse_cache,
        trust_legacy_cache=args.trust_legacy_cache,
    )
    if args.resume and args.reset_state:
        raise SystemExit("--resume and --reset-state are mutually exclusive.")
    if args.status:
        summary = get_audit_status(config)
        totals = summary["totals"]
        if not isinstance(totals, dict):
            raise SystemExit("Invalid audit status payload.")
        totals_dict = {key: int(value) for key, value in totals.items() if key != "shards"}
        stale = totals_dict.get("stale", 0)
        print(
            "audit status:"
            f" completed={totals_dict['completed']}"
            f" running={totals_dict['running']}"
            f" pending={totals_dict['pending']}"
            f" failed={totals_dict['failed']}"
            f" interrupted={totals_dict['interrupted']}"
            f" stale={stale}"
            f" processed_rules={totals_dict['processed_rules']}/{totals_dict['total_rules']}"
        )
        jobs = summary.get("shards", [])
        if not isinstance(jobs, list):
            jobs = []
        for job in jobs:
            if not isinstance(job, dict) or job.get("queue_state") != "running":
                continue
            print(
                f"  {job.get('claimed_by', 'unknown')}  {job.get('database', job.get('job_id'))}"
                f"  {job.get('processed_rules', 0)}/{job.get('total_rules', 0)} rules"
            )
        by_host: dict[str, int] = {}
        for job in jobs:
            if job.get("queue_state") != "running":
                continue
            host_name = str(job.get("claimed_by") or "unknown")
            by_host[host_name] = by_host.get(host_name, 0) + 1
        if by_host:
            print("hosts:")
            for host_name, count in sorted(by_host.items()):
                print(f"  {host_name} running={count}")
        return 0
    run_audit(config)
    return 0


def _load_audit_settings(settings_path: Path) -> dict[str, object]:
    payload = yaml.safe_load(settings_path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        return {}
    settings: dict[str, object] = {}
    audit = payload.get("audit")
    if isinstance(audit, dict):
        settings.update(audit)
    hosts = payload.get("hosts")
    if "hosts" not in settings and isinstance(hosts, list):
        settings["hosts"] = hosts
    workers = payload.get("workers")
    if "workers_per_host" not in settings and isinstance(workers, int):
        settings["workers_per_host"] = workers
    algorithm = payload.get("algorithm")
    if isinstance(algorithm, dict):
        parameters = algorithm.get("parameters")
        if isinstance(parameters, dict):
            settings.update(parameters)
    parameters = payload.get("parameters")
    if isinstance(parameters, dict):
        settings.update(parameters)
    return settings


def _int_setting(settings: dict[str, object], key: str, default: int) -> int:
    value = settings.get(key, default)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value)
    return default


def _resolve_hosts(value: str | None, settings: dict[str, object]) -> list[str]:
    if value:
        hosts = [host.strip() for host in value.split(",") if host.strip()]
    else:
        raw = settings.get("hosts", [])
        hosts = [str(host) for host in raw] if isinstance(raw, list) else []
    if len(hosts) != len(set(hosts)):
        raise SystemExit("Audit hosts must not contain duplicates.")
    return hosts


def _resolve_host(value: str | None, hosts: list[str], *, status: bool = False) -> str | None:
    if value is None:
        if status:
            return None
        if hosts:
            value = "auto"
        else:
            return None
    host = socket.gethostname().split(".")[0] if value == "auto" else value
    if not hosts:
        raise SystemExit("--host requires a non-empty --hosts list or settings hosts list.")
    if host not in hosts:
        raise SystemExit(f"Host {host!r} is not in audit settings hosts: {', '.join(hosts)}")
    return host


if __name__ == "__main__":
    raise SystemExit(main())
