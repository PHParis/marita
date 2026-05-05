from mahilda.algorithms.mahilda_core.candidate_rule_chains import CandidateRuleChains
from mahilda.algorithms.mahilda_core.constraint_graph import (
    AttributeMapper,
    IndexedAttribute,
    JoinableIndexedAttributes,
)


def _ia(i: int, j: int, k: int) -> IndexedAttribute:
    return IndexedAttribute(i, j, k)


def _jia(a: IndexedAttribute, b: IndexedAttribute) -> JoinableIndexedAttributes:
    return JoinableIndexedAttributes(a, b)


def _mapper() -> AttributeMapper:
    return AttributeMapper(
        table_name_to_index={"a": 0, "b": 1},
        attribute_name_to_index={
            "a": {"id": 0, "x": 1},
            "b": {"id": 0, "y": 1},
        },
    )


def test_find_candidate_rule_chains_connected_components() -> None:
    a00 = _ia(0, 0, 0)
    a01 = _ia(0, 1, 0)
    b00 = _ia(1, 0, 0)
    b01 = _ia(1, 1, 0)

    cr = [
        _jia(a00, a01),
        _jia(a01, b00),
        _jia(b00, b01),
    ]

    chains = CandidateRuleChains(cr).cr_chains

    assert len(chains) == 1
    assert set(chains[0]) == {a00, a01, b00, b01}


def test_find_candidate_rule_chains_disconnected() -> None:
    a00 = _ia(0, 0, 0)
    a01 = _ia(0, 1, 0)
    b00 = _ia(1, 0, 0)
    b01 = _ia(1, 1, 0)

    cr = [_jia(a00, a01), _jia(b00, b01)]
    chains = CandidateRuleChains(cr).cr_chains

    assert len(chains) == 2
    assert {frozenset(c) for c in chains} == {
        frozenset({a00, a01}),
        frozenset({b00, b01}),
    }


def test_get_x_chains_filters_body_and_head() -> None:
    mapper = _mapper()
    a00 = _ia(0, 0, 0)
    b00 = _ia(1, 0, 0)
    a01 = _ia(0, 1, 1)
    b01 = _ia(1, 1, 1)
    cr = [_jia(a00, b00), _jia(a01, b01)]

    chains = CandidateRuleChains(cr)
    body = {(0, 0)}
    head = {(1, 0)}

    both = chains.get_x_chains(body=body, head=head, mapper=mapper)
    body_only = chains.get_x_chains(body=body, head=head, mapper=mapper, select_body=True)
    head_only = chains.get_x_chains(body=body, head=head, mapper=mapper, select_head=True)

    assert both == [[("a", 0, "id"), ("b", 0, "id")]]
    assert body_only == [[("a", 0, "id")]]
    assert head_only == [[("b", 0, "id")]]
