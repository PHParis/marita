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
uv run mahilda audit --no-progress
```

`--strict` exits with code `2` if any comparable true competitor rule is not recovered by MAHILDA.
Progress bars are enabled by default and show one bar per competitor algorithm. Use `--no-progress` for CI logs or redirected output.

## Inputs

The audit expects the benchmark layout already used by the repository:

```text
results/paper_table2/
  MAHILDA/MAHILDA_<DB>/MAHILDA_<DB>_results.json
  MATILDA/MATILDA_<DB>/MATILDA_<DB>_results.json
  AMIE3/AMIE3_<DB>/AMIE3_<DB>_results.json
  SPIDER/SPIDER_<DB>/SPIDER_<DB>_results.json
  POPPER/POPPER_<DB>/POPPER_<DB>_results.json

data/relational/<DB>.db
```

The audit recomputes support and confidence from the SQLite database. It does not trust exported confidence fields as proof of exactness.

## Rule Categories

Each competitor rule receives exactly one formal classification.

`parse_failed`: the rule cannot be parsed into a known representation.

`out_of_scope`: the rule is parseable or recognized, but not comparable to MAHILDA's audited target class. Examples include RDF/triple AMIE3 rules that cannot be translated to relational atoms, ILP/Popper rules that remain outside the relational formula parser, rules with head-only variables, non-FK joins, missing database tables or columns, and unsupported inclusion-dependency shapes.

`vacuous`: the rule is formally redundant under the audit definition. The current checks mark a rule vacuous when the head atom is already present in the body after canonicalization, or when repeated occurrences of the same relation force reuse of the same primary-key tuple under relation-disjoint semantics.

`approximate`: the rule is inside scope and evaluable, but its recomputed confidence is below the configured threshold. The default threshold is `1.0`, so only rules true on every body match are treated as exact.

`comparable_true`: the rule is parseable, in scope, non-vacuous, and exact on the database instance.

## Matching Against MAHILDA

Comparable true rules are matched against MAHILDA rules for the same database.

`recalled_alpha`: the competitor rule and a MAHILDA rule have the same canonical form modulo variable renaming and body atom order.

`recalled_subsumed`: no alpha-equivalent MAHILDA rule exists, but a MAHILDA rule with the same canonical head has a body contained in the competitor body under the current canonical-body check.

`unmatched`: no alpha-equivalent or subsuming MAHILDA rule was found.

The primary recall claim should use alpha-equivalence. Subsumption recall is reported separately because it is a more permissive criterion.

## Outputs

The command writes:

```text
audit_summary.json
audit_rules.csv
audit_unmatched.md
audit_claims.md
```

`audit_summary.json` contains totals by algorithm and database.

`audit_rules.csv` contains one row per audited competitor rule: source, classification, reason, recomputed support/confidence, canonical rule, match status, matched MAHILDA rule, and original display string.

`audit_unmatched.md` lists examples of comparable true rules not recovered by MAHILDA.

`audit_claims.md` gives conservative claim text generated from the computed counts.

## Claims The Audit Can Support

If there are comparable true rules and no unmatched comparable true rules, the following claim is supported for the audited artifacts:

> After excluding approximate, vacuous, unparseable, and out-of-scope rules, MAHILDA recovered 100% of the remaining comparable true competitor rules under the reported audit criterion.

If unmatched comparable true rules exist, the audit does not support a 100% recall claim. The unmatched examples should be inspected as implementation bugs, scope mismatches, or evidence that MAHILDA does not recover all comparable rules.

## Claims The Audit Does Not Support

The audit does not prove that MAHILDA finds all true rules in a database.

The audit does not prove that every MAHILDA rule is useful or interesting.

The audit does not prove open-world truth; it uses closed-world truth on the SQLite database instance.

The audit does not justify calling competitor rules "wrong" or "bad" except through the formal categories reported in the CSV and summary.

The audit does not make AMIE3 RDF/triple rules comparable unless they can be translated into the relational rule representation used by MAHILDA.
