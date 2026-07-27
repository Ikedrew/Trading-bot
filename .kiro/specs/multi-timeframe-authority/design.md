# Technical Design Document

## Overview

This document describes the technical design for the Hierarchical Multi-Timeframe Authority system. The architecture transforms the current M5-only pipeline into a layered authority hierarchy where higher timeframes constrain lower timeframes through cached context — without competing with or bypassing M5 execution authority.

The key architectural principle: **authority flows downward, never upward**. H4 constrains H1, H1 constrains M15, M15 constrains M5. No lower timeframe can invalidate a higher timeframe's decision.

## Architecture

### Authority Hierarchy

```
┌─────────────────────────────────────────────────────────────────────┐
│                    AUTHORITY FLOW (top → down)                       │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  H4 REGIME AUTHORITY (refreshes every 4h)                           │
│  ├── Classifies: TRENDING_BULLISH/BEARISH, RANGING, VOLATILE, TRANSITIONAL
│  ├── Constrains: score thresholds, regime penalties                 │
│  └── Cannot: trigger trades, override M5 decisions upward          │
│       │                                                             │
│       ▼                                                             │
│  H1 BIAS AUTHORITY (refreshes every 1h)                             │
│  ├── Classifies: BULLISH, BEARISH, NEUTRAL + confidence             │
│  ├── Constrains: directional gating, score bonuses/penalties        │
│  ├── Respects: H4 regime (bias within regime context)               │
│  └── Cannot: trigger trades, override M5 decisions upward          │
│       │                                                             │
│       ▼                                                             │
│  M15 STRUCTURE AUTHORITY (refreshes every 15m)                      │
│  ├── Classifies: structure quality 0.0–1.0                          │
│  ├── Constrains: structural quality gate, score bonuses             │
│  ├── Respects: H1 bias direction                                    │
│  └── Cannot: trigger trades, override M5 decisions upward          │
│       │                                                             │
│       ▼                                                             │
│  M5 EXECUTION AUTHORITY (every new M5 bar)                          │
│  ├── Owns: pattern detection, scoring, confirmation, intent build   │
│  ├── Consumes: H4 regime + H1 bias + M15 structure (read-only)     │
│  ├── Decides: trade / no-trade (SOLE AUTHORITY)                     │
│  └── Produces: OrderIntent → execution layer                        │
│       │                                                             │
│       ▼ (optional, post-decision only)                              │
│  M1 REFINEMENT (refreshes every 1m, disabled by default)            │
│  ├── Provides: entry timing context                                 │
│  ├── Consulted: ONLY after M5 decides should_trade=True             │
│  └── Cannot: change trade/no-trade decision                         │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### Runtime Sequencing (per M5 cycle)

```
FOR each symbol in scan cycle:
  │
  ├── 1. CHECK TimeframeCache staleness for all HTFs
  │     ├── H4: new bar? → fetch H4 candles → run regime analyzer → cache
  │     ├── H1: new bar? → fetch H1 candles → run bias analyzer → cache
  │     ├── M15: new bar? → fetch M15 candles → run structure analyzer → cache
  │     └── (M1: if enabled, new bar? → fetch → cache)
  │
  ├── 2. BUILD HTFContext snapshot (read cached values)
  │     └── HTFContext = { regime, bias, structure_quality, confidence scores }
  │
  ├── 3. EXISTING M5 PIPELINE (unchanged stages 1-5)
  │     ├── market_context → strategy_detection → structure_analysis
  │     ├── confirmations → trade_quality_post_confirm
  │     └── [all existing logic unchanged]
  │
  ├── 4. HTF-AUGMENTED SCORING (new integration point)
  │     ├── Apply H4 regime penalty/threshold adjustment
  │     ├── Apply H1 bias bonus or contradiction gate
  │     ├── Apply M15 structure quality gate or bonus
  │     └── Compute final augmented score
  │
  ├── 5. EXISTING SCORING THRESHOLD + INTENT BUILD
  │     └── [existing scoring_engine + intent_builder unchanged]
  │
  └── 6. OPTIONAL M1 REFINEMENT (only if should_trade=True)
        └── Provide M1 context for entry timing
```

### Difference from Previous Architecture

| Aspect | Previous (M5-only) | New (Hierarchical MTF) |
|--------|--------------------|-----------------------|
| Bias source | M5 MA setup (10-period) | H1 swing structure + EMA |
| Regime detection | M5 market_context (5-bar ratio) | H4 structural classification |
| Structure validation | M5 structure_analysis (same TF) | M15 key levels + order blocks |
| Scoring influence | M5 confluence only | M5 confluence + HTF bonuses/penalties |
| Trade gating | M5 pipeline stages only | M5 stages + HTF directional/structural gates |
| Data dependency | 300 M5 candles | 300 M5 + 100 H4 + 200 H1 + 200 M15 |

**Critical distinction:** The existing M5 bias FSM (EXPIRED→BUILDING→CONFIRMED) continues to operate independently. HTF bias is an *additional* constraint layer, not a replacement. Both must agree for a trade to proceed.

## Components and Interfaces

### Project Structure

```
core/timeframes/
├── __init__.py              # Public API: TimeframeCache, HTFContext, apply_htf_constraints
├── cache.py                 # TimeframeCache: per-symbol, per-TF snapshot management
├── types.py                 # RegimeState, BiasState, StructureState, HTFContext
├── h4_regime.py             # H4 regime analyzer (pure function)
├── h1_bias.py               # H1 bias analyzer (pure function)
├── m15_structure.py         # M15 structure analyzer (pure function)
├── m1_refinement.py         # M1 refinement provider (optional)
└── integration.py           # apply_htf_constraints(): scoring/gating logic
```

### Type Definitions (`core/timeframes/types.py`)

```python
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from strategy.signals import Side


class RegimeClassification(Enum):
    TRENDING_BULLISH = "TRENDING_BULLISH"
    TRENDING_BEARISH = "TRENDING_BEARISH"
    RANGING = "RANGING"
    VOLATILE = "VOLATILE"
    TRANSITIONAL = "TRANSITIONAL"


