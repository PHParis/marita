from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING

from scripts.capture_mahilda_baseline import (
    collect_sqlite_metadata,
    normalize_rule,
    parse_artifact_snapshot,
    parse_compatibility,
    parse_constraint_graph,
    parse_log_attempts,
    reconcile_database,
    safe_load_json_text,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_parse_log_attempts_groups_repeated_runs_and_reconciles_timeout() -> None:
    text = """
2026-07-19 09:00:00,000 [INFO] - \x1b[36mStarting rule discovery process.\x1b[0m
2026-07-19 09:00:01,000 [INFO] - Using database URI: sqlite:////data/demo.db
2026-07-19 09:00:03,000 [INFO] - Discovered 2 rules.
2026-07-19 09:00:03,100 [INFO] - Process completed successfully.
2026-07-19 10:00:00,000 [INFO] - Starting rule discovery process.
2026-07-19 10:00:01,000 [INFO] - Using database URI: sqlite:////data/demo.db
2026-07-19 10:10:01,000 [ERROR] - Execution timeout exceeded: 600.2s > 600s
2026-07-19 10:10:01,100 [INFO] - Received signal 15. Shutting down gracefully...
"""

    attempts = parse_log_attempts(text, "MAHILDA/demo/global.log")

    assert len(attempts) == 2
    assert attempts[0]["database"] == "demo"
    assert attempts[0]["status"] == "success"
    assert attempts[0]["rules_count"] == 2
    assert attempts[1]["status"] == "timeout"
    assert attempts[1]["runtime_seconds"] == 600.2
    assert attempts[1]["ended_at"] == "2026-07-19T10:10:01.100"


def test_parse_log_attempts_preserves_memory_and_no_joinable_statuses() -> None:
    memory_text = """
2026-07-19 09:00:00,000 [INFO] - Starting rule discovery process.
2026-07-19 09:00:01,000 [INFO] - Using database URI: sqlite:////data/tpcds.db
2026-07-19 09:05:01,000 [ERROR] - Memory usage exceeded: 10.01 GB
2026-07-19 09:05:01,100 [INFO] - Received signal 15. Shutting down gracefully...
"""
    no_joinable_text = """
2026-07-19 09:00:00,000 [INFO] - Starting rule discovery process.
2026-07-19 09:00:01,000 [INFO] - Using database URI: sqlite:////data/empty.db
2026-07-19 09:00:02,000 [INFO] - No joinable indexed attributes found.
"""

    memory_attempt = parse_log_attempts(memory_text)[0]
    no_joinable_attempt = parse_log_attempts(no_joinable_text)[0]

    assert memory_attempt["status"] == "memory_limit"
    assert memory_attempt["memory_event_gb"] == 10.01
    assert no_joinable_attempt["status"] == "no_joinable_indexed"


def test_safe_json_loading_distinguishes_empty_and_malformed() -> None:
    assert safe_load_json_text("   ") == (None, "empty_artifact")
    value, error = safe_load_json_text('{"ok": true}')
    assert value == {"ok": True}
    assert error is None
    value, error = safe_load_json_text("not json")
    assert value is None
    assert error is not None and error.startswith("malformed_json:")


def test_constraint_graph_and_compatibility_summaries() -> None:
    graph = parse_constraint_graph(
        "ConstraintGraph(Nodes: 3, Edges: 2)\n"
        "JIA((i=0, j=0, k=0), (i=1, j=0, k=0)) -> x\n"
        "JIA((i=0, j=0, k=0), (i=2, j=0, k=0)) -> y\n"
    )
    compatibility = parse_compatibility({"a___sep___id": ["b___sep___id"], "b___sep___id": ["a___sep___id"]})

    assert graph == {"parse_status": "ok", "nodes": 3, "edges": 2, "listed_edge_lines": 2}
    assert compatibility["attribute_count"] == 2
    assert compatibility["directed_pair_count"] == 2
    assert compatibility["pair_count"] == 1
    assert parse_compatibility([])["parse_status"] == "invalid_type"


def test_rule_normalization_has_stable_hash() -> None:
    rule = {
        "type": "MARITARule",
        "body": ["a(x)"],
        "head": ["b(x)"],
        "display": "a(x) => b(x)",
        "support": 3,
        "confidence": 0.5,
    }
    first = normalize_rule("demo", 1, rule)
    second = normalize_rule("demo", 1, dict(rule))

    assert first == second
    assert len(first["rule_hash"]) == 64
    assert first["support"] == 3


def test_artifact_snapshot_handles_empty_malformed_and_metric_only_files(tmp_path: Path) -> None:
    results = tmp_path / "results"
    complete = results / "MAHILDA_demo"
    complete.mkdir(parents=True)
    (complete / "MAHILDA_demo_results.json").write_text("[]", encoding="utf-8")
    (complete / "execution_time_demo.json").write_text(
        json.dumps({"status": "success", "rules_count": 0}), encoding="utf-8"
    )
    (complete / "init_time_metrics_demo.json").write_text("{}", encoding="utf-8")
    (complete / "cg_metrics_demo.json").write_text('"ConstraintGraph(Nodes: 0, Edges: 0)"', encoding="utf-8")
    (complete / "compatibility_demo.json").write_text("{}", encoding="utf-8")
    (results / "report_MAHILDA_demo.md").write_text("**Number of Rules Discovered:** 0", encoding="utf-8")

    partial = results / "MAHILDA_partial"
    partial.mkdir(parents=True)
    (partial / "init_time_metrics_partial.json").write_text("{}", encoding="utf-8")
    (partial / "cg_metrics_partial.json").write_text("{malformed", encoding="utf-8")
    (partial / "compatibility_partial.json").write_text("", encoding="utf-8")

    malformed = results / "MAHILDA_bad"
    malformed.mkdir(parents=True)
    (malformed / "MAHILDA_bad_results.json").write_text("{malformed", encoding="utf-8")

    snapshots, rules = parse_artifact_snapshot(results)

    assert snapshots["demo"]["state"] == "complete"
    assert snapshots["demo"]["artifacts"]["rule_json"]["rule_count"] == 0
    assert snapshots["partial"]["state"] == "partial"
    assert any("empty_artifact" in error for error in snapshots["partial"]["parse_errors"])
    assert any("malformed_json" in error for error in snapshots["bad"]["parse_errors"])
    assert rules == []


def test_reconcile_database_reports_cross_artifact_disagreements() -> None:
    snapshot = {
        "state": "complete",
        "artifacts": {
            "rule_json": {"available": True, "rule_count": 2},
            "execution_time_json": {"available": True, "reported_status": "success", "reported_rules_count": 3},
            "report_md": {"available": True, "reported_rules_count": 2},
        },
    }
    attempts = [{"status": "timeout"}]

    checks = reconcile_database("demo", attempts, snapshot)

    assert checks["latest_log_status"] == "timeout"
    assert "rule_json_vs_execution_rules_count" in checks["disagreements"]
    assert "execution_status_vs_latest_log_attempt" in checks["disagreements"]


def test_collect_sqlite_metadata_reports_schema_counts(tmp_path: Path) -> None:
    database = tmp_path / "demo.db"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        PRAGMA foreign_keys = ON;
        CREATE TABLE parent (id INTEGER PRIMARY KEY, label TEXT);
        CREATE TABLE child (id INTEGER PRIMARY KEY, parent_id INTEGER, FOREIGN KEY(parent_id) REFERENCES parent(id));
        CREATE INDEX child_parent_idx ON child(parent_id);
        INSERT INTO parent VALUES (1, 'one');
        INSERT INTO child VALUES (1, 1);
        """
    )
    connection.close()

    metadata = collect_sqlite_metadata(database)

    assert metadata["size_bytes"] > 0
    assert len(metadata["sha256"]) == 64
    assert metadata["sqlite"]["table_count"] == 2
    assert metadata["sqlite"]["row_count"] == 2
    assert metadata["sqlite"]["column_count"] == 4
    assert metadata["sqlite"]["foreign_key_count"] == 1
    assert metadata["sqlite"]["index_count"] >= 1
