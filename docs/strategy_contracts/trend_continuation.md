# TREND_CONTINUATION

## 1. Market Hypothesis

In a strong, established multi-timeframe directional trend, measured pullbacks into structure offer asymmetric continuation entries. The trade aligns with the dominant institutional force during a temporary retracement — buying value in a markup, selling value in a markdown.

The edge: price retraces to a level where the trend's participants are incentivised to reload, and the pullback's participants are trapped when the trend resumes.

## 2. Required Evidence Contract

| Requirement | Market Reason | V10 Field | Producer | Status |
|---|---|---|---|---|
| Strong H4 directional trend (strength >= 0.5) | Without macro trend persistence, "continuation" is speculative. Weak/dying trends produce false continuation signals | `state.h4.trend in ("BULLISH","BEARISH") AND state.h4.trend_strength >= 0.5` | H4 regime analyzer → RegimeSnapshot.trend_bias + trend_strength → MarketContextBuilder → H4Summary → V10MarketState.h4 | GREEN — reliably populated every H4 bar. Represents genuine H4 candle structure analysis. |
| H1 structure aligned with H4 (BOS in trend direction) | Execution-timeframe must confirm macro. BOS = institutional footprint proving directional commitment at the intraday level | `state.h1.dominant_trend == state.h4.trend AND state.h1.bos_confirmed AND state.h1.bos_direction == state.h4.trend` | H1 bias analyzer → BiasSnapshot (direction, bos_confirmed, bos_direction) → build_h1_understanding → V10MarketState.h1 | GREEN — BOS detection from H1 candle pivot analysis. Reliable swing-break detection. |
| M15 pullback active | Price must have retraced into structure. Without pullback, entry is at extension (chasing). Pullback provides value + structural stop reference | `state.m15.pullback_active` | build_m15_understanding(): compares last 5 candles vs prior 5, depth > 0.5 ATR triggers True | GREEN — computed from live M5 candles every cycle. Reliable candle-based detection. |

## 3. Supporting Evidence

| Evidence | Purpose | V10 Field | Status |
|---|---|---|---|
| M15 internal BOS resumes trend | Earliest structural confirmation pullback is complete — micro-structure re-breaking in trend direction | `state.m15.internal_bos AND direction == h4.trend` | RED — field exists but no M15 BOS detector is wired. Always False. |
| TRENDING regime classification | Independent regime confirmation from behaviour analysis | `state.regime.regime == "TRENDING"` | GREEN — populated by BehaviourContext. |
| M5 rejection in trend direction | Candle-level confirmation of pullback exhaustion | `state.m5.rejection_present AND state.m5.rejection_direction == h4.trend` | GREEN — available but NOT currently checked by strategy engine. |

## 4. Invalidations

None explicitly coded. Implicit: if required conditions unmet, confidence = 0.

Conceptual invalidations that SHOULD exist:
- H4 trend weakening below 0.3 (trend dying)
- H1 CHoCH detected (structure reversing)
- Pullback exceeds 100% retracement (no longer a pullback — trend broken)

## 5. Current Implementation Assessment

The implementation correctly captures the core hypothesis. All three required conditions use reliable, production-populated fields. The strategy IS firing in live (24 selections on Jul 30). The only weakness is the supporting condition (`m15.internal_bos`) which is dead — reducing confidence scoring but not blocking selection.

## 6. Evidence Gaps

| Gap | Severity | Impact |
|---|---|---|
| `m15.internal_bos` never populated | Low | Supporting only — doesn't block |
| No M5 rejection direction check | Low | Available signal not used for confirmation |
| No explicit invalidation conditions | Medium | Strategy has no "abort" — relies on downstream entry/risk gates |

## 7. Recommended V10-Compatible Contract

The current implementation is nearly complete. Minimal improvements:
- Replace dead `m15.internal_bos` supporting check with available `m5.rejection_direction == h4.trend OR m5.local_bos_direction == h4.trend`
- Add invalidation: `h1.bos_direction OPPOSING h4.trend` → abort (structure reversing)
- Add invalidation: `m15.pullback_depth_atr > 3.0` → abort (pullback too deep, likely breakdown)
