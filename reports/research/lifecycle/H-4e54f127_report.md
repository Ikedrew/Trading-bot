# Research Report: Fingerprint Test

**Hypothesis ID**: H-4e54f127
**Status**: CONCLUDED
**Conclusion**: INCONCLUSIVE
**Confidence**: LOW
**Classification**: RED

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
| Population | FP Exp |
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
**INCONCLUSIVE**: p=1.0000 does not pass Bonferroni threshold (0.0500)

## Governance
- Human approval required: True
- Human approval granted: False

## Audit Trail
- 2026-08-14T00:32:03.563067+00:00: DETECTED → REGISTERED (Formally registered for investigation)
- 2026-08-14T00:32:03.568069+00:00: REGISTERED → TESTING (Starting experiment EXP-0f0ae1a3)
- 2026-08-14T00:32:04.082367+00:00: TESTING → CHALLENGED (Challenged with validation: tests complete)
- 2026-08-14T00:32:04.084368+00:00: CHALLENGED → CONCLUDED (INCONCLUSIVE: p=1.0000 does not pass Bonferroni threshold (0.0500))
