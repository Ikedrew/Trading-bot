# Research Report: Gov test

**Hypothesis ID**: H-2e19f6a2
**Status**: CONCLUDED
**Conclusion**: INCONCLUSIVE
**Confidence**: LOW
**Classification**: AMBER

## Claim
> Inverting GOV_PAT direction produces positive expected value

## Results
| Metric | Value |
|---|---|
| N | 50 |
| Mean R | -1.0000 |
| Total R | -50.0 |
| Win Rate | 0.0% |
| 90% CI | [-1.000, -1.000] |


## Dataset Provenance
| Property | Value |
|---|---|
| Dataset ID | population_DIRECTION_INVERSION |
| Version | shadow_trades_v2 |
| Population | Gov exp |
| Observations | 50 |
| Content SHA-256 | `ba6572aca138bf63...` |
| Algorithm | SHA-256 |
| Schema | shadow_trades_v2 |
| First observation | 1784739300.0 |
| Last observation | 1784754000.0 |
| Symbols | EURUSD |
| Filters | pattern=['GOV_PAT']; symbol=[]; direction= |

## Validation
- OOS (N=20): Mean R = -1.0000
- Symbols positive: 0/1
- Temporal stability: 0/5 periods positive
- Outlier robust (top-20 removed): NO

## Discovery Bias
- Variants tested before discovery: 1
- Bonferroni threshold: p < 0.0500
- 

## Conclusion
**INCONCLUSIVE**: Mixed evidence — does not meet all validation criteria

## Governance
- Human approval required: True
- Human approval granted: False

## Audit Trail
- 2026-09-01T13:36:12.961354+00:00: DETECTED → REGISTERED (Formally registered for investigation)
- 2026-09-01T13:36:12.961354+00:00: REGISTERED → TESTING (Starting experiment EXP-7d1e2a0e)
- 2026-09-01T13:36:13.197579+00:00: TESTING → CHALLENGED (Challenged with validation: tests complete)
- 2026-09-01T13:36:13.197579+00:00: CHALLENGED → CONCLUDED (INCONCLUSIVE: Mixed evidence — does not meet all validation criteria)
