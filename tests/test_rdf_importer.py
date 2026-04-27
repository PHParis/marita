from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

from mahilda.database.rdf_importer import import_rdf_benchmark

if TYPE_CHECKING:
    from pathlib import Path


def _write_tiny_ttl(path: Path) -> None:
    path.write_text(
        """
@prefix ex: <http://example.org/> .
@prefix schema: <http://schema.org/> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

ex:PersonShape a sh:NodeShape ;
    sh:property ex:birthPlaceShape .

ex:birthPlaceShape
    sh:path schema:birthPlace ;
    sh:class schema:Place ;
    sh:maxCount 1 .

schema:Person rdfs:subClassOf schema:Thing .

ex:Alice a schema:Person ;
    rdfs:label "Alice"@en ;
    schema:birthDate "1990-01-01"^^xsd:date ;
    schema:birthPlace ex:Paris .

ex:Paris a schema:Place .
""".strip()
        + "\n",
        encoding="utf-8",
    )


def _table_names(db_path: Path) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        return {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
            if not str(row[0]).startswith("sqlite_")
        }


def _count_rows(db_path: Path, table_name: str) -> int:
    with sqlite3.connect(db_path) as conn:
        return int(conn.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0])


def test_import_rdf_benchmark_materializes_core_and_ontology_lite(tmp_path: Path) -> None:
    ttl_path = tmp_path / "sample.ttl"
    output_dir = tmp_path / "out"
    _write_tiny_ttl(ttl_path)

    result = import_rdf_benchmark(
        input_path=ttl_path,
        output_dir=output_dir,
        variants=["core", "ontology-lite"],
        dataset_name="sample",
    )

    assert result.artifacts.full_db.exists()
    assert result.artifacts.manifest.exists()
    assert result.artifacts.report.exists()

    manifest = result.manifest
    assert manifest["category_counts"]["instance_type"] == 2
    assert manifest["category_counts"]["abox_object_fact"] == 1
    assert manifest["category_counts"]["abox_datatype_fact"] == 1
    assert manifest["category_counts"]["class_hierarchy"] == 1
    assert manifest["category_counts"]["annotation"] == 1

    core_db = result.artifacts.variant_dbs["core"]
    ontology_db = result.artifacts.variant_dbs["ontology-lite"]
    assert "rdfs_subClassOf" not in _table_names(core_db)
    assert "rdfs_subClassOf" in _table_names(ontology_db)
    assert {"entity", "literal", "class", "rdf_type", "schema_birthDate", "schema_birthPlace"}.issubset(
        _table_names(core_db)
    )
    assert _count_rows(core_db, "rdf_type") == 2
    assert _count_rows(core_db, "schema_birthPlace") == 1
    assert _count_rows(core_db, "schema_birthDate") == 1
    assert _count_rows(ontology_db, "rdfs_subClassOf") == 1

    with sqlite3.connect(core_db) as conn:
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []

    core_tsv = result.artifacts.variant_tsvs["core"]
    ontology_tsv = result.artifacts.variant_tsvs["ontology-lite"]
    assert core_tsv.exists()
    assert ontology_tsv.exists()
    assert len(core_tsv.read_text(encoding="utf-8").splitlines()) == 4
    assert len(ontology_tsv.read_text(encoding="utf-8").splitlines()) == 5


def test_import_rdf_benchmark_preserves_shacl_metadata_in_archive(tmp_path: Path) -> None:
    ttl_path = tmp_path / "sample.ttl"
    output_dir = tmp_path / "out"
    _write_tiny_ttl(ttl_path)

    result = import_rdf_benchmark(ttl_path, output_dir, variants=["core"], dataset_name="sample")

    with sqlite3.connect(result.artifacts.full_db) as conn:
        shape_count = conn.execute("SELECT COUNT(*) FROM shape_property").fetchone()[0]
        max_count = conn.execute(
            "SELECT max_count FROM shape_property WHERE path_iri = 'http://schema.org/birthPlace'"
        ).fetchone()[0]

    assert shape_count == 1
    assert max_count == 1
