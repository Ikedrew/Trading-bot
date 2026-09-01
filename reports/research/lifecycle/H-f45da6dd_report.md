# Research Report: Persistence Test

**Hypothesis ID**: H-f45da6dd
**Status**: CONCLUDED
**Conclusion**: INCONCLUSIVE
**Confidence**: LOW
**Classification**: AMBER

## Claim
> c

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
| Version | shadow_trades_v1 |
| Population | Persistence Experiment |
| Observations | 50 |
| Content SHA-256 | `541ad9abba77007d...` |
| Algorithm | SHA-256 |
| Schema | shadow_trades_v1 |
| First observation | 1784739300.0 |
| Last observation | 1784754000.0 |
| Symbols | EURUSD |
| Filters | pattern=['THREE_BLACK_CROWS']; symbol=[]; direction= |

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
- 2026-09-01T20:36:53.209767+00:00: DETECTED → REGISTERED (Formally registered for investigation)
- 2026-09-01T20:36:53.227389+00:00: REGISTERED → TESTING (Starting experiment EXP-758a848a)
- 2026-09-01T20:36:53.747248+00:00: TESTING → CHALLENGED (Challenged with validation: tests complete)
- 2026-09-01T20:36:53.747248+00:00: CHALLENGED → CONCLUDED (INCONCLUSIVE: Mixed evidence — does not meet all validation criteria)
