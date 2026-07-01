from mahilda.algorithms.mahilda_core.constraint_graph import (
    AttributeMapper,
    IndexedAttribute,
    JoinableIndexedAttributes,
)
from mahilda.algorithms.mahilda_core.tgd_discovery import (
    assign_variables,
    check_max_table,
    check_max_vars,
    check_minimal_candidate_rule,
    check_table_occurrences,
    construct_tgd_string,
    duplicate_test,
    extract_table_occurrences,
    is_start_node,
    next_node_test,
    split_candidate_rule,
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
