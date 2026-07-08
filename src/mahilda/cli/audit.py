from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from mahilda.audit import AuditConfig, run_audit
from mahilda.audit.runner import get_audit_status


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit competitor rule coverage by MAHILDA.")
    parser.add_argument("--results-dir", default="results/paper_table2", help="Benchmark results root.")
    parser.add_argument("--database-dir", default="data/relational", help="SQLite database directory.")
    parser.add_argument("--output-dir", default=None, help="Audit output directory.")
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
    parser.add_argument("--confidence-threshold", type=float, default=1.0)
    parser.add_argument("--max-examples", type=int, default=25)
    parser.add_argument("--strict", action="store_true", help="Exit 2 if comparable true rules are unmatched.")
    parser.add_argument("--no-progress", action="store_true", help="Disable audit progress bars.")
    parser.add_argument("--workers", type=int, default=1, help="Run audits concurrently across databases.")
    parser.add_argument("--resume", action="store_true", help="Resume a checkpointed audit run.")
    parser.add_argument("--reset-state", action="store_true", help="Discard checkpoint state and recompute.")
    parser.add_argument("--status", action="store_true", help="Show audit checkpoint status and exit.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_arguments(argv)
    results_dir = Path(args.results_dir)
    output_dir = Path(args.output_dir) if args.output_dir else results_dir / "audit"
    competitors = tuple(part.strip().upper() for part in args.competitors.split(",") if part.strip())
    settings = _load_audit_settings(Path(args.settings)) if args.settings else {}
    default_config = AuditConfig(results_dir=results_dir, database_dir=Path(args.database_dir), output_dir=output_dir)
    disjoint_setting = bool(settings.get("disjoint_semantics", default_config.disjoint_semantics))
    config = AuditConfig(
        results_dir=results_dir,
        database_dir=Path(args.database_dir),
        output_dir=output_dir,
        target=args.target.strip().upper(),
        competitors=competitors,
        confidence_threshold=args.confidence_threshold,
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
        workers=args.workers,
        resume=args.resume,
        reset_state=args.reset_state,
        status_only=args.status,
    )
    if args.resume and args.reset_state:
        raise SystemExit("--resume and --reset-state are mutually exclusive.")
    if args.status:
        summary = get_audit_status(config)
        totals = summary["totals"]
        if not isinstance(totals, dict):
            raise SystemExit("Invalid audit status payload.")
        totals_dict = {key: int(value) for key, value in totals.items() if key != "shards"}
        print(
            "audit status:"
            f" completed={totals_dict['completed']}"
            f" running={totals_dict['running']}"
            f" pending={totals_dict['pending']}"
            f" failed={totals_dict['failed']}"
            f" interrupted={totals_dict['interrupted']}"
            f" processed_rules={totals_dict['processed_rules']}/{totals_dict['total_rules']}"
        )
        return 0
    run_audit(config)
    return 0


def _load_audit_settings(settings_path: Path) -> dict[str, object]:
    payload = yaml.safe_load(settings_path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        return {}
    algorithm = payload.get("algorithm")
    if isinstance(algorithm, dict):
        parameters = algorithm.get("parameters")
        if isinstance(parameters, dict):
            return dict(parameters)
    parameters = payload.get("parameters")
    if isinstance(parameters, dict):
        return dict(parameters)
    return {}


def _int_setting(settings: dict[str, object], key: str, default: int) -> int:
    value = settings.get(key, default)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value)
    return default


if __name__ == "__main__":
    raise SystemExit(main())
