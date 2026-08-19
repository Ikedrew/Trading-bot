# Research Report: Persistence Test

**Hypothesis ID**: H-efd1b2c3
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
| Version | shadow_trades_v2 |
| Population | Persistence Experiment |
| Observations | 50 |
| Content SHA-256 | `541ad9abba77007d...` |
| Algorithm | SHA-256 |
| Schema | shadow_trades_v2 |
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
- 2026-08-14T09:57:25.702039+00:00: DETECTED → REGISTERED (Formally registered for investigation)
- 2026-08-14T09:57:25.704065+00:00: REGISTERED → TESTING (Starting experiment EXP-82a21150)
- 2026-08-14T09:57:26.312038+00:00: TESTING → CHALLENGED (Challenged with validation: tests complete)
- 2026-08-14T09:57:26.314047+00:00: CHALLENGED → CONCLUDED (INCONCLUSIVE: Mixed evidence — does not meet all validation criteria)
