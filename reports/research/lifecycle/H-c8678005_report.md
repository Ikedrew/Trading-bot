# Research Report: Test Investigation Hypothesis

**Hypothesis ID**: H-c8678005
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
- 2026-08-13T16:17:27.752407+00:00: DETECTED → REGISTERED (Formally registered for investigation)
- 2026-08-13T16:17:27.756406+00:00: REGISTERED → TESTING (Starting experiment EXP-b516d6c3)
- 2026-08-13T16:17:28.398411+00:00: TESTING → CHALLENGED (Challenged with validation: tests complete)
- 2026-08-13T16:17:28.418395+00:00: CHALLENGED → CONCLUDED (INCONCLUSIVE: p=1.0000 does not pass Bonferroni threshold (0.0050))
