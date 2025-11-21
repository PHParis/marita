
# Run Report

**Date:** 2025-11-21 01:23:19
**Algorithm:** MAHILDA
**Database:** CORA.db
**Number of Rules Discovered:** 6
**Results Path:** results_all_databases/MAHILDA_CORA/MAHILDA_CORA_results.json

## Summary
- **Algorithm:** MAHILDA
- **Database:** CORA.db
- **Number of Rules Discovered:** 6
- **Results Path:** results_all_databases/MAHILDA_CORA/MAHILDA_CORA_results.json

## Top 5 Best Rules
Below are the top-5 best rules discovered based on their scores:

| Rank | Rule Description | Support  | Confidence |
|------|------------------|----------| -----------|
| 1 | ∀ x0: paper_0(paper_id=x0) ⇒ cites_0(citing_paper_id=x0) | 1.000 | 1.000 |
| 2 | ∀ x0: cites_0(citing_paper_id=x0) ⇒ paper_0(paper_id=x0) | 1.000 | 1.000 |
| 3 | ∀ x0: paper_0(paper_id=x0) ⇒ content_0(paper_id=x0) | 1.000 | 1.000 |
| 4 | ∀ x0: cites_0(citing_paper_id=x0) ∧ paper_0(paper_id=x0) ⇒ paper_1(paper_id=x0) | 0.000 | 0.000 |
| 5 | ∀ x0: cites_0(citing_paper_id=x0) ∧ paper_0(paper_id=x0) ∧ paper_1(paper_id=x0) ⇒ paper_2(paper_id=x0) | 0.000 | 0.000 |


## Details
The rule discovery process was completed successfully. The discovered rules have been saved to the specified results path.

    