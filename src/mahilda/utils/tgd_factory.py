import logging
import re
from collections import Counter

from mahilda.utils.relation_names import (
    internal_relation_name,
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

        body = tuple(PredicateUtils.str_to_predicate(atom) for atom in split_conjuncts(body_str))
        head = tuple(PredicateUtils.str_to_predicate(atom) for atom in split_conjuncts(head_str))
        return TGDRule(body=body, head=head, display=tgd_str, accuracy=support, confidence=confidence)

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
