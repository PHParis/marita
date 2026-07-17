from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

from mahilda.algorithms.mahilda import MAHILDA
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
