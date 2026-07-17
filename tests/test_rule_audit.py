from __future__ import annotations

import csv
import json
import sqlite3
from typing import TYPE_CHECKING

from mahilda.audit import AuditConfig, run_audit
from mahilda.audit.evaluator import SQLiteRuleEvaluator
from mahilda.audit.matching import alpha_equivalent, covered_on_instance, subsumes
from mahilda.audit.models import AuditClassification, MatchStatus, RelationalRule, ScopeStatus
from mahilda.audit.parsing import parse_formula
from mahilda.cli.audit import main as audit_main

if TYPE_CHECKING:
    from pathlib import Path

import pytest


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


def test_sqlite_fk_scope_accepts_both_fk_orientations_but_not_same_table(tmp_path: Path) -> None:
    db_path = _write_tiny_database(tmp_path)
    evaluator = SQLiteRuleEvaluator(db_path)
    try:
        child_to_parent = parse_formula("∀ x0: child_0(parent_id=x0) ⇒ parent_0(id=x0)")
        parent_to_child = parse_formula("∀ x0: parent_0(id=x0) ⇒ child_0(parent_id=x0)")
        same_table = parse_formula("∀ x0: child_0(id=x0) ⇒ child_1(id=x0)")

        assert evaluator.is_fk_joinable(child_to_parent) is True
        assert evaluator.is_fk_joinable(parent_to_child) is True
        assert evaluator.is_fk_joinable(same_table) is False
    finally:
        evaluator.close()


def test_rule_matching_alpha_subsumption_and_instance_coverage(tmp_path: Path) -> None:
    db_path = _write_tiny_database(tmp_path)
    competitor = parse_formula("∀ x0: child_0(parent_id=x0) ⇒ parent_0(id=x0)")
    alpha_target = parse_formula("∀ y0: child_7(parent_id=y0) ⇒ parent_3(id=y0)")
    general_target = parse_formula("∀ x0: child_0(parent_id=x0) ⇒ parent_0(id=x0)")
    specific_rule = parse_formula("∀ x0, y0: child_0(parent_id=x0, id=y0) ⇒ parent_0(id=x0)")
    instance_target = parse_formula("∀ x0, y0: child_0(parent_id=x0, id=y0) ⇒ parent_0(id=x0)")

    evaluator = SQLiteRuleEvaluator(db_path)
    try:
        assert alpha_equivalent(competitor, alpha_target)
        assert subsumes(general_target, specific_rule)
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
    assert any(record.scope_status == ScopeStatus.OUTSIDE_FK_JOINABILITY for record in records)
    assert any(record.reason == "existential_or_head_only_variable" for record in records)
    assert any(record.claim_relevant and record.coverage_alpha for record in records)
    assert (output_dir / "audit_summary.json").exists()
    assert (output_dir / "audit_rules.csv").exists()
    assert (output_dir / "audit_diagnosis.md").exists()
    assert (output_dir / "audit_claims.md").exists()

    summary = json.loads((output_dir / "audit_summary.json").read_text(encoding="utf-8"))
    assert summary["coverage_mode"] == "alpha"
    assert "in_target_class" in summary["by_scope_status"]
    funnel = summary["funnel_by_algorithm"]["MATILDA"]
    assert funnel["total"] == 4
    assert funnel["parseable"] == 4
    assert funnel["within_scope"] == 2
    assert funnel["non_vacuous"] == 2
    assert funnel["above_threshold"] == 1
    assert funnel["exactly_recovered"] == 1
    assert funnel["covered_by_more_general_rule"] == 0

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
    _write_results(results_dir, "AMIE3", "tiny", ["parent(x0, y0) => child(x0, y0)"])

    records = run_audit(
        AuditConfig(
            results_dir=results_dir,
            database_dir=database_dir,
            output_dir=output_dir,
            competitors=("AMIE3",),
            show_progress=False,
        )
    )
    assert records == []

    included = run_audit(
        AuditConfig(
            results_dir=results_dir,
            database_dir=database_dir,
            output_dir=output_dir,
            competitors=("AMIE3",),
            include_amie_rdf=True,
            show_progress=False,
            reset_state=True,
        )
    )
    assert len(included) == 1
    assert included[0].scope_status == ScopeStatus.UNSUPPORTED_REPRESENTATION


