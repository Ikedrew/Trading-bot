# Architecture Audit — H4 Structural Analysis as Canonical Model

**Generated:** 2026-07-21
**Status:** Audit only — no code modified
**Context:** Post-Migration 1 (Regime), Migration 2 (Trend Alignment)

---

## 1. H4 Structure Engine — Complete Catalogue

### Location
- **File:** `core/timeframes/h4_regime.py`
- **Class:** None (module-level functions)
- **Entry point:** `analyze_regime(candles: list[Candle]) -> RegimeSnapshot`
- **Update frequency:** Every H4 bar close (~4 hours)
- **Cached:** ✅ Yes, in `TimeframeCache._entries[_TF_H4]`
- **Persisted:** ✅ Via MarketContext JSONL (H4 summary) + Decision Trace fields

### Helper Functions

| Function | Inputs | Outputs | Purpose |
|----------|--------|---------|---------|
| `_ema(values, period)` | list[float], int | list[float] | EMA series computation |
| `_atr(candles, period)` | list[Candle], int | list[float] | ATR series (Wilder smoothing) |
| `_detect_hh_hl(candles, lookback)` | list[Candle], int | (hh_count, hl_count) | Higher-high / higher-low detection |
| `_detect_lh_ll(candles, lookback)` | list[Candle], int | (lh_count, ll_count) | Lower-high / lower-low detection |
| `analyze_regime(candles)` | list[Candle] | RegimeSnapshot | Main classifier |

### Output Fields (RegimeSnapshot)

| Field | Type | Purpose |
|-------|------|---------|
| `classification` | RegimeClassification enum | TRENDING_BULLISH / TRENDING_BEARISH / RANGING / VOLATILE / TRANSITIONAL |
| `confidence` | float (0.0–1.0) | Classification certainty |
| `bar_time` | int | Timestamp of bar that produced this |
| `atr_ratio` | float | Current ATR / Average ATR (volatility state) |
| `ema_slope` | float | Normalized EMA-20 slope (trend direction) |
| `trend_bias` | str | "BULLISH" / "BEARISH" / "NEUTRAL" |
| `trend_strength` | float (0.0–1.0) | Structural trend confidence |

---

## 2. Structural Concepts Computed by H4

| Concept | Computed? | Method | Persisted? | Consumed Downstream? |
|---------|-----------|--------|-----------|---------------------|
| **HH/HL** | ✅ | `_detect_hh_hl()` — counts per-bar consecutive HH/HL in lookback window | ✅ (implicit in classification + trend_bias) | ✅ Regime classification |
| **LH/LL** | ✅ | `_detect_lh_ll()` — counts per-bar consecutive LH/LL | ✅ (implicit) | ✅ Regime classification |
| **Swing highs** | ❌ | Not computed (uses bar-to-bar comparison, not pivot detection) | — | — |
| **Swing lows** | ❌ | Not computed | — | — |
| **BOS** | ❌ | Not computed | — | — |
| **CHoCH** | ❌ | Not computed | — | — |
| **Trend direction** | ✅ | EMA-20 slope direction + HH/HL vs LH/LL dominance → `trend_bias` | ✅ | ✅ MarketContext direction |
| **Trend strength** | ✅ | Combined bull_ratio + ema_slope → `trend_strength` | ✅ | ✅ MarketContext conflict resolver |
| **Swing confidence** | ❌ | Not computed (confidence is for overall regime, not swing) | — | — |
| **Impulse/correction phase** | ❌ | Not computed (only identifies regime state, not phase within it) | — | — |
| **Structure quality** | ❌ | Not computed (only classifies regime type) | — | — |
| **Liquidity information** | ❌ | Not computed | — | — |
| **Volatility regime** | ✅ | ATR ratio → VOLATILE classification when >1.5 | ✅ | ✅ MarketContext H4 summary |
| **Range compression** | ✅ | Range/ATR ratio → contributes to RANGING classification | ✅ (implicit) | ✅ |

---

## 3. Cross-Timeframe Comparison Matrix

### Structural Concepts by Timeframe

