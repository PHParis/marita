#!/usr/bin/env python3
"""
Convert MAHILDA Results to LaTeX Tables

This script generates LaTeX tables from MAHILDA rule discovery results.
Supports multiple output formats and filtering options.

Usage:
    python3 results_to_latex.py [options]
    
Options:
    --output FILE           Output LaTeX file (default: mahilda_results.tex)
    --top N                 Show top N rules per database (default: 10)
    --metric METRIC         Sort by metric: accuracy, confidence, support (default: accuracy)
    --summary               Generate summary statistics table only
    --all                   Generate all possible tables
    --format FORMAT         Table format: booktabs, tabular, longtable (default: booktabs)
"""

import json
import os
from pathlib import Path
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass
import argparse
from datetime import datetime
import time


@dataclass
class Rule:
    """Represents a discovered rule"""
    display: str
    accuracy: float
    confidence: float
    body_len: int
    head_len: int
    
    @property
    def support(self) -> float:
        """Compute support as (body_len * accuracy) / 100"""
        return (self.body_len * self.accuracy) / 100 if self.accuracy else 0


@dataclass
class DatabaseResults:
    """Results for a single database"""
    database: str
    rules: List[Rule]
    num_rules: int
    avg_accuracy: float
    avg_confidence: float
    execution_time: Optional[float] = None


def load_results(base_path: str = "results_all_databases") -> Dict[str, DatabaseResults]:
    """Load all MAHILDA results from the results directory"""
    results = {}
    base_path = Path(base_path)
    
    if not base_path.exists():
        print(f"Error: {base_path} not found")
        return results
    
    # Find all result JSON files
    for result_file in sorted(base_path.glob("MAHILDA_*/MAHILDA_*_results.json")):
        db_dir = result_file.parent.name  # e.g., "MAHILDA_Bupa"
        db_name = db_dir.replace("MAHILDA_", "")
        
        try:
            with open(result_file, 'r') as f:
                rules_data = json.load(f)
            
            rules = []
            for rule_data in rules_data:
                rule = Rule(
                    display=rule_data.get('display', 'N/A'),
                    accuracy=float(rule_data.get('accuracy', 0)),
                    confidence=float(rule_data.get('confidence', 0)),
                    body_len=len(rule_data.get('body', [])),
                    head_len=len(rule_data.get('head', []))
                )
                rules.append(rule)
            
            # Calculate statistics
            num_rules = len(rules)
            avg_accuracy = sum(r.accuracy for r in rules) / len(rules) if rules else 0
            avg_confidence = sum(r.confidence for r in rules) / len(rules) if rules else 0
            
            # Try to load execution time from multiple possible files
            execution_time = None
            
            # Try new execution_time file first
            exec_time_file = result_file.parent / f"execution_time_{db_name}.json"
            if exec_time_file.exists():
                try:
                    with open(exec_time_file, 'r') as f:
                        time_data = json.load(f)
                        execution_time = float(time_data.get('execution_time_seconds', 0))
                except:
                    pass
            
            # Fallback to old init_time_metrics file
            if execution_time is None:
                init_time_file = result_file.parent / f"init_time_metrics_{db_name}.json"
                if init_time_file.exists():
                    try:
                        with open(init_time_file, 'r') as f:
                            time_data = json.load(f)
                            execution_time = float(time_data.get('total_time', 0))
                    except:
                        pass
            
            results[db_name] = DatabaseResults(
                database=db_name,
                rules=rules,
                num_rules=num_rules,
                avg_accuracy=avg_accuracy,
                avg_confidence=avg_confidence,
                execution_time=execution_time
            )
            
            print(f"✓ Loaded {db_name}: {num_rules} rules")
            
        except Exception as e:
            print(f"✗ Error loading {db_name}: {e}")
    
    return results


def escape_latex(text: str) -> str:
    """Escape special LaTeX characters"""
    replacements = {
        '\\': r'\textbackslash{}',
        '&': r'\&',
        '%': r'\%',
        '$': r'\$',
        '#': r'\#',
        '_': r'\_',
        '{': r'\{',
        '}': r'\}',
        '~': r'\textasciitilde{}',
        '^': r'\textasciicircum{}',
    }
    result = text
    for char, replacement in replacements.items():
        result = result.replace(char, replacement)
    return result


def truncate_rule(rule: str, max_length: int = 80) -> str:
    """Truncate rule display for table"""
    rule = escape_latex(rule)
    if len(rule) > max_length:
        return rule[:max_length-3] + "..."
    return rule


