# Structure-Based Entry Hypothesis Audit

---

## 1. What Structure Information Already Exists?

The system has EXTENSIVE structure data already computed and available:

### H1 Structure (via MarketContext.h1)

| Field | Source | Timeframe | Available Before Entry? | Linkable to Outcomes? |
|-------|--------|-----------|------------------------|----------------------|
| `h1.swing_structure` | H1 candle analysis | H1 | ✅ Yes | ✅ Via shadow trade phase |
| `h1.bos_confirmed` | Break of Structure detection | H1 | ✅ Yes | ✅ Via phase=IMPULSE proxy |
| `h1.bos_direction` | BOS direction (BULLISH/BEARISH) | H1 | ✅ Yes | ⚠️ Not directly in shadow |
| `h1.direction` | Overall H1 bias | H1 | ✅ Yes | ✅ h1_bias field (89%) |
| `h1.ema_position` | Price vs H1 EMA | H1 | ✅ Yes | ⚠️ Not in shadow |

### M15 Structure (via MarketContext.m15)

| Field | Source | Timeframe | Available? | Linkable? |
|-------|--------|-----------|-----------|-----------|
| `m15.at_key_level` | Support/resistance proximity | M15 | ✅ Yes | ⚠️ Via strategy_observations only |
| `m15.order_block_present` | Institutional interest zone | M15 | ✅ Yes | ⚠️ Via strategy_observations |
| `m15.quality_score` | Structure readability | M15 | ✅ Yes | ⚠️ Via strategy_observations |
| `m15.nearest_support` | Closest support price | M15 | ✅ Yes | ⚠️ Not in shadow |
| `m15.nearest_resistance` | Closest resistance price | M15 | ✅ Yes | ⚠️ Not in shadow |

### H4 Structure (via MarketContext.h4 / regime)

| Field | Source | Timeframe | Available? | Linkable? |
|-------|--------|-----------|-----------|-----------|
| `h4.regime` | HH/HL vs LH/LL classification | H4 | ✅ Yes | ✅ regime field (100%) |
| `h4.trend_bias` | BULLISH/BEARISH/NEUTRAL | H4 | ✅ Yes | ⚠️ Partially |
| `h4.trend_strength` | Structure conviction | H4 | ✅ Yes | ⚠️ Partially |

### M5 Swing Context (computed per cycle)

| Field | Source | Timeframe | Available? | Linkable? |
|-------|--------|-----------|-----------|-----------|
| `swing_direction` | HH/HL or LH/LL detection | M5 | ✅ At engine time | ⚠️ In decision_trace only (31%) |
| `swing_break_confirmed` | M5 BOS | M5 | ✅ At engine time | ⚠️ In decision_trace only (31%) |
| `last_swing_high/low` | Pivot levels | M5 | ✅ Computed | ❌ Not persisted |

### Phase Classification (derived FROM structure)

| Field | Derivation | Coverage |
|-------|-----------|----------|
| `market_phase` | H1.swing_structure + H1.bos_confirmed + H1.bos_direction | **89% in shadow trades** |
| IMPULSE | = HH_HL + BOS confirmed in trend direction | 273 trades (31.5%) |
| PULLBACK | = Directional structure present but no BOS | 150 trades (17.3%) |
| REVERSAL | = BOS confirmed against prior direction | 149 trades (17.2%) |
| CONSOLIDATION | = Mixed structure, no clear direction | 171 trades (19.7%) |
| EXHAUSTION | = Extended structure showing fatigue | 32 trades (3.7%) |

---

## 2. Critical Insight: Market Phase IS a Structure Proxy

**The phase field already encodes structure state.** When EQ1 tested cost-adjusted EV by phase, it was effectively testing "does BOS/structure predict direction?"

