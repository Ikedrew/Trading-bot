# Research Report: CHAIN_PAT shows catastrophic performance

**Hypothesis ID**: H-a0e60727
**Status**: CONCLUDED
**Conclusion**: INCONCLUSIVE
**Confidence**: LOW
**Classification**: RED

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
**INCONCLUSIVE**: p=1.0000 does not pass Bonferroni threshold (0.0500)

## Governance
- Human approval required: True
- Human approval granted: False

## Audit Trail
- 2026-08-13T16:46:14.425272+00:00: DETECTED → REGISTERED (Formally registered for investigation)
- 2026-08-13T16:46:14.460369+00:00: REGISTERED → TESTING (Starting experiment EXP-d8c6ea77)
- 2026-08-13T16:46:15.131069+00:00: TESTING → CHALLENGED (Challenged with validation: tests complete)
- 2026-08-13T16:46:15.224586+00:00: CHALLENGED → CONCLUDED (INCONCLUSIVE: p=1.0000 does not pass Bonferroni threshold (0.0500))
