import logging
import re
from collections import Counter

from mahilda.utils.relation_names import (
    RelationAtom,
    internal_relation_name,
    parse_relation_atom,
    parse_relation_reference,
    split_conjuncts,
    split_implication,
    split_relation_atom,
)
from mahilda.utils.rules import Predicate, PredicateUtils, TGDRule


class TGDRuleFactory:
    """Factory for creating TGDRule objects from ILP display strings."""

    @staticmethod
    def str_to_tgd(tgd_str: str, support: float, confidence: float) -> TGDRule:
        try:
            body_str, head_str = split_implication(tgd_str)
        except ValueError as exc:
            raise ValueError(f"Invalid TGD string format: {tgd_str}") from exc
        if body_str.strip().startswith("∀") and ":" in body_str:
            body_str = body_str.split(":", maxsplit=1)[1]
        if head_str.strip().startswith("∃") and ":" in head_str:
            head_str = head_str.split(":", maxsplit=1)[1]

        body_atoms = split_conjuncts(body_str)
        head_atoms = split_conjuncts(head_str)
        parsed_atoms: list[RelationAtom | tuple[Predicate, ...]] = []
        for atom in (*body_atoms, *head_atoms):
            try:
                parsed_atoms.append(parse_relation_atom(atom))
            except ValueError as parse_error:
                try:
                    parsed_atoms.append((PredicateUtils.str_to_predicate(atom),))
                except ValueError as predicate_error:
                    raise ValueError(f"Invalid atom {atom!r} in TGD {tgd_str!r}: {parse_error}") from predicate_error

        logical_variables = {
            variable for parsed_atom in parsed_atoms for variable in TGDRuleFactory._atom_variables(parsed_atom)
        }
        row_variables = TGDRuleFactory._fresh_row_variables(len(parsed_atoms), logical_variables)
        predicates_by_atom = [
            TGDRuleFactory._atom_to_predicates(parsed_atom, row_variable)
            for parsed_atom, row_variable in zip(parsed_atoms, row_variables, strict=True)
        ]
        body = tuple(predicate for predicates in predicates_by_atom[: len(body_atoms)] for predicate in predicates)
        head = tuple(predicate for predicates in predicates_by_atom[len(body_atoms) :] for predicate in predicates)
        return TGDRule(body=body, head=head, display=tgd_str, accuracy=support, confidence=confidence)

    @staticmethod
    def _fresh_row_variables(count: int, reserved: set[str]) -> tuple[str, ...]:
        variables: list[str] = []
        candidate = 0
        while len(variables) < count:
            variable = f"__row_{candidate}"
            candidate += 1
            if variable in reserved:
                continue
            variables.append(variable)
        return tuple(variables)

    @staticmethod
    def _atom_variables(atom: RelationAtom | tuple[Predicate, ...]) -> set[str]:
        if isinstance(atom, RelationAtom):
            return {variable for _, variable in atom.terms}
        return {variable for predicate in atom for variable in predicate[:1] + predicate[2:]}

    @staticmethod
    def _atom_to_predicates(atom: RelationAtom | tuple[Predicate, ...], row_variable: str) -> tuple[Predicate, ...]:
        if not isinstance(atom, RelationAtom):
            return atom
        relation = internal_relation_name(atom.table, atom.occurrence, atom.has_occurrence)
        if len(atom.terms) == 1:
            column, variable = atom.terms[0]
            return (Predicate(variable1=column, relation=relation, variable2=variable),)
        return tuple(
            Predicate(
                variable1=row_variable,
                relation=f"{relation}___sep___{column}",
                variable2=variable,
            )
            for column, variable in atom.terms
        )

    @classmethod
    def create_from_ilp_display(cls, display: str, accuracy: float) -> TGDRule:
        factory = cls()
        head_str, body_str = factory._get_head_body(display)

        head_predicates = factory._create_predicates_from_relation(head_str)
        if not head_predicates:
            logging.warning(f"No head predicates extracted from: {head_str}")

        body_pattern = r"\b\w+\([^)]*\)"
        body_relations = re.findall(body_pattern, body_str)
        if not body_relations:
            logging.warning(f"No body relations extracted from: {body_str}")

        body_predicates = []
        for relation_str in body_relations:
            body_predicates.extend(factory._create_predicates_from_relation(relation_str))

        body_predicates = factory._filter_predicates(body_predicates, head_predicates)
        head_predicates = factory._filter_predicates(head_predicates, body_predicates)

        if not head_predicates:
            logging.warning("After filtering, no valid head predicates remain.")
        if not body_predicates:
            logging.warning("After filtering, no valid body predicates remain.")

        return TGDRule(
            body=tuple(body_predicates),
            head=tuple(head_predicates),
            display=display,
            accuracy=accuracy,
            confidence=float("nan"),
        )

    def _get_head_body(self, disp: str) -> tuple[str, str]:
        if ":-" not in disp:
            raise ValueError(f"Invalid rule display, expected ':-' in: {disp}")
        head_str, body_str = disp.split(":-")
        head_str = head_str.strip()
        body_str = body_str.strip()
        if body_str.endswith("."):
            body_str = body_str[:-1].strip()
        return head_str, body_str

    def _create_predicates_from_relation(self, relation_str: str) -> list[Predicate]:
        sep_relation_variable = "___sep___"
        relation, vars_str = split_relation_atom(relation_str.strip())
        table, occurrence, has_occurrence = parse_relation_reference(relation)
        relation = internal_relation_name(table, occurrence, has_occurrence)
        variables = [v.strip() for v in vars_str.split(",")]

        predicates = []
        for i, variable in enumerate(variables):
            column = f"column_{i}"
            predicates.append(
                Predicate(variable1="id", relation=relation + sep_relation_variable + column, variable2=variable)
            )
        return predicates

    def _filter_predicates(self, preds: list[Predicate], other_preds: list[Predicate]) -> list[Predicate]:
        variable_counts: Counter = Counter()

        for predicate in preds:
            variable_counts[predicate.variable1] += 1
            variable_counts[predicate.variable2] += 1
        for predicate in other_preds:
            variable_counts[predicate.variable1] += 1
            variable_counts[predicate.variable2] += 1

        return [
            predicate
            for predicate in preds
            if variable_counts[predicate.variable1] >= 2 and variable_counts[predicate.variable2] >= 2
        ]
