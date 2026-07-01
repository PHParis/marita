from __future__ import annotations

import argparse
from pathlib import Path

from mahilda.audit import AuditConfig, run_audit


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
    parser.add_argument("--confidence-threshold", type=float, default=1.0)
    parser.add_argument("--max-examples", type=int, default=25)
    parser.add_argument("--strict", action="store_true", help="Exit 2 if comparable true rules are unmatched.")
    parser.add_argument("--no-progress", action="store_true", help="Disable audit progress bars.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_arguments(argv)
    results_dir = Path(args.results_dir)
    output_dir = Path(args.output_dir) if args.output_dir else results_dir / "audit"
    competitors = tuple(part.strip().upper() for part in args.competitors.split(",") if part.strip())
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
    )
    run_audit(config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