| Phase | Structure Meaning | n | Cost-Adj EV | Result |
|-------|-------------------|---|-------------|--------|
| IMPULSE | BOS confirmed, trend continuing | 273 | **-0.656R** | ❌ |
| PULLBACK | Structure present, no BOS | 150 | **-0.411R** | ❌ |
| CONSOLIDATION | No clear structure | 171 | **-0.582R** | ❌ |
| REVERSAL | BOS against prior direction (≈ CHOCH) | 149 | **-0.993R** | ❌ |
| EXHAUSTION | Structure fatiguing | 32 | **-0.476R** | ❌ |

**The "BOS predicts direction" hypothesis has ALREADY been tested indirectly:**
- IMPULSE = BOS confirmed → EV = -0.656R (worst after costs)
- REVERSAL = structural reversal (CHOCH) → EV = -0.993R (catastrophic)

Both are deeply negative. Structure events as currently detected do NOT predict direction after costs.

---

## 3. Structure vs Current Architecture Comparison

### Current System

```
M5 bar closes
    ↓
Candlestick pattern detected (shape only)
    ↓
Direction assigned (HAMMER→BUY, etc.)
    ↓
Context checked (H1 bias, regime, phase)
    ↓
Score computed (10 factors)
    ↓
Risk check → Execute
```

**Failure point:** Pattern shape does not predict direction. Context helps marginally but not enough to overcome costs.

### Structure Hypothesis

```
H1 structure shift detected (BOS/CHOCH)
    ↓
Directional bias established from structure
    ↓
Wait for pullback to structure level
    ↓
Entry trigger confirms (pattern + structure alignment)
    ↓
SL placed beyond structure level (wider = lower cost ratio)
    ↓
Execute
```

**Claimed improvements over current:**

| Problem | Current | Structure Hypothesis |
|---------|---------|---------------------|
| Directional evidence | Pattern shape only | BOS/CHOCH (structural shift) |
| Risk geometry | 3.5 pip M5 candle SL | Structure invalidation (15-30+ pips) |
| Cost ratio | 48% of risk | ~5-10% of risk |
| Entry quality | Any pattern any time | Only after structural event + pullback |
| Trade frequency | 867 trades in period | Likely much fewer (higher quality) |

### What Information Is ADDED?

1. **Causal directional evidence:** BOS means market PROVED control changed (not just a candle shape)
2. **Structural invalidation level:** SL at structure break = wider SL with LOGICAL reason
3. **Confluence:** Entry only when H1 structure + M15 level + M5 trigger align
4. **Quality filter:** Reduces trade count to only structural setups

### What Failure Modes Does It Solve?

| Failure | Solved? |
|---------|---------|
| Zero directional edge | ⚠️ MAYBE — structure is a stronger directional claim than candle shape, but untested |
| Catastrophic cost ratio | ✅ LIKELY — structure SL is 15-30 pips (cost drops to 5-10%) |
| Too many losing trades | ✅ LIKELY — structure events are rarer, reducing low-quality entries |

### What Failure Modes REMAIN?

| Remaining Risk | Why |
|----------------|-----|
| Structure detection may not predict future movement | BOS/CHOCH in M5 FX may have no more predictive value than patterns |
| False structural breaks | Price often breaks structure then reverses |
| Reduced trade frequency = slower validation | Fewer signals = longer to prove/disprove |
| Same underlying market (M5 FX noise) | The market itself may be unpredictable at this scale regardless of entry method |

---

## 4. Structure Entry Research Questions

### SQ1: Does BOS predict directional movement after costs?

**Hypothesis:** After confirmed H1 BOS in direction X, price continues in direction X more often than not, with sufficient magnitude to overcome transaction costs.

**Data needed:** H1 BOS events + subsequent price movement for 60+ bars.
**Currently available:** market_phase=IMPULSE is a proxy for BOS (n=273). EQ1 shows -0.656R.
**Key difference:** Current system enters pattern DURING impulse. Structure entry would enter on PULLBACK AFTER BOS.

### SQ2: Does structure-based SL geometry reduce cost impact to viable levels?

**Hypothesis:** SL at structure invalidation (H1 swing) produces risk distances of 15-30 pips where spread < 10% of risk.

