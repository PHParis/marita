from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import mahilda.algorithms.mahilda_core.tgd_discovery as discovery
from mahilda.algorithms.mahilda import MAHILDA
from mahilda.algorithms.mahilda_core.constraint_graph import Attribute, ConstraintGraph, JoinableIndexedAttributes
from mahilda.algorithms.mahilda_core.tgd_discovery import dfs, init, instantiate_tgd
from mahilda.audit.evaluator import SQLiteRuleEvaluator
from mahilda.audit.parsing import parse_formula
from mahilda.database.alchemy_utility import AlchemyUtility

if TYPE_CHECKING:
    from pathlib import Path


def _write_fk_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE parent (id INTEGER PRIMARY KEY);
            CREATE TABLE child (
                id INTEGER PRIMARY KEY,
                parent_id INTEGER NOT NULL REFERENCES parent(id)
            );
            INSERT INTO parent(id) VALUES (1), (2);
            INSERT INTO child(id, parent_id) VALUES (10, 1), (11, 1);
            """
        )
        connection.commit()
    finally:
        connection.close()


def test_marita_mines_and_independently_validates_repeated_relation_rule(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "paper_contract.db"
    _write_fk_database(database_path)
    database = AlchemyUtility(
        f"sqlite:///{database_path}",
        create_index=False,
        create_csv=False,
        create_tsv=False,
        get_data=False,
    )
    algorithm = MAHILDA(
        database,
        settings={
            "walk_length": 2,
            "max_tables": 3,
            "max_variables": 2,
            "disjoint_semantics": True,
            "joinability": "fk",
            "support_threshold": 1,
            "results_dir": tmp_path / "results",
        },
    )

    discovered = [parse_formula(rule.display) for rule in algorithm.discover_rules()]
    expected = parse_formula("child_0(parent_id=x0) ∧ child_1(parent_id=x0) ⇒ parent_0(id=x0)")

    canonical_rules = {rule.canonical_key() for rule in discovered}
    assert expected.canonical_key() in canonical_rules
    assert len(canonical_rules) == len(discovered)
    assert all(rule.head_variables() <= rule.body_variables() for rule in discovered)

    evaluator = SQLiteRuleEvaluator(database_path, relation_disjoint=True)
    try:
        evaluation = evaluator.evaluate(expected)
    finally:
        evaluator.close()

    assert evaluation.support == 1
    assert evaluation.predictions == 1
    assert evaluation.confidence == 1.0


def test_discovery_initialization_is_read_only(tmp_path: Path) -> None:
    database_path = tmp_path / "read_only_contract.db"
    _write_fk_database(database_path)
    database = AlchemyUtility(
        f"sqlite:///{database_path}",
        create_index=False,
        create_csv=False,
        create_tsv=False,
        get_data=False,
    )
    try:

        def fail_if_called(*_args, **_kwargs):
            raise AssertionError("discovery must not create compatibility indexes")

        database.create_composed_indexes = fail_if_called
        results = tmp_path / "init"
        results.mkdir()
        init(database, max_nb_occurrence=2, results_path=str(results))
    finally:
        database.close()


def test_fk_metadata_keeps_multiple_targets_for_one_column(tmp_path: Path) -> None:
    database_path = tmp_path / "multi_target_fk.db"
    connection = sqlite3.connect(database_path)
    try:
        connection.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE first_target (id INTEGER PRIMARY KEY);
            CREATE TABLE second_target (id INTEGER PRIMARY KEY);
            CREATE TABLE source (
                id INTEGER PRIMARY KEY,
                value INTEGER,
                FOREIGN KEY(value) REFERENCES first_target(id),
                FOREIGN KEY(value) REFERENCES second_target(id)
            );
            """
        )
        connection.commit()
    finally:
        connection.close()

    database = AlchemyUtility(
        f"sqlite:///{database_path}",
        create_index=False,
        create_csv=False,
        create_tsv=False,
        get_data=False,
    )
    try:
        attributes = Attribute.generate_attributes(database)
        pairs = discovery._foreign_key_attribute_pairs(attributes, database)
        assert pairs is not None
        assert database.check_foreign_key_silently("source", "value", "first_target", "id") is True
        assert database.check_foreign_key_silently("source", "value", "second_target", "id") is True
        pair_names = {(left.table, left.name, right.table, right.name) for left, right in pairs}
        assert pair_names == {
            ("source", "value", "first_target", "id"),
            ("source", "value", "second_target", "id"),
        }
    finally:
        database.close()


def _reference_graph(jia_list) -> ConstraintGraph:
    """Build the pre-optimization pairwise graph for conformance comparison."""
    graph = ConstraintGraph()
    nodes = sorted(set(jia_list))
    for node in nodes:
        graph.add_node(node)
    for index, source in enumerate(nodes):
        for target in nodes[index + 1 :]:
            if source.is_connected(target):
                graph.add_edge(source, target)
    return graph


def _enumerated_rule_keys(graph, database, mapper) -> set[tuple]:
    keys = set()
    for candidate, (body, head), _metrics in dfs(
        graph,
        None,
        discovery.path_pruning,
        database,
        mapper,
        max_table=3,
        max_vars=2,
        support_threshold=1,
    ):
        formula = instantiate_tgd(candidate, (body, head), mapper)
        parsed = parse_formula(formula)
        assert parsed.head is not None
        assert parsed.head_variables() <= parsed.body_variables()
        keys.add(parsed.canonical_key())
    return keys


def test_optimized_graph_preserves_exhaustive_bounded_rule_set(tmp_path: Path) -> None:
    """The indexed graph must enumerate exactly the old bounded hypothesis class."""
    database_path = tmp_path / "exhaustive_contract.db"
    _write_fk_database(database_path)
    database = AlchemyUtility(
        f"sqlite:///{database_path}",
        create_index=False,
        create_csv=False,
        create_tsv=False,
        get_data=False,
    )
    try:
        (tmp_path / "init").mkdir()
        discovery.APPLY_DISJOINT = True
        discovery.APPLY_FULL_JOINABILITY = False
        discovery.SUPPORT_THRESHOLD = 1
        optimized, mapper, jia_list = init(
            database,
            max_nb_occurrence=2,
            results_path=str(tmp_path / "init"),
        )
        reference = _reference_graph(jia_list)

        attributes = Attribute.generate_attributes(database)
        pairwise_jias = set()
        for index, first in enumerate(attributes):
            for second in attributes[index:]:
                if not first.is_compatible(second, db_inspector=database):
                    continue
                for first_occurrence in range(2):
                    for second_occurrence in range(2):
                        pairwise_jias.add(
                            JoinableIndexedAttributes(
                                mapper.attribute_to_indexed(first, first_occurrence),
                                mapper.attribute_to_indexed(second, second_occurrence),
                            )
                        )
        assert set(jia_list) == pairwise_jias

        assert optimized.nodes == reference.nodes
        assert {(source, target) for source, targets in optimized.edges.items() for target in targets} == {
            (source, target) for source, targets in reference.edges.items() for target in targets
        }
        assert all(optimized.all_neighbors(node) == reference.all_neighbors(node) for node in optimized.nodes)

        optimized_rules = _enumerated_rule_keys(optimized, database, mapper)
        reference_rules = _enumerated_rule_keys(reference, database, mapper)

        assert optimized_rules == reference_rules
        assert optimized_rules
    finally:
        database.close()
