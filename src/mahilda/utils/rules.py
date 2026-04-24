import json
from dataclasses import asdict, dataclass
from typing import Dict, List, NamedTuple, Tuple, Union, Optional
import re
import logging
from collections import Counter


@dataclass(frozen=True)
class InclusionDependency:
    table_dependant: str
    columns_dependant: Tuple[str]
    table_referenced: str
    columns_referenced: Tuple[str]
    display: Optional[str] = None
    correct: Optional[bool] = None
    compatible: Optional[bool] = None
    accuracy: Optional[float] = None
    confidence: Optional[float] = None

    def export_to_json(self, filepath: str):
        with open(filepath, 'a+') as f:
            json.dump(asdict(self), f, indent=4)


@dataclass(frozen=True)
class FunctionalDependency:
    table: str
    determinant: Tuple[str, ...]
    dependent: str
    correct: Optional[bool] = None
    compatible: Optional[bool] = None

    def export_to_json(self, filepath: str):
        with open(filepath, "w") as f:
            json.dump(asdict(self), f, indent=4)


@dataclass(frozen=True)
class DCCondition:
    column_1: str
    operator: str
    value: Union[str, Tuple[str, str]]
    negation: bool = False

    def __str__(self):
        negation_str = "NOT " if self.negation else ""
        return f"{self.column_1} {negation_str}{self.operator} {self.value}"


@dataclass(frozen=True)
class DenialConstraint:
    table: str
    conditions: Tuple[DCCondition]
    correct: Optional[bool] = None
    compatible: Optional[bool] = None

    def export_to_json(self, filepath: str):
        with open(filepath, "a+") as f:
            json.dump(
                {
                    "table": self.table,
                    "conditions": [asdict(cond) for cond in self.conditions],
                    "correct": self.correct,
                    "compatible": self.compatible
                },
                f,
                indent=4,
            )


class Predicate(NamedTuple):
    variable1: str
    relation: str
    variable2: str


@dataclass(frozen=True)
class HornRule:
    body: Tuple[Predicate]
    head: Predicate
    display: str
    correct: Optional[bool] = None
    compatible: Optional[bool] = None

    def export_to_json(self, filepath: str):
        with open(filepath, 'a+') as f:
            json.dump({
                "body": [str(pred) for pred in self.body],
                "head": str(self.head),
                "display": self.display,
                "correct": self.correct,
                "compatible": self.compatible
            }, f, indent=4)

    def __eq__(self, other):
        list1 = list(self.body + (self.head,))
        if not isinstance(other, HornRule):
            if isinstance(other, TGDRule):
                list2 = list(other.body + other.head)
                return PredicateUtils.compare_lists(list1, list2)
            return NotImplemented
        list2 = list(other.body + (other.head,))
        return PredicateUtils.compare_lists(list1, list2)


@dataclass(frozen=True)
class TGDRule:
    body: Tuple[Predicate]
    head: Tuple[Predicate]
    display: str
    accuracy: float
    confidence: float
    correct: Optional[bool] = None
    compatible: Optional[bool] = None

    def export_to_json(self, filepath: str):
        with open(filepath, 'w') as f:
            json.dump({
                "body": [str(pred) for pred in self.body],
                "head": [str(pred) for pred in self.head],
                "display": self.display,
                "accuracy": self.accuracy,
                "confidence": self.confidence,
                "correct": self.correct,
                "compatible": self.compatible
            }, f, indent=4)

    def __eq__(self, other):
        list1 = list(self.body + self.head)
        if isinstance(other, TGDRule):
            list2 = list(other.body + other.head)
            return PredicateUtils.compare_lists(list1, list2)
        elif isinstance(other, HornRule):
            list2 = list(other.body + (other.head,))
            return PredicateUtils.compare_lists(list1, list2)
        else:
            return NotImplemented

    def __le__(self, other):
        if not isinstance(other, TGDRule):
            return NotImplemented
        self_length = len(self.body) + len(self.head)
        other_length = len(other.body) + len(other.head)
        return self_length <= other_length

    def __lt__(self, other):
        if not isinstance(other, TGDRule):
            return NotImplemented
        self_length = len(self.body) + len(self.head)
        other_length = len(other.body) + len(other.head)
        return self_length < other_length


