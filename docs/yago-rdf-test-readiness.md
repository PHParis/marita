# YAGO RDF Benchmark Test Readiness

This note records what remains before running YAGO with MAHILDA and the competitor baselines. The RDF import architecture and AMIE3 direct TSV path exist, but a full comparative benchmark still needs a few follow-up checks and one MAHILDA fix.

## Current Status

Implemented:

- `uv run mahilda import-rdf` parses `data/yago-tiny.ttl` and writes benchmark artifacts.
- The importer writes a full archive DB, variant SQLite DBs, direct AMIE3 TSV exports, manifest JSON, and report markdown.
- The `core` variant contains ABox object facts, ABox datatype facts, and instance `rdf:type` facts.
- The `ontology-lite` variant adds explicit `rdfs:subClassOf` statements.
- AMIE3 can consume a direct TSV via `benchmark --input-tsv`, avoiding relational re-export bias.
- `configs/config.yago-core.yaml` points MAHILDA at the generated core SQLite DB with conservative parameters.

Generate the artifacts with:

```bash
uv run mahilda import-rdf --input data/yago-tiny.ttl --output-dir data/yago --variants core,ontology-lite --dataset-name yago_tiny
```

Expected outputs:

```text
data/yago/yago_tiny_full.db
data/yago/yago_tiny_core.db
data/yago/yago_tiny_core.tsv
data/yago/yago_tiny_ontology_lite.db
data/yago/yago_tiny_ontology_lite.tsv
data/yago/yago_tiny_manifest.json
data/yago/yago_tiny_report.md
```

## Main MAHILDA Fix (Implemented)

MAHILDA compatibility discovery now checks both foreign-key directions in `src/mahilda/algorithms/mahilda_core/constraint_graph.py`:

```python
return (
    db_inspector.check_foreign_key_silently(self.table, self.name, other_attribute.table, other_attribute.name)
    or db_inspector.check_foreign_key_silently(other_attribute.table, other_attribute.name, self.table, self.name)
)
```

The generated YAGO relational DB is predicate-centric. Predicate tables such as `schema_birthPlace(subject_id, object_id)` reference `entity(entity_id)`. Without this fix, MAHILDA would miss FK edges when iterating attributes in sorted table order. The fix ensures both directions are checked.

## Recommended MAHILDA Smoke Run

After generating artifacts:

```bash
uv run mahilda run --config configs/config.yago-core.yaml
```

If the run is too slow, temporarily lower these values in `configs/config.yago-core.yaml`:

```yaml
algorithm:
  parameters:
    walk_length: 2
    max_tables: 2
    max_variables: 4
    timeout: 600
```

## Competitor Readiness

### AMIE3

Closest to ready. Use direct TSV input:

```bash
uv run mahilda benchmark --config configs/config.yago-core.yaml --baseline AMIE3 --input-tsv data/yago/yago_tiny_core.tsv
```

YAGO runs can raise the AMIE3 subprocess timeout in `configs/config.yago-core.yaml`:

```yaml
benchmark:
  timeout: 1800
```

### SPIDER

Runnable on the generated SQLite DB:

```bash
uv run mahilda benchmark --config configs/config.yago-core.yaml --baseline SPIDER
```

Remaining caveat: SPIDER will see many normalized predicate tables and may produce inclusion dependencies dominated by shared `entity`, `literal`, and `class` reference columns. This is expected for a predicate-centric KG relationalization and should be discussed when interpreting results.

### POPPER

Not yet practical for full YAGO without additional controls.

```bash
uv run mahilda benchmark --config configs/config.yago-core.yaml --baseline POPPER
```

Remaining caveats:

- Requires `clingo` and SWI-Prolog runtime dependencies.
- The current adapter derives search size from table count and may create a very large Prolog task for YAGO.
- It likely needs table filtering, max-table limits, or a dedicated small YAGO subset before a full run is practical.

## Recommended Test Order

1. Generate artifacts with `import-rdf`.
2. Inspect `data/yago/yago_tiny_manifest.json` and `data/yago/yago_tiny_report.md`.
3. Run `uv run mahilda run --config configs/config.yago-core.yaml`.
4. Run AMIE3 with `--input-tsv`.
5. Run SPIDER against the generated SQLite DB.
6. Attempt POPPER only after confirming table count and dependency availability.

## Short Answer

The import architecture, AMIE3 fair-input path, and MAHILDA symmetric FK compatibility check are all in place. A full comparative YAGO benchmark is ready to run once the YAGO artifacts have been generated and smoke-tested.