**Data needed:** H1 swing high/low at entry time + entry price + spread.
**Currently available:** `h1_last_swing_high/low` computed in swing_context.py + horizon_trade_builder already uses this for EXTENDED horizon. INTRADAY uses m15_nearest_support/resistance (10-15 pips).
**Can test today:** Partially — the INTRADAY horizon shadows already use M15 structure SL (11.3 pips avg). Their raw EV is -0.038R (nearly zero) vs SCALP -0.074R.

### SQ3: Does pullback INTO structure create higher probability entries?

**Hypothesis:** Entering on a pullback to a broken structure level (support-becomes-resistance) produces better outcomes than entering at any point during impulse.

**Data needed:** Precise structure levels + entry timing relative to levels.
**Currently available:** `m15.at_key_level` exists (True/False). Can test whether at_key_level=True improves outcomes.
**Status:** Partially testable via strategy_observations (n=348).

### SQ4: Does distance from structure level predict trade outcome?

**Hypothesis:** Entries CLOSER to structure levels (better RR location) produce better outcomes than entries far from structure.

**Data needed:** m15_nearest_support, m15_nearest_resistance, entry_price.
**Currently available:** In MarketContext but NOT persisted in shadow trades.
**Status:** Requires join or new data capture.

### SQ5: Does structure-based risk geometry improve cost efficiency?

**Hypothesis:** Using H1 swing / M15 structure for SL (instead of M5 candle) produces risk distances where cost < 10%, enabling the signal to be evaluated fairly.

**Data needed:** Compare SL from M5 candle vs M15 structure vs H1 swing.
**Currently available:** Horizon shadows already compare this:
- SCALP (M5 candle): 2.7 pip SL, cost=37% 
- INTRADAY (M15 structure): 11.3 pip SL, cost=9%
**Already tested:** INTRADAY raw EV = -0.038R. Still negative but cost-viable.

### SQ6: Does BOS + H1 alignment + M15 level confluence produce positive EV?

**Hypothesis:** The combination of (BOS confirmed) + (H1 direction matches trade) + (M15 at_key_level) produces positive cost-adjusted EV even at current spreads.

**Data needed:** All three fields simultaneously. 
**Currently available:** Partially — via strategy_observations which capture all three.
**Status:** n=348 observations. Requires outcome linkage and specific combination filtering.

### SQ7: Does CHOCH (change of character) predict direction reversal?

**Hypothesis:** When market_phase transitions from IMPULSE to REVERSAL (CHOCH proxy), the new direction is profitable.

**Data needed:** Phase transitions + outcomes of trades taken in new direction.
**Currently available:** phase=REVERSAL has n=149, cost-adj EV=-0.993R. 
**ALREADY ANSWERED:** CHOCH/REVERSAL produces the WORST outcomes. ❌

### SQ8: Is the structure entry hypothesis fundamentally different from what's already tested?

**Key question:** Does the structure hypothesis offer genuinely NEW predictive information, or does it simply REFRAME the existing H1 bias + phase + regime data that has already been proven non-predictive?

---

## 5. Data Availability Matrix

| Experiment | Can Answer Today? | Why |
|---|---|---|
| SQ1 (BOS predicts direction) | 🟡 PARTIALLY | phase=IMPULSE is proxy. Already tested (-0.656R). BUT: entry timing is different (structure enters on pullback AFTER BOS, not during impulse) |
| SQ2 (Structure SL geometry) | 🟢 YES | INTRADAY horizon already uses M15 structure SL. Data exists (n=328) |
| SQ3 (Pullback into structure) | 🟡 PARTIALLY | m15.at_key_level in strategy_observations (n=348). Needs outcome linkage |
| SQ4 (Distance from level) | 🔴 NO | m15_nearest_support/resistance not in shadow trades. Requires new data capture |
| SQ5 (Structure risk geometry) | 🟢 YES | Horizon comparison already shows INTRADAY (M15 SL) vs SCALP (M5 SL) |
| SQ6 (BOS + alignment + level) | 🟡 PARTIALLY | Fields exist in strategy_observations but sample may be small |
| SQ7 (CHOCH predicts reversal) | 🟢 ALREADY ANSWERED | phase=REVERSAL = -0.993R. ❌ CHOCH does NOT predict reversal |
| SQ8 (Genuinely new info?) | Analysis question | See below |

