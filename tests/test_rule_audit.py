from __future__ import annotations

import csv
import json
import sqlite3
from typing import TYPE_CHECKING

from mahilda.audit import AuditConfig, run_audit
from mahilda.audit.evaluator import SQLiteRuleEvaluator
from mahilda.audit.matching import alpha_equivalent, covered_on_instance, subsumes
from mahilda.audit.models import AuditClassification, MatchStatus, ScopeStatus
from mahilda.audit.parsing import parse_formula
from mahilda.cli.audit import main as audit_main

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


def test_rule_matching_alpha_subsumption_and_instance_coverage(tmp_path: Path) -> None:
    db_path = _write_tiny_database(tmp_path)
    competitor = parse_formula("∀ x0: child_0(parent_id=x0) ⇒ parent_0(id=x0)")
    alpha_target = parse_formula("∀ y0: child_7(parent_id=y0) ⇒ parent_3(id=y0)")
    general_target = parse_formula("∀ z0: child_0(parent_id=z0) ⇒ parent_0(id=z0)")
    specific = parse_formula("∀ z0, z1: child_0(parent_id=z0, id=z1) ⇒ parent_0(id=z0)")
    instance_target = parse_formula("∀ x0, y0: child_0(parent_id=x0, id=y0) ⇒ parent_0(id=x0)")

    evaluator = SQLiteRuleEvaluator(db_path)
    try:
        assert alpha_equivalent(competitor, alpha_target)
        assert subsumes(general_target, specific)
        assert covered_on_instance(evaluator, competitor, [instance_target])
    finally:
        evaluator.close()


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
    assert any(record.claim_relevant and record.coverage_alpha for record in records)
    assert (output_dir / "audit_summary.json").exists()
    assert (output_dir / "audit_rules.csv").exists()
    assert (output_dir / "audit_diagnosis.md").exists()
    assert (output_dir / "audit_claims.md").exists()
    summary = json.loads((output_dir / "audit_summary.json").read_text(encoding="utf-8"))
    assert summary["coverage_mode"] == "alpha"
    assert "in_target_class" in summary["by_scope_status"]
    with (output_dir / "audit_rules.csv").open(encoding="utf-8", newline="") as handle:
        row = next(csv.DictReader(handle))
    assert "scope_status" in row
    assert "coverage_subsumption" in row
    assert "claim_relevant" in row


def test_audit_skips_amie_rdf_by_default(tmp_path: Path) -> None:
    database_dir = tmp_path / "data"
    results_dir = tmp_path / "results"
    output_dir = tmp_path / "audit"
    database_dir.mkdir()
    _write_tiny_database(database_dir, "tiny.db")
    _write_results(results_dir, "MAHILDA", "tiny", ["∀ x0: child_0(parent_id=x0) ⇒ parent_0(id=x0)"])
    _write_results(results_dir, "AMIE3", "tiny", ["?x <p> ?y => ?x <q> ?y"])

    skipped = run_audit(
        AuditConfig(
            results_dir=results_dir,
            database_dir=database_dir,
            output_dir=output_dir,
            competitors=("AMIE3",),
            show_progress=False,
        )
    )
    included = run_audit(
        AuditConfig(
            results_dir=results_dir,
            database_dir=database_dir,
            output_dir=output_dir,
            competitors=("AMIE3",),
            show_progress=False,
            include_amie_rdf=True,
        )
    )

    assert skipped == []
    assert included[0].scope_status == ScopeStatus.UNSUPPORTED_REPRESENTATION


def test_audit_cli_settings_and_explicit_flags_precedence(tmp_path: Path, monkeypatch) -> None:
    captured: dict[str, AuditConfig] = {}
    settings_path = tmp_path / "settings.yaml"
    settings_path.write_text(
        "algorithm:\n"
        "  parameters:\n"
        "    walk_length: 9\n"
        "    max_tables: 8\n"
        "    max_variables: 7\n"
        "    disjoint_semantics: false\n"
        "    joinability: full\n",
        encoding="utf-8",
    )

    def fake_run_audit(config: AuditConfig) -> list[object]:
        captured["config"] = config
        return []

    monkeypatch.setattr("mahilda.cli.audit.run_audit", fake_run_audit)

    exit_code = audit_main(
        [
            "--results-dir",
            str(tmp_path / "results"),
            "--database-dir",
            str(tmp_path / "data"),
            "--output-dir",
            str(tmp_path / "audit"),
            "--settings",
            str(settings_path),
            "--walk-length",
            "3",
            "--include-amie-rdf",
            "--no-diagnose-unmatched",
        ]
    )

    assert exit_code == 0
    assert captured["config"].walk_length == 3
    assert captured["config"].max_tables == 8
    assert captured["config"].max_variables == 7
    assert captured["config"].disjoint_semantics is False
    assert captured["config"].joinability == "full"
    assert captured["config"].include_amie_rdf is True
    assert captured["config"].diagnose_unmatched is False


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