def format_metric(value: float, metric_type: str = "float") -> str:
    """Format metric for display"""
    if metric_type == "float":
        return f"{value:.4f}"
    elif metric_type == "percentage":
        return f"{value*100:.2f}\\%"
    elif metric_type == "time":
        # Display in ms if < 1 second, otherwise in seconds
        if value < 1.0:
            return f"{value*1000:.2f}ms"
        elif value < 60:
            return f"{value:.3f}s"
        elif value < 3600:
            minutes = int(value // 60)
            seconds = value % 60
            return f"{minutes}m {seconds:.1f}s"
        else:
            hours = int(value // 3600)
            minutes = int((value % 3600) // 60)
            return f"{hours}h {minutes}m"
    else:
        return str(value)


def generate_summary_table(results: Dict[str, DatabaseResults]) -> str:
    """Generate summary statistics table"""
    latex = []
    latex.append(r"\begin{table}[h!]")
    latex.append(r"\centering")
    latex.append(r"\caption{MAHILDA Rule Discovery Summary Statistics}")
    latex.append(r"\label{tab:mahilda_summary}")
    latex.append(r"\begin{tabular}{|l|r|r|r|r|}")
    latex.append(r"\hline")
    latex.append(r"\textbf{Database} & \textbf{Rules} & \textbf{Accuracy} & \textbf{Confidence} & \textbf{Time (s)} \\")
    latex.append(r"\hline")
    
    total_rules = 0
    for db_name in sorted(results.keys()):
        db_results = results[db_name]
        total_rules += db_results.num_rules
        
        time_str = format_metric(db_results.execution_time, "time") if db_results.execution_time else "N/A"
        
        latex.append(
            f"{db_name} & "
            f"{db_results.num_rules} & "
            f"{format_metric(db_results.avg_accuracy)} & "
            f"{format_metric(db_results.avg_confidence)} & "
            f"{time_str} \\\\"
        )
    
    latex.append(r"\hline")
    latex.append(f"\\textbf{{Total}} & \\textbf{{{total_rules}}} & & & \\\\")
    latex.append(r"\hline")
    latex.append(r"\end{tabular}")
    latex.append(r"\end{table}")
    latex.append("")
    
    return "\n".join(latex)


def generate_top_rules_tables(
    results: Dict[str, DatabaseResults],
    top_n: int = 10,
    metric: str = "accuracy",
    format_type: str = "booktabs"
) -> str:
    """Generate tables with top N rules per database"""
    latex = []
    
    for db_name in sorted(results.keys()):
        db_results = results[db_name]
        
        # Sort rules by selected metric
        if metric == "accuracy":
            sorted_rules = sorted(db_results.rules, key=lambda r: r.accuracy, reverse=True)
        elif metric == "confidence":
            sorted_rules = sorted(db_results.rules, key=lambda r: r.confidence, reverse=True)
        elif metric == "support":
            sorted_rules = sorted(db_results.rules, key=lambda r: r.support, reverse=True)
        else:
            sorted_rules = db_results.rules
        
        top_rules = sorted_rules[:top_n]
        
        if not top_rules:
            continue
        
        # Generate table
        safe_name = db_name.replace("_", "").replace("-", "")
        latex.append(r"\begin{table}[h!]")
        latex.append(r"\centering")
        latex.append(r"\small")
        latex.append(f"\\caption{{Top {top_n} Rules Discovered in {db_name}}}")
        latex.append(f"\\label{{tab:rules_{safe_name}}}")
        
        if format_type == "booktabs":
            latex.append(r"\begin{tabular}{|c|p{5cm}|c|c|}")
            latex.append(r"\hline")
            latex.append(r"\textbf{Rank} & \textbf{Rule} & \textbf{Accuracy} & \textbf{Confidence} \\")
            latex.append(r"\hline")
            
            for i, rule in enumerate(top_rules, 1):
                latex.append(
                    f"{i} & "
                    f"{truncate_rule(rule.display, 60)} & "
                    f"{format_metric(rule.accuracy)} & "
                    f"{format_metric(rule.confidence)} \\\\"
                )
        
        elif format_type == "longtable":
            latex.append(r"\begin{longtable}{|c|p{5cm}|c|c|}")
            latex.append(r"\hline")
            latex.append(r"\textbf{Rank} & \textbf{Rule} & \textbf{Accuracy} & \textbf{Confidence} \\")
            latex.append(r"\hline")
            latex.append(r"\endhead")
            
            for i, rule in enumerate(top_rules, 1):
                latex.append(
                    f"{i} & "
                    f"{truncate_rule(rule.display, 60)} & "
                    f"{format_metric(rule.accuracy)} & "
                    f"{format_metric(rule.confidence)} \\\\"
                )
            
            latex.append(r"\hline")
            latex.append(r"\end{longtable}")
            latex.append("")
            continue
        
        latex.append(r"\hline")
        latex.append(r"\end{tabular}")
        latex.append(r"\end{table}")
        latex.append("")
    
    return "\n".join(latex)


def generate_all_rules_table(results: Dict[str, DatabaseResults]) -> str:
    """Generate a comprehensive table with all rules"""
    latex = []
    latex.append(r"\begin{table}[h!]")
    latex.append(r"\centering")
    latex.append(r"\tiny")
    latex.append(r"\caption{All MAHILDA Rules - Complete Results}")
    latex.append(r"\label{tab:all_rules}")
    latex.append(r"\begin{longtable}{|l|p{6cm}|c|c|c|c|}")
    latex.append(r"\hline")
    latex.append(
        r"\textbf{Database} & \textbf{Rule} & \textbf{Body} & "
        r"\textbf{Head} & \textbf{Accuracy} & \textbf{Confidence} \\"
    )
    latex.append(r"\hline")
    latex.append(r"\endhead")
    
    total_rules = 0
    for db_name in sorted(results.keys()):
        db_results = results[db_name]
        for rule in sorted(db_results.rules, key=lambda r: r.accuracy, reverse=True):
            latex.append(
                f"{db_name} & "
                f"{truncate_rule(rule.display, 50)} & "
                f"{rule.body_len} & "
                f"{rule.head_len} & "
                f"{format_metric(rule.accuracy)} & "
                f"{format_metric(rule.confidence)} \\\\"
            )
            total_rules += 1
    
    latex.append(r"\hline")
    latex.append(f"\\textbf{{Total Rules}} & & & & & \\textbf{{{total_rules}}} \\\\")
    latex.append(r"\hline")
    latex.append(r"\end{longtable}")
    latex.append(r"\end{table}")
    latex.append("")
    
    return "\n".join(latex)


def generate_latex_document(
    results: Dict[str, DatabaseResults],
    summary_only: bool = False,
    all_tables: bool = False,
    top_n: int = 10,
    metric: str = "accuracy",
    format_type: str = "booktabs"
) -> str:
    """Generate complete LaTeX document"""
    latex = []
    
    # Document preamble
    latex.append(r"\documentclass[11pt,a4paper]{article}")
    latex.append(r"\usepackage{longtable}")
    latex.append(r"\usepackage{booktabs}")
    latex.append(r"\usepackage{array}")
    latex.append(r"\usepackage{geometry}")
    latex.append(r"\geometry{margin=1in}")
    latex.append(r"\usepackage{hyperref}")
    latex.append(r"\usepackage{xcolor}")
    latex.append("")
    latex.append(r"\title{MAHILDA Rule Discovery Results}")
    latex.append(f"\\author{{Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}}}")
    latex.append(r"\date{\today}")
    latex.append("")
    latex.append(r"\begin{document}")
    latex.append(r"\maketitle")
    latex.append("")
    
    # Generate tables
    latex.append(r"\section{Summary Statistics}")
    latex.append(generate_summary_table(results))
    
    if not summary_only:
        latex.append(r"\section{Top Rules by Database}")
        latex.append(
            f"The following tables show the top {top_n} rules discovered in each database, "
            f"sorted by {metric}:\n"
        )
        latex.append("")
        latex.append(generate_top_rules_tables(results, top_n, metric, format_type))
    
    if all_tables:
        latex.append(r"\section{Complete Results}")
        latex.append(generate_all_rules_table(results))
    
    # Document closing
    latex.append(r"\end{document}")
    
    return "\n".join(latex)


def main():
    # Start timing
    start_time = time.perf_counter()
    
    parser = argparse.ArgumentParser(
        description="Convert MAHILDA results to LaTeX tables",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    
    parser.add_argument("--output", "-o", default="mahilda_results.tex",
                        help="Output LaTeX file (default: mahilda_results.tex)")
    parser.add_argument("--top", "-t", type=int, default=10,
                        help="Top N rules per database (default: 10)")
    parser.add_argument("--metric", "-m", choices=["accuracy", "confidence", "support"],
                        default="accuracy", help="Sort metric (default: accuracy)")
    parser.add_argument("--summary", "-s", action="store_true",
                        help="Generate summary table only")
    parser.add_argument("--all", "-a", action="store_true",
                        help="Generate all possible tables")
    parser.add_argument("--format", "-f", choices=["booktabs", "tabular", "longtable"],
                        default="booktabs", help="Table format (default: booktabs)")
    parser.add_argument("--results-dir", "-d", default="results_all_databases",
                        help="Results directory (default: results_all_databases)")
    parser.add_argument("--compile", "-c", action="store_true",
                        help="Compile PDF with pdflatex")
    
    args = parser.parse_args()
    
    print("🔍 Loading MAHILDA results...")
    results = load_results(args.results_dir)
    
    if not results:
        print("✗ No results found!")
        return
    
    print(f"\n✓ Loaded {len(results)} databases with {sum(r.num_rules for r in results.values())} total rules")
    
    print(f"\n📝 Generating LaTeX document...")
    latex_content = generate_latex_document(
        results,
        summary_only=args.summary,
        all_tables=args.all,
        top_n=args.top,
        metric=args.metric,
        format_type=args.format
    )
    
    # Write to file
    with open(args.output, 'w') as f:
        f.write(latex_content)
    
    print(f"✓ LaTeX file created: {args.output}")
    print(f"  Size: {len(latex_content)} bytes")
    
    # Optionally compile PDF
    if args.compile:
        print(f"\n🔨 Compiling PDF...")
        os.system(f"cd {Path(args.output).parent} && pdflatex -interaction=nonstopmode {Path(args.output).name}")
        pdf_name = args.output.replace(".tex", ".pdf")
        print(f"✓ PDF generated: {pdf_name}")
    
    # End timing and display execution time
    end_time = time.perf_counter()
    execution_time_ms = (end_time - start_time) * 1000
    
    print(f"\n⏱️  Execution time: {execution_time_ms:.3f} ms ({execution_time_ms/1000:.6f} s)")
    print("\n✅ Done!")


if __name__ == "__main__":
    main()