Rule = Union[
    InclusionDependency, FunctionalDependency, DenialConstraint, HornRule, TGDRule
]


class PredicateUtils:
    @staticmethod
    def sort_and_rename_variables(lst: List[Predicate], skip: int = 0) -> List[Predicate]:
        try:
            lst.sort(key=lambda x: x.relation)
        except Exception as e:
            return lst

        variable_mapping = {}
        counter = 0

        for i in range(len(lst)):
            index_lst = i + skip
            if index_lst >= len(lst):
                index_lst = index_lst - len(lst)
            predicate = lst[index_lst]

            if predicate.variable1 not in variable_mapping:
                variable_mapping[predicate.variable1] = f"x_{counter}"
                counter += 1
            if predicate.variable2 not in variable_mapping:
                variable_mapping[predicate.variable2] = f"x_{counter}"
                counter += 1

            lst[index_lst] = Predicate(
                variable_mapping[predicate.variable1],
                predicate.relation,
                variable_mapping[predicate.variable2],
            )
        return lst

    @staticmethod
    def compare_lists(list1: List[Predicate], list2: List[Predicate]) -> bool:
        list1 = PredicateUtils.sort_and_rename_variables(list1)
        for skip in range(len(list1)):
            list2 = PredicateUtils.sort_and_rename_variables(list2, skip)
            if len(list1) != len(list2):
                return False

            links1 = [(p.variable1, p.relation, p.variable2) for p in list1]
            links2 = [(p.variable1, p.relation, p.variable2) for p in list2]

            if links1 == links2:
                return True

        # Complex comparison logic for variable equivalences
        for skip in range(len(list1)):
            list2 = PredicateUtils.sort_and_rename_variables(list2, skip)
            links1 = [(p.variable1, p.relation, p.variable2) for p in list1]
            links2 = [(p.variable1, p.relation, p.variable2) for p in list2]
            links1.sort(key=lambda x: x[1])
            links2.sort(key=lambda x: x[1])
            ok_links1 = []
            equivalence = {}
            for pred1 in links1:
                for pred2 in links2:
                    if pred1 == pred2:
                        ok_links1.append(pred1)
                        equivalence[pred1[2]] = pred1[0]
                    else:
                        if pred1[1] == pred2[1] and pred1[2] == pred2[2]:
                            if equivalence.get(pred1[0]) == pred2[0]:
                                ok_links1.append(pred1)

            if ok_links1 == links1 or list(set(ok_links1)) == links1:
                return True

            ok_links2 = []
            equivalence = {}
            for pred2 in links2:
                for pred1 in links1:
                    if pred2 == pred1:
                        ok_links2.append(pred2)
                        equivalence[pred1[2]] = pred1[0]
                    else:
                        if pred2[1] == pred1[1] and pred2[2] == pred1[2]:
                            if equivalence.get(pred2[0]) == pred1[0]:
                                ok_links2.append(pred2)

            if ok_links2 == links2 or list(set(ok_links2)) == links2:
                return True
        return False

    @staticmethod
    @staticmethod
    def str_to_predicate(s: str) -> Predicate:
        s = s.strip()

        # 1. Try the old format: Predicate(variable1='x', relation='relates_to', variable2='y')
        match = re.match(
            r"Predicate\(variable1='(.*?)', relation='(.*?)', variable2='(.*?)'\)", s
        )
        if match:
            variable1, relation, variable2 = match.groups()
            return Predicate(variable1, relation, variable2)

        # 2. Try the new format with argument names: relates_to(arg1=x, arg2=y)
        match = re.match(r"^([A-Za-z0-9_]+)\(([^=]+)=([^)]*)\)$", s)
        if match:
            relation, variable1, variable2 = match.groups()
            return Predicate(variable1.strip(), relation.strip(), variable2.strip())

        # 3. Try the alternate new format: rel1(x, y)
        match = re.match(r"^([A-Za-z0-9_]+)\(([^,]+),\s*([^)]+)\)$", s)
        if match:
            relation, variable1, variable2 = match.groups()
            return Predicate(variable1.strip(), relation.strip(), variable2.strip())

        raise ValueError(f"Invalid Predicate string: {s}")

