from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import rdflib
from rdflib import BNode, Graph, Literal, URIRef

if TYPE_CHECKING:
    from rdflib.term import Node

RDF_NS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
RDFS_NS = "http://www.w3.org/2000/01/rdf-schema#"
OWL_NS = "http://www.w3.org/2002/07/owl#"
SH_NS = "http://www.w3.org/ns/shacl#"
SCHEMA_NS = "http://schema.org/"
YAGO_SCHEMA_NS = "http://yago-knowledge.org/schema#"
PROV_NS = "http://www.w3.org/ns/prov#"

RDF_TYPE = f"{RDF_NS}type"
RDFS_CLASS = f"{RDFS_NS}Class"
RDFS_SUBCLASS_OF = f"{RDFS_NS}subClassOf"
RDFS_LABEL = f"{RDFS_NS}label"
RDFS_COMMENT = f"{RDFS_NS}comment"
RDF_PROPERTY = f"{RDF_NS}Property"
OWL_CLASS = f"{OWL_NS}Class"
OWL_OBJECT_PROPERTY = f"{OWL_NS}ObjectProperty"
OWL_DATATYPE_PROPERTY = f"{OWL_NS}DatatypeProperty"
OWL_DISJOINT_WITH = f"{OWL_NS}disjointWith"
SH_NODE_SHAPE = f"{SH_NS}NodeShape"
SH_PROPERTY_SHAPE = f"{SH_NS}PropertyShape"
SH_PATH = URIRef(f"{SH_NS}path")
SH_CLASS = URIRef(f"{SH_NS}class")
SH_DATATYPE = URIRef(f"{SH_NS}datatype")
SH_OR = URIRef(f"{SH_NS}or")
SH_MIN_COUNT = URIRef(f"{SH_NS}minCount")
SH_MAX_COUNT = URIRef(f"{SH_NS}maxCount")

ARCHIVE_TABLES = {
    "namespace",
    "term",
    "literal",
    "statement",
    "shape_property",
    "transformation_decision",
    "import_metadata",
}

VARIANT_RESERVED_TABLES = {
    "class",
    "entity",
    "literal",
    "rdf_type",
    "rdfs_subClassOf",
}

ANNOTATION_PREDICATES = {
    RDFS_LABEL,
    RDFS_COMMENT,
    f"{SCHEMA_NS}alternateName",
    f"{SCHEMA_NS}description",
    f"{SCHEMA_NS}familyName",
    f"{SCHEMA_NS}givenName",
    f"{SCHEMA_NS}image",
    f"{SCHEMA_NS}name",
    f"{SCHEMA_NS}sameAs",
    f"{SCHEMA_NS}url",
    f"{YAGO_SCHEMA_NS}demonym",
}

PROVENANCE_PREDICATES = {
    f"{YAGO_SCHEMA_NS}fromClass",
    f"{YAGO_SCHEMA_NS}fromProperty",
}

SCHEMA_DECLARATION_OBJECTS = {
    RDF_PROPERTY,
    RDFS_CLASS,
    OWL_CLASS,
    OWL_DATATYPE_PROPERTY,
    OWL_OBJECT_PROPERTY,
    SH_NODE_SHAPE,
    SH_PROPERTY_SHAPE,
}

CATEGORY_DESCRIPTIONS = {
    "abox_datatype_fact": "Instance-level fact with a literal object.",
    "abox_object_fact": "Instance-level fact with a resource object.",
    "annotation": "Lexical, label, URL, image, or external identity metadata.",
    "class_disjointness": "Explicit class disjointness statement.",
    "class_hierarchy": "Explicit class hierarchy statement.",
    "instance_type": "Instance-to-class rdf:type assertion.",
    "provenance_or_mapping": "Source, provenance, or external mapping metadata.",
    "schema_declaration": "Class, property, node shape, or property shape declaration.",
    "shacl_shape": "SHACL shape metadata used for transformation and validation.",
    "unsupported_or_other": "Statement preserved in the archive but not selected for benchmark variants.",
}


@dataclass(frozen=True)
class RDFImportArtifacts:
    full_db: Path
    manifest: Path
    report: Path
    variant_dbs: dict[str, Path]
    variant_tsvs: dict[str, Path]


@dataclass(frozen=True)
class RDFImportResult:
    artifacts: RDFImportArtifacts
    manifest: dict[str, Any]


@dataclass(frozen=True)
class RDFImportVariant:
    name: str
    stem: str
    categories: frozenset[str]


@dataclass(frozen=True)
class NormalizedTerm:
    term_id: int
    value: str
    kind: str
    display: str


@dataclass(frozen=True)
class NormalizedLiteral:
    literal_id: int
    lexical_form: str
    datatype_iri: str | None
    lang: str | None
    parsed_value: str | None


@dataclass(frozen=True)
class StatementRecord:
    statement_id: int
    subject_id: int
    predicate_id: int
    object_kind: str
    object_id: int | None
    literal_id: int | None
    category: str
    statement_hash: str


@dataclass(frozen=True)
class PropertyShapeRecord:
    shape_iri: str
    path_iri: str | None
    class_iris: list[str]
    datatype_iris: list[str]
    min_count: int | None
    max_count: int | None
    labels: list[str]
    comments: list[str]


DEFAULT_VARIANTS: dict[str, RDFImportVariant] = {
    "core": RDFImportVariant(
        name="core",
        stem="core",
        categories=frozenset({"abox_object_fact", "abox_datatype_fact", "instance_type"}),
    ),
    "ontology-lite": RDFImportVariant(
        name="ontology-lite",
        stem="ontology_lite",
        categories=frozenset({"abox_object_fact", "abox_datatype_fact", "instance_type", "class_hierarchy"}),
    ),
}


class _TermRegistry:
    def __init__(self) -> None:
        self._terms_by_value: dict[str, NormalizedTerm] = {}
        self._literals_by_key: dict[tuple[str, str | None, str | None], NormalizedLiteral] = {}

    @property
    def terms(self) -> list[NormalizedTerm]:
        return sorted(self._terms_by_value.values(), key=lambda term: term.term_id)

    @property
    def literals(self) -> list[NormalizedLiteral]:
        return sorted(self._literals_by_key.values(), key=lambda literal: literal.literal_id)

    def term_id(self, term: Node) -> int:
        value = self._term_value(term)
        existing = self._terms_by_value.get(value)
        if existing is not None:
            return existing.term_id

        normalized = NormalizedTerm(
            term_id=len(self._terms_by_value) + 1,
            value=value,
            kind=self._term_kind(term),
            display=self._term_display(term),
        )
        self._terms_by_value[value] = normalized
        return normalized.term_id

    def literal_id(self, literal: Literal) -> int:
        datatype_iri = str(literal.datatype) if literal.datatype is not None else None
        lang = str(literal.language) if literal.language is not None else None
        lexical_form = str(literal)
        key = (lexical_form, datatype_iri, lang)
        existing = self._literals_by_key.get(key)
        if existing is not None:
            return existing.literal_id

        normalized = NormalizedLiteral(
            literal_id=len(self._literals_by_key) + 1,
            lexical_form=lexical_form,
            datatype_iri=datatype_iri,
            lang=lang,
            parsed_value=_parse_literal_value(literal),
        )
        self._literals_by_key[key] = normalized
        return normalized.literal_id

    def term_by_id(self, term_id: int) -> NormalizedTerm:
        for term in self._terms_by_value.values():
            if term.term_id == term_id:
                return term
        raise KeyError(f"Unknown term id: {term_id}")

    def literal_by_id(self, literal_id: int) -> NormalizedLiteral:
        for literal in self._literals_by_key.values():
            if literal.literal_id == literal_id:
                return literal
        raise KeyError(f"Unknown literal id: {literal_id}")

    @staticmethod
    def _term_value(term: Node) -> str:
        if isinstance(term, BNode):
            return f"_:{term}"
        return str(term)

    @staticmethod
    def _term_kind(term: Node) -> str:
        if isinstance(term, BNode):
            return "blank_node"
        if isinstance(term, URIRef):
            return "iri"
        return "resource"

    @staticmethod
    def _term_display(term: Node) -> str:
        if isinstance(term, BNode):
            return f"_:{term}"
        return str(term)


