# SV1 Pre-Experiment Validity Audit

## Checklist

| Requirement | Status | Evidence |
|-------------|--------|---------|
| ✅ CURRENT epoch only | PASS | `load_shadow_trades(epoch='CURRENT')` — 867 total, 323 pairs |
| ✅ Same opportunity paired | PASS | Matched by cycle_id + symbol (323 pairs) |
| ✅ Same entry trigger | PASS | Same pattern fires for both (100% match verified) |
| ✅ Same direction | PASS | Direction identical in 100% of sample (300/300 checks) |
| ✅ Same pattern | PASS | Pattern identical in 100% of sample |
| ✅ Same score | PASS | Score within 0.01 for 100% of sample |
| ✅ Same H1 bias | PASS | H1 bias identical in 100% of sample |
| ✅ Same execution timing | PASS | timestamp_decision_utc identical in 100% |
| ✅ Same exit policy | PASS | Simulated SL + timeout (no TP) on BOTH variants |
| ✅ Same timeout | PASS | max_bars=60 for both |
| ✅ Same TP logic | PASS | No TP applied (both use tp_r=99, unreachable) |
| ✅ Only one variable differs | PASS (after mitigation) | See below |

## Variable Isolation

**Raw data has TWO differences:**
- SL distance: SCALP=2.56 pips, INTRADAY=5.64 pips (2.2× wider)
- RR target: SCALP=2:1, INTRADAY=3:1

**Mitigation applied:** Both variants simulated with IDENTICAL exit logic:
- Exit at -1R (normalised to each variant's own risk) OR timeout at 60 bars
- NO take-profit applied to either
- This isolates the single variable: **SL distance / risk geometry**

## Single Differing Variable

**Stop loss distance (risk geometry):**
- Control: M5 candle SL (2.56 pips median)
- Variant: M15 structure SL (5.64 pips median, 2.2× wider)

Everything else is provably identical.

## VERDICT: ✅ VALID — Proceed to execution.
