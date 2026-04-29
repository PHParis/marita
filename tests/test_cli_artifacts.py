from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from mahilda.cli.artifacts import (
    build_command_artifacts,
    format_duration,
    write_batch_summary,
    write_execution_time_metrics,
    write_markdown_report,
)


def test_build_command_artifacts_paths(tmp_path: Path) -> None:
    artifacts = build_command_artifacts(tmp_path, "MAHILDA", Path("demo.db"))
    assert artifacts.run_dir == tmp_path / "MAHILDA_demo"
    assert artifacts.result_json.name == "MAHILDA_demo_results.json"
    assert artifacts.report_md.name == "report_MAHILDA_demo.md"
    assert artifacts.execution_time_json.name == "execution_time_demo.json"


def test_format_duration_variants() -> None:
    assert format_duration(0.5).endswith("ms")
    assert format_duration(3.25).endswith("seconds")
    assert format_duration(90).startswith("1m")
    assert format_duration(3700).startswith("1h")


def test_write_markdown_report_and_escape(tmp_path: Path) -> None:
    report = tmp_path / "report.md"
    top_rules = [
        SimpleNamespace(display="A(x)|B(y)", accuracy=0.1234, confidence=0.5678),
        "raw rule",
    ]

    write_markdown_report(
        report_path=report,
        report_title="Demo Report",
        subject_label="Algorithm",
        subject_name="MAHILDA",
        database_name="demo.db",
        number_of_rules=2,
        result_path=tmp_path / "results.json",
        top_rules=top_rules,
        execution_time=1.2,
    )

    text = report.read_text(encoding="utf-8")
    assert "# Demo Report" in text
    assert "A(x)\\|B(y)" in text
    assert "0.123" in text
    assert "0.568" in text
    assert "raw rule" in text


def test_write_execution_time_metrics_payload(tmp_path: Path) -> None:
    metrics = tmp_path / "metrics.json"
    start = datetime.now()
    end = datetime.now()
    write_execution_time_metrics(
        metrics_path=metrics,
        database_stem="demo",
        execution_time=2.5,
        status="success",
        rules_count=7,
        algorithm_name="MAHILDA",
        start_time=start,
        end_time=end,
    )
    payload = json.loads(metrics.read_text(encoding="utf-8"))
    assert payload["database"] == "demo"
    assert payload["execution_time_seconds"] == 2.5
    assert payload["execution_time_ms"] == 2500.0
    assert payload["status"] == "success"
    assert payload["rules_count"] == 7
    assert payload["algorithm"] == "MAHILDA"
    assert payload["start_time"] == start.isoformat()
    assert payload["end_time"] == end.isoformat()


def test_write_batch_summary_with_success_timeout_error(tmp_path: Path) -> None:
    summary = tmp_path / "summary.txt"
    all_results = [
        {"database": "a", "status": "success", "duration": 1.0, "rules_count": 5, "error": None},
        {"database": "b", "status": "timeout", "duration": 70.0, "rules_count": 0, "error": "late"},
        {"database": "c", "status": "error", "duration": 2.0, "rules_count": 0, "error": "boom"},
    ]

    write_batch_summary(summary, all_results, total_duration=73.0)

    text = summary.read_text(encoding="utf-8")
    assert "Total databases: 3" in text
    assert "Successful: 1" in text
    assert "Timeouts: 1" in text
    assert "Errors: 1" in text
    assert "Total rules: 5" in text
    assert "Database: a" in text
    assert "Database: b" in text
    assert "Error: boom" in text
