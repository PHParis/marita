import json
import re
from dataclasses import asdict, dataclass
from typing import List, NamedTuple, Optional, Tuple, Union

from marita.utils.relation_names import internal_relation_name, parse_relation_reference, split_relation_atom


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
        with open(filepath, "a+") as f:
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
                    "compatible": self.compatible,
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
        with open(filepath, "a+") as f:
            json.dump(
                {
                    "body": [str(pred) for pred in self.body],
                    "head": str(self.head),
                    "display": self.display,
                    "correct": self.correct,
                    "compatible": self.compatible,
                },
                f,
                indent=4,
            )

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
        with open(filepath, "w") as f:
            json.dump(
                {
                    "body": [str(pred) for pred in self.body],
                    "head": [str(pred) for pred in self.head],
                    "display": self.display,
                    "accuracy": self.accuracy,
                    "confidence": self.confidence,
                    "correct": self.correct,
                    "compatible": self.compatible,
                },
                f,
                indent=4,
            )

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


@dataclass(frozen=True)
class MARITARule:
    """A MARITA rule; support is raw projected-head support."""

    body: Tuple[Predicate, ...]
    head: Tuple[Predicate, ...]
    display: str
    support: int
    confidence: float
    correct: Optional[bool] = None
    compatible: Optional[bool] = None

    @property
    def accuracy(self) -> int:
        """Compatibility alias for old in-memory consumers."""
        return self.support


Rule = Union[InclusionDependency, FunctionalDependency, DenialConstraint, HornRule, TGDRule, MARITARule]


class PredicateUtils:
    @staticmethod
    def sort_and_rename_variables(lst: List[Predicate], skip: int = 0) -> List[Predicate]:
        try:
            lst.sort(key=lambda x: x.relation)
        except TypeError:
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
    def str_to_predicate(s: str) -> Predicate:
        s = s.strip()

        match = re.match(r"Predicate\(variable1='(.*?)', relation='(.*?)', variable2='(.*?)'\)", s)
        if match:
            variable1, relation, variable2 = match.groups()
            return Predicate(variable1, relation, variable2)

        relation_text, arguments = split_relation_atom(s)
        table, occurrence, has_occurrence = parse_relation_reference(relation_text)
        relation = internal_relation_name(table, occurrence, has_occurrence)
        raw_arguments = [part.strip() for part in arguments.split(",") if part.strip()]
        if len(raw_arguments) == 1 and "=" in raw_arguments[0]:
            variable1, variable2 = raw_arguments[0].split("=", maxsplit=1)
            return Predicate(variable1.strip(), relation, variable2.strip())
        if len(raw_arguments) == 2 and all("=" not in part for part in raw_arguments):
            return Predicate(raw_arguments[0], relation, raw_arguments[1])

        raise ValueError(f"Invalid Predicate string: {s}")


def __getattr__(name: str):
    """Lazily provide historical utility re-exports without import cycles."""
    if name == "RuleIO":
        from marita.utils.rule_io import RuleIO

        return RuleIO
    if name == "TGDRuleFactory":
        from marita.utils.tgd_factory import TGDRuleFactory

        return TGDRuleFactory
    raise AttributeError(name)
