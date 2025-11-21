
# Run Report

**Date:** 2025-11-21 04:04:26
**Algorithm:** MAHILDA
**Database:** yago.db
**Number of Rules Discovered:** 43
**Results Path:** results_all_databases/MAHILDA_yago/MAHILDA_yago_results.json

## Summary
- **Algorithm:** MAHILDA
- **Database:** yago.db
- **Number of Rules Discovered:** 43
- **Results Path:** results_all_databases/MAHILDA_yago/MAHILDA_yago_results.json

## Top 5 Best Rules
Below are the top-5 best rules discovered based on their scores:

| Rank | Rule Description | Support  | Confidence |
|------|------------------|----------| -----------|
| 1 | ∀ x0, y0: CreativeWork_0(id=x0) ∧ CreativeWork_1(id=x0, material=y0) ∧ CreativeWork_3(id=x0) ∧ Product_0(id=y0) ⇒ CreativeWork_2(id=x0) | 4.000 | 0.000 |
| 2 | ∀ x0, y0: CreativeWork_1(id=x0, material=y0) ∧ CreativeWork_2(id=x0) ∧ CreativeWork_3(id=x0) ∧ Product_0(id=y0) ⇒ CreativeWork_0(id=x0) | 4.000 | 0.000 |
| 3 | ∀ x0, y0: CreativeWork_0(id=y0) ∧ CreativeWork_1(id=y0, material=x0) ∧ CreativeWork_2(id=y0) ∧ CreativeWork_3(id=y0) ⇒ Product_0(id=x0) | 2.000 | 0.667 |
| 4 | ∀ x0, x1: CreativeWork_0(id=x0) ∧ CreativeWork_2(id=x0) ∧ Product_0(id=x1) ⇒ CreativeWork_1(id=x0, material=x1) | 1.333 | 4.000 |
| 5 | ∀ x0: Person_0(parentTaxon=x0) ⇒ Taxon_0(id=x0) | 1.000 | 1.000 |


## Details
The rule discovery process was completed successfully. The discovered rules have been saved to the specified results path.

    