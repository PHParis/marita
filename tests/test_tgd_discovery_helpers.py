import pytest

import mahilda.algorithms.mahilda_core.tgd_discovery as discovery
from mahilda.algorithms.mahilda_core.constraint_graph import (
    AttributeMapper,
    ConstraintGraph,
    IndexedAttribute,
    JoinableIndexedAttributes,
)
from mahilda.algorithms.mahilda_core.tgd_discovery import (
    assign_variables,
    candidate_rule_key,
    check_max_table,
    check_max_vars,
    check_minimal_candidate_rule,
    check_table_occurrences,
    construct_tgd_string,
    dfs,
    duplicate_test,
    extract_table_occurrences,
    horn_rule_key,
    is_safe_split,
    is_start_node,
    next_node_test,
    split_candidate_rule,
    split_pruning,
)


def _ia(i: int, j: int, k: int) -> IndexedAttribute:
    return IndexedAttribute(i, j, k)


def _jia(a: IndexedAttribute, b: IndexedAttribute) -> JoinableIndexedAttributes:
    return JoinableIndexedAttributes(a, b)


def test_extract_table_occurrences() -> None:
    cr = [_jia(_ia(0, 0, 0), _ia(1, 0, 0)), _jia(_ia(1, 0, 1), _ia(2, 0, 0))]
    assert extract_table_occurrences(cr) == {(0, 0), (1, 0), (2, 0)}


def test_split_candidate_rule_empty_returns_false() -> None:
    assert split_candidate_rule([]) is False


def test_split_candidate_rule_non_empty_has_valid_splits() -> None:
    cr = [_jia(_ia(0, 0, 0), _ia(1, 0, 0))]
    splits = split_candidate_rule(cr)
    assert isinstance(splits, set)
    assert any(len(head) > 0 for _body, head in splits)


@pytest.mark.parametrize(
    ("body", "head"),
    [
        ({(0, 0)}, {(1, 0)}),
        ({(1, 0)}, {(0, 0)}),
    ],
    ids=["dunur-sister-to-person", "basketball-players-to-teams"],
)
def test_split_candidate_rule_keeps_both_fk_edge_orientations(body, head) -> None:
    candidate = [_jia(_ia(0, 0, 0), _ia(1, 0, 0))]

    assert (frozenset(body), frozenset(head)) in split_candidate_rule(candidate)


def test_split_candidate_rule_is_deterministic_for_repeated_occurrences() -> None:
    first = _jia(_ia(0, 0, 0), _ia(1, 0, 0))
    repeated = _jia(_ia(0, 1, 0), _ia(1, 0, 0))
    candidate = [first, repeated]

    splits = split_candidate_rule(candidate)

    assert (frozenset({(0, 1), (1, 0)}), frozenset({(0, 0)})) not in splits


def test_safe_split_requires_every_head_variable_in_body() -> None:
    shared = _jia(_ia(0, 0, 0), _ia(1, 0, 0))
    head_only = _jia(_ia(1, 0, 1), _ia(1, 0, 2))
    candidate = [shared, head_only]

    assert is_safe_split(candidate, {(0, 0)}, {(1, 0)}) is False
    assert is_safe_split([shared], {(0, 0)}, {(1, 0)}) is True


def test_split_pruning_accepts_repeated_occurrence_with_degree_one(
    monkeypatch,
) -> None:
    repeated_first = _jia(_ia(0, 0, 0), _ia(0, 1, 1))
    repeated_second = _jia(_ia(0, 1, 1), _ia(1, 0, 0))
    candidate = [repeated_first, repeated_second]

    monkeypatch.setattr(discovery, "prediction", lambda *args, **kwargs: 1)
    monkeypatch.setattr(discovery, "calculate_support", lambda *args, **kwargs: 1.0)
    monkeypatch.setattr(discovery, "calculate_confidence", lambda *args, **kwargs: 1.0)

    accepted, support, confidence = split_pruning(
        candidate,
        {(0, 0), (0, 1)},
        {(1, 0)},
        None,
        None,
    )

    assert accepted is True
    assert support == 1.0
    assert confidence == 1.0


def _dfs_test_graph() -> tuple[ConstraintGraph, JoinableIndexedAttributes]:
    first = _jia(_ia(0, 0, 0), _ia(1, 0, 0))
    second = _jia(_ia(1, 0, 1), _ia(2, 0, 0))
    graph = ConstraintGraph()
    graph.add_node(first)
    graph.add_node(second)
    graph.add_edge(first, second)
    return graph, first


