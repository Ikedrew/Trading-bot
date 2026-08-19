# Research Report: NAS100 shows +0.483R anomaly vs other symbols

**Hypothesis ID**: H-7e98ca45
**Status**: CONCLUDED
**Conclusion**: INCONCLUSIVE
**Confidence**: LOW
**Classification**: AMBER

## Claim
> V10 performance on NAS100 is materially different from portfolio

## Results
| Metric | Value |
|---|---|
| N | 109 |
| Mean R | +0.7618 |
| Total R | +83.0 |
| Win Rate | 34.9% |
| 90% CI | [+0.068, +1.634] |


## Dataset Provenance
| Property | Value |
|---|---|
| Dataset ID | population_CONDITIONING_ANALYSIS |
| Version | shadow_trades_v2 |
| Population | Auto: NAS100 shows +0.483R anomaly vs other symbols |
| Observations | 109 |
| Content SHA-256 | `1a9504ac9644287e...` |
| Algorithm | SHA-256 |
| Schema | shadow_trades_v2 |
| First observation | 1785342900.0 |
| Last observation | 1786725900.0 |
| Symbols | NAS100 |
| Filters | pattern=[]; symbol=['NAS100']; direction= |

## Validation
- OOS (N=44): Mean R = +2.1145
- Symbols positive: 1/1
- Temporal stability: 1/5 periods positive
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
- 2026-08-15T15:55:52.743816+00:00: DETECTED → REGISTERED (Formally registered for investigation)
- 2026-08-15T15:55:52.782901+00:00: REGISTERED → TESTING (Starting experiment EXP-2834fef3)
- 2026-08-15T15:55:54.868554+00:00: TESTING → CHALLENGED (Challenged with validation: OOS positive)
- 2026-08-15T15:55:54.899492+00:00: CHALLENGED → CONCLUDED (INCONCLUSIVE: Mixed evidence — does not meet all validation criteria)
