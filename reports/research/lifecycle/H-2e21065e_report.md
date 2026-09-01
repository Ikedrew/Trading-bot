# Research Report: Test Investigation Hypothesis

**Hypothesis ID**: H-2e21065e
**Status**: CONCLUDED
**Conclusion**: INCONCLUSIVE
**Confidence**: LOW
**Classification**: AMBER

## Claim
> Inversion produces positive R

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
| Population | Test Direction Inversion |
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
- Variants tested before discovery: 10
- Bonferroni threshold: p < 0.0050
- 

## Conclusion
**INCONCLUSIVE**: Mixed evidence — does not meet all validation criteria

## Governance
- Human approval required: True
- Human approval granted: False

## Audit Trail
- 2026-09-01T20:39:59.259082+00:00: DETECTED → REGISTERED (Formally registered for investigation)
- 2026-09-01T20:39:59.263112+00:00: REGISTERED → TESTING (Starting experiment EXP-66846616)
- 2026-09-01T20:39:59.563354+00:00: TESTING → CHALLENGED (Challenged with validation: tests complete)
- 2026-09-01T20:39:59.568399+00:00: CHALLENGED → CONCLUDED (INCONCLUSIVE: Mixed evidence — does not meet all validation criteria)
