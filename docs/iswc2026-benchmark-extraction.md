# ISWC 2026 Benchmark Extraction

This document records the benchmark parameters and reported results extracted from the active paper draft in `/home/paris/dev/tex/MaHilda_paper` on 2026-04-30. The active draft is assembled by `main.tex`, which includes `sections/experiment_updated.tex` and `sections/ablation_updated.tex`.

## Source Files

- `main.tex`: active document root.
- `sections/experiment_updated.tex`: active main benchmark description and results table.
- `sections/ablation_updated.tex`: active ablation description and results.
- Stale non-`*_updated.tex` section files contain older numbers and should not be treated as the current source of truth unless explicitly revisiting historical drafts.

## Global Evaluation Protocol

- Dataset source: `relational-data.org`.
- Dataset format: relational datasets migrated to SQLite 3.46.
- Active paper states 54 benchmark datasets, with a representative subset of 10 reported in the main table.
- Local reproduction dataset directory: `data/relational`.
- Local converted datasets currently available: 83 `.db` files.
- Hardware stated in paper: Intel Xeon E5-2650 v2 @ 2.60 GHz.
- Per-job limits stated in paper: 2 hours and 15 GB.
- Metrics: wall-clock time and mean resident-set size (RSS).

## MARITA Parameters

- Joinability scope: declared foreign-key edges only.
- Support threshold: `k_s = 0`.
- Confidence threshold: `k_c = 0`.
- Rule filtering: rules matching only one tuple are filtered after discovery.
- Maximum total rule length: `L = 3` atoms.
- Semantics for reproduction: `disjoint_semantics: true`.
- Code-level reproduction parameters are configurable under `algorithm.parameters` in YAML configs.

Recommended config mapping for reruns:

```yaml
algorithm:
  name: MARITA
  parameters:
    walk_length: 3
    max_tables: 3
    max_variables: 3
    disjoint_semantics: true
    support_threshold: 1
    timeout: 7200
```

## Baseline Parameters Found In Code

The active paper draft describes the baseline systems but does not fully enumerate their command-line parameters. The executable repository currently hard-codes these settings.

### AMIE3

Adapter: `src/marita/evaluation/baselines/amie3.py`

Command shape:

```text
java -Xmx15G -jar src/marita/evaluation/third_party/amie3/amie-milestone-intKB.jar \
  -mins 0 -minc 0 -minpca 0 -minhc 0 -minis 0 <input.tsv>
```

Notes:

- The benchmark CLI passes `benchmark.timeout` to AMIE3.
- If `--input-tsv` or `benchmark.input_tsv` is provided, AMIE3 consumes that TSV directly.
- Without direct TSV input, the relational database is exported to TSV by `AlchemyUtility`.

### SPIDER

Adapter: `src/marita/evaluation/baselines/spider.py`

Command shape:

```text
java -cp metanome-cli-1.2-SNAPSHOT.jar:SPIDER-1.2-SNAPSHOT.jar \
  de.metanome.cli.App \
  --algorithm de.metanome.algorithms.spider.SPIDERFile \
  --files <csv files> \
  --table-key INPUT_FILES \
  --separator , \
  --output file:<output_prefix> \
  --header
```

Notes:

- Current direct `marita benchmark` execution does not pass an explicit timeout to SPIDER.
- The paper-runner added for reproduction wraps the whole command with a wall-clock timeout.

### POPPER

Adapter: `src/marita/evaluation/baselines/popper.py`

Runtime settings passed to Popper:

```python
Settings(
    kbpath=directory,
    max_body=max(3, number_of_tables - 1),
    max_vars=number_max_attributes,
    quiet=False,
    debug=True,
)
```

Generated `bias.pl` includes:

```prolog
max_body(6).
max_vars(<dynamic max arity>).
allow_singletons.
```

Notes:

- POPPER depends on an external `run-popper` wrapper; see `docs/popper-user-space-install.md`.
- Current direct `marita benchmark` execution does not enforce the paper timeout uniformly for POPPER.
- The paper-runner added for reproduction wraps the whole command with a wall-clock timeout.

## Main Paper Results

Reported in `sections/experiment_updated.tex`.