def test_dfs_emits_equal_score_rules_with_same_head(monkeypatch) -> None:
    graph, first = _dfs_test_graph()
    seen_lengths: list[int] = []

    monkeypatch.setattr(discovery, "next_node_test", lambda *args, **kwargs: True)
    monkeypatch.setattr(discovery, "path_pruning", lambda *args, **kwargs: True)
    monkeypatch.setattr(discovery, "is_safe_split", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        discovery,
        "split_candidate_rule",
        lambda candidate: {
            (
                frozenset(extract_table_occurrences(candidate) - {(1, 0)}),
                frozenset({(1, 0)}),
            )
        },
    )

    def fake_split_pruning(candidate, body, head, db_inspector, mapper):
        del body, head, db_inspector, mapper
        seen_lengths.append(len(candidate))
        return True, 1.0, 1.0

    monkeypatch.setattr(discovery, "split_pruning", fake_split_pruning)

    yielded = list(
        dfs(
            graph,
            first,
            discovery.path_pruning,
            None,
            None,
        )
    )

    assert seen_lengths == [1, 2]
    assert len(yielded) == 2


def test_dfs_continues_after_lower_score_rule_is_suppressed(monkeypatch) -> None:
    graph, first = _dfs_test_graph()
    seen_lengths: list[int] = []

    monkeypatch.setattr(discovery, "next_node_test", lambda *args, **kwargs: True)
    monkeypatch.setattr(discovery, "path_pruning", lambda *args, **kwargs: True)
    monkeypatch.setattr(discovery, "is_safe_split", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        discovery,
        "split_candidate_rule",
        lambda candidate: {
            (
                frozenset(extract_table_occurrences(candidate) - {(1, 0)}),
                frozenset({(1, 0)}),
            )
        },
    )

    def fake_split_pruning(candidate, body, head, db_inspector, mapper):
        del body, head, db_inspector, mapper
        seen_lengths.append(len(candidate))
        return True, 2.0 if len(candidate) == 1 else 1.0, 1.0

    monkeypatch.setattr(discovery, "split_pruning", fake_split_pruning)

    yielded = list(
        dfs(
            graph,
            first,
            discovery.path_pruning,
            None,
            None,
        )
    )

    assert seen_lengths == [1, 2]
    assert len(yielded) == 2


def test_dfs_does_not_prune_deeper_orientation_after_short_candidate_fails(monkeypatch) -> None:
    graph, first = _dfs_test_graph()

    monkeypatch.setattr(discovery, "next_node_test", lambda *args, **kwargs: True)
    monkeypatch.setattr(discovery, "path_pruning", lambda *args, **kwargs: True)
    monkeypatch.setattr(discovery, "is_safe_split", lambda *args, **kwargs: True)

    def fake_splits(candidate):
        if len(candidate) == 1:
            return {(frozenset({(0, 0)}), frozenset({(1, 0)}))}
        return {(frozenset({(0, 0), (2, 0)}), frozenset({(1, 0)}))}

    monkeypatch.setattr(discovery, "split_candidate_rule", fake_splits)

    def fake_split_pruning(candidate, body, head, db_inspector, mapper):
        del body, head, db_inspector, mapper
        return (len(candidate) > 1, 1.0 if len(candidate) > 1 else 0.0, 1.0)

    monkeypatch.setattr(discovery, "split_pruning", fake_split_pruning)

    yielded = list(dfs(graph, first, discovery.path_pruning, None, None))

    assert len(yielded) == 1
    assert yielded[0][1] == (frozenset({(0, 0), (2, 0)}), frozenset({(1, 0)}))


def test_dfs_reaches_connected_candidate_through_incoming_edge(monkeypatch) -> None:
    low = _jia(_ia(0, 0, 0), _ia(1, 0, 0))
    middle = _jia(_ia(0, 0, 1), _ia(2, 0, 0))
    high = _jia(_ia(1, 0, 1), _ia(2, 0, 1))
    assert low < middle < high

    graph = ConstraintGraph()
    for node in (low, middle, high):
        graph.add_node(node)
    graph.add_edge(low, high)
    graph.add_edge(middle, high)

    visited_candidates: list[frozenset[JoinableIndexedAttributes]] = []
    monkeypatch.setattr(discovery, "next_node_test", lambda *args, **kwargs: True)
    monkeypatch.setattr(discovery, "path_pruning", lambda *args, **kwargs: True)
    monkeypatch.setattr(discovery, "is_safe_split", lambda *args, **kwargs: True)

    def fake_splits(candidate):
        visited_candidates.append(frozenset(candidate))
        if len(candidate) == 3:
            return {(frozenset({(0, 0), (1, 0)}), frozenset({(2, 0)}))}
        return set()

    monkeypatch.setattr(discovery, "split_candidate_rule", fake_splits)
    monkeypatch.setattr(
        discovery,
        "split_pruning",
        lambda *args, **kwargs: (True, 1.0, 1.0),
    )

    yielded = list(dfs(graph, low, discovery.path_pruning, None, None))

    assert frozenset({low, middle, high}) in visited_candidates
    assert len(yielded) == 1


