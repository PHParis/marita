#!/usr/bin/env python3
"""
Script to run MARITA on all databases in a specified directory.
Features:
- Beautiful colored CLI output
- Parallel execution (3-4 databases at once)
- 2 hour timeout per database
- Progress tracking
- Error handling and logging
- Summary report at the end
"""

import _thread
import argparse
import signal
import threading
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeoutError
from datetime import datetime
from pathlib import Path

from marita.cli._color import Fore, Style
from marita.cli.artifacts import (
    build_command_artifacts,
    format_duration,
    write_batch_summary,
    write_execution_time_metrics,
)
from marita.cli.processors import DatabaseProcessor
from marita.cli.runtime import initialize_directories, scoped_env_vars
from marita.utils.config_loader import load_typed_config
from marita.utils.logging_utils import configure_global_logger


def print_banner(title: str, char: str = "═", width: int = 80, color: str = ""):
    """Print a beautiful banner."""
    border = char * width
    if color:
        print(f"{color}{border}{Style.RESET_ALL}")
        print(f"{color}{Style.BRIGHT}{title.center(width)}{Style.RESET_ALL}")
        print(f"{color}{border}{Style.RESET_ALL}")
    else:
        print(border)
        print(title.center(width))
        print(border)


def print_section(title: str):
    """Print a section header."""
    print(f"\n{Fore.CYAN}{Style.BRIGHT}{'─' * 80}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}{Style.BRIGHT}{title}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}{Style.BRIGHT}{'─' * 80}{Style.RESET_ALL}\n")


def print_info(label: str, value: str, icon: str = "•"):
    """Print an info line."""
    print(f"{Fore.CYAN}{icon} {Style.BRIGHT}{label}:{Style.RESET_ALL} {Fore.WHITE}{value}{Style.RESET_ALL}")


def print_success(message: str):
    """Print a success message."""
    print(f"{Fore.GREEN}✓ {message}{Style.RESET_ALL}")


def print_error(message: str):
    """Print an error message."""
    print(f"{Fore.RED}✗ {message}{Style.RESET_ALL}")


def print_warning(message: str):
    """Print a warning message."""
    print(f"{Fore.YELLOW}⚠ {message}{Style.RESET_ALL}")


def run_database(
    db_path: Path,
    db_name: str,
    results_base_dir: Path,
    timeout: int = 7200,
    log_root: Path | None = None,
) -> dict:
    """
    Run MARITA on a single database.

    Args:
        db_path: Path to the database file
        db_name: Name of the database (without .db extension)
        results_base_dir: Base directory for results
        timeout: Timeout in seconds (default: 2 hours)

    Returns:
        dict with status, duration, rules_count, error
    """
    result = {
        "database": db_name,
        "status": "running",
        "duration": 0,
        "rules_count": 0,
        "error": None,
        "start_time": None,
        "end_time": None,
    }

    artifacts = build_command_artifacts(results_base_dir, "MARITA", db_path)
    artifacts.run_dir.mkdir(parents=True, exist_ok=True)

    if log_root is None:
        log_root = Path("logs")
    log_dir = log_root / db_name
    log_dir.mkdir(parents=True, exist_ok=True)

    logger = configure_global_logger(str(log_dir))

    start_time = time.time()
    result["start_time"] = datetime.now()

    try:
        with scoped_env_vars({"MARITA_LOG_DIR": str(log_dir), "MARITA_QUIET": "1"}):
            processor = DatabaseProcessor(
                algorithm_name="MARITA",
                database_name=Path(db_path.name),
                database_path=db_path.parent,
                results_dir=results_base_dir,
                logger=logger,
                use_mlflow=False,
            )

            def timeout_handler(signum, frame):
                del signum, frame
                raise TimeoutError(f"Execution exceeded {timeout} seconds")

            timed_out = False

            def interrupt_main() -> None:
                nonlocal timed_out
                timed_out = True
                _thread.interrupt_main()

            use_sigalrm = hasattr(signal, "SIGALRM")
            timeout_timer: threading.Timer | None = None

            if use_sigalrm:
                signal.signal(signal.SIGALRM, timeout_handler)
                signal.alarm(timeout)
            else:
                timeout_timer = threading.Timer(timeout, interrupt_main)
                timeout_timer.daemon = True
                timeout_timer.start()

            try:
                rules_count = processor.discover_rules()
                result["rules_count"] = rules_count
                result["status"] = "success"
            except (TimeoutError, FuturesTimeoutError) as e:
                result["status"] = "timeout"
                result["error"] = str(e)
            except KeyboardInterrupt:
                if timed_out:
                    result["status"] = "timeout"
                    result["error"] = f"Execution exceeded {timeout} seconds"
                else:
                    raise
            finally:
                if use_sigalrm:
                    signal.alarm(0)
                if timeout_timer is not None:
                    timeout_timer.cancel()
                processor.clean_up()

    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)
        logger.error(f"Error processing {db_name}: {e}", exc_info=True)

    end_time = time.time()
    result["end_time"] = datetime.now()
    result["duration"] = end_time - start_time

    try:
        write_execution_time_metrics(
            metrics_path=artifacts.execution_time_json,
            database_stem=db_name,
            execution_time=float(result["duration"]),
            status=str(result["status"]),
            rules_count=int(result["rules_count"]),
            algorithm_name="MARITA",
            start_time=result["start_time"],
            end_time=result["end_time"],
        )
    except Exception as e:
        logger.warning(f"Failed to save time metrics: {e}")

    return result


