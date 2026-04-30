from __future__ import annotations

import argparse
import logging
from pathlib import Path

from mahilda.evaluation.datasets.relational import RelationalDatasetPreparer, RelationalDownloadSettings


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download and convert relational benchmark databases.")
    parser.add_argument(
        "-o",
        "--output",
        default="data/relational",
        help="Output directory for SQL dumps, SQLite DBs, and report (default: data/relational)",
    )
    parser.add_argument(
        "--database",
        action="append",
        default=None,
        help="Database export name to prepare. May be supplied multiple times. Defaults to all discovered databases.",
    )
    parser.add_argument("--max-databases", type=int, default=None, help="Limit the number of selected databases")
    parser.add_argument("--timeout", type=int, default=300, help="mysqldump timeout per database in seconds")
    parser.add_argument("--list", action="store_true", help="List remote database export names without dumping")
    parser.add_argument("--dump-only", action="store_true", help="Only create .sql dumps; do not convert to SQLite")
    parser.add_argument(
        "--convert-only", action="store_true", help="Convert existing .sql dumps in the output directory"
    )
    parser.add_argument("--quiet", action="store_true", help="Suppress progress bars")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_arguments(argv)
    if args.dump_only and args.convert_only:
        logging.getLogger(__name__).error("--dump-only and --convert-only are mutually exclusive")
        return 2

    settings = RelationalDownloadSettings.from_env(Path(args.output), timeout=args.timeout)
    preparer = RelationalDatasetPreparer(settings)
    progress = not args.quiet

    try:
        if args.list:
            names = preparer.discover_databases(progress=progress)
            if args.max_databases is not None:
                names = names[: args.max_databases]
            for name in names:
                print(name)
            return 0

        report = preparer.prepare(
            database_names=args.database,
            max_databases=args.max_databases,
            dump_only=args.dump_only,
            convert_only=args.convert_only,
            progress=progress,
        )
    except Exception as exc:
        logging.getLogger(__name__).error("Relational database preparation failed: %s", exc, exc_info=True)
        return 1

    print(f"Selected: {len(report.discovered)}")
    print(f"Dumped: {len(report.dumped)}")
    print(f"Converted: {len(report.converted)}")
    print(f"Skipped: {len(report.skipped)}")
    print(f"Failed: {len(report.failed)}")
    print(f"Report: {settings.output_dir / 'conversion_report.txt'}")
    return 1 if report.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