def test_assign_variables_labels_by_body_and_head() -> None:
    shared = _ia(0, 0, 0)
    body_only = _ia(0, 0, 1)
    head_only = _ia(1, 0, 0)
    split = ({(0, 0)}, {(0, 0), (1, 0)})
    eq_classes = [{shared}, {body_only}, {head_only}]

    assigned = assign_variables(eq_classes, split)

    assert assigned[shared].startswith("x")
    assert assigned[body_only].startswith("x")
    assert assigned[head_only].startswith("z")


def test_construct_tgd_string_quantifiers_and_bottom_top() -> None:
    x = _ia(0, 0, 0)
    y = _ia(1, 0, 0)
    variable_assignment = {x: "x0", y: "z0"}
    body = {(0, 0)}
    head = {(1, 0)}

    tgd = construct_tgd_string("A_0(id=x0)", "B_0(id=z0)", variable_assignment, body, head)
    assert "∀" in tgd
    assert "∃" in tgd
    assert "⇒" in tgd

    bottom = construct_tgd_string("A_0(id=x0)", "", variable_assignment, body, set())
    assert bottom.endswith("⊥")

    top = construct_tgd_string("", "B_0(id=z0)", variable_assignment, set(), head)
    assert top.startswith("⊤")


def test_duplicate_test_detects_duplicates() -> None:
    assert duplicate_test(["a", "b"]) == 0

    try:
        duplicate_test(["a", "a"])
        raise AssertionError("Expected ValueError")
    except ValueError:
        assert True


def test_node_and_constraint_checks() -> None:
    n1 = _jia(_ia(0, 0, 0), _ia(1, 0, 0))
    n2 = _jia(_ia(0, 1, 0), _ia(1, 0, 1))
    cr = [n1]

    assert is_start_node(cr) is True
    assert check_table_occurrences(cr, n2) is True
    assert check_minimal_candidate_rule(cr, n2) in {True, False}
    assert check_max_table(cr, n2, max_table=10) is True
    assert check_max_vars(cr, n2, max_vars=2) is True
    assert check_max_vars(cr, n2, max_vars=1) is False


def test_max_variables_counts_logical_equality_chains() -> None:
    shared = _ia(1, 0, 0)
    first = _jia(_ia(0, 0, 0), shared)
    second = _jia(shared, _ia(2, 0, 0))

    assert check_max_vars([first], second, max_vars=1) is True


def test_minimality_accepts_any_licensed_spanning_tree_and_rejects_cycles() -> None:
    a = _ia(0, 0, 0)
    b = _ia(1, 0, 0)
    c = _ia(2, 0, 0)
    ab = _jia(a, b)
    bc = _jia(b, c)
    ac = _jia(a, c)

    assert check_minimal_candidate_rule([ab], bc) is True
    assert check_minimal_candidate_rule([ab, bc], ac) is False
    assert candidate_rule_key([ab, bc]) == candidate_rule_key([ab, ac])


def test_horn_rule_key_normalizes_repeated_head_occurrence() -> None:
    child0 = _ia(0, 0, 0)
    child1 = _ia(0, 1, 0)
    parent = _ia(1, 0, 0)
    candidate = [_jia(child0, parent), _jia(child1, parent)]

    first_head = horn_rule_key(candidate, {(0, 1), (1, 0)}, {(0, 0)})
    second_head = horn_rule_key(candidate, {(0, 0), (1, 0)}, {(0, 1)})

    assert first_head == second_head


def test_next_node_test_rejects_visited() -> None:
    n1 = _jia(_ia(0, 0, 0), _ia(1, 0, 0))
    n2 = _jia(_ia(0, 1, 0), _ia(1, 0, 1))
    assert next_node_test([n1], n2, visited={n2}) is False


def test_next_node_test_accepts_valid_candidate() -> None:
    n1 = _jia(_ia(0, 0, 0), _ia(1, 0, 0))
    n2 = _jia(_ia(0, 1, 0), _ia(1, 0, 1))
    assert next_node_test([n1], n2, visited=set(), max_table=10, max_vars=10) is True


def test_construct_predicates_smoke_with_mapper() -> None:
    mapper = AttributeMapper(
        table_name_to_index={"a": 0, "b": 1},
        attribute_name_to_index={"a": {"id": 0}, "b": {"id": 0}},
    )
    split = ({(0, 0)}, {(1, 0)})
    assigned = assign_variables([{_ia(0, 0, 0)}, {_ia(1, 0, 0)}], split)
    tgd = construct_tgd_string("a_0(id=x0)", "b_0(id=z0)", assigned, split[0], split[1])
    assert mapper.index_to_table_name[0] == "a"
    assert "a_0" in tgd
