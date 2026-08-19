# Research Report: MEAN_REVERSION shows unusually strong performance

**Hypothesis ID**: H-fcf078b8
**Status**: CONCLUDED
**Conclusion**: VALIDATED
**Confidence**: HIGH
**Classification**: GREEN

## Claim
> MEAN_REVERSION has genuine positive edge

## Results
| Metric | Value |
|---|---|
| N | 407 |
| Mean R | +2.2055 |
| Total R | +897.6 |
| Win Rate | 70.3% |
| 90% CI | [+1.695, +2.787] |


## Dataset Provenance
| Property | Value |
|---|---|
| Dataset ID | population_CONDITIONING_ANALYSIS |
| Version | shadow_trades_v2 |
| Population | Auto: MEAN_REVERSION shows unusually strong performance |
| Observations | 407 |
| Content SHA-256 | `a3d7e1c5665c11df...` |
| Algorithm | SHA-256 |
| Schema | shadow_trades_v2 |
| First observation | 1785774000.0 |
| Last observation | 1786734000.0 |
| Symbols | AUDUSD, EURUSD, GBPUSD, NAS100, NZDUSD, US500, USDCAD, USDCHF, USDJPY, XAUUSD |
| Filters | pattern=['MEAN_REVERSION']; symbol=[]; direction= |

## Validation
- OOS (N=163): Mean R = +3.5591
- Symbols positive: 10/10
- Temporal stability: 4/5 periods positive
- Outlier robust (top-20 removed): YES

## Discovery Bias
- Variants tested before discovery: 2
- Bonferroni threshold: p < 0.0250
- 

## Conclusion
**VALIDATED**: Passes all gates: p=1.0000, OOS=+3.5591, symbols=10/10, placebo=PASS

## Governance
- Human approval required: True
- Human approval granted: False

## Audit Trail
- 2026-08-14T16:52:48.550894+00:00: DETECTED → REGISTERED (Formally registered for investigation)
- 2026-08-14T16:52:48.561901+00:00: REGISTERED → TESTING (Starting experiment EXP-9782906b)
- 2026-08-14T16:52:57.298419+00:00: TESTING → CHALLENGED (Challenged with validation: OOS positive, Outlier-robust)
- 2026-08-14T16:52:57.338410+00:00: CHALLENGED → CONCLUDED (VALIDATED: Passes all gates: p=1.0000, OOS=+3.5591, symbols=10/10, placebo=PASS)
