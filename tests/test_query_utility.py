import logging

import pytest
from sqlalchemy import Column, ForeignKey, Integer, MetaData, String, Table, create_engine

from mahilda.database.query_utility import QueryUtility


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


def test_construct_select_query_count_over_requires_aliases() -> None:
    engine, metadata, _a, _b = _build_db()
    util = _utility(engine, metadata)
    with pytest.raises(ValueError):
        util._construct_select_query(join_base="x", distinct=False, count_over=[[("a", 0, "id")]], aliases=None)


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
