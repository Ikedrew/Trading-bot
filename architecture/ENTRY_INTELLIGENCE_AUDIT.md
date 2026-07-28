# Entry Intelligence Layer Audit + EI Research Questions

---

## PART 1: What Information Exists at Entry Time?

### Available Context Fields (in shadow_trade decision_snapshot)

| Field | Coverage | Purpose | Potentially Predictive? |
|-------|----------|---------|------------------------|
| pattern | 100% | Candlestick shape detected | ❌ No (proven by EQ1) |
| score | 100% | 10-factor composite (0-1) | ⚠️ Weak monotonic (see below) |
| regime | 100% | RANGE / TRENDING / TRANSITIONAL | ❌ No (EQ1: all negative) |
| h4_regime | 89% | H4 classification | Same as regime |
| market_phase | 89% | IMPULSE / PULLBACK / etc. | ⚠️ Slight variation (PULLBACK best) |
| h1_bias | 89% | BULLISH / BEARISH / NEUTRAL | ⚠️ Alignment helps marginally |
| direction | 100% | BUY / SELL | N/A (this IS the prediction) |
| risk_pips | 100% | SL distance in pips | ✅ Strong relationship with cost ratio |
| reward_risk_ratio | 100% | RR target | Present but fixed per pattern |
| trade_horizon | 84% | SCALP / INTRADAY / EXTENDED | Untested after costs |

### NOT Available in Shadow Trades (requires join)

| Field | Source | Join Key |
|-------|--------|----------|
| 10 scoring components | decision_trace | entity_id |
| spread at entry | execution_context | correlation_id |
| session (London/NY/Asia) | execution_context | correlation_id |
| ATR at entry | feature_bundle | not directly joinable |
| M5 bias FSM state | engine_state | not persisted per trade |

---

## PART 2: Does Any Context Field Predict Success After Costs?

### Score (10-factor composite)

| Score Range | n | Cost-Adj EV | Observation |
|-------------|---|-------------|-------------|
| 0.35-0.45 | 26 | -0.745R | Worst |
| 0.45-0.55 | 180 | -0.766R | Bad |
| 0.55-0.65 | 344 | -0.703R | Slightly better |
| **0.65+ (high)** | 317 | **-0.648R** | Best (still negative) |

**Verdict:** Score has a weak monotonic relationship — higher scores lose slightly less. But the difference between worst and best is only 0.12R. No score level achieves positive EV. Score cannot be used as a viable entry gate.

### H1 Bias Alignment

| Alignment | n | Cost-Adj EV |
|-----------|---|-------------|
| **Aligned** (H1 supports direction) | 420 | **-0.538R** |
| Opposing (H1 against) | 140 | -0.619R |
| Neutral | 215 | -0.888R |

**Verdict:** H1 alignment produces the least-negative result (-0.54R vs -0.89R for neutral). The difference (0.35R) is meaningful but still deeply negative. The signal quality improves when H1 agrees, but not enough to overcome costs.

### Risk Distance

| Risk Bucket | n | Raw EV | Cost-Adj EV |
|-------------|---|--------|-------------|
| <2 pips | 36 | -0.31R | -3.91R |
| 2-4 pips | 441 | -0.07R | -0.57R |
| 4-6 pips | 99 | -0.06R | -0.34R |
| **6-10 pips** | 86 | **-0.02R** | **-0.19R** |
| 10+ pips | 205 | -0.68R | -0.78R |

**Verdict:** 6-10 pip risk trades have the best raw EV (-0.02R, nearly zero) and manageable cost ratio. This is the only bucket where the signal approaches break-even before costs. But the cost (-0.17R) still dominates.

The 10+ pip bucket has terrible raw EV (-0.68R) — these are trades where the pattern fires during high volatility but the move is AGAINST the predicted direction.

### Phase × Alignment Combinations

| Combination | n | Cost-Adj EV |
|-------------|---|-------------|
| PULLBACK + H1 aligned | 150 | -0.411R |
| REVERSAL + H1 counter | 36 | -0.337R |

**Verdict:** The best multi-factor combination (REVERSAL phase with H1 counter-direction, n=36) produces -0.34R. Better than average but still negative and n too small.

---

## PART 3: Summary of Entry Intelligence Value

| Context Layer | Predictive Value | Size of Effect | Enough to Overcome Costs? |
|---------------|-----------------|----------------|--------------------------|
| Pattern type | None | 0R differential between patterns | ❌ |
| Score | Weak monotonic | ~0.12R best-to-worst | ❌ |
| H1 alignment | Moderate | ~0.35R aligned vs neutral | ❌ |
| Market phase | Weak | ~0.25R PULLBACK vs IMPULSE | ❌ |
| Risk geometry | Strong (for cost ratio) | ~0.38R (6-10 pip vs 2-4 pip adjusted) | ❌ Still negative |
| Phase+alignment | Moderate | Best combo: -0.34R | ❌ |

**No single context field or combination produces positive cost-adjusted EV.**

---

## PART 4: EI Research Questions

The following questions investigate whether ADDITIONAL information — not currently used for the trade decision — could convert pattern observations into predictive signals.

### EI1: Does bar-1 velocity predict continuation?

**Hypothesis:** If the first bar after entry moves strongly in the trade direction, the pattern signal was correct. Early velocity may be identifiable before full cost realisation.

**Data:** trade_state_progression (bar-by-bar R). Compare bar-1 R to final outcome.

**Metric:** Correlation between first-bar movement and ultimate cost-adjusted R.

### EI2: Does pre-pattern momentum predict pattern reliability?

