# Engine A — Market Context Layer Architecture Design

## Status: DESIGN ONLY — No Implementation

---

## 1. Executive Summary

This document specifies a unified **Market Context Layer** that consolidates
all market interpretation into a single authoritative service. Currently,
market understanding is scattered across 5+ modules that independently read
timeframe data, classify regime, assess bias, and evaluate structure. The
proposed layer replaces this with one department responsible for answering:

> "What is the market doing right now?"

The Decision Engine then asks only:

> "Given this context, should I trade?"

---

## 2. Problem Statement

### Current Scattered Architecture

| Concern | Current Location | Problem |
|---------|-----------------|---------|
| H4 Regime | `core/timeframes/h4_regime.py` | Read by cache, consumed in scoring |
| H1 Bias | `core/timeframes/h1_bias.py` | Read by cache, consumed in scoring |
| M15 Structure | `core/timeframes/m15_structure.py` | Read by cache, consumed in scoring |
| M5 Regime | `core/pipeline/market_context.py` | Mutates EngineState.regime_state |
| Market State | `core/pipeline/market_state_engine.py` | Rolling window, separate singleton |
| Bias FSM | `core/pipeline/bias_fsm.py` | Mutates EngineState bias fields |
| Chop Filter | `strategy/market_filter.py` | Hard gate in market_context |
| Trend EMA | Inside `_score_trend()` in new_engine | Embedded in scoring |
| HTF Integration | `core/timeframes/integration.py` | Dead code (Phase 1 no-op) |

### Problems This Creates

1. **No single source of truth** — regime is classified in H4, M5, and MarketStateEngine
2. **Scattered interpretation** — 5 modules independently interpret "is this tradeable?"
3. **Coupling** — scoring functions embed market reading logic
4. **No conflict resolution** — H4 BULLISH + H1 BEARISH has no arbiter
5. **Duplicate state** — EngineState.regime_state vs RegimeClassification vs MarketState
6. **Untestable** — market understanding is entangled with scoring weights
7. **No change-detection** — context is rebuilt every cycle even when nothing changed

---

## 3. Proposed Architecture

### High-Level Flow

```
MT5 Data Feed
     │
     ▼
┌─────────────────────────────────┐
│     Timeframe Readers           │  (existing: h4_regime, h1_bias, m15_structure)
│     H4 │ H1 │ M15 │ M5        │
└────┬────┴────┴─────┴───────────┘
     │
     ▼
┌─────────────────────────────────┐
│     Market Context Builder      │  ← NEW: single authority
│                                 │
│  • Combines all timeframes      │
│  • Resolves cross-TF conflicts  │
│  • Classifies state + direction │
│  • Computes confidence          │
│  • Detects material changes     │
└────┬────────────────────────────┘
     │
     ▼
┌─────────────────────────────────┐
│     Market Context Object       │  ← NEW: frozen, immutable
│     (one per symbol per cycle)  │
└────┬────────────────────────────┘
     │
     ├──▶ Persistence (on material change)
     │       └─▶ Local JSONL + S3 mirror
     │
     ▼
┌─────────────────────────────────┐
│     Decision Engine             │  (existing: new_engine.py)
│     Receives MarketContext      │
│     as a read-only input        │
└─────────────────────────────────┘
```

### Responsibility Boundaries

| Component | Does | Does NOT |
|-----------|------|----------|
| Market Context Builder | Read timeframes, combine, classify, persist | Execute trades, calculate risk, detect patterns |
| Market Context Object | Carry immutable state snapshot | Mutate, influence scoring weights directly |
| Decision Engine | Read context, score, decide | Read timeframes, classify regime |

---

## 4. Folder Structure

```
core/
├── market_context/                    ← NEW PACKAGE
│   ├── __init__.py                    # Public API: build_market_context()
│   ├── builder.py                     # MarketContextBuilder class
│   ├── models.py                      # MarketContext frozen dataclass
│   ├── conflict_resolver.py           # Cross-timeframe conflict arbiter
│   ├── change_detector.py            # Material change detection
│   ├── persistence.py                 # JSONL + S3 writer
│   └── state_machine.py              # State transition logic
│
├── timeframes/                        ← EXISTING (unchanged)
│   ├── h4_regime.py                   # Still produces RegimeSnapshot
│   ├── h1_bias.py                     # Still produces BiasSnapshot
│   ├── m15_structure.py               # Still produces StructureSnapshot
│   ├── cache.py                       # Still manages fetch scheduling
│   └── types.py                       # Existing types preserved
│
├── pipeline/
│   ├── market_context.py              # DEPRECATED (replaced by builder)
│   ├── market_state_engine.py         # ABSORBED into builder
│   └── new_engine.py                  # MODIFIED: receives MarketContext
```

