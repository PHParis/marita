from __future__ import annotations

from typing import TYPE_CHECKING

from mahilda.audit.evaluator import AuditEvaluationError, SQLiteRuleEvaluator

if TYPE_CHECKING:
    from mahilda.audit.models import Atom, RelationalRule


def alpha_equivalent(left: RelationalRule, right: RelationalRule) -> bool:
    return len(left.body) == len(right.body) and subsumes(left, right) and subsumes(right, left)


def subsumes(general: RelationalRule, specific: RelationalRule) -> bool:
    mapping: dict[str, str] = {}
    if not _extend_atom_mapping(general.head, specific.head, mapping):
        return False
    return _match_body_atoms(general.body, specific.body, mapping)


def covered_on_instance(
    evaluator: SQLiteRuleEvaluator,
    rule: RelationalRule,
    target_rules: list[RelationalRule],
    *,
    projected_head_rows_cache: dict[str, set[tuple[object, ...]]] | None = None,
) -> bool:
    try:
        expected_rows = evaluator.projected_head_rows(rule)
    except AuditEvaluationError:
        return False
    if not expected_rows:
        return False

    covered_rows: set[tuple[object, ...]] = set()
    for target_rule in target_rules:
        if not _same_head_shape(rule.head, target_rule.head):
            continue
        cache_key = target_rule.canonical_key()
        if projected_head_rows_cache is not None and cache_key in projected_head_rows_cache:
            target_rows = projected_head_rows_cache[cache_key]
        else:
            try:
                target_rows = evaluator.projected_head_rows(target_rule)
            except AuditEvaluationError:
                continue
            if projected_head_rows_cache is not None:
                projected_head_rows_cache[cache_key] = target_rows
        covered_rows.update(target_rows)
    return expected_rows.issubset(covered_rows)


def _match_body_atoms(
    general_atoms: tuple[Atom, ...],
    specific_atoms: tuple[Atom, ...],
    mapping: dict[str, str],
) -> bool:
    if not general_atoms:
        return True

    first, *rest = general_atoms
    for candidate in specific_atoms:
        candidate_mapping = dict(mapping)
        if not _extend_atom_mapping(first, candidate, candidate_mapping):
            continue
        if _match_body_atoms(tuple(rest), specific_atoms, candidate_mapping):
            mapping.update(candidate_mapping)
            return True
    return False


def _extend_atom_mapping(general: Atom, specific: Atom, mapping: dict[str, str]) -> bool:
    if general.table != specific.table:
        return False

    specific_terms = dict(specific.terms)
    for column, general_variable in general.terms:
        specific_variable = specific_terms.get(column)
        if specific_variable is None:
            return False
        existing = mapping.get(general_variable)
        if existing is None:
            mapping[general_variable] = specific_variable
        elif existing != specific_variable:
            return False
    return True


def _same_head_shape(left: Atom, right: Atom) -> bool:
    return left.table == right.table and tuple(column for column, _ in left.terms) == tuple(
        column for column, _ in right.terms
    )
