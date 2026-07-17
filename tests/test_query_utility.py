import logging

import pytest
from sqlalchemy import Column, ForeignKey, Integer, MetaData, String, Table, create_engine

from mahilda.database.query_utility import ColorFormatter, QueryUtility


def _build_db() -> tuple:
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    a = Table(
        "a",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("val", String),
        Column("b_id", Integer, ForeignKey("b.id")),
    )
    b = Table(
        "b",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("val", String),
    )
    metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(b.insert(), [{"id": 1, "val": "x"}, {"id": 2, "val": "y"}])
        conn.execute(
            a.insert(),
            [
                {"id": 10, "val": "x", "b_id": 1},
                {"id": 11, "val": "z", "b_id": 2},
            ],
        )
    return engine, metadata, a, b


def _utility(engine, metadata) -> QueryUtility:
    l1 = logging.getLogger("test.query.time")
    l2 = logging.getLogger("test.query.results")
    l1.handlers.clear()
    l2.handlers.clear()
    return QueryUtility(engine=engine, metadata=metadata, logger_query_time=l1, logger_query_results=l2)


def test_setup_logging_handlers_only_once() -> None:
    engine, metadata, _a, _b = _build_db()
    util = _utility(engine, metadata)
    first_time_handlers = len(util.logger_query_time.handlers)
    first_result_handlers = len(util.logger_query_results.handlers)
    util._setup_logging_handlers()
    assert len(util.logger_query_time.handlers) == first_time_handlers
    assert len(util.logger_query_results.handlers) == first_result_handlers


def test_organize_and_process_join_conditions() -> None:
    engine, metadata, _a, _b = _build_db()
    util = _utility(engine, metadata)
    conds = [("a", 0, "b_id", "b", 0, "id")]
    groups = util._organize_join_conditions(conds)
    assert len(groups) == 1

    join_bases, aliases, used_aliases, table_occ = util._process_join_conditions(groups, disjoint_semantics=True)
    assert len(join_bases) == 1
    assert "a_0" in aliases
    assert "b_0" in aliases
    assert "a_0" in used_aliases
    assert "a" in table_occ


def test_construct_join_and_count_query() -> None:
    engine, metadata, _a, _b = _build_db()
    util = _utility(engine, metadata)
    conds = [("a", 0, "b_id", "b", 0, "id")]

    query, primary_key_conditions, join_base = util._construct_count_query(conds, False, False, None)
    assert query is not None
    assert join_base is not None
    assert isinstance(primary_key_conditions, list)

    count = util.get_join_row_count(conds)
    assert count == 2


def test_query_result_cache_keys_include_semantic_arguments() -> None:
    engine, metadata, _a, _b = _build_db()
    util = _utility(engine, metadata)
    conditions = [("a", 0, "b_id", "b", 0, "id")]

    assert util.get_join_row_count(conditions, disjoint_semantics=False) == 2
    assert util.get_join_row_count(conditions, disjoint_semantics=False) == 2
    assert util.get_join_row_count(conditions, disjoint_semantics=True) == 2
    assert util.get_join_row_count(conditions, distinct=True) == 2
    assert util.query_cache_hits >= 1

    assert util.get_rule_count([("b", 0)], [], [[("b", 0, "id")]]) == 2
    assert util.get_rule_count([("b", 0)], [], [[("b", 0, "val")]]) == 2


def test_column_value_set_cache_preserves_string_filtering_and_clears() -> None:
    engine, metadata, a, _b = _build_db()
    with engine.begin() as conn:
        conn.execute(a.insert(), {"id": 12, "val": None, "b_id": 1})
    util = _utility(engine, metadata)

    assert util.get_column_value_set("a", "val") == frozenset({"x", "z"})
    assert util.get_column_value_set("a", "val") == frozenset({"x", "z"})
    assert util.column_value_cache_hits == 1
    assert util._column_value_cache_size == 2

    util.clear_caches()
    assert not util._query_result_cache
    assert not util._column_value_set_cache


def test_construct_select_query_count_over_requires_aliases() -> None:
    engine, metadata, _a, _b = _build_db()
    util = _utility(engine, metadata)
    with pytest.raises(ValueError):
        util._construct_select_query(join_base="x", distinct=False, count_over=[[("a", 0, "id")]], aliases=None)


def test_explicit_rule_count_supports_isolated_atoms_and_projections() -> None:
    engine, metadata, _a, _b = _build_db()
    util = _utility(engine, metadata)
    assert util.get_rule_count([("b", 0)], [], [[("b", 0, "id")]]) == 2
    assert (
        util.get_rule_count(
            [("a", 0), ("a", 1)],
            [("a", 0, "val", "a", 1, "val")],
            [[("a", 0, "val"), ("a", 1, "val")]],
            disjoint_semantics=True,
        )
        == 0
    )