| Concept | H4 (`h4_regime.py`) | H1 (`h1_bias.py`) | M15 (`m15_structure.py`) | M5 (`swing_context.py`) | M5 (`regime_activation.py`) | M5 (`structure_bias_scoring.py`) | M5 (`structure_scoring.py`) |
|---------|-----|-----|------|------|------|------|------|
| **HH/HL detection** | ✅ bar-to-bar counts | ✅ pivot-based (1-bar confirm) | ❌ | ✅ pivot-based (3-bar confirm) | ✅ half-window max comparison | ✅ half-window max comparison | ✅ per-bar adjacent comparison |
| **LH/LL detection** | ✅ bar-to-bar counts | ✅ pivot-based | ❌ | ✅ pivot-based | ✅ half-window comparison | ❌ | ✅ per-bar adjacent comparison |
| **Swing pivot detection** | ❌ (no actual pivots) | ✅ 1-bar confirmation | ✅ 2-bar left/right confirmation | ✅ 3-bar confirmation | ❌ | ✅ 2-bar confirmation | ❌ |
| **BOS** | ❌ | ❌ | ❌ | ✅ (2-bar close beyond level) | ❌ | ✅ (close beyond half-window level) | ❌ |
| **CHoCH** | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| **Trend direction** | ✅ EMA slope + structure | ✅ Swing structure + EMA + momentum | ❌ | ✅ HH/HL vs LH/LL | ✅ displacement + structure | ❌ | ❌ |
| **Trend strength** | ✅ combined ratio | ✅ confidence score | ❌ | ✅ composite (0–1) | ✅ displacement-based | ❌ | ❌ |
| **EMA computation** | ✅ EMA-20 (full series) | ✅ EMA-20 (latest value) | ❌ | ❌ | ❌ | ❌ | ❌ |
| **ATR computation** | ✅ ATR-14 (Wilder, full series) | ✅ ATR approximation (14-bar sum) | ✅ ATR approximation | ❌ | ❌ | ❌ | ❌ |
| **Volatility state** | ✅ atr_ratio classification | ❌ | ❌ | ❌ | ✅ vol_expanding/compressing | ✅ expansion/compression | ❌ |
| **Key levels (S/R)** | ❌ | ❌ | ✅ nearest_support/resistance | ❌ | ❌ | ❌ | ❌ |
| **Order blocks** | ❌ | ❌ | ✅ impulsive move detection | ❌ | ❌ | ❌ | ❌ |
| **Structure quality** | ❌ | ❌ | ✅ clarity score (0–1) | ❌ | ❌ | ✅ structure_score (0–100) | ✅ rolling score + regime |
| **Range quality** | ✅ (contributes to RANGING) | ❌ | ❌ | ❌ | ✅ range_quality (0–1) | ❌ | ❌ |
| **Phase (impulse/pullback)** | ❌ | ❌ | ❌ | ✅ EXPANSION/DISTRIBUTION/CORRECTION | ❌ | ❌ | ❌ |

### Classification

| Status | Count | Details |
|--------|-------|---------|
| **Duplicated across TFs** | 3 | HH/HL detection, LH/LL detection, trend direction |
| **Missing from H4** | 6 | Swing pivots, BOS, CHoCH, phase, structure quality, key levels |
| **Unique to M15** | 2 | Key levels (S/R), order blocks |
| **Unique to M5 swing_context** | 2 | BOS, phase classification |
| **Unique to H4** | 2 | ATR series (full), volatility ratio |
| **Conflicting implementations** | 2 | HH/HL (4 different methods), swing pivot confirmation (1/2/3-bar variants) |

---

## 4. Reusability Assessment

### Can H4 become a generic StructureAnalyzer(timeframe)?

**Answer: NO — not in its current form.**

### Reasons

