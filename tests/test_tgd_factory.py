import math

import pytest

from mahilda.utils.tgd_factory import TGDRuleFactory


def test_str_to_tgd_valid() -> None:
    tgd = TGDRuleFactory.str_to_tgd(
        "∀ x0: person(id=x0) ⇒ ∃ z0: city(id=z0)",
        support=0.7,
        confidence=0.9,
    )
    assert tgd.display.startswith("∀")
    assert tgd.accuracy == 0.7
    assert tgd.confidence == 0.9
    assert len(tgd.body) == 1
    assert len(tgd.head) == 1


def test_str_to_tgd_invalid_raises() -> None:
    with pytest.raises(ValueError):
        TGDRuleFactory.str_to_tgd("not a tgd", support=0.1, confidence=0.2)


def test_create_from_ilp_display_parses_rule() -> None:
    rule = TGDRuleFactory.create_from_ilp_display(
        "Head(x,y):-Body1(x,y), Body2(y,z).",
        accuracy=0.5,
    )
    assert rule.display.startswith("Head")
    assert rule.accuracy == 0.5
    assert math.isnan(rule.confidence)


def test_get_head_body_invalid_raises() -> None:
    factory = TGDRuleFactory()
    with pytest.raises(ValueError):
        factory._get_head_body("Head(x,y) Body(x,y)")


def test_create_predicates_from_relation_invalid_raises() -> None:
    factory = TGDRuleFactory()
    with pytest.raises(ValueError):
        factory._create_predicates_from_relation("invalid relation")


def test_filter_predicates_drops_isolated_variables() -> None:
    factory = TGDRuleFactory()
    preds = factory._create_predicates_from_relation("R(a,b)")
    other = factory._create_predicates_from_relation("S(c,d)")
    filtered = factory._filter_predicates(preds, other)
    assert filtered == []