def test_check_threshold_and_query_failures() -> None:
    engine, metadata, _a, _b = _build_db()
    util = _utility(engine, metadata)
    conds = [("a", 0, "b_id", "b", 0, "id")]
    assert util.check_threshold(conds, threshold=0) == 1

    assert util.get_join_row_count([("missing", 0, "x", "b", 0, "id")]) == 0


def test_metadata_helpers_and_foreign_keys() -> None:
    engine, metadata, _a, _b = _build_db()
    util = _utility(engine, metadata)
    assert util._get_table_names() == ["a", "b"]
    assert set(util._get_attribute_names("a")) == {"id", "val", "b_id"}
    assert util._get_attribute_domain("a", "id") is not None
    assert util._get_attribute_is_key("a", "id") is True

    fk = util._get_foreign_keys()
    assert fk["a"]["b_id"] == ("b", "id")


def test_color_formatter_wraps_message() -> None:
    fmt = ColorFormatter("%(message)s")
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "hello", args=(), exc_info=None)
    out = fmt.format(record)
    assert "hello" in out


def test_get_or_create_alias_invalid_table_raises() -> None:
    engine, metadata, _a, _b = _build_db()
    util = _utility(engine, metadata)
    with pytest.raises(ValueError):
        util._get_or_create_alias({}, "missing", 0)


def test_construct_join_empty_and_where_constraint_branch() -> None:
    engine, metadata, _a, _b = _build_db()
    util = _utility(engine, metadata)
    join_base, constraints = util._construct_join([], {}, set())
    assert join_base is None
    assert constraints == []

    groups = util._organize_join_conditions([("a", 0, "id", "a", 0, "id")])
    join_bases, aliases, used_aliases, _ = util._process_join_conditions(groups, disjoint_semantics=False)
    assert used_aliases == {"a_0"}
    join_base2, constraints2 = util._construct_join(join_bases, aliases, used_aliases)
    assert join_base2 is not None
    assert constraints2


def test_construct_primary_key_conditions_for_multiple_occurrences() -> None:
    engine, metadata, _a, _b = _build_db()
    util = _utility(engine, metadata)
    aliases = {
        "a_0": util._get_or_create_alias({}, "a", 0),
        "a_1": util._get_or_create_alias({}, "a", 1),
    }
    conds = util._construct_primary_key_conditions({"a": {0, 1}}, aliases, {"a_0", "a_1"})
    assert len(conds) == 1


def test_relation_disjoint_composite_key_uses_tuple_inequality() -> None:
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    composite = Table(
        "composite",
        metadata,
        Column("left_id", Integer, primary_key=True),
        Column("right_id", Integer, primary_key=True),
    )
    metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(
            composite.insert(),
            [
                {"left_id": 1, "right_id": 1},
                {"left_id": 1, "right_id": 2},
            ],
        )

    util = _utility(engine, metadata)
    conditions = [("composite", 0, "left_id", "composite", 1, "left_id")]

    assert util.get_join_row_count(conditions, disjoint_semantics=True) == 2


def test_relation_disjoint_repeated_table_without_primary_key_has_no_witness() -> None:
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    no_key = Table("no_key", metadata, Column("value", Integer))
    metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(no_key.insert(), [{"value": 1}, {"value": 1}])

    util = _utility(engine, metadata)
    conditions = [("no_key", 0, "value", "no_key", 1, "value")]

    assert util.get_join_row_count(conditions, disjoint_semantics=True) == 0


def test_connected_join_construction_is_independent_of_condition_order() -> None:
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    d = Table("d", metadata, Column("id", Integer, primary_key=True))
    c = Table(
        "c",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("d_id", Integer, ForeignKey("d.id")),
    )
    b = Table(
        "b_chain",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("c_id", Integer, ForeignKey("c.id")),
    )
    a = Table(
        "a_chain",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("b_id", Integer, ForeignKey("b_chain.id")),
    )
    metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(d.insert(), [{"id": 2}])
        conn.execute(c.insert(), [{"id": 1, "d_id": None}])
        conn.execute(b.insert(), [{"id": 1, "c_id": 1}])
        conn.execute(a.insert(), [{"id": 1, "b_id": 1}])
        conn.execute(c.update().values(d_id=99))

    util = _utility(engine, metadata)
    conditions = [
        ("a_chain", 0, "b_id", "b_chain", 0, "id"),
        ("c", 0, "d_id", "d", 0, "id"),
        ("b_chain", 0, "c_id", "c", 0, "id"),
    ]

    assert util.get_join_row_count(conditions) == 0


def test_count_over_alias_missing_raises() -> None:
    engine, metadata, _a, _b = _build_db()
    util = _utility(engine, metadata)
    with pytest.raises(ValueError):
        util._construct_select_query(
            join_base=metadata.tables["a"],
            distinct=False,
            count_over=[[("missing", 0, "id")]],
            aliases={"a_0": util._get_or_create_alias({}, "a", 0)},
        )