def import_rdf_benchmark(
    input_path: str | Path,
    output_dir: str | Path,
    variants: list[str] | None = None,
    dataset_name: str | None = None,
) -> RDFImportResult:
    """Parse an RDF/Turtle file and write benchmark archive, SQLite, TSV, and report artifacts."""
    source_path = Path(input_path).expanduser().resolve()
    destination = Path(output_dir).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)

    selected_variants = _resolve_variants(variants)
    name = dataset_name or _sanitize_dataset_name(source_path.stem)

    graph = Graph()
    graph.parse(source_path, format="turtle")

    input_sha256 = _file_sha256(source_path)
    shape_terms = _collect_shape_terms(graph)
    property_shapes = _extract_property_shapes(graph)
    registry = _TermRegistry()
    records = _build_statement_records(graph, registry, shape_terms)

    full_db = destination / f"{name}_full.db"
    _write_archive_db(
        db_path=full_db,
        graph=graph,
        registry=registry,
        records=records,
        property_shapes=property_shapes,
        source_path=source_path,
        input_sha256=input_sha256,
        selected_variants=selected_variants,
    )

    variant_dbs: dict[str, Path] = {}
    variant_tsvs: dict[str, Path] = {}
    variant_reports: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    for variant in selected_variants:
        selected_records = _select_records(records, variant)
        variant_db = destination / f"{name}_{variant.stem}.db"
        variant_tsv = destination / f"{name}_{variant.stem}.tsv"
        materialization = _write_variant_db(variant_db, registry, selected_records, variant)
        tsv_report = _write_variant_tsv(variant_tsv, registry, selected_records)
        variant_dbs[variant.name] = variant_db
        variant_tsvs[variant.name] = variant_tsv
        variant_reports[variant.name] = {
            "categories": sorted(variant.categories),
            "db_path": str(variant_db),
            "tsv_path": str(variant_tsv),
            "selected_statement_count": len(selected_records),
            "table_counts": materialization["table_counts"],
            "predicate_tables": materialization["predicate_tables"],
            "tsv_row_count": tsv_report["row_count"],
            "tsv_sha256": tsv_report["sha256"],
            "foreign_key_violations": materialization["foreign_key_violations"],
        }
        if materialization["foreign_key_violations"]:
            warnings.append(f"{variant.name}: SQLite foreign key violations were found.")

    category_counts = Counter(record.category for record in records)
    predicate_counts = _predicate_counts_by_category(records, registry)
    manifest = {
        "input": str(source_path),
        "input_sha256": input_sha256,
        "dataset_name": name,
        "parser": {"name": "rdflib", "version": rdflib.__version__},
        "total_statements": len(records),
        "category_counts": dict(sorted(category_counts.items())),
        "predicate_counts_by_category": predicate_counts,
        "variants": variant_reports,
        "namespaces": _namespace_records(graph),
        "property_shape_count": len(property_shapes),
        "warnings": warnings,
    }

    manifest_path = destination / f"{name}_manifest.json"
    report_path = destination / f"{name}_report.md"
    _write_json(manifest_path, manifest)
    _write_report(report_path, manifest)

    return RDFImportResult(
        artifacts=RDFImportArtifacts(
            full_db=full_db,
            manifest=manifest_path,
            report=report_path,
            variant_dbs=variant_dbs,
            variant_tsvs=variant_tsvs,
        ),
        manifest=manifest,
    )


def _resolve_variants(variants: list[str] | None) -> list[RDFImportVariant]:
    requested = variants or ["core", "ontology-lite"]
    resolved = []
    for raw_name in requested:
        name = raw_name.strip()
        if not name:
            continue
        variant = DEFAULT_VARIANTS.get(name)
        if variant is None:
            supported = ", ".join(sorted(DEFAULT_VARIANTS))
            raise ValueError(f"Unsupported RDF benchmark variant '{name}'. Supported variants: {supported}.")
        resolved.append(variant)
    if not resolved:
        raise ValueError("At least one RDF benchmark variant must be requested.")
    return resolved


def _build_statement_records(graph: Graph, registry: _TermRegistry, shape_terms: set[str]) -> list[StatementRecord]:
    records: list[StatementRecord] = []
    for statement_id, (subject, predicate, obj) in enumerate(_sorted_triples(graph), start=1):
        subject_id = registry.term_id(subject)
        predicate_id = registry.term_id(predicate)
        category = _classify_statement(subject, predicate, obj, shape_terms)
        if isinstance(obj, Literal):
            literal_id = registry.literal_id(obj)
            object_id = None
            object_kind = "literal"
        else:
            literal_id = None
            object_id = registry.term_id(obj)
            object_kind = "resource"

        records.append(
            StatementRecord(
                statement_id=statement_id,
                subject_id=subject_id,
                predicate_id=predicate_id,
                object_kind=object_kind,
                object_id=object_id,
                literal_id=literal_id,
                category=category,
                statement_hash=_statement_hash(subject, predicate, obj),
            )
        )
    return records


