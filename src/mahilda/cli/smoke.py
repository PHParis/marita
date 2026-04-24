import argparse
import os
import time
from pathlib import Path

from mahilda.cli import run as run_cli


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run a fast MAHILDA smoke test with test config.",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Show discovered rules.")
    parser.add_argument("-q", "--quiet", action="store_true", help="Minimal output mode.")
    parser.add_argument(
        "-c",
        "--config",
        default="configs/config.test.yaml",
        help="Path to smoke-test config (default: configs/config.test.yaml).",
    )
    args = parser.parse_args(argv)

    if args.verbose and args.quiet:
        parser.error("--verbose and --quiet are mutually exclusive")

    if args.verbose:
        os.environ["MAHILDA_VERBOSE"] = "1"
    elif args.quiet:
        os.environ["MAHILDA_QUIET"] = "1"

    config_path = Path(args.config)
    if not config_path.exists():
        print(f"Config not found: {config_path}")
        return 1

    start_time = time.time()
    exit_code = run_cli.main(["--config", str(config_path)])
    elapsed = time.time() - start_time

    if not args.quiet:
        print(f"Smoke test finished in {elapsed:.2f}s")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