| Database | Popper Rules | Popper Time (s) | SPIDER Rules | SPIDER Time (s) | AMIE3 Rules | AMIE3 Time (s) | MARITA Rules | MARITA Time (s) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Biodegradability | 0 | 1754.1 | 4 | 1.6 | 0 | 0.8 | 4 | 0.22 |
| CDESchools | OOM | OOM | 2 | 18.5 | OOM | OOM | 3 | 1.79 |
| Chess | OOM | OOM | 1 | 1.9 | 0 | 14.8 | 2 | 0.08 |
| Cora | 0 | 20.6 | 4 | 1.7 | 0 | 2.6 | 4 | 0.43 |
| CraftBeer | 0 | 35.1 | 1 | 1.5 | 0 | 2.2 | 2 | 0.06 |
| Countries | OOM | OOM | OOM | OOM | 0 | 270.6 | 0 | 1.21 |
| Dunur | 1 | 3.9 | 20 | 1.7 | 0 | 0.6 | 12 | 0.19 |
| Nations | OOM | OOM | 2 | 5.7 | 0 | 623.2 | 2 | 0.18 |
| PTE | 6 | 2122.2 | 22 | 4.8 | 0 | 3.5 | 16 | 0.48 |
| SAT | 4 | 0.7 | 8 | 2.6 | 0 | 9.5 | 7 | 0.41 |

Local filename mapping for the 10 reported DBs:

- `Biodegradability.db`
- `CDESchools.db`
- `Chess.db`
- `CORA.db`
- `CraftBeer.db`
- `Countries.db`
- `Dunur.db`
- `nations.db`
- `PTE.db`
- `SAT.db`

## Ablation Parameters And Results

Reported in `sections/ablation_updated.tex`.

- Dataset: `Biodegradability`.
- Dataset stats stated in paper: 1,055 tuples, 13 tables, 47 attributes, 12 foreign keys.
- Recursion depth: `N in {1, 2, ..., 10}`.
- Compared modes: disjoint semantics vs non-disjoint semantics.
- Other parameters: `k_s = 0`, `k_c = 0`, `L = 3`, timeout 600 seconds.

Disjoint runtime by `N`:

| N | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Time (s) | 0.23 | 0.28 | 0.26 | 0.28 | 0.29 | 0.30 | 0.30 | 0.30 | 0.30 | 0.30 |
| Rules | 6 | 7 | 7 | 7 | 7 | 7 | 7 | 7 | 7 | 7 |

Non-disjoint results:

| N | Time (s) | Rules |
| --- | ---: | ---: |
| 1 | 0.23 | 6 |
| 2 | 22.53 | 12 |
| >= 3 | timeout | timeout |

## Reproduction Caveats

- The active paper draft does not state all baseline command-line parameters; this document records the settings in the current codebase.
- `marita batch` is MARITA-only and, at the time of extraction, did not propagate `algorithm.parameters`; use `marita paper-benchmark` for paper reruns.
- Direct `marita benchmark` runs one baseline on one database. Use `marita paper-benchmark` for multi-database, multi-algorithm reruns.
- `data/relational` is ignored by git via `data/*`; benchmark databases are intentionally local artifacts, not repository contents.

Install baseline Python dependencies before baseline reruns:

```bash
uv sync --extra benchmark
```

## Recommended Rerun Sequence

Start with a dry-run plan:

```bash
uv run marita paper-benchmark --dry-run --algorithms MARITA --databases paper
```

Run MARITA on the 10 reported paper databases first:

```bash
uv run marita paper-benchmark --algorithms MARITA --databases paper --timeout 7200 --memory-gb 15
```

If the MARITA rule counts differ from the paper table, inspect the generated configs under `results/iswc2026/configs/` and the per-run artifacts under `results/iswc2026/MARITA/` before running baselines.

Run selected baselines only after confirming runtime dependencies:

```bash
uv run marita paper-benchmark --algorithms AMIE3,SPIDER --databases Biodegradability,CORA --timeout 7200 --memory-gb 15
uv run marita paper-benchmark --algorithms POPPER --databases SAT,Dunur --timeout 7200 --memory-gb 15
```

Run the full 10-database, all-algorithm matrix last:

```bash
uv run marita paper-benchmark --algorithms ALL --databases paper --timeout 7200 --memory-gb 15
```

Use `--databases all` only after the paper subset has been validated, since the local directory currently contains more datasets than the active paper table reports.