---

## 6. The Honest Assessment: Is This Hypothesis Different?

### What the structure hypothesis ACTUALLY changes vs current system:

| Aspect | Current System | Structure Entry | Is It ACTUALLY Different? |
|--------|---------------|-----------------|--------------------------|
| Directional source | Pattern shape → direction | BOS/CHOCH → direction | **Different mechanism, but...** |
| ...already tested as | Pattern + h1_bias alignment | h1_bias = H1 structural direction | **Same underlying data** |
| Risk geometry | M5 candle (3.5 pip) | M15/H1 structure (10-30 pip) | ✅ **Genuinely different — WIDER** |
| Entry timing | Any pattern at any time | Only after structural event + pullback | ⚠️ Different but fewer trades |
| Trade frequency | ~867 / period | Estimated 50-100 / period | Lower frequency |
| Phase context | Pattern fires in any phase | Only in PULLBACK after IMPULSE | **Subset of existing trades** |

### The critical question:

**Is "enter on pullback after BOS with structure SL" genuinely different from what we already tested, or is it just selecting the PULLBACK phase subset with wider SL?**

Answer: **It's MOSTLY a combination of existing tested factors:**
- PULLBACK phase: already tested → adj EV = -0.411R
- H1 aligned: already tested → adj EV = -0.538R
- Wider SL (INTRADAY): already tested → raw EV = -0.038R

**The ONE thing not yet tested:** Does the COMBINATION of all three (PULLBACK + H1 aligned + structure-width SL) create viability? This is essentially EI10 from the Entry Intelligence design.

---

## 7. Priority Order

| Priority | Experiment | Reason |
|----------|-----------|--------|
| **P0** | **EI10 + SQ5 combined** | Test PULLBACK + H1 aligned + risk≥6 pips with cost adjustment. This IS the structure hypothesis expressed in available data. | 
| **P0** | **SQ2/SQ5** | Verify whether INTRADAY structure-SL geometry brings raw EV close enough to zero that other filters can push it positive |
| **P1** | **SQ3** | Test m15.at_key_level correlation with outcomes (requires strategy_obs→outcome join) |
| **P2** | **SQ4** | Add structure level distance to shadow trades (data capture change) |
| **P3** | **SQ1 (true BOS)** | Requires BOS field in shadow trades (not just phase proxy) |

---

## 8. Final Verdict: Is Structure More Promising Than M5 Pattern Optimisation?

### YES — but for a specific reason.

The structure hypothesis is NOT primarily about better directional prediction (the data suggests H1 structure direction already fails to predict after costs). It IS about **better risk geometry**.

The single most impactful difference:
- M5 candle SL = 3.5 pips → cost = 48% of risk → impossible
- M15/H1 structure SL = 10-30 pips → cost = 5-10% → cost-viable

**The structure entry hypothesis is actually a RISK GEOMETRY hypothesis in disguise.** The directional claim (BOS predicts direction) has weak existing support (IMPULSE trades still lose), but the geometry claim (structure SL reduces cost impact by 5-10×) is mechanically true and already partially validated via the INTRADAY horizon (raw EV = -0.038R vs -0.074R with SCALP SL).

### The viable research path:

1. Is raw EV near zero at structure-width risk? → INTRADAY shows -0.038R (nearly zero)
2. Can any filter push -0.038R to positive? → NOT YET TESTED for INTRADAY subset
3. If yes → walk-forward validation → shadow deployment

**This is more promising than continuing M5 pattern optimisation** because it addresses the PROVEN root cause (cost ratio) rather than the unprovable assumption (pattern directional accuracy).
