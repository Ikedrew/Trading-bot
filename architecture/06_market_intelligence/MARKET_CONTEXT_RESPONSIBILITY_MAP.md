# Market Context Layer — Responsibility Migration Map

**Generated:** 2026-07-20
**Status:** Architecture Analysis Only — No Code Modified
**Baseline:** `CURRENT_MARKET_CONTEXT_ARCHITECTURE.md`
**Purpose:** Define what moves, what stays, and the safest implementation path

---

## 1. Current Responsibility Map

### H4 — Current Responsibilities

| Responsibility | Output | Influence on Decisions |
|---------------|--------|----------------------|
| Regime classification | TRENDING_BULLISH / TRENDING_BEARISH / RANGING / VOLATILE / TRANSITIONAL | Scored via `_score_h4()` → 10% weight |
| Trend bias (shadow) | BULLISH / BEARISH / NEUTRAL | Currently observational — shadow field on RegimeSnapshot |
| Trend strength | 0.0–1.0 | Fed into `_score_h4()` for counter-trend penalty |
| ATR ratio | Current/Average volatility | Available on snapshot but only used implicitly in regime classification |
| EMA slope | Normalized directional slope | Used internally for regime classification, not exposed to scoring directly |

**Current H4 influence: 10% of composite score via one component.**
**H4 never hard-gates a trade.**

### H1 — Current Responsibilities

| Responsibility | Output | Influence on Decisions |
|---------------|--------|----------------------|
| Directional bias | BULLISH / BEARISH / NEUTRAL | Scored via `_score_htf()` → 14% weight (shared with M15) |
| Bias confidence | 0.0–1.0 | Modulates H1 contribution within `_score_htf()` |
| Swing structure | HH_HL / LH_LL / MIXED | Available on snapshot, not directly scored |
| EMA position | Normalized distance from EMA-20 | Available on snapshot, not directly scored |

**Current H1 influence: Primary contributor to 14% `htf_alignment` component.**
**H1 never hard-gates a trade.**

### M15 — Current Responsibilities

| Responsibility | Output | Influence on Decisions |
|---------------|--------|----------------------|
| Structure quality | 0.0–1.0 | Modifier (±0.1) within `_score_htf()` → included in 14% weight |
| Nearest support/resistance | Price levels | Available on snapshot, not directly scored |
| At key level | Boolean | Available on snapshot, not directly scored |
| Order block detection | Boolean | Available on snapshot, not directly scored |

**Current M15 influence: ±0.1 modifier within the `htf_alignment` component.**
**M15 never hard-gates a trade. Most of its output is unused by scoring.**

### M5 — Current Responsibilities

