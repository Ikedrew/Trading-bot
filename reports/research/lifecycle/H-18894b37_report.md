# Research Report: NZDUSD shows -0.879R anomaly vs other symbols

**Hypothesis ID**: H-18894b37
**Status**: CONCLUDED
**Conclusion**: INCONCLUSIVE
**Confidence**: LOW
**Classification**: AMBER

## Claim
> V10 performance on NZDUSD is materially different from portfolio

## Results
| Metric | Value |
|---|---|
| N | 353 |
| Mean R | +0.3217 |
| Total R | +113.6 |
| Win Rate | 42.5% |
| 90% CI | [+0.166, +0.485] |


## Dataset Provenance
| Property | Value |
|---|---|
| Dataset ID | population_CONDITIONING_ANALYSIS |
| Version | shadow_trades_v2 |
| Population | Auto: NZDUSD shows -0.879R anomaly vs other symbols |
| Observations | 353 |
| Content SHA-256 | `f917334de1ecd9fa...` |
| Algorithm | SHA-256 |
| Schema | shadow_trades_v2 |
| First observation | 1784739900.0 |
| Last observation | 1786734600.0 |
| Symbols | NZDUSD |
| Filters | pattern=[]; symbol=['NZDUSD']; direction= |

## Validation
- OOS (N=142): Mean R = +0.8924
- Symbols positive: 1/1
- Temporal stability: 3/5 periods positive
- Outlier robust (top-20 removed): YES

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
- 2026-08-15T15:58:35.797511+00:00: DETECTED → REGISTERED (Formally registered for investigation)
- 2026-08-15T15:58:35.827828+00:00: REGISTERED → TESTING (Starting experiment EXP-52c6f6b1)
- 2026-08-15T15:58:40.820191+00:00: TESTING → CHALLENGED (Challenged with validation: OOS positive, Outlier-robust)
- 2026-08-15T15:58:40.850589+00:00: CHALLENGED → CONCLUDED (INCONCLUSIVE: Mixed evidence — does not meet all validation criteria)
