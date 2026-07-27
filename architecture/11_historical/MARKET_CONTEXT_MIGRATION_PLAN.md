# Market Context Layer — Detailed Migration Plan

**Generated:** 2026-07-20
**Status:** Analysis Only — No Code Modified
**Purpose:** Every file, class, function, schema, and dependency that must change

---

## 1. New Files to Create

| # | File Path | Contents | Dependencies |
|---|-----------|----------|-------------|
| 1 | `core/market_context/__init__.py` | Public API: `build_market_context()`, re-exports | models, builder |
| 2 | `core/market_context/models.py` | `MarketContext` frozen dataclass + enums | None (pure data) |
| 3 | `core/market_context/builder.py` | `MarketContextBuilder` class | models, core/timeframes/types |
| 4 | `core/market_context/change_detector.py` | `ChangeDetector` class | models |
| 5 | `core/market_context/persistence.py` | JSONL + S3 writer | models, core/config, boto3 |
| 6 | `core/market_context/conflict_resolver.py` | Cross-TF resolution logic | models |
| 7 | `core/market_context/state_machine.py` | Phase transition logic | models |

**Total new files:** 7

---

## 2. Existing Files to Modify

### 2.1 Phase 1 Modifications (Shadow Mode — No Behaviour Change)

| # | File | Current Location | Change Required | Compatibility |
|---|------|-----------------|-----------------|---------------|
| 1 | `core/config.py` | Lines 65–67 (feature flags area) | ADD 3 new flags | Additive only |
| 2 | `core/runtime/live_scanner.py` | Lines 345–365 (HTF context build + engine call) | ADD MarketContext build between cache and engine call | Existing flow unchanged |
| 3 | `core/runtime/scanner_init.py` | Line ~136 (TimeframeCache creation) | ADD MarketContextBuilder creation alongside tf_cache | Additive |
| 4 | `core/runtime/live_scanner.py` `_LiveSymbolState` | Line ~85 | ADD `market_context_builder` field | Additive |
| 5 | `core/pipeline/observers.py` `ObserverContext` | Line ~42 | ADD `market_context` optional field | Additive, None default |

### 2.2 Phase 2 Modifications (Shadow Comparison)

| # | File | Change | Compatibility |
|---|------|--------|---------------|
| 6 | `core/decision_trace.py` `DecisionTrace` | ADD fields: `h1_phase`, `m15_setup_quality`, `context_direction`, `market_context_id` | Additive — existing fields unchanged |
| 7 | `core/decision_trace.py` `build_decision_trace()` | ADD market_context parameter, populate new fields | Optional param with None default |
| 8 | `core/decision_trace.py` `persist_decision_trace()` | Include new fields in JSONL output | Backward compatible (new fields added to end) |

### 2.3 Phase 3 Modifications (Engine Integration — Feature Flagged)

| # | File | Change | Compatibility |
|---|------|--------|---------------|
| 9 | `core/pipeline/new_engine.py` `run_new_engine()` | ADD `market_context` parameter (default None) | Backward compatible — None = use existing inline logic |
| 10 | `core/pipeline/new_engine.py` `_compute_all_scores()` | ADD `market_context` parameter (default None). When populated, read scores from context instead of computing inline | Feature-flagged: if None, existing logic unchanged |
| 11 | `core/pipeline/new_engine.py` `_score_htf()` | MODIFY to optionally read from MarketContext | When market_context=None, existing inline computation unchanged |
| 12 | `core/pipeline/new_engine.py` `_score_h4()` | MODIFY to optionally read from MarketContext | Same pattern |
| 13 | `core/models/opportunity_assessment.py` | ADD fields: `h1_phase`, `m15_setup_confidence`, `market_context_ref` | All optional with defaults |
| 14 | `core/runtime/live_scanner.py` engine call | PASS `market_context=` to `run_new_engine()` when flag enabled | Conditional on feature flag |
| 15 | `core/runtime/engine_execution_handler.py` `prepare_execution()` | Accept optional `market_context` for snapshot | Backward compatible |
| 16 | `core/research_assessment/research_shadow_engine.py` `_persist_research_trade()` | ADD `market_context` summary field to record | Additive — existing fields unchanged |

