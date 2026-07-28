# Horizon System Audit

---

## 1. What Horizons Currently Exist?

| Horizon | Status | Shadow Generating | Connected to Live | Max Bars | RR Target | SL Source |
|---------|--------|-------------------|-------------------|----------|-----------|-----------|
| SCALP | Production + Shadow | ✅ Yes (hshadow_ prefix) | ✅ Yes (PERMITTED_HORIZONS) | 60 | 2.0 | M5 candle geometry (2.7 pips avg) |
| INTRADAY | Shadow only | ✅ Yes (hshadow_*_INTRADAY) | ❌ No | 60 | 3.0 | M15 structure levels (11.3 pips avg) |
| EXTENDED | Shadow only | ⚠️ Minimal | ❌ No | Unknown | 4.0 | H1 swing levels |

**Key finding:** Both SCALP and INTRADAY use the same `max_bars=60` limit. The ONLY differences are:
- SL distance: SCALP=2.7 pips, INTRADAY=11.3 pips (4.2× wider)
- TP target: SCALP=2:1 RR, INTRADAY=3:1 RR

---

## 2. Per-Horizon Evidence (CURRENT Epoch)

### SCALP (Horizon Shadow — n=401)

- Also represented in 323 matched pairs
- **From matched pairs (n=323):**
  - EV: **-0.074R**
  - Win rate: 39.6%
  - Timeout: 86.4% (279/323)
  - Stop loss: 12.4%
  - Take profit: 1.2%
  - Avg bars: 54.3

### INTRADAY (Horizon Shadow — n=328)

- Also represented in 323 matched pairs
- **From matched pairs (n=323):**
  - EV: **-0.040R**
  - Win rate: 40.2%
  - Timeout: **97.5%** (315/323)
  - Stop loss: 2.5%
  - Take profit: 0.0%
  - Avg bars: 58.8

### EXTENDED (n=0 CURRENT)

No CURRENT-epoch EXTENDED shadow trades exist. ALL-epoch data (n=95) shows 100% SL exits — suggesting H1 structure levels are too tight or market noise exceeds the SL distance.

### REVERSAL Family Subset

| Metric | SCALP (n=307) | INTRADAY (n=245) |
|--------|---------------|-----------------|
| EV | -0.082R | -0.038R |
| Win rate | 39.7% | 42.0% |
| MFE | 0.419R | 0.200R |
| Timeout % | 87.0% | 97.1% |
| SL % | 12.1% | 2.9% |
| TP % | 1.0% | 0.0% |
| Avg bars | 55.2 | 58.6 |
| MFE capture | -1.58 | -1.53 |

---

## 3. Comparison: Same Entries, Different Horizons (n=323 pairs)

| Metric | SCALP | INTRADAY | Winner |
|--------|-------|----------|--------|
| EV | -0.074R | -0.040R | INTRADAY (+0.034R) |
| Win rate | 39.6% | 40.2% | INTRADAY |
| Stop loss % | 12.4% | 2.5% | INTRADAY (fewer stops) |
| Timeout % | 86.4% | 97.5% | SCALP (fewer timeouts) |
| Take profit % | 1.2% | 0.0% | SCALP (barely) |
| Avg bars | 54.3 | 58.8 | SCALP (exits earlier) |

**Answer to "Does changing the horizon improve trade outcomes without changing the entry signal?"**

**YES — marginally.** INTRADAY produces -0.040R vs SCALP's -0.074R (+0.034R improvement per trade). This is because the wider SL (11.3 pips vs 2.7 pips) allows more trades to survive initial adverse movement. However:
- Neither horizon achieves positive EV
- Both still time out overwhelmingly (86-97%)
- Neither captures meaningful profit (TP hit rate ≈ 0-1%)

The improvement is real but insufficient. The wider SL helps avoid premature stops, but the TP remains unreachable.

---

## 4. Critical Observation: Both Horizons Use max_bars=60

Both SCALP and INTRADAY horizon shadows are evaluated with the **same** `max_bars=60`. This means:
- SCALP: 60 × 5 minutes = 5 hours maximum hold
- INTRADAY: ALSO 60 × 5 minutes = 5 hours maximum hold

The INTRADAY horizon should theoretically use a LONGER holding period (e.g., 150-200 bars = 12.5-16 hours) since its TP target (3:1 RR with 11.3 pips SL = 33.9 pips TP) requires more time to reach.

