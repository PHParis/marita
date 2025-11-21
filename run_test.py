#!/usr/bin/env python3
"""
Beautiful CLI script to run MAHILDA on the test database.
This script ensures all directories exist and runs from the correct location.
"""
import os
import sys
import time
import argparse
from pathlib import Path

try:
    from colorama import Fore, Back, Style, init
    init(autoreset=True)
    COLORS_AVAILABLE = True
except ImportError:
    COLORS_AVAILABLE = False
    # Fallback to no colors
    class Fore:
        GREEN = YELLOW = BLUE = CYAN = RED = MAGENTA = WHITE = RESET = ""
    class Back:
        GREEN = YELLOW = BLUE = CYAN = RED = MAGENTA = WHITE = BLACK = RESET = ""
    class Style:
        BRIGHT = DIM = NORMAL = RESET_ALL = ""

# Change to the project root directory
project_root = Path(__file__).parent
os.chdir(project_root)

# Create necessary directories
(project_root / "logs").mkdir(exist_ok=True)
(project_root / "results_test").mkdir(exist_ok=True)

# Run main.py with the test config
sys.path.insert(0, str(project_root / "src"))


def print_banner(title: str, char: str = "═", width: int = 70, color: str = ""):
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


def print_info(label: str, value: str, icon: str = "•"):
    """Print an info line with colors."""
    print(f"{Fore.CYAN}{icon} {Style.BRIGHT}{label}:{Style.RESET_ALL} {Fore.WHITE}{value}")


def print_success(message: str):
    """Print a success message."""
    print(f"{Fore.GREEN}✓ {message}{Style.RESET_ALL}")


def print_error(message: str):
    """Print an error message."""
    print(f"{Fore.RED}✗ {message}{Style.RESET_ALL}")


def print_warning(message: str):
    """Print a warning message."""
    print(f"{Fore.YELLOW}⚠ {message}{Style.RESET_ALL}")


def main_wrapper():
    """Main entry point with beautiful CLI."""
    parser = argparse.ArgumentParser(
        description="Run MAHILDA on test database with beautiful output",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose output (show all discovered rules)"
    )
    parser.add_argument(
        "-q", "--quiet",
        action="store_true",
        help="Quiet mode (minimal output)"
    )
    args = parser.parse_args()
    
    # Set environment variable for verbose mode
    if args.verbose:
        os.environ["MAHILDA_VERBOSE"] = "1"
    elif args.quiet:
        os.environ["MAHILDA_QUIET"] = "1"
    
    from src.main import main
    
    # Override sys.argv to use test config
    original_argv = sys.argv
    sys.argv = [sys.argv[0], "--config", "config_test.yaml"]
    
    try:
        if not args.quiet:
            print()
            print_banner("🔍 MAHILDA Rule Discovery", "═", 70, Fore.CYAN)
            print()
            print_info("Working directory", os.getcwd(), "📁")
            print_info("Database", "test_data/test.db", "💾")
            print_info("Configuration", "config_test.yaml", "⚙️")
            print_info("Results location", "results_test/", "📊")
            if args.verbose:
                print_info("Mode", "Verbose (all rules shown)", "🔊")
            print()
            print(f"{Fore.YELLOW}{'─' * 70}{Style.RESET_ALL}")
            print()
        
        start_time = time.time()
        main()
        elapsed = time.time() - start_time
        
        if not args.quiet:
            print()
            print(f"{Fore.YELLOW}{'─' * 70}{Style.RESET_ALL}")
            print()
            print_success(f"Process completed in {elapsed:.2f} seconds")
            print()
            print_banner("✨ Done!", "═", 70, Fore.GREEN)
            print()
            
    except KeyboardInterrupt:
        print()
        print_warning("Process interrupted by user")
        sys.exit(0)
    except Exception as e:
        print()
        print_error(f"An error occurred: {e}")
        if args.verbose:
            import traceback
            print()
            print(f"{Fore.RED}{Style.DIM}")
            traceback.print_exc()
            print(f"{Style.RESET_ALL}")
        sys.exit(1)
    finally:
        sys.argv = original_argv


if __name__ == "__main__":
    main_wrapper()