---

## 3. Function Signature Changes

### 3.1 New Function Signatures (core/market_context/)

```python
# core/market_context/__init__.py
def build_market_context(
    *,
    htf_context: HTFContext | None,
    candles: list[Candle],
    closed_i: int,
    engine_state: Any,
    symbol: str,
    cycle_id: int,
    current_time_s: float,
    current_price: float,
) -> MarketContext: ...

# core/market_context/builder.py
class MarketContextBuilder:
    def __init__(self, symbol: str, persistence: MarketContextPersistence | None = None) -> None: ...
    def build(self, *, htf_context, candles, closed_i, engine_state, cycle_id, current_time_s, current_price) -> MarketContext: ...
    @property
    def previous_context(self) -> MarketContext | None: ...

# core/market_context/change_detector.py
class ChangeDetector:
    def is_material(self, current: MarketContext, previous: MarketContext | None) -> bool: ...
    def describe_change(self, current: MarketContext, previous: MarketContext | None) -> str: ...

# core/market_context/persistence.py
class MarketContextPersistence:
    def persist(self, context: MarketContext) -> None: ...
```

### 3.2 Modified Function Signatures (Existing Files)

```python
# CURRENT: core/pipeline/new_engine.py
def run_new_engine(
    *, candles, closed_i, symbol, bid, ask, engine_state, config,
    detected_patterns, risk_manager, htf_context=None, cycle_id=0,
) -> dict[str, Any]: ...

# NEW: core/pipeline/new_engine.py (Phase 3)
def run_new_engine(
    *, candles, closed_i, symbol, bid, ask, engine_state, config,
    detected_patterns, risk_manager, htf_context=None, cycle_id=0,
    market_context=None,  # ← NEW optional parameter
) -> dict[str, Any]: ...


# CURRENT: core/pipeline/new_engine.py
def _compute_all_scores(
    *, candles, closed_i, best_pattern, engine_state, config, htf_context=None,
) -> dict[str, float]: ...

# NEW: core/pipeline/new_engine.py (Phase 3)
def _compute_all_scores(
    *, candles, closed_i, best_pattern, engine_state, config, htf_context=None,
    market_context=None,  # ← NEW optional parameter
) -> dict[str, float]: ...


# CURRENT: core/decision_trace.py
def build_decision_trace(
    *, engine_result: dict, runtime_session_id: str = "", pattern_count: int = 0,
) -> DecisionTrace: ...

# NEW: core/decision_trace.py (Phase 2)
def build_decision_trace(
    *, engine_result: dict, runtime_session_id: str = "", pattern_count: int = 0,
    market_context: "MarketContext | None" = None,  # ← NEW optional parameter
) -> DecisionTrace: ...


# CURRENT: core/runtime/engine_execution_handler.py
def prepare_execution(
    *, new_result, new_engine_score, new_engine_htf, sym_state, cycle_id,
    closed_time, candles, closed_i, bid, ask, tick_time, feed_state,
    cycle_start, dd_result, dl_result, runtime_session_id, config,
) -> ExecutionPrep: ...

# NEW: core/runtime/engine_execution_handler.py (Phase 3)
def prepare_execution(
    *, new_result, new_engine_score, new_engine_htf, sym_state, cycle_id,
    closed_time, candles, closed_i, bid, ask, tick_time, feed_state,
    cycle_start, dd_result, dl_result, runtime_session_id, config,
    market_context=None,  # ← NEW optional parameter
) -> ExecutionPrep: ...
```

---

## 4. Class/Schema Changes

### 4.1 New Classes

