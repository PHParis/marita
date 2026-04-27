# YAGO RDF Benchmark Protocol

This protocol defines how to transform `data/yago-tiny.ttl` into benchmark artifacts for MAHILDA and graph-rule baselines without favoring either input paradigm. The core principle is to define one canonical set of KG statements first, then derive all algorithm-specific inputs from that same statement set.

## Goals

- Preserve the original KG as completely as possible in an archival representation.
- Keep the primary benchmark focused on rule discovery from observed facts.
- Avoid giving AMIE3 direct ontology facts that MAHILDA only receives after relational modeling, or giving MAHILDA relational shortcuts that are absent from the graph export.
- Make every inclusion, exclusion, parsing, and materialization decision auditable for publication.

## Dataset Variants

Use the following variants in experiments.

| Variant | Name | Content | Intended Role |
|---|---|---|---|
| V0 | Full archive | Every parsed RDF statement, including ontology, SHACL, facts, annotations, blank nodes, datatypes, and language tags | Preservation and audit only |
| V1 | Core benchmark | ABox object facts, ABox datatype facts, and instance `rdf:type` facts | Primary benchmark |
| V2 | Ontology-lite ablation | V1 plus explicit class hierarchy statements such as `rdfs:subClassOf` | Secondary ablation |
| V3 | Schema-rich ablation | V2 plus selected property/schema metadata derived from SHACL | Optional sensitivity analysis |
| V4 | Annotation ablation | V1 plus annotation and identifier predicates such as labels, URLs, images, and `sameAs` | Optional noise/scale sensitivity analysis |

V1 should be the main paper setting. V2 and later variants answer different questions and should be reported as ablations, not as replacements for the primary benchmark.

## Why V1 Is The Primary Benchmark

V1 tests whether algorithms discover Horn rules from observed instance-level regularities. V2 adds curated ontology knowledge, which changes the task from factual rule discovery to factual rule discovery with explicit background theory.

Ontology triples can create rules that are true by construction. For example, `schema:Person(x) -> schema:Thing(x)` follows from the class hierarchy rather than from empirical regularities in the facts. Such rules are meaningful, but they measure use of curated schema knowledge rather than discovery from data.

Keeping V2 as an ablation makes the scientific claim cleaner. The primary result evaluates factual rule discovery; the ablation quantifies how much explicit ontology knowledge changes the outcome.

## Canonical Artifacts

The pipeline should produce these artifacts from the same input TTL file.

| Artifact | Description |
|---|---|
| `yago_tiny_full.db` | Loss-preserving SQLite archive of the parsed RDF graph |
| `yago_tiny_core.db` | Relational SQLite materialization for V1 |
| `yago_tiny_core.tsv` | Direct graph TSV export for AMIE3 from V1 statements |
| `yago_tiny_ontology_lite.db` | Relational SQLite materialization for V2 |
| `yago_tiny_ontology_lite.tsv` | Direct graph TSV export for AMIE3 from V2 statements |
| `yago_tiny_manifest.json` | Machine-readable provenance, counts, mappings, and checksums |
| `yago_tiny_report.md` | Human-readable transformation report for the paper appendix |

The AMIE3 TSV must be exported from the selected KG statements, not from `TripleConverter` over the relational SQLite database. Re-exporting from the relational database would give AMIE3 a second-order encoding of the relational model rather than the same KG statement set.

## Statement Categories

Each parsed RDF statement must be assigned exactly one category before benchmark selection.

| Category | Definition | V1 | V2 | V3 | V4 |
|---|---|---:|---:|---:|---:|
| `abox_object_fact` | Subject and object are resources, predicate is an instance-level domain predicate | yes | yes | yes | yes |
| `abox_datatype_fact` | Subject is a resource, object is a literal, predicate is an instance-level domain predicate | yes | yes | yes | yes |
| `instance_type` | `rdf:type` where the subject is an instance-like entity and the object is a domain class | yes | yes | yes | yes |
| `class_hierarchy` | Explicit `rdfs:subClassOf` between classes | no | yes | yes | no |
| `class_disjointness` | Explicit `owl:disjointWith` between classes | no | optional | yes | no |
| `schema_declaration` | Class, property, node shape, and property shape declarations | no | no | yes | no |
| `shacl_shape` | `sh:*` shape metadata, including `sh:path`, `sh:class`, `sh:datatype`, `sh:or`, and cardinalities | no | no | yes | no |
| `annotation` | Labels, comments, names, URLs, images, external IDs, and lexical annotations | no | no | no | yes |
| `provenance_or_mapping` | Provenance, source mapping, or external-source metadata such as `ys:fromClass` and `ys:fromProperty` | no | no | optional | no |
| `unsupported_or_other` | Any statement that does not fit the categories above | no | no | no | no |

The report must include the statement count per category and the predicate count per category.