| Limitation | Detail |
|-----------|--------|
| **Incomplete model** | H4 computes only 5 of 13 structural concepts. It lacks BOS, CHoCH, swing pivots, phase, S/R levels, structure quality, order blocks. |
| **Regime-centric, not structure-centric** | H4 answers "what regime are we in?" not "what is the structural state?" — regime is a classification derived FROM structure, not structure itself. |
| **No pivot detection** | H4 uses bar-to-bar counting (is this bar's high > previous bar's high?) not proper swing pivot detection (local extrema with confirmation bars). This is a simpler, less accurate method. |
| **No BOS/CHoCH** | The two most important structural events for trading decisions are completely absent from H4. |
| **Different concerns per timeframe** | H4 needs regime; H1 needs phase/direction; M15 needs quality/levels; M5 needs entry timing. A single implementation cannot serve all purposes. |
| **Parameters are hardcoded for H4** | Lookback (10-bar), EMA period (20), ATR period (14), slope threshold (0.15) — all calibrated for 4-hour bar geometry. |

### What COULD be shared

If a `StructureAnalyzer` base class were created, these functions could be reusable:

| Function | Shareable? | Timeframe-specific parameters |
|----------|-----------|-------------------------------|
| `_ema(values, period)` | ✅ Yes — pure math | period (20 for H4, 20 for H1) |
| `_atr(candles, period)` | ✅ Yes — pure math | period (14 universally) |
| `_detect_hh_hl(candles, lookback)` | ⚠️ Partially — the bar-to-bar method works but is less accurate than pivot-based | lookback window |
| `_detect_lh_ll(candles, lookback)` | ⚠️ Same as above | lookback window |
| Swing pivot detection | ❌ Different implementations: H1 uses 1-bar, M15 uses 2-bar, M5 uses 3-bar confirmation | confirmation bars |
| BOS detection | ❌ Only exists in M5, would need clean implementation | confirmation criteria |
| Classification logic | ❌ Completely different per timeframe (regime vs bias vs quality vs phase) | Everything |

### Conclusion

**A generic `StructureAnalyzer(timeframe)` would need to be a NEW implementation, not a refactoring of H4.**

H4's `analyze_regime()` is a regime classifier that uses some structural signals internally.
It is NOT a general-purpose structure analyzer. The reusable parts are limited to EMA/ATR math utilities.

---

## 5. Dependency Map

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    STRUCTURE ANALYSIS LAYER                              │
│                                                                         │
│  H4:  h4_regime.py → RegimeSnapshot                                    │
│        (HH/HL counts, EMA slope, ATR ratio, range compression)          │
│                                                                         │
│  H1:  h1_bias.py → BiasSnapshot                                        │
│        (swing structure HH_HL/LH_LL, EMA position, momentum)            │
│                                                                         │
│  M15: m15_structure.py → StructureSnapshot                              │
│        (swing pivots, S/R levels, order blocks, structure quality)        │
│                                                                         │
│  M5:  swing_context.py → SwingContext                                   │
│        (swing direction, BOS, phase, strength)                           │
│       regime_activation.py → RegimeOutput                                │
│        (displacement regime, noise, structure state)                      │
│       structure_bias_scoring.py → StructureBiasResult                    │
│        (structure score, bias score, regime, confidence)                  │
│       structure_scoring.py → (score, regime) on EngineState              │
│        (rolling per-bar cohesion)                                         │
└────────────────────────────────┬────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    MARKET CONTEXT LAYER                                  │
│                                                                         │
│  MarketContextBuilder reads:                                             │
│    H4: RegimeSnapshot → h4.regime, h4.trend_bias, h4.atr_ratio          │
│    H1: BiasSnapshot → h1.direction, h1.swing_structure, h1.confidence    │
│    M15: StructureSnapshot → m15.quality, m15.at_key_level                │
│    M5: EngineState → m5.regime_state, m5.bias_phase, m5.bias_strength    │
│                                                                         │
│  Produces: MarketContext (frozen)                                         │
└────────────────────────────────┬────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    STRATEGY / SCORING LAYER                              │
│                                                                         │
│  Consumers of structural data:                                           │
│                                                                         │
│  new_engine.py:                                                          │
│    _score_h4() ← reads HTFContext.regime (H4 structure)                  │
│    _score_htf() ← reads HTFContext.bias (H1 structure)                   │
│    _score_trend_alignment() ← reads H1 direction (Migration 2)           │
│    compute_swing_context() ← DUPLICATES H1 structure from M5 candles     │
│    check_swing_permission() ← reads M5 swing BOS (HARD GATE)            │
│                                                                         │
│  strategy_activation:                                                    │
│    run_strategy_activation(market_context_regime=) ← H4 regime (Mig. 1) │
│    eligibility_activation ← reads regime + BOS                           │
│    gating_activation ← reads swing context from M5                       │
│                                                                         │
│  structure_bias_scoring (advisory):                                      │
│    score_structure_and_bias() ← M5 candles (parallel, non-authoritative) │
└────────────────────────────────┬────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    DECISION / PERSISTENCE LAYER                          │
│                                                                         │
│  OpportunityAssessment:                                                  │
│    regime, htf_alignment, h4_alignment, components dict                   │
│                                                                         │
│  DecisionTrace:                                                          │
│    regime, regime_source, regime_timeframe, trend_alignment_source,       │
│    htf_alignment, h4_alignment, swing_direction, swing_break_confirmed    │
│                                                                         │
│  Market Context JSONL:                                                    │
│    h4.regime, h1.swing_structure, m15.quality, m5.regime_state            │
└────────────────────────────────┬────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    RESEARCH LAYER                                         │
│                                                                         │
│  Research shadow trades: decision_snapshot.market_context summary         │
│  Athena queries: regime × outcome, structure × R-multiple                │
│  Promotion monitor: accumulates outcomes per regime                       │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 6. Migration Impact — One vs Separate Implementations