def main(argv: list[str] | None = None) -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Run MARITA on all databases in a directory", formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "-c",
        "--config",
        default="configs/config.example.yaml",
        help="Path to config file (default: configs/config.example.yaml)",
    )
    parser.add_argument(
        "-d",
        "--directory",
        default=None,
        help="Directory containing database files (overrides config database.path)",
    )
    parser.add_argument(
        "-o",
        "--output",
        default="results/batch",
        help="Output directory for results (default: results/batch)",
    )
    parser.add_argument(
        "-t",
        "--timeout",
        type=int,
        default=None,
        help="Timeout per database in seconds (overrides config batch.timeout)",
    )
    parser.add_argument("--start-from", type=int, default=0, help="Start from database index (default: 0)")
    parser.add_argument(
        "--max-databases", type=int, default=None, help="Maximum number of databases to process (default: all)"
    )
    parser.add_argument(
        "-w", "--workers", type=int, default=None, help="Number of parallel workers (overrides config batch.workers)"
    )

    args = parser.parse_args(argv)

    try:
        config = load_typed_config(args.config)
    except ValueError as exc:
        print_error(str(exc))
        return 1

    configured_db_dir = str(config.database.path)
    target_dir = args.directory or configured_db_dir
    effective_timeout = args.timeout if args.timeout is not None else config.batch.timeout
    effective_workers = args.workers if args.workers is not None else config.batch.workers
    if not target_dir:
        print_error("No database directory provided. Use --directory or set database.path in config.")
        return 1

    # Get database directory
    db_dir = Path(target_dir)
    if not db_dir.exists():
        print_error(f"Directory not found: {db_dir}")
        return 1

    # Find all .db files
    db_files = sorted(str(path) for path in db_dir.glob("*.db"))
    if not db_files:
        print_error(f"No .db files found in {db_dir}")
        return 1

    # Apply filters
    if args.start_from > 0:
        if args.start_from >= len(db_files):
            print_error(f"--start-from {args.start_from} is out of range (only {len(db_files)} databases found)")
            return 1
        db_files = db_files[args.start_from :]
    if args.max_databases:
        db_files = db_files[: args.max_databases]

    # Create results directory
    results_base_dir = Path(args.output)
    log_root = config.logging.log_dir
    initialize_directories(results_base_dir, log_root)

    # Print header
    print()
    print_banner("🚀 MARITA Batch Processing", "═", 80, Fore.MAGENTA)
    print()
    print_info("Database directory", str(db_dir), "📁")
    print_info("Results directory", str(results_base_dir), "📊")
    print_info("Total databases", str(len(db_files)), "💾")
    print_info("Parallel workers", str(effective_workers), "⚡")
    print_info("Timeout per database", format_duration(effective_timeout), "⏱️")
    print()

    # Track results
    all_results = []
    start_time_total = time.time()

    # Process databases in parallel
    print_section("Processing databases in parallel...")

    with ProcessPoolExecutor(max_workers=effective_workers) as executor:
        # Submit all tasks
        future_to_db = {}
        for db_file in db_files:
            db_path = Path(db_file)
            db_name = db_path.stem
            future = executor.submit(run_database, db_path, db_name, results_base_dir, effective_timeout, log_root)
            future_to_db[future] = db_name

        # Process completed tasks
        for completed, future in enumerate(as_completed(future_to_db), start=1):
            db_name = future_to_db[future]
            progress = f"[{completed}/{len(db_files)}]"

            try:
                result = future.result()
                all_results.append(result)

                # Print result
                if result["status"] == "success":
                    print_success(
                        f"{progress} {db_name}: {result['rules_count']} rules in {format_duration(result['duration'])}"
                    )
                elif result["status"] == "timeout":
                    print_warning(f"{progress} {db_name}: Timeout after {format_duration(result['duration'])}")
                else:
                    print_error(f"{progress} {db_name}: {result['error']}")

            except Exception as e:
                print_error(f"{progress} {db_name}: Exception - {e}")
                all_results.append(
                    {
                        "database": db_name,
                        "status": "error",
                        "duration": 0,
                        "rules_count": 0,
                        "error": str(e),
                        "start_time": None,
                        "end_time": None,
                    }
                )

    # Print summary
    total_duration = time.time() - start_time_total

    print()
    print_banner("📊 Summary Report", "═", 80, Fore.CYAN)
    print()

    successful = [r for r in all_results if r["status"] == "success"]
    timeouts = [r for r in all_results if r["status"] == "timeout"]
    errors = [r for r in all_results if r["status"] == "error"]

    print_info("Total databases processed", str(len(all_results)), "💾")
    print_success(f"Successful: {len(successful)}")
    if timeouts:
        print_warning(f"Timeouts: {len(timeouts)}")
    if errors:
        print_error(f"Errors: {len(errors)}")
    print_info("Total duration", format_duration(total_duration), "⏱️")
    print()

    if successful:
        total_rules = sum(r["rules_count"] for r in successful)
        avg_duration = sum(r["duration"] for r in successful) / len(successful)
        print_info("Total rules discovered", str(total_rules), "📈")
        print_info("Average duration", format_duration(avg_duration), "⏱️")
        print()

    # Top 5 databases by rules
    if successful:
        print(f"{Fore.GREEN}{Style.BRIGHT}Top 5 databases by rules discovered:{Style.RESET_ALL}")
        top_5 = sorted(successful, key=lambda x: x["rules_count"], reverse=True)[:5]
        for i, r in enumerate(top_5, 1):
            print(
                f"  {i}. {Fore.CYAN}{r['database']}{Style.RESET_ALL}: "
                f"{Fore.GREEN}{r['rules_count']}{Style.RESET_ALL} rules "
                f"({format_duration(r['duration'])})"
            )
        print()

    # Failed databases
    if timeouts:
        print(f"{Fore.YELLOW}{Style.BRIGHT}Timeout databases:{Style.RESET_ALL}")
        for r in timeouts:
            print(f"  • {Fore.YELLOW}{r['database']}{Style.RESET_ALL}")
        print()

    if errors:
        print(f"{Fore.RED}{Style.BRIGHT}Failed databases:{Style.RESET_ALL}")
        for r in errors:
            print(f"  • {Fore.RED}{r['database']}{Style.RESET_ALL}: {r['error']}")
        print()

    summary_file = results_base_dir / "summary.txt"
    write_batch_summary(summary_file, all_results, total_duration)

    print_success(f"Summary saved to {summary_file}")
    print()
    print_banner("✨ Done!", "═", 80, Fore.GREEN)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