### Persistence Layout

```
logs/
├── market_context/
│   ├── EURUSD/
│   │   └── 2026-07-20.jsonl          # One record per material change
│   ├── GBPUSD/
│   │   └── 2026-07-20.jsonl
│   └── ...

S3:
s3://trading-bot-data-mk1/market_context/
    ├── EURUSD/
    │   └── 2026-07-20.jsonl
    └── ...
```

---

## 5. Data Model — MarketContext

```python
@dataclass(frozen=True)
class MarketContext:
    """
    Single authoritative market interpretation.
    Produced once per symbol per cycle by MarketContextBuilder.
    Consumed read-only by Decision Engine and all downstream systems.
    """

    # ─── IDENTITY ─────────────────────────────────────────────────────
    symbol: str
    cycle_id: int
    timestamp_utc: float              # Unix seconds of evaluation
    context_version: int = 1          # Schema version for evolution

    # ─── DIRECTION (unified cross-TF conclusion) ──────────────────────
    direction: Direction              # BULLISH | BEARISH | NEUTRAL
    direction_confidence: float       # 0.0–1.0 (weighted TF agreement)
    direction_source: str             # Which TF dominated: "H4" | "H1" | "CONSENSUS"

    # ─── REGIME (macro environment) ───────────────────────────────────
    regime: Regime                    # TRENDING | RANGING | TRANSITIONAL
    regime_confidence: float          # 0.0–1.0
    regime_source: str                # "H4" | "M5" | "COMBINED"

    # ─── STATE (micro execution environment) ──────────────────────────
    state: MarketState                # IMPULSE | PULLBACK | CONSOLIDATION
                                      #   | EXHAUSTION | REVERSAL
    state_confidence: float           # 0.0–1.0
    state_duration_bars: int          # How long in this state

    # ─── CONFIDENCE (overall tradability) ─────────────────────────────
    tradability_score: float          # 0.0–1.0 composite
    alignment_score: float            # 0.0–1.0 cross-TF agreement

    # ─── VOLATILITY ───────────────────────────────────────────────────
    volatility_regime: str            # "LOW" | "NORMAL" | "EXPANDING" | "EXTREME"
    atr_ratio: float                  # Current/Average ATR

    # ─── STRUCTURE (M15 level context) ────────────────────────────────
    structure_quality: float          # 0.0–1.0
    at_key_level: bool
    nearest_support: float
    nearest_resistance: float

    # ─── TIMEFRAME COMPONENTS (raw inputs for transparency) ───────────
    h4_snapshot: H4Summary            # Condensed H4 state
    h1_snapshot: H1Summary            # Condensed H1 state
    m15_snapshot: M15Summary          # Condensed M15 state
    m5_snapshot: M5Summary            # Condensed M5 state

    # ─── CONFLICT RESOLUTION ──────────────────────────────────────────
    conflict_detected: bool           # True if TFs disagree
    conflict_description: str         # Human-readable conflict
    resolution_method: str            # How conflict was resolved

    # ─── CHANGE METADATA ──────────────────────────────────────────────
    is_material_change: bool          # True if different from previous
    previous_direction: Direction | None
    previous_regime: Regime | None
    previous_state: MarketState | None
    change_reason: str                # What changed and why
```

### Supporting Enums

```python
class Direction(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"

class Regime(str, Enum):
    TRENDING = "TRENDING"
    RANGING = "RANGING"
    TRANSITIONAL = "TRANSITIONAL"

class MarketState(str, Enum):
    IMPULSE = "IMPULSE"
    PULLBACK = "PULLBACK"
    CONSOLIDATION = "CONSOLIDATION"
    EXHAUSTION = "EXHAUSTION"
    REVERSAL = "REVERSAL"
```

### Timeframe Summary Types