def _sorted_triples(graph: Graph) -> list[tuple[Node, Node, Node]]:
    return sorted(graph, key=lambda triple: tuple(_node_sort_key(node) for node in triple))


def _node_sort_key(node: Node) -> str:
    if isinstance(node, Literal):
        datatype = str(node.datatype) if node.datatype is not None else ""
        language = str(node.language) if node.language is not None else ""
        return f"literal\0{str(node)}\0{datatype}\0{language}"
    if isinstance(node, BNode):
        return f"blank\0{node}"
    return f"resource\0{node}"


def _classify_statement(subject: Node, predicate: Node, obj: Node, shape_terms: set[str]) -> str:
    subject_value = _node_value(subject)
    predicate_value = _node_value(predicate)
    object_value = _node_value(obj)

    if predicate_value == RDFS_SUBCLASS_OF:
        return "class_hierarchy"
    if predicate_value == OWL_DISJOINT_WITH:
        return "class_disjointness"
    if predicate_value == RDF_TYPE and object_value in SCHEMA_DECLARATION_OBJECTS:
        return "schema_declaration"
    if _is_shacl_statement(subject_value, predicate_value, object_value, shape_terms):
        return "shacl_shape"
    if _is_provenance_statement(predicate_value):
        return "provenance_or_mapping"
    if predicate_value in ANNOTATION_PREDICATES:
        return "annotation"
    if predicate_value == RDF_TYPE:
        return "instance_type"
    if isinstance(obj, Literal):
        return "abox_datatype_fact"
    if isinstance(obj, (URIRef, BNode)):
        return "abox_object_fact"
    return "unsupported_or_other"


def _is_shacl_statement(subject: str, predicate: str, obj: str, shape_terms: set[str]) -> bool:
    return (
        subject in shape_terms
        or obj in shape_terms
        or predicate.startswith(SH_NS)
        or obj in {SH_NODE_SHAPE, SH_PROPERTY_SHAPE}
    )


def _is_provenance_statement(predicate: str) -> bool:
    return predicate in PROVENANCE_PREDICATES or predicate.startswith(PROV_NS)


def _collect_shape_terms(graph: Graph) -> set[str]:
    terms: set[str] = set()
    for subject in graph.subjects(SH_PATH, None):
        terms.add(_node_value(subject))
    for subject in graph.subjects(URIRef(RDF_TYPE), URIRef(SH_NODE_SHAPE)):
        terms.add(_node_value(subject))
    for subject in graph.subjects(URIRef(RDF_TYPE), URIRef(SH_PROPERTY_SHAPE)):
        terms.add(_node_value(subject))
    for obj in graph.objects(None, URIRef(f"{SH_NS}property")):
        terms.add(_node_value(obj))
    return terms


def _extract_property_shapes(graph: Graph) -> list[PropertyShapeRecord]:
    records: list[PropertyShapeRecord] = []
    for shape in sorted(graph.subjects(SH_PATH, None), key=_node_sort_key):
        paths = [_node_value(value) for value in graph.objects(shape, SH_PATH)]
        class_iris = [_node_value(value) for value in graph.objects(shape, SH_CLASS)]
        datatype_iris = [_node_value(value) for value in graph.objects(shape, SH_DATATYPE)]
        for or_node in graph.objects(shape, SH_OR):
            for option in graph.items(or_node):
                class_iris.extend(_node_value(value) for value in graph.objects(option, SH_CLASS))
                datatype_iris.extend(_node_value(value) for value in graph.objects(option, SH_DATATYPE))

        records.append(
            PropertyShapeRecord(
                shape_iri=_node_value(shape),
                path_iri=paths[0] if paths else None,
                class_iris=sorted(set(class_iris)),
                datatype_iris=sorted(set(datatype_iris)),
                min_count=_literal_int(next(iter(graph.objects(shape, SH_MIN_COUNT)), None)),
                max_count=_literal_int(next(iter(graph.objects(shape, SH_MAX_COUNT)), None)),
                labels=_literal_values(graph.objects(shape, URIRef(RDFS_LABEL))),
                comments=_literal_values(graph.objects(shape, URIRef(RDFS_COMMENT))),
            )
        )
    return records


