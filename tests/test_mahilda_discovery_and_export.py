from __future__ import annotations

from typing import TYPE_CHECKING

from mahilda.algorithms import mahilda as mahilda_module
from mahilda.algorithms.mahilda import MAHILDA, HornRuleExtended
from mahilda.algorithms.mahilda_core.constraint_graph import IndexedAttribute, JoinableIndexedAttributes
from mahilda.utils.rules import Predicate, TGDRule

if TYPE_CHECKING:
    from pathlib import Path


def _one_head_rule(display: str, support: float, confidence: float) -> TGDRule:
    return TGDRule(
        body=(Predicate("x", "r", "y"),),
        head=(Predicate("x", "s", "z"),),
        display=display,
        accuracy=support,
        confidence=confidence,
    )


def test_discover_rules_handles_empty_jia_and_restores_globals(monkeypatch) -> None:
    algorithm = MAHILDA(database=object(), settings={"disjoint_semantics": True, "split_mean_threshold": 0.25})
    old_disjoint = mahilda_module.mahilda_core.APPLY_DISJOINT
    old_threshold = mahilda_module.mahilda_core.SPLIT_PRUNING_MEAN_THRESHOLD
    old_full_join = mahilda_module.mahilda_core.APPLY_FULL_JOINABILITY
    old_full_join = mahilda_module.mahilda_core.APPLY_FULL_JOINABILITY

    def fake_init(*args, **kwargs):
        del args, kwargs
        return object(), object(), []

    monkeypatch.setattr(mahilda_module, "init", fake_init)

    discovered = list(algorithm.discover_rules())
    assert discovered == []
    assert old_disjoint == mahilda_module.mahilda_core.APPLY_DISJOINT
    assert old_threshold == mahilda_module.mahilda_core.SPLIT_PRUNING_MEAN_THRESHOLD
    assert old_full_join == mahilda_module.mahilda_core.APPLY_FULL_JOINABILITY


def test_discover_rules_yields_only_single_head_and_skips_errors(monkeypatch) -> None:
    algorithm = MAHILDA(database=object(), settings={"timeout": 100})

    ia1 = IndexedAttribute(0, 0, 0)
    ia2 = IndexedAttribute(1, 0, 0)
    jia = JoinableIndexedAttributes(ia1, ia2)

    def fake_init(*args, **kwargs):
        del args, kwargs
        return object(), object(), [jia]

    def fake_dfs(*args, **kwargs):
        del args, kwargs
        yield [jia], ({(0, 0)}, {(1, 0)}), (0.7, 0.8)
        yield [jia], ({(0, 0)}, {(1, 0)}), (0.4, 0.5)

    calls = {"n": 0}

    def fake_str_to_tgd(tgd_str: str, support: float, confidence: float):
        calls["n"] += 1
        if calls["n"] == 1:
            return _one_head_rule(tgd_str, support, confidence)
        if calls["n"] == 2:
            return TGDRule(
                body=(Predicate("x", "r", "y"),),
                head=(Predicate("x", "s", "z"), Predicate("x", "t", "w")),
                display=tgd_str,
                accuracy=support,
                confidence=confidence,
            )
        raise ValueError("boom")

    monkeypatch.setattr(mahilda_module, "init", fake_init)
    monkeypatch.setattr(mahilda_module, "dfs", fake_dfs)
    monkeypatch.setattr(mahilda_module, "instantiate_tgd", lambda *args, **kwargs: "∀ x: A(x=x) ⇒ B(x=x)")
    monkeypatch.setattr(mahilda_module.TGDRuleFactory, "str_to_tgd", fake_str_to_tgd)

    rules = list(algorithm.discover_rules())
    assert len(rules) == 1
    assert rules[0].accuracy == 0.7


def test_discover_rules_respects_should_stop_and_timeout(monkeypatch) -> None:
    algorithm = MAHILDA(database=object())

    ia1 = IndexedAttribute(0, 0, 0)
    ia2 = IndexedAttribute(1, 0, 0)
    jia = JoinableIndexedAttributes(ia1, ia2)

    monkeypatch.setattr(mahilda_module, "init", lambda *args, **kwargs: (object(), object(), [jia]))
    monkeypatch.setattr(mahilda_module, "dfs", lambda *args, **kwargs: iter(()))

    assert list(algorithm.discover_rules(should_stop=lambda: True)) == []
    assert list(algorithm.discover_rules(timeout=0)) == []


def test_horn_rule_extended_and_export_and_stats(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        mahilda_module.TGDRuleFactory,
        "str_to_tgd",
        lambda display, support, confidence: _one_head_rule(display, support, confidence),
    )

    ext = HornRuleExtended(
        body=(Predicate("x", "r", "y"),),
        head=Predicate("x", "s", "z"),
        support=0.6,
        confidence=0.9,
        display="∀ x: r(x=y) ⇒ s(x=z)",
    )
    converted = ext.to_tgd_rule()
    assert converted.confidence == 0.9

    rules = [
        _one_head_rule("r1", 0.2, 0.3),
        TGDRule(
            body=(Predicate("x", "r", "y"),),
            head=(Predicate("x", "s", "z"), Predicate("x", "t", "w")),
            display="r2",
            accuracy=0.4,
            confidence=0.5,
        ),
    ]

    stats = MAHILDA.get_horn_rule_statistics(rules)
    assert stats["horn_rules"] == 1
    assert stats["average_support"] == 0.2

    out = tmp_path / "rules.txt"
    MAHILDA.export_horn_rules(rules, str(out))
    assert out.exists()
    assert out.read_text(encoding="utf-8").strip() == "r1"


def test_coercion_helpers() -> None:
    assert MAHILDA._to_bool("yes") is True
    assert MAHILDA._to_bool("0") is False
    assert MAHILDA._coerce_positive_int("4", 2) == 4
    assert MAHILDA._coerce_positive_int("x", 2) == 2
    assert MAHILDA._coerce_timeout("10") == 10
    assert MAHILDA._coerce_timeout("0") is None
    assert MAHILDA._coerce_float("1.5", 0.0) == 1.5
    assert MAHILDA._coerce_float("bad", 0.0) == 0.0


def test_joinability_coercion() -> None:
    assert MAHILDA._coerce_joinability(True) == "full"
    assert MAHILDA._coerce_joinability(False) == "fk"
    assert MAHILDA._coerce_joinability(None) == "fk"
    assert MAHILDA._coerce_joinability("full") == "full"
    assert MAHILDA._coerce_joinability("fk") == "fk"
    assert MAHILDA._coerce_joinability("  FULL  ") == "full"
