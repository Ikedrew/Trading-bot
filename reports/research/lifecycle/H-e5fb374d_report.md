# Research Report: CHAIN_PAT shows catastrophic performance

**Hypothesis ID**: H-e5fb374d
**Status**: CONCLUDED
**Conclusion**: INCONCLUSIVE
**Confidence**: LOW
**Classification**: AMBER

## Claim
> Inverting CHAIN_PAT direction produces positive expected value

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
| Population | Auto-investigation: CHAIN_PAT shows catastrophic performance |
| Observations | 50 |
| Content SHA-256 | `b62a7fac2ee9cdbe...` |
| Algorithm | SHA-256 |
| Schema | shadow_trades_v2 |
| First observation | 1784739300.0 |
| Last observation | 1784754000.0 |
| Symbols | EURUSD |
| Filters | pattern=['CHAIN_PAT']; symbol=[]; direction= |

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
- 2026-09-01T13:49:03.529275+00:00: DETECTED → REGISTERED (Formally registered for investigation)
- 2026-09-01T13:49:03.654262+00:00: REGISTERED → TESTING (Starting experiment EXP-18cdd52f)
- 2026-09-01T13:49:04.090058+00:00: TESTING → CHALLENGED (Challenged with validation: tests complete)
- 2026-09-01T13:49:04.105676+00:00: CHALLENGED → CONCLUDED (INCONCLUSIVE: Mixed evidence — does not meet all validation criteria)