class RuleIO:
    @staticmethod
    def rule_to_dict(rule: Rule) -> Dict:
        if isinstance(rule, InclusionDependency):
            return {"type": "InclusionDependency", **asdict(rule)}
        elif isinstance(rule, FunctionalDependency):
            return {"type": "FunctionalDependency", **asdict(rule)}
        elif isinstance(rule, DenialConstraint):
            return {
                "type": "DenialConstraint",
                "table": rule.table,
                "conditions": [str(cond) for cond in rule.conditions],
                "correct": rule.correct,
                "compatible": rule.compatible
            }
        elif isinstance(rule, HornRule):
            return {
                "type": "HornRule",
                "body": [str(pred) for pred in rule.body],
                "head": str(rule.head),
                "display": rule.display,
                "correct": rule.correct,
                "compatible": rule.compatible
            }
        elif isinstance(rule, TGDRule):
            return {
                "type": "TGDRule",
                "body": [str(pred) for pred in rule.body],
                "head": [str(pred) for pred in rule.head],
                "display": rule.display,
                "accuracy": rule.accuracy,
                "confidence": rule.confidence,
                "correct": rule.correct,
                "compatible": rule.compatible
            }
        else:
            raise ValueError("Unknown rule type")

    @staticmethod
    def rule_from_dict(d: Dict) -> Rule:
        rule_type = d.get("type", "TGDRule")

        try:
            if "table_dependant" in d or "columns_dependant" in d or "table_referenced" in d:
                return InclusionDependency(
                    table_dependant=d["table_dependant"],
                    columns_dependant=tuple(d["columns_dependant"]),
                    table_referenced=d["table_referenced"],
                    columns_referenced=tuple(d["columns_referenced"]),
                    display=d.get("display"),  # Added this line
                    correct=d.get("correct"),
                    compatible=d.get("compatible")
                )
            elif rule_type == "FunctionalDependency":
                # Create a copy of the dictionary and remove the 'type' key
                rule_data = d.copy()
                rule_data.pop("type", None)
                return FunctionalDependency(**rule_data)
            elif rule_type == "DenialConstraint":
                # For simplicity, not fully implemented since reconstruction of DCCondition was not specified.
                raise NotImplementedError("DenialConstraint reconstruction not fully implemented.")
            elif rule_type == "HornRule":
                if "body" not in d or "head" not in d:
                    raise ValueError("Missing 'body' or 'head' in HornRule.")
                body = tuple(PredicateUtils.str_to_predicate(pred) for pred in d["body"])
                head = PredicateUtils.str_to_predicate(d["head"])
                return HornRule(
                    body=body,
                    head=head,
                    display=d.get("display"),
                    correct=d.get("correct"),
                    compatible=d.get("compatible")
                )
            elif rule_type == "TGDRule":
                if "body" not in d or "head" not in d:
                    raise ValueError("Missing 'body' or 'head' in TGDRule.")
                body = tuple(PredicateUtils.str_to_predicate(pred) for pred in d["body"])
                head = tuple(PredicateUtils.str_to_predicate(pred) for pred in d["head"])
                return TGDRule(
                    body=body,
                    head=head,
                    display=d.get("display"),
                    accuracy=d.get("accuracy", 0.0),
                    confidence=d.get("confidence", 0.0),
                    correct=d.get("correct"),
                    compatible=d.get("compatible")
                )
            else:
                raise ValueError(f"Unknown rule type: {rule_type}")
        except Exception as e:
            logging.error(f"Error converting rule from dict: {e}. Rule data: {d}")
            raise

    @staticmethod
    def save_yieled_rules_to_json(rule: Rule, filepath: str) -> None:
        try:
            with open(filepath, "r") as f:
                data = json.load(f)
        except Exception:
            data = []

        data.append(RuleIO.rule_to_dict(rule))
        with open(filepath, "w") as f:
            json.dump(data, f, indent=4)

    @staticmethod
    def save_rules_to_json(rules: List[Rule], filepath: str) -> int:
        try:
            rules_generated = [RuleIO.rule_to_dict(rule) for rule in rules]
            with open(filepath, "w") as f:
                json.dump(rules_generated, f, indent=4)
            return len(rules_generated)
        except Exception as e:
            raise e

    @staticmethod
    def save_yielded_rule_to_json(rule: Rule, filepath: str) -> None:
        try:
            with open(filepath, "r") as f:
                data = json.load(f)
        except FileNotFoundError:
            data = []

        data.append(RuleIO.rule_to_dict(rule))

        with open(filepath, "w") as f:
            json.dump(data, f, indent=4)

    @staticmethod
    def load_rules_from_json(filepath: str) -> List[Rule]:
        with open(filepath, "r") as f:
            return [RuleIO.rule_from_dict(d) for d in json.load(f)]


