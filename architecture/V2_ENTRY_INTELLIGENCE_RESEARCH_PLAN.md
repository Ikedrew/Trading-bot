# V2 Entry Intelligence Research Plan

---

## 1. Version 2 Hypothesis

### Core Claim

> "Higher timeframe market context provides directional predictive value, with lower timeframe patterns used only as execution timing."

### Preliminary Evidence Check (computed during plan creation)

| Test | Result | Implication |
|------|--------|-------------|
| H1 aligned vs H1 counter (raw EV) | Aligned: -0.053R, Counter: **+0.027R** | ⚠️ H1 bias as currently measured does NOT predict direction |
| H1 aligned vs neutral (adj EV) | Aligned: -0.304R, Neutral: -0.410R | Slight improvement but ALL negative |
| H1 direction differential | **-0.080R** (aligned WORSE than counter) | ⚠️ Trading WITH H1 bias is WORSE than against |

### Honest Assessment Before Starting

**The preliminary data suggests the V2 hypothesis may ALSO fail.** H1 directional bias (the primary "context" signal) does not predict direction in the current data. This does not invalidate the research plan — it means Phase 1 experiments are more likely to conclude quickly with a clear answer.

**The plan below is designed to either:**
- Discover genuine predictive context (if it exists) within 4-6 experiments
- Definitively prove it doesn't exist (if true) with the same experiments

---

## 2. Research Hierarchy

```
V2 LEVEL 0: DOES ANY CONTEXT PREDICT DIRECTION?
    ↓ Required experiments: CQ1-CQ4
    ↓ Decision: YES (unlock Level 1) or NO (project conclusion)
    
V2 LEVEL 1: WHICH CONTEXT COMBINATIONS HAVE EDGE?
    ↓ Only if Level 0 passes
    ↓ Test: context combinations after costs
    
V2 LEVEL 2: DOES CONTEXT + TIMING CREATE VIABLE ENTRIES?
    ↓ Only if Level 1 finds positive subset
    ↓ Test: context direction + M5 timing trigger
    
V2 LEVEL 3: DOES IT SURVIVE VALIDATION?
    ↓ Walk-forward, OOS, live shadow
```

---

## 3. Phase 1 Questions (Context Prediction)

### CQ1: Does H1 directional bias predict future movement?

**Hypothesis:** After H1 classifies direction as BULLISH/BEARISH, price moves in that direction more than against it (measured at 1R from a neutral entry point).

**Measurement:**
- Given H1=BULLISH: probability of +1R before -1R
- Given H1=BEARISH: probability of -1R before +1R
- Compare to random (50%)

**Data source:** CURRENT-epoch shadow trades with h1_bias field (89% coverage)

**Success criterion:** Directional accuracy > 53% (to overcome typical 6-10% cost at M15 geometry)

**Preliminary result:** H1 aligned raw EV = -0.053R, H1 counter raw EV = +0.027R. H1 prediction appears **INVERTED** (counter-trend performs better). This needs deeper investigation — it may indicate H1 bias is lagging.

---

### CQ2: Does H1 BOS direction predict continuation?

**Hypothesis:** After confirmed BOS (Break of Structure) on H1, price continues in the BOS direction for at least +1R.

**Measurement:**
- Trades in IMPULSE phase (BOS confirmed) vs PULLBACK (no BOS)
- Raw directional accuracy (did trade move in predicted direction?)
- MFE distribution by phase

**Data source:** market_phase field in CURRENT shadow trades. IMPULSE = BOS confirmed.

**Success criterion:** IMPULSE trades raw EV > 0 (not just > PULLBACK)

**Known data:** IMPULSE raw EV = -0.023R (from M9). Not positive. BOS does NOT currently predict continuation.

---

### CQ3: Does H4 regime improve directional accuracy?

**Hypothesis:** H4 TRENDING regime + trade in trend direction produces better outcomes than H4 RANGING regime or counter-trend.

**Measurement:**
- TRENDING + aligned direction vs TRENDING + counter
- RANGING + any direction vs TRENDING + any direction

**Data source:** h4_regime field (100% coverage in CURRENT)

**Known data:** TRENDING regime EV = -1.11R (all trades hit SL immediately). This appears to be a data quality issue — 92 TRENDING trades all produce -1R, suggesting these are trades taken counter to a strong trend.

---

### CQ4: Does M15 structure location improve outcomes?

**Hypothesis:** Trades taken at M15 key levels (support/resistance, order blocks) produce better outcomes than trades in "open space."

**Measurement:**
- m15_at_key_level=True vs m15_at_key_level=False
- m15_order_block_present=True vs False

**Data source:** Strategy observations (n=348) contain these fields. Shadow trades do NOT contain them directly — requires join via entity_id.

**Status:** Requires data linkage or new data capture.

---

## 4. Required Experiments

| ID | Question | Data Available? | Can Run Now? | Est. Result |
|---|---|---|---|---|
| **CQ1** | H1 bias predicts direction | ✅ (89% coverage) | ✅ YES | Preliminary: FAIL (aligned ≤ counter) |
| **CQ2** | H1 BOS predicts continuation | ✅ (via phase=IMPULSE) | ✅ YES | Known: IMPULSE EV = -0.023R (flat) |
| **CQ3** | H4 regime improves accuracy | ✅ (100% coverage) | ✅ YES | Known: TRENDING = -1.11R (worse) |
| **CQ4** | M15 structure location helps | ⚠️ (strategy_obs only) | 🟡 Partial | Unknown |
| **CQ5** | Bar-1 velocity (EI1 from V1) | ✅ (state_progression) | ✅ YES | Unknown — not yet computed |
| **CQ6** | Volatility expansion predicts movement | ⚠️ (ATR not in shadow) | 🔴 Requires join | Unknown |

