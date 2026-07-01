from __future__ import annotations

import pytest

from mahilda.algorithms.mahilda_core import tgd_discovery
from mahilda.algorithms.mahilda_core.constraint_graph import (
    Attribute,
    AttributeMapper,
    ConstraintGraph,
    IndexedAttribute,
    JoinableIndexedAttributes,
)


class _FakeInspector:
    def __init__(self) -> None:
        self.fk = set()
        self.values = {}
        self.tables = ["a", "b"]

    def check_foreign_key_silently(self, t1, c1, t2, c2):
        return (t1, c1, t2, c2) in self.fk

    def get_attribute_values(self, table, col):
        return self.values.get((table, col), [])

    def get_table_names(self):
        return self.tables

    def get_attribute_names(self, table):
        return ["id", "val"]

    def get_attribute_domain(self, table, attr):
        return "TEXT" if attr == "val" else "INT"

    def get_attribute_is_key(self, table, attr):
        return attr == "id"


def test_attribute_compatibility_and_overlap_methods() -> None:
    insp = _FakeInspector()
    a = Attribute("a", "id")
    b = Attribute("b", "a_id")

    insp.fk.add(("a", "id", "b", "a_id"))
    assert a.is_compatible(b, db_inspector=insp) is True

    c = Attribute("a", "val")
    insp.values[("a", "val")] = ["x", "y", None]
    insp.values[("b", "val")] = ["y", "z"]
    assert c.has_common_elements_above_threshold(insp, "a", "val", "b", "val", 0) is True
    assert c.has_common_elements_above_threshold_percentage(insp, "a", "val", "b", "val", 0.1) is True


def test_generate_attributes() -> None:
    insp = _FakeInspector()
    attrs = Attribute.generate_attributes(insp)
    assert len(attrs) == 4
    assert any(a.table == "a" and a.name == "id" and a.is_key for a in attrs)


def test_indexed_attribute_and_joinable_ordering_and_connection() -> None:
    with pytest.raises(ValueError):
        IndexedAttribute(-1, 0, 0)

    a = IndexedAttribute(0, 0, 0)
    b = IndexedAttribute(0, 1, 0)
    c = IndexedAttribute(1, 0, 0)
    assert a < b
    assert a <= b
    assert a.is_connected(IndexedAttribute(0, 0, 1)) is True

    j1 = JoinableIndexedAttributes(a, c)
    j2 = JoinableIndexedAttributes(b, c)
    assert j1 != j2
    assert (j1 < j2) in {True, False}
    assert j1.is_connected(j2) is True
    assert list(iter(j1))


def test_attribute_mapper_round_trip() -> None:
    mapper = AttributeMapper(
        table_name_to_index={"a": 0, "b": 1},
        attribute_name_to_index={"a": {"id": 0}, "b": {"id": 0}},
    )
    attr = Attribute("a", "id")
    ia = mapper.attribute_to_indexed(attr, table_occurrence=2)
    assert ia.i == 0 and ia.j == 2 and ia.k == 0
    back = mapper.indexed_attribute_to_attribute(ia)
    assert back.table == "a" and back.name == "id"


def test_constraint_graph_core_behaviors() -> None:
    a = IndexedAttribute(0, 0, 0)
    b = IndexedAttribute(1, 0, 0)
    c = IndexedAttribute(0, 1, 0)
    j1 = JoinableIndexedAttributes(a, b)
    j2 = JoinableIndexedAttributes(b, c)

    graph = ConstraintGraph.from_jia_list([j1, j2])
    assert len(graph.nodes) >= 2
    assert graph.neighbors(j1) == sorted(graph.neighbors(j1))

    graph2 = ConstraintGraph()
    graph2.add_node(j1)
    graph2.add_node(j2)
    graph2.add_edge(j1, j2)
    assert graph2.is_connected(j1, j2) is True

    high = JoinableIndexedAttributes(IndexedAttribute(2, 0, 0), IndexedAttribute(3, 0, 0))
    low = JoinableIndexedAttributes(IndexedAttribute(0, 0, 0), IndexedAttribute(1, 0, 0))
    graph2.add_node(high)
    graph2.add_node(low)
    try:
        graph2.add_edge(high, low)
    except Exception as exc:
        assert "Source node must" in str(exc)
    else:
        raise AssertionError("Expected source/target ordering exception")


def test_is_compatible_fk_only_no_overlap_returns_false(monkeypatch) -> None:
    monkeypatch.setattr(tgd_discovery, "APPLY_FULL_JOINABILITY", False)

    insp = _FakeInspector()
    a = Attribute("a", "val")
    b = Attribute("b", "val")
    insp.values[("a", "val")] = []
    insp.values[("b", "val")] = ["x", "y"]
    assert a.is_compatible(b, db_inspector=insp) is False


def test_is_compatible_full_joinability_with_overlap_returns_true(monkeypatch) -> None:
    monkeypatch.setattr(tgd_discovery, "APPLY_FULL_JOINABILITY", True)

    insp = _FakeInspector()
    a = Attribute("a", "val")
    b = Attribute("b", "val")
    insp.values[("a", "val")] = ["x", "y"]
    insp.values[("b", "val")] = ["y", "z"]
    assert a.is_compatible(b, db_inspector=insp, threshold_overlap=0) is True
