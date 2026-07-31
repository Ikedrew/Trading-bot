# BREAKOUT_EXPANSION

## 1. Market Hypothesis

After a period of volatility compression (consolidation / tight range / coiling), a sudden volatility expansion with institutional displacement signals the start of a new directional move. The compression represents potential energy accumulation; the displacement is the release.

The trade: enter with the expansion direction after displacement confirms institutional commitment. The stop is on the other side of the compression zone. The target is projected from the compression width.

## 2. Required Evidence Contract

| Requirement | Market Reason | V10 Field | Producer | Status |
|---|---|---|---|---|
| Prior compression / consolidation period | The "coiled spring" prerequisite. Without compression, an expansion candle is just noise or continuation — not the start of something new. Compression = energy built up waiting for release | `state.regime.expansion_state == "COMPRESSING" OR state.regime.volatility_state == "CONTRACTION" OR state.regime.compression_bars > 5` | RegimeState.expansion_state (dead — never set), RegimeState.volatility_state (populated by BehaviourContext), RegimeState.compression_bars (dead — never computed) | YELLOW — 2/3 paths are dead fields. Surviving path: `volatility_state == "CONTRACTION"` IS populated by the regime/behaviour classifier. This represents genuine low-volatility detection. The concept IS represented but through only one of three intended paths. |
| Volatility expanding NOW | The spring is releasing. Proves compression led somewhere — not just ongoing quietness | `state.m15.displacement_present OR state.regime.expansion_state == "EXPANDING" OR state.regime.volatility_state == "EXPANSION"` | M15 displacement (populated — candle > 1.5 ATR), expansion_state (dead), volatility_state (populated) | YELLOW — functional via `m15.displacement_present` (reliable candle analysis) and `volatility_state` (if transition detected). Two of three paths work. |
| Displacement with institutional magnitude | Not just noise expansion but committed directional force. Large candle proves institutions are participating in the expansion | `state.m15.displacement_present` | build_m15_understanding(): last M15 candle range > 1.5 ATR triggers True. Direction from candle directionality. | GREEN — reliable candle-based detection. 1.5 ATR threshold ensures meaningful moves only. Represents genuine institutional-grade candle activity. |

## 3. Supporting Evidence

| Evidence | Purpose | V10 Field | Status |
|---|---|---|---|
| Displacement magnitude >= 1.5 ATR | Very strong expansion = high institutional commitment (distinguishes large move from just "above average") | `state.m15.displacement_magnitude_atr >= 1.5` | GREEN — computed from candle range / ATR. |

## 4. Invalidations

None explicitly coded.

Conceptual invalidations:
- Displacement immediately reverses (false expansion / wick)
- Multiple displacements in opposite directions (chop, not directional expansion)
- H4 trend strongly opposes displacement direction (fighting macro)
- Displacement into a major resistance/support level (expansion may be absorbed)

## 5. Current Implementation Assessment

The strategy has a **structural timing problem**: it requires observing a TRANSITION (compression → expansion) within a single evaluation snapshot. In reality:
- Compression happens over many bars (low vol persisting)
- One bar then displaces (the expansion event)
- The strategy must see BOTH conditions on the SAME evaluation

This IS possible when: `volatility_state == "CONTRACTION"` persists (regime classifier hasn't updated yet because regime transitions are smoothed), AND `m15.displacement_present` fires on the current candle. The regime classifier may still report CONTRACTION on the very bar that displacement occurs — before it updates to EXPANSION on the next cycle.

The strategy can fire but requires this specific timing window. This is architecturally fragile but not broken.

## 6. Evidence Gaps

| Gap | Severity | Impact |
|---|---|---|
| `expansion_state` never populated | Medium | One R1 path dead. Surviving path (volatility_state) works. |
| `compression_bars` never computed | Medium | Would provide "how long was compression" — useful for confidence but not blocking |
| Temporal transition is fragile | Architectural | Single-snapshot may miss the compression→expansion transition if regime updates too fast |

## 7. Recommended V10-Compatible Contract

The current implementation is CONDITIONALLY functional using surviving paths. Improvements:

| Concept | Available V10 Proxy | Trade-off |
|---|---|---|
| "Compression period existed" | `volatility_state == "CONTRACTION"` | Works — the regime classifier detects low-vol environments. May not persist on the bar displacement fires (race condition). |
| "Compression period existed" (stronger) | `h1.structural_clarity < 0.4 AND regime == RANGING` | Low clarity + ranging = price going nowhere (compression). Available but untested as proxy. |
| "Expansion is genuine, not noise" | `m15.displacement_present AND m15.displacement_magnitude_atr >= 1.5` | Already implemented. Strong signal. |
| "Direction of expansion" | `m15.displacement_direction` | GREEN — available for entry direction. |

The main architectural recommendation: if `compression_bars` were tracked (count of consecutive CONTRACTION cycles), the strategy could fire on `compression_bars >= 5 AND displacement_present` without the timing-race issue. This requires a simple counter in the regime classifier — not a new system.

Alternative: accept that this strategy fires rarely (when timing aligns) and validate its performance empirically when it does.
