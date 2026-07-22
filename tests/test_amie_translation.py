from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from mahilda.audit.amie_translation import (
    build_rdb_kg_mapping,
    parse_amie_display,
    translate_amie_source,
    validate_mapping_artifacts,
    validate_tsv_predicates,
    write_mapping_manifest,
)
from mahilda.audit.models import RuleKind, SourceRule


def _database(tmp_path: Path) -> Path:
    path = tmp_path / "tiny.db"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        PRAGMA foreign_keys = ON;
        CREATE TABLE parent (id INTEGER PRIMARY KEY, label TEXT);
        CREATE TABLE child (
            id INTEGER PRIMARY KEY,
            parent_id INTEGER NOT NULL REFERENCES parent(id),
            name TEXT
        );
        INSERT INTO parent VALUES (1, 'p');
        INSERT INTO child VALUES (10, 1, 'c');
        """
    )
    connection.commit()
    connection.close()
    return path


def _source(display: str) -> SourceRule:
    return SourceRule(
        algorithm="AMIE3",
        database="tiny",
        source_path=Path("AMIE3_tiny_results.json"),
        index=0,
        display=display,
        payload={},
    )


def test_mapping_matches_exported_predicates(tmp_path: Path) -> None:
    mapping = build_rdb_kg_mapping(_database(tmp_path))
    assert set(mapping.predicates) == {
        "child.name",
        "child.parent_id",
        "parent.label",
    }
    assert mapping.predicates["child.parent_id"].referenced_table == "parent"
    assert mapping.to_manifest()["version"] == 1
    json.dumps(mapping.to_manifest())


def test_parse_amie_display_ignores_metrics() -> None:
    body, head = parse_amie_display("?a child.parent_id ?b ?b parent.label ?c => ?a child.name ?c\t1\t0.5\t9")
    assert len(body) == 2
    assert len(head) == 1


def test_translate_fk_object_reused_as_subject(tmp_path: Path) -> None:
    parsed = translate_amie_source(
        _source("?a child.parent_id ?b ?b parent.label ?c => ?a child.name ?c\t1\t1"),
        _database(tmp_path),
    )
    dependency = parsed.relational_dependency()
    assert parsed.unsupported_reason is None
    assert dependency is not None
    assert dependency.rule_kind() == RuleKind.HORN
    child, parent = dependency.body
    child_terms = dict(child.terms)
    parent_terms = dict(parent.terms)
    assert child_terms["parent_id"] == parent_terms["id"]
    assert dict(dependency.head[0].terms)["name"] == parent_terms["label"]


def test_translate_merges_same_subject_and_table(tmp_path: Path) -> None:
    parsed = translate_amie_source(
        _source("?a child.parent_id ?b ?a child.name ?c => ?b parent.label ?c"),
        _database(tmp_path),
    )
    dependency = parsed.relational_dependency()
    assert dependency is not None
    assert len(dependency.body) == 1
    assert set(dict(dependency.body[0].terms)) == {"id", "name", "parent_id"}


def test_translate_preserves_same_row_across_body_and_head(tmp_path: Path) -> None:
    parsed = translate_amie_source(
        _source("?a child.parent_id ?b => ?a child.name ?c"),
        _database(tmp_path),
    )
    dependency = parsed.relational_dependency()
    assert dependency is not None
    assert dict(dependency.body[0].terms)["id"] == dict(dependency.head[0].terms)["id"]
    assert dependency.body[0].occurrence == 0
    assert dependency.head[0].occurrence == 1


def test_translate_rejects_constants_and_unknown_predicates(tmp_path: Path) -> None:
    database = _database(tmp_path)
    constant = translate_amie_source(_source('?a child.name "c" => ?a child.name ?b'), database)
    unknown = translate_amie_source(_source("?a missing.value ?b => ?a child.name ?b"), database)
    assert constant.unsupported_reason == "amie_constants_not_supported"
    assert unknown.unsupported_reason == "amie_unknown_predicate:missing.value"


def test_mapping_reports_sanitization_collision(tmp_path: Path) -> None:
    path = tmp_path / "collision.db"
    connection = sqlite3.connect(path)
    connection.executescript(
        'CREATE TABLE "a-b" (id INTEGER PRIMARY KEY, value TEXT);'
        'CREATE TABLE "a_b" (id INTEGER PRIMARY KEY, value TEXT);'
    )
    connection.close()
    mapping = build_rdb_kg_mapping(path)
    assert mapping.collisions["a_b.value"] == (("a-b", "value"), ("a_b", "value"))
    parsed = translate_amie_source(_source("?a a_b.value ?b => ?a a_b.value ?b"), path)
    assert parsed.unsupported_reason == "amie_predicate_collision:a_b.value"


def test_mapping_expands_composite_primary_key(tmp_path: Path) -> None:
    path = tmp_path / "composite.db"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE pair (left_id INTEGER, right_id INTEGER, value TEXT, PRIMARY KEY(left_id, right_id))"
    )
    connection.close()
    parsed = translate_amie_source(_source("?a pair.value ?b => ?a pair.value ?b"), path)
    dependency = parsed.relational_dependency()
    assert dependency is not None
    assert set(dict(dependency.body[0].terms)) == {"left_id", "right_id", "value"}


def test_validate_tsv_predicates(tmp_path: Path) -> None:
    mapping = build_rdb_kg_mapping(_database(tmp_path))
    path = tmp_path / "tiny.tsv"
    path.write_text('child_10\tchild.name\t"c"\nchild_10\tunknown.value\tx\n', encoding="utf-8")
    assert validate_tsv_predicates(mapping, path) == ("unknown.value",)


def test_mapping_manifest_validates_database_and_tsv_hashes(tmp_path: Path) -> None:
    database = _database(tmp_path)
    tsv = tmp_path / "tiny.tsv"
    tsv.write_text('child_10\tchild.name\t"c"\n', encoding="utf-8")
    manifest = tmp_path / "tiny.mapping.json"
    write_mapping_manifest(database, manifest, tsv_path=tsv)

    assert validate_mapping_artifacts(database, tsv, manifest) is None
    tsv.write_text('child_10\tchild.name\t"changed"\n', encoding="utf-8")
    validate_mapping_artifacts.cache_clear()
    assert validate_mapping_artifacts(database, tsv, manifest) == "amie_mapping_tsv_hash_mismatch"