class TGDRuleFactory:
    """
    A factory class for creating TGDRule objects from ILP display strings.
    """

    @staticmethod
    def str_to_tgd(tgd_str: str, support: float, confidence: float) -> TGDRule:
        # Regular expression pattern to match the TGD format
        pattern = r"∀ (.*): (.*?) ⇒ (∃.*:)?(.*?)$"
        match = re.match(pattern, tgd_str)

        if match:
            variables_str, body_str, variables_head_str, head_str = match.groups()

            # Process the body
            body_predicates = []
            for split in body_str.split(" \u2227 "):
                # Each 'split' should represent a single predicate string
                body_pred = PredicateUtils.str_to_predicate(split)
                body_predicates.append(body_pred)
            body = tuple(body_predicates)

            # Process the head
            head_predicates = []
            for split in head_str.split(" \u2227 "):
                head_pred = PredicateUtils.str_to_predicate(split)
                head_predicates.append(head_pred)
            head = tuple(head_predicates)

            # Create and return the TGDRule object
            return TGDRule(
                body=body,
                head=head,
                display=tgd_str,
                accuracy=support,
                confidence=confidence
            )
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
            confidence=-1
        )

    def _get_head_body(self, disp: str) -> Tuple[str, str]:
        if ":-" not in disp:
            raise ValueError(f"Invalid rule display, expected ':-' in: {disp}")
        head_str, body_str = disp.split(":-")
        head_str = head_str.strip()
        body_str = body_str.strip()
        if body_str.endswith('.'):
            body_str = body_str[:-1].strip()
        return head_str, body_str

    def _create_predicates_from_relation(self, relation_str: str) -> List[Predicate]:
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
                Predicate(
                    variable1="id",
                    relation=relation + sep_relation_variable + column,
                    variable2=variable
                )
            )
        return predicates

    def _filter_predicates(self, preds: List[Predicate], other_preds: List[Predicate]) -> List[Predicate]:
        variable_counts = Counter()

        for predicate in preds:
            variable_counts[predicate.variable1] += 1
            variable_counts[predicate.variable2] += 1
        for predicate in other_preds:
            variable_counts[predicate.variable1] += 1
            variable_counts[predicate.variable2] += 1

        filtered = [
            predicate for predicate in preds
            if variable_counts[predicate.variable1] >= 2 and variable_counts[predicate.variable2] >= 2
        ]
        return filtered