def _write_archive_db(
    db_path: Path,
    graph: Graph,
    registry: _TermRegistry,
    records: list[StatementRecord],
    property_shapes: list[PropertyShapeRecord],
    source_path: Path,
    input_sha256: str,
    selected_variants: list[RDFImportVariant],
) -> None:
    _replace_file(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(
            """
            CREATE TABLE namespace (
                prefix TEXT PRIMARY KEY,
                iri TEXT NOT NULL
            );
            CREATE TABLE term (
                term_id INTEGER PRIMARY KEY,
                value TEXT NOT NULL UNIQUE,
                kind TEXT NOT NULL,
                display TEXT NOT NULL
            );
            CREATE TABLE literal (
                literal_id INTEGER PRIMARY KEY,
                lexical_form TEXT NOT NULL,
                datatype_iri TEXT,
                lang TEXT,
                parsed_value TEXT
            );
            CREATE TABLE statement (
                statement_id INTEGER PRIMARY KEY,
                subject_id INTEGER NOT NULL REFERENCES term(term_id),
                predicate_id INTEGER NOT NULL REFERENCES term(term_id),
                object_kind TEXT NOT NULL,
                object_id INTEGER REFERENCES term(term_id),
                literal_id INTEGER REFERENCES literal(literal_id),
                category TEXT NOT NULL,
                statement_hash TEXT NOT NULL UNIQUE
            );
            CREATE TABLE shape_property (
                shape_iri TEXT PRIMARY KEY,
                path_iri TEXT,
                class_iris TEXT NOT NULL,
                datatype_iris TEXT NOT NULL,
                min_count INTEGER,
                max_count INTEGER,
                labels TEXT NOT NULL,
                comments TEXT NOT NULL
            );
            CREATE TABLE transformation_decision (
                variant TEXT NOT NULL,
                category TEXT NOT NULL,
                included INTEGER NOT NULL,
                reason TEXT NOT NULL,
                PRIMARY KEY (variant, category)
            );
            CREATE TABLE import_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        conn.executemany(
            "INSERT INTO namespace(prefix, iri) VALUES (?, ?)",
            _namespace_records(graph),
        )
        conn.executemany(
            "INSERT INTO term(term_id, value, kind, display) VALUES (?, ?, ?, ?)",
            [(term.term_id, term.value, term.kind, term.display) for term in registry.terms],
        )
        conn.executemany(
            "INSERT INTO literal(literal_id, lexical_form, datatype_iri, lang, parsed_value) VALUES (?, ?, ?, ?, ?)",
            [
                (literal.literal_id, literal.lexical_form, literal.datatype_iri, literal.lang, literal.parsed_value)
                for literal in registry.literals
            ],
        )
        conn.executemany(
            """
            INSERT INTO statement(
                statement_id, subject_id, predicate_id, object_kind, object_id, literal_id, category, statement_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    record.statement_id,
                    record.subject_id,
                    record.predicate_id,
                    record.object_kind,
                    record.object_id,
                    record.literal_id,
                    record.category,
                    record.statement_hash,
                )
                for record in records
            ],
        )
        conn.executemany(
            """
            INSERT INTO shape_property(
                shape_iri, path_iri, class_iris, datatype_iris, min_count, max_count, labels, comments
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    shape.shape_iri,
                    shape.path_iri,
                    json.dumps(shape.class_iris, sort_keys=True),
                    json.dumps(shape.datatype_iris, sort_keys=True),
                    shape.min_count,
                    shape.max_count,
                    json.dumps(shape.labels, sort_keys=True),
                    json.dumps(shape.comments, sort_keys=True),
                )
                for shape in property_shapes
            ],
        )
        decisions = []
        for variant in selected_variants:
            for category in sorted(CATEGORY_DESCRIPTIONS):
                included = int(category in variant.categories)
                reason = "selected for variant" if included else "preserved in archive only"
                decisions.append((variant.name, category, included, reason))
        conn.executemany(
            "INSERT INTO transformation_decision(variant, category, included, reason) VALUES (?, ?, ?, ?)",
            decisions,
        )
        conn.executemany(
            "INSERT INTO import_metadata(key, value) VALUES (?, ?)",
            [
                ("input_path", str(source_path)),
                ("input_sha256", input_sha256),
                ("parser", "rdflib"),
                ("parser_version", rdflib.__version__),
                ("statement_count", str(len(records))),
            ],
        )


def _write_variant_db(
    db_path: Path,
    registry: _TermRegistry,
    records: list[StatementRecord],
    variant: RDFImportVariant,
) -> dict[str, Any]:
    _replace_file(db_path)
    predicate_tables: dict[str, str] = {}
    used_table_names = set(ARCHIVE_TABLES) | set(VARIANT_RESERVED_TABLES)
    table_counts: dict[str, int] = {}
    object_records = [
        record for record in records if record.category == "abox_object_fact" and record.object_id is not None
    ]
    literal_records = [
        record for record in records if record.category == "abox_datatype_fact" and record.literal_id is not None
    ]
    type_records = [record for record in records if record.category == "instance_type" and record.object_id is not None]
    hierarchy_records = [
        record for record in records if record.category == "class_hierarchy" and record.object_id is not None
    ]

    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(
            """
            CREATE TABLE entity (
                entity_id INTEGER PRIMARY KEY,
                iri TEXT NOT NULL UNIQUE,
                term_kind TEXT NOT NULL
            );
            CREATE TABLE literal (
                literal_id INTEGER PRIMARY KEY,
                lexical_form TEXT NOT NULL,
                datatype_iri TEXT,
                lang TEXT,
                parsed_value TEXT
            );
            CREATE TABLE class (
                class_id INTEGER PRIMARY KEY,
                iri TEXT NOT NULL UNIQUE
            );
            CREATE TABLE rdf_type (
                statement_id INTEGER PRIMARY KEY,
                subject_id INTEGER NOT NULL REFERENCES entity(entity_id),
                class_id INTEGER NOT NULL REFERENCES class(class_id)
            );
            """
        )
        if "class_hierarchy" in variant.categories:
            conn.executescript(
                """
                CREATE TABLE rdfs_subClassOf (
                    statement_id INTEGER PRIMARY KEY,
                    subclass_id INTEGER NOT NULL REFERENCES class(class_id),
                    superclass_id INTEGER NOT NULL REFERENCES class(class_id)
                );
                """
            )

        entity_ids, class_ids, literal_ids = _selected_ids(records)
        _insert_entities(conn, registry, entity_ids)
        _insert_literals(conn, registry, literal_ids)
        _insert_classes(conn, registry, class_ids)

        conn.executemany(
            "INSERT INTO rdf_type(statement_id, subject_id, class_id) VALUES (?, ?, ?)",
            [(record.statement_id, record.subject_id, record.object_id) for record in type_records],
        )

        if "class_hierarchy" in variant.categories:
            conn.executemany(
                "INSERT INTO rdfs_subClassOf(statement_id, subclass_id, superclass_id) VALUES (?, ?, ?)",
                [(record.statement_id, record.subject_id, record.object_id) for record in hierarchy_records],
            )

        for record in object_records:
            predicate_value = registry.term_by_id(record.predicate_id).value
            table_name = _predicate_table_name(predicate_value, "object", predicate_tables, used_table_names)
            _ensure_object_table(conn, table_name)
            conn.execute(
                f"INSERT INTO {_quote_identifier(table_name)}(statement_id, subject_id, object_id) VALUES (?, ?, ?)",
                (record.statement_id, record.subject_id, record.object_id),
            )

        for record in literal_records:
            predicate_value = registry.term_by_id(record.predicate_id).value
            table_name = _predicate_table_name(predicate_value, "literal", predicate_tables, used_table_names)
            _ensure_literal_table(conn, table_name)
            literal = registry.literal_by_id(record.literal_id or 0)
            conn.execute(
                f"""
                INSERT INTO {_quote_identifier(table_name)}(
                    statement_id, subject_id, literal_id, raw_lexical, parsed_value
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (record.statement_id, record.subject_id, record.literal_id, literal.lexical_form, literal.parsed_value),
            )

        _create_variant_indexes(conn, sorted(used_table_names - ARCHIVE_TABLES), variant)
        table_counts = _table_counts(conn)
        foreign_key_violations = conn.execute("PRAGMA foreign_key_check").fetchall()

    return {
        "table_counts": table_counts,
        "predicate_tables": dict(sorted(predicate_tables.items())),
        "foreign_key_violations": [tuple(row) for row in foreign_key_violations],
    }


def _selected_ids(records: list[StatementRecord]) -> tuple[set[int], set[int], set[int]]:
    entity_ids: set[int] = set()
    class_ids: set[int] = set()
    literal_ids: set[int] = set()
    for record in records:
        if record.category in {"abox_object_fact", "abox_datatype_fact", "instance_type"}:
            entity_ids.add(record.subject_id)
        if record.category == "abox_object_fact" and record.object_id is not None:
            entity_ids.add(record.object_id)
        if record.category == "abox_datatype_fact" and record.literal_id is not None:
            literal_ids.add(record.literal_id)
        if record.category == "instance_type" and record.object_id is not None:
            class_ids.add(record.object_id)
        if record.category == "class_hierarchy":
            class_ids.add(record.subject_id)
            if record.object_id is not None:
                class_ids.add(record.object_id)
    return entity_ids, class_ids, literal_ids


def _insert_entities(conn: sqlite3.Connection, registry: _TermRegistry, entity_ids: set[int]) -> None:
    conn.executemany(
        "INSERT INTO entity(entity_id, iri, term_kind) VALUES (?, ?, ?)",
        [(term.term_id, term.value, term.kind) for term in registry.terms if term.term_id in entity_ids],
    )


def _insert_literals(conn: sqlite3.Connection, registry: _TermRegistry, literal_ids: set[int]) -> None:
    conn.executemany(
        "INSERT INTO literal(literal_id, lexical_form, datatype_iri, lang, parsed_value) VALUES (?, ?, ?, ?, ?)",
        [
            (literal.literal_id, literal.lexical_form, literal.datatype_iri, literal.lang, literal.parsed_value)
            for literal in registry.literals
            if literal.literal_id in literal_ids
        ],
    )


def _insert_classes(conn: sqlite3.Connection, registry: _TermRegistry, class_ids: set[int]) -> None:
    conn.executemany(
        "INSERT INTO class(class_id, iri) VALUES (?, ?)",
        [(term.term_id, term.value) for term in registry.terms if term.term_id in class_ids],
    )


def _ensure_object_table(conn: sqlite3.Connection, table_name: str) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_quote_identifier(table_name)} (
            statement_id INTEGER PRIMARY KEY,
            subject_id INTEGER NOT NULL REFERENCES entity(entity_id),
            object_id INTEGER NOT NULL REFERENCES entity(entity_id)
        )
        """
    )


def _ensure_literal_table(conn: sqlite3.Connection, table_name: str) -> None:
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_quote_identifier(table_name)} (
            statement_id INTEGER PRIMARY KEY,
            subject_id INTEGER NOT NULL REFERENCES entity(entity_id),
            literal_id INTEGER NOT NULL REFERENCES literal(literal_id),
            raw_lexical TEXT NOT NULL,
            parsed_value TEXT
        )
        """
    )


