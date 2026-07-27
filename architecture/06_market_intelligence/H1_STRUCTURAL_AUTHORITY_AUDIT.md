# Architecture Audit — H1 Structural Authority

**Generated:** 2026-07-21
**Status:** Audit only — no code modified
**Context:** Post-Migration 1 (Regime) and Migration 2 (Trend Alignment)

---

## 1. Production of H1 Structural Fields

### Where H1 fields originate

| Field | Originating Module | Function | Update Frequency | Cached? | Authoritative? |
|-------|-------------------|----------|-----------------|---------|---------------|
| `h1.direction` | `core/timeframes/h1_bias.py` | `analyze_bias()` | Every H1 bar close (~1 hour) | ✅ in `TimeframeCache` | ✅ Yes — used by Migration 2 |
| `h1.confidence` | `core/timeframes/h1_bias.py` | `analyze_bias()` | Every H1 bar close | ✅ in `TimeframeCache` | ✅ Yes — used by Migration 2 |
| `h1.swing_structure` | `core/timeframes/h1_bias.py` | `_swing_structure()` | Every H1 bar close | ✅ in `TimeframeCache` | ⚠️ Partially — computed but only consumed by MarketContext builder for phase classification |
| `h1.bos_confirmed` | **DOES NOT EXIST** | — | — | — | ❌ Not produced by any H1 analyzer |

### Key Finding

`bos_confirmed` does NOT exist as an H1-level output. It was proposed in the
`MARKET_CONTEXT_LAYER_DESIGN.md` but never implemented. The only BOS computation
currently exists in `core/pipeline/swing_context.py` which operates on **M5 candles**.

### Persistence Locations

