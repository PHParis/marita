# MARITA runtime and memory optimization report

Date: 2026-07-17

This report records the first runtime optimization pass requested for MAHILDA/MARITA. The paper rule language, support threshold semantics, joinability scope, public rule representation, and bounded completeness contract were left unchanged.

## Bottleneck

Initialization previously built the constraint graph by comparing every JIA with every later JIA, and `ConstraintGraph.all_neighbors()` then rescanned the complete directed edge map for every DFS expansion. FK-only compatibility also re-read the complete foreign-key mapping for every attribute pair.

The optimized path now:

- obtains FK-compatible attribute pairs once from the inspector's metadata in `joinability: fk` mode;
- groups JIAs by shared `(table, occurrence)` endpoints and creates the same canonical edges from those groups;
- keeps a reverse edge index, so neighbor lookup is independent of total edge count;
- uses ordered occurrence lists rather than transient sets during indexed graph construction.

Full/value-overlap joinability and inspectors without FK metadata retain the existing pairwise compatibility fallback.

## Semantic evidence

`tests/test_marita_paper_conformance.py::test_optimized_graph_preserves_exhaustive_bounded_rule_set` constructs both the indexed graph and the old pairwise reference graph on a small FK database. It checks:

- identical nodes, directed edges, and direction-independent neighbors;
- identical JIA compatibility output from the FK metadata fast path and the old pairwise `Attribute.is_compatible()` path;
- identical canonical rule sets from exhaustive bounded DFS;
- one-head and safe-rule invariants for every emitted rule.

The existing focused tests additionally cover FK-only joinability, disjoint and composite-key semantics, repeated relations, equality transitivity, minimality, bounds, deterministic traversal, and suppression of unsafe/out-of-scope rules.

## Reproducible measurements

The benchmark oracle is in `scripts/benchmark_discovery.py`. It times the old pairwise graph constructor and the indexed constructor on the same JIA list and records peak Python allocations with `tracemalloc`:

```sh
uv run python scripts/benchmark_discovery.py \
  --database data/relational/Carcinogenesis.db
uv run python scripts/benchmark_discovery.py \
  --database data/relational/SAT.db
uv run python scripts/benchmark_discovery.py \
  --database data/relational/SAT.db \
  --profile-output /tmp/mahilda-sat-final.prof
uv run python scripts/benchmark_discovery.py \
  --synthetic-fk-chain \
  --profile-output /tmp/mahilda-synthetic-final.prof
uv run python -c \
  'import pstats; pstats.Stats("/tmp/mahilda-sat-final.prof").strip_dirs().sort_stats("cumulative").print_stats(12)'
```

Fresh measurements from this worktree were:

| Workload | JIAs | Edges | Pairwise graph | Indexed graph | Pairwise peak alloc. | Indexed peak alloc. |
|---|---:|---:|---:|---:|---:|---:|
| Synthetic eight-table FK chain | 63 | 288 | 0.00985 s | 0.00753 s | 72,996 B | 75,740 B |
| Carcinogenesis | 117 | 1,764 | 0.01656 s | 0.01548 s | 298,524 B | 303,092 B |
| SAT | 585 | 28,161 | 0.28189 s | 0.24624 s | 3,809,728 B | 3,831,652 B |

These are graph-initialization measurements, not end-to-end paper-run speedup claims. The indexed path was about 6.5% faster on Carcinogenesis and 12.6% faster on SAT in these single-run measurements. Peak graph allocations increased slightly because the reverse index is retained for DFS; this is a deliberate linear-memory tradeoff that removes repeated full-edge scans. No change to the candidate-rule set was accepted without the reference comparison above.

The cProfile run on SAT confirms that graph construction is the measured hotspot: `add_edge` accounts for 1.498 s cumulative across the two constructors, the indexed constructor accounts for 0.914 s, and the pairwise oracle accounts for 0.866 s under profiling overhead. SQL rule evaluation remains the dominant unmeasured end-to-end limit on this host and should be profiled separately before changing query semantics.

For context, completed paper artifacts already in `results/paper_table2` report 7.59 s / 10 rules for Carcinogenesis and 45.23 s / 41 rules for SAT. Those historical end-to-end runs are retained as baseline evidence; the measurements above isolate the changed initialization/enumeration data structure and do not overwrite paper results.

## Verification

At the end of this pass:

- focused conformance/helper suite: 35 passed;
- full suite: 269 passed, 4 skipped;
- Ruff check and format check: passed;
- pyright: 0 errors, 0 warnings, 0 informations;
- pre-commit: Ruff, format, and pyright hooks passed;
- no paper rule-language, support, output-format, or completeness claim was changed.

## Remaining limits

The JIA list and directed edge sets are still materialized, and SQL evaluation still runs once per candidate split. A subsequent profiling pass found that candidate-bound checks were constructing full transitive analyses even when only relation-occurrence bounds were needed. Those checks now use the endpoint occurrences directly, and candidate signatures are accumulated in one pass rather than rescanning every equivalence class for every occurrence.

The change was verified against the stored July 19 rule oracles for the three recommended datasets. The generated ordered sequences of display, support, and confidence values have identical SHA-256 hashes:

| Database | Rules | Current/reference hash |
| --- | ---: | --- |
| Mesh | 1,173 | `767dcbc6973d78d497c52eb1fc0d15fa71a830a4d8df5559e0a674b4c62c044e` |
| SAT | 3,139 | `243d10f7f53f32f0a261d945e2d8abd94d07fa6bb86c5c73bbeda3694ef0c76f` |
| Biodegradability | 39 | `a7bd55549cea1aa7e9234942374301bd7f19b9b01a78f0b2a42a9e7a1c24fe25` |

On the current host, direct library runs measured approximately 3.7 s, 45.2 s, and 21.0 s respectively. These wall-clock values are directional because the stored reference was captured on another host; the hashes and counts are the regression gates. A future SQL query-construction optimization should retain the same exact-oracle comparison before acceptance.
