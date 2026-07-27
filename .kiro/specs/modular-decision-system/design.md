# Technical Design Document

## Overview

This document describes the technical design for the Modular Decision System (Q1–Q7), a parallel evaluation architecture that coexists with the existing MK1 pipeline (`core/engine.py` → `core/pipeline/*`). The new system lives entirely within `core/questions/` and is activated via a runtime config flag. It consumes the same `Candle` arrays and tick data already flowing through the system, applies seven independent evaluation modules, aggregates their outputs using strategy-profile-specific weights, and produces a `TradeDecision` that maps back to the existing `Decision` wire format for downstream execution.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        Runtime Loop                              │
│  (core/runtime/live_scanner.py or replay_runtime.py)            │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌─── Config Flag: DECISION_SYSTEM = "PIPELINE" | "QUESTIONS" ──┐
│  │                                                               │
│  │  IF "PIPELINE":                                               │
│  │    core/engine.py → process_bar() → UnifiedDecision           │
│  │                                                               │
│  │  IF "QUESTIONS":                                              │
│  │    core/questions/orchestrator.py → evaluate_bar()             │
│  │      → MarketDataAdapter.from_bar_context(candles, bid, ask)  │
│  │      → Q1..Q7 modules (parallel-safe, stateless)              │
│  │      → Decision_Aggregator (weighted sum + threshold)         │
│  │      → TradeDecision → adapt_to_legacy_decision()             │
│  │                                                               │
│  └───────────────────────────────────────────────────────────────┘
│                                                                 │
│  Execution layer receives Decision (same wire format either way) │
└─────────────────────────────────────────────────────────────────┘
```

## Design Principles

1. **Zero modification to existing pipeline** — `core/pipeline/*` files remain untouched.
2. **Same data contract** — Consumes `list[Candle]`, `bid`, `ask`, `symbol` already available at the call site.
3. **Stateless modules** — Each Q module is a pure function of `MarketData`; no cross-module dependencies.
4. **Fail-safe isolation** — A crashing module returns neutral/0.0; orchestrator continues.
5. **Profile-driven aggregation** — Weights and thresholds are declarative data, not code.
6. **Full traceability** — Every evaluation produces a structured report with per-module breakdown.
7. **Gradual migration** — Config flag allows instant rollback to existing pipeline.


## Project Structure

```
core/questions/
├── __init__.py              # Public API: Orchestrator, load_profile, ModuleOutput
├── orchestrator.py          # Central coordinator: invokes modules, delegates to aggregator
├── aggregator.py            # Decision_Aggregator: weighted sum + threshold logic
├── types.py                 # MarketData, ModuleOutput, TradeDecision, DirectionalState
├── adapter.py               # MarketDataAdapter: Candle[] + tick → MarketData
├── modules/
│   ├── __init__.py          # Module registry (auto-discovery)
│   ├── base.py              # Abstract QuestionModule interface
│   ├── q1_trend.py          # Higher Timeframe Trend
│   ├── q2_levels.py         # Key Levels (support/resistance)
│   ├── q3_liquidity.py      # Liquidity / sweep detection
│   ├── q4_confirmation.py   # Candlestick confirmation
│   ├── q5_momentum.py       # Momentum strength/direction
│   ├── q6_timing.py         # Session timing / kill zones
│   └── q7_risk.py           # Volatility / spread / adverse conditions
└── profiles/
    ├── __init__.py           # Profile loader + validator
    ├── scalping.py           # Scalping profile (weights + thresholds)
    ├── intraday.py           # Intraday profile (weights + thresholds)
    └── swing.py              # Swing profile (weights + thresholds)
```

## Data Types

### MarketData (Input to all Q modules)

```python
@dataclass(frozen=True)
class MarketData:
    """Immutable snapshot consumed by all Question Modules."""
    
    symbol: str
    candles: list[Candle]          # Full history window (300 bars from config.CANDLE_COUNT)
    closed_i: int                   # Index of last closed bar
    bid: float
    ask: float
    current_time_s: float           # Unix timestamp of evaluation moment
    
    # Derived convenience (computed once by adapter, not by each module)
    spread: float                   # ask - bid
    atr_14: float                   # 14-period ATR at closed_i
    recent_highs: list[float]       # Last 20 bar highs
    recent_lows: list[float]        # Last 20 bar lows
    ema_50: float                   # 50-period EMA at closed_i
    ema_10: float                   # 10-period EMA at closed_i (setup MA)
    
    @property
    def closed_bar(self) -> Candle:
        return self.candles[self.closed_i]
    
    @property
    def lookback(self) -> list[Candle]:
        """Last 20 closed bars (or fewer if not available)."""
        start = max(0, self.closed_i - 19)
        return self.candles[start:self.closed_i + 1]
```

### ModuleOutput (Return from each Q module)

```python
@dataclass(frozen=True)
class ModuleOutput:
    """Structured result from a single Question Module evaluation."""
    
    module_id: str                  # e.g. "q1_trend", "q2_levels"
    score: float                    # Normalized: -1.0 to +1.0
    directional_state: DirectionalState  # BULLISH / BEARISH / NEUTRAL
    confidence: float               # 0.0–1.0 (how certain the module is)
    reasoning: str                  # Human-readable explanation for audit
```

### DirectionalState (Enum)

```python
class DirectionalState(Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"
```

### TradeDecision (Orchestrator output)

```python
@dataclass(frozen=True)
class TradeDecision:
    """Final aggregated decision from the Q-system."""
    
    action: TradeAction             # ENTER_LONG / ENTER_SHORT / NO_TRADE
    weighted_sum: float             # Final aggregated score
    profile_name: str               # Active strategy profile
    module_results: dict[str, ModuleOutput]  # Per-module breakdown
    weighted_scores: dict[str, float]        # Per-module weighted contribution
    excluded_modules: list[str]     # Modules that failed/timed out
    timestamp_s: float              # Evaluation timestamp
    symbol: str

class TradeAction(Enum):
    ENTER_LONG = "enter_long"
    ENTER_SHORT = "enter_short"
    NO_TRADE = "no_trade"
```

### StrategyProfile (Configuration)

```python
@dataclass(frozen=True)
class StrategyProfile:
    """Declarative strategy configuration — no logic, only parameters."""
    
    name: str                       # "scalping" | "intraday" | "swing"
    weights: dict[str, float]       # module_id → weight (sum must equal 1.0)
    long_threshold: float           # Weighted sum must exceed this for ENTER_LONG
    short_threshold: float          # Weighted sum must be below this for ENTER_SHORT (negative)
    pip_distance_tolerance: float   # Price distance sensitivity
    strictness: int                 # 1–5 (passed to modules as sensitivity hint)
    preferred_timeframe: str        # Timeframe bias identifier
```


## Component Design

### MarketDataAdapter (`core/questions/adapter.py`)

Bridges the existing data layer to the `MarketData` contract. Computed once per bar evaluation, shared across all seven modules.

```python
class MarketDataAdapter:
    """Transforms raw Candle[] + tick into the enriched MarketData snapshot."""
    
    @staticmethod
    def from_bar_context(
        candles: list[Candle],
        closed_i: int,
        symbol: str,
        bid: float,
        ask: float,
        current_time_s: float,
    ) -> MarketData:
        """
        Compute derived fields (ATR, EMA, recent highs/lows) once.
        All computations are pure — no MT5 calls, no side effects.
        """
        ...
```

**Derived field computation:**
- `spread`: `ask - bid` (trivial)
- `atr_14`: Standard 14-period ATR using Wilder smoothing over `candles[:closed_i+1]`
- `ema_50` / `ema_10`: Exponential moving averages at `closed_i`
- `recent_highs` / `recent_lows`: Last 20 bar high/low values

**Invariants:**
- Never calls MT5 or any external service
- Returns `MarketData` with `atr_14 = 0.0` if fewer than 15 candles available
- All fields are deterministic given the same input

### QuestionModule Base (`core/questions/modules/base.py`)

```python
from abc import ABC, abstractmethod

class QuestionModule(ABC):
    """Abstract base for all Q1–Q7 evaluation modules."""
    
    @property
    @abstractmethod
    def module_id(self) -> str:
        """Canonical identifier: 'q1_trend', 'q2_levels', etc."""
        ...
    
    @abstractmethod
    def evaluate(self, data: MarketData, profile: StrategyProfile) -> ModuleOutput:
        """
        Evaluate market conditions for this module's concern.
        
        Contract:
        - MUST be stateless (no instance variables mutated)
        - MUST return score in [-1.0, +1.0]
        - MUST NOT raise exceptions (return neutral on error)
        - MUST NOT import or reference other QuestionModules
        - SHOULD complete within 100ms for live trading
        """
        ...
    
    def safe_evaluate(self, data: MarketData, profile: StrategyProfile) -> ModuleOutput:
        """Wrapper that catches exceptions and returns neutral."""
        try:
            result = self.evaluate(data, profile)
            # Clamp score to valid range
            clamped_score = max(-1.0, min(1.0, result.score))
            if clamped_score != result.score:
                return ModuleOutput(
                    module_id=self.module_id,
                    score=clamped_score,
                    directional_state=result.directional_state,
                    confidence=result.confidence,
                    reasoning=f"{result.reasoning} [score clamped from {result.score:.3f}]",
                )
            return result
        except Exception as exc:
            logger.warning(
                "[Q_MODULE_FAULT] module=%s error=%s", self.module_id, exc
            )
            return ModuleOutput(
                module_id=self.module_id,
                score=0.0,
                directional_state=DirectionalState.NEUTRAL,
                confidence=0.0,
                reasoning=f"module_exception: {type(exc).__name__}",
            )
```

### Module Registry (`core/questions/modules/__init__.py`)

```python
"""Auto-discovery and registration of Q modules."""

from core.questions.modules.base import QuestionModule
from core.questions.modules.q1_trend import Q1Trend
from core.questions.modules.q2_levels import Q2Levels
from core.questions.modules.q3_liquidity import Q3Liquidity
from core.questions.modules.q4_confirmation import Q4Confirmation
from core.questions.modules.q5_momentum import Q5Momentum
from core.questions.modules.q6_timing import Q6Timing
from core.questions.modules.q7_risk import Q7Risk

ALL_MODULES: list[QuestionModule] = [
    Q1Trend(),
    Q2Levels(),
    Q3Liquidity(),
    Q4Confirmation(),
    Q5Momentum(),
    Q6Timing(),
    Q7Risk(),
]

MODULE_MAP: dict[str, QuestionModule] = {m.module_id: m for m in ALL_MODULES}
```

### Orchestrator (`core/questions/orchestrator.py`)

```python
class Orchestrator:
    """
    Central coordinator: invokes all Q modules, collects outputs,
    delegates to Decision_Aggregator, returns TradeDecision.
    """
    
    def __init__(self, profile: StrategyProfile) -> None:
        self._profile = profile
        self._aggregator = DecisionAggregator()
        self._modules = ALL_MODULES
        self._logger = logging.getLogger("core.questions.orchestrator")
    
    def evaluate_bar(
        self,
        candles: list[Candle],
        closed_i: int,
        symbol: str,
        bid: float,
        ask: float,
        current_time_s: float,
    ) -> TradeDecision:
        """
        Full evaluation cycle for one closed bar.
        
        Flow:
        1. Build MarketData via adapter
        2. Invoke all modules (safe_evaluate)
        3. Delegate to aggregator with profile weights/thresholds
        4. Emit decision report if logging enabled
        5. Return TradeDecision
        """
        # Step 1: Adapt raw data
        market_data = MarketDataAdapter.from_bar_context(
            candles=candles,
            closed_i=closed_i,
            symbol=symbol,
            bid=bid,
            ask=ask,
            current_time_s=current_time_s,
        )
        
        # Step 2: Evaluate all modules
        results: dict[str, ModuleOutput] = {}
        for module in self._modules:
            output = module.safe_evaluate(market_data, self._profile)
            results[module.module_id] = output
        
        # Step 3: Aggregate
        decision = self._aggregator.aggregate(
            results=results,
            profile=self._profile,
            symbol=symbol,
            timestamp_s=current_time_s,
        )
        
        # Step 4: Emit report
        if getattr(config, "Q_MODULE_DEBUG_LOGS", False):
            self._emit_decision_report(decision)
        
        return decision
```

### Decision Aggregator (`core/questions/aggregator.py`)

```python
class DecisionAggregator:
    """
    Combines weighted module scores into a final TradeDecision.
    
    Algorithm:
    1. For each module with a valid result, multiply score × weight
    2. If any modules are excluded (fault/timeout), re-normalize remaining weights
    3. Sum weighted scores → weighted_sum
    4. Compare weighted_sum against profile thresholds
    5. Produce TradeAction
    """
    
    def aggregate(
        self,
        results: dict[str, ModuleOutput],
        profile: StrategyProfile,
        symbol: str,
        timestamp_s: float,
    ) -> TradeDecision:
        excluded: list[str] = []
        active_weights: dict[str, float] = {}
        
        for module_id, weight in profile.weights.items():
            if module_id in results and results[module_id].confidence > 0.0:
                active_weights[module_id] = weight
            else:
                excluded.append(module_id)
        
        # Re-normalize if modules excluded
        weight_sum = sum(active_weights.values())
        if weight_sum > 0 and weight_sum != 1.0:
            active_weights = {k: v / weight_sum for k, v in active_weights.items()}
        
        # Compute weighted sum
        weighted_scores: dict[str, float] = {}
        total = 0.0
        for module_id, weight in active_weights.items():
            clamped = max(-1.0, min(1.0, results[module_id].score))
            contribution = clamped * weight
            weighted_scores[module_id] = contribution
            total += contribution
        
        # Threshold comparison
        if total > profile.long_threshold:
            action = TradeAction.ENTER_LONG
        elif total < profile.short_threshold:
            action = TradeAction.ENTER_SHORT
        else:
            action = TradeAction.NO_TRADE
        
        return TradeDecision(
            action=action,
            weighted_sum=total,
            profile_name=profile.name,
            module_results=results,
            weighted_scores=weighted_scores,
            excluded_modules=excluded,
            timestamp_s=timestamp_s,
            symbol=symbol,
        )
```


## Module Specifications

### Q1: Higher Timeframe Trend (`q1_trend.py`)

**Responsibility:** Evaluate directional bias from higher timeframe structure.

**Inputs used:** `candles`, `closed_i`, `ema_50`, `ema_10`, `recent_highs`, `recent_lows`

**Logic:**
- Detect higher-high / higher-low sequences (bullish structure)
- Detect lower-high / lower-low sequences (bearish structure)
- EMA slope direction (50-period)
- Price position relative to EMA-50
- Pullback depth relative to recent swing

**Score mapping:**
- Strong bullish structure (4+ signals) → +0.8 to +1.0
- Moderate bullish → +0.3 to +0.7
- Neutral/ranging → -0.2 to +0.2
- Moderate bearish → -0.7 to -0.3
- Strong bearish structure → -1.0 to -0.8

### Q2: Key Levels (`q2_levels.py`)

**Responsibility:** Evaluate proximity and reaction to significant support/resistance.

**Inputs used:** `candles`, `closed_i`, `recent_highs`, `recent_lows`, `atr_14`

**Logic:**
- Identify swing highs/lows from last 50 bars as key levels
- Measure distance from current price to nearest level (normalized by ATR)
- Count touches/reactions at level (level strength)
- Detect rejection wicks at level
- Assess reaction strength (body-to-wick ratio at level)

**Score mapping:**
- At strong support with rejection → +0.7 to +1.0 (bullish bounce expected)
- At strong resistance with rejection → -0.7 to -1.0 (bearish rejection expected)
- Near level but no reaction → ±0.3
- Far from any level → 0.0 (neutral)

### Q3: Liquidity (`q3_liquidity.py`)

**Responsibility:** Detect liquidity sweeps and volume characteristics.

**Inputs used:** `candles`, `closed_i`, `recent_highs`, `recent_lows`

**Logic:**
- Detect sweep of previous swing high (wick above, close below)
- Detect sweep of previous swing low (wick below, close above)
- Measure sweep distance in ATR multiples
- Check if price closed back inside prior range
- Volume spike detection (tick_volume vs 20-bar average)

**Score mapping:**
- Bullish sweep (swept low, closed back inside) → +0.6 to +1.0
- Bearish sweep (swept high, closed back inside) → -0.6 to -1.0
- Partial sweep without confirmation → ±0.3
- No sweep activity → 0.0

### Q4: Confirmation (`q4_confirmation.py`)

**Responsibility:** Evaluate candlestick pattern confirmation and price action signals.

**Inputs used:** `candles`, `closed_i`

**Logic:**
- Leverage existing `patterns/registry.py` for pattern detection
- Map detected patterns to directional score
- Weight by pattern confidence (from Signal.confidence)
- Check candle body percentage (strong close)
- Consecutive directional closes

**Score mapping:**
- Strong bullish pattern (engulfing, morning star) with high confidence → +0.7 to +1.0
- Weak bullish pattern (hammer) → +0.3 to +0.5
- No pattern → 0.0
- Weak bearish pattern → -0.3 to -0.5
- Strong bearish pattern → -0.7 to -1.0

**Integration note:** This module imports from `patterns.registry` (read-only) to reuse existing pattern detection. This is the ONLY cross-package import allowed for Q modules.

### Q5: Momentum (`q5_momentum.py`)

**Responsibility:** Evaluate strength and direction of current price momentum.

**Inputs used:** `candles`, `closed_i`, `atr_14`

**Logic:**
- Average body size vs ATR (normalized momentum)
- Consecutive directional closes count
- Body size trend (increasing = accelerating)
- Opposing wick pressure (high opposing wicks = weakening)
- Rate of change over 5 and 10 bars

**Score mapping:**
- Strong bullish momentum (4+ signals) → +0.7 to +1.0
- Moderate bullish → +0.3 to +0.6
- Stalling/neutral → -0.1 to +0.1
- Moderate bearish → -0.6 to -0.3
- Strong bearish momentum → -1.0 to -0.7

### Q6: Timing (`q6_timing.py`)

**Responsibility:** Evaluate session timing and time-of-day suitability.

**Inputs used:** `current_time_s`

**Logic:**
- Determine current session (London, New York, Asian, overlap)
- Kill zone detection (London open: 07:00–09:00 UTC, NY open: 12:00–14:00 UTC)
- Dead zone detection (Asian range: 22:00–05:00 UTC for majors)
- Day-of-week filter (Friday afternoon risk)
- Proximity to session boundaries

**Score mapping:**
- Kill zone (London/NY open overlap) → +0.8 to +1.0
- Active session (London or NY body) → +0.4 to +0.7
- Session transition → +0.1 to +0.3
- Asian session (for major pairs) → -0.3 to 0.0
- Dead zone / Friday close → -0.5 to -1.0

**Note:** Score is directionally neutral (magnitude only, no bullish/bearish). DirectionalState always NEUTRAL for timing. The score acts as a confidence multiplier.

### Q7: Risk (`q7_risk.py`)

**Responsibility:** Evaluate current risk conditions (volatility, spread, adverse potential).

**Inputs used:** `spread`, `atr_14`, `candles`, `closed_i`, `current_time_s`

**Logic:**
- Spread as percentage of ATR (excessive spread = unfavorable)
- Current volatility vs historical average (too high = dangerous, too low = no opportunity)
- Recent adverse movement detection (sharp moves against potential entry)
- Implied risk-reward feasibility (can a 2:1 RR be achieved given current ATR?)
- Consecutive same-direction bars (overextension risk)

**Score mapping:**
- Favorable conditions (tight spread, moderate vol, good RR potential) → +0.6 to +1.0
- Acceptable conditions → +0.2 to +0.5
- Marginal conditions → -0.2 to +0.1
- Unfavorable (wide spread or extreme vol) → -0.5 to -0.3
- Dangerous (extreme spread, news spike vol) → -1.0 to -0.6

**Note:** Like Q6, this is directionally neutral. DirectionalState always NEUTRAL. Score represents risk favorability.


## Strategy Profiles

### Profile Structure

Each profile is a frozen dataclass instance defined in its own file under `core/questions/profiles/`.

### Scalping Profile (`profiles/scalping.py`)

```python
SCALPING = StrategyProfile(
    name="scalping",
    weights={
        "q1_trend": 0.10,       # Less important — scalps work in any trend
        "q2_levels": 0.15,      # Key levels matter for precision entries
        "q3_liquidity": 0.15,   # Sweeps are prime scalp triggers
        "q4_confirmation": 0.25, # Pattern confirmation is critical
        "q5_momentum": 0.15,    # Need momentum for quick moves
        "q6_timing": 0.10,      # Session matters but less critical
        "q7_risk": 0.10,        # Spread/vol awareness
    },
    long_threshold=0.35,         # Lower bar — more trades, tighter management
    short_threshold=-0.35,
    pip_distance_tolerance=0.0003,  # 3 pips for FX majors
    strictness=2,                   # Relaxed — accept more setups
    preferred_timeframe="M5",
)
```

### Intraday Profile (`profiles/intraday.py`)

```python
INTRADAY = StrategyProfile(
    name="intraday",
    weights={
        "q1_trend": 0.20,       # Trend alignment important
        "q2_levels": 0.15,      # Key levels for entry precision
        "q3_liquidity": 0.10,   # Sweeps helpful but not required
        "q4_confirmation": 0.20, # Pattern confirmation needed
        "q5_momentum": 0.15,    # Momentum for follow-through
        "q6_timing": 0.10,      # Session timing matters
        "q7_risk": 0.10,        # Risk conditions
    },
    long_threshold=0.40,         # Moderate selectivity
    short_threshold=-0.40,
    pip_distance_tolerance=0.0005,  # 5 pips
    strictness=3,                   # Balanced
    preferred_timeframe="M15",
)
```

### Swing Profile (`profiles/swing.py`)

```python
SWING = StrategyProfile(
    name="swing",
    weights={
        "q1_trend": 0.25,       # Trend is paramount for swing
        "q2_levels": 0.20,      # Must be at significant levels
        "q3_liquidity": 0.10,   # Sweeps less relevant at swing scale
        "q4_confirmation": 0.15, # Confirmation needed but less weight
        "q5_momentum": 0.10,    # Momentum less critical (patience)
        "q6_timing": 0.05,      # Timing least important for swing
        "q7_risk": 0.15,        # Risk management critical for larger stops
    },
    long_threshold=0.50,         # High bar — fewer but higher quality trades
    short_threshold=-0.50,
    pip_distance_tolerance=0.0010,  # 10 pips
    strictness=5,                   # Most strict
    preferred_timeframe="H1",
)
```

### Profile Validation

```python
def validate_profile(profile: StrategyProfile) -> list[str]:
    """
    Validate profile constraints. Returns list of error messages (empty = valid).
    
    Checks:
    1. Exactly 7 weights defined (one per module)
    2. Each weight in [0.0, 1.0]
    3. Weights sum to 1.0 (tolerance: ±0.001)
    4. long_threshold in [0.0, 10.0]
    5. short_threshold in [-10.0, 0.0]
    6. strictness in [1, 5]
    7. pip_distance_tolerance >= 0.0
    """
    ...
```

## Pipeline Integration

### Runtime Integration Point

The integration happens in `core/runtime/live_scanner.py` (and `replay_runtime.py`), at the point where `process_bar()` is currently called. A new dispatcher function selects the evaluation path:

```python
# In core/runtime/live_scanner.py (conceptual — actual edit is minimal)

from core import config

def _evaluate_bar(candles, closed_i, symbol, bid, ask, now_s, risk_mgr, state):
    """Dispatch to either legacy pipeline or Q-system based on config."""
    
    decision_system = getattr(config, "DECISION_SYSTEM", "PIPELINE")
    
    if decision_system == "QUESTIONS":
        from core.questions import Orchestrator, load_profile
        profile = load_profile(getattr(config, "Q_STRATEGY_PROFILE", "intraday"))
        orch = Orchestrator(profile)
        trade_decision = orch.evaluate_bar(
            candles=candles,
            closed_i=closed_i,
            symbol=symbol,
            bid=bid,
            ask=ask,
            current_time_s=now_s,
        )
        # Adapt to legacy Decision format for execution layer
        return adapt_trade_decision_to_unified(trade_decision, candles, closed_i, symbol, bid, ask, now_s, config)
    else:
        # Existing pipeline (default)
        from core.engine import process_bar
        return process_bar(
            candles=candles,
            closed_i=closed_i,
            symbol=symbol,
            config=config,
            risk=risk_mgr,
            state=state,
            bid=bid,
            ask=ask,
            now_s=now_s,
        )
```

### Legacy Decision Adapter

Maps `TradeDecision` → `UnifiedDecision` so the execution layer receives the same wire format:

```python
def adapt_trade_decision_to_unified(
    td: TradeDecision,
    candles: list[Candle],
    closed_i: int,
    symbol: str,
    bid: float,
    ask: float,
    current_time_s: float,
    config_module,
) -> UnifiedDecision:
    """
    Bridge Q-system output to existing execution contract.
    
    Mapping:
    - ENTER_LONG → Decision(should_trade=True, signal=Signal(side=BUY, ...))
    - ENTER_SHORT → Decision(should_trade=True, signal=Signal(side=SELL, ...))
    - NO_TRADE → Decision(should_trade=False, reason=...)
    """
    bar_ev = BarEvaluationContext(
        candles=candles,
        closed_i=closed_i,
        symbol=symbol,
        bid=bid,
        ask=ask,
        current_time_s=current_time_s,
        config_module=config_module,
    )
    
    if td.action == TradeAction.NO_TRADE:
        decision = Decision(
            should_trade=False,
            reason=f"q_system_no_trade (score={td.weighted_sum:.3f})",
            signal=None,
            intent=None,
            score=int(td.weighted_sum * 10),
        )
    else:
        side = Side.BUY if td.action == TradeAction.ENTER_LONG else Side.SELL
        # Find strongest confirming pattern from Q4 reasoning
        pattern_name = _extract_pattern_from_q4(td.module_results.get("q4_confirmation"))
        signal = Signal(
            pattern=pattern_name or "Q_SYSTEM_SIGNAL",
            side=side,
            bar_index=closed_i,
            bar_time=candles[closed_i].time,
            confidence=abs(td.weighted_sum),
        )
        decision = Decision(
            should_trade=True,
            reason=f"q_system_entry ({td.profile_name}, score={td.weighted_sum:.3f})",
            signal=signal,
            intent=None,  # Intent built downstream by existing risk layer
            bias=side,
            score=int(td.weighted_sum * 10),
        )
    
    return UnifiedDecision(
        bar_context=bar_ev,
        last_completed_stage="q_system_complete",
        decision=decision,
    )
```

### Configuration Additions to `core/config.py`

```python
# --- Modular Decision System (Q1–Q7) ---
DECISION_SYSTEM = "PIPELINE"          # "PIPELINE" (existing) or "QUESTIONS" (new Q-system)
Q_STRATEGY_PROFILE = "intraday"       # "scalping" | "intraday" | "swing"
Q_MODULE_TIMEOUT_SECONDS = 5.0        # Max time per module before exclusion
Q_MODULE_DEBUG_LOGS = False            # Already exists — reused for Q-system report logging
```


## Observability

### Decision Report Logging

When `Q_MODULE_DEBUG_LOGS = True` or at INFO level when `ESSENTIAL_LOGS = True`:

```
[Q_DECISION] symbol=EURUSD_SB profile=intraday action=NO_TRADE score=0.182
[Q_MODULES] q1_trend=+0.45(w=0.20) q2_levels=+0.12(w=0.15) q3_liquidity=0.00(w=0.10) 
            q4_confirmation=+0.30(w=0.20) q5_momentum=+0.22(w=0.15) q6_timing=+0.60(w=0.10) 
            q7_risk=+0.40(w=0.10)
[Q_THRESHOLD] weighted_sum=0.182 long_threshold=0.40 short_threshold=-0.40 result=NO_TRADE
```

### Dashboard Integration

The Q-system integrates with the existing `core/pipeline/dashboard.py` pattern:

```python
# Record Q-system cycle (parallel to record_cycle() for legacy pipeline)
dashboard.record_q_cycle()

# Record Q-system decisions for rejection analysis
if td.action == TradeAction.NO_TRADE:
    dashboard.record_q_rejection(reason="below_threshold", score=td.weighted_sum)
```

### Decision Audit Trail

When `DECISION_AUDIT_ENABLED = True` and the Q-system produces an actionable decision (ENTER_LONG or ENTER_SHORT), the audit trail captures:

```json
{
    "timestamp": 1716422400.0,
    "system": "questions",
    "symbol": "EURUSD_SB",
    "profile": "intraday",
    "action": "enter_long",
    "weighted_sum": 0.523,
    "modules": {
        "q1_trend": {"score": 0.65, "state": "bullish", "weight": 0.20, "contribution": 0.130},
        "q2_levels": {"score": 0.80, "state": "bullish", "weight": 0.15, "contribution": 0.120},
        "q3_liquidity": {"score": 0.40, "state": "bullish", "weight": 0.10, "contribution": 0.040},
        "q4_confirmation": {"score": 0.70, "state": "bullish", "weight": 0.20, "contribution": 0.140},
        "q5_momentum": {"score": 0.50, "state": "bullish", "weight": 0.15, "contribution": 0.075},
        "q6_timing": {"score": 0.60, "state": "neutral", "weight": 0.10, "contribution": 0.060},
        "q7_risk": {"score": -0.42, "state": "neutral", "weight": 0.10, "contribution": -0.042}
    },
    "excluded_modules": [],
    "thresholds": {"long": 0.40, "short": -0.40}
}
```

## Risk and Execution Alignment

### Risk Layer Integration

The Q-system does NOT replace the risk layer. After the Q-system produces a `TradeDecision` with `ENTER_LONG` or `ENTER_SHORT`:

1. The adapter creates a `Decision` with `should_trade=True`
2. The existing execution flow in `live_scanner.py` passes this to `RiskManager`
3. `RiskManager` performs:
   - Position sizing (FIXED or DYNAMIC mode)
   - SL/TP calculation via `risk/levels.py`
   - Exposure guard check via `risk/guards.py`
   - Drawdown guard check (if enabled)
4. If risk approves → `OrderIntent` is built → execution proceeds
5. If risk rejects → trade is blocked (same as existing pipeline)

**The Q-system is a SIGNAL GENERATOR, not an execution authority.** Risk and execution remain unchanged.

### Execution Safety

- The Q-system never calls `mt5.order_send()` directly
- The Q-system never modifies `EngineState`
- The Q-system never accesses open positions
- All execution safety (idempotency, pre-flight, DRY_RUN) remains in `execution/mt5_execution.py`

## State Management

### Statelessness Guarantee

The Q-system is fully stateless by design:

- No `EngineState` dependency (unlike the existing pipeline)
- No bias FSM, no lock timers, no decay counters
- Each `evaluate_bar()` call is independent of all previous calls
- The same inputs always produce the same outputs (deterministic)

**Implication:** The Q-system does not benefit from warm-start persistence. It evaluates each bar fresh from the candle history alone.

### EngineState Coexistence

When `DECISION_SYSTEM = "QUESTIONS"`:
- `EngineState` is still instantiated (for potential fallback to pipeline)
- `EngineState` is NOT updated by the Q-system
- If the user switches back to `DECISION_SYSTEM = "PIPELINE"`, EngineState resumes from its last persisted snapshot (if warm-start enabled)

## Testing Strategy

### Unit Tests (per module)

Each Q module gets its own test file: `tests/test_q1_trend.py`, `tests/test_q2_levels.py`, etc.

**Test patterns:**
```python
def test_q1_strong_bullish_structure(make_candle):
    """Higher highs + higher lows + EMA alignment → score > 0.7"""
    candles = [...]  # Synthetic bullish structure
    data = MarketDataAdapter.from_bar_context(candles, ...)
    result = Q1Trend().evaluate(data, INTRADAY)
    assert result.score >= 0.7
    assert result.directional_state == DirectionalState.BULLISH

def test_q1_insufficient_data_returns_neutral(make_candle):
    """Fewer than 2 candles → neutral/0.0"""
    candles = [make_candle(1, 1.0, 1.0, 1.0, 1.0)]
    data = MarketDataAdapter.from_bar_context(candles, ...)
    result = Q1Trend().evaluate(data, INTRADAY)
    assert result.score == 0.0
    assert result.directional_state == DirectionalState.NEUTRAL
```

### Property-Based Tests (Hypothesis)

```python
from hypothesis import given, strategies as st

@given(scores=st.lists(st.floats(min_value=-1.0, max_value=1.0), min_size=7, max_size=7))
def test_aggregator_weighted_sum_bounded(scores):
    """Weighted sum is always in [-1.0, +1.0] when all weights sum to 1.0."""
    results = {f"q{i+1}": ModuleOutput(f"q{i+1}", scores[i], DirectionalState.NEUTRAL, 1.0, "") 
               for i in range(7)}
    decision = aggregator.aggregate(results, INTRADAY, "TEST", 0.0)
    assert -1.0 <= decision.weighted_sum <= 1.0

@given(scores=st.lists(st.floats(min_value=-1.0, max_value=1.0), min_size=7, max_size=7))
def test_threshold_decision_consistency(scores):
    """If weighted_sum > long_threshold → ENTER_LONG; < short_threshold → ENTER_SHORT."""
    results = {f"q{i+1}": ModuleOutput(f"q{i+1}", scores[i], DirectionalState.NEUTRAL, 1.0, "")
               for i in range(7)}
    decision = aggregator.aggregate(results, INTRADAY, "TEST", 0.0)
    if decision.weighted_sum > INTRADAY.long_threshold:
        assert decision.action == TradeAction.ENTER_LONG
    elif decision.weighted_sum < INTRADAY.short_threshold:
        assert decision.action == TradeAction.ENTER_SHORT
    else:
        assert decision.action == TradeAction.NO_TRADE
```

### Integration Tests

```python
def test_full_orchestrator_with_real_candles():
    """End-to-end: candles → orchestrator → TradeDecision (no MT5 needed)."""
    candles = load_test_candles("EURUSD_SB_sample.json")
    orch = Orchestrator(INTRADAY)
    decision = orch.evaluate_bar(candles, len(candles)-2, "EURUSD_SB", 1.08500, 1.08520, time.time())
    assert isinstance(decision, TradeDecision)
    assert decision.action in (TradeAction.ENTER_LONG, TradeAction.ENTER_SHORT, TradeAction.NO_TRADE)

def test_legacy_adapter_produces_valid_unified_decision():
    """Q-system output adapts cleanly to UnifiedDecision for execution layer."""
    td = TradeDecision(action=TradeAction.ENTER_LONG, weighted_sum=0.55, ...)
    unified = adapt_trade_decision_to_unified(td, candles, ...)
    assert unified.decision.should_trade is True
    assert unified.decision.signal.side == Side.BUY
```

### Correctness Properties

1. **Bounded output:** For any valid `MarketData`, every module score is in `[-1.0, +1.0]`.
2. **Deterministic:** Same `MarketData` + same `StrategyProfile` → same `TradeDecision`.
3. **Fault isolation:** If any single module raises, the orchestrator still returns a valid `TradeDecision`.
4. **Weight normalization:** Aggregator weighted sum is always in `[-1.0, +1.0]` when weights sum to 1.0.
5. **Threshold consistency:** `weighted_sum > long_threshold` ↔ `ENTER_LONG`; `weighted_sum < short_threshold` ↔ `ENTER_SHORT`.
6. **Profile validity:** A profile with weights not summing to 1.0 is rejected at load time.
7. **No side effects:** Calling `evaluate_bar()` never modifies any external state (EngineState, config, MT5).

## Migration Path

### Phase 1: Shadow Mode (Current Design)
- Q-system runs alongside existing pipeline
- Config flag selects which system drives execution
- Both can be logged simultaneously for comparison

### Phase 2: Dual Evaluation (Future)
- Both systems evaluate every bar
- Decisions are compared in audit log
- Discrepancies flagged for analysis

### Phase 3: Q-System Primary (Future)
- Q-system becomes default (`DECISION_SYSTEM = "QUESTIONS"`)
- Existing pipeline available as fallback
- EngineState maintained for rollback capability

### Phase 4: Pipeline Retirement (Future)
- Existing pipeline removed after sufficient live validation
- EngineState simplified or removed
- Q-system becomes sole decision authority

## Constraints and Invariants

1. **No modification to `core/pipeline/*`** — The Q-system is additive only.
2. **No MT5 calls from Q modules** — All data arrives via `MarketData` adapter.
3. **No cross-module imports** — Q modules only import from `core/questions/types.py` and `core/questions/modules/base.py`. Exception: Q4 may import from `patterns/registry.py` (read-only).
4. **No mutable state** — All types are frozen dataclasses or produce new instances.
5. **Config-driven activation** — System is inert unless `DECISION_SYSTEM = "QUESTIONS"`.
6. **Execution layer unchanged** — `execution/mt5_execution.py` receives the same `Decision` format regardless of which system produced it.
7. **Risk layer unchanged** — `risk/manager.py` validates and sizes trades identically for both systems.
8. **Profile weights must sum to 1.0** — Enforced at load time; runtime never operates with invalid profiles.
