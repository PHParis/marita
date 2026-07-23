# MARITA paper conformance contract

This document maps the active specification in
`MARITA_ISWC_2026_short_paper/main.tex` to executable implementation checks.
It is intended to be reviewed whenever the paper's algorithm or the miner is
changed.

## Conformance matrix

| Paper requirement | Implementation | Regression evidence | Status |
|---|---|---|---|
| Foreign-key-joinable equality edges | `Attribute.is_compatible()` and `init()` in `constraint_graph.py` / `tgd_discovery.py` | End-to-end FK fixture in `test_marita_paper_conformance.py` | Conformant in `joinability: fk` mode |
| Connected proto-rules are exhaustively reachable | Direction-independent neighbors and semantic candidate deduplication in `ConstraintGraph.all_neighbors()` and `dfs()` | V-shaped incoming-edge test in `test_tgd_discovery_helpers.py` | Conformant for the configured occurrence/table/variable bounds |
| Equality chains are transitive and independent of traversal order | Union-find construction in `CandidateRuleChains` | Pair-order permutation test in `test_candidate_rule_chains.py` | Conformant |
| Minimal proto-rules may use any licensed spanning tree | Cycle-based minimality check plus equality-closure candidate key | Spanning-tree/cycle test and end-to-end repeated-relation test | Conformant |
| Single relational head | `dfs()` retains only splits with one head occurrence | Helper and end-to-end tests | Conformant |
| Safe Horn rules: every head variable occurs in the body | `is_safe_split()` before evaluation/emission | Safe/unsafe split tests; all end-to-end output checked | Conformant |
| Repeated occurrences of the same relation are supported | No degree-one occurrence rejection; occurrence-aware aliases | Degree-one helper test and real repeated-`child` mining test | Conformant |
| Relation-disjoint semantics compares different primary-key tuples | `_construct_primary_key_conditions()` | Single- and composite-key query tests | Conformant when enabled |
| Composite-key tuple inequality is a disjunction of key-column inequalities | SQLAlchemy `or_(*inequalities)` | Composite-key rows differing in one key component | Conformant |
| Repeated relation without a declared primary key cannot silently use self-witnesses | Query is made unsatisfiable | No-primary-key repeated-relation test | Explicitly rejected by zero witnesses |
| SQL evaluation includes every equality edge regardless of input order | Pending-edge fixed-point join construction | Out-of-order connected join test | Conformant |
| Empty-witness pruning is anti-monotonic | `path_pruning()` is the only witness-based branch stop | DFS tests | Conformant |
| Output suppression does not stop descendant traversal | Emission and expansion are independent | Lower-score descendant test | Conformant |
| Equal-scoring rules with the same head remain eligible | Best-head comparison accepts ties | Same-head tie test | Conformant with the current output policy |
| Syntactically equivalent rules are returned only once | `horn_rule_key()` canonicalizes variables, body order, and repeated-relation occurrence numbers while fixing the head | Repeated-head key unit test and end-to-end canonical-output assertion | Conformant |
| Bounds count relation occurrences and logical variables | `check_max_table()` and equality-chain count in `check_max_vars()` | Bound tests | Conformant |
| No mutable search state leaks between runs | Per-invocation pruning and candidate caches | Helper tests and function defaults | Conformant |
| Deterministic search result | Sorted roots/neighbors and canonical equality-closure keys | Traversal/order tests | Structurally deterministic; SQL/database ordering is not used for decisions |

## Publication blockers

### Support semantics

The active paper defines support as the number of distinct assignments to head
variables for which the complete rule has a witness. `prediction()` computes
that count for a safe rule, but `calculate_support()` currently divides it by a
body count. The value exported as `support`/`accuracy` is therefore normalized
and is not the paper's support. `calculate_confidence()` divides by a head-only
count and is not the confidence definition currently hidden inside `\ignore{}`
either.

MARITA now exports raw projected-head support as an integer and applies the
mandatory `support_threshold >= 1`; confidence is retained only as a diagnostic.

### Completeness versus best-per-head output pruning

The paper claims that every non-equivalent in-scope rule above threshold is
returned. The implementation suppresses a rule when a previously seen rule
with the same indexed head occurrence has higher support. Those rules need not
be syntactically equivalent, so both statements cannot be true simultaneously.

The global best-per-head suppression has been removed from discovery. Optional
best-per-head ranking is available as explicit post-processing and retains ties.

## Scope assumptions

- Publication runs must set `joinability: fk` and `disjoint_semantics: true`.
  The general API defaults currently allow other modes.
- The paper assumes no `NULL` values. SQL equality follows SQL null semantics;
  databases containing nulls are outside the stated model unless the paper and
  evaluator are extended together.
- Length/occurrence, relation, and logical-variable bounds are part of the
  hypothesis class. Completeness is only meaningful inside those configured
  bounds.
- A repeated relation under relation-disjoint semantics requires a declared
  primary key. MARITA treats a missing key as having no admissible disjoint
  witness rather than silently changing semantics.

## Required verification

Run before producing publication results:

```sh
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest
```

The focused conformance suite is:

```sh
uv run pytest \
  tests/test_marita_paper_conformance.py \
  tests/test_candidate_rule_chains.py \
  tests/test_tgd_discovery_helpers.py \
  tests/test_query_utility.py
```