class BiasDirection(Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


@dataclass(frozen=True)
class RegimeSnapshot:
    """H4 regime analysis result."""
    classification: RegimeClassification
    confidence: float          # 0.0–1.0
    bar_time: int              # timestamp of H4 bar that produced this
    atr_ratio: float           # current ATR / rolling average ATR
    ema_slope: float           # normalized EMA slope direction


@dataclass(frozen=True)
class BiasSnapshot:
    """H1 bias analysis result."""
    direction: BiasDirection
    confidence: float          # 0.0–1.0
    bar_time: int              # timestamp of H1 bar that produced this
    ema_position: float        # price distance from EMA (normalized by ATR)
    swing_structure: str       # "HH_HL" | "LH_LL" | "MIXED"


@dataclass(frozen=True)
class StructureSnapshot:
    """M15 structure analysis result."""
    quality_score: float       # 0.0–1.0
    bar_time: int              # timestamp of M15 bar that produced this
    nearest_support: float     # price level
    nearest_resistance: float  # price level
    at_key_level: bool         # price within ATR distance of S/R
    order_block_present: bool  # bullish/bearish OB detected


@dataclass(frozen=True)
class HTFContext:
    """
    Immutable snapshot of all higher-timeframe authority states.
    Consumed by M5 pipeline (read-only). Built once per bar evaluation.
    """
    regime: RegimeSnapshot | None
    bias: BiasSnapshot | None
    structure: StructureSnapshot | None
    
    @property
    def is_populated(self) -> bool:
        """True if at least one HTF layer has data."""
        return any(x is not None for x in (self.regime, self.bias, self.structure))


@dataclass(frozen=True)
class HTFInfluence:
    """Result of applying HTF constraints to M5 scoring."""
    score_adjustment: float        # net bonus/penalty to add to confluence score
    min_score_adjustment: float    # increase to minimum score threshold
    directional_block: bool        # True = trade blocked by HTF bias contradiction
    structural_block: bool         # True = trade blocked by M15 quality gate
    block_reason: str              # human-readable reason if blocked
    breakdown: dict[str, float]    # per-layer contribution for audit
```


## Data Models

### 4. Multi-Timeframe Data Architecture

#### 4.1 Timeframe Cache Manager (`cache.py`)

The system introduces a per-symbol, per-timeframe caching layer responsible for maintaining the latest validated snapshot of each higher timeframe. This ensures that all higher timeframe logic is computed once per bar change and reused across multiple M5 evaluations.

**Cache Entry Structure:**

```python
@dataclass
class _CacheEntry:
    """Single timeframe snapshot for one symbol."""
    bar_time: int              # timestamp of the bar that produced this snapshot
    snapshot: RegimeSnapshot | BiasSnapshot | StructureSnapshot | None
    last_fetch_wall: float     # wall-clock time of last successful fetch
    fetch_failures: int        # consecutive fetch failure count
```

Each entry represents:
- The last processed bar for a given timeframe
- The computed analytical snapshot (regime, bias, or structure)
- Failure tracking for resilience and degraded operation

**TimeframeCache Core Design:**

```python
class TimeframeCache:
    """
    Per-symbol, per-timeframe snapshot store.
    
    Lifecycle:
      1. Created once per symbol at scanner init
      2. update_if_needed() called at top of each M5 cycle
      3. get_htf_context() called by pipeline to read cached state
      4. Never modified by pipeline stages (read-only consumer)
    """
    
    def __init__(self, symbol: str, feed: MT5DataFeed, config) -> None:
        self._symbol = symbol
        self._feed = feed
        self._config = config
        self._entries: dict[int, _CacheEntry] = {}  # keyed by MT5 timeframe constant
    
    def update_if_needed(self, current_time_s: float) -> None:
        """Check all configured timeframes for new bar closure; refresh stale entries."""
        ...
    
    def get_htf_context(self, current_price: float) -> HTFContext:
        """Build immutable HTFContext from cached snapshots. Never fetches."""
        ...
```

**Responsibilities:**
- Own all HTF snapshot storage per symbol
- Detect new bar closures per timeframe
- Trigger conditional fetch + analysis
- Provide immutable HTF context to downstream pipeline
- Prevent repeated MT5 calls within M5 cycles

**Interface contracts:**

`update_if_needed(current_time_s)`:
- Runs every M5 cycle
- Performs lightweight "new bar detection" (single 1-bar fetch per TF to compare timestamps)
- Only triggers full fetch + analyzer when new bar confirmed
- Never raises — failures are logged and swallowed

`get_htf_context(current_price)`:
- NEVER calls MT5
- Only reads cached snapshots
- Produces immutable HTFContext for pipeline consumption
- Returns HTFContext with None fields for any timeframe without cached data

#### 4.2 Synchronized Candle Access Strategy

All higher timeframe data is synchronised through controlled MT5 access patterns:

```
MT5 Terminal
  ├── H4: copy_rates_from_pos (only on new H4 bar — every ~4 hours)
  ├── H1: copy_rates_from_pos (only on new H1 bar — every ~1 hour)
  ├── M15: copy_rates_from_pos (only on new M15 bar — every ~15 minutes)
  ├── M1: copy_rates_from_pos (optional, if MTF_M1_ENABLED — every ~1 minute)
  └── M5: copy_rates_from_pos (every cycle, unchanged existing behavior)
```

**Key Rule:** Higher timeframes are NOT polled continuously — they are event-driven by bar closure detection.

**New bar detection (lightweight):**

```python
def _check_new_bar(self, tf: int) -> bool:
    """Lightweight check: fetch 1 bar, compare timestamp. O(1) MT5 call."""
    try:
        latest = self._feed.copy_rates_closed(self._symbol, tf, 1)
        if not latest:
            return False
        new_time = latest[0].time
        cached_time = self._entries.get(tf, _CacheEntry(0, None, 0.0, 0)).bar_time
        return new_time > cached_time
    except RuntimeError:
        return False
```

#### 4.3 Stale Data Protection

```python
def _is_stale(self, entry: _CacheEntry, tf_seconds: int, current_time_s: float) -> bool:
    """
    A snapshot is stale if:
      1. bar_time is older than 3x the timeframe duration, OR
      2. No snapshot exists (cold start)
    """
    if entry.snapshot is None:
        return True
    max_age = tf_seconds * 3
    return (current_time_s - entry.bar_time) > max_age
```

**Staleness thresholds:**

| Timeframe | Bar duration | Stale after |
|-----------|-------------|-------------|
| H4 | 14400s (4h) | 43200s (12h) |
| H1 | 3600s (1h) | 10800s (3h) |
| M15 | 900s (15m) | 2700s (45m) |
| M1 | 60s (1m) | 180s (3m) |

#### 4.4 Cache Invalidation Rules

1. **Normal refresh:** New bar detected (bar_time > cached bar_time) → fetch + analyze → replace snapshot
2. **Staleness refresh:** Snapshot age exceeds 3× timeframe duration → force fetch attempt on next cycle
3. **Failure retention:** If fetch fails, retain previous snapshot (never clear to None after first successful load)
4. **Cold start:** First cycle has no cached data → all HTF constraints disabled until first successful fetch per timeframe

#### 4.5 Fetch Scheduling

```
PER M5 CYCLE (1 second poll interval):

  ┌─ H4 check: compare cached bar_time vs latest H4 candle time
  │   └── New bar? (~6× per day) → fetch 100 H4 candles → analyze → cache
  │
  ├─ H1 check: compare cached bar_time vs latest H1 candle time
  │   └── New bar? (~24× per day) → fetch 200 H1 candles → analyze → cache
  │
  ├─ M15 check: compare cached bar_time vs latest M15 candle time
  │   └── New bar? (~96× per day) → fetch 200 M15 candles → analyze → cache
  │
  └─ M1 check (if enabled): compare cached bar_time vs latest M1 candle time
      └── New bar? (~1440× per day) → fetch 60 M1 candles → cache
```

**Cost analysis per M5 cycle:**
- 3–4 lightweight 1-bar fetches (new bar detection) = ~4ms total
- Full fetch only when new bar confirmed = amortized negligible
- Worst case (all TFs close simultaneously): 4 full fetches in one cycle = ~50ms additional latency

#### 4.6 Historical Warmup Requirements

| Timeframe | Candles needed | Warmup purpose |
|-----------|---------------|----------------|
| H4 | 100 bars (~17 days) | EMA-50 calculation, ATR rolling average, HH/HL sequences |
| H1 | 200 bars (~8 days) | EMA-50, swing structure, momentum, key levels |
| M15 | 200 bars (~2 days) | Swing highs/lows, S/R levels, order blocks |
| M1 | 60 bars (1 hour) | Recent price action for entry refinement |

**First-cycle behavior:** On startup, all timeframes attempt a full fetch. If any fail, the system operates in degraded mode (no HTF constraints for that layer) until the next successful fetch.

#### 4.7 Partial Data Failure Handling

| Failure scenario | Behavior |
|-----------------|----------|
| H4 fetch fails (MT5 error) | Retain previous H4 snapshot; log `[MTF_FETCH_FAIL] tf=H4`; M5 pipeline runs without H4 constraints |
| H1 fetch fails | Retain previous H1 snapshot; log warning; no directional gating applied |
| M15 fetch fails | Retain previous M15 snapshot; log warning; no structural gate applied |
| M1 fetch fails | Ignored (M1 is optional refinement only) |
| All HTF fetches fail | M5 pipeline runs exactly as if MTF_ENABLED=False (full graceful degradation) |
| Fewer bars returned than requested | Analyzer receives partial data; returns low-confidence or default result |

#### 4.8 Data Ownership Boundaries

```
OWNERSHIP:

TimeframeCache (core/timeframes/cache.py)
  OWNS: _entries dict, fetch scheduling, staleness detection
  READS: MT5DataFeed (via existing copy_rates_closed)
  PRODUCES: HTFContext (immutable, frozen dataclass)
  
HTFContext (frozen dataclass)
  OWNED BY: nobody (immutable value object)
  CONSUMED BY: integration.py (apply_htf_constraints)
  LIFETIME: one M5 bar evaluation (created, consumed, discarded)

MT5DataFeed (data/mt5_data.py)
  UNCHANGED: no modifications required
  USED BY: TimeframeCache (additional timeframe fetches)
```

#### 4.9 Concurrency Considerations

**None required.** The system is single-threaded:
- `TimeframeCache.update_if_needed()` runs synchronously at the top of each symbol's processing
- All MT5 calls are blocking (same as existing M5 fetch)
- No shared mutable state between symbols (each has its own TimeframeCache instance)
- No async, no threading, no race conditions possible

#### 4.10 Runtime Synchronization Strategy

```
SYNCHRONIZATION MODEL: Sequential, deterministic

Per-symbol processing order (within one scan cycle):
  1. TimeframeCache.update_if_needed()    ← HTF refresh (if new bars)
  2. HTFContext = cache.get_htf_context()  ← read cached state
  3. process_bar(htf_context=...)          ← M5 pipeline with HTF context
  4. [execution if should_trade]

Cross-symbol isolation:
  - Each symbol has independent TimeframeCache
  - No symbol's HTF state influences another symbol
  - Processing order within cycle is deterministic (list order)
```


### 5. State Management Design

#### 5.1 State Ownership Model (MTF Extension)

The multi-timeframe system introduces a strict separation between persistent cache state, ephemeral runtime state, and immutable decision snapshots.

| State Object | Owner | Mutability | Lifetime |
|-------------|-------|-----------|----------|
| `_entries` (TimeframeCache) | TimeframeCache | Mutable | Process lifetime |
| `RegimeSnapshot` | TimeframeCache | Replaced per H4 bar | Until next H4 bar |
| `BiasSnapshot` | TimeframeCache | Replaced per H1 bar | Until next H1 bar |
| `StructureSnapshot` | TimeframeCache | Replaced per M15 bar | Until next M15 bar |
| `HTFContext` | None (value object) | Immutable | Single M5 cycle |
| `HTFInfluence` | None (value object) | Immutable | Single M5 cycle |

#### 5.2 Core State Philosophy

The system enforces three rules:

**1. Cache is mutable, decisions are not**
- TimeframeCache can update snapshots
- Pipeline cannot mutate cached state

**2. All pipeline inputs are immutable**
- HTFContext is frozen once created
- HTFInfluence is a pure output object

**3. No cross-stage mutation**
- No stage modifies upstream state
- No HTF logic writes into EngineState

#### 5.3 HTFContext (Immutable Runtime View)

```python
@dataclass(frozen=True)
class HTFContext:
    regime: RegimeSnapshot | None
    bias: BiasSnapshot | None
    structure: StructureSnapshot | None
    timestamp: int
    symbol: str

    @property
    def is_populated(self) -> bool:
        return any(x is not None for x in (self.regime, self.bias, self.structure))
```

**Purpose:** Provides a read-only snapshot of all higher timeframe intelligence at the moment of M5 evaluation.

**Properties:**
- Fully immutable (`frozen=True`)
- No MT5 access
- No computation logic inside
- Safe to pass through entire pipeline

#### 5.4 HTFInfluence (Decision Modifier Object)

```python
@dataclass(frozen=True)
class HTFInfluence:
    score_adjustment: float        # net bonus/penalty to add to confluence score
    min_score_adjustment: float    # increase to minimum score threshold
    directional_block: bool        # True = trade blocked by HTF bias contradiction
    structural_block: bool         # True = trade blocked by M15 quality gate
    block_reason: str              # human-readable reason if blocked
    breakdown: dict[str, float]    # per-layer contribution for audit
```

**Purpose:** Encapsulates how HTF conditions affect M5 decision-making.

**Behaviour Rules:**
- Does NOT decide trades directly
- Only modifies scoring / gating
- Must remain deterministic and pure

#### 5.5 Snapshot Lifecycle Model

Each snapshot follows this lifecycle:

```
FETCH → ANALYZE → STORE → REUSE → REPLACE (on new bar)
```

**RegimeSnapshot (H4):**
- Defines market regime (TRENDING / RANGING / VOLATILE)
- Drives global risk tolerance and scoring bias

**BiasSnapshot (H1):**
- Defines directional bias (BULLISH / BEARISH / NEUTRAL)
- Acts as primary directional filter for M5

**StructureSnapshot (M15):**
- Defines structural quality (breakouts, S/R validity, order flow context)
- Acts as execution permission layer

#### 5.6 EngineState Isolation

The MTF system introduces **zero new fields** to EngineState. All HTF state lives in TimeframeCache (separate object). This ensures:
- No risk of NaN/Inf corruption in EngineState from HTF calculations
- No warm-start compatibility issues
- No coupling between M5 bias FSM and HTF bias
- Clean separation of concerns
- Existing `validate_engine_state()` continues to work unchanged

#### 5.7 Persistence Model

- **TimeframeCache is NOT persisted.** On restart, all HTF snapshots start empty (cold start). First cycle fetches all timeframes.
- **HTFContext is NOT persisted.** It's rebuilt every bar from cached snapshots.
- **No warm-start dependency.** The MTF system is fully self-bootstrapping within one cycle.


### 6. Runtime Loop Integration

#### 6.1 Integration Point (`live_scanner.py`)

The multi-timeframe system integrates directly into the existing M5 scanner loop with minimal disruption to the core execution flow.

**BEFORE (current system):**

```python
for sym_state in states:
    # fetch M5 candles
    unified = process_bar(...)
```

**AFTER (with MTF system):**

```python
for sym_state in states:
    # 1. Existing M5 pipeline inputs
    # (candles, ticks, trade management remain unchanged)

    # 2. HTF Cache Update (NEW)
    if mtf_enabled:
        sym_state.tf_cache.update_if_needed(current_time_s=float(closed_time))
        htf_context = sym_state.tf_cache.get_htf_context(current_price=bid)
    else:
        htf_context = None

    # 3. Pipeline execution (extended)
    unified = process_bar(
        candles=candles,
        closed_i=closed_i,
        symbol=sym_state.symbol,
        config=config,
        risk=sym_state.risk,
        state=sym_state.engine_state,
        bid=bid,
        ask=ask,
        htf_context=htf_context,   # NEW injection point
    )
```

#### 6.2 Execution Order Guarantees

The system enforces a strict ordering:

```
1. Tick / candle fetch (M5 unchanged)
2. HTF cache update (if enabled)
3. HTFContext construction
4. M5 pipeline execution
5. Decision finalisation / trade execution
```

**Critical Rule:** HTF updates ALWAYS happen BEFORE M5 evaluation.

This ensures:
- No stale HTF influence
- Deterministic decision-making per cycle
- Consistent scoring inputs

#### 6.3 Pipeline Injection Point (`engine.py`)

HTF logic is injected after signal formation but before scoring.

```python
def process_bar(..., htf_context: HTFContext | None = None) -> UnifiedDecision:
```

**Placement in pipeline:**

```
Stage 1–5: Existing M5 logic (unchanged)
    ↓ Signal + Bias produced
    ↓
🔴 NEW: HTF Constraint Layer
    ↓
Scoring Engine (modified to accept HTF adjustments)
    ↓
Decision Finalisation
```

#### 6.4 HTF Constraint Application

```python
if htf_context is not None and htf_context.is_populated:
    htf_influence = apply_htf_constraints(
        htf_context=htf_context,
        signal=cont.signal,
        evaluation_bias=cont.evaluation_bias,
        config=config,
    )
```

**Behaviour:**
- Pure function (no state mutation)
- No MT5 calls
- No EngineState writes
- Only transforms scoring inputs

#### 6.5 Hard Blocking Behaviour

HTF can short-circuit execution:

```python
if htf_influence.directional_block or htf_influence.structural_block:
    return engine.finalize(
        ...,
        params=FinishParams(
            should_trade=False,
            reason=f"htf_block:{htf_influence.block_reason}",
            ...
        ),
    )
```

**Meaning:**
- Trade is fully cancelled
- Pipeline stops immediately
- No scoring occurs

#### 6.6 Scoring Engine Augmentation

HTF modifies scoring, not decision logic:

```python
# Existing score calculation
score = (base_score * bias_age_weight * time_decay_multiplier) + vol_penalty + regime_bonus + sweep_bonus

# NEW: HTF adjustment applied
score += htf_influence.score_adjustment

# Existing threshold
min_score = max(float(getattr(config, "MIN_SCORE_TO_TRADE", 5)), confluence_threshold_dynamic)

# NEW: HTF threshold adjustment
min_score += htf_influence.min_score_adjustment
```

**Key principle:** HTF does NOT decide trades — it reshapes the probability space.

#### 6.7 Runtime Tick Lifecycle

Each M5 cycle follows this deterministic flow:

```
M5 Candle Close Detected
        ↓
Fetch M5 data (existing system)
        ↓
Update TimeframeCache (H4/H1/M15/M1 if needed)
        ↓
Build HTFContext (immutable snapshot)
        ↓
Run process_bar()
        ↓
Apply HTF constraints (gate or adjust)
        ↓
Run scoring engine (augmented)
        ↓
Generate UnifiedDecision
        ↓
Execute / skip trade
```

#### 6.8 Timing Constraints

**HTF update frequency:**

| Layer | Update Frequency |
|-------|-----------------|
| H4 | ~6 times/day |
| H1 | ~24 times/day |
| M15 | ~96 times/day |
| M1 | ~1440 times/day (optional) |

**M5 loop:** Runs continuously. HTF checks are event-driven, not polling heavy.

#### 6.9 Failure Safety Model

If HTF system fails:

**Case 1: Cache update failure**
- Retain previous snapshot
- Log warning
- Continue M5 execution

**Case 2: Full HTF failure**
- System falls back to MTF_DISABLED behaviour → pure M5 strategy only

**Case 3: Partial data failure**
- Degraded influence
- No hard blocks applied
- Scoring continues normally

#### 6.10 Determinism Guarantee

The runtime guarantees:
- Same HTFContext → same decision outcome
- No async race conditions
- No cross-symbol interference
- No frame drift between evaluation and execution


### 7. Configuration Architecture

#### 7.1 Hierarchical Configuration Model

The system uses a layered configuration hierarchy where global settings define system-wide behaviour, and timeframe-specific overrides refine behaviour per authority layer.

**Hierarchy Order (highest → lowest priority):**

```
GLOBAL CONFIG
    ↓
TIMEFRAME OVERRIDES (H4 / H1 / M15 / M1)
    ↓
STRATEGY-SPECIFIC SETTINGS
    ↓
RUNTIME OVERRIDES (temporary / debug flags)
```

**Core Principle:** Lower layers can override defaults but never remove structural constraints defined above them.

#### 7.2 Timeframe-Specific Settings

Each timeframe authority layer has isolated configuration controls:

```python
# --- Multi-Timeframe Authority ---
MTF_ENABLED = False                          # Master switch

# H4 Regime Layer
MTF_H4_ENABLED = True                        # Enable H4 regime analysis
MTF_H4_CANDLE_COUNT = 100                    # Bars to fetch (17 days)
MTF_H4_RANGING_SCORE_PENALTY = 1.0           # Subtracted from score when RANGING
MTF_H4_VOLATILE_MIN_SCORE_INCREASE = 1.0     # Added to min_score when VOLATILE
MTF_H4_REGIME_SENSITIVITY = 1.0              # Multiplier for regime confidence

# H1 Bias Layer
MTF_H1_ENABLED = True                        # Enable H1 bias analysis
MTF_H1_CANDLE_COUNT = 200                    # Bars to fetch (8 days)
MTF_H1_ALIGNED_BONUS = 0.5                   # Added to score when bias aligns
MTF_H1_NEUTRAL_MIN_SCORE_INCREASE = 0.5      # Added to min_score when NEUTRAL
MTF_H1_CONTRADICTION_THRESHOLD = 7.0         # Score must exceed this to override contradiction
MTF_H1_CONTRADICTION_BLOCK = True            # Whether contradiction blocks or just penalizes

# M15 Structure Layer
MTF_M15_ENABLED = True                       # Enable M15 structure analysis
MTF_M15_CANDLE_COUNT = 200                   # Bars to fetch (2 days)
MTF_M15_MIN_STRUCTURE_QUALITY = 0.3          # Below this = structural block
MTF_M15_HIGH_QUALITY_THRESHOLD = 0.7         # Above this = quality bonus
MTF_M15_HIGH_QUALITY_BONUS = 0.5             # Added to score when quality is high

# M1 Refinement Layer (optional)
MTF_M1_ENABLED = False                       # Disabled by default
MTF_M1_CANDLE_COUNT = 60                     # Bars to fetch (1 hour)
```

**Rule:**
- H4 defines environment
- H1 defines direction
- M15 defines validity
- M1 only refines entries

#### 7.3 Enable/Disable Controls

Master switches allow safe degradation:

```python
MTF_ENABLED = True          # Master switch — False = entire MTF system inert
MTF_H4_ENABLED = True       # Per-layer switches
MTF_H1_ENABLED = True
MTF_M15_ENABLED = True
MTF_M1_ENABLED = False
```

**Behaviour Rules:**
- If `MTF_ENABLED = False` → system behaves like legacy M5-only scanner
- If individual layers disabled → influence is skipped but system remains stable
- Disabling a layer does not affect other layers

#### 7.4 Threshold Management System

All decision thresholds are configurable and dynamically injected into the HTF influence layer.

```python
MTF_H1_CONTRADICTION_THRESHOLD = 7.0     # Score override threshold
MTF_H4_RANGING_SCORE_PENALTY = 1.0       # Regime penalty
MTF_M15_MIN_STRUCTURE_QUALITY = 0.3      # Structural gate
```

**Key Rule:** Thresholds affect scoring only — never control data fetching or cache logic.

#### 7.5 Configuration Validation Rules

At startup (integrated into existing `validate_and_freeze_config()`):

- All timeframe configs must exist if enabled
- No negative thresholds allowed
- Boolean flags must resolve deterministically
- Missing values fallback to safe defaults
- Candle counts must be positive integers
- Bonus/penalty values must be finite floats

**Validation Failure → system enters SAFE MODE:**
- SAFE MODE → MTF disabled automatically
- Log `[MTF_CONFIG_INVALID]` with reason
- M5 pipeline continues unchanged

#### 7.6 Backward Compatibility Strategy

The system must support existing M5-only configurations without modification.

**Compatibility Rules:**
- If no `MTF_*` config exists → default to `MTF_ENABLED = False`
- Existing pipeline continues unchanged
- No required schema migration
- All new config flags have safe defaults

**Migration Behaviour:**
```
Old Config (no MTF_* keys) → Auto maps to: MTF_ENABLED = False
```


### 8. Observability and Diagnostics

#### 8.1 Structured Logging Model

All HTF events use structured log categories:

```
[MTF_REFRESH]   → cache updates (new bar detected, snapshot replaced)
[MTF_BLOCK]     → trade blocked by HTF constraint (directional or structural)
[MTF_SCORE]     → scoring adjustments applied (bonus/penalty breakdown)
[MTF_FALLBACK]  → degraded mode activation (fetch failure, stale data)
[MTF_FETCH_FAIL]→ MT5 candle fetch failure for a specific timeframe
[MTF_STALE]     → snapshot exceeded staleness threshold
```

#### 8.2 Authority Decision Tracing

Every decision includes a trace object in the decision audit:

```json
{
    "htf": {
        "h4_regime": "RANGING",
        "h4_confidence": 0.82,
        "h1_bias": "BEARISH",
        "h1_confidence": 0.71,
        "m15_quality": 0.62,
        "score_adjustment": -0.8,
        "min_score_adjustment": 0.5,
        "directional_block": false,
        "structural_block": false,
        "block_reason": ""
    }
}
```

**Purpose:**
- Reconstruct why a trade happened or didn't happen
- Debug authority conflicts
- Validate system behaviour over time
- Tune thresholds based on historical decisions

#### 8.3 Runtime Diagnostics

System exposes per-symbol HTF health:

```
[MTF_STATUS] symbol=EURUSD_SB h4_age=7200s h1_age=1800s m15_age=450s 
             h4_state=RANGING h1_state=BEARISH m15_quality=0.62
             h4_failures=0 h1_failures=0 m15_failures=0
```

Emitted periodically (every 25 cycles, aligned with existing dashboard interval).

#### 8.4 Cache Health Monitoring

Each timeframe tracks:
- Last successful fetch time
- Consecutive failure count
- Staleness state (FRESH / STALE / COLD)

**Alert conditions:**

| Alert | Trigger |
|-------|---------|
| `H4_CACHE_STALE` | H4 snapshot older than 12 hours |
| `H1_FETCH_FAIL` | 3+ consecutive H1 fetch failures |
| `M15_DEGRADED` | M15 snapshot stale AND fetch failing |
| `MTF_FULL_DEGRADATION` | All HTF layers unavailable |

#### 8.5 Execution Audit Flow

Each M5 cycle produces a traceable audit chain:

```
M5 CYCLE
  → HTF UPDATE (which TFs refreshed, if any)
    → HTF CONTEXT BUILD (snapshot values used)
      → HTF INFLUENCE (score_adj, min_score_adj, blocks)
        → SCORE ADJUSTMENT (final augmented score)
          → FINAL DECISION (trade / no-trade + reason)
```

Integrated into existing `persist_decision_audit()` — HTF breakdown added to the `confluence_breakdown` dict.

#### 8.6 Key Metrics

| Metric | Description |
|--------|-------------|
| HTF refresh latency | Time taken for full fetch + analyze per TF |
| Cache freshness ratio | % of cycles where cached data was used vs fresh fetch |
| HTF block frequency | Trades blocked by HTF per 100 cycles |
| Scoring adjustment impact | Average score modification from HTF |
| Trade rejection rate (HTF) | % of would-be trades blocked by HTF constraints |

#### 8.7 Failure Visibility Strategy

Failures are never silent:
- All MT5 fetch errors logged with `[MTF_FETCH_FAIL]`
- Degraded mode explicitly flagged with `[MTF_FALLBACK]`
- Stale snapshots visible in diagnostics with `[MTF_STALE]`
- Fallback state always explicit in decision audit output
- No HTF failure can produce a silent behavioral change


## Error Handling

### 9. Failure Handling and Recovery

#### 9.1 Degraded Mode Behaviour

If any HTF layer fails, the system degrades gracefully:

| Layer Failure | System Behaviour |
|--------------|-----------------|
| H4 fail | No regime filtering applied; scoring unmodified by H4 |
| H1 fail | No directional bias gating; no alignment bonus/penalty |
| M15 fail | No structural quality gate; no structure bonus |
| All HTF fail | M5 system continues normally (equivalent to MTF_ENABLED=False) |

**Principle:** M5 execution is never blocked by HTF infrastructure failure.

#### 9.2 Missing Timeframe Handling

When a snapshot is None (cold start or persistent failure):
- Treated as neutral (no influence)
- No blocking applied
- No score penalty unless explicitly configured
- Pipeline proceeds as if that layer doesn't exist

#### 9.3 Stale Data Behaviour

If a snapshot exceeds the staleness threshold (3× timeframe duration):
- Flagged as stale in diagnostics
- Still usable (better than no data)
- Influence strength optionally downgraded (multiplied by staleness decay factor)
- Fresh fetch attempted on next cycle

#### 9.4 Cache Corruption Handling

If an invalid snapshot is detected (analyzer returns unexpected type or NaN values):
- Discard corrupted entry (set snapshot to None)
- Attempt re-fetch on next cycle
- Fallback to last valid snapshot if available
- Log `[MTF_CACHE_CORRUPT]` with details

#### 9.5 Reconnect / Recovery Sequencing

On MT5 reconnect (after `attempt_reconnect` succeeds):
1. Invalidate all cache entries (mark as stale)
2. Rebuild in priority order: H4 → H1 → M15 → M1
3. Each fetch is independent (failure of one doesn't block others)
4. Resume normal cycle after first successful fetch per layer

**Integration:** TimeframeCache exposes `invalidate_all()` method called by live_scanner after reconnect.

#### 9.6 Authority Desynchronisation Handling

If timeframes disagree strongly (e.g., H4=TRENDING_BULLISH but H1=BEARISH):
- No hard failure
- Handled via scoring penalties only
- H4 always overrides lower TF in conflict resolution
- The contradiction is logged for analysis but does not crash the system

#### 9.7 Safe-Fail Rules

System always prefers:
- **No trade > incorrect trade** (conservative by default)
- **Degraded mode > unstable decisions** (disable layer rather than use bad data)
- **Stale data > missing data crash** (old snapshot better than None)
- **Logging > silence** (every failure path emits a structured log)

#### 9.8 Recovery Priority Order

```
1. H4 (highest priority — defines regime for all other layers)
2. H1 (directional authority)
3. M15 (structural validation)
4. M1 (optional — lowest priority)
```

On startup or reconnect, fetches are attempted in this order. If H4 fails but H1 succeeds, H1 operates without regime context (acceptable degradation).


## Correctness Properties

### 10. Migration Strategy

#### 10.1 Phased Migration Plan

| Phase | Scope | Risk |
|-------|-------|------|
| **Phase 1 — Infrastructure** | Implement TimeframeCache, types, directory structure. No scoring changes. | Zero (no behavioral change) |
| **Phase 2 — Data Layer** | H4/H1/M15 analyzer implementations. Snapshot generation and caching. | Zero (analyzers run but output unused) |
| **Phase 3 — Integration** | Inject HTFContext into pipeline. Pass-through only, no blocking logic. | Minimal (context available but inert) |
| **Phase 4 — Scoring Influence** | Enable HTF scoring adjustments (bonuses/penalties). | Low (scoring modified, no hard blocks) |
| **Phase 5 — Full Authority Mode** | Enable blocking rules (directional gate, structural gate). | Medium (trades can be blocked by HTF) |

Each phase is independently deployable and reversible.

#### 10.2 Compatibility Bridge

System must support simultaneously:
- M5-only legacy mode (`MTF_ENABLED=False`)
- Partial HTF enablement (individual layers on/off)
- Runtime toggle switching (config change → immediate effect next cycle)

#### 10.3 Temporary Adapter Layer

```
Legacy Pipeline (unchanged)
      ↓
HTF Adapter (no-op when MTF_ENABLED=False)
      ↓
Future full HTF system (scoring + blocking when enabled)
```

The adapter is `apply_htf_constraints()` — when HTFContext is None or empty, it returns a zero-influence HTFInfluence object (no-op).

#### 10.4 Rollback Strategy

At any phase:
- Set `MTF_ENABLED = False`
- Revert to M5-only behaviour immediately
- No data loss occurs (cache is ephemeral, not persisted)
- No EngineState corruption possible (HTF never writes to EngineState)

#### 10.5 Incremental Implementation Ordering

Always implement in this order:
1. Cache infrastructure (types + TimeframeCache)
2. Analyzers (H4, H1, M15 — pure functions)
3. Context construction (HTFContext building)
4. Scoring influence (bonuses/penalties)
5. Blocking logic (directional + structural gates)
6. Observability (logging, metrics, audit integration)
7. Optimisation (fetch scheduling, staleness tuning)

#### 10.6 Risk Mitigation

Architectural guarantees that make migration safe:
- No direct EngineState mutation from HTF code
- No MT5 API changes (uses existing `copy_rates_closed`)
- No concurrency introduction (single-threaded model preserved)
- Deterministic execution preserved (same inputs → same outputs)
- No modification to existing pipeline stage files
- All new code in isolated `core/timeframes/` directory


### 11. Testing Strategy

#### 11.1 Unit Testing

| Component | Tests |
|-----------|-------|
| TimeframeCache | `update_if_needed` logic, new bar detection, staleness detection, failure retention |
| H4 Regime Analyzer | Classification correctness for trending/ranging/volatile candle sets |
| H1 Bias Analyzer | Bias direction + confidence for bullish/bearish/neutral structures |
| M15 Structure Analyzer | Quality score for various S/R configurations |
| HTFContext construction | Correct assembly from cached snapshots, None handling |
| HTFInfluence generation | `apply_htf_constraints` produces correct bonuses/penalties/blocks |
| Configuration validation | Invalid configs rejected, safe defaults applied |

#### 11.2 Integration Testing

| Scenario | Validation |
|----------|-----------|
| Full M5 cycle with HTF enabled | Pipeline produces valid UnifiedDecision with HTF influence |
| Fallback mode activation | MTF_ENABLED=False produces identical output to legacy system |
| Cache refresh behaviour | New H4 bar triggers refresh, stale H1 triggers re-fetch |
| HTF block propagation | Directional contradiction blocks trade, reason logged |
| Scoring augmentation | HTF bonus/penalty correctly modifies final score |

#### 11.3 Runtime Simulation

Simulate edge conditions:
- **Slow markets:** No new H4 bar for 12+ hours (staleness handling)
- **Missing feeds:** MT5 returns None for H1 candles (degraded mode)
- **Volatile regime shifts:** H4 transitions TRENDING→VOLATILE mid-session
- **Rapid M15 changes:** Structure quality oscillates around threshold

#### 11.4 Authority Validation Tests

Ensure hierarchical rules hold:
- H4 RANGING penalty applies regardless of H1/M15 state
- H1 contradiction blocks trade even when M5 score is high (below threshold)
- H1 contradiction does NOT block when M5 score exceeds override threshold
- M15 structural gate blocks regardless of H1 alignment
- No lower TF can override a higher TF constraint

#### 11.5 Synchronisation Tests

- Multi-symbol execution: each symbol has independent cache
- Cache independence: updating EURUSD cache does not affect GBPUSD
- No cross-contamination: HTFContext for one symbol never leaks to another
- Deterministic ordering: same symbol list → same processing order

#### 11.6 Stale Data Tests

- Verify 3× timeframe staleness rule triggers correctly
- Verify stale snapshot is still usable (not discarded)
- Verify degraded scoring behaviour when stale
- Verify fresh fetch attempted after staleness detected

#### 11.7 Recovery Tests

- MT5 disconnect → reconnect → cache invalidation → rebuild sequence
- Partial snapshot restoration (H4 succeeds, H1 fails)
- Full rebuild sequence timing (all TFs fetched in priority order)
- Cold start behaviour (first cycle, no cached data)

#### 11.8 Test Hierarchy

```
Unit Tests (per-component, fast, no MT5)
    ↓
Integration Tests (pipeline end-to-end, mocked MT5)
    ↓
Simulation Tests (edge cases, timing, degradation)
    ↓
Recovery Tests (reconnect, rebuild, failover)
    ↓
Stress Tests (long-duration, many symbols, rapid TF changes)
```

#### 11.9 Runtime Assertions (Property-Based)

```python
# Correctness properties verified via Hypothesis:

# 1. HTFContext immutability
@given(...)
def test_htf_context_never_mutated(candles):
    ctx = cache.get_htf_context(price)
    process_bar(..., htf_context=ctx)
    assert ctx == cache.get_htf_context(price)  # unchanged

# 2. EngineState isolation
@given(...)
def test_engine_state_not_contaminated(candles):
    state_before = copy(engine_state)
    apply_htf_constraints(htf_context, signal, bias, config)
    assert engine_state == state_before  # no mutation

# 3. Deterministic scoring
@given(...)
def test_same_inputs_same_output(htf_context, signal, bias):
    r1 = apply_htf_constraints(htf_context, signal, bias, config)
    r2 = apply_htf_constraints(htf_context, signal, bias, config)
    assert r1 == r2  # deterministic

# 4. Bounded influence
@given(scores=st.floats(-1, 1))
def test_htf_influence_bounded(scores):
    influence = apply_htf_constraints(...)
    assert -10.0 <= influence.score_adjustment <= 10.0
    assert influence.min_score_adjustment >= 0.0
```


### 12. Module Boundaries and Dependencies

#### 12.1 Module Map

```
core/timeframes/
├── cache.py          → TimeframeCache (data ownership, fetch scheduling)
├── types.py          → RegimeSnapshot, BiasSnapshot, StructureSnapshot, HTFContext, HTFInfluence
├── h4_regime.py      → analyze_regime() pure function
├── h1_bias.py        → analyze_bias() pure function
├── m15_structure.py  → analyze_structure() pure function
├── m1_refinement.py  → refinement context provider (optional)
├── integration.py    → apply_htf_constraints() (scoring/gating logic)
└── __init__.py       → public API exports
```

#### 12.2 Responsibility Boundaries

| Module | Responsibility |
|--------|---------------|
| `cache.py` | Data ownership — fetch scheduling, staleness, snapshot storage |
| `types.py` | Immutable views — all frozen dataclasses and enums |
| `integration.py` | HTF logic — constraint application, scoring influence |
| `scoring_engine.py` | Decision adjustment — accepts HTF parameters, modifies score/threshold |
| `live_scanner.py` | Orchestration — calls cache update, builds context, passes to pipeline |

#### 12.3 Dependency Rules

```
cache.py → types.py → integration.py → engine.py → decision_engine.py
```

**No reverse dependencies allowed.**

| Module | May Import From | Must NOT Import From |
|--------|----------------|---------------------|
| `types.py` | `strategy.signals` (Side enum only) | anything in `core/timeframes/` |
| `cache.py` | `types.py`, `h4_regime.py`, `h1_bias.py`, `m15_structure.py`, `data.mt5_data` | `integration.py`, `engine.py` |
| `h4_regime.py` | `types.py`, `data.mt5_data` | `cache.py`, `integration.py` |
| `h1_bias.py` | `types.py`, `data.mt5_data` | `cache.py`, `integration.py` |
| `m15_structure.py` | `types.py`, `data.mt5_data` | `cache.py`, `integration.py` |
| `integration.py` | `types.py`, `strategy.signals`, `core.config` | `cache.py`, `engine.py` |
| `engine.py` | `integration.py`, `types.py` | `cache.py` (cache accessed via live_scanner only) |


### 13. Future Extensibility

#### 13.1 Additional Timeframes

The architecture supports adding higher timeframes (H8, D1, W1) as additional authority layers:
- Each new timeframe follows the same pattern: analyzer → snapshot → cache entry
- New layers slot into the hierarchy above H4 (constrain H4, which constrains H1, etc.)
- Plug-in snapshots per timeframe via the existing `_CacheEntry` structure

#### 13.2 Multi-Symbol Scaling

- Each symbol has an independent TimeframeCache instance
- No shared state between symbols
- Adding new symbols requires no architectural changes

#### 13.3 Distributed Evaluation

The architecture supports future parallelisation:
- Per-symbol processing is already independent (no cross-symbol state)
- TimeframeCache instances could run in separate threads/processes
- HTFContext is immutable (safe to pass across boundaries)

#### 13.4 Strategy Plug-ins

The scoring engine can accept external modifiers:
- `apply_htf_constraints()` is a pure function — additional constraint functions can be composed
- HTFInfluence is additive (multiple influence objects could be merged)
- New scoring plug-ins follow the same `score_adjustment` + `min_score_adjustment` pattern

#### 13.5 Portfolio-Level Authority

Future extension above the per-symbol hierarchy:
- Cross-symbol regime detection (correlated pairs)
- Correlation-aware bias (if EURUSD bullish, constrain USDCHF bearish)
- Portfolio-level exposure limits informed by HTF regime

#### 13.6 ML / AI Augmentation

Possible additions that fit the existing architecture:
- Regime classification models (replace rule-based H4 analyzer)
- Predictive bias layers (augment H1 bias with ML confidence)
- Adaptive threshold tuning (adjust MTF_H1_CONTRADICTION_THRESHOLD based on historical performance)

All ML extensions would produce the same snapshot types (RegimeSnapshot, BiasSnapshot) — downstream logic unchanged.