```python
@dataclass(frozen=True)
class H4Summary:
    regime: str               # TRENDING_BULLISH | TRENDING_BEARISH | RANGING | VOLATILE
    confidence: float
    trend_bias: str           # BULLISH | BEARISH | NEUTRAL
    trend_strength: float
    atr_ratio: float
    ema_slope: float

@dataclass(frozen=True)
class H1Summary:
    direction: str            # BULLISH | BEARISH | NEUTRAL
    confidence: float
    swing_structure: str      # HH_HL | LH_LL | MIXED
    ema_position: float

@dataclass(frozen=True)
class M15Summary:
    quality_score: float
    at_key_level: bool
    order_block_present: bool
    nearest_support: float
    nearest_resistance: float

@dataclass(frozen=True)
class M5Summary:
    regime_state: str         # TREND_UP | TREND_DOWN | RANGING
    chop_ratio: float         # net_move / sum_range
    bias_direction: str       # Current bias FSM state
    bias_strength: float
    trend_ema_position: float # Price relative to EMA-50
```

---

## 6. Market Context Builder — Class Design

```python
class MarketContextBuilder:
    """
    Single authority for producing MarketContext.

    Lifecycle:
        1. Created once per symbol at scanner init
        2. build() called once per M5 cycle
        3. Returns frozen MarketContext
        4. Persists on material change only

    Dependencies (injected):
        - TimeframeCache (provides HTFContext)
        - Candles + EngineState (provides M5 context)
        - ConflictResolver (resolves cross-TF disagreements)
        - ChangeDetector (determines material change)
        - MarketContextPersistence (writes JSONL + S3)
    """

    def __init__(
        self,
        symbol: str,
        tf_cache: TimeframeCache,
        persistence: MarketContextPersistence,
        conflict_resolver: ConflictResolver | None = None,
        change_detector: ChangeDetector | None = None,
    ) -> None: ...

    def build(
        self,
        *,
        candles: list[Candle],
        closed_i: int,
        engine_state: EngineState,
        cycle_id: int,
        current_time_s: float,
        current_price: float,
    ) -> MarketContext:
        """
        Produce one MarketContext for this cycle.

        Steps:
            1. Read HTFContext from cache (already updated by scanner)
            2. Compute M5 context from candles + engine_state
            3. Resolve cross-TF conflicts
            4. Classify unified direction, regime, state
            5. Compute confidence and tradability
            6. Detect material change vs previous
            7. Persist if material change
            8. Return frozen MarketContext
        """
        ...

    @property
    def previous_context(self) -> MarketContext | None:
        """Last produced context (for change detection)."""
        ...
```

### Conflict Resolver

```python
class ConflictResolver:
    """
    Resolves disagreements between timeframes.

    Hierarchy (higher = more authority):
        H4 > H1 > M15 > M5

    Rules:
        1. If H4 TRENDING + H1 agrees → strong directional (high confidence)
        2. If H4 TRENDING + H1 contradicts → H4 wins but confidence reduced
        3. If H4 RANGING + H1 directional → H1 provides direction, low confidence
        4. If all neutral → NEUTRAL with high confidence
        5. If 3+ timeframes agree → consensus (highest confidence)
    """

    def resolve(
        self,
        h4: H4Summary,
        h1: H1Summary,
        m15: M15Summary,
        m5: M5Summary,
    ) -> tuple[Direction, float, str]:
        """Returns (direction, confidence, resolution_method)."""
        ...
```

### Change Detector

```python
class ChangeDetector:
    """
    Determines whether a new MarketContext represents a material change.

    Material change = any of:
        - Direction changed (BULLISH → BEARISH, etc.)
        - Regime changed (TRENDING → RANGING, etc.)
        - State changed (IMPULSE → PULLBACK, etc.)
        - Confidence crossed a significance threshold (±0.2)

    NOT material:
        - Small confidence fluctuation within same classification
        - Timeframe summary value drift without state change
        - Structure score movement within same quality band
    """

    def is_material(self, current: MarketContext, previous: MarketContext | None) -> bool:
        ...

    def describe_change(self, current: MarketContext, previous: MarketContext | None) -> str:
        ...
```

---

## 7. State Transition Diagram

