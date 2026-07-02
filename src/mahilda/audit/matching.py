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
        try:
            covered_rows.update(evaluator.projected_head_rows(target_rule))
        except AuditEvaluationError:
            continue
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
        if column not in specific_terms:
            return False
        specific_variable = specific_terms[column]
        mapped = mapping.get(general_variable)
        if mapped is None:
            mapping[general_variable] = specific_variable
        elif mapped != specific_variable:
            return False
    return True


def _same_head_shape(left: Atom, right: Atom) -> bool:
    return left.table == right.table and tuple(column for column, _ in left.terms) == tuple(
        column for column, _ in right.terms
    )
