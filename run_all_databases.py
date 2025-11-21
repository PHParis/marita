#!/usr/bin/env python3
"""
Script to run MAHILDA on all databases in a specified directory.
Features:
- Beautiful colored CLI output
- Parallel execution (3-4 databases at once)
- 2 hour timeout per database
- Progress tracking
- Error handling and logging
- Summary report at the end
"""
import os
import sys
import time
import glob
import argparse
from pathlib import Path
from datetime import datetime, timedelta
from concurrent.futures import ProcessPoolExecutor, as_completed
from multiprocessing import Manager

try:
    from colorama import Fore, Back, Style, init
    init(autoreset=True)
    COLORS_AVAILABLE = True
except ImportError:
    COLORS_AVAILABLE = False
    class Fore:
        GREEN = YELLOW = BLUE = CYAN = RED = MAGENTA = WHITE = RESET = ""
    class Back:
        GREEN = YELLOW = BLUE = CYAN = RED = MAGENTA = WHITE = BLACK = RESET = ""
    class Style:
        BRIGHT = DIM = NORMAL = RESET_ALL = ""

# Change to project root
project_root = Path(__file__).parent
os.chdir(project_root)

# Add src to path
sys.path.insert(0, str(project_root / "src"))


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


def format_duration(seconds: float) -> str:
    """Format duration in human readable format."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    elif seconds < 3600:
        minutes = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{minutes}m {secs}s"
    else:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        return f"{hours}h {minutes}m"


def run_database(db_path: Path, db_name: str, results_base_dir: Path, timeout: int = 7200) -> dict:
    """
    Run MAHILDA on a single database.
    
    Args:
        db_path: Path to the database file
        db_name: Name of the database (without .db extension)
        results_base_dir: Base directory for results
        timeout: Timeout in seconds (default: 2 hours)
    
    Returns:
        dict with status, duration, rules_count, error
    """
    # Import here to avoid issues with multiprocessing
    import sys
    import json
    from pathlib import Path
    project_root = Path(__file__).parent
    sys.path.insert(0, str(project_root / "src"))
    
    from src.main import DatabaseProcessor
    from utils.logging_utils import configure_global_logger
    from database.alchemy_utility import AlchemyUtility
    
    result = {
        'database': db_name,
        'status': 'running',
        'duration': 0,
        'rules_count': 0,
        'error': None,
        'start_time': None,
        'end_time': None
    }
    
    # Create results directory for this database
    results_dir = results_base_dir / f"MAHILDA_{db_name}"
    results_dir.mkdir(parents=True, exist_ok=True)
    
    # Create logs directory
    log_dir = Path("logs") / db_name
    log_dir.mkdir(parents=True, exist_ok=True)
    
    # Configure logger
    logger = configure_global_logger(str(log_dir))
    
    # Set quiet mode
    os.environ["MAHILDA_QUIET"] = "1"
    
    start_time = time.time()
    result['start_time'] = datetime.now()
    
    try:
        # Create processor
        processor = DatabaseProcessor(
            algorithm_name="MAHILDA",
            database_name=Path(db_path.name),
            database_path=db_path.parent,
            results_dir=results_base_dir,
            logger=logger,
            use_mlflow=False,
        )
        
        # Run with timeout
        import signal
        
        def timeout_handler(signum, frame):
            raise TimeoutError(f"Execution exceeded {timeout} seconds")
        
        # Set timeout (only on Unix systems)
        if hasattr(signal, 'SIGALRM'):
            signal.signal(signal.SIGALRM, timeout_handler)
            signal.alarm(timeout)
        
        try:
            rules_count = processor.discover_rules()
            result['rules_count'] = rules_count
            result['status'] = 'success'
            
            if hasattr(signal, 'SIGALRM'):
                signal.alarm(0)  # Cancel alarm
                
        except TimeoutError as e:
            result['status'] = 'timeout'
            result['error'] = str(e)
            if hasattr(signal, 'SIGALRM'):
                signal.alarm(0)
        
    except Exception as e:
        result['status'] = 'error'
        result['error'] = str(e)
        logger.error(f"Error processing {db_name}: {e}", exc_info=True)
    
    finally:
        # Clean environment
        if "MAHILDA_QUIET" in os.environ:
            del os.environ["MAHILDA_QUIET"]
    
    end_time = time.time()
    result['end_time'] = datetime.now()
    result['duration'] = end_time - start_time
    
    # Save execution time metrics to JSON file
    time_metrics_file = results_dir / f"execution_time_{db_name}.json"
    time_metrics = {
        'database': db_name,
        'execution_time_seconds': result['duration'],
        'execution_time_ms': result['duration'] * 1000,
        'start_time': result['start_time'].isoformat() if result['start_time'] else None,
        'end_time': result['end_time'].isoformat() if result['end_time'] else None,
        'status': result['status'],
        'rules_count': result['rules_count']
    }
    
    try:
        with open(time_metrics_file, 'w') as f:
            json.dump(time_metrics, f, indent=2)
    except Exception as e:
        logger.warning(f"Failed to save time metrics: {e}")
    
    return result


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Run MAHILDA on all databases in a directory",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "-d", "--directory",
        default="/Volumes/backup_mac_1/data_mahilda_3",
        help="Directory containing database files (default: /Volumes/backup_mac_1/data_mahilda_3)"
    )
    parser.add_argument(
        "-o", "--output",
        default="results_all_databases",
        help="Output directory for results (default: results_all_databases)"
    )
    parser.add_argument(
        "-t", "--timeout",
        type=int,
        default=7200,
        help="Timeout per database in seconds (default: 7200 = 2 hours)"
    )
    parser.add_argument(
        "--start-from",
        type=int,
        default=0,
        help="Start from database index (default: 0)"
    )
    parser.add_argument(
        "--max-databases",
        type=int,
        default=None,
        help="Maximum number of databases to process (default: all)"
    )
    parser.add_argument(
        "-w", "--workers",
        type=int,
        default=3,
        help="Number of parallel workers (default: 3)"
    )
    
    args = parser.parse_args()
    
    # Get database directory
    db_dir = Path(args.directory)
    if not db_dir.exists():
        print_error(f"Directory not found: {db_dir}")
        sys.exit(1)
    
    # Find all .db files
    db_files = sorted(glob.glob(str(db_dir / "*.db")))
    if not db_files:
        print_error(f"No .db files found in {db_dir}")
        sys.exit(1)
    
    # Apply filters
    if args.start_from > 0:
        db_files = db_files[args.start_from:]
    if args.max_databases:
        db_files = db_files[:args.max_databases]
    
    # Create results directory
    results_base_dir = Path(args.output)
    results_base_dir.mkdir(parents=True, exist_ok=True)
    
    # Print header
    print()
    print_banner("🚀 MAHILDA Batch Processing", "═", 80, Fore.MAGENTA)
    print()
    print_info("Database directory", str(db_dir), "📁")
    print_info("Results directory", str(results_base_dir), "📊")
    print_info("Total databases", str(len(db_files)), "💾")
    print_info("Parallel workers", str(args.workers), "⚡")
    print_info("Timeout per database", format_duration(args.timeout), "⏱️")
    print()
    
    # Track results
    all_results = []
    start_time_total = time.time()
    
    # Process databases in parallel
    print_section("Processing databases in parallel...")
    
    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        # Submit all tasks
        future_to_db = {}
        for idx, db_file in enumerate(db_files, 1):
            db_path = Path(db_file)
            db_name = db_path.stem
            future = executor.submit(run_database, db_path, db_name, results_base_dir, args.timeout)
            future_to_db[future] = (idx, db_name, db_path)
        
        # Process completed tasks
        completed = 0
        for future in as_completed(future_to_db):
            completed += 1
            idx, db_name, db_path = future_to_db[future]
            progress = f"[{completed}/{len(db_files)}]"
            
            try:
                result = future.result()
                all_results.append(result)
                
                # Print result
                if result['status'] == 'success':
                    print_success(
                        f"{progress} {db_name}: {result['rules_count']} rules "
                        f"in {format_duration(result['duration'])}"
                    )
                elif result['status'] == 'timeout':
                    print_warning(f"{progress} {db_name}: Timeout after {format_duration(result['duration'])}")
                else:
                    print_error(f"{progress} {db_name}: {result['error']}")
                    
            except Exception as e:
                print_error(f"{progress} {db_name}: Exception - {e}")
                all_results.append({
                    'database': db_name,
                    'status': 'error',
                    'duration': 0,
                    'rules_count': 0,
                    'error': str(e),
                    'start_time': None,
                    'end_time': None
                })
    
    # Print summary
    total_duration = time.time() - start_time_total
    
    print()
    print_banner("📊 Summary Report", "═", 80, Fore.CYAN)
    print()
    
    successful = [r for r in all_results if r['status'] == 'success']
    timeouts = [r for r in all_results if r['status'] == 'timeout']
    errors = [r for r in all_results if r['status'] == 'error']
    
    print_info("Total databases processed", str(len(all_results)), "💾")
    print_success(f"Successful: {len(successful)}")
    if timeouts:
        print_warning(f"Timeouts: {len(timeouts)}")
    if errors:
        print_error(f"Errors: {len(errors)}")
    print_info("Total duration", format_duration(total_duration), "⏱️")
    print()
    
    if successful:
        total_rules = sum(r['rules_count'] for r in successful)
        avg_duration = sum(r['duration'] for r in successful) / len(successful)
        print_info("Total rules discovered", str(total_rules), "📈")
        print_info("Average duration", format_duration(avg_duration), "⏱️")
        print()
    
    # Top 5 databases by rules
    if successful:
        print(f"{Fore.GREEN}{Style.BRIGHT}Top 5 databases by rules discovered:{Style.RESET_ALL}")
        top_5 = sorted(successful, key=lambda x: x['rules_count'], reverse=True)[:5]
        for i, r in enumerate(top_5, 1):
            print(f"  {i}. {Fore.CYAN}{r['database']}{Style.RESET_ALL}: "
                  f"{Fore.GREEN}{r['rules_count']}{Style.RESET_ALL} rules "
                  f"({format_duration(r['duration'])})")
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
    
    # Save summary to file
    summary_file = results_base_dir / "summary.txt"
    with open(summary_file, 'w') as f:
        f.write("="*80 + "\n")
        f.write("MAHILDA Batch Processing Summary\n")
        f.write("="*80 + "\n\n")
        f.write(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"Total databases: {len(all_results)}\n")
        f.write(f"Successful: {len(successful)}\n")
        f.write(f"Timeouts: {len(timeouts)}\n")
        f.write(f"Errors: {len(errors)}\n")
        f.write(f"Total duration: {format_duration(total_duration)}\n\n")
        
        if successful:
            f.write(f"Total rules: {sum(r['rules_count'] for r in successful)}\n")
            f.write(f"Average duration: {format_duration(avg_duration)}\n\n")
        
        f.write("\nDetailed Results:\n")
        f.write("-"*80 + "\n\n")
        for r in all_results:
            f.write(f"Database: {r['database']}\n")
            f.write(f"Status: {r['status']}\n")
            f.write(f"Duration: {format_duration(r['duration'])}\n")
            if r['status'] == 'success':
                f.write(f"Rules: {r['rules_count']}\n")
            elif r['error']:
                f.write(f"Error: {r['error']}\n")
            f.write("\n")
    
    print_success(f"Summary saved to {summary_file}")
    print()
    print_banner("✨ Done!", "═", 80, Fore.GREEN)
    print()


if __name__ == "__main__":
    main()