```python
# core/market_context/models.py

class Direction(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"

class Regime(str, Enum):
    TRENDING = "TRENDING"
    RANGING = "RANGING"
    TRANSITIONAL = "TRANSITIONAL"

class Phase(str, Enum):
    IMPULSE = "IMPULSE"
    PULLBACK = "PULLBACK"
    CONSOLIDATION = "CONSOLIDATION"
    EXHAUSTION = "EXHAUSTION"
    REVERSAL = "REVERSAL"

@dataclass(frozen=True)
class MarketContext:
    # Identity
    symbol: str
    cycle_id: int
    timestamp_utc: float
    context_version: int = 1

    # Unified interpretation
    direction: Direction
    direction_confidence: float
    regime: Regime
    regime_confidence: float
    phase: Phase
    phase_confidence: float

    # Tradability
    tradability_score: float
    alignment_score: float

    # H4 Structure summary
    h4_regime: str
    h4_trend_bias: str
    h4_trend_strength: float
    h4_atr_ratio: float

    # H1 Phase summary
    h1_direction: str
    h1_confidence: float
    h1_swing_structure: str
    h1_bos_confirmed: bool

    # M15 Setup summary
    m15_quality: float
    m15_at_key_level: bool
    m15_order_block: bool

    # M5 State summary
    m5_regime_state: str
    m5_bias_phase: str
    m5_bias_strength: float

    # Change metadata
    is_material_change: bool
    change_reason: str

    def to_dict(self) -> dict[str, Any]: ...
    def to_summary(self) -> dict[str, Any]: ...  # Compact version for embedding
```

### 4.2 Modified Classes

```python
# core/runtime/live_scanner.py — _LiveSymbolState (ADD field)
@dataclass
class _LiveSymbolState:
    symbol: str
    feed: MT5DataFeed
    engine_state: EngineState
    event_state: EventState
    risk: RiskManager
    trade_manager: TradeStateManager | None
    stale_monitor: StaleDataMonitor
    tf_cache: "TimeframeCache | None" = None
    market_context_builder: Any = None  # ← NEW (Phase 1)
    last_closed_time: int | None = None
    iterations: int = 0


# core/pipeline/observers.py — ObserverContext (ADD field)
@dataclass
class ObserverContext:
    symbol: str
    cycle_id: int
    bar_time: float
    engine_result: dict[str, Any]
    engine_state: Any
    candles: Any
    closed_i: int
    bid: float
    ask: float
    config: Any
    detected_patterns: Any
    risk_manager: Any
    htf_context: Any
    runtime_session_id: str
    decision_funnel: Any
    market_context: Any = None  # ← NEW (Phase 1)


# core/decision_trace.py — DecisionTrace (ADD fields)
@dataclass(frozen=True)
class DecisionTrace:
    # ... existing fields unchanged ...

    # NEW fields (Phase 2) — all optional with defaults
    h1_phase: str | None = None
    m15_setup_quality: float | None = None
    context_direction: str | None = None
    context_regime: str | None = None
    market_context_id: str | None = None


# core/models/opportunity_assessment.py — OpportunityAssessment (ADD fields)
@dataclass(frozen=True)
class OpportunityAssessment:
    # ... existing 26+ fields unchanged ...

    # NEW fields (Phase 3) — all optional with defaults
    h1_phase: str | None = None
    m15_setup_confidence: float | None = None
    market_context_ref: str | None = None  # Serialized context ID for linkage
```

### 4.3 Config Additions

```python
# core/config.py — NEW flags (Phase 1)

# --- Market Context Layer ---
MARKET_CONTEXT_ENABLED = True           # When True, MarketContextBuilder runs per cycle
MARKET_CONTEXT_SCORING_ENABLED = False  # When True, engine reads scores from MarketContext (Phase 3)
MARKET_CONTEXT_PERSISTENCE_ENABLED = True  # When True, persists on material change
```

---

## 5. Dependency Graph — What Imports What

### 5.1 New Package Dependencies

