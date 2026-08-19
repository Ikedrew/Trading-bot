# Research Report: Test Investigation Hypothesis

**Hypothesis ID**: H-d70808a8
**Status**: CONCLUDED
**Conclusion**: INCONCLUSIVE
**Confidence**: LOW
**Classification**: RED

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
- Positive placebos: 0/5
- Passes: YES
- Placebo PASSES: 0/5 control patterns show positive R (≤50% threshold). Effect appears SPECIFIC to hypothesis population.

## Discovery Bias
- Variants tested before discovery: 10
- Bonferroni threshold: p < 0.0050
- 

## Conclusion
**INCONCLUSIVE**: p=1.0000 does not pass Bonferroni threshold (0.0050)

## Governance
- Human approval required: True
- Human approval granted: False

## Audit Trail
- 2026-08-13T17:10:29.722834+00:00: DETECTED → REGISTERED (Formally registered for investigation)
- 2026-08-13T17:10:29.786835+00:00: REGISTERED → TESTING (Starting experiment EXP-a789172a)
- 2026-08-13T17:10:30.224720+00:00: TESTING → CHALLENGED (Challenged with validation: Placebo passes)
- 2026-08-13T17:10:30.230731+00:00: CHALLENGED → CONCLUDED (INCONCLUSIVE: p=1.0000 does not pass Bonferroni threshold (0.0050))
