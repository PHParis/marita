import json

import pytest

from mahilda.utils.rule_io import RuleIO
from mahilda.utils.rules import FunctionalDependency, HornRule, InclusionDependency, Predicate, TGDRule


def test_rule_io_round_trip_inclusion_dependency() -> None:
    rule = InclusionDependency(
        table_dependant="child",
        columns_dependant=("parent_id",),
        table_referenced="parent",
        columns_referenced=("id",),
        display="child.parent_id ⊆ parent.id",
    )
    payload = RuleIO.rule_to_dict(rule)
    restored = RuleIO.rule_from_dict(payload)
    assert restored == rule


def test_rule_io_round_trip_functional_dependency() -> None:
    rule = FunctionalDependency(table="t", determinant=("a",), dependent="b")
    payload = RuleIO.rule_to_dict(rule)
    restored = RuleIO.rule_from_dict(payload)
    assert restored == rule


def test_rule_io_round_trip_horn_rule() -> None:
    body = (Predicate("x", "r", "y"),)
    head = Predicate("x", "s", "y")
    rule = HornRule(body=body, head=head, display="s(x,y) :- r(x,y)")
    payload = RuleIO.rule_to_dict(rule)
    restored = RuleIO.rule_from_dict(payload)
    assert restored == rule


def test_rule_io_round_trip_tgd_rule() -> None:
    body = (Predicate("x", "r", "y"),)
    head = (Predicate("x", "s", "z"),)
    rule = TGDRule(body=body, head=head, display="forall", accuracy=0.3, confidence=0.8)
    payload = RuleIO.rule_to_dict(rule)
    restored = RuleIO.rule_from_dict(payload)
    assert restored == rule


def test_rule_from_dict_validation_errors() -> None:
    with pytest.raises(ValueError):
        RuleIO.rule_from_dict({"type": "Unknown"})

    with pytest.raises(ValueError):
        RuleIO.rule_from_dict({"type": "HornRule", "head": "S(x=y)"})

    with pytest.raises(ValueError):
        RuleIO.rule_from_dict({"type": "TGDRule", "body": ["R(x=y)"]})

    with pytest.raises(NotImplementedError):
        RuleIO.rule_from_dict({"type": "DenialConstraint", "table": "t"})


def test_save_and_load_rules_json(tmp_path) -> None:
    rules = [
        FunctionalDependency(table="t", determinant=("a",), dependent="b"),
        InclusionDependency(
            table_dependant="child",
            columns_dependant=("parent_id",),
            table_referenced="parent",
            columns_referenced=("id",),
        ),
    ]
    path = tmp_path / "rules.json"
    written = RuleIO.save_rules_to_json(rules, str(path))
    assert written == 2
    data = json.loads(path.read_text(encoding="utf-8"))
    assert len(data) == 2
    loaded = RuleIO.load_rules_from_json(str(path))
    assert len(loaded) == 2
    assert isinstance(loaded[0], FunctionalDependency)
    assert loaded[0].table == "t"
    assert tuple(loaded[0].determinant) == ("a",)
    assert isinstance(loaded[1], InclusionDependency)
    assert loaded[1].table_dependant == "child"
