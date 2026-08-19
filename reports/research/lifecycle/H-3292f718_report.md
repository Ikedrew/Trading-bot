# Research Report: MEAN_REVERSION shows unusually strong performance

**Hypothesis ID**: H-3292f718
**Status**: CONCLUDED
**Conclusion**: VALIDATED
**Confidence**: HIGH
**Classification**: GREEN

## Claim
> MEAN_REVERSION has genuine positive edge

## Results
| Metric | Value |
|---|---|
| N | 420 |
| Mean R | +2.0032 |
| Total R | +841.4 |
| Win Rate | 70.7% |
| 90% CI | [+1.589, +2.462] |


## Dataset Provenance
| Property | Value |
|---|---|
| Dataset ID | population_CONDITIONING_ANALYSIS |
| Version | shadow_trades_v2 |
| Population | Auto: MEAN_REVERSION shows unusually strong performance |
| Observations | 420 |
| Content SHA-256 | `22356a361d1b48c3...` |
| Algorithm | SHA-256 |
| Schema | shadow_trades_v2 |
| First observation | 1785774000.0 |
| Last observation | 1786739400.0 |
| Symbols | AUDUSD, EURUSD, GBPUSD, NAS100, NZDUSD, US500, USDCAD, USDCHF, USDJPY, XAUUSD |
| Filters | pattern=['MEAN_REVERSION']; symbol=[]; direction= |

## Validation
- OOS (N=168): Mean R = +2.5337
- Symbols positive: 10/10
- Temporal stability: 4/5 periods positive
- Outlier robust (top-20 removed): YES

## Discovery Bias
- Variants tested before discovery: 2
- Bonferroni threshold: p < 0.0250
- 

## Conclusion
**VALIDATED**: Passes all gates: p=1.0000, OOS=+2.5337, symbols=10/10, placebo=PASS

## Governance
- Human approval required: True
- Human approval granted: False

## Audit Trail
- 2026-08-15T15:38:14.549329+00:00: DETECTED → REGISTERED (Formally registered for investigation)
- 2026-08-15T15:38:14.577373+00:00: REGISTERED → TESTING (Starting experiment EXP-4f5bb664)
- 2026-08-15T15:38:47.582989+00:00: TESTING → CHALLENGED (Challenged with validation: OOS positive, Outlier-robust)
- 2026-08-15T15:38:47.618119+00:00: CHALLENGED → CONCLUDED (VALIDATED: Passes all gates: p=1.0000, OOS=+2.5337, symbols=10/10, placebo=PASS)
