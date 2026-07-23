import math

import pytest

from marita.utils.tgd_factory import TGDRuleFactory


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


@pytest.mark.parametrize(
    "display",
    [
        "∀ x0, y0: bupa_0(arg1=x0, arg2=y0) ∧ bupa_type_0(arg1=y0) ⇒ bupa_name_0(arg1=x0)",
        "∀ x0, y0: fault_0(tm=x0, tf=y0) ∧ trfl_0(tf=y0) ⇒ time_0(tm=x0)",
        "∀ x0, y0: fault_test_0(tm=y0, tf=x0) ∧ time_0(tm=y0) ⇒ trfl_0(tf=x0)",
    ],
)
def test_str_to_tgd_expands_multi_column_atoms(display: str) -> None:
    tgd = TGDRuleFactory.str_to_tgd(display, support=1, confidence=1)

    assert len(tgd.body) == 3
    assert len(tgd.head) == 1
    assert tgd.body[0].variable1 == tgd.body[1].variable1
    assert tgd.body[0].relation.endswith("___sep___" + ("arg1" if "bupa" in display else "tm"))


def test_str_to_tgd_preserves_legacy_positional_binary_atoms() -> None:
    tgd = TGDRuleFactory.str_to_tgd("R(x0, y0) => S(x0, y0)", support=1, confidence=1)

    assert tgd.body[0].relation == "R"
    assert tgd.body[0].variable1 == "x0"
    assert tgd.body[0].variable2 == "y0"


def test_str_to_tgd_uses_distinct_row_variables_for_repeated_atoms() -> None:
    tgd = TGDRuleFactory.str_to_tgd(
        "∀ x0, y0: pair_0(left=x0, right=y0) ∧ pair_1(left=x0, right=y0) ⇒ target_0(id=x0)",
        support=1,
        confidence=1,
    )

    assert tgd.body[0].variable1 == tgd.body[1].variable1
    assert tgd.body[2].variable1 == tgd.body[3].variable1
    assert tgd.body[0].variable1 != tgd.body[2].variable1


def test_str_to_tgd_reports_invalid_atom_context() -> None:
    with pytest.raises(ValueError, match="Invalid atom.*broken"):
        TGDRuleFactory.str_to_tgd("∀ x0: broken ⇒ parent_0(id=x0)", support=1, confidence=1)


@pytest.mark.parametrize("table", ["Order Details", "Metadata - Countries", "table_0"])
def test_str_to_tgd_supports_quoted_relation_names(table: str) -> None:
    tgd = TGDRuleFactory.str_to_tgd(
        f'∀ x0: "{table}"_0(id=x0) ⇒ parent_0(id=x0)',
        support=1,
        confidence=1,
    )

    assert tgd.body[0].relation == f"{table}_0"


def test_str_to_tgd_ignores_implication_text_inside_quoted_relation_name() -> None:
    tgd = TGDRuleFactory.str_to_tgd(
        '∀ x0: "left => right"_0(id=x0) ⇒ parent_0(id=x0)',
        support=1,
        confidence=1,
    )

    assert tgd.body[0].relation == "left => right_0"


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


def test_create_from_ilp_display_logs_empty_body_and_filtered_warnings(caplog) -> None:
    caplog.set_level("WARNING")
    rule = TGDRuleFactory.create_from_ilp_display("Head(x):- .", accuracy=0.2)
    assert rule.display.startswith("Head")
    assert "No body relations extracted" in caplog.text
    assert "no valid" in caplog.text.lower()


def test_create_from_ilp_display_logs_empty_head(monkeypatch, caplog) -> None:
    caplog.set_level("WARNING")

    original = TGDRuleFactory._create_predicates_from_relation

    def patched(self, relation_str: str):
        if relation_str.startswith("Head"):
            return []
        return original(self, relation_str)

    monkeypatch.setattr(TGDRuleFactory, "_create_predicates_from_relation", patched)
    _ = TGDRuleFactory.create_from_ilp_display("Head(x):-Body(x,y).", accuracy=0.3)
    assert "No head predicates extracted" in caplog.text
