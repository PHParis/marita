from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from tqdm import tqdm

from mahilda.audit.evaluator import AuditEvaluationError, SQLiteRuleEvaluator
from mahilda.audit.matching import covered_on_instance, subsumes
from mahilda.audit.models import (
    AuditClassification,
    AuditRecord,
    MatchStatus,
    ParsedRule,
    RelationalRule,
    ScopeStatus,
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
    walk_length: int = 3
    max_tables: int = 3
    max_variables: int = 3
    disjoint_semantics: bool = True
    joinability: str = "fk"
    coverage: str = "alpha"
    diagnose_unmatched: bool = True
    include_amie_rdf: bool = False


@dataclass(frozen=True)
class TargetRuleIndex:
    rules: list[RelationalRule]
    rules_by_canonical_key: dict[str, RelationalRule]
    rules_by_head_key: dict[str, list[RelationalRule]]


@dataclass
class AuditRuntime:
    target_indexes_by_db: dict[str, TargetRuleIndex]
    evaluator_cache: dict[str, SQLiteRuleEvaluator] = field(default_factory=dict)
    projected_head_rows_cache: dict[str, dict[str, set[tuple[object, ...]]]] = field(
        default_factory=lambda: defaultdict(dict)
    )

    def get_evaluator(self, database: str, database_path: Path, *, disjoint_semantics: bool) -> SQLiteRuleEvaluator:
        evaluator = self.evaluator_cache.get(database)
        if evaluator is None:
            evaluator = SQLiteRuleEvaluator(database_path, relation_disjoint=disjoint_semantics)
            self.evaluator_cache[database] = evaluator
        return evaluator

    def close(self) -> None:
        while self.evaluator_cache:
            _, evaluator = self.evaluator_cache.popitem()
            evaluator.close()


def run_audit(config: AuditConfig) -> list[AuditRecord]:
    config.output_dir.mkdir(parents=True, exist_ok=True)
    runtime = AuditRuntime(target_indexes_by_db=_load_target_rule_indexes(config))
    records: list[AuditRecord] = []

    try:
        for algorithm in config.competitors:
            if algorithm == "AMIE3" and not config.include_amie_rdf:
                continue
            sources = load_source_rules(config.results_dir, algorithm)
            iterator = tqdm(
                sources,
                desc=f"Auditing {algorithm}",
                disable=not config.show_progress,
                unit="rule",
            )
            for source in iterator:
                parsed = parse_source_rule(source)
                target_index = runtime.target_indexes_by_db.get(source.database, _empty_target_rule_index())
                records.append(_audit_rule(config, runtime, parsed, target_index))
    finally:
        runtime.close()

    _write_outputs(config, records)
    if config.strict and any(record.match_status == MatchStatus.UNMATCHED for record in records):
        raise SystemExit(2)
    return records


def _empty_target_rule_index() -> TargetRuleIndex:
    return TargetRuleIndex(rules=[], rules_by_canonical_key={}, rules_by_head_key={})


def _load_target_rule_indexes(config: AuditConfig) -> dict[str, TargetRuleIndex]:
    by_database: dict[str, list[RelationalRule]] = defaultdict(list)
    for source in load_source_rules(config.results_dir, config.target):
        parsed = parse_source_rule(source)
        if parsed.rule is not None:
            by_database[source.database].append(parsed.rule)

    indexes: dict[str, TargetRuleIndex] = {}
    for database, rules in by_database.items():
        rules_by_canonical_key: dict[str, RelationalRule] = {}
        rules_by_head_key: dict[str, list[RelationalRule]] = defaultdict(list)
        for rule in rules:
            canonical_key = rule.canonical_key()
            rules_by_canonical_key.setdefault(canonical_key, rule)
            rules_by_head_key[rule.canonical_head_key()].append(rule)
        indexes[database] = TargetRuleIndex(
            rules=rules,
            rules_by_canonical_key=rules_by_canonical_key,
            rules_by_head_key=dict(rules_by_head_key),
        )
    return indexes


def _audit_rule(
    config: AuditConfig,
    runtime: AuditRuntime,
    parsed: ParsedRule,
    target_index: TargetRuleIndex,
) -> AuditRecord:
    source = parsed.source
    rule = parsed.rule
    if rule is None:
        unsupported = bool(parsed.unsupported_reason and parsed.unsupported_reason.endswith("_rule"))
        classification = AuditClassification.OUT_OF_SCOPE if unsupported else AuditClassification.PARSE_FAILED
        scope_status = ScopeStatus.UNSUPPORTED_REPRESENTATION if unsupported else ScopeStatus.NOT_PARSED
        reason = parsed.unsupported_reason or "parse_failed"
        return _record(
            parsed=parsed,
            classification=classification,
            match_status=MatchStatus.NOT_APPLICABLE,
            scope_status=scope_status,
            scope_reason=reason,
            diagnosis=scope_status.value,
            reason=reason,
        )

    scope_status, scope_reason = _static_scope_status(config, rule)
    if scope_status != ScopeStatus.IN_TARGET_CLASS:
        return _record(
            parsed=parsed,
            classification=AuditClassification.OUT_OF_SCOPE,
            match_status=MatchStatus.NOT_APPLICABLE,
            scope_status=scope_status,
            scope_reason=scope_reason,
            diagnosis=scope_status.value,
            reason=scope_reason,
        )

    database_path = config.database_dir / f"{source.database}.db"
    if not database_path.exists():
        reason = f"missing_database:{database_path}"
        return _record(
            parsed=parsed,
            classification=AuditClassification.PARSE_FAILED,
            match_status=MatchStatus.NOT_APPLICABLE,
            scope_status=ScopeStatus.MISSING_DATABASE,
            scope_reason=reason,
            diagnosis=ScopeStatus.MISSING_DATABASE.value,
            reason=reason,
        )

    evaluator = runtime.get_evaluator(
        source.database,
        database_path,
        disjoint_semantics=config.disjoint_semantics,
    )
    try:
        if config.joinability == "fk" and not evaluator.is_fk_joinable(rule):
            return _record(
                parsed=parsed,
                classification=AuditClassification.OUT_OF_SCOPE,
                match_status=MatchStatus.NOT_APPLICABLE,
                scope_status=ScopeStatus.OUTSIDE_FK_JOINABILITY,
                scope_reason="not_foreign_key_joinable",
                diagnosis=ScopeStatus.OUTSIDE_FK_JOINABILITY.value,
                reason="not_foreign_key_joinable",
            )
        if _has_head_atom_in_body(rule) or (config.disjoint_semantics and evaluator.is_relation_disjoint_vacuous(rule)):
            return _record(
                parsed=parsed,
                classification=AuditClassification.VACUOUS,
                match_status=MatchStatus.NOT_APPLICABLE,
                scope_status=ScopeStatus.OUTSIDE_RELATION_DISJOINTNESS,
                scope_reason="vacuous_under_relation_disjoint_semantics",
                diagnosis=ScopeStatus.OUTSIDE_RELATION_DISJOINTNESS.value,
                reason="vacuous_under_relation_disjoint_semantics",
            )
        evaluation = evaluator.evaluate(rule)
    except AuditEvaluationError as exc:
        reason = str(exc)
        scope_status = (
            ScopeStatus.MISSING_PK if reason.startswith("missing_primary_key") else ScopeStatus.EVALUATOR_ERROR
        )
        return _record(
            parsed=parsed,
            classification=AuditClassification.OUT_OF_SCOPE,
            match_status=MatchStatus.NOT_APPLICABLE,
            scope_status=scope_status,
            scope_reason=reason,
            diagnosis=scope_status.value,
            reason=reason,
        )

    if evaluation.confidence < config.confidence_threshold:
        return _record(
            parsed=parsed,
            classification=AuditClassification.APPROXIMATE,
            match_status=MatchStatus.NOT_APPLICABLE,
            scope_status=ScopeStatus.IN_TARGET_CLASS,
            scope_reason=ScopeStatus.IN_TARGET_CLASS.value,
            diagnosis=AuditClassification.APPROXIMATE.value,
            reason="confidence_below_threshold",
            support=evaluation.support,
            predictions=evaluation.predictions,
            confidence=evaluation.confidence,
        )

    match_status, matched_rule = _match_rule(
        evaluator=evaluator,
        rule=rule,
        coverage=config.coverage,
        target_index=target_index,
        projected_head_rows_cache=runtime.projected_head_rows_cache[source.database],
    )
    return _record(
        parsed=parsed,
        classification=AuditClassification.COMPARABLE_TRUE,
        match_status=match_status,
        scope_status=ScopeStatus.IN_TARGET_CLASS,
        scope_reason=ScopeStatus.IN_TARGET_CLASS.value,
        diagnosis=_diagnosis_for_match(match_status),
        reason="matched" if match_status != MatchStatus.UNMATCHED else "unmatched",
        support=evaluation.support,
        predictions=evaluation.predictions,
        confidence=evaluation.confidence,
        matched_rule=matched_rule,
    )


def _static_scope_status(config: AuditConfig, rule: RelationalRule) -> tuple[ScopeStatus, str]:
    if not rule.body:
        return ScopeStatus.EMPTY_BODY, ScopeStatus.EMPTY_BODY.value
    if rule.head_variables() - rule.body_variables():
        return ScopeStatus.HEAD_ONLY_VARIABLE, "existential_or_head_only_variable"

    total_atoms = len(rule.body) + 1
    if total_atoms > config.walk_length:
        return ScopeStatus.OUTSIDE_BOUNDS, f"outside_walk_length:{total_atoms}>{config.walk_length}"

    tables = {atom.table for atom in rule.all_atoms()}
    if len(tables) > config.max_tables:
        return ScopeStatus.OUTSIDE_BOUNDS, f"outside_max_tables:{len(tables)}>{config.max_tables}"

    variables = rule.body_variables() | rule.head_variables()
    if len(variables) > config.max_variables:
        return ScopeStatus.OUTSIDE_BOUNDS, f"outside_max_variables:{len(variables)}>{config.max_variables}"

    return ScopeStatus.IN_TARGET_CLASS, ScopeStatus.IN_TARGET_CLASS.value


def _has_head_atom_in_body(rule: RelationalRule) -> bool:
    head = rule.head.without_occurrence()
    return any(atom.without_occurrence() == head for atom in rule.body)


def _match_rule(
    *,
    evaluator: SQLiteRuleEvaluator,
    rule: RelationalRule,
    coverage: str,
    target_index: TargetRuleIndex,
    projected_head_rows_cache: dict[str, set[tuple[object, ...]]],
) -> tuple[MatchStatus, str]:
    canonical_key = rule.canonical_key()
    alpha_match = target_index.rules_by_canonical_key.get(canonical_key)
    if alpha_match is not None:
        return MatchStatus.RECALLED_ALPHA, alpha_match.canonical_key()
    if coverage == "alpha":
        return MatchStatus.UNMATCHED, ""

    head_candidates = target_index.rules_by_head_key.get(rule.canonical_head_key(), [])
    for target_rule in head_candidates:
        if subsumes(target_rule, rule):
            return MatchStatus.RECALLED_SUBSUMED, target_rule.canonical_key()
    if coverage != "instance":
        return MatchStatus.UNMATCHED, ""

    if covered_on_instance(
        evaluator,
        rule,
        head_candidates,
        projected_head_rows_cache=projected_head_rows_cache,
    ):
        return MatchStatus.COVERED_ON_INSTANCE, "finite_instance_coverage"
    return MatchStatus.UNMATCHED, ""


def _diagnosis_for_match(match_status: MatchStatus) -> str:
    if match_status == MatchStatus.RECALLED_ALPHA:
        return "covered_by_alpha_equivalence"
    if match_status == MatchStatus.RECALLED_SUBSUMED:
        return "covered_by_logical_subsumption"
    if match_status == MatchStatus.COVERED_ON_INSTANCE:
        return "covered_on_finite_instance"
    return "claim_relevant_uncovered"


def _record(
    *,
    parsed: ParsedRule,
    classification: AuditClassification,
    match_status: MatchStatus,
    scope_status: ScopeStatus,
    scope_reason: str,
    diagnosis: str,
    reason: str,
    support: int | None = None,
    predictions: int | None = None,
    confidence: float | None = None,
    matched_rule: str = "",
) -> AuditRecord:
    rule = parsed.rule
    coverage_alpha = match_status == MatchStatus.RECALLED_ALPHA
    coverage_subsumption = match_status in {MatchStatus.RECALLED_ALPHA, MatchStatus.RECALLED_SUBSUMED}
    coverage_instance = match_status in {
        MatchStatus.RECALLED_ALPHA,
        MatchStatus.RECALLED_SUBSUMED,
        MatchStatus.COVERED_ON_INSTANCE,
    }
    target_class_member = scope_status == ScopeStatus.IN_TARGET_CLASS
    claim_relevant = classification == AuditClassification.COMPARABLE_TRUE
    return AuditRecord(
        algorithm=parsed.source.algorithm,
        database=parsed.source.database,
        source_path=str(parsed.source.source_path),
        rule_index=parsed.source.index,
        classification=classification,
        match_status=match_status,
        scope_status=scope_status,
        scope_reason=scope_reason,
        target_class_member=target_class_member,
        coverage_alpha=coverage_alpha,
        coverage_subsumption=coverage_subsumption,
        coverage_instance=coverage_instance,
        diagnosis=diagnosis,
        claim_relevant=claim_relevant,
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
    _write_summary_json(config.output_dir / "audit_summary.json", records, config)
    _write_unmatched_markdown(config.output_dir / "audit_unmatched.md", records, config.max_examples)
    _write_diagnosis_markdown(
        config.output_dir / "audit_diagnosis.md",
        records,
        config.max_examples,
        diagnose_unmatched=config.diagnose_unmatched,
    )
    _write_claims_markdown(config.output_dir / "audit_claims.md", records, config)


def _write_rules_csv(path: Path, records: list[AuditRecord]) -> None:
    fieldnames = list(AuditRecord.__dataclass_fields__.keys())
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for record in records:
            writer.writerow({field: getattr(record, field) for field in fieldnames})


def _write_summary_json(path: Path, records: list[AuditRecord], config: AuditConfig) -> None:
    by_algorithm: dict[str, Counter[str]] = defaultdict(Counter)
    by_database: dict[str, Counter[str]] = defaultdict(Counter)
    by_scope_status = Counter(record.scope_status.value for record in records)
    by_diagnosis = Counter(record.diagnosis for record in records)
    for record in records:
        by_algorithm[record.algorithm][record.classification.value] += 1
        by_algorithm[record.algorithm][record.match_status.value] += 1
        by_algorithm[record.algorithm][record.scope_status.value] += 1
        by_algorithm[record.algorithm][record.diagnosis] += 1
        by_database[record.database][record.classification.value] += 1
        by_database[record.database][record.diagnosis] += 1

    payload = {
        "coverage_mode": config.coverage,
        "totals": _totals(records),
        "by_scope_status": dict(sorted(by_scope_status.items())),
        "by_diagnosis": dict(sorted(by_diagnosis.items())),
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
    alpha_subsumed_or_instance = sum(
        record.match_status
        in {MatchStatus.RECALLED_ALPHA, MatchStatus.RECALLED_SUBSUMED, MatchStatus.COVERED_ON_INSTANCE}
        for record in records
    )
    unmatched = sum(record.match_status == MatchStatus.UNMATCHED for record in records)
    alpha_uncovered = comparable - alpha
    subsumption_uncovered = comparable - alpha_or_subsumed
    instance_uncovered = comparable - alpha_subsumed_or_instance
    return {
        "audited_rules": len(records),
        "comparable_true": comparable,
        "alpha_claim_denominator": comparable,
        "subsumption_claim_denominator": comparable,
        "instance_claim_denominator": comparable,
        "recalled_alpha": alpha,
        "recalled_alpha_or_subsumed": alpha_or_subsumed,
        "recalled_alpha_subsumed_or_instance": alpha_subsumed_or_instance,
        "unmatched": unmatched,
        "claim_relevant_rules": comparable,
        "claim_relevant_uncovered": alpha_uncovered,
        "subsumption_uncovered": subsumption_uncovered,
        "instance_uncovered": instance_uncovered,
        "alpha_recall": alpha / comparable if comparable else 0.0,
        "alpha_or_subsumed_recall": alpha_or_subsumed / comparable if comparable else 0.0,
        "alpha_subsumed_or_instance_recall": alpha_subsumed_or_instance / comparable if comparable else 0.0,
    }


def _write_unmatched_markdown(path: Path, records: list[AuditRecord], max_examples: int) -> None:
    unmatched = [record for record in records if record.match_status == MatchStatus.UNMATCHED]
    lines = ["# Unmatched Comparable True Rules", ""]
    if not unmatched:
        lines.append("No unmatched comparable true rules found.")
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


def _write_diagnosis_markdown(
    path: Path,
    records: list[AuditRecord],
    max_examples: int,
    *,
    diagnose_unmatched: bool,
) -> None:
    diagnosis_counts = Counter(record.diagnosis for record in records)
    scope_counts = Counter(record.scope_status.value for record in records)
    alpha_uncovered = [record for record in records if record.claim_relevant and not record.coverage_alpha]
    lines = [
        "# Audit Diagnosis",
        "",
        "This report explains why rules are or are not usable in the MAHILDA coverage claim.",
        "",
        "## Scope Status Counts",
        "",
    ]
    for status, count in scope_counts.most_common():
        lines.append(f"- {status}: {count}")
    lines.extend(["", "## Diagnosis Counts", ""])
    for diagnosis, count in diagnosis_counts.most_common():
        lines.append(f"- {diagnosis}: {count}")
    lines.extend(["", "## Claim-Relevant Uncovered Examples", ""])
    if not diagnose_unmatched:
        lines.append("Unmatched-rule diagnosis examples disabled with `--no-diagnose-unmatched`.")
    elif not alpha_uncovered:
        lines.append("No claim-relevant uncovered rules found under alpha-equivalence.")
    else:
        lines.append(f"Showing up to {max_examples} rules that block an alpha-equivalence 100% coverage claim.")
        lines.append("")
        for record in alpha_uncovered[:max_examples]:
            lines.extend(
                [
                    f"### {record.algorithm} / {record.database} / rule {record.rule_index}",
                    "",
                    f"- Diagnosis: {record.diagnosis}",
                    f"- Scope: {record.scope_status.value}",
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


def _write_claims_markdown(path: Path, records: list[AuditRecord], config: AuditConfig) -> None:
    totals = _totals(records)
    comparable = int(totals["comparable_true"])
    alpha = int(totals["recalled_alpha"])
    alpha_or_subsumed = int(totals["recalled_alpha_or_subsumed"])
    alpha_subsumed_or_instance = int(totals["recalled_alpha_subsumed_or_instance"])
    unmatched = int(totals["unmatched"])
    claim_relevant_uncovered = int(totals["claim_relevant_uncovered"])
    if config.coverage == "subsumption":
        selected_uncovered = int(totals["subsumption_uncovered"])
        selected_label = "alpha-equivalence or logical subsumption"
    elif config.coverage == "instance":
        selected_uncovered = int(totals["instance_uncovered"])
        selected_label = "alpha-equivalence, logical subsumption, or finite-instance coverage"
    else:
        selected_uncovered = claim_relevant_uncovered
        selected_label = "alpha-equivalence"

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
        f"- Recalled by alpha-equivalence, subsumption, or finite-instance coverage: {alpha_subsumed_or_instance}",
        f"- Unmatched comparable true rules: {unmatched}",
        f"- Claim-relevant uncovered rules under alpha-equivalence: {claim_relevant_uncovered}",
        f"- Selected coverage criterion: {selected_label}",
        f"- Uncovered rules under selected criterion: {selected_uncovered}",
        "",
        "## Permitted Claim",
        "",
    ]
    if comparable and selected_uncovered == 0:
        lines.append(
            "After excluding approximate, vacuous, unparseable, and out-of-scope rules, "
            f"MAHILDA recovered 100% of the remaining comparable true competitor rules under {selected_label}."
        )
    elif comparable:
        lines.append(
            f"The current audited artifacts do not support a 100% recall claim under {selected_label} because at least "
            "one comparable true competitor rule is uncovered."
        )
    else:
        lines.append("No comparable true competitor rules found, so recall is not meaningful.")
    lines.extend(
        [
            "",
            "## Not Supported",
            "",
            "- audit does not prove that MAHILDA finds all true rules in the database.",
            "- The audit does not judge whether a true rule is useful or interesting.",
            "- audit does not support calling competitor rules incorrect without formal category counts.",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")
