from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING

from tqdm import tqdm

from mahilda.audit.evaluator import AuditEvaluationError, SQLiteRuleEvaluator
from mahilda.audit.models import (
    AuditClassification,
    AuditRecord,
    MatchStatus,
    ParsedRule,
    RelationalRule,
)
from mahilda.audit.parsing import load_source_rules, parse_source_rule

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True)
class AuditConfig:
    results_dir: Path
    database_dir: Path
    output_dir: Path
    target: str = "MAHILDA"
    competitors: tuple[str, ...] = ("AMIE3", "MATILDA", "SPIDER", "POPPER")
    confidence_threshold: float = 1.0
    max_examples: int = 25
    strict: bool = False
    show_progress: bool = True


def run_audit(config: AuditConfig) -> list[AuditRecord]:
    config.output_dir.mkdir(parents=True, exist_ok=True)
    target_rules_by_db = _load_target_rules(config)
    records: list[AuditRecord] = []

    for algorithm in config.competitors:
        sources = load_source_rules(config.results_dir, algorithm)
        iterator = tqdm(
            sources,
            desc=f"Auditing {algorithm}",
            disable=not config.show_progress,
            unit="rule",
        )
        for source in iterator:
            parsed = parse_source_rule(source)
            records.append(_audit_rule(config, parsed, target_rules_by_db.get(parsed.source.database, {})))

    _write_outputs(config, records)
    if config.strict and any(record.match_status == MatchStatus.UNMATCHED for record in records):
        raise SystemExit(2)
    return records


def _load_target_rules(config: AuditConfig) -> dict[str, dict[str, RelationalRule]]:
    by_database: dict[str, dict[str, RelationalRule]] = defaultdict(dict)
    for source in load_source_rules(config.results_dir, config.target):
        parsed = parse_source_rule(source)
        if parsed.rule is not None:
            by_database[source.database][parsed.rule.canonical_key()] = parsed.rule
    return dict(by_database)


def _audit_rule(
    config: AuditConfig,
    parsed: ParsedRule,
    target_rules: dict[str, RelationalRule],
) -> AuditRecord:
    source = parsed.source
    if parsed.rule is None:
        classification = (
            AuditClassification.OUT_OF_SCOPE
            if parsed.unsupported_reason and parsed.unsupported_reason.endswith("_rule")
            else AuditClassification.PARSE_FAILED
        )
        return _record(
            parsed=parsed,
            classification=classification,
            match_status=MatchStatus.NOT_APPLICABLE,
            reason=parsed.unsupported_reason or "parse_failed",
        )

    rule = parsed.rule
    scope_reason = _scope_reason(rule)
    if scope_reason is not None:
        return _record(
            parsed=parsed,
            classification=AuditClassification.OUT_OF_SCOPE,
            match_status=MatchStatus.NOT_APPLICABLE,
            reason=scope_reason,
        )

    database_path = config.database_dir / f"{source.database}.db"
    if not database_path.exists():
        return _record(
            parsed=parsed,
            classification=AuditClassification.PARSE_FAILED,
            match_status=MatchStatus.NOT_APPLICABLE,
            reason=f"missing_database:{database_path}",
        )

    evaluator = SQLiteRuleEvaluator(database_path)
    try:
        if not evaluator.is_fk_joinable(rule):
            return _record(
                parsed=parsed,
                classification=AuditClassification.OUT_OF_SCOPE,
                match_status=MatchStatus.NOT_APPLICABLE,
                reason="not_foreign_key_joinable",
            )
        if _has_head_atom_in_body(rule) or evaluator.is_relation_disjoint_vacuous(rule):
            return _record(
                parsed=parsed,
                classification=AuditClassification.VACUOUS,
                match_status=MatchStatus.NOT_APPLICABLE,
                reason="vacuous_under_relation_disjoint_semantics",
            )
        evaluation = evaluator.evaluate(rule)
    except AuditEvaluationError as exc:
        return _record(
            parsed=parsed,
            classification=AuditClassification.OUT_OF_SCOPE,
            match_status=MatchStatus.NOT_APPLICABLE,
            reason=str(exc),
        )
    finally:
        evaluator.close()

    if evaluation.confidence < config.confidence_threshold:
        return _record(
            parsed=parsed,
            classification=AuditClassification.APPROXIMATE,
            match_status=MatchStatus.NOT_APPLICABLE,
            reason="confidence_below_threshold",
            support=evaluation.support,
            predictions=evaluation.predictions,
            confidence=evaluation.confidence,
        )

    match_status, matched_rule = _match_rule(rule, target_rules)
    return _record(
        parsed=parsed,
        classification=AuditClassification.COMPARABLE_TRUE,
        match_status=match_status,
        reason="matched" if match_status != MatchStatus.UNMATCHED else "no_matching_mahilda_rule",
        support=evaluation.support,
        predictions=evaluation.predictions,
        confidence=evaluation.confidence,
        matched_rule=matched_rule,
    )