## Predicate Inclusion Rules

Use predicate families rather than hand-picked individual predicates whenever possible.

| Predicate Family | Examples | V1 Decision | Rationale |
|---|---|---|---|
| Instance object predicates | `schema:birthPlace`, `schema:memberOf`, `schema:worksFor`, `yago:hasFather` | include | Core relational and graph structure |
| Instance datatype predicates | `schema:birthDate`, `schema:deathDate`, `yago:area`, `yago:siteLinks` | include | Observed factual attributes |
| Instance type predicate | `rdf:type` for instance-to-class assertions | include | Type facts are part of the KG ABox and are essential context |
| Class hierarchy predicates | `rdfs:subClassOf` | exclude from V1, include in V2 | Ontology knowledge, not observed instance fact |
| SHACL predicates | `sh:path`, `sh:class`, `sh:datatype`, `sh:or`, `sh:maxCount` | exclude from V1, include only in V3 metadata | Useful for transformation and validation, but curated schema information |
| Annotation predicates | `rdfs:label`, `rdfs:comment`, `schema:url`, `schema:image`, `schema:alternateName` | exclude from V1, include in V4 | High-volume lexical metadata can dominate discovered rules |
| Identity/linkout predicates | `schema:sameAs`, external identifiers | exclude from V1, include in V4 | Valuable metadata, but mostly identifier regularities |
| Provenance/mapping predicates | `ys:fromClass`, `ys:fromProperty`, `prov:*` | exclude from V1 | Transformation metadata rather than object-level facts |

If a predicate is ambiguous, keep it in V0, exclude it from V1, and record the decision in the manifest.

## Term Normalization

Terms must be stored and exported with stable canonical identifiers.

| Term Type | Canonical Form |
|---|---|
| IRI | Absolute IRI string |
| CURIE | Display-only abbreviation derived from the stored namespace table |
| Literal | Lexical form, datatype IRI, language tag, and optional parsed value |
| Blank node | Stable skolem IRI or stable generated identifier scoped to the input checksum |

The archival database must preserve the original literal lexical form even when a parsed numeric, date, or URI value is also stored.

## Archive SQLite Schema

The full archive should preserve all parsed RDF content before any benchmark filtering.

Minimum tables:

| Table | Purpose |
|---|---|
| `namespace` | Prefix to namespace IRI mappings from the TTL |
| `term` | All IRIs, blank nodes, and skolemized identifiers |
| `literal` | Literal lexical form, datatype IRI, language tag, and parsed value columns |
| `statement` | Subject, predicate, object or literal, object kind, category, and statement hash |
| `shape_property` | Optional denormalized SHACL property metadata extracted for reporting |
| `transformation_decision` | Inclusion or exclusion decision per predicate/category/variant |

The archive uses RDF graph set semantics. If duplicate triples appear in the TTL, the parser behavior must be documented. If duplicate preservation is required, the parser must record occurrence-level statement rows before RDF graph deduplication.

## Benchmark Relational Materialization

The relational benchmark DB should be predicate-centric and normalized. Avoid wide class tables as the primary representation because they collapse multi-valued predicates, union ranges, and annotations into arbitrary columns.

Minimum tables for V1:

| Table | Purpose |
|---|---|
| `entity` | Every resource appearing in an included V1 statement |
| `literal` | Every literal appearing in an included V1 statement |
| `class` | Every class appearing in an included `rdf:type` statement |
| `rdf_type` | Instance-to-class assertions |
| one object table per object predicate | Binary relation table with `subject_id` and `object_id` |
| one literal table per datatype predicate | Binary relation table with `subject_id`, `literal_id`, and optional parsed value columns |

Object predicate table example:

```sql
CREATE TABLE schema_birthPlace (
    statement_id INTEGER PRIMARY KEY,
    subject_id INTEGER NOT NULL REFERENCES entity(entity_id),
    object_id INTEGER NOT NULL REFERENCES entity(entity_id)
);
```

Datatype predicate table example:

```sql
CREATE TABLE schema_birthDate (
    statement_id INTEGER PRIMARY KEY,
    subject_id INTEGER NOT NULL REFERENCES entity(entity_id),
    literal_id INTEGER NOT NULL REFERENCES literal(literal_id),
    parsed_date TEXT,
    raw_lexical TEXT NOT NULL
);
```

Do not collapse `sh:maxCount 1` predicates into scalar columns in the primary representation. Use `sh:maxCount` for validation and reporting. This avoids giving the relational representation information loss or structural advantages that the graph representation does not share.

For V2, add class hierarchy tables:

```sql
CREATE TABLE rdfs_subClassOf (
    statement_id INTEGER PRIMARY KEY,
    subclass_id INTEGER NOT NULL REFERENCES class(class_id),
    superclass_id INTEGER NOT NULL REFERENCES class(class_id)
);
```