```
                    ┌──────────────────┐
          ┌────────│   CONSOLIDATION   │────────┐
          │        └──────────────────┘         │
          │            ▲         │              │
          │            │         │ breakout     │ breakdown
          │    fade    │         ▼              ▼
          │        ┌──────┐  ┌──────────┐  ┌──────────┐
          │        │EXHAUS│  │  IMPULSE  │  │ REVERSAL │
          │        │TION  │  │           │  │          │
          │        └──┬───┘  └─────┬────┘  └────┬─────┘
          │           │            │             │
          │           │ exhaust    │ retrace     │ settles
          │           ▼            ▼             ▼
          │        ┌──────────────────────────────┐
          └────────│         PULLBACK              │
                   └──────────────────────────────┘
                       │                    │
                       │ resumes            │ fails
                       ▼                    ▼
                   ┌──────────┐      ┌──────────────┐
                   │  IMPULSE │      │ CONSOLIDATION │
                   └──────────┘      └──────────────┘
```

### State Transition Rules

| From | To | Condition |
|------|----|-----------|
| CONSOLIDATION | IMPULSE | Breakout: price clears range + momentum spike |
| CONSOLIDATION | REVERSAL | Breakdown: sharp directional move against prior trend |
| IMPULSE | PULLBACK | Retrace: price pulls back 38-62% of impulse |
| IMPULSE | EXHAUSTION | Wick rejection + momentum divergence |
| PULLBACK | IMPULSE | Resumes: price continues original direction |
| PULLBACK | CONSOLIDATION | Fails: no continuation after N bars |
| EXHAUSTION | CONSOLIDATION | Settles: volatility contracts |
| EXHAUSTION | REVERSAL | Continues: opposite direction impulse |
| REVERSAL | IMPULSE | Confirms: new direction established |
| REVERSAL | CONSOLIDATION | Fails: no follow-through |

### Regime Transition Rules

| From | To | Condition |
|------|----|-----------|
| TRENDING | RANGING | EMA slope flattens + range compression |
| TRENDING | TRANSITIONAL | Conflicting signals (momentum loss but no compression) |
| RANGING | TRENDING | Breakout + directional structure forms |
| RANGING | TRANSITIONAL | Expansion without clear direction |
| TRANSITIONAL | TRENDING | Direction resolves (2+ TFs agree) |
| TRANSITIONAL | RANGING | Compression resumes |

---

## 8. Context Lifecycle

```
Scanner Cycle Start
     │
     ├─ 1. TimeframeCache.update_if_needed()     ← existing, unchanged
     │      (refreshes H4/H1/M15 if new bar closed)
     │
     ├─ 2. MarketContextBuilder.build()           ← NEW
     │      │
     │      ├─ 2a. Read HTFContext from cache
     │      ├─ 2b. Compute M5 metrics from candles
     │      ├─ 2c. Resolve cross-TF conflicts
     │      ├─ 2d. Classify direction/regime/state
     │      ├─ 2e. Detect material change
     │      ├─ 2f. Persist if material change
     │      └─ 2g. Return frozen MarketContext
     │
     ├─ 3. run_new_engine(market_context=ctx)     ← MODIFIED signature
     │      │
     │      ├─ Reads ctx.direction, ctx.regime
     │      ├─ Reads ctx.tradability_score
     │      ├─ Reads ctx.alignment_score
     │      ├─ No longer calls _score_htf() or _score_h4() internally
     │      └─ Uses ctx values directly in component scoring
     │
     ├─ 4. OpportunityAssessment constructed
     │      └─ Includes market_context reference
     │
     ├─ 5. DecisionTrace constructed
     │      └─ Includes ctx.direction, ctx.regime, ctx.state
     │
     └─ 6. Cycle ends
            └─ MarketContext discarded (or retained for next change detection)
```

### Object Lifetime

| Object | Created | Lives Until | Mutated? |
|--------|---------|-------------|----------|
| TimeframeCache | Scanner init | Scanner shutdown | Yes (internal) |
| MarketContextBuilder | Scanner init | Scanner shutdown | Yes (tracks previous) |
| MarketContext | Each cycle | End of cycle | Never (frozen) |
| HTFContext | Each cycle | End of cycle | Never (frozen) |

---

## 9. Persistence Strategy

### Write Policy

- **Write on material change only** — not every cycle
- Expected write frequency: 1-5 per hour per symbol (regime/state changes)
- This avoids 12 writes/hour/symbol (one per M5 bar) with identical content

### Local Persistence

```
Path: logs/market_context/{SYMBOL}/{YYYY-MM-DD}.jsonl
Format: One JSON object per line (append-only)
```

### S3 Mirror

```
Path: s3://trading-bot-data-mk1/market_context/{SYMBOL}/{YYYY-MM-DD}.jsonl
Gate: EVENT_STREAM_S3_MIRROR config flag (existing pattern)
Method: Read-append-write (same as research_shadow_engine.py)
```

