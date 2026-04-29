import logging
import re
from collections import Counter

from mahilda.utils.rules import Predicate, PredicateUtils, TGDRule


class TGDRuleFactory:
    """Factory for creating TGDRule objects from ILP display strings."""

    @staticmethod
    def str_to_tgd(tgd_str: str, support: float, confidence: float) -> TGDRule:
        pattern = r"∀ (.*): (.*?) ⇒ (∃.*:)?(.*?)$"
        match = re.match(pattern, tgd_str)

        if match:
            variables_str, body_str, variables_head_str, head_str = match.groups()

            body_predicates = []
            for split in body_str.split(" ∧ "):
                body_pred = PredicateUtils.str_to_predicate(split)
                body_predicates.append(body_pred)
            body = tuple(body_predicates)

            head_predicates = []
            for split in head_str.split(" ∧ "):
                head_pred = PredicateUtils.str_to_predicate(split)
                head_predicates.append(head_pred)
            head = tuple(head_predicates)

            return TGDRule(body=body, head=head, display=tgd_str, accuracy=support, confidence=confidence)
        else:
            raise ValueError(f"Invalid TGD string format: {tgd_str}")

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
        match = re.match(r"(\w+)\(([^)]*)\)", relation_str.strip())
        if not match:
            raise ValueError(f"Invalid relation string: {relation_str}")
        relation, vars_str = match.groups()
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
