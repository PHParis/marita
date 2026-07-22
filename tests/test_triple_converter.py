import logging

from sqlalchemy import Column, ForeignKey, ForeignKeyConstraint, Integer, MetaData, String, Table, create_engine

from mahilda.database.triple_converter import TripleConverter


def _make_logger() -> logging.Logger:
    logger = logging.getLogger("test.triple_converter")
    logger.handlers.clear()
    logger.setLevel(logging.DEBUG)
    return logger


def test_convert_to_triples_literals_and_foreign_keys() -> None:
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()

    parent = Table(
        "parent",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("name", String),
    )
    child = Table(
        "child",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("parent_id", Integer, ForeignKey("parent.id")),
        Column("label", String),
    )
    metadata.create_all(engine)

    with engine.begin() as conn:
        conn.execute(parent.insert(), [{"id": 1, "name": "Alice"}])
        conn.execute(child.insert(), [{"id": 10, "parent_id": 1, "label": 'A "quoted" child'}])

    converter = TripleConverter(engine, metadata, _make_logger())
    triples = converter.convert_to_triples()

    assert ("parent_1", "parent.name", '"Alice"') in triples
    assert ("child_10", "child.label", '"A \\"quoted\\" child"') in triples
    assert ("child_10", "child.parent_id", "parent_1") in triples


def test_convert_to_triples_skips_no_pk_and_single_column() -> None:
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()

    Table("no_pk", metadata, Column("a", Integer), Column("b", String))
    Table("single_col", metadata, Column("only", Integer, primary_key=True))
    metadata.create_all(engine)

    converter = TripleConverter(engine, metadata, _make_logger())
    assert converter.convert_to_triples() == []


def test_convert_to_triples_keeps_multiple_foreign_key_targets() -> None:
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    first_target = Table("first_target", metadata, Column("id", Integer, primary_key=True))
    second_target = Table("second_target", metadata, Column("id", Integer, primary_key=True))
    source = Table(
        "source",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("value", Integer),
        ForeignKeyConstraint(["value"], ["first_target.id"]),
        ForeignKeyConstraint(["value"], ["second_target.id"]),
    )
    metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(first_target.insert(), [{"id": 1}])
        conn.execute(second_target.insert(), [{"id": 1}])
        conn.execute(source.insert(), [{"id": 10, "value": 1}])

    converter = TripleConverter(engine, metadata, _make_logger())

    assert converter._get_foreign_keys()["source"]["value"] == (
        ("first_target", "id"),
        ("second_target", "id"),
    )


def test_select_query_handles_missing_table_and_execution_error(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    Table("t", metadata, Column("id", Integer, primary_key=True), Column("v", String))
    metadata.create_all(engine)
    converter = TripleConverter(engine, metadata, _make_logger())

    assert converter._select_query("missing", ["id"]) == []

    class FailingEngine:
        def connect(self):
            raise RuntimeError("boom")

    converter.engine = FailingEngine()  # type: ignore[assignment]
    assert converter._select_query("t", ["id", "v"]) == []


def test_generate_rdf_id_and_sanitize_identifier() -> None:
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    converter = TripleConverter(engine, metadata, _make_logger())

    assert converter._generate_rdf_id("tab", ["id"], {"id": "A-1"}) == "tab_A_1"
    assert converter._generate_rdf_id("tab", ["missing"], {"id": 1}) == "unknown_id"
    assert converter._sanitize_identifier("a b.c-") == "a_b_c_"