def test_audit_excludes_competitor_rules_when_target_run_failed(tmp_path: Path) -> None:
    database_dir = tmp_path / "data"
    results_dir = tmp_path / "results"
    output_dir = tmp_path / "audit"
    database_dir.mkdir()
    _write_tiny_database(database_dir, "tiny.db")
    _write_results(results_dir, "MAHILDA", "tiny", ["∀ x0: child_0(parent_id=x0) ⇒ parent_0(id=x0)"])
    _write_results(results_dir, "MATILDA", "tiny", ["∀ x0: child_0(parent_id=x0) ⇒ parent_0(id=x0)"])
    progress_dir = results_dir / "progress"
    progress_dir.mkdir()
    (progress_dir / "MAHILDA_tiny.db.json").write_text(
        json.dumps({"algorithm": "MAHILDA", "database": "tiny.db", "status": "timeout"}),
        encoding="utf-8",
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

    assert records == []


def test_audit_excludes_partial_target_result_from_summary_status(tmp_path: Path) -> None:
    database_dir = tmp_path / "data"
    results_dir = tmp_path / "results"
    output_dir = tmp_path / "audit"
    database_dir.mkdir()
    _write_tiny_database(database_dir, "tiny.db")
    _write_results(results_dir, "MAHILDA", "tiny", ["∀ x0: child_0(parent_id=x0) ⇒ parent_0(id=x0)"])
    _write_results(results_dir, "MATILDA", "tiny", ["∀ x0: child_0(parent_id=x0) ⇒ parent_0(id=x0)"])
    (results_dir / "summary.json").write_text(
        json.dumps(
            {
                "runs": [
                    {
                        "algorithm": "MAHILDA",
                        "database": "tiny.db",
                        "status": "success",
                        "rules_count": 2,
                    }
                ]
            }
        ),
        encoding="utf-8",
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

    assert records == []


def test_cli_audit_merges_settings_and_overrides(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_cli_audit_reads_distributed_settings_and_auto_host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, AuditConfig] = {}
    settings_path = tmp_path / "settings.yaml"
    settings_path.write_text(
        "hosts: [local]\n"
        "audit:\n"
        "  workers_per_host: 3\n"
        "  heartbeat_seconds: 7\n"
        "  stale_after_seconds: 99\n"
        "  max_attempts: 4\n",
        encoding="utf-8",
    )

    def fake_run_audit(config: AuditConfig) -> list[object]:
        captured["config"] = config
        return []

    monkeypatch.setattr("mahilda.cli.audit.run_audit", fake_run_audit)
    monkeypatch.setattr("mahilda.cli.audit.socket.gethostname", lambda: "local.example")

    assert (
        audit_main(
            [
                "--settings",
                str(settings_path),
                "--host",
                "auto",
                "--output-dir",
                str(tmp_path / "audit"),
            ]
        )
        == 0
    )
    assert captured["config"].host == "local"
    assert captured["config"].hosts == ("local",)
    assert captured["config"].workers == 3
    assert captured["config"].heartbeat_seconds == 7
    assert captured["config"].stale_after_seconds == 99
    assert captured["config"].max_attempts == 4


def test_status_only_run_does_not_create_audit_state(tmp_path: Path) -> None:
    database_dir, results_dir, output_dir = _setup_subsumption_fixture(tmp_path)

    run_audit(
        AuditConfig(
            results_dir=results_dir,
            database_dir=database_dir,
            output_dir=output_dir,
            competitors=("MATILDA",),
            status_only=True,
            hosts=("h0",),
            host="h0",
            show_progress=False,
        )
    )

    assert not (output_dir / ".audit_state").exists()


def test_alpha_coverage_skips_instance_matching(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database_dir, results_dir, output_dir = _setup_subsumption_fixture(tmp_path)

    def fail_if_called(*args: object, **kwargs: object) -> bool:
        raise AssertionError("covered_on_instance should not run in alpha mode")

    monkeypatch.setattr("mahilda.audit.runner.covered_on_instance", fail_if_called)
    records = run_audit(
        AuditConfig(
            results_dir=results_dir,
            database_dir=database_dir,
            output_dir=output_dir,
            competitors=("MATILDA",),
            coverage="alpha",
            show_progress=False,
        )
    )

    assert [record.match_status for record in records] == [MatchStatus.UNMATCHED]


def test_subsumption_coverage_skips_instance_matching(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database_dir, results_dir, output_dir = _setup_subsumption_fixture(tmp_path)

    def fail_if_called(*args: object, **kwargs: object) -> bool:
        raise AssertionError("covered_on_instance should not run in subsumption mode")

    monkeypatch.setattr("mahilda.audit.runner.covered_on_instance", fail_if_called)
    records = run_audit(
        AuditConfig(
            results_dir=results_dir,
            database_dir=database_dir,
            output_dir=output_dir,
            competitors=("MATILDA",),
            coverage="subsumption",
            show_progress=False,
        )
    )

    assert [record.match_status for record in records] == [MatchStatus.RECALLED_SUBSUMED]


def test_reuse_cache_rematches_without_sqlite_evaluation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database_dir, results_dir, output_dir = _setup_subsumption_fixture(tmp_path)
    run_audit(
        AuditConfig(
            results_dir=results_dir,
            database_dir=database_dir,
            output_dir=output_dir,
            competitors=("MATILDA",),
            coverage="alpha",
            show_progress=False,
        )
    )

    def fail_if_evaluated(*args: object, **kwargs: object) -> object:
        raise AssertionError("cached rematching must not query SQLite")

    monkeypatch.setattr(SQLiteRuleEvaluator, "evaluate", fail_if_evaluated)
    records = run_audit(
        AuditConfig(
            results_dir=results_dir,
            database_dir=database_dir,
            output_dir=output_dir,
            competitors=("MATILDA",),
            coverage="subsumption",
            show_progress=False,
            reuse_cache=True,
        )
    )

    assert [record.match_status for record in records] == [MatchStatus.RECALLED_SUBSUMED]


def test_reuse_legacy_cache_requires_explicit_opt_in(tmp_path: Path) -> None:
    database_dir, results_dir, output_dir = _setup_subsumption_fixture(tmp_path)
    run_audit(
        AuditConfig(
            results_dir=results_dir,
            database_dir=database_dir,
            output_dir=output_dir,
            competitors=("MATILDA",),
            show_progress=False,
        )
    )
    (output_dir / ".audit_cache").rename(output_dir / ".audit_cache_saved")

    with pytest.raises(SystemExit, match="No reusable audit cache found"):
        run_audit(
            AuditConfig(
                results_dir=results_dir,
                database_dir=database_dir,
                output_dir=output_dir,
                competitors=("MATILDA",),
                coverage="subsumption",
                show_progress=False,
                reuse_cache=True,
            )
        )

    records = run_audit(
        AuditConfig(
            results_dir=results_dir,
            database_dir=database_dir,
            output_dir=output_dir,
            competitors=("MATILDA",),
            coverage="subsumption",
            show_progress=False,
            reuse_cache=True,
            trust_legacy_cache=True,
        )
    )
    assert [record.match_status for record in records] == [MatchStatus.RECALLED_SUBSUMED]


def test_run_audit_reuses_one_evaluator_per_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database_dir, results_dir, output_dir = _setup_evaluator_reuse_fixture(tmp_path)
    init_calls: list[Path] = []
    original = SQLiteRuleEvaluator

    class CountingEvaluator(original):
        def __init__(self, database_path: Path, *, relation_disjoint: bool = True) -> None:
            init_calls.append(database_path)
            super().__init__(database_path, relation_disjoint=relation_disjoint)

    monkeypatch.setattr("mahilda.audit.runner.SQLiteRuleEvaluator", CountingEvaluator)
    records = run_audit(
        AuditConfig(
            results_dir=results_dir,
            database_dir=database_dir,
            output_dir=output_dir,
            competitors=("MATILDA",),
            show_progress=False,
        )
    )

    assert len(records) == 2
    assert init_calls == [database_dir / "tiny.db"]


def test_instance_coverage_caches_target_projected_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database_dir, results_dir, output_dir, target_rule = _setup_instance_fixture(tmp_path)
    counts: dict[str, int] = {}
    original = SQLiteRuleEvaluator.projected_head_rows
    target_key = target_rule.canonical_key()

    def counting_projected_head_rows(self: SQLiteRuleEvaluator, rule) -> set[tuple[object, ...]]:
        if rule.canonical_key() == target_key:
            counts[target_key] = counts.get(target_key, 0) + 1
        return original(self, rule)

    monkeypatch.setattr(SQLiteRuleEvaluator, "projected_head_rows", counting_projected_head_rows)
    records = run_audit(
        AuditConfig(
            results_dir=results_dir,
            database_dir=database_dir,
            output_dir=output_dir,
            competitors=("MATILDA",),
            coverage="instance",
            show_progress=False,
        )
    )

    assert [record.match_status for record in records] == [
        MatchStatus.COVERED_ON_INSTANCE,
        MatchStatus.COVERED_ON_INSTANCE,
    ]
    assert counts[target_key] == 1


def test_run_audit_writes_checkpoint_state_and_shards(tmp_path: Path) -> None:
    database_dir = tmp_path / "data"
    results_dir = tmp_path / "results"
    output_dir = tmp_path / "audit"
    database_dir.mkdir()
    _write_tiny_database(database_dir, "alpha.db")
    _write_tiny_database(database_dir, "beta.db")
    _write_results(results_dir, "MAHILDA", "alpha", ["∀ x0: child_0(parent_id=x0) ⇒ parent_0(id=x0)"])
    _write_results(results_dir, "MAHILDA", "beta", ["∀ x0: child_0(parent_id=x0) ⇒ parent_0(id=x0)"])
    _write_results(results_dir, "MATILDA", "alpha", ["∀ x0: child_0(parent_id=x0) ⇒ parent_0(id=x0)"])
    _write_results(results_dir, "MATILDA", "beta", ["∀ x0: child_0(parent_id=x0) ⇒ parent_0(id=x0)"])

    records = run_audit(
        AuditConfig(
            results_dir=results_dir,
            database_dir=database_dir,
            output_dir=output_dir,
            competitors=("MATILDA",),
            show_progress=False,
            workers=2,
        )
    )

    assert len(records) == 2
    state_dir = output_dir / ".audit_state"
    assert (state_dir / "manifest.json").exists()
    summary = json.loads((state_dir / "run_summary.json").read_text(encoding="utf-8"))
    assert summary["totals"]["completed"] == 2
    shard_state = json.loads((state_dir / "shards" / "MATILDA__alpha.json").read_text(encoding="utf-8"))
    assert shard_state["status"] == "completed"
    assert (output_dir / "shards" / "MATILDA" / "alpha" / "audit_rules.csv").exists()


def test_distributed_audit_processes_each_database_as_one_job(tmp_path: Path) -> None:
    database_dir = tmp_path / "data"
    results_dir = tmp_path / "results"
    output_dir = tmp_path / "audit"
    database_dir.mkdir()
    for database in ("alpha", "beta"):
        _write_tiny_database(database_dir, f"{database}.db")
        _write_results(
            results_dir,
            "MAHILDA",
            database,
            ["∀ x0: child_0(parent_id=x0) ⇒ parent_0(id=x0)"],
        )
        _write_results(
            results_dir,
            "MATILDA",
            database,
            ["∀ x0: child_0(parent_id=x0) ⇒ parent_0(id=x0)"],
        )

    records = run_audit(
        AuditConfig(
            results_dir=results_dir,
            database_dir=database_dir,
            output_dir=output_dir,
            competitors=("MATILDA",),
            show_progress=False,
            workers=2,
            hosts=("h0", "h1"),
            host="h0",
        )
    )

    assert len(records) == 2
    queue_dir = output_dir / ".audit_state" / "queue"
    assert len(list((queue_dir / "done").glob("*.json"))) == 2
    assert (output_dir / ".audit_state" / "finalized.json").exists()
    assert not (output_dir / ".audit_state" / "finalize.lock").exists()


def test_parallel_audit_disables_worker_progress_bars(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database_dir = tmp_path / "data"
    results_dir = tmp_path / "results"
    output_dir = tmp_path / "audit"
    database_dir.mkdir()
    _write_tiny_database(database_dir, "tiny.db")
    _write_results(results_dir, "MAHILDA", "tiny", ["∀ x0: child_0(parent_id=x0) ⇒ parent_0(id=x0)"])
    _write_results(results_dir, "MATILDA", "tiny", ["∀ x0: child_0(parent_id=x0) ⇒ parent_0(id=x0)"])

    progress_calls: list[dict[str, object]] = []

    class RecordingProgress:
        def __init__(self, iterable: object = None, **kwargs: object) -> None:
            self.iterable = iterable
            progress_calls.append(kwargs)
            self.n = int(kwargs.get("initial", 0))

        def __iter__(self) -> object:
            return iter(self.iterable or ())

        def update(self, count: int) -> None:
            self.n += count

        def close(self) -> None:
            pass

    monkeypatch.setattr("mahilda.audit.runner.tqdm", RecordingProgress)
    run_audit(
        AuditConfig(
            results_dir=results_dir,
            database_dir=database_dir,
            output_dir=output_dir,
            competitors=("MATILDA",),
            show_progress=True,
            workers=2,
        )
    )

    assert len(progress_calls) == 1
    assert progress_calls[0]["desc"] == "Auditing"


def test_run_audit_requires_resume_when_state_exists(tmp_path: Path) -> None:
    database_dir, results_dir, output_dir = _setup_evaluator_reuse_fixture(tmp_path)
    run_audit(
        AuditConfig(
            results_dir=results_dir,
            database_dir=database_dir,
            output_dir=output_dir,
            competitors=("MATILDA",),
            show_progress=False,
        )
    )

    with pytest.raises(SystemExit, match="Existing audit state found"):
        run_audit(
            AuditConfig(
                results_dir=results_dir,
                database_dir=database_dir,
                output_dir=output_dir,
                competitors=("MATILDA",),
                show_progress=False,
            )
        )


def test_run_audit_resume_skips_completed_shards(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database_dir, results_dir, output_dir = _setup_evaluator_reuse_fixture(tmp_path)
    run_audit(
        AuditConfig(
            results_dir=results_dir,
            database_dir=database_dir,
            output_dir=output_dir,
            competitors=("MATILDA",),
            show_progress=False,
        )
    )

    def fail(*args: object, **kwargs: object) -> object:
        raise AssertionError("completed shard should not rerun on resume")

    monkeypatch.setattr("mahilda.audit.runner._run_audit_shard", fail)
    records = run_audit(
        AuditConfig(
            results_dir=results_dir,
            database_dir=database_dir,
            output_dir=output_dir,
            competitors=("MATILDA",),
            show_progress=False,
            resume=True,
        )
    )

    assert len(records) == 2


def _setup_subsumption_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
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
        ["∀ x0, y0: child_0(parent_id=x0, id=y0) ⇒ parent_0(id=x0)"],
    )
    return database_dir, results_dir, output_dir


def _setup_evaluator_reuse_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    database_dir = tmp_path / "data"
    results_dir = tmp_path / "results"
    output_dir = tmp_path / "audit"
    database_dir.mkdir()
    _write_tiny_database(database_dir, "tiny.db")
    _write_results(
        results_dir,
        "MAHILDA",
        "tiny",
        [
            "∀ x0: child_0(parent_id=x0) ⇒ parent_0(id=x0)",
            "∀ x0, y0: child_0(parent_id=x0, id=y0) ⇒ parent_0(id=x0)",
        ],
    )
    _write_results(
        results_dir,
        "MATILDA",
        "tiny",
        [
            "∀ x0: child_0(parent_id=x0) ⇒ parent_0(id=x0)",
            "∀ x0, y0: child_0(parent_id=x0, id=y0) ⇒ parent_0(id=x0)",
        ],
    )
    return database_dir, results_dir, output_dir


def _setup_instance_fixture(tmp_path: Path) -> tuple[Path, Path, Path, RelationalRule]:
    database_dir = tmp_path / "data"
    results_dir = tmp_path / "results"
    output_dir = tmp_path / "audit"
    database_dir.mkdir()
    _write_tiny_database(database_dir, "tiny.db")
    target_display = "∀ x0, y0: child_0(parent_id=x0, id=y0) ⇒ parent_0(id=x0)"
    target_rule = parse_formula(target_display)
    _write_results(results_dir, "MAHILDA", "tiny", [target_display])
    _write_results(
        results_dir,
        "MATILDA",
        "tiny",
        [
            "∀ x0: child_0(parent_id=x0) ⇒ parent_0(id=x0)",
            "∀ x0, z0: child_0(parent_id=x0, label=z0) ⇒ parent_0(id=x0)",
        ],
    )
    return database_dir, results_dir, output_dir, target_rule


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