No inferred closure should be materialized in V1 or V2 unless a separate closure variant is explicitly declared. If closure is used, both the relational DB and AMIE3 TSV must receive the same inferred statements, and the report must distinguish explicit from inferred statements.

## AMIE3 Graph Export

AMIE3 input must be generated directly from the selected statement set for each variant.

Export format:

```text
subject<TAB>predicate<TAB>object
```

Rules:

- Use the same canonical term identifiers as the archive and manifest.
- Export one TSV row per selected statement.
- For literals, use a stable literal identifier or a fully escaped literal token consistently across all runs.
- Do not export triples derived from SQLite table names, column names, or row identifiers.
- Do not use `src/mahilda/database/triple_converter.py` for this RDF-origin benchmark unless the experiment is explicitly labeled as a relational re-export baseline.

## Fairness Constraints

These constraints must hold for every compared algorithm.

| Constraint | Requirement |
|---|---|
| Same fact set | MAHILDA and AMIE3 receive serializations derived from the same selected statement IDs |
| Same normalization | Entity, class, predicate, and literal identifiers are normalized once before export |
| Same variant | Results are only compared within the same variant, such as V1-to-V1 or V2-to-V2 |
| No algorithm-specific filtering | Do not remove predicates only because one algorithm performs poorly on them |
| No hidden ontology use | If ontology or SHACL data is used to construct schema, document it separately from facts given to the algorithms |
| No inferred facts by default | Do not add RDFS/OWL/SHACL entailments unless declared as a separate closure experiment |

SHACL may be used to design and validate the transformation. That does not mean SHACL statements are part of the mined benchmark input. The report must distinguish transformation metadata from algorithm input.

## Validation Checks

The pipeline should fail or warn on the following conditions.

| Check | Expected Result |
|---|---|
| Input checksum | Manifest records SHA-256 of the TTL file |
| Parse completeness | Parsed statement count is recorded |
| Category coverage | Every statement has exactly one category |
| Selection reproducibility | Every exported row points back to a selected statement ID |
| Predicate count parity | Counts per predicate match between selected statements and AMIE3 TSV |
| Table count parity | Counts per predicate match between selected statements and relational tables |
| FK integrity | SQLite foreign key checks pass |
| Literal preservation | Every literal preserves lexical form, datatype, and language tag |
| SHACL cardinality | `sh:maxCount` violations are reported but not silently collapsed |
| Ambiguous typing | Resources with multiple root classes are reported |
| Dropped statements | Excluded statements are counted by category and predicate |

## Manifest Requirements

The manifest should contain:

- input path and SHA-256 checksum
- parser name and version
- transformation code version or git commit
- namespace mappings
- statement counts by category
- predicate counts by category and variant
- class counts for `rdf:type`
- literal datatype counts
- selected statement IDs per variant or a reproducible selection hash
- relational table names and source predicates
- AMIE3 TSV row count and checksum
- warnings and validation failures

## Report Requirements

The human-readable report should include:

- a prose description of the input KG
- the exact dataset variant definitions
- a table of included and excluded predicate families
- a summary of SHACL shape usage in transformation
- row counts for all output tables
- AMIE3 export counts
- examples of transformed object facts, datatype facts, and type facts
- validation warnings
- known limitations

## Suggested Paper Wording

The benchmark was constructed in two stages. First, the RDF/Turtle source was parsed into a loss-preserving SQLite archive that stores all RDF statements, literals, datatype annotations, language tags, namespaces, ontology statements, and SHACL shapes. Second, benchmark variants were materialized from this archive by selecting canonical statement sets. The primary variant contains instance-level object facts, instance-level datatype facts, and explicit instance type assertions. Ontology and SHACL statements are preserved in the archive but excluded from the primary benchmark input to avoid conflating factual rule discovery with the use of curated schema knowledge.

For fairness, all algorithm-specific inputs were derived from the same selected statement identifiers. MAHILDA received a normalized predicate-centric SQLite materialization, while AMIE3 received a direct TSV graph export. The AMIE3 input was not generated by re-exporting the relational database, because that would encode the relational schema rather than the original KG statement set. Ontology-enriched inputs were evaluated separately as ablations.

## Implementation Notes For This Repository

- `mahilda batch` schedules `.db` files, so the relational materializations should live in a dataset directory as SQLite files.
- The current AMIE3 adapter reads TSV generated by `TripleConverter`; this RDF benchmark should add or use a direct TSV path for AMIE3 to avoid relational re-export bias.
- MAHILDA benefits from declared primary keys and foreign keys during compatibility discovery, so generated SQLite tables should declare them explicitly.
- The full archive should not be used directly as the MAHILDA benchmark DB because ontology and SHACL tables would be reflected as ordinary database tables.