def _scope_reason(rule: RelationalRule) -> str | None:
    if not rule.body:
        return "empty_body"
    if rule.head_variables() - rule.body_variables():
        return "existential_or_head_only_variable"
    return None


def _has_head_atom_in_body(rule: RelationalRule) -> bool:
    head = rule.head.without_occurrence()
    return any(atom.without_occurrence() == head for atom in rule.body)


def _match_rule(rule: RelationalRule, target_rules: dict[str, RelationalRule]) -> tuple[MatchStatus, str]:
    canonical_key = rule.canonical_key()
    if canonical_key in target_rules:
        return MatchStatus.RECALLED_ALPHA, canonical_key

    competitor_body = rule.canonical_body_atoms()
    competitor_head = rule.canonical_head_key()
    for target_key, target_rule in target_rules.items():
        if target_rule.canonical_head_key() != competitor_head:
            continue
        if target_rule.canonical_body_atoms().issubset(competitor_body):
            return MatchStatus.RECALLED_SUBSUMED, target_key
    return MatchStatus.UNMATCHED, ""


def _record(
    *,
    parsed: ParsedRule,
    classification: AuditClassification,
    match_status: MatchStatus,
    reason: str,
    support: int | None = None,
    predictions: int | None = None,
    confidence: float | None = None,
    matched_rule: str = "",
) -> AuditRecord:
    rule = parsed.rule
    return AuditRecord(
        algorithm=parsed.source.algorithm,
        database=parsed.source.database,
        source_path=str(parsed.source.source_path),
        rule_index=parsed.source.index,
        classification=classification,
        match_status=match_status,
        reason=reason,
        support=support,
        predictions=predictions,
        confidence=confidence,
        canonical_rule=rule.canonical_key() if rule is not None else "",
        matched_rule=matched_rule,
        display=parsed.source.display,
    )


def _write_outputs(config: AuditConfig, records: list[AuditRecord]) -> None:
    _write_rules_csv(config.output_dir / "audit_rules.csv", records)
    _write_summary_json(config.output_dir / "audit_summary.json", records)
    _write_unmatched_markdown(config.output_dir / "audit_unmatched.md", records, config.max_examples)
    _write_claims_markdown(config.output_dir / "audit_claims.md", records)


def _write_rules_csv(path: Path, records: list[AuditRecord]) -> None:
    fieldnames = list(AuditRecord.__dataclass_fields__.keys())
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for record in records:
            writer.writerow({field: getattr(record, field) for field in fieldnames})


