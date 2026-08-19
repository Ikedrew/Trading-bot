# Research Report: Test Investigation Hypothesis

**Hypothesis ID**: H-71fb6f75
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
| Version | shadow_trades_v2 |
| Population | Test Direction Inversion |
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
- Positive placebos: 0/2
- Passes: YES
- Placebo PASSES: 0/2 control patterns show positive R (≤50% threshold). Effect appears SPECIFIC to hypothesis population.

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
- 2026-08-14T09:57:32.840644+00:00: DETECTED → REGISTERED (Formally registered for investigation)
- 2026-08-14T09:57:32.847646+00:00: REGISTERED → TESTING (Starting experiment EXP-08efe43c)
- 2026-08-14T09:57:33.909615+00:00: TESTING → CHALLENGED (Challenged with validation: Placebo passes)
- 2026-08-14T09:57:33.912606+00:00: CHALLENGED → CONCLUDED (INCONCLUSIVE: Mixed evidence — does not meet all validation criteria)
