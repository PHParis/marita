from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING

from mahilda.audit import AuditConfig, run_audit
from mahilda.audit.evaluator import SQLiteRuleEvaluator
from mahilda.audit.models import AuditClassification, MatchStatus
from mahilda.audit.parsing import parse_formula

if TYPE_CHECKING:
    from pathlib import Path


def test_parse_formula_canonicalizes_variable_names() -> None:
    first = parse_formula("∀ x0: child_0(parent_id=x0) ⇒ parent_0(id=x0)")
    second = parse_formula("∀ z9: child_7(parent_id=z9) ⇒ parent_3(id=z9)")

    assert first.canonical_key() == second.canonical_key()


def test_sqlite_evaluator_recomputes_confidence(tmp_path: Path) -> None:
    db_path = _write_tiny_database(tmp_path)
    evaluator = SQLiteRuleEvaluator(db_path)
    try:
        exact = parse_formula("∀ x0: child_0(parent_id=x0) ⇒ parent_0(id=x0)")
        approximate = parse_formula("∀ x0: parent_0(id=x0) ⇒ child_0(parent_id=x0)")

        exact_eval = evaluator.evaluate(exact)
        approximate_eval = evaluator.evaluate(approximate)
    finally:
        evaluator.close()

    assert exact_eval.support == 1
    assert exact_eval.predictions == 1
    assert exact_eval.confidence == 1.0
    assert approximate_eval.support == 1
    assert approximate_eval.predictions == 2
    assert approximate_eval.confidence == 0.5


def test_run_audit_classifies_and_reports(tmp_path: Path) -> None:
    database_dir = tmp_path / "data"
    results_dir = tmp_path / "results"
    output_dir = tmp_path / "audit"
    database_dir.mkdir()
    _write_tiny_database(database_dir, "tiny.db")
    _write_results(
        results_dir,
        "MAHILDA",
        "tiny",
        ["∀ x0: child_0(parent_id=x0) ⇒ parent_0(id=x0)"],
    )
    _write_results(
        results_dir,
        "MATILDA",
        "tiny",
        [
            "∀ y0: child_9(parent_id=y0) ⇒ parent_2(id=y0)",
            "∀ x0: parent_0(id=x0) ⇒ child_0(parent_id=x0)",
            "∀ x0: child_0(parent_id=x0) ⇒ child_1(parent_id=x0)",
            "∀ x0: child_0(parent_id=x0) ⇒ parent_0(id=y0)",
        ],
    )

    records = run_audit(
        AuditConfig(
            results_dir=results_dir,
            database_dir=database_dir,
            output_dir=output_dir,
            competitors=("MATILDA",),
            show_progress=False,
        )
    )

    statuses = {(record.classification, record.match_status, record.reason) for record in records}
    assert (
        AuditClassification.COMPARABLE_TRUE,
        MatchStatus.RECALLED_ALPHA,
        "matched",
    ) in statuses
    assert any(record.classification == AuditClassification.APPROXIMATE for record in records)
    assert any(record.classification == AuditClassification.VACUOUS for record in records)
    assert any(record.reason == "existential_or_head_only_variable" for record in records)
    assert (output_dir / "audit_summary.json").exists()
    assert (output_dir / "audit_rules.csv").exists()
    assert (output_dir / "audit_claims.md").exists()


def _write_tiny_database(directory: Path, name: str = "tiny.db") -> Path:
    db_path = directory / name
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("CREATE TABLE parent (id INTEGER PRIMARY KEY, name TEXT)")
        connection.execute(
            "CREATE TABLE child (id INTEGER PRIMARY KEY, parent_id INTEGER REFERENCES parent(id), label TEXT)"
        )
        connection.executemany("INSERT INTO parent (id, name) VALUES (?, ?)", [(1, "one"), (2, "two")])
        connection.execute("INSERT INTO child (id, parent_id, label) VALUES (10, 1, 'child')")
    return db_path


def _write_results(results_dir: Path, algorithm: str, database: str, displays: list[str]) -> None:
    run_dir = results_dir / algorithm / f"{algorithm}_{database}"
    run_dir.mkdir(parents=True)
    payload = [
        {
            "type": "TGDRule",
            "body": [],
            "head": [],
            "display": display,
            "accuracy": 1.0,
            "confidence": 1.0,
            "correct": None,
            "compatible": None,
        }
        for display in displays
    ]
    (run_dir / f"{algorithm}_{database}_results.json").write_text(json.dumps(payload), encoding="utf-8")
