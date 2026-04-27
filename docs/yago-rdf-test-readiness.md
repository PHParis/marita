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

## Main Remaining MAHILDA Fix

MAHILDA compatibility discovery currently checks only one foreign-key direction in `src/mahilda/algorithms/mahilda_core/constraint_graph.py`:

```python
return db_inspector.check_foreign_key_silently(self.table, self.name, other_attribute.table, other_attribute.name)
```

The generated YAGO relational DB is predicate-centric. Predicate tables such as `schema_birthPlace(subject_id, object_id)` reference `entity(entity_id)`. Because MAHILDA iterates attributes in sorted table order, it may compare `entity.entity_id` to `schema_birthPlace.subject_id` before the reverse comparison and miss the FK edge.

Recommended fix:

```python
return (
    db_inspector.check_foreign_key_silently(self.table, self.name, other_attribute.table, other_attribute.name)
    or db_inspector.check_foreign_key_silently(other_attribute.table, other_attribute.name, self.table, self.name)
)
```

Until this is fixed, MAHILDA may run on `yago_tiny_core.db` but produce too few or zero compatible attributes, making the result hard to interpret.

## Recommended MAHILDA Smoke Run

After generating artifacts and fixing symmetric FK compatibility:

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

Remaining caveat: `src/mahilda/evaluation/baselines/amie3.py` currently uses a hardcoded `300` second timeout. YAGO may need this to be configurable for real benchmark runs.

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
3. Apply symmetric FK compatibility fix for MAHILDA.
4. Run `uv run mahilda run --config configs/config.yago-core.yaml`.
5. Run AMIE3 with `--input-tsv`.
6. Run SPIDER against the generated SQLite DB.
7. Attempt POPPER only after confirming table count and dependency availability.

## Short Answer

The import architecture and AMIE3 fair-input path are ready. A full comparative YAGO benchmark is not fully ready until MAHILDA's FK compatibility check is symmetric and the generated YAGO artifacts have been smoke-tested.