def _create_variant_indexes(conn: sqlite3.Connection, table_names: list[str], variant: RDFImportVariant) -> None:
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rdf_type_subject ON rdf_type(subject_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rdf_type_class ON rdf_type(class_id)")
    if "class_hierarchy" in variant.categories:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rdfs_subClassOf_subclass ON rdfs_subClassOf(subclass_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rdfs_subClassOf_superclass ON rdfs_subClassOf(superclass_id)")
    for table_name in table_names:
        if table_name in {"entity", "literal", "class", "rdf_type", "rdfs_subClassOf"}:
            continue
        quoted = _quote_identifier(table_name)
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS {_quote_identifier(f'idx_{table_name}_subject')} ON {quoted}(subject_id)"
        )
        columns = [row[1] for row in conn.execute(f"PRAGMA table_info({quoted})").fetchall()]
        if "object_id" in columns:
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS {_quote_identifier(f'idx_{table_name}_object')} ON {quoted}(object_id)"
            )
        if "literal_id" in columns:
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS {_quote_identifier(f'idx_{table_name}_literal')} ON {quoted}(literal_id)"
            )


def _write_variant_tsv(path: Path, registry: _TermRegistry, records: list[StatementRecord]) -> dict[str, Any]:
    with path.open("w", encoding="utf-8", newline="") as handle:
        for record in records:
            subject = registry.term_by_id(record.subject_id).value
            predicate = registry.term_by_id(record.predicate_id).value
            if record.object_kind == "literal":
                obj = f"literal:{record.literal_id}"
            elif record.object_id is not None:
                obj = registry.term_by_id(record.object_id).value
            else:
                continue
            handle.write(f"{subject}\t{predicate}\t{obj}\n")
    return {"row_count": len(records), "sha256": _file_sha256(path)}