### Execution Order

1. **CQ5 (bar-1 velocity)** — fresh hypothesis, testable now, no V1 data on this
2. **CQ1 (full analysis)** — deeper investigation of the inverted H1 finding
3. **CQ2 (BOS continuation)** — confirm BOS has no predictive value
4. **CQ3 (regime)** — investigate the TRENDING anomaly (all -1R)
5. **CQ4 (M15 location)** — if data linkage possible

---

## 5. Success Criteria

### Phase 1 passes if ANY of the following are demonstrated:

| Criterion | Threshold | What It Means |
|-----------|-----------|---------------|
| Context variable predicts direction | Raw EV > 0 in aligned group | Context has genuine predictive value |
| Directional accuracy > 53% | At M15 geometry costs (~6-10%) | Enough accuracy to overcome spread |
| At least ONE context produces positive adj EV | Adj EV > 0, CI above zero, n≥100 | Viable trading direction source |
| Walk-forward confirms | Train + test both positive | Not overfit |

### Phase 1 FAILS if ALL of the following are true:

| Criterion | Evidence |
|-----------|----------|
| H1 direction has no predictive value | CQ1 aligned ≤ counter in raw EV |
| BOS has no continuation value | CQ2 IMPULSE EV ≤ 0 |
| H4 regime has no predictive value | CQ3 all regimes negative |
| M15 location has no predictive value | CQ4 at_key_level does not improve |
| Bar-1 velocity has no predictive value | CQ5 first-bar direction ≠ final outcome |

---

## 6. Promotion Criteria (if Phase 1 passes)

Before ANY context-based entry can be promoted:

| Gate | Requirement |
|------|-------------|
| Epoch | CURRENT only |
| Sample size | n ≥ 100 per context group |
| Cost-adjusted EV | > 0 |
| CI lower bound | > 0 |
| Walk-forward | Train AND test positive |
| Not data-mined | Pre-registered hypothesis |
| Survives costs | At M15+ geometry (spread < 10% of risk) |
| Single variable | Each context tested independently first |

---

## 7. Failure Criteria (if Phase 1 fails)

If ALL Phase 1 experiments produce negative or neutral results:

### Conclusion:

> "The information currently computed by this bot (H1 bias, H4 regime, M15 structure, market phase, BOS/CHOCH) does not contain measurable directional predictive value on FX at intraday timeframes."

### Implications:

| What Dies | Why |
|-----------|-----|
| Context-driven V2 hypothesis | Empirically falsified |
| All strategy family research | Families are meaningless without predictive direction |
| Phase-matching research | Phase doesn't predict outcome |
| The entire H1/H4 architecture | These timeframes don't predict direction either |

### What Opens:

| Path | Rationale |
|------|-----------|
| External data (order flow, sentiment, news) | If candlestick + structure doesn't predict, need different information |
| Machine learning on raw features | Maybe non-obvious combinations have value |
| Different market (crypto, equities where patterns work) | FX M5 may simply be unpredictable at this scale |
| Accept that short-term FX trading is not viable | Honest conclusion if evidence supports it |

---

## 8. Migration Plan from Version 1

### What to KEEP from V1:

| Component | Why Keep |
|-----------|---------|
| Shadow trade infrastructure | Validated, producing data correctly |
| MarketContext builder | Produces the context data V2 needs |
| Epoch safety (load_shadow_trades) | Prevents contamination |
| Validity gates | Ensures research quality |
| Observation persistence (S3 + local) | Evidence accumulation |
| Statistical testing utilities | Reusable for V2 experiments |
| Strategy observer | Records context per cycle |
| Research registry | Extendable for CQ questions |

### What to IGNORE from V1:

| Component | Why Ignore |
|-----------|-----------|
| Pattern → direction assumption | Disproven |
| 10-factor scoring weights | Optimise a non-signal |
| Strategy activation (REVERSAL/CONTINUATION/FALSE_BREAK) | Classifies non-predictive signal |
| EV gate at current calibration | Gate on zero-EV system |
| Position sizing recommendations (R3-R5) | All invalidated |
| Exit optimisation findings (EX1-EX10) | Cannot fix zero signal |

### What to CHANGE conceptually:

| V1 | V2 |
|----|----|
| Pattern predicts direction | Context predicts direction, pattern times entry |
| Score determines confidence | Prediction probability determines confidence |
| All patterns treated as trade signals | Only trades WITH context alignment |
| Optimise everything simultaneously | Prove direction FIRST, optimise LATER |

---

## Timeline

| Phase | Duration | Outcome |
|-------|----------|---------|
| Phase 1: CQ1-CQ5 | 1-2 sessions | PASS or FAIL on context prediction |
| Phase 2: Combinations | 1 session (only if Phase 1 passes) | Identify best context recipe |
| Phase 3: Entry construction | 1-2 sessions (only if Phase 2 succeeds) | Build new entry model |
| Phase 4: Validation | Ongoing | Walk-forward + shadow testing |

**Expected duration to decision: 1-2 sessions (CQ experiments run on existing data).**

If Phase 1 fails, the project reaches its final architectural decision within one more session.