| Responsibility | Output | Influence on Decisions |
|---------------|--------|----------------------|
| Pattern detection | Signal[] | **HARD GATE** — no patterns = no trade |
| Pattern quality scoring | 0.0–1.0 | 14% weight via `pattern_quality` component |
| Bias alignment | 0.0–1.0 | 18% weight via `bias_alignment` component |
| Trend alignment (EMA-50) | 0.0–1.0 | 10% weight via `trend_alignment` component |
| Market quality (displacement) | 0.0–1.0 | 8% weight via `market_quality` component |
| Chop clarity (overlap) | 0.0–1.0 | 6% weight via `chop_clarity` component |
| Volatility quality | 0.0–1.0 | 7% weight via `volatility_quality` component |
| Bias stability | 0.0–1.0 | 7% weight via `bias_stability` component |
| Confirmation preview | 0.0–1.0 | 6% weight via `confirmation_pre` component |
| Strategy Activation (regime) | TRENDING / RANGE / TRANSITIONAL | Advisory: selects weight profile |
| Swing Context | Direction + BOS + Phase | **HARD GATE** — blocks reversals without BOS |
| Market State Engine | STRUCTURED / TRANSITIONAL / CHOP | ExecutionPolicy gate (sizing + RR requirements) |
| Bias FSM | Phase + strength + divergence | Post-scoring metadata (next cycle's bias_alignment input) |
| Structure Cohesion | score + regime | Parallel system (not yet authoritative) |
| Confirmation (full) | 0.0–1.0 | Feeds EV probability calculation |
| EV calculation | Positive/Negative | **PRIMARY GATE** — negative EV blocks trade |
| Execution Policy | Allow/Block | **HARD GATE** — policy blocks override all |

**Current M5 influence: 76% of scoring weight + 3 hard gates + EV primary decision.**
**M5 is overwhelmingly dominant in the current system.**

---

## 2. M5 Responsibility Audit

### Every Place Where M5 Currently Influences Decisions

| # | Component | Current Module | Current Role | Recommended Owner | Reason |
|---|-----------|---------------|-------------|-------------------|--------|
| 1 | Pattern detection | `signal_orchestrator.py` | Hard gate | **KEEP ON M5** | Patterns are candlestick-level signals — M5 is the correct timeframe for entry triggers |
| 2 | Pattern quality scoring | `new_engine._score_pattern_quality()` | 14% score weight | **KEEP ON M5** | Pattern strength is a property of the M5 candle that formed it |
| 3 | Bias alignment | `new_engine._score_bias_alignment()` | 18% score weight | **SPLIT: M15 + M5** | The bias FSM state (which drives this score) operates on M5 but represents medium-term directional conviction — this is a PHASE concept (H1) surfaced on M5. In the target model, the H1 Phase provides direction; M5 confirms alignment with that direction |
| 4 | Trend alignment (EMA-50) | `new_engine._score_trend_alignment()` | 10% score weight | **MOVE TO H1** | EMA-50 on M5 ≈ EMA-4 on H1. This is measuring medium-term trend — naturally an H1 Phase responsibility |
| 5 | Market quality (displacement) | `new_engine._score_market_quality()` | 8% score weight | **MOVE TO M15** | 5-bar net displacement on M5 = 25-minute momentum window. This evaluates whether there's a "setup forming" — M15 Setup responsibility |
| 6 | Chop clarity (overlap ratio) | `new_engine._score_chop_clarity()` | 6% score weight | **MOVE TO M15** | Candle overlap detection evaluates structure quality in the setup timeframe — M15 Setup responsibility |
| 7 | Volatility quality | `new_engine._score_volatility_quality()` | 7% score weight | **SPLIT: H4 + M5** | Volatility regime (expanding/normal/low) is a macro concept (H4 Structure). M5 keeps the micro execution check (spread-to-range feasibility) |
| 8 | Bias stability | `new_engine._score_bias_stability()` | 7% score weight | **MOVE TO H1** | Bias strength/stability represents conviction duration — this is an H1 Phase output indicating how established the current directional phase is |
| 9 | Confirmation preview | `new_engine._score_confirmation_pre()` | 6% score weight | **KEEP ON M5** | Candle body quality at bar-of-entry — this is execution micro-confirmation (Trigger responsibility) |
| 10 | Strategy Activation regime | `strategy_activation.py` | Advisory (weight selection) | **MOVE TO H4/H1** | 20-bar M5 displacement ≈ looking at 100-minute structure. This duplicates what H4 already classifies. Should read H4 regime directly |
| 11 | Swing Context direction | `swing_context.py` | Hard gate for reversals | **MOVE TO H1** | 50-bar M5 swing analysis ≈ 4-hour directional structure. This is literally computing H1-level swing direction on M5 data. Natural H1 Phase output |
| 12 | Swing BOS confirmation | `swing_context.py` | Hard gate | **MOVE TO H1** | Break of Structure is a phase-change indicator — H1 Phase responsibility |
| 13 | Swing Phase | `swing_context.py` | Not currently used in scoring | **MOVE TO H1** | EXPANSION/DISTRIBUTION/CORRECTION is the textbook definition of "market phase" |
| 14 | Market State Engine | `market_state_engine.py` | ExecutionPolicy gate | **KEEP ON M5** | This measures execution environment stability (score variance, strategy flip rate) — it's a meta-signal about M5 scoring reliability, not market structure |
| 15 | Bias FSM | `bias_fsm.py` | Post-scoring metadata | **SPLIT: H1 provides direction, M5 FSM confirms timing** | The FSM tracks when M5 price action confirms or contradicts the prevailing direction. Direction ownership moves to H1; confirmation timing stays M5 |
| 16 | Confirmation (full) | `confirm_signal_detailed()` | Feeds EV probability | **KEEP ON M5** | Candle quality at point of execution — M5 Trigger |
| 17 | EV calculation | `expected_value.py` | Primary gate | **KEEP ON M5** | EV is computed at execution timeframe with execution-level parameters |
| 18 | Execution Policy | `execution_policy.py` | Hard gate | **KEEP ON M5** | Policy decisions are execution-level concerns |

### Summary of Migrations

| Destination | Items Moving |
|-------------|-------------|
| **KEEP ON M5** | Pattern detection, pattern quality, confirmation_pre, confirmation full, Market State Engine, EV, Execution Policy (8 items) |
| **MOVE TO H4** | Strategy Activation regime (1 item, partially — reads H4 instead of recomputing), volatility regime (1 item, partially) |
| **MOVE TO H1** | Trend alignment, bias stability, Swing Direction, Swing BOS, Swing Phase (5 items) |
| **MOVE TO M15** | Market quality, chop clarity (2 items) |
| **SPLIT** | Bias alignment (H1 direction + M5 confirmation), Volatility quality (H4 macro + M5 micro), Bias FSM (H1 direction + M5 timing) (3 items) |

---

## 3. New Timeframe Ownership Model

### H4 = STRUCTURE

Responsible for answering: **"What kind of market are we in?"**

| Responsibility | Source | Output |
|---------------|--------|--------|
| Macro regime classification | Existing `h4_regime.py` (unchanged) | TRENDING / RANGING / VOLATILE / TRANSITIONAL |
| Trend direction | Existing `trend_bias` field on RegimeSnapshot | BULLISH / BEARISH / NEUTRAL |
| Trend strength/confidence | Existing fields | 0.0–1.0 |
| Volatility regime | Existing `atr_ratio` → classified | LOW / NORMAL / EXPANDING / EXTREME |
| Market location (macro) | NEW: position within H4 range | NEAR_HIGH / NEAR_LOW / MID_RANGE |

**Update frequency:** Every 4 hours (H4 bar close)
**Hard gate authority:** None (advisory only — same as today)
**Scoring influence:** Regime alignment + volatility context

### H1 = PHASE

Responsible for answering: **"What phase of the move are we in?"**

| Responsibility | Source | Output |
|---------------|--------|--------|
| Directional bias | Existing `h1_bias.py` (unchanged) | BULLISH / BEARISH / NEUTRAL |
| Market phase | NEW: derived from H1 swing analysis | IMPULSE / PULLBACK / CONSOLIDATION / EXHAUSTION / REVERSAL |
| Swing direction | MIGRATED from `swing_context.py` (recomputed on H1) | BULLISH / BEARISH / NEUTRAL |
| Break of Structure | MIGRATED from `swing_context.py` | boolean |
| Trend alignment | MIGRATED from `_score_trend_alignment()` | 0.0–1.0 |
| Bias conviction | MIGRATED from `_score_bias_stability()` | 0.0–1.0 |
| Phase duration | NEW: bars in current phase | integer |

**Update frequency:** Every 1 hour (H1 bar close)
**Hard gate authority:** Swing BOS requirement for reversals (migrated from M5)
**Scoring influence:** Phase alignment + directional conviction + trend agreement

### M15 = SETUP

Responsible for answering: **"Is there a valid opportunity forming?"**

| Responsibility | Source | Output |
|---------------|--------|--------|
| Structure quality | Existing `m15_structure.py` (unchanged) | 0.0–1.0 |
| Key level proximity | Existing fields | at_key_level boolean + S/R levels |
| Order block presence | Existing field | boolean |
| Setup quality (displacement) | MIGRATED from `_score_market_quality()` | 0.0–1.0 |
| Setup clarity (anti-chop) | MIGRATED from `_score_chop_clarity()` | 0.0–1.0 |
| Strategy suitability | MIGRATED from `strategy_activation.py` context eval | REVERSAL / CONTINUATION / FALSE_BREAK appropriateness |
| Setup confidence | NEW: composite of quality + clarity + level + structure | 0.0–1.0 |

**Update frequency:** Every 15 minutes (M15 bar close)
**Hard gate authority:** None (scored, not gated)
**Scoring influence:** Setup quality + clarity + level relevance

### M5 = TRIGGER

Responsible for answering: **"Is this the right moment to enter?"**

| Responsibility | Source | Output |
|---------------|--------|--------|
| Pattern detection | Existing `signal_orchestrator.py` (unchanged) | Signal[] |
| Pattern quality | Existing `_score_pattern_quality()` (unchanged) | 0.0–1.0 |
| Candle confirmation | Existing `confirm_signal_detailed()` (unchanged) | STRONG / WEAK / INVALID |
| Confirmation score | Existing `_compute_confirmation_score()` (unchanged) | 0.0–1.0 |
| Execution feasibility | Market State Engine (unchanged) | STRUCTURED / TRANSITIONAL / CHOP |
| Spread/execution conditions | Existing spread_guard | Within limits |
| EV calculation | Existing `expected_value.py` (unchanged) | Positive/Negative |
| Execution Policy | Existing `execution_policy.py` (unchanged) | Allow/Block |
| M5 bias FSM timing | Existing `bias_fsm.py` (reads H1 direction, confirms on M5) | Phase transitions |

**Update frequency:** Every cycle (M5 bar close)
**Hard gate authority:** Pattern gate, EV gate, Execution Policy, Runtime Guards
**Scoring influence:** Pattern quality + confirmation + execution environment

---

## 4. Migration Table

| # | Current Component | Current Owner | New Owner | Migration Required? | Reason |
|---|------------------|---------------|-----------|---------------------|--------|
| 1 | Pattern detection | M5 (`signal_orchestrator`) | **M5 (TRIGGER)** | ❌ No | Entry signals belong on execution timeframe |
| 2 | Pattern quality scoring | M5 (`_score_pattern_quality`) | **M5 (TRIGGER)** | ❌ No | Pattern strength is M5-bar-level data |
| 3 | Bias alignment scoring | M5 (`_score_bias_alignment`) | **H1 (PHASE) → M5** | ✅ Yes | H1 provides direction; M5 checks alignment with it. Scoring formula unchanged, input source changes |
| 4 | HTF alignment scoring | M5 (`_score_htf`) | **H1 (PHASE) + M15 (SETUP)** | ✅ Yes | Currently reads raw HTFContext inline. Should read pre-computed Phase direction + Setup quality from MarketContext |
| 5 | H4 alignment scoring | M5 (`_score_h4`) | **H4 (STRUCTURE)** | ✅ Yes | Currently reads raw RegimeSnapshot inline. Should read pre-computed Structure regime from MarketContext |
| 6 | Confirmation score | M5 (`_compute_confirmation_score`) | **M5 (TRIGGER)** | ❌ No | Execution-moment candle quality |
| 7 | Market quality | M5 (`_score_market_quality`) | **M15 (SETUP)** | ✅ Yes | 5-bar M5 displacement ≈ M15 momentum. Recompute on M15 candles for setup-level view |
| 8 | Chop clarity | M5 (`_score_chop_clarity`) | **M15 (SETUP)** | ✅ Yes | Candle overlap analysis belongs at setup timeframe |
| 9 | Trend alignment | M5 (`_score_trend_alignment`) | **H1 (PHASE)** | ✅ Yes | EMA-50 on M5 ≈ medium-term trend. H1 Phase already has directional bias + EMA position |
| 10 | Volatility quality | M5 (`_score_volatility_quality`) | **H4 (STRUCTURE) + M5** | ✅ Partial | Macro volatility → H4 ATR ratio. Micro execution check → stays M5 |
| 11 | Bias stability | M5 (`_score_bias_stability`) | **H1 (PHASE)** | ✅ Yes | Conviction duration is a phase-level concept |
| 12 | Regime classification | M5 (`strategy_activation._detect_regime`) | **H4 (STRUCTURE)** | ✅ Yes | Currently duplicates H4's job on M5 data. Should read H4 regime directly |
| 13 | Strategy selection | M5 (`strategy_activation`) | **M15 (SETUP)** | ✅ Yes | Strategy suitability depends on setup context (key level, sweep, rejection) — M15 structural features |
| 14 | Swing direction | M5 (`swing_context.py`) | **H1 (PHASE)** | ✅ Yes | 50-bar M5 analysis = H1 directional structure |
| 15 | Swing BOS | M5 (`swing_context.py`) | **H1 (PHASE)** | ✅ Yes | Break of structure is a phase transition indicator |
| 16 | Swing phase | M5 (`swing_context.py`) | **H1 (PHASE)** | ✅ Yes | EXPANSION/DISTRIBUTION/CORRECTION = market phase |
| 17 | Market State Engine | M5 (`market_state_engine.py`) | **M5 (TRIGGER)** | ❌ No | Execution stability meta-signal — stays on M5 |
| 18 | Bias FSM | M5 (`bias_fsm.py`) | **M5 reads H1 PHASE** | ✅ Partial | FSM continues on M5 for timing, but reads H1 Phase direction instead of self-detecting |
| 19 | EV calculation | M5 (`expected_value.py`) | **M5 (TRIGGER)** | ❌ No | Execution-level calculation |
| 20 | Execution Policy | M5 (`execution_policy.py`) | **M5 (TRIGGER)** | ❌ No | Execution-level gate |
| 21 | Opportunity Assessment | M5 (`new_engine.py`) | **M5 (TRIGGER)** | ✅ Enriched | Gains MarketContext reference; existing fields preserved |

### Migration Count Summary

| Category | Count |
|----------|-------|
| No migration needed (stays on M5) | 9 |
| Full migration to H4 | 2 |
| Full migration to H1 | 5 |
| Full migration to M15 | 3 |
| Partial migration (split or enriched) | 4 |
| **Total components** | **21** |

---

## 5. Data Flow — Before vs After

### CURRENT Data Flow

```
MT5 Data Feed
    │
    ├── H4 bars → h4_regime.py → RegimeSnapshot ──────────────────────┐
    ├── H1 bars → h1_bias.py → BiasSnapshot ──────────────────────────┤
    ├── M15 bars → m15_structure.py → StructureSnapshot ──────────────┤
    │                                                                  │
    │   TimeframeCache.get_htf_context() ─────────── HTFContext ◄──────┘
    │                                                    │
    ├── M5 bars ─────────────────────────────────────────┼──────────┐
    │                                                    │          │
    │   ┌─────────── run_new_engine() ───────────────────┼──────────┤
    │   │                                                │          │
    │   │  Pattern Detection (M5) ◄──────────────────────┼──────────┘
    │   │       │                                        │
    │   │  Strategy Activation (M5 regime) ◄─────────────┘(not used)
    │   │       │
    │   │  Swing Context (M5 50-bar) ←────── Hard gate
    │   │       │
    │   │  10 Component Scores:
    │   │    ├─ pattern_quality (M5)
    │   │    ├─ bias_alignment (M5 FSM state)
    │   │    ├─ market_quality (M5 5-bar)
    │   │    ├─ trend_alignment (M5 EMA-50)
    │   │    ├─ chop_clarity (M5 overlap)
    │   │    ├─ volatility_quality (M5 5-bar)
    │   │    ├─ bias_stability (M5 FSM strength)
    │   │    ├─ confirmation_pre (M5 candle)
    │   │    ├─ htf_alignment (H1+M15 → inline computation)
    │   │    └─ h4_alignment (H4 → inline computation)
    │   │       │
    │   │  Market State Engine (M5 rolling scores)
    │   │       │
    │   │  Execution Policy (EV gate)
    │   │       │
    │   └── Decision: EXECUTE / NO_TRADE
    │
    └── Bias FSM update (post-decision, metadata)
```

**Problem:** 8 of 10 scoring components are M5-local computations.
HTF data passes through but is consumed inline with minimal influence (24%).
M5 does its own regime, phase, and structure analysis independently.

### TARGET Data Flow

```
MT5 Data Feed
    │
    ├── H4 bars → h4_regime.py → RegimeSnapshot ──────┐
    │                                                   │
    │   ┌── H4 STRUCTURE Layer ◄────────────────────────┘
    │   │   • Regime: TRENDING / RANGING / VOLATILE / TRANSITIONAL
    │   │   • Direction: BULLISH / BEARISH / NEUTRAL
    │   │   • Volatility: LOW / NORMAL / EXPANDING / EXTREME
    │   │   • Confidence: 0.0–1.0
    │   └──────────────────────────────────────────────────┐
    │                                                       │
    ├── H1 bars → h1_bias.py → BiasSnapshot ──────────┐    │
    │                                                   │    │
    │   ┌── H1 PHASE Layer ◄────────────────────────────┘    │
    │   │   • Phase: IMPULSE / PULLBACK / CONSOLIDATION /    │
    │   │           EXHAUSTION / REVERSAL                     │
    │   │   • Direction: BULLISH / BEARISH / NEUTRAL          │
    │   │   • Swing BOS: boolean (hard gate for reversals)   │
    │   │   • Conviction: 0.0–1.0                            │
    │   │   • Trend alignment: 0.0–1.0                       │
    │   │   Reads H4 Structure for context ◄──────────────────┘
    │   └──────────────────────────────────────────────────┐
    │                                                       │
    ├── M15 bars → m15_structure.py → StructureSnapshot ┐   │
    │                                                    │   │
    │   ┌── M15 SETUP Layer ◄────────────────────────────┘   │
    │   │   • Setup quality: 0.0–1.0                         │
    │   │   • Setup clarity (anti-chop): 0.0–1.0            │
    │   │   • Displacement quality: 0.0–1.0                  │
    │   │   • Key level: boolean + levels                    │
    │   │   • Strategy suitability: best strategy type       │
    │   │   • Setup confidence: 0.0–1.0 composite           │
    │   │   Reads H1 Phase for context ◄──────────────────────┘
    │   └──────────────────────────────────────────────────┐
    │                                                       │
    ├── M5 bars ────────────────────────────────────────────┤
    │                                                       │
    │   ┌── M5 TRIGGER Layer ◄──────────────────────────────┘
    │   │   Reads MarketContext (H4+H1+M15 unified)
    │   │
    │   │   Pattern Detection (M5) ← Hard gate
    │   │       │
    │   │   Score Components (reduced set):
    │   │     ├─ pattern_quality (M5 — unchanged)
    │   │     ├─ phase_alignment (reads H1 Phase direction)
    │   │     ├─ setup_quality (reads M15 Setup confidence)
    │   │     ├─ structure_alignment (reads H4 Structure regime)
    │   │     ├─ confirmation_pre (M5 — unchanged)
    │   │     └─ execution_environment (Market State Engine — unchanged)
    │   │       │
    │   │   EV Calculation (unchanged)
    │   │       │
    │   │   Execution Policy (unchanged)
    │   │       │
    │   └── Decision: EXECUTE / NO_TRADE
    │
    └── Bias FSM (reads H1 Phase direction, confirms on M5 timing)
```

**Key change:** Information flows TOP-DOWN (H4 → H1 → M15 → M5).
Each layer produces ONE output consumed by the layer below.
M5 no longer recomputes what higher timeframes already know.

---

## 6. Persistence Impact

### 6.1 Decision Trace

| Change Type | Field | Action |
|-------------|-------|--------|
| **Reuse** | `regime` | Populated from MarketContext.regime instead of activation.regime |
| **Reuse** | `regime_confidence` | From MarketContext instead of activation.regime_confidence |
| **Reuse** | `market_state` | Unchanged (still from MarketStateEngine) |
| **Reuse** | `market_state_confidence` | Unchanged |
| **Reuse** | `htf_alignment` | Score value unchanged; computation source moves |
| **Reuse** | `h4_alignment` | Score value unchanged; computation source moves |
| **Reuse** | `components` dict | Same 10 keys (scores may be derived differently but format preserved) |
| **New** | `market_context_id` | Unique identifier linking to market_context persistence |
| **New** | `h4_structure` | H4 STRUCTURE output summary |
| **New** | `h1_phase` | H1 PHASE classification (IMPULSE/PULLBACK/etc.) |
| **New** | `m15_setup_quality` | M15 SETUP confidence score |
| **New** | `context_direction` | Unified direction from MarketContext |
| **Obsolete** | None | All existing fields remain (backward compatible) |

### 6.2 Decision Audit

| Change Type | Field | Action |
|-------------|-------|--------|
| **Reuse** | All existing fields | Unchanged — decision audit captures full engine state |
| **New** | `market_context` | Nested object with unified context snapshot |
| **Obsolete** | None | Existing fields preserved for backward compatibility |

### 6.3 Opportunity Assessment

| Change Type | Field | Action |
|-------------|-------|--------|
| **Reuse** | All 26+ existing fields | Unchanged |
| **New** | `market_context_ref` | Reference to frozen MarketContext used for this assessment |
| **New** | `h1_phase` | Phase at time of assessment |
| **New** | `m15_setup_confidence` | Setup confidence at assessment time |
| **Reclassified** | `regime` | Still present; source changes from activation to MarketContext |
| **Reclassified** | `htf_alignment` | Still present; score derivation changes but value range preserved |
| **Obsolete** | None | All existing fields remain |

### 6.4 Shadow Trades / Research Shadow Trades

| Change Type | Field | Action |
|-------------|-------|--------|
| **Reuse** | All existing `decision_snapshot` fields | Unchanged |
| **New** | `decision_snapshot.market_context` | Nested summary: {direction, regime, phase, setup_quality} |
| **Obsolete** | None |

### 6.5 Trade Truth

| Change Type | Action |
|-------------|--------|
| **Unchanged** | Trade truth records execution outcomes — not market context. No changes needed. |

### 6.6 New Persistence: Market Context

| Aspect | Detail |
|--------|--------|
| **Path** | `logs/market_context/{SYMBOL}/{DATE}.jsonl` |
| **S3** | `s3://trading-bot-data-mk1/market_context/{SYMBOL}/{DATE}.jsonl` |
| **Write trigger** | Material change only (direction/regime/phase changed) |
| **Schema** | As defined in `MARKET_CONTEXT_LAYER_DESIGN.md` §9 |

### 6.7 Summary

- **New fields required:** 5-6 across decision_trace, assessment, shadow trades
- **Existing fields reused:** All current fields preserved with same semantics
- **Fields becoming obsolete:** None (backward compatibility preserved)
- **Fields remaining unchanged:** trade_truth, event_stream, ledger structure

---

## 7. Risk Assessment

### 7.1 Can Safely Move Without Changing Behaviour

| Item | Why Safe |
|------|----------|
| H4 regime reading (strategy_activation → read MarketContext instead) | Same data, different source. Activation currently computes its own M5 regime — switching to H4 changes the regime value but activation is ADVISORY only (no hard gate). Shadow mode validates. |
| HTF scoring input source (inline → MarketContext) | As long as MarketContext exposes same H1 direction + M15 quality, the `_score_htf()` math is identical. |
| Market context persistence | Additive — new persistence layer writes alongside existing. Zero impact on decision logic. |
| Decision trace enrichment | Additive new fields — existing fields preserved. |

### 7.2 Would Change Strategy Behaviour

| Item | Risk | Mitigation |
|------|------|-----------|
| Replacing M5 `_detect_regime()` with H4 regime | Strategy activation currently sees TRENDING/RANGE/TRANSITIONAL from M5 20-bar. H4 classifies differently (broader view). This CHANGES which strategy gets selected and which weights apply. | Shadow mode: compute both, log disagreements, measure impact before switching |
| Moving trend alignment from M5 EMA-50 to H1 bias | EMA-50 on M5 updates every cycle. H1 bias updates every 12 cycles. Direction can lag or differ. | Shadow mode: log both scores, compare, only switch when correlation > 0.8 |
| Moving swing direction from M5 50-bar to H1 | H1 swing analysis may use different lookback and detect different pivots. BOS gate could fire differently. | Critical — this is a HARD GATE. Must validate zero-diff on BOS decisions before switching |
| Moving market_quality and chop_clarity to M15 | These currently score on M5 5-bar windows. M15 analysis operates on M15 candles with different bar geometry. Scores WILL change. | Must recalibrate: compute equivalent on M15, compare distribution against current M5 scores |

### 7.3 Must Remain Backwards Compatible

| Item | Constraint |
|------|-----------|
| OpportunityAssessment field names + types | Consumers (decision_audit, decision_trace, Athena queries) depend on existing schema |
| 10 component score keys | `components` dict keys must remain the same for replay comparison |
| Score range [0.0–1.0] per component | All downstream (EV, policy, trace diagnostics) assume this range |
| Engine result dict structure | `handle_no_trade_outcome()`, `prepare_execution()`, observers all read specific keys |
| Persistence S3 paths + schemas | Athena tables depend on current structure |

### 7.4 Should Be Introduced In Shadow Mode First

| Item | Shadow Mode Approach |
|------|---------------------|
| **MarketContext construction** | Build context, log it, compare against what engine currently computes inline |
| **H1 Phase classification** | Classify phase from H1 data, log alongside existing swing_context output, measure agreement |
| **M15 Setup quality** | Compute setup confidence from M15 data, log alongside current market_quality + chop_clarity, compare distributions |
| **Regime source switch** | Log H4-sourced regime alongside strategy_activation regime, count disagreements |
| **Unified direction** | Log resolved direction alongside current bias_alignment, measure impact |
| **Swing BOS migration** | Log H1-computed BOS alongside current M5-computed BOS, count cases where they disagree. BLOCK migration until disagreement rate < 1% |

---

## 8. Final Recommendation

### Phase 1: Create Foundation (No Behaviour Change)

**What to create:**

1. `core/market_context/models.py` — MarketContext frozen dataclass with H4/H1/M15/M5 summary sections
2. `core/market_context/builder.py` — Reads existing TimeframeCache + EngineState, produces MarketContext
3. `core/market_context/persistence.py` — JSONL + S3 persistence (material-change-only)
4. `core/market_context/change_detector.py` — Detects direction/regime/phase transitions

**Behaviour:**
- MarketContext is built every cycle (consumes existing cached data)
- Logged in shadow mode alongside existing pipeline
- Persisted on material change
- Engine A continues unchanged — does NOT read MarketContext yet
- No scoring, no gating, no decision influence

**Validation:**
- MarketContext fields match what engine currently computes inline
- Persistence works (local + S3)
- Athena table created and queryable
- Zero impact on decisions (replay produces identical results)

**Effort:** ~3 days
**Risk:** None (additive only)

---

### Phase 2: H1 Phase Classification + Shadow Comparison

**What to create:**

1. H1 Phase classifier — Determines IMPULSE/PULLBACK/CONSOLIDATION/EXHAUSTION/REVERSAL from H1 candles
2. H1 Swing BOS — Migrates swing direction + BOS logic to operate on H1 data
3. Shadow comparison logging — Logs H1 phase vs M5 swing_context, counts disagreements

**Behaviour:**
- H1 Phase is computed and stored in MarketContext
- M5 swing_context continues to run as production authority
- Both are logged — disagreements tracked
- Decision: if disagreement rate < 1% over 1000 decisions → Phase 3 migration safe

**What to validate:**
- H1 Phase classification makes sense (spot-check against charts)
- H1 BOS agrees with M5 BOS in >99% of cases
- M15 setup quality correlates with current market_quality + chop_clarity

**Effort:** ~5 days
**Risk:** Low (shadow only — no decision impact)

---

### Phase 3: Connect to Decision Making

**What to migrate (once Phase 2 validation passes):**

1. **Score source migration** — `_score_htf()` reads MarketContext.h1_phase instead of raw HTFContext
2. **Score source migration** — `_score_h4()` reads MarketContext.h4_structure instead of raw RegimeSnapshot
3. **Regime source** — `strategy_activation` reads MarketContext.regime instead of self-computing
4. **Swing gate** — Swing BOS reads MarketContext.h1_bos instead of computing from M5
5. **New components** — Replace market_quality + chop_clarity with m15_setup_quality
6. **Bias FSM input** — Reads H1 Phase direction as primary direction source

**Behaviour:**
- Engine A scores change (derivation path different, values should be similar)
- Must validate: replay produces decisions within ε of baseline
- Score distributions must match (±0.05 per component)
- No new hard gates introduced (only source changes)

**Safeguard:**
- Feature flag: `MARKET_CONTEXT_SCORING_ENABLED = False` (default)
- When False: engine reads inline as today (zero change)
- When True: engine reads from MarketContext
- A/B comparison: log both paths, promote when validated

**Effort:** ~5 days
**Risk:** Medium (changes scoring source — must validate extensively)

---

### Phase 4: Remove Legacy Paths (After Validation)

**What to clean up (only after Phase 3 is validated in production):**

1. Remove `strategy_activation._detect_regime()` (now reads H4)
2. Remove M5 swing_context computation (now provided by H1)
3. Remove inline `_score_htf()` / `_score_h4()` raw HTFContext reads (reads MarketContext)
4. Simplify `_compute_all_scores()` — 6 lean components instead of 10 mixed-source
5. Deprecate `core/timeframes/integration.py` (dead code — already a no-op)

**Effort:** ~2 days
**Risk:** Low (only removes code that's already been replaced and validated)

---

### Summary: The Safest Path

```
Phase 1 (Week 1):    Build MarketContext — additive, no behaviour change
Phase 2 (Week 2-3):  Shadow H1 Phase — compare against existing, measure agreement
Phase 3 (Week 4):    Connect to scoring — feature-flagged, validated via replay
Phase 4 (Week 5+):   Clean up — remove legacy paths after production validation
```

**Guiding principle:** At no point does the system lose the ability to produce its
current decisions. Every migration step is reversible via feature flag.
Engine A authority is preserved throughout.

---

*Document produced: 2026-07-20*
*Status: Architecture Analysis — No Code Modified*
*Implementation: NOT started*