**This means the Horizon system is NOT correctly testing different durations.** It's testing different SL/TP distances but the same holding period.

---

## 5. Diagnosis: What Is the Exit Problem?

| Hypothesis | Evidence | Verdict |
|-----------|----------|---------|
| A) Exit distance problem (TP too far) | TP hit rate = 0.5%. Simulation shows any TP ≤ 2R improves EV. | ✅ CONFIRMED |
| B) Holding period problem (max_bars too short) | Both horizons use max_bars=60. INTRADAY needs longer but doesn't get it. | ⚠️ PARTIALLY — max_bars may be insufficient for wider SL/TP |
| C) Entry quality problem (entries have no directional edge) | 14.5% of trades reach +0.5R, mean MFE=0.7R — entries DO have some signal | ❌ NOT THE PRIMARY ISSUE (but 85% entries are weak) |
| D) Horizon assignment problem (wrong horizon for entry) | SCALP has narrower SL → more stops. INTRADAY has wider SL → fewer stops but same timeout. | ⚠️ PARTIALLY — wider SL helps but doesn't solve timeout |

**Primary diagnosis: A + B combined.** The TP is unreachable AND the holding period doesn't differentiate between horizons. INTRADAY should hold longer but doesn't.

---

## 6. Is the Horizon System a Shadow Research System or a Production Mechanism?

**Both:**
- `PERMITTED_HORIZONS = ["SCALP"]` — only SCALP can execute live trades
- INTRADAY and EXTENDED run as shadow-only (via `hshadow_` prefix trades)
- The `HorizonExecutionAuthority` gates production access
- Shadow results are persisted to `logs/shadow_trades/` like all other shadows
- The `shadow_evaluation.py` framework reads results and produces readiness reports

The design is correct: shadow-validate higher horizons before promoting to live. But the current shadow configuration has both horizons using the same max_bars, which undermines the comparison.

---

## 7. Can the Current Horizon Infrastructure Answer: "Which trade duration and exit policy best monetises existing entry signals?"

### NO — not fully.

**What it CAN answer:**
- Whether wider SL reduces premature stops (YES — INTRADAY has 2.5% SL vs SCALP's 12.4%)
- Whether higher RR target reaches TP (NO — 0% for INTRADAY, 1% for SCALP)
- Relative performance of 2:1 vs 3:1 RR at identical max_bars (INTRADAY slightly better)

**What it CANNOT answer:**
1. **Different holding periods** — Both use max_bars=60. No evidence exists for max_bars=120 or 200 (true INTRADAY durations).
2. **Trailing stop per horizon** — No horizon shadow tests trailing exits. The improvement identified in the trailing stop experiment (+0.185R) has not been tested per-horizon.
3. **Optimal duration** — The `trade_state_progression` data could answer this but has not been analysed per-horizon (at which bar does each horizon's R peak?).
4. **EXTENDED horizon** — Zero CURRENT-epoch data. Cannot evaluate.

### What is missing:

| Gap | Impact | Fix |
|-----|--------|-----|
| INTRADAY max_bars = 60 (same as SCALP) | Cannot test whether longer hold helps INTRADAY | Set INTRADAY max_bars to 180 (15h) |
| No trailing stop in horizon shadows | Cannot test best exit per horizon | Add trailing config to horizon shadow engine |
| EXTENDED has no CURRENT data | Cannot evaluate 3rd horizon | Ensure EXTENDED shadows are created |
| No bar-by-bar analysis per horizon | Cannot find optimal exit bar per duration | Run state_progression analysis grouped by horizon |

---

## 8. Final Conclusion

The Horizon system is **architecturally correct** — it creates parallel shadow trades with different SL/TP/RR configurations from the same entry signal. The framework for evaluating horizons exists (shadow_evaluation.py, research_contract.py, observation_builder.py).

However, the current configuration **does not differentiate holding periods**, making it a test of SL/TP distance rather than a test of trade duration. The INTRADAY horizon should hold significantly longer than SCALP, but both cap at 60 bars.

The evidence shows:
- Wider SL helps marginally (INTRADAY: -0.040R vs SCALP: -0.074R)
- Neither achieves positive EV
- The primary value of the Horizon system is confirming that **exit distance alone does not solve negative EV** — it must be combined with a trailing mechanism or reduced TP

The Horizon infrastructure is a valid research tool that has produced useful evidence but is not yet configured to answer the full duration question.
