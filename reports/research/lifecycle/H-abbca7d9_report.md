# Research Report: Reconstruction Test

**Hypothesis ID**: H-abbca7d9
**Status**: CONCLUDED
**Conclusion**: INCONCLUSIVE
**Confidence**: LOW
**Classification**: AMBER

## Claim
> X is better than Y

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
| Population | Reconstruction Experiment |
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

## Placebo Control
- Positive placebos: 0/1
- Passes: YES
- Placebo PASSES: 0/1 control patterns show positive R (≤50% threshold). Effect appears SPECIFIC to hypothesis population.

## Discovery Bias
- Variants tested before discovery: 5
- Bonferroni threshold: p < 0.0100
- Found after testing 5 variants

## Conclusion
**INCONCLUSIVE**: Mixed evidence — does not meet all validation criteria

## Governance
- Human approval required: True
- Human approval granted: False

## Audit Trail
- 2026-08-14T13:00:58.888348+00:00: DETECTED → REGISTERED (Formally registered for investigation)
- 2026-08-14T13:00:58.892345+00:00: REGISTERED → TESTING (Starting experiment EXP-2c28ff37)
- 2026-08-14T13:00:59.499353+00:00: TESTING → CHALLENGED (Challenged with validation: Placebo passes)
- 2026-08-14T13:00:59.501349+00:00: CHALLENGED → CONCLUDED (INCONCLUSIVE: Mixed evidence — does not meet all validation criteria)