def _select_records(records: list[StatementRecord], variant: RDFImportVariant) -> list[StatementRecord]:
    return [record for record in records if record.category in variant.categories]


def _predicate_counts_by_category(records: list[StatementRecord], registry: _TermRegistry) -> dict[str, dict[str, int]]:
    counts: dict[str, Counter[str]] = {}
    for record in records:
        predicate = registry.term_by_id(record.predicate_id).value
        counts.setdefault(record.category, Counter())[predicate] += 1
    return {category: dict(sorted(counter.items())) for category, counter in sorted(counts.items())}


def _table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    table_names = [
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name").fetchall()
        if not str(row[0]).startswith("sqlite_")
    ]
    return {
        table: int(conn.execute(f"SELECT COUNT(*) FROM {_quote_identifier(table)}").fetchone()[0])
        for table in table_names
    }


def _predicate_table_name(
    predicate_value: str,
    table_kind: str,
    mappings: dict[str, str],
    used_names: set[str],
) -> str:
    mapping_key = f"{table_kind}:{predicate_value}"
    existing = mappings.get(mapping_key)
    if existing is not None:
        return existing

    base_name = _sanitize_identifier(_compact_iri(predicate_value))
    if table_kind == "literal" and base_name in used_names:
        base_name = _sanitize_identifier(f"{base_name}_literal")
    table_name = base_name
    if table_name in used_names:
        table_name = f"{base_name}_{hashlib.sha256(predicate_value.encode('utf-8')).hexdigest()[:8]}"
    mappings[mapping_key] = table_name
    used_names.add(table_name)
    return table_name