**Hypothesis:** Patterns that form AFTER momentum in the predicted direction (continuation setup) may be more reliable than patterns forming against momentum (reversal setup).

**Data:** Candle data for 5-10 bars before pattern. Compute: did price move in trade direction in the preceding bars?

**Metric:** Cost-adjusted EV grouped by pre-pattern momentum direction.

### EI3: Does structural confluence improve directional accuracy?

**Hypothesis:** Patterns at key structure levels (support/resistance, order blocks) are more reliable than patterns in "open space."

**Data:** m15_at_key_level, m15_order_block_present, m15_quality_score in MarketContext.

**Metric:** Cost-adjusted EV when structural confluence exists vs when it doesn't.

### EI4: Does multi-timeframe alignment predict success?

**Hypothesis:** When H4 regime + H1 direction + M5 pattern ALL agree, the signal is stronger than any single timeframe.

**Data:** h4_regime + h1_bias + pattern direction from decision_snapshot.

**Metric:** Cost-adjusted EV when all three align vs when they conflict.

### EI5: Does volatility expansion predict tradeable movement?

**Hypothesis:** Patterns that form during volatility expansion (ATR increasing) produce larger moves that can overcome costs, while patterns in low-volatility produce insufficient movement.

**Data:** ATR at entry time, volatility regime from MarketContext.

**Metric:** Cost-adjusted EV grouped by ATR percentile at entry.

### EI6: Does risk distance pre-filtering create viable entries?

**Hypothesis:** If only trades with risk_pips >= 6 are taken (where cost < 20% of risk), the subset may approach viability — especially when combined with other filters.

**Data:** risk_pips from decision_snapshot (already 100% available).

**Metric:** Cost-adjusted EV for risk_pips >= 6 COMBINED with H1 alignment + phase filter.

### EI7: Does session timing affect directional accuracy?

**Hypothesis:** Patterns during London/NY overlap (highest liquidity) are more reliable than off-session patterns, because institutional flow provides genuine directional commitment.

**Data:** Execution_context.session (requires correlation_id join).

**Metric:** Cost-adjusted EV by session.

### EI8: Does the M5 bias FSM state predict pattern follow-through?

**Hypothesis:** If the M5 bias FSM is in CONFIRMED state (directionally committed) when a pattern fires, the pattern is more likely to follow through.

**Data:** bias_phase from engine_state (available in some traces).

**Metric:** Cost-adjusted EV by bias FSM state at entry.

### EI9: Does immediate rejection (wick dominance) predict direction better than body patterns?

**Hypothesis:** Single-bar rejection patterns (HAMMER, SHOOTING_STAR — strong wick) may have better directional accuracy than multi-bar patterns (THREE_WHITE_SOLDIERS — body progression), because wick rejection represents a real supply/demand event.

**Data:** Pattern type + MFE in first 3 bars (from state_progression).

**Metric:** First-3-bar directional accuracy (did price move in trade direction within 3 bars?) grouped by single-bar vs multi-bar patterns.

### EI10: Does combining risk filter + context filter + timing create a viable subset?

**Hypothesis:** The COMBINATION of (risk_pips ≥ 6) + (H1 aligned) + (score ≥ 0.60) + (PULLBACK or REVERSAL phase) may produce enough filtering to create positive cost-adjusted EV, even though no single filter is sufficient alone.

**Data:** All fields from decision_snapshot (100% coverage for score + risk; 89% for phase/h1_bias).

**Metric:** Cost-adjusted EV of the multi-filtered subset. Sample size check (n≥50 minimum for indication).

---

## PART 5: Priority Ordering

| Priority | Question | Why | Testable Today? |
|----------|----------|-----|----------------|
| **P0** | EI6 (risk filter) | Uses 100% available data, proven cost-ratio benefit | ✅ YES |
| **P0** | EI10 (combined filter) | Tests the most promising hypothesis with available data | ✅ YES |
| **P0** | EI1 (bar-1 velocity) | 100% have trade_state_progression | ✅ YES |
| **P1** | EI4 (multi-TF alignment) | 89% have all three fields | ✅ YES |
| **P1** | EI9 (rejection vs body) | 100% have pattern + progression | ✅ YES |
| **P2** | EI3 (structural confluence) | Requires MarketContext (via observer join) | ⚠️ Partial |
| **P2** | EI5 (volatility expansion) | Requires ATR data (not in shadow_trade) | ⚠️ Requires join |
| **P2** | EI7 (session timing) | Requires execution_context join | ⚠️ Requires join |
| **P3** | EI2 (pre-pattern momentum) | Requires raw candle data before entry | ❌ Not in shadow trade |
| **P3** | EI8 (bias FSM state) | Not reliably persisted | ❌ Limited data |

---

## PART 6: What Would Constitute Evidence of a Predictive Signal?

For any EI question to indicate a viable signal, it must show:

1. **Cost-adjusted EV > 0** in the filtered subset
2. **95% CI entirely above zero** (not merely including zero)
3. **n ≥ 100** for the subset (minimum for any conclusion)
4. **Walk-forward validation** (first 60% for discovery, last 40% confirms)
5. **Effect not explainable by look-ahead** (all filter variables known at entry time)

If no EI question achieves this, the conclusion is: **the information available at entry time in the current system cannot distinguish profitable from unprofitable entries at a level that overcomes transaction costs.**

That would be a definitive architectural conclusion requiring either:
- New information sources (order flow, depth, sentiment)
- New timeframe (H1/H4 where movement >> spread)
- New entry mechanism (not candlestick patterns)
