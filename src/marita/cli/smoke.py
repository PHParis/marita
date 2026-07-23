import argparse
import logging
import time
from pathlib import Path

from marita.cli import run as run_cli
from marita.cli.runtime import scoped_env_vars

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run a fast MARITA smoke test with test config.",
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

    env_overrides: dict[str, str | None] = {}
    if args.verbose:
        env_overrides = {"MARITA_VERBOSE": "1", "MARITA_QUIET": None}
    elif args.quiet:
        env_overrides = {"MARITA_QUIET": "1", "MARITA_VERBOSE": None}

    config_path = Path(args.config)
    if not config_path.exists():
        logger.error("Config not found: %s", config_path)
        return 1

    with scoped_env_vars(env_overrides):
        start_time = time.time()
        exit_code = run_cli.main(["--config", str(config_path)])
        elapsed = time.time() - start_time

    if not args.quiet:
        logger.info("Smoke test finished in %.2fs", elapsed)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