def _compact_iri(value: str) -> str:
    namespace_prefixes = {
        RDF_NS: "rdf",
        RDFS_NS: "rdfs",
        OWL_NS: "owl",
        SH_NS: "sh",
        SCHEMA_NS: "schema",
        YAGO_SCHEMA_NS: "yago",
    }
    for namespace, prefix in namespace_prefixes.items():
        if value.startswith(namespace):
            return f"{prefix}_{value[len(namespace) :]}"
    return value.rsplit("/", 1)[-1].rsplit("#", 1)[-1]


def _sanitize_identifier(value: str) -> str:
    identifier = re.sub(r"[^0-9A-Za-z_]", "_", value).strip("_")
    if not identifier:
        identifier = "predicate"
    if identifier[0].isdigit():
        identifier = f"p_{identifier}"
    return identifier[:110]


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _namespace_records(graph: Graph) -> list[tuple[str, str]]:
    return sorted((str(prefix), str(namespace)) for prefix, namespace in graph.namespaces())


def _node_value(node: Node) -> str:
    if isinstance(node, BNode):
        return f"_:{node}"
    return str(node)


def _statement_hash(subject: Node, predicate: Node, obj: Node) -> str:
    encoded = "\x1f".join([_node_sort_key(subject), _node_sort_key(predicate), _node_sort_key(obj)])
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _parse_literal_value(literal: Literal) -> str | None:
    value = literal.toPython()
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    return str(literal)


def _literal_int(value: Node | None) -> int | None:
    if not isinstance(value, Literal):
        return None
    try:
        return int(str(value))
    except ValueError:
        return None


def _literal_values(values: Any) -> list[str]:
    return sorted(str(value) for value in values)


def _replace_file(path: Path) -> None:
    if path.exists():
        path.unlink()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sanitize_dataset_name(value: str) -> str:
    return _sanitize_identifier(value.replace("-", "_"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_report(path: Path, manifest: dict[str, Any]) -> None:
    lines = [
        "# YAGO RDF Import Report",
        "",
        f"Input: `{manifest['input']}`",
        f"Input SHA-256: `{manifest['input_sha256']}`",
        f"Parser: `{manifest['parser']['name']} {manifest['parser']['version']}`",
        f"Total statements: {manifest['total_statements']}",
        "",
        "## Category Counts",
        "",
        "| Category | Count |",
        "|---|---:|",
    ]
    for category, count in manifest["category_counts"].items():
        lines.append(f"| `{category}` | {count} |")
    lines.extend(["", "## Variants", ""])
    for variant_name, variant in manifest["variants"].items():
        lines.extend(
            [
                f"### {variant_name}",
                "",
                f"Selected statements: {variant['selected_statement_count']}",
                f"SQLite DB: `{variant['db_path']}`",
                f"AMIE3 TSV: `{variant['tsv_path']}`",
                f"TSV rows: {variant['tsv_row_count']}",
                "",
                "| Table | Rows |",
                "|---|---:|",
            ]
        )
        for table, count in variant["table_counts"].items():
            lines.append(f"| `{table}` | {count} |")
        lines.append("")
    if manifest["warnings"]:
        lines.extend(["## Warnings", ""])
        lines.extend(f"- {warning}" for warning in manifest["warnings"])
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