### JSONL Record Schema

```json
{
  "context_version": 1,
  "symbol": "EURUSD",
  "cycle_id": 1234,
  "timestamp_utc": 1784562000.0,
  "direction": "BULLISH",
  "direction_confidence": 0.72,
  "direction_source": "CONSENSUS",
  "regime": "TRENDING",
  "regime_confidence": 0.85,
  "regime_source": "H4",
  "state": "IMPULSE",
  "state_confidence": 0.68,
  "state_duration_bars": 5,
  "tradability_score": 0.78,
  "alignment_score": 0.82,
  "volatility_regime": "NORMAL",
  "atr_ratio": 1.1,
  "structure_quality": 0.65,
  "at_key_level": false,
  "conflict_detected": false,
  "conflict_description": "",
  "resolution_method": "CONSENSUS",
  "is_material_change": true,
  "change_reason": "direction: NEUTRAL → BULLISH",
  "h4": {
    "regime": "TRENDING_BULLISH",
    "confidence": 0.8,
    "trend_bias": "BULLISH",
    "trend_strength": 0.7
  },
  "h1": {
    "direction": "BULLISH",
    "confidence": 0.65,
    "swing_structure": "HH_HL"
  },
  "m15": {
    "quality_score": 0.6,
    "at_key_level": false,
    "order_block_present": true
  },
  "m5": {
    "regime_state": "TREND_UP",
    "chop_ratio": 0.55,
    "bias_direction": "BUY",
    "bias_strength": 45.0
  }
}
```

### Athena Table DDL

```sql
CREATE EXTERNAL TABLE IF NOT EXISTS trading_bot.market_context (
    context_version INT,
    symbol STRING,
    cycle_id BIGINT,
    timestamp_utc DOUBLE,
    direction STRING,
    direction_confidence DOUBLE,
    direction_source STRING,
    regime STRING,
    regime_confidence DOUBLE,
    regime_source STRING,
    state STRING,
    state_confidence DOUBLE,
    state_duration_bars INT,
    tradability_score DOUBLE,
    alignment_score DOUBLE,
    volatility_regime STRING,
    atr_ratio DOUBLE,
    structure_quality DOUBLE,
    at_key_level BOOLEAN,
    conflict_detected BOOLEAN,
    conflict_description STRING,
    resolution_method STRING,
    is_material_change BOOLEAN,
    change_reason STRING,
    h4 STRUCT<
        regime: STRING,
        confidence: DOUBLE,
        trend_bias: STRING,
        trend_strength: DOUBLE
    >,
    h1 STRUCT<
        direction: STRING,
        confidence: DOUBLE,
        swing_structure: STRING
    >,
    m15 STRUCT<
        quality_score: DOUBLE,
        at_key_level: BOOLEAN,
        order_block_present: BOOLEAN
    >,
    m5 STRUCT<
        regime_state: STRING,
        chop_ratio: DOUBLE,
        bias_direction: STRING,
        bias_strength: DOUBLE
    >
)
ROW FORMAT SERDE 'org.openx.data.jsonserde.JsonSerDe'
WITH SERDEPROPERTIES (
    'ignore.malformed.json' = 'true',
    'case.insensitive' = 'true'
)
STORED AS INPUTFORMAT 'org.apache.hadoop.mapred.TextInputFormat'
OUTPUTFORMAT 'org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat'
LOCATION 's3://trading-bot-data-mk1/market_context/'
TBLPROPERTIES ('has_encrypted_data'='false');
```

---

## 10. Integration Points

### 10.1 Events System

```
Current: market_context.py emits RISK_CHECK events on chop filter veto
New:     MarketContextBuilder emits MARKET_CONTEXT_CHANGE event on material change

Event payload:
{
    "type": "MARKET_CONTEXT_CHANGE",
    "symbol": "EURUSD",
    "direction": "BULLISH",
    "regime": "TRENDING",
    "state": "IMPULSE",
    "previous_direction": "NEUTRAL",
    "previous_regime": "TRANSITIONAL",
    "change_reason": "H4 trending confirmed + H1 bias aligned"
}
```

The chop filter veto (RISK_CHECK event) is preserved but emitted by the builder
rather than the pipeline step.

### 10.2 Decision Trace

