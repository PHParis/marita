# YAGO RDF Import Implementation Plan

This plan implements the benchmark protocol in `docs/yago-rdf-benchmark-protocol.md`. The goal is to transform `data/yago-tiny.ttl` into scientifically auditable artifacts for relational and graph-rule benchmarks without privileging MARITA or AMIE3.

## Target Artifacts

The import command should produce the following files under a caller-provided output directory:

| Artifact | Purpose |
|---|---|
| `yago_tiny_full.db` | Loss-preserving SQLite archive of parsed RDF statements |
| `yago_tiny_core.db` | V1 predicate-centric relational SQLite benchmark DB |
| `yago_tiny_core.tsv` | V1 direct AMIE3 TSV export from selected KG statements |
| `yago_tiny_ontology_lite.db` | V2 relational benchmark DB with class hierarchy statements |
| `yago_tiny_ontology_lite.tsv` | V2 direct AMIE3 TSV export |
| `yago_tiny_manifest.json` | Machine-readable provenance, counts, mapping decisions, checksums, and warnings |
| `yago_tiny_report.md` | Human-readable transformation report for paper appendices |

## Phase 1: Dependencies And Data Model

1. Add `rdflib` as a runtime dependency.
2. Add `src/marita/database/rdf_importer.py`.
3. Define small dataclasses for normalized RDF terms, literals, statements, variant definitions, and import stats.
4. Use stable integer IDs in SQLite and SHA-256 statement hashes for traceability.

## Phase 2: Full Archive

1. Parse the Turtle input with `rdflib`.
2. Record input SHA-256, parser name/version, command options, and output paths.
3. Normalize IRIs, blank nodes, and literals.
4. Store all statements in a full archive DB before benchmark filtering.
5. Include at least these archive tables:
   - `namespace`
   - `term`
   - `literal`
   - `statement`
   - `shape_property`
   - `transformation_decision`
   - `import_metadata`

## Phase 3: Statement Classification

Classify every parsed statement into exactly one category:

- `abox_object_fact`
- `abox_datatype_fact`
- `instance_type`
- `class_hierarchy`
- `class_disjointness`
- `schema_declaration`
- `shacl_shape`
- `annotation`
- `provenance_or_mapping`
- `unsupported_or_other`

The classifier should use RDF/RDFS/OWL/SHACL vocabulary rules first, then predicate-family rules. Ambiguous statements remain preserved in the archive and are excluded from V1 unless explicitly assigned.

## Phase 4: SHACL Metadata Extraction

Extract SHACL property-shape metadata into the archive and manifest:

- `sh:path`
- `sh:class`
- `sh:datatype`
- `sh:or`
- `sh:minCount`
- `sh:maxCount`
- labels and comments

Use this information for documentation and validation. Do not include SHACL statements in V1 benchmark input.

## Phase 5: Variant Selection

Implement at least these variants first:

| Variant | Selection |
|---|---|
| `core` | `abox_object_fact`, `abox_datatype_fact`, `instance_type` |
| `ontology-lite` | `core` plus `class_hierarchy` |

Keep `schema-rich` and `annotation` as future flags after V1/V2 are stable.

## Phase 6: Relational Materialization

Materialize each selected variant as a predicate-centric SQLite database.

Minimum V1 tables:

- `entity`
- `literal`
- `class`
- `rdf_type`
- one object table per object predicate
- one literal table per datatype predicate

Each generated predicate table should include `statement_id`, foreign keys, and indexes. Do not collapse `sh:maxCount 1` predicates into scalar columns in the primary materialization.

## Phase 7: Direct AMIE3 TSV Export

Export one TSV row per selected statement:

```text
subject<TAB>predicate<TAB>object
```

This export must be produced directly from selected statement IDs, not from `TripleConverter` over the relational SQLite database. Literals should use a stable representation, preferably skolemized literal IDs, to keep the graph export entity-predicate-entity shaped.

## Phase 8: AMIE3 Adapter Support

Add optional direct TSV support to the AMIE3 baseline:

1. Read an optional `benchmark.input_tsv` config value or runtime keyword.
2. If provided, pass that TSV path directly to AMIE3.
3. Otherwise, keep the current `TripleConverter` behavior for existing relational benchmarks.

## Phase 9: CLI Integration

Add a command such as:

```bash
uv run marita import-rdf \
  --input data/yago-tiny.ttl \
  --output-dir data/yago \
  --variants core,ontology-lite
```

The command should create parent directories, fail clearly on parse errors, and print the generated artifact paths.

## Phase 10: Validation And Reporting

Validate and report:

- TTL input checksum
- parser version
- total parsed statements
- statement counts by category
- selected statement counts per variant
- predicate counts per category and variant
- SQLite row counts per generated table
- AMIE3 TSV row counts and checksums
- `PRAGMA foreign_key_check` results
- literal datatype/language preservation
- SHACL `maxCount` violations as warnings, not silent row collapse
- dropped statements by category and predicate

## Phase 11: Tests

Add a small Turtle fixture and tests for:

- parsing and namespace capture
- literal preservation with datatype and language tag
- statement classification
- V1 excluding SHACL and class hierarchy statements
- V2 including class hierarchy statements
- predicate table materialization with foreign keys
- direct AMIE3 TSV export from selected statements
- manifest count parity against DB and TSV outputs
- AMIE3 direct TSV path preserving existing fallback behavior

## Phase 12: Commit Strategy

Use regular logical commits:

1. Documentation protocol and implementation plan.
2. Core importer archive/classification/materialization/export logic.
3. CLI and AMIE3 direct TSV integration.
4. Tests and final validation fixes.