```
core/market_context/models.py
    └── imports: None (pure data — enums + frozen dataclass)

core/market_context/conflict_resolver.py
    └── imports: core.market_context.models

core/market_context/change_detector.py
    └── imports: core.market_context.models

core/market_context/state_machine.py
    └── imports: core.market_context.models

core/market_context/persistence.py
    └── imports: core.market_context.models, core.config, os, json, boto3 (lazy)

core/market_context/builder.py
    └── imports: core.market_context.models
    └── imports: core.market_context.conflict_resolver
    └── imports: core.market_context.change_detector
    └── imports: core.market_context.persistence
    └── imports: core.market_context.state_machine
    └── imports: core.timeframes.types (HTFContext, RegimeSnapshot, BiasSnapshot, StructureSnapshot)
    └── imports: data.mt5_data (Candle)

core/market_context/__init__.py
    └── imports: core.market_context.builder
    └── imports: core.market_context.models
```

### 5.2 Existing Files — New Import Requirements

| File | New Import Required | Phase |
|------|-------------------|-------|
| `core/runtime/live_scanner.py` | `from core.market_context import build_market_context` (lazy, inside try) | 1 |
| `core/runtime/scanner_init.py` | `from core.market_context.builder import MarketContextBuilder` (conditional on flag) | 1 |
| `core/pipeline/observers.py` | None (field typed as `Any`) | 1 |
| `core/decision_trace.py` | None (new fields use str/float/None — no type import needed) | 2 |
| `core/pipeline/new_engine.py` | None in Phase 1-2. Phase 3: conditional `from core.market_context.models import MarketContext` (lazy) | 3 |
| `core/models/opportunity_assessment.py` | None (new fields are str/float/None) | 3 |
| `core/runtime/engine_execution_handler.py` | None (market_context param typed as `Any`) | 3 |
| `core/config.py` | None (new flags are simple constants) | 1 |

### 5.3 Forbidden Dependencies (Enforced)

```
core/market_context/ MUST NOT import:
    ├── core/pipeline/new_engine.py       (circular — engine consumes context)
    ├── core/pipeline/execution_policy.py  (policy consumes context)
    ├── core/pipeline/expected_value.py    (EV consumes context)
    ├── core/runtime/live_scanner.py       (orchestrator consumes context)
    ├── risk/                              (risk layer is independent)
    ├── execution/                         (execution is downstream)
    └── strategy/signal_orchestrator.py    (patterns are independent)

core/market_context/ MAY import:
    ├── core/timeframes/types.py           (reads HTFContext, snapshots)
    ├── core/config.py                     (reads flags)
    ├── core/clock.py                      (timestamp utilities)
    ├── data/mt5_data.py                   (Candle type only)
    └── os, json, logging, boto3           (infrastructure)
```

---

## 6. Complete File Impact Registry

### 6.1 Files Modified (by Phase)

| # | File | Phase | Change Type | Lines Affected (est.) | Risk |
|---|------|-------|-------------|----------------------|------|
| 1 | `core/config.py` | 1 | ADD 3 flags | +5 lines | None |
| 2 | `core/runtime/scanner_init.py` | 1 | ADD builder creation (conditional) | +12 lines | None |
| 3 | `core/runtime/live_scanner.py` `_LiveSymbolState` | 1 | ADD field | +1 line | None |
| 4 | `core/runtime/live_scanner.py` (engine call area) | 1 | ADD context build step between cache and engine | +15 lines | None |
| 5 | `core/pipeline/observers.py` `ObserverContext` | 1 | ADD field | +1 line | None |
| 6 | `core/decision_trace.py` `DecisionTrace` | 2 | ADD 5 fields | +5 lines |  None |
| 7 | `core/decision_trace.py` `build_decision_trace()` | 2 | ADD param + populate new fields | +10 lines | None |
| 8 | `core/decision_trace.py` serialization | 2 | ADD new fields to JSONL dict | +5 lines | None |
| 9 | `core/pipeline/new_engine.py` `run_new_engine()` | 3 | ADD param `market_context=None` | +1 line | None (default None) |
| 10 | `core/pipeline/new_engine.py` `_compute_all_scores()` | 3 | ADD param + conditional reads | +20 lines | Medium (feature-flagged) |
| 11 | `core/pipeline/new_engine.py` `_score_htf()` | 3 | ADD conditional market_context path | +8 lines | Medium (feature-flagged) |
| 12 | `core/pipeline/new_engine.py` `_score_h4()` | 3 | ADD conditional market_context path | +8 lines | Medium (feature-flagged) |
| 13 | `core/models/opportunity_assessment.py` | 3 | ADD 3 optional fields | +3 lines | None |
| 14 | `core/models/opportunity_assessment.py` `to_dict()` | 3 | ADD new fields to output | +3 lines | None |
| 15 | `core/runtime/live_scanner.py` (engine call) | 3 | PASS market_context param | +1 line | None |
| 16 | `core/runtime/engine_execution_handler.py` | 3 | ADD param `market_context=None` | +2 lines | None |
| 17 | `core/research_assessment/research_shadow_engine.py` | 3 | ADD market_context to record | +5 lines | None |

### 6.2 Files NOT Modified (Explicitly Preserved)

| File | Reason |
|------|--------|
| `core/timeframes/h4_regime.py` | Analyzer unchanged — output consumed by builder |
| `core/timeframes/h1_bias.py` | Analyzer unchanged — output consumed by builder |
| `core/timeframes/m15_structure.py` | Analyzer unchanged — output consumed by builder |
| `core/timeframes/cache.py` | Cache unchanged — still provides HTFContext |
| `core/timeframes/types.py` | Types unchanged — still the contract |
| `core/pipeline/execution_policy.py` | Policy unchanged — consumes MarketStateResult as before |
| `core/pipeline/expected_value.py` | EV unchanged — same inputs |
| `core/pipeline/market_state_engine.py` | Unchanged — still computes execution stability |
| `core/pipeline/bias_fsm.py` | Unchanged in Phase 1-2. Phase 3: reads H1 direction |
| `core/pipeline/swing_context.py` | Unchanged in Phase 1-3. Phase 4: deprecated |
| `core/pipeline/strategy_activation.py` | Unchanged in Phase 1-2. Phase 3: reads MarketContext regime |
| `risk/` (entire package) | Independent of market context |
| `execution/` (entire package) | Independent of market context |
| `strategy/signal_orchestrator.py` | Pattern detection independent |
| `core/event_stream.py` | Strict allowlist preserved |
| `core/decision_ledger.py` | Existing schema preserved |
| `core/decision_audit.py` | Existing schema preserved |

---

## 7. Schema Migration

### 7.1 Persistence Schema: Market Context (NEW)

```
Local: logs/market_context/{SYMBOL}/{YYYY-MM-DD}.jsonl
S3:    s3://trading-bot-data-mk1/market_context/{SYMBOL}/{YYYY-MM-DD}.jsonl
```

```json
{
  "context_version": 1,
  "symbol": "EURUSD",
  "cycle_id": 1234,
  "timestamp_utc": 1784562000.0,
  "direction": "BULLISH",
  "direction_confidence": 0.72,
  "regime": "TRENDING",
  "regime_confidence": 0.85,
  "phase": "IMPULSE",
  "phase_confidence": 0.68,
  "tradability_score": 0.78,
  "alignment_score": 0.82,
  "h4": {"regime": "TRENDING_BULLISH", "trend_bias": "BULLISH", "trend_strength": 0.7, "atr_ratio": 1.1},
  "h1": {"direction": "BULLISH", "confidence": 0.65, "swing_structure": "HH_HL", "bos_confirmed": true},
  "m15": {"quality": 0.6, "at_key_level": false, "order_block": true},
  "m5": {"regime_state": "TREND_UP", "bias_phase": "CONFIRMED", "bias_strength": 72.0},
  "is_material_change": true,
  "change_reason": "direction: NEUTRAL → BULLISH"
}
```

### 7.2 Decision Trace Schema (ENRICHED)

Current fields preserved. New fields added:

```json
{
  "... existing fields ...": "...",
  "h1_phase": "IMPULSE",
  "m15_setup_quality": 0.65,
  "context_direction": "BULLISH",
  "context_regime": "TRENDING",
  "market_context_id": "EURUSD_1784562000_v1"
}
```

### 7.3 Opportunity Assessment Schema (ENRICHED)

Current `to_dict()` output preserved. New fields added:

```json
{
  "... existing 26+ fields ...": "...",
  "h1_phase": "IMPULSE",
  "m15_setup_confidence": 0.72,
  "market_context_ref": "EURUSD_1784562000_v1"
}
```

### 7.4 Research Shadow Trade Schema (ENRICHED)

Current `decision_snapshot` preserved. New nested field:

```json
{
  "identity": { "..." : "..." },
  "decision_snapshot": {
    "... existing fields ...": "...",
    "market_context": {
      "direction": "BULLISH",
      "regime": "TRENDING",
      "phase": "IMPULSE",
      "tradability_score": 0.78
    }
  },
  "simulated_outcome": { "...": "..." }
}
```

### 7.5 Athena DDL (NEW)

```sql
CREATE EXTERNAL TABLE IF NOT EXISTS trading_bot.market_context (
    context_version INT,
    symbol STRING,
    cycle_id BIGINT,
    timestamp_utc DOUBLE,
    direction STRING,
    direction_confidence DOUBLE,
    regime STRING,
    regime_confidence DOUBLE,
    phase STRING,
    phase_confidence DOUBLE,
    tradability_score DOUBLE,
    alignment_score DOUBLE,
    h4 STRUCT<regime:STRING, trend_bias:STRING, trend_strength:DOUBLE, atr_ratio:DOUBLE>,
    h1 STRUCT<direction:STRING, confidence:DOUBLE, swing_structure:STRING, bos_confirmed:BOOLEAN>,
    m15 STRUCT<quality:DOUBLE, at_key_level:BOOLEAN, order_block:BOOLEAN>,
    m5 STRUCT<regime_state:STRING, bias_phase:STRING, bias_strength:DOUBLE>,
    is_material_change BOOLEAN,
    change_reason STRING
)
ROW FORMAT SERDE 'org.openx.data.jsonserde.JsonSerDe'
WITH SERDEPROPERTIES ('ignore.malformed.json' = 'true', 'case.insensitive' = 'true')
STORED AS INPUTFORMAT 'org.apache.hadoop.mapred.TextInputFormat'
OUTPUTFORMAT 'org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat'
LOCATION 's3://trading-bot-data-mk1/market_context/'
TBLPROPERTIES ('has_encrypted_data'='false');
```

---

## 8. Migration Sequence (Ordered Steps)

### Phase 1: Foundation (Zero Behaviour Change)

```
Step 1.1: Add config flags
    File: core/config.py
    Change: Add MARKET_CONTEXT_ENABLED, MARKET_CONTEXT_SCORING_ENABLED, MARKET_CONTEXT_PERSISTENCE_ENABLED
    Deps: None
    Verify: Import config, confirm flags exist

Step 1.2: Create models
    File: core/market_context/models.py (NEW)
    Change: Define MarketContext, Direction, Regime, Phase
    Deps: None
    Verify: from core.market_context.models import MarketContext

Step 1.3: Create persistence
    File: core/market_context/persistence.py (NEW)
    Change: MarketContextPersistence class (JSONL + S3)
    Deps: models, core/config
    Verify: Unit test — writes JSONL locally

Step 1.4: Create change detector
    File: core/market_context/change_detector.py (NEW)
    Change: ChangeDetector.is_material()
    Deps: models
    Verify: Unit test — detects direction/regime/phase changes

Step 1.5: Create conflict resolver
    File: core/market_context/conflict_resolver.py (NEW)
    Change: ConflictResolver.resolve()
    Deps: models
    Verify: Unit test — H4 BULLISH + H1 BEARISH → resolved direction

Step 1.6: Create builder
    File: core/market_context/builder.py (NEW)
    Change: MarketContextBuilder.build()
    Deps: models, change_detector, persistence, conflict_resolver, core/timeframes/types
    Verify: Unit test — given HTFContext + candles → produces valid MarketContext

Step 1.7: Create __init__
    File: core/market_context/__init__.py (NEW)
    Change: Public API
    Deps: builder, models
    Verify: from core.market_context import build_market_context

Step 1.8: Wire into scanner
    File: core/runtime/scanner_init.py
    Change: Create MarketContextBuilder per symbol (conditional on flag)
    File: core/runtime/live_scanner.py
    Change: Add market_context_builder field to _LiveSymbolState
    Change: Call builder.build() between tf_cache and engine call
    Change: Log output (print or debug log)
    Deps: All above + config flag
    Verify: Run system — see MarketContext logged per cycle, decisions unchanged

Step 1.9: Add persistence infrastructure
    File: logs/market_context/ (directory created on first write)
    File: S3 mirror via existing pattern
    Verify: JSONL files appear on material changes
```

### Phase 2: Shadow Comparison

```
Step 2.1: Enrich DecisionTrace
    File: core/decision_trace.py
    Change: Add 5 new fields to DecisionTrace, populate in build_decision_trace()
    Deps: Phase 1 complete
    Verify: New fields appear in logs/decision_trace/ JSONL

Step 2.2: Pass to observers
    File: core/pipeline/observers.py
    Change: Add market_context field to ObserverContext
    File: core/runtime/live_scanner.py
    Change: Pass market_context in ObserverContext construction
    Deps: Phase 1 complete
    Verify: Observers receive market_context (logged by visibility_layer)

Step 2.3: Shadow validation logging
    File: core/market_context/builder.py
    Change: Log comparison between MarketContext outputs and what engine computes inline
    Verify: Log disagreements — measure rate over 500+ cycles
```

### Phase 3: Engine Integration (Feature-Flagged)

```
Step 3.1: Add market_context param to engine
    File: core/pipeline/new_engine.py
    Change: Add market_context=None to run_new_engine() and _compute_all_scores()
    Deps: Phase 2 validation complete
    Verify: Default None — system behaviour unchanged

Step 3.2: Conditional score reading
    File: core/pipeline/new_engine.py
    Change: When market_context is not None AND MARKET_CONTEXT_SCORING_ENABLED:
            - _score_htf() reads from market_context instead of htf_context
            - _score_h4() reads from market_context instead of htf_context
    Deps: Step 3.1
    Verify: With flag=False, scores identical. With flag=True, scores within ε

Step 3.3: Pass market_context from scanner
    File: core/runtime/live_scanner.py
    Change: Pass market_context= in run_new_engine() call
    Deps: Step 3.1 + 3.2
    Verify: Engine receives and uses MarketContext when flag enabled

Step 3.4: Enrich OpportunityAssessment
    File: core/models/opportunity_assessment.py
    Change: Add h1_phase, m15_setup_confidence, market_context_ref fields
    Change: Include in to_dict()
    Deps: Step 3.3
    Verify: Assessment JSONL contains new fields

Step 3.5: Enrich research shadow trades
    File: core/research_assessment/research_shadow_engine.py
    Change: Include market_context summary in _persist_research_trade() output
    Deps: Step 3.3
    Verify: New records contain market_context nested field

Step 3.6: Pass to execution handler
    File: core/runtime/engine_execution_handler.py
    Change: Accept market_context param for HTF snapshot enrichment
    Deps: Step 3.3
    Verify: prepare_execution receives and stores market_context reference
```

---

## 9. Compatibility Requirements

### 9.1 Backward Compatibility Guarantees

| Guarantee | How Enforced |
|-----------|-------------|
| All new parameters default to None | Existing callers unchanged |
| MARKET_CONTEXT_SCORING_ENABLED defaults False | Engine uses existing inline logic until flag flipped |
| Existing persistence schemas unchanged | New fields added alongside, never replacing |
| component dict keys unchanged | Same 10 keys returned by _compute_all_scores() |
| Score ranges [0.0–1.0] preserved | MarketContext values map to same scale |
| engine_result dict structure unchanged | All existing keys preserved, new keys additive |
| HTFContext continues to work | market_context=None path exercises original code |
| Decision outcomes reproducible | Replay with flag=False produces identical results |