### Recommendation: **Separate Implementations Per Timeframe**

### Justification

| Factor | One StructureAnalyzer | Separate Per-TF | Winner |
|--------|----------------------|-----------------|--------|
| **Accuracy** | Generic must compromise on confirmation bars, lookbacks | Each optimized for its timeframe's noise profile | **Separate** |
| **Responsibility** | Single module does too much (regime + bias + quality + phase) | Each module answers ONE question about its timeframe | **Separate** |
| **Existing code** | Would require rewriting 4 working analyzers | Analyzers already work; migration only moves AUTHORITY | **Separate** |
| **Testability** | Harder to test (must mock timeframe context) | Each testable in isolation (pure function of candles) | **Separate** |
| **Risk** | Changing shared code breaks all timeframes simultaneously | Changes to one TF don't affect others | **Separate** |
| **Performance** | Single computation but requires all candles available | Each computes independently on its own cached candles | **Separate** |
| **Shared utilities** | Would share EMA/ATR math | Can share via utility module without coupling analysis logic | **Separate** (with shared math) |

### Suggested Architecture

```
core/timeframes/
├── math_utils.py          ← NEW: shared EMA, ATR, pivot detection utilities
├── h4_regime.py           ← EXISTING: regime classification (H4 responsibility)
├── h1_bias.py             ← EXISTING: bias + direction (H1 responsibility)
├── h1_structure.py        ← NEW: BOS + swing direction (H1 structural authority)
├── m15_structure.py       ← EXISTING: quality + levels (M15 responsibility)
├── m5_micro.py            ← FUTURE: execution-level micro-structure (M5 responsibility)
├── cache.py               ← EXISTING: fetch scheduling
└── types.py               ← EXISTING: type contracts
```

The key insight: **each timeframe answers a different structural question:**
- H4: "What kind of market?" (regime)
- H1: "What direction and phase?" (bias + BOS)
- M15: "How tradeable is this location?" (quality + levels)
- M5: "Is this the right moment?" (entry timing)

A single analyzer cannot answer all four questions because they require different algorithms, different outputs, and different update frequencies.

---

## 7. Final Recommendation

### Architecture Diagram (Target)

```
H4 analyze_regime()      H1 analyze_bias()       M15 analyze_structure()
    │                    + h1_structure (NEW)          │
    │                         │                       │
    ▼                         ▼                       ▼
RegimeSnapshot           BiasSnapshot              StructureSnapshot
+ classification         + direction               + quality_score
+ trend_bias             + swing_structure         + at_key_level
+ atr_ratio              + bos_confirmed (NEW)     + order_block
+ ema_slope              + confidence              + nearest_s_r
    │                         │                       │
    └───────────┬─────────────┴───────────────────────┘
                │
                ▼
        MarketContext (unified)
                │
        ┌───────┼───────────┐
        ▼       ▼           ▼
    Scoring  Gating     Persistence
```

