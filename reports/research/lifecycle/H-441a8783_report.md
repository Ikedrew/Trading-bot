# Research Report: Score non-monotonicity: 2 inversions, Q4-Q1=-0.963R

**Hypothesis ID**: H-441a8783
**Status**: CONCLUDED
**Conclusion**: VALIDATED
**Confidence**: HIGH
**Classification**: GREEN

## Claim
> V10 score does not monotonically predict outcome quality

## Results
| Metric | Value |
|---|---|
| N | 2308 |
| Mean R | +0.3475 |
| Total R | +802.0 |
| Win Rate | 39.6% |
| 90% CI | [+0.256, +0.444] |


## Dataset Provenance
| Property | Value |
|---|---|
| Dataset ID | population_CONDITIONING_ANALYSIS |
| Version | shadow_trades_v2 |
| Population | Auto: Score non-monotonicity: 2 inversions, Q4-Q1=-0.963R |
| Observations | 2308 |
| Content SHA-256 | `9a9991b10763f8e1...` |
| Algorithm | SHA-256 |
| Schema | shadow_trades_v2 |
| First observation | 1784736600.0 |
| Last observation | 1786739400.0 |
| Symbols | AUDUSD, EURUSD, GBPUSD, NAS100, NZDUSD, US500, USDCAD, USDCHF, USDJPY, XAUUSD |
| Filters | pattern=[]; symbol=[]; direction= |

## Validation
- OOS (N=924): Mean R = +0.9325
- Symbols positive: 9/10
- Temporal stability: 3/5 periods positive
- Outlier robust (top-20 removed): YES

## Discovery Bias
- Variants tested before discovery: 2
- Bonferroni threshold: p < 0.0250
- 

## Conclusion
**VALIDATED**: Passes all gates: p=1.0000, OOS=+0.9325, symbols=9/10, placebo=PASS

## Governance
- Human approval required: True
- Human approval granted: False

## Audit Trail
- 2026-08-14T22:43:06.325331+00:00: DETECTED → REGISTERED (Formally registered for investigation)
- 2026-08-14T22:43:06.348326+00:00: REGISTERED → TESTING (Starting experiment EXP-34a3977d)
- 2026-08-14T22:43:29.055923+00:00: TESTING → CHALLENGED (Challenged with validation: OOS positive, Outlier-robust)
- 2026-08-14T22:43:29.105933+00:00: CHALLENGED → CONCLUDED (VALIDATED: Passes all gates: p=1.0000, OOS=+0.9325, symbols=9/10, placebo=PASS)