```
Current DecisionTrace fields:
    regime, regime_confidence, market_state, market_state_confidence,
    htf_alignment, h4_alignment

New DecisionTrace fields:
    market_context_direction, market_context_regime, market_context_state,
    market_context_confidence, market_context_alignment,
    market_context_conflict_detected
```

The DecisionTrace gains richer context without needing to recompute anything —
it reads directly from the MarketContext object.

### 10.3 Decision Audit

The decision audit currently captures regime + market_state + htf context.
After migration, it captures the full `MarketContext.to_dict()` as a nested
field, giving Athena full access to the unified interpretation.

### 10.4 Opportunity Assessment

```python
# Current: OpportunityAssessment computed INSIDE new_engine.py
# New: OpportunityAssessment receives MarketContext as a construction input

@dataclass(frozen=True)
class OpportunityAssessment:
    # ... existing fields ...

    # NEW: replaces scattered htf_alignment, h4_alignment, market_state fields
    market_context: MarketContext  # Full unified context snapshot
```

This replaces the current pattern where OpportunityAssessment redundantly
stores `market_state`, `htf_alignment`, `h4_alignment`, `regime`,
`regime_confidence` as separate fields — they all now live in one place.

### 10.5 Research Shadow Trades

```
Current: Research shadow trades record htf_snapshot (nullable)
New:     Research shadow trades record market_context summary

decision_snapshot field gains:
    "market_context": {
        "direction": "BULLISH",
        "regime": "TRENDING",
        "state": "PULLBACK",
        "tradability_score": 0.72,
        "conflict_detected": false
    }
```

This enables Athena queries like:
```sql
SELECT
    decision_snapshot.market_context.regime,
    AVG(simulated_outcome.pnl_r_multiple) AS avg_r
FROM research_shadow_trades
GROUP BY decision_snapshot.market_context.regime
```

### 10.6 Decision Engine (new_engine.py)

```python
# Current signature:
def run_new_engine(*, ..., htf_context: Any = None, ...) -> dict:

# New signature:
def run_new_engine(*, ..., market_context: MarketContext, ...) -> dict:
```

The engine no longer:
- Calls `_score_htf()` or `_score_h4()` with raw HTFContext
- Reads regime from strategy_activation separately
- Computes market_state_engine.evaluate() inline

Instead, it reads pre-computed values from MarketContext:
- `market_context.direction` → replaces _score_htf bias logic
- `market_context.regime` → replaces strategy_activation regime
- `market_context.tradability_score` → replaces MarketStateEngine inline call
- `market_context.h4_snapshot` → replaces _score_h4 raw regime reading
- `market_context.alignment_score` → replaces ad-hoc agreement calculations

---

## 11. Migration Plan

### Phase 0: Shadow Mode (No Behaviour Change)

1. Create `core/market_context/` package
2. Implement `MarketContextBuilder` that produces `MarketContext`
3. Wire into `live_scanner.py` AFTER existing HTF context build
4. Log MarketContext alongside existing flow (dual-write)
5. **Do not modify new_engine.py**
6. Validate: MarketContext output matches existing scattered interpretations

Verification:
- Compare `ctx.direction` vs what `_score_htf()` would infer
- Compare `ctx.regime` vs `strategy_activation.regime`
- Compare `ctx.tradability_score` vs `MarketStateEngine.evaluate().state`
- Zero-diff on all existing decision outcomes

### Phase 1: Persistence Layer

1. Implement `persistence.py` (JSONL + S3 mirror)
2. Implement `change_detector.py`
3. Start persisting MarketContext on material changes
4. Create Athena table
5. **No engine changes yet**

Verification:
- Context records appear in `logs/market_context/`
- S3 mirror works when `EVENT_STREAM_S3_MIRROR=True`
- Athena queries return valid data

### Phase 2: Engine Integration

1. Add `market_context: MarketContext` parameter to `run_new_engine()`
2. Replace internal `_score_htf()` logic with `ctx.h1_snapshot` + `ctx.m15_snapshot` reads
3. Replace internal `_score_h4()` logic with `ctx.h4_snapshot` reads
4. Replace inline `MarketStateEngine.evaluate()` with `ctx.tradability_score`
5. Update `OpportunityAssessment` to include MarketContext reference

Verification:
- Run full replay: decisions must be IDENTICAL to Phase 0 baseline
- Score components must match within ε=0.001
- Zero new trade executions or blocked trades

### Phase 3: Deprecation

