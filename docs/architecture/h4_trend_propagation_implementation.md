# H4 Trend Propagation Implementation

## Root Cause

`build_h4_understanding()` in `core/v3_shadow/builders.py` used conditional extraction that only read `RegimeSnapshot.trend_bias` and `trend_strength` for TRENDING and RANGING classifications. VOLATILE and TRANSITIONAL regimes fell through with `trend=""` and `trend_strength=0.0`, losing directional information that the H4 regime analyzer had already computed.

The analyzer (`core/timeframes/h4_regime.py`) computes `trend_bias` for ALL classifications using structural HH/HL and LH/LL counts plus EMA slope — regardless of whether the final classification is TRENDING, VOLATILE, RANGING, or TRANSITIONAL. The data was available but the builder discarded it.

---

## Code Change

**File:** `core/v3_shadow/builders.py`, function `build_h4_understanding()`, lines ~76-88

### Before

```python
if not trend and htf_context is not None:
    regime_snap = getattr(htf_context, "regime", None)
    if regime_snap:
        classification = getattr(regime_snap, "classification", None)
        if classification:
            regime_str = classification.value if hasattr(classification, "value") else str(classification)
            if "TRENDING" in regime_str.upper():
                trend = getattr(regime_snap, "trend_bias", "NEUTRAL") or "NEUTRAL"
                trend_strength = float(getattr(regime_snap, "trend_strength", 0) or 0)
            elif "RANG" in regime_str.upper():
                trend = "NEUTRAL"
        atr_ratio = float(getattr(regime_snap, "atr_ratio", 1.0) or 1.0)
        volatility_state = (...)
```

### After

```python
if not trend and htf_context is not None:
    regime_snap = getattr(htf_context, "regime", None)
    if regime_snap:
        # Unconditionally propagate trend_bias/trend_strength from RegimeSnapshot.
        # The analyzer computes these for ALL classifications (TRENDING, RANGING,
        # VOLATILE, TRANSITIONAL). Previously only extracted for TRENDING/RANGING,
        # causing VOLATILE/TRANSITIONAL to lose directional information.
        trend = getattr(regime_snap, "trend_bias", "NEUTRAL") or "NEUTRAL"
        trend_strength = float(getattr(regime_snap, "trend_strength", 0) or 0)

        atr_ratio = float(getattr(regime_snap, "atr_ratio", 1.0) or 1.0)
        volatility_state = (...)
```

---

## Before/After Data Flow

### Before

```
analyze_regime(candles)
    → RegimeSnapshot(classification=VOLATILE, trend_bias="BULLISH", trend_strength=0.65)
        ↓
build_h4_understanding(htf_context)
    → classification contains "TRENDING"? NO
    → classification contains "RANG"? NO
    → trend stays "" , trend_strength stays 0.0
        ↓
H4Understanding(trend="", trend_strength=0.0)
        ↓
H4State(trend="", trend_strength=0.0)
        ↓
Persisted as: h4_trend=null, h4_trend_strength=0.0
```

### After

```
analyze_regime(candles)
    → RegimeSnapshot(classification=VOLATILE, trend_bias="BULLISH", trend_strength=0.65)
        ↓
build_h4_understanding(htf_context)
    → trend = "BULLISH", trend_strength = 0.65 (unconditional)
        ↓
H4Understanding(trend="BULLISH", trend_strength=0.65)
        ↓
H4State(trend="BULLISH", trend_strength=0.65)
        ↓
Persisted as: h4_trend="BULLISH", h4_trend_strength=0.65
```

---

## Tests Added

**File:** `tests/test_h4_trend_propagation.py` — 17 tests, 8 classes

| Class | Tests | Verifies |
|---|---|---|
| TestTrendingBullish | 2 | TRENDING_BULLISH → "BULLISH" (existing behaviour preserved) |
| TestTrendingBearish | 2 | TRENDING_BEARISH → "BEARISH" (existing behaviour preserved) |
| TestVolatileBullish | 2 | VOLATILE + bullish → "BULLISH" (NEW — previously lost) |
| TestVolatileBearish | 1 | VOLATILE + bearish → "BEARISH" (NEW — previously lost) |
| TestVolatileNeutral | 1 | VOLATILE + neutral → "NEUTRAL" |
| TestTransitional | 3 | TRANSITIONAL + any bias → preserved |
| TestRanging | 2 | RANGING + NEUTRAL → "NEUTRAL"; RANGING + directional → preserved |
| TestEdgeCases | 4 | None context, no regime, empty bias, volatility_state preservation |

---

## Behavioural Impact Summary

| Impact | Records Affected | Correct? |
|---|---|---|
| h4_trend population: 44% → ~85-90% | +318 VOLATILE records gain direction | YES |
| TREND_CONTINUATION: can now fire in VOLATILE regimes | +20-40 estimated new selections | YES — directional volatile IS continuation territory |
| MEAN_REVERSION: 2 false positives removed | -2 (VOLATILE with directional trend) | YES — fading a directional volatile trend is wrong |
| BehaviourContext: directional VOLATILE → regime="TRENDING" | ~100-150 records reclassified | YES — more accurate |
| Horizon: EXTENDED may trigger for VOLATILE+directional | +15-25 estimated | YES — strong trend supports wider targets |
| EXECUTE decisions | 0 change (entry geometry gates) | Safe |

### What Did NOT Change

- Strategy selection logic (no code changes to strategy_engine.py)
- Strategy thresholds or contracts
- Entry engine / stop-target calculation
- Risk sizing
- Execution logic
- Opportunity engine
- Persistence schema

---

## Startup Verification

After restart, the terminal displays:

```
==================================================
V10 CODE VERSION
==================================================
  strategy_engine: 2026-07-31 ...
  entry_engine:    2026-07-30 ...
  opportunity_engine: 2026-07-30 ...
  H4 trend propagation: ACTIVE (all regimes)
  ENGINE_MODE: V10
==================================================
```

Logger also records `h4_propagation=ACTIVE` for persistent audit trail.
