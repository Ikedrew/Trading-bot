# Research Report: THREE_BLACK_CROWS/THREE_WHITE_SOLDIERS contain reversal information

**Hypothesis ID**: H-cfb55e3a
**Status**: CONCLUDED
**Conclusion**: REJECTED
**Confidence**: HIGH
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
| Permutation p | 0.0002 |

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
**REJECTED**: Placebo FAILS: 71% of controls positive (threshold 50%). Effect is general, not specific.

## Governance
- Human approval required: True
- Human approval granted: False

## Audit Trail
- 2026-08-13T14:06:34.571654+00:00: DETECTED → REGISTERED (Formally registered for investigation)
- 2026-08-13T14:06:34.601636+00:00: REGISTERED → TESTING (Starting experiment EXP-d718ca4a)
- 2026-08-13T14:06:45.847854+00:00: TESTING → CHALLENGED (Challenged with validation: OOS positive, Outlier-robust)
- 2026-08-13T14:06:45.903431+00:00: CHALLENGED → CONCLUDED (REJECTED: Placebo FAILS: 71% of controls positive (threshold 50%). Effect is general, not specific.)