### Duplicate Computation Map

| Computation | Should Be Computed By | Currently Also Computed By (DUPLICATES) |
|-------------|----------------------|----------------------------------------|
| HH/HL direction | H1 `_swing_structure()` | M5 `swing_context._find_swing_highs/lows()`, M5 `regime_activation`, M5 `structure_scoring.score_bar()`, M5 `structure_bias_scoring._score_hh_ll_consistency()` |
| BOS | H1 (NEW — to be created) | M5 `swing_context.py` (50-bar), M5 `structure_bias_scoring._score_bos_presence()` |
| Regime classification | H4 `analyze_regime()` | M5 `regime_activation.classify_regime()` (DEPRECATED by Migration 1) |
| Trend direction | H1 `analyze_bias()` | M5 EMA-50 in `_score_trend_alignment()` (DEPRECATED by Migration 2) |
| Structure quality | M15 `analyze_structure()` | M5 `structure_scoring.py`, M5 `structure_bias_scoring.py` |

### Canonical Ownership Map (Target State)

| Concept | Canonical Owner | Authority Status |
|---------|----------------|-----------------|
| Market regime | H4 `h4_regime.py` | ✅ MIGRATED (Migration 1) |
| Trend direction | H1 `h1_bias.py` | ✅ MIGRATED (Migration 2) |
| Swing direction (HH/HL vs LH/LL) | H1 `h1_bias.py` | ⚠️ AVAILABLE but not used for gating |
| BOS | H1 (NEW — `h1_structure.py`) | ❌ NOT YET IMPLEMENTED |
| Structure quality | M15 `m15_structure.py` | ⚠️ Available but underused (only ±0.1 modifier) |
| Key levels (S/R) | M15 `m15_structure.py` | ⚠️ Computed but not consumed by scoring |
| Entry timing / confirmation | M5 (various) | ✅ Correctly owned |
| Volatility regime | H4 `h4_regime.py` | ✅ Available via `atr_ratio` |

### Refactor Opportunities

| Opportunity | Effort | Impact | Priority |
|-------------|--------|--------|----------|
| Create `core/timeframes/math_utils.py` for shared EMA/ATR | Low (extract 2 functions) | Low (reduces duplication) | LOW |
| Create `core/timeframes/h1_structure.py` for BOS | Medium (new module) | HIGH (enables swing gate migration) | **HIGH** |
| Promote M15 structure quality into scoring (beyond ±0.1) | Low (weight adjustment) | Medium (better setup quality signal) | MEDIUM |
| Remove M5 `regime_activation.classify_regime()` | Low (already bypassed) | Low (cleanup) | LOW |
| Remove M5 `swing_context.py` (after H1 BOS implemented) | Medium (hard gate migration) | HIGH (removes duplicate computation) | AFTER H1 BOS |

### Risks

| Risk | Severity | Mitigation |
|------|----------|-----------|
| Creating h1_structure.py changes nothing until wired | None | Implement in shadow mode first |
| Shared math_utils.py creates coupling | Low | Keep as pure math (no market logic) |
| Removing M5 structure too early breaks swing gate | HIGH | Only after H1 BOS validated at >95% agreement |

### Suggested Migration Order

```
1. ✅ DONE: H4 Regime Authority (Migration 1)
2. ✅ DONE: H1 Trend Alignment (Migration 2)
3. NEXT: Implement H1 BOS computation (new h1_structure.py)
4. THEN: Shadow-compare H1 BOS vs M5 BOS (validate agreement)
5. THEN: Move swing gate authority to H1 (Migration 3)
6. THEN: Promote M15 structure quality in scoring (Migration 4)
7. LAST: Deprecate/remove M5 structural duplicates (cleanup)
```

---

*Document produced: 2026-07-21*
*Status: Architecture Audit — No Code Modified*
*Recommendation: Separate implementations per timeframe, with shared math utilities*