def _write_summary_json(path: Path, records: list[AuditRecord]) -> None:
    by_algorithm: dict[str, Counter[str]] = defaultdict(Counter)
    by_database: dict[str, Counter[str]] = defaultdict(Counter)
    for record in records:
        by_algorithm[record.algorithm][record.classification.value] += 1
        by_algorithm[record.algorithm][record.match_status.value] += 1
        by_database[record.database][record.classification.value] += 1

    payload = {
        "totals": _totals(records),
        "by_algorithm": {algorithm: dict(counter) for algorithm, counter in sorted(by_algorithm.items())},
        "by_database": {database: dict(counter) for database, counter in sorted(by_database.items())},
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _totals(records: list[AuditRecord]) -> dict[str, int | float]:
    classification_counts = Counter(record.classification for record in records)
    comparable = classification_counts[AuditClassification.COMPARABLE_TRUE]
    alpha = sum(record.match_status == MatchStatus.RECALLED_ALPHA for record in records)
    alpha_or_subsumed = sum(
        record.match_status in {MatchStatus.RECALLED_ALPHA, MatchStatus.RECALLED_SUBSUMED} for record in records
    )
    unmatched = sum(record.match_status == MatchStatus.UNMATCHED for record in records)
    return {
        "audited_rules": len(records),
        "comparable_true": comparable,
        "recalled_alpha": alpha,
        "recalled_alpha_or_subsumed": alpha_or_subsumed,
        "unmatched": unmatched,
        "alpha_recall": alpha / comparable if comparable else 0.0,
        "alpha_or_subsumed_recall": alpha_or_subsumed / comparable if comparable else 0.0,
    }


def _write_unmatched_markdown(path: Path, records: list[AuditRecord], max_examples: int) -> None:
    unmatched = [record for record in records if record.match_status == MatchStatus.UNMATCHED]
    lines = ["# Unmatched Comparable True Rules", ""]
    if not unmatched:
        lines.append("No unmatched comparable true rules were found.")
    else:
        lines.append(f"Showing up to {max_examples} unmatched rules.")
        lines.append("")
        for record in unmatched[:max_examples]:
            lines.extend(
                [
                    f"## {record.algorithm} / {record.database} / rule {record.rule_index}",
                    "",
                    f"- Support: {record.support}",
                    f"- Predictions: {record.predictions}",
                    f"- Confidence: {record.confidence}",
                    f"- Canonical: `{record.canonical_rule}`",
                    "",
                    "```text",
                    record.display,
                    "```",
                    "",
                ]
            )
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_claims_markdown(path: Path, records: list[AuditRecord]) -> None:
    totals = _totals(records)
    comparable = int(totals["comparable_true"])
    alpha = int(totals["recalled_alpha"])
    alpha_or_subsumed = int(totals["recalled_alpha_or_subsumed"])
    unmatched = int(totals["unmatched"])

    lines = [
        "# Audit Claims",
        "",
        "This audit supports only formal, closed-world, instance-level claims.",
        "",
        "## Computed Counts",
        "",
        f"- Audited competitor rules: {totals['audited_rules']}",
        f"- Comparable true rules: {comparable}",
        f"- Recalled by alpha-equivalence: {alpha}",
        f"- Recalled by alpha-equivalence or subsumption: {alpha_or_subsumed}",
        f"- Unmatched comparable true rules: {unmatched}",
        "",
        "## Permitted Claim",
        "",
    ]
    if comparable and unmatched == 0:
        lines.append(
            "After excluding approximate, vacuous, unparseable, and out-of-scope rules, "
            "MAHILDA recovered 100% of the remaining comparable true competitor rules "
            "under the reported audit criterion."
        )
    elif comparable:
        lines.append(
            "The current audited artifacts do not support a 100% recall claim because at least one "
            "comparable true competitor rule is unmatched."
        )
    else:
        lines.append("No comparable true competitor rules were found, so recall is not meaningful.")
    lines.extend(
        [
            "",
            "## Not Supported",
            "",
            "- The audit does not prove that MAHILDA finds all true rules in the database.",
            "- The audit does not judge whether a true rule is useful or interesting.",
            "- The audit does not support calling competitor rules incorrect without the formal category counts.",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")