1. Mark `core/pipeline/market_context.py` as deprecated
2. Mark `core/pipeline/market_state_engine.py` as deprecated
3. Remove `core/timeframes/integration.py` (dead code)
4. Remove duplicate EngineState.regime_state writes (now owned by builder)
5. Clean up unused imports

### Phase 4: Enhancement

1. Activate `conflict_resolver.py` (currently H4 just wins by weight)
2. Activate `state_machine.py` (IMPULSE/PULLBACK/etc classification)
3. Add state_duration tracking
4. Enable context-aware scoring weight selection (replace _GLOBAL_WEIGHTS)
5. Feed MarketContext into research shadow trade decisions

---

## 12. Class Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                         live_scanner.py                              │
│                                                                     │
│  Per symbol per cycle:                                              │
│  ┌────────────────┐     ┌──────────────────────┐                   │
│  │ TimeframeCache │────▶│ MarketContextBuilder  │                   │
│  │ (existing)     │     │ (new)                 │                   │
│  └────────────────┘     └──────────┬───────────┘                   │
│                                    │                                │
│                                    ▼                                │
│                          ┌─────────────────┐                        │
│                          │  MarketContext   │──────┐                │
│                          │  (frozen)        │      │                │
│                          └────────┬────────┘      │                │
│                                   │               │                │
│              ┌────────────────────┼───────────┐   │                │
│              ▼                    ▼            ▼   ▼                │
│  ┌───────────────────┐ ┌──────────────┐ ┌────────────────────┐    │
│  │ run_new_engine()  │ │DecisionTrace │ │ Persistence        │    │
│  │ (scoring+policy)  │ │              │ │ (JSONL + S3)       │    │
│  └───────────────────┘ └──────────────┘ └────────────────────┘    │
└─────────────────────────────────────────────────────────────────────┘
```

### Dependency Graph

```
core/market_context/models.py          ← Pure data (no imports from core)
         ▲
         │
core/market_context/conflict_resolver.py  ← Imports models only
core/market_context/change_detector.py    ← Imports models only
core/market_context/state_machine.py      ← Imports models only
         ▲
         │
core/market_context/builder.py         ← Imports above + TimeframeCache + types
         ▲
         │
core/market_context/persistence.py     ← Imports models + config + boto3
         ▲
         │
core/market_context/__init__.py        ← Public API (build_market_context)
         ▲
         │
core/runtime/live_scanner.py           ← Calls build_market_context()
core/pipeline/new_engine.py            ← Receives MarketContext as parameter
```

### Key Constraint: No Circular Dependencies

```
TimeframeCache → (reads MT5)
     │
     ▼
MarketContextBuilder → (reads cache, produces MarketContext)
     │
     ▼
new_engine.py → (reads MarketContext, produces decision)
     │
     ▼
DecisionTrace / OpportunityAssessment → (records context)
```

Each layer depends only on the layer above it. No upstream imports.

---

## 13. Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| Behaviour change during migration | HIGH | Shadow mode first (Phase 0); replay comparison |
| Scoring drift from rounding | LOW | Assert ε < 0.001 on all component scores |
| Performance regression (extra computation) | LOW | Builder reuses cached HTFContext; no new MT5 calls |
| Conflict resolver makes wrong calls | MEDIUM | Phase 0-2 use simple hierarchy (H4 > H1 > M15); resolver activated in Phase 4 only |
| State machine misclassifies | MEDIUM | Phase 0-2 map existing regime_state; FSM activated in Phase 4 |
| Persistence volume too high | LOW | Material-change-only writes; expected 1-5/hr/symbol |
| S3 append contention | LOW | Same read-append-write as research_shadow_engine (proven pattern) |
| EngineState mutation removed too early | HIGH | Phase 0-1 preserve ALL existing mutations; Phase 3 removes them after verified redundancy |
| Breaking OpportunityAssessment consumers | MEDIUM | Add market_context field ALONGSIDE existing fields; deprecate old fields in Phase 3 |

---

## 14. Benefits

1. **Single source of truth** — One place answers "what is the market doing?"
2. **Testable in isolation** — MarketContextBuilder can be unit tested with mock HTFContext
3. **Athena-queryable** — Market context becomes a first-class research dataset
4. **Decoupled scoring** — new_engine.py no longer embeds market reading logic
5. **Conflict resolution** — Cross-TF disagreements get explicit handling
6. **Reduced complexity** — 5 scattered interpretation points → 1 builder
7. **Change detection** — Only persists when something actually changed
8. **State machine clarity** — IMPULSE/PULLBACK/etc replaces vague "TRANSITIONAL"
9. **Reusable service** — Future strategies (B, C) consume the same MarketContext
10. **Observability** — Discord alerts on regime transitions become trivial

---

## 15. Future Extensions

### 15.1 Context-Aware Weight Selection

Instead of hardcoded `_GLOBAL_WEIGHTS`, the engine selects weights based on
`market_context.state`:

```python
WEIGHT_PROFILES = {
    MarketState.IMPULSE: {...},       # Momentum-heavy
    MarketState.PULLBACK: {...},      # Mean-reversion-heavy
    MarketState.CONSOLIDATION: {...}, # Range-play weights
}
```

### 15.2 Regime-Gated Strategies

MarketContext enables strategies that only activate in specific regimes:
- "Only trade pullbacks in TRENDING regime"
- "Only trade breakouts from CONSOLIDATION state"
- "Never trade during EXHAUSTION"

### 15.3 Context Diffing for Research

Compare market context at decision time vs context at outcome time:
```sql
SELECT
    decision_context.regime AS regime_at_entry,
    outcome_context.regime AS regime_at_exit,
    AVG(pnl_r_multiple)
