# MAHILDA Rule Coverage Audit

The audit answers one narrow question:

> Among competitor rules that are parseable, exact on the source database instance, non-vacuous, and inside MAHILDA's target class, which ones are recovered by MAHILDA?

It is a formal artifact audit. It does not judge whether a rule is useful, novel, or semantically interesting to a domain expert.

## Running The Audit

```sh
uv run mahilda audit \
  --results-dir results/paper_table2 \
  --database-dir data/relational \
  --output-dir results/paper_table2/audit
```

Useful options:

```sh
uv run mahilda audit --competitors MATILDA
uv run mahilda audit --confidence-threshold 1.0 --strict
uv run mahilda audit --settings configs/paper/benchmark_83.yaml
uv run mahilda audit --coverage subsumption
uv run mahilda audit --coverage instance
uv run mahilda audit --include-amie-rdf
uv run mahilda audit --no-progress
```

For a shared multi-server run, start the same command on every server. `--host auto`
uses the server's short hostname, and `--workers` controls local worker processes per
server. Each queue job owns one database, so competitor shards for one database are
processed sequentially while different databases run concurrently:

```sh
uv run mahilda audit \
  --results-dir results/paper_table2 \
  --database-dir data/relational \
  --output-dir results/paper_table2/audit \
  --hosts tipi00,tipi01,tipi02,tipi04 \
  --host auto \
  --workers 2
```

The output directory must be shared by all servers and support atomic file rename and
directory creation. All servers must use the same source revision, Python environment,
database files, results, and audit arguments. Monitor the global queue from any server:

```sh
uv run mahilda audit \
  --results-dir results/paper_table2 \
  --database-dir data/relational \
  --output-dir results/paper_table2/audit \
  --hosts tipi00,tipi01,tipi02,tipi04 \
  --status
```

Workers write leases and heartbeats under `<output-dir>/.audit_state/queue/`. A stale
lease is requeued until `--max-attempts` is reached. Use `--resume` after an interrupted
or failed run; completed database shards are skipped. `--reset-state` refuses to remove
state while a distributed job is active. The final aggregate reports are written once,
under a shared finalization lock, after every database job completes.

When the results root comes from `paper-benchmark`, the audit reads
`<results-dir>/progress/*.json` and `summary*.json` run metadata. Competitor
rules are audited only for databases whose MAHILDA run is recorded as a
complete `success`; timeout, OOM, error, missing, and partial runs are omitted.
Use `--status-dir` to point at a separate status directory. If no status
metadata exists, legacy result-directory behavior is retained.

To reuse a previous audit's stored per-rule evaluations and recompute only
matching and reports, use the cache mode:

```sh
uv run mahilda audit \
  --results-dir results/paper_table2 \
  --database-dir data/relational \
  --output-dir results/paper_table2/audit \
  --competitors MATILDA,SPIDER \
  --coverage subsumption \
  --reuse-cache \
  --no-progress
```

For a pre-cache audit that has only the legacy root CSV, add
`--trust-legacy-cache` once. Shards already present in that CSV are reused;
missing competitor shards are evaluated normally:

```sh
uv run mahilda audit \
  --results-dir results/paper_table2 \
  --database-dir data/relational \
  --output-dir results/paper_table2/audit \
  --competitors MATILDA,SPIDER \
  --coverage subsumption \
  --reuse-cache \
  --trust-legacy-cache \
  --no-progress
```

The first normal audit run writes a validated cache under
`<output-dir>/.audit_cache/`. Cache reuse does not execute SQLite evaluation.
It validates source, database, target-result, and scope signatures before
reusing a shard. The existing pre-cache `audit_rules.csv` can be imported
once with `--trust-legacy-cache`; that opt-in is required because the legacy
run did not record provenance metadata. Instance coverage is not available in
cache mode because it requires fresh SQLite projection queries.

`--strict` exits with code `2` if any comparable true competitor rule is not recovered by MAHILDA.
Progress bars are enabled by default. Serial runs show one bar per shard; parallel runs show one aggregate bar in the parent process so worker output does not overlap. Use `--no-progress` for CI logs or redirected output.

The default audit target-class settings match the paper benchmark defaults: `walk_length=3`, `max_tables=3`, `max_variables=3`, `joinability=fk`, and relation-disjoint semantics enabled. Override these with `--walk-length`, `--max-tables`, `--max-variables`, `--joinability`, `--no-disjoint-semantics`, or load a YAML config with `--settings`.

AMIE3 RDF/triple rules are skipped by default because no relational back-translation is implemented. Use `--include-amie-rdf` only to count them as unsupported audit rows; they remain outside the claim denominator.

## Inputs

The audit expects the benchmark layout already used by the repository:

```text
results/paper_table2/
  MAHILDA/MAHILDA_<DB>/MAHILDA_<DB>_results.json
  MATILDA/MATILDA_<DB>/MATILDA_<DB>_results.json
  AMIE3/AMIE3_<DB>/AMIE3_<DB>_results.json
  SPIDER/SPIDER_<DB>/SPIDER_<DB>_results.json
  POPPER/POPPER_<DB>/POPPER_<DB>_results.json
  progress/MAHILDA_<DB>.db.json

data/relational/<DB>.db
```

The audit recomputes support and confidence from the SQLite database. It does not trust exported confidence fields as proof of exactness.

## Rule Categories

Each competitor rule receives exactly one formal classification.

`parse_failed`: the rule cannot be parsed into a known representation.

`out_of_scope`: the rule is parseable or recognized, but not comparable to MAHILDA's audited target class. Examples include RDF/triple AMIE3 rules that cannot be translated to relational atoms, ILP/Popper rules that remain outside the relational formula parser, rules with head-only variables, non-FK joins, missing database tables or columns, rules outside the configured MAHILDA bounds, and unsupported inclusion-dependency shapes.

`vacuous`: the rule is formally redundant under the audit definition. The current checks mark a rule vacuous when the head atom is already present in the body after canonicalization, or when repeated occurrences of the same relation force reuse of the same primary-key tuple under relation-disjoint semantics.

`approximate`: the rule is inside scope and evaluable, but its recomputed confidence is below the configured threshold. The default threshold is `1.0`, so only rules true on every body match are treated as exact.

`comparable_true`: the rule is parseable, in scope, non-vacuous, and exact on the database instance.

The CSV also records `scope_status`, `scope_reason`, `target_class_member`, and `diagnosis`. These fields explain why a rule is excluded from or included in the claim denominator.

## Matching Against MAHILDA

Comparable true rules are matched against MAHILDA rules for the same database.

`recalled_alpha`: the competitor rule and a MAHILDA rule have the same canonical form modulo variable renaming and body atom order.

`recalled_subsumed`: no alpha-equivalent MAHILDA rule exists, but a MAHILDA rule logically subsumes the competitor rule under a rule homomorphism preserving relation names, columns, and shared variables.

`covered_on_instance`: no alpha-equivalent or subsuming MAHILDA rule exists, but the MAHILDA output covers the competitor rule's head projections on the current SQLite instance. This is a finite-instance diagnostic, not a logical recall proof.

`unmatched`: no alpha-equivalent or subsuming MAHILDA rule was found.

The primary recall claim should use alpha-equivalence. Subsumption recall is reported separately because it is a more permissive logical criterion. Finite-instance coverage is weaker still and should be used only as a diagnostic unless the paper explicitly says the claim is instance-level.

## Outputs

The command writes:

```text
audit_summary.json
audit_rules.csv
audit_unmatched.md
audit_diagnosis.md
audit_claims.md
```

`audit_summary.json` contains totals by algorithm and database.

`audit_funnel.csv` and `audit_summary.json:funnel_by_algorithm` contain the
per-system funnel: total, parseable, within scope, non-vacuous, above the
confidence threshold, exactly recovered, covered by a more general rule, and
unmatched. Exclusions are also explicit as parse-failed, out-of-scope,
vacuous, and below-threshold counts. Exact alpha-equivalent recovery and
logical subsumption are separate counts; they are not combined into one
recovered value.

`audit_rules.csv` contains one row per audited competitor rule: source, classification, reason, recomputed support/confidence, canonical rule, match status, matched MAHILDA rule, and original display string.

`audit_unmatched.md` lists examples of comparable true rules not recovered by MAHILDA.

`audit_diagnosis.md` groups rules by scope status and diagnosis, and lists claim-relevant uncovered examples.

`audit_claims.md` gives conservative claim text generated from the computed counts.

## Claims The Audit Can Support

If there are comparable true rules and no uncovered comparable true rules under the selected coverage criterion, the following claim is supported for the audited artifacts:

> After excluding approximate, vacuous, unparseable, and out-of-scope rules, MAHILDA recovered 100% of the remaining comparable true competitor rules under [alpha-equivalence / logical subsumption / finite-instance coverage].

If uncovered comparable true rules exist under the selected criterion, the audit does not support a 100% recall claim. The examples should be inspected as implementation bugs, scope mismatches, audit-model limitations, or evidence that MAHILDA does not recover all comparable rules.

For paper use, prefer the alpha-equivalence claim if it succeeds. A subsumption claim is acceptable only if the paper defines the subsumption relation. A finite-instance claim must be described as instance-level coverage, not logical recall.

## Claims The Audit Does Not Support

The audit does not prove that MAHILDA finds all true rules in a database.

The audit does not prove that every MAHILDA rule is useful or interesting.

The audit does not prove open-world truth; it uses closed-world truth on the SQLite database instance.

The audit does not justify calling competitor rules "wrong" or "bad" except through the formal categories reported in the CSV and summary.

The audit does not make AMIE3 RDF/triple rules comparable unless they can be translated into the relational rule representation used by MAHILDA.