### 9.2 Feature Flag Matrix

| Flag | Default | Effect When True | Effect When False |
|------|---------|-----------------|-------------------|
| `MARKET_CONTEXT_ENABLED` | `True` | Builder runs, context logged | No context built (no CPU cost) |
| `MARKET_CONTEXT_SCORING_ENABLED` | `False` | Engine reads from MarketContext | Engine uses inline computation (current behaviour) |
| `MARKET_CONTEXT_PERSISTENCE_ENABLED` | `True` | JSONL + S3 on material change | No persistence (but builder still runs) |

### 9.3 Rollback Procedure

If any phase causes issues:

```
Phase 1 rollback: Set MARKET_CONTEXT_ENABLED = False
    → Builder stops running. Zero impact on decisions.

Phase 2 rollback: Revert DecisionTrace additions (optional) + disable flag
    → New trace fields become None. Downstream unaffected.

Phase 3 rollback: Set MARKET_CONTEXT_SCORING_ENABLED = False
    → Engine reverts to inline computation immediately.
    → No restart required (flag read per-cycle).
```

---

## 10. Testing Strategy

### 10.1 Unit Tests (per new module)

| Module | Test File | Key Test Cases |
|--------|-----------|---------------|
| `models.py` | `tests/test_market_context_models.py` | Frozen, serializable, to_dict works |
| `change_detector.py` | `tests/test_change_detector.py` | Material vs non-material changes |
| `conflict_resolver.py` | `tests/test_conflict_resolver.py` | H4+H1 agree, disagree, neutral cases |
| `persistence.py` | `tests/test_market_context_persistence.py` | JSONL write, S3 mock |
| `builder.py` | `tests/test_market_context_builder.py` | Full build from mock HTFContext |

### 10.2 Integration Tests

| Test | Validates |
|------|-----------|
| `test_market_context_shadow_mode.py` | Builder produces valid context without affecting engine decisions |
| `test_scoring_parity.py` | With SCORING_ENABLED=True, scores within ε of inline computation |
| `test_persistence_on_change.py` | JSONL written only on material changes (not every cycle) |

### 10.3 Replay Validation

```
1. Run full replay with MARKET_CONTEXT_ENABLED=True, SCORING_ENABLED=False
   → Assert: decisions identical to baseline (zero-diff)

2. Run full replay with SCORING_ENABLED=True
   → Assert: score differences < 0.05 per component
   → Assert: no new trade executions or blocked trades (within tolerance)
```

---

## 11. Summary

### Total Change Surface

| Category | Count |
|----------|-------|
| New files | 7 |
| Modified files (Phase 1) | 5 |
| Modified files (Phase 2) | 3 |
| Modified files (Phase 3) | 8 |
| **Total files touched** | **23** |
| New config flags | 3 |
| New class fields (across all files) | 14 |
| Modified function signatures | 5 |
| New Athena tables | 1 |
| Persistence schemas enriched | 3 |
| Files explicitly preserved | 17 |

### Critical Path Dependencies

```
core/market_context/models.py           ← Must exist first (all others depend on it)
    ↓
core/market_context/change_detector.py  ← Needed by builder
core/market_context/persistence.py      ← Needed by builder
core/market_context/conflict_resolver.py ← Needed by builder
    ↓
core/market_context/builder.py          ← Central component
    ↓
core/config.py (flags)                  ← Gate for wiring
    ↓
core/runtime/live_scanner.py (wiring)   ← Activates the layer
    ↓
core/pipeline/new_engine.py (Phase 3)   ← Final connection to decisions
```

---

*Document produced: 2026-07-20*
*Status: Migration Plan — No Code Modified*
*Implementation: NOT started*