FROM decisions d
JOIN market_context dc ON d.entry_cycle = dc.cycle_id
JOIN market_context oc ON d.exit_cycle = oc.cycle_id
GROUP BY 1, 2
```

### 15.4 Multi-Strategy Context Routing

MarketContext becomes the router for which strategy engine evaluates:
- TRENDING + IMPULSE → Continuation engine
- RANGING + CONSOLIDATION → Range engine
- TRENDING + PULLBACK → Pullback engine

### 15.5 Discord Notifications on Context Change

```python
if ctx.is_material_change:
    log_router.emit("market-context", f"{symbol} → {ctx.regime} {ctx.direction}")
```

---

## 16. Constraints

1. **Preserve Engine A architecture** — The decision pipeline sequence is unchanged
2. **No circular dependencies** — MarketContext depends only on timeframe layer
3. **Minimal coupling** — Builder injected via constructor, not imported globally
4. **Maintain observability** — All existing event types continue to emit
5. **Support Athena research** — Schema designed for flat JSON queries
6. **Reusable service** — No coupling to specific strategy or pattern logic
7. **Never crash production** — All builder code wrapped in try/except
8. **No trading logic** — Builder NEVER decides to trade or not trade
9. **Frozen output** — MarketContext is immutable after construction
10. **Backward compatible** — Phase 0-1 add new code without changing existing behaviour

---

## 17. Design Decisions

### Why not extend HTFContext?

`HTFContext` is a thin container for raw analyzer outputs. MarketContext is an
**interpretation** — it adds conflict resolution, state classification, and
change detection. These are different responsibilities.

### Why not put this in new_engine.py?

The engine's job is to decide whether to trade. Market understanding is a
prerequisite, not part of the decision logic. Separating them means we can
test market reading independently and reuse it across multiple engines.

### Why persist only on material change?

Writing identical context every 5 minutes (12x/hour) produces noise in Athena
and wastes S3 storage. Material-change-only gives a clean state changelog.

### Why frozen dataclass?

Downstream consumers (engine, trace, audit) must not modify the context.
Freezing prevents accidental mutation and makes the contract explicit.

### Why separate conflict_resolver?

Conflict resolution logic will evolve as we learn more about cross-TF
agreement patterns. Isolating it means we can A/B test different resolvers
without touching the builder.

### Why include M5 context in MarketContext?

The M5 regime state (from market_context.py and bias_fsm.py) is currently
scattered across EngineState mutations. Including it in MarketContext gives
one object that represents ALL timeframe understanding, not just HTF.

---

## 18. Summary

The Market Context Layer consolidates scattered market interpretation into a
single authoritative service that:

- Reads all timeframes (existing infrastructure)
- Resolves conflicts (new)
- Classifies unified direction + regime + state (new)
- Persists on material change (new)
- Provides immutable context to the decision engine (replaces scattered reads)

Implementation follows a 5-phase migration plan with shadow mode validation
ensuring zero behaviour change until explicitly activated.

---

*Document produced: 2026-07-20*
*Status: Architecture Design — Ready for Review*
*Implementation: NOT started*