| Field | Persisted In | Format |
|-------|-------------|--------|
| `h1.direction` | MarketContext JSONL (`logs/market_context/`) | String in nested h1 object |
| `h1.confidence` | MarketContext JSONL | Float in nested h1 object |
| `h1.swing_structure` | MarketContext JSONL | String in nested h1 object |
| `h1.bos_confirmed` | **Nowhere** (field doesn't exist) | — |

---

## 2. Consumption of H1 Structural Fields

### h1.direction

| File | Function | Purpose | Classification |
|------|----------|---------|---------------|
| `core/pipeline/new_engine.py` | `_score_trend_alignment()` | Reads H1 direction for trend alignment scoring | **SCORING** (Migration 2) |
| `core/pipeline/new_engine.py` | `_score_htf()` | Reads H1 direction for htf_alignment component | **SCORING** |
| `core/market_context/builder.py` | `_extract_h1()` | Extracts into H1Summary for MarketContext | **OBSERVABILITY** |
| `core/market_context/builder.py` | `_classify_phase()` | Uses H1 direction for phase classification | **OBSERVABILITY** |
| `core/market_context/conflict_resolver.py` | `resolve()` | Uses H1 direction for unified direction | **OBSERVABILITY** |
| `core/timeframes/htf_snapshot.py` | `build_htf_snapshot()` | Captures in HTFSnapshot for logging | **OBSERVABILITY** |

### h1.confidence

| File | Function | Purpose | Classification |
|------|----------|---------|---------------|
| `core/pipeline/new_engine.py` | `_score_trend_alignment()` | Modulates score based on H1 confidence | **SCORING** (Migration 2) |
| `core/pipeline/new_engine.py` | `_score_htf()` | Weights H1 contribution by confidence | **SCORING** |
| `core/market_context/builder.py` | `_classify_phase()` | Confidence threshold for phase classification | **OBSERVABILITY** |
| `core/market_context/conflict_resolver.py` | `resolve()` | Weights direction confidence in resolution | **OBSERVABILITY** |

### h1.swing_structure

| File | Function | Purpose | Classification |
|------|----------|---------|---------------|
| `core/market_context/builder.py` | `_classify_phase()` | HH_HL/LH_LL → IMPULSE/PULLBACK classification | **OBSERVABILITY** |
| `core/market_context/builder.py` | `_extract_h1()` | Stored in H1Summary | **OBSERVABILITY** |
| `core/timeframes/htf_snapshot.py` | `build_htf_snapshot()` | Used to infer H1 regime (TRENDING if HH_HL or LH_LL) | **OBSERVABILITY** |

**NOT consumed by:** scoring, gating, strategy selection, execution, or research systems.

### h1.bos_confirmed

**ZERO consumers** — this field does not exist in any code.

---

## 3. Duplicate Logic — Structure Computation from M5

### Locations that independently compute HH/HL, LH/LL, or BOS from M5 data:

| # | Module | Function | Data Source | Computes | Purpose |
|---|--------|----------|-------------|----------|---------|
| 1 | `core/pipeline/swing_context.py` | `compute_swing_context()` | **M5 candles** (50-bar lookback) | Swing direction (HH/HL vs LH/LL) + BOS + Phase | **HARD GATE** (blocks reversals without BOS) |
| 2 | `core/pipeline/swing_context.py` | `_find_swing_highs/lows()` | **M5 candles** (50-bar, 3-bar pivot confirmation) | Swing pivot points | Supports BOS calculation |
| 3 | `strategy/regime_activation.py` | `classify_regime()` | **M5 candles** (20-bar lookback) | HH/HL + LH/LL for regime classification | Regime detection (now partially replaced by Migration 1) |
| 4 | `strategy/structure_bias_scoring.py` | `_score_hh_ll_consistency()` | **M5 candles** | HH/HL sequence consistency | Advisory scoring (structure_bias) |
| 5 | `strategy/structure_bias_scoring.py` | `_score_bos_presence()` | **M5 candles** | Level break detection | Advisory scoring |
| 6 | `core/pipeline/structure_scoring.py` | `score_bar()` | **M5 candles** (per-bar) | Higher-high/higher-low detection | Parallel structure cohesion system |
| 7 | `core/timeframes/h1_bias.py` | `_swing_structure()` | **H1 candles** (20-bar lookback) | HH/HL + LH/LL swing structure | H1 bias analysis (authoritative H1 data) |
| 8 | `core/timeframes/h4_regime.py` | `_detect_hh_hl()` / `_detect_lh_ll()` | **H4 candles** (10-bar lookback) | HH/HL + LH/LL counts | H4 regime classification |

### Classification of Duplicate Computation

| Logic | H4 Version | H1 Version | M5 Version(s) | Status |
|-------|-----------|-----------|---------------|--------|
| **Swing direction (HH/HL vs LH/LL)** | `h4_regime._detect_hh_hl/ll()` | `h1_bias._swing_structure()` | `swing_context.py` (50-bar) + `regime_activation.py` (20-bar) + `structure_scoring.py` (per-bar) | **TRIPLICATED on M5** — same structural concept computed 3 different ways from M5 candles |
| **Break of Structure (BOS)** | Not computed | **Not computed** | `swing_context.py` (2-bar close confirmation) + `structure_bias_scoring._score_bos_presence()` | **M5 ONLY** — no H1 equivalent exists |
| **Structure quality** | Not computed | Not computed | `structure_scoring.py` (rolling buffer) + `structure_bias_scoring.py` (composite) | **M5 ONLY** |

---

## 4. Authority Map

| Concept | Current Authority | Source Data | Consumers | H1 Equivalent Exists? |
|---------|------------------|-------------|-----------|----------------------|
| **Regime** | H4 MarketContext (Migration 1) | H4 candles via `h4_regime.py` | Strategy activation, scoring, policy | ✅ Migrated |
| **Trend Alignment** | H1 Phase (Migration 2) | H1 candles via `h1_bias.py` | `_score_trend_alignment()` | ✅ Migrated |
| **Swing Direction** | **M5** `swing_context.py` | M5 candles (50-bar) | strategy_activation (advisory), scoring meta | ✅ `h1.swing_structure` (HH_HL/LH_LL/MIXED) — available but NOT used for gating |
| **BOS** | **M5** `swing_context.py` | M5 candles (50-bar + 2-bar confirm) | **HARD GATE** (reversal block) + eligibility | ❌ **No H1 BOS exists** |
| **Structural Bias** | **M5** `structure_bias_scoring.py` | M5 candles | Advisory score (try/except, non-authoritative) | ⚠️ H1 swing_structure partially overlaps |
| **Structure Cohesion** | **M5** `structure_scoring.py` | M5 candles (5-bar rolling) | Writes to EngineState (parallel, non-authoritative) | ❌ No H1 equivalent |

### Key Insight

**Swing Direction** has an H1 equivalent (`h1.swing_structure`) that is already computed and cached,
but it is NOT used for the swing gate. The swing gate reads exclusively from `compute_swing_context()`
which operates on M5 candles.

**BOS** has NO H1 equivalent. It would need to be implemented as a new computation on H1 candles.

---

## 5. Migration Readiness Assessment

### h1.swing_structure

| Question | Answer |
|----------|--------|
| Already flowing? | ✅ YES — computed in `h1_bias.py`, cached in TimeframeCache, exposed in MarketContext H1Summary |
| Used for decisions? | ❌ NO — only consumed by MarketContext builder (observability) and htf_snapshot (observability) |
| Equivalent M5 computation exists? | ✅ YES — `swing_context.py` computes HH/HL vs LH/LL from M5 50-bar data |
| Can replace M5 version? | ⚠️ PARTIALLY — H1 swing_structure provides DIRECTION (HH_HL/LH_LL/MIXED) but NOT the full SwingContext (strength, phase, levels, BOS) |
| Persistence? | ✅ Persisted in `logs/market_context/` JSONL |

### h1.bos_confirmed

| Question | Answer |
|----------|--------|
| Already flowing? | ❌ NO — field does not exist in any analyzer output |
| Used for decisions? | N/A — doesn't exist |
| Equivalent M5 computation exists? | ✅ YES — `swing_context.py` computes BOS from M5 data and uses it as a HARD GATE |
| Can replace M5 version? | ❌ NOT YET — H1 BOS would need to be implemented as a new computation in `h1_bias.py` or a new H1 analyzer module |
| Persistence? | N/A |

### Assessment Summary

```
swing_structure:  AVAILABLE but UNUSED for decisions
bos_confirmed:    NOT AVAILABLE — must be built
```

---

## 6. Migration Recommendation

### Recommended Migration 3 Scope: **C. Move Both Together (with staged implementation)**

**Justification:**

1. **Swing Direction alone is insufficient** — the swing gate checks BOTH direction AND BOS.
   Moving direction without BOS would create an inconsistency where direction comes from H1
   but BOS still comes from M5 (mixed timeframe authority on the same structural concept).

2. **BOS must be implemented first** — before we can move the swing gate to H1, we need
   an H1-level BOS computation that produces reliable results on H1 candles.

3. **Staged approach:**

   **Phase A:** Implement H1 BOS computation (new function in `h1_bias.py` or new module)
   - Compute whether price has broken the last H1 swing level
   - Produce `bos_confirmed: bool` field on H1 BiasSnapshot or a new H1StructureSnapshot
   - Run in shadow mode: log H1 BOS alongside M5 BOS, measure agreement rate

   **Phase B:** Once agreement rate is validated (>95% over 500+ decisions):
   - Replace `swing_context.py`'s M5-computed direction with H1 `swing_structure`
   - Replace `swing_context.py`'s M5-computed BOS with H1 `bos_confirmed`
   - Swing gate now reads from H1 authority instead of M5

   **Phase C:** Deprecate M5 swing_context computation (keep as diagnostic)

### Risk Assessment

| Risk | Severity | Mitigation |
|------|----------|-----------|
| H1 BOS fires less frequently than M5 BOS (hourly vs 5-min updates) | MEDIUM | H1 BOS represents REAL structural breaks; M5 may detect noise breaks |
| H1 BOS may disagree with M5 BOS in edge cases | HIGH | Shadow comparison required — cannot switch without validating agreement |
| Swing gate currently blocks 22.9% of decisions | HIGH | Any change to BOS source directly impacts trade count. Must validate carefully. |
| H1 updates every 12 cycles — stale BOS status possible | LOW | BOS is a structural event that persists for hours; hourly update is appropriate |
| Removing M5 swing_context entirely removes phase (EXPANSION/DISTRIBUTION/CORRECTION) | MEDIUM | H1 phase classification in MarketContext already captures this; migration preserves it |

### Why NOT "D. No migration required"

The current architecture has swing structure computed **three times independently** on M5 data:
- `swing_context.py` (50-bar, hard gate)
- `regime_activation.py` (20-bar, regime)
- `structure_scoring.py` (per-bar, parallel)

Plus once correctly on H1:
- `h1_bias.py` (20-bar H1, which = 240 M5 bars)

The H1 version is architecturally correct — it reads actual hourly structural data.
The M5 versions are attempting to reconstruct hourly structure from 5-minute noise.
Migration is warranted, but BOS must be implemented on H1 first.

---

## Deliverables Summary

### 1. Dependency Map
- H1 structural fields produced by `h1_bias.py` → cached in `TimeframeCache` → consumed by MarketContext builder + Migration 2 scoring
- M5 structural fields produced by `swing_context.py` → consumed directly by engine (hard gate) + strategy_activation (advisory)
- No dependency link between H1 structure and the M5 swing gate

### 2. Authority Map
See Section 4 table above.

### 3. Duplicate Computation Map
See Section 3 table above. Key finding: HH/HL/LH/LL is computed 5 times across 3 timeframes.

### 4. Migration Readiness
- `swing_structure`: READY (H1 data available, just not consumed by gate)
- `bos_confirmed`: NOT READY (must be implemented on H1 first)

### 5. Risk Assessment
See table in Section 6. Main risk: swing gate blocks 22.9% of decisions — changing BOS source directly affects trade count.

### 6. Recommended Migration 3 Scope
**Move both together** via staged implementation: implement H1 BOS → shadow compare → switch authority → deprecate M5 computation.

---

*Document produced: 2026-07-21*
*Status: Architecture Audit — No Code Modified*
