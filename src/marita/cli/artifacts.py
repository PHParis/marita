from __future__ import annotations

import datetime
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True)
class CommandArtifacts:
    run_dir: Path
    result_json: Path
    report_md: Path
    execution_time_json: Path


def build_command_artifacts(results_dir: Path, command_name: str, database_name: Path) -> CommandArtifacts:
    database_stem = database_name.stem
    command_database = f"{command_name}_{database_stem}"
    run_dir = results_dir / command_database
    return CommandArtifacts(
        run_dir=run_dir,
        result_json=run_dir / f"{command_database}_results.json",
        report_md=results_dir / f"report_{command_database}.md",
        execution_time_json=run_dir / f"execution_time_{database_stem}.json",
    )


def format_duration(seconds: float) -> str:
    if seconds < 1.0:
        return f"{seconds * 1000:.2f} ms"
    if seconds < 60:
        return f"{seconds:.3f} seconds"
    if seconds < 3600:
        minutes = int(seconds // 60)
        remaining_seconds = seconds % 60
        return f"{minutes}m {remaining_seconds:.1f}s"
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    return f"{hours}h {minutes}m"


def _format_rule_description(rule: Any) -> str:
    display = getattr(rule, "display", None)
    if display:
        return str(display)

    table_dependant = getattr(rule, "table_dependant", None)
    columns_dependant = getattr(rule, "columns_dependant", None)
    table_referenced = getattr(rule, "table_referenced", None)
    columns_referenced = getattr(rule, "columns_referenced", None)
    if table_dependant and columns_dependant and table_referenced and columns_referenced:
        dependant = f"{table_dependant}({', '.join(map(str, columns_dependant))})"
        referenced = f"{table_referenced}({', '.join(map(str, columns_referenced))})"
        return f"{dependant} <= {referenced}"

    return str(rule)


def _format_optional_score(value: Any) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, int | float):
        return f"{float(value):.3f}"
    return str(value)


def write_markdown_report(
    *,
    report_path: Path,
    report_title: str,
    subject_label: str,
    subject_name: str,
    database_name: str,
    number_of_rules: int,
    result_path: Path,
    top_rules: list[Any],
    execution_time: float | None = None,
) -> None:
    execution_time_label = format_duration(execution_time) if execution_time is not None else "N/A"

    lines = [
        f"# {report_title}",
        "",
        f"**Date:** {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"**{subject_label}:** {subject_name}",
        f"**Database:** {database_name}",
        f"**Number of Rules Discovered:** {number_of_rules}",
        f"**Execution Time:** {execution_time_label}",
        f"**Results Path:** {result_path}",
        "",
        "## Top 5 Best Rules",
        "Below are the top-5 best rules discovered based on their scores:",
        "",
        "| Rank | Rule Description | Support | Confidence |",
        "|------|------------------|---------|------------|",
    ]

    for index, rule in enumerate(top_rules[:5], start=1):
        rule_desc = _format_rule_description(rule).replace("\n", " ").replace("|", "\\|")
        accuracy = getattr(rule, "support", getattr(rule, "accuracy", "N/A"))
        confidence = getattr(rule, "confidence", "N/A")

        support_display = _format_optional_score(accuracy)
        confidence_display = _format_optional_score(confidence)
        lines.append(f"| {index} | {rule_desc} | {support_display} | {confidence_display} |")

    lines.extend(
        [
            "",
            "## Details",
            "The rule discovery process completed successfully. The discovered rules were written to the results path above.",
            "",
        ]
    )
    report_path.write_text("\n".join(lines), encoding="utf-8")


def write_execution_time_metrics(
    *,
    metrics_path: Path,
    database_stem: str,
    execution_time: float,
    status: str,
    rules_count: int,
    algorithm_name: str,
    start_time: datetime.datetime | None = None,
    end_time: datetime.datetime | None = None,
) -> None:
    payload: dict[str, Any] = {
        "database": database_stem,
        "execution_time_seconds": execution_time,
        "execution_time_ms": execution_time * 1000,
        "status": status,
        "rules_count": rules_count,
        "algorithm": algorithm_name,
        "timestamp": datetime.datetime.now().isoformat(),
    }
    if start_time is not None:
        payload["start_time"] = start_time.isoformat()
    if end_time is not None:
        payload["end_time"] = end_time.isoformat()

    metrics_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def write_batch_summary(summary_path: Path, all_results: list[dict[str, Any]], total_duration: float) -> None:
    successful = [result for result in all_results if result["status"] == "success"]
    timeouts = [result for result in all_results if result["status"] == "timeout"]
    errors = [result for result in all_results if result["status"] == "error"]

    lines = [
        "=" * 80,
        "MARITA Batch Processing Summary",
        "=" * 80,
        "",
        f"Date: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"Total databases: {len(all_results)}",
        f"Successful: {len(successful)}",
        f"Timeouts: {len(timeouts)}",
        f"Errors: {len(errors)}",
        f"Total duration: {format_duration(total_duration)}",
        "",
    ]

    if successful:
        total_rules = sum(result["rules_count"] for result in successful)
        avg_duration = sum(result["duration"] for result in successful) / len(successful)
        lines.extend([f"Total rules: {total_rules}", f"Average duration: {format_duration(avg_duration)}", ""])

    lines.extend(["Detailed Results:", "-" * 80, ""])
    for result in all_results:
        lines.append(f"Database: {result['database']}")
        lines.append(f"Status: {result['status']}")
        lines.append(f"Duration: {format_duration(float(result['duration']))}")
        if result["status"] == "success":
            lines.append(f"Rules: {result['rules_count']}")
        elif result.get("error"):
            lines.append(f"Error: {result['error']}")
        lines.append("")

    summary_path.write_text("\n".join(lines), encoding="utf-8")
