# Research Report: THREE_BLACK_CROWS/THREE_WHITE_SOLDIERS contain reversal information

**Hypothesis ID**: H-b858cb19
**Status**: CONCLUDED
**Conclusion**: INCONCLUSIVE
**Confidence**: LOW
**Classification**: RED

## Claim
> Inverting the direction of TBC (→BUY) and TWS (→SELL) produces positive expected value over a 60-bar horizon using the canonical shadow methodology.

## Results
| Metric | Value |
|---|---|
| N | 0 |
| Mean R | +0.0000 |
| Total R | +0.0 |
| Win Rate | 0.0% |



## Dataset Provenance
*Fingerprint: UNAVAILABLE (historical experiment)*

## Validation
- OOS (N=0): Mean R = +0.0000
- Symbols positive: 0/0
- Temporal stability: 0/0 periods positive
- Outlier robust (top-20 removed): NO

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
- 2026-08-13T14:22:36.940862+00:00: DETECTED → REGISTERED (Formally registered for investigation)
- 2026-08-13T14:22:36.944545+00:00: REGISTERED → TESTING (Starting experiment EXP-61c9796d)
- 2026-08-13T14:22:39.227279+00:00: TESTING → CHALLENGED (Challenged with validation: tests complete)
- 2026-08-13T14:22:40.014933+00:00: CHALLENGED → CONCLUDED (INCONCLUSIVE: p=1.0000 does not pass Bonferroni threshold (0.0021))
