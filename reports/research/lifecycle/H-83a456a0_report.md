# Research Report: THREE_BLACK_CROWS/THREE_WHITE_SOLDIERS contain reversal information

**Hypothesis ID**: H-83a456a0
**Status**: CONCLUDED
**Conclusion**: INCONCLUSIVE
**Confidence**: LOW
**Classification**: RED

## Claim
> Inverting the direction of TBC (→BUY) and TWS (→SELL) produces positive expected value over a 60-bar horizon using the canonical shadow methodology.

## Results
| Metric | Value |
|---|---|
| N | 484 |
| Mean R | +0.2483 |
| Total R | +120.2 |
| Win Rate | 31.8% |
| 90% CI | [+0.109, +0.386] |


## Validation
- OOS (N=194): Mean R = +0.2307
- Symbols positive: 9/10
- Temporal stability: 5/5 periods positive
- Outlier robust (top-20 removed): YES

## Placebo Control
- Positive placebos: 10/14
- Passes: NO
- Placebo FAILS: 10/14 control patterns show positive R (>50% threshold). Effect appears GENERAL — not specific to hypothesis.

## Discovery Bias
- Variants tested before discovery: 24
- Bonferroni threshold: p < 0.0021
- Hypothesis discovered after testing 6 stop widths × 2 patterns × 2 directions = 24 variants. Only inverted direction at 1R stop showed strong positive.

## Conclusion
**INCONCLUSIVE**: p=1.0000 does not pass Bonferroni threshold (0.0021)

## Governance
- Human approval required: True
- Human approval granted: False

## Audit Trail
- 2026-08-13T13:53:07.998828+00:00: DETECTED → REGISTERED (Formally registered for investigation)
- 2026-08-13T13:53:08.502979+00:00: REGISTERED → TESTING (Starting experiment EXP-d871e026)
- 2026-08-13T13:53:27.414206+00:00: TESTING → CHALLENGED (Challenged with validation: OOS positive, Outlier-robust)
- 2026-08-13T13:53:27.848217+00:00: CHALLENGED → CONCLUDED (INCONCLUSIVE: p=1.0000 does not pass Bonferroni threshold (0.0021))
