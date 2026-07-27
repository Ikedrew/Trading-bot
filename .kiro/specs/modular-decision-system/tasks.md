# Implementation Tasks

## Task Dependency Graph

```
Task 1 (Types + Base) 
  → Task 2 (MarketData Adapter)
  → Task 3 (Q1 Trend)
  → Task 4 (Q2 Levels)
  → Task 5 (Q3 Liquidity)
  → Task 6 (Q4 Confirmation)
  → Task 7 (Q5 Momentum)
  → Task 8 (Q6 Timing)
  → Task 9 (Q7 Risk)
  → Task 10 (Module Registry)
    → Task 11 (Strategy Profiles)
      → Task 12 (Decision Aggregator)
        → Task 13 (Orchestrator)
          → Task 14 (Legacy Adapter)
            → Task 15 (Runtime Integration)
              → Task 16 (Observability)
                → Task 17 (Integration Tests)
                  → Task 18 (Property-Based Tests)
```

## Tasks

### Task 1: Core Types and Base Module Interface

**Requirements:** R1, R2, R8

**Description:** Create the foundational type system and abstract base class for the modular decision system.

**Files to create:**
- `core/questions/__init__.py`
- `core/questions/types.py`
- `core/questions/modules/__init__.py` (empty placeholder)
- `core/questions/modules/base.py`
- `core/questions/profiles/__init__.py` (empty placeholder)

**Sub-tasks:**
1. Create `core/questions/` directory structure with `__init__.py`
2. Implement `DirectionalState` enum in `types.py` (BULLISH, BEARISH, NEUTRAL)
3. Implement `ModuleOutput` frozen dataclass in `types.py` (module_id, score, directional_state, confidence, reasoning)
4. Implement `TradeAction` enum in `types.py` (ENTER_LONG, ENTER_SHORT, NO_TRADE)
5. Implement `TradeDecision` frozen dataclass in `types.py` (action, weighted_sum, profile_name, module_results, weighted_scores, excluded_modules, timestamp_s, symbol)
6. Implement `StrategyProfile` frozen dataclass in `types.py` (name, weights, long_threshold, short_threshold, pip_distance_tolerance, strictness, preferred_timeframe)
7. Implement `QuestionModule` abstract base class in `modules/base.py` with `module_id` property, `evaluate()` abstract method, and `safe_evaluate()` wrapper
8. The `safe_evaluate()` method must catch all exceptions, clamp scores to [-1.0, +1.0], and return neutral ModuleOutput on failure
9. Write unit tests in `tests/test_q_types.py` verifying: ModuleOutput immutability, score clamping in safe_evaluate, exception isolation in safe_evaluate

**Acceptance criteria:**
- All types are frozen dataclasses (immutable)
- `safe_evaluate()` never raises exceptions
- Score clamping enforces [-1.0, +1.0] range
- Tests pass: `pytest tests/test_q_types.py -v`


### Task 2: MarketData Adapter

**Requirements:** R1, R3, R7

**Depends on:** Task 1

**Description:** Create the MarketData frozen dataclass and the adapter that transforms raw Candle arrays + tick data into the enriched MarketData snapshot consumed by all Q modules.

**Files to create:**
- `core/questions/adapter.py`

**Files to modify:**
- `core/questions/types.py` (add MarketData dataclass)

**Sub-tasks:**
1. Add `MarketData` frozen dataclass to `types.py` with fields: symbol, candles, closed_i, bid, ask, current_time_s, spread, atr_14, recent_highs, recent_lows, ema_50, ema_10, plus `closed_bar` and `lookback` properties
2. Implement `MarketDataAdapter` class in `adapter.py` with static method `from_bar_context(candles, closed_i, symbol, bid, ask, current_time_s) → MarketData`
3. Implement ATR-14 calculation (Wilder smoothing) — return 0.0 if fewer than 15 candles
4. Implement EMA-50 and EMA-10 calculations at closed_i
5. Implement recent_highs/recent_lows extraction (last 20 bars)
6. Implement spread calculation (ask - bid)
7. Handle edge cases: fewer than 2 candles returns MarketData with all derived fields at 0.0/empty
8. Write unit tests in `tests/test_q_adapter.py`: ATR correctness, EMA correctness, edge cases (empty candles, single candle), spread calculation

**Acceptance criteria:**
- Adapter is a pure function (no MT5 calls, no side effects)
- ATR/EMA calculations match standard financial formulas
- Edge cases return safe defaults without raising
- Tests pass: `pytest tests/test_q_adapter.py -v`

### Task 3: Q1 Higher Timeframe Trend Module

**Requirements:** R2, R3

**Depends on:** Task 1, Task 2

**Description:** Implement the Q1 module that evaluates directional bias from higher timeframe structure using the candle history.

**Files to create:**
- `core/questions/modules/q1_trend.py`
- `tests/test_q1_trend.py`

**Sub-tasks:**
1. Create `Q1Trend` class extending `QuestionModule` with `module_id = "q1_trend"`
2. Implement higher-high / higher-low detection over last 20 bars
3. Implement lower-high / lower-low detection over last 20 bars
4. Implement EMA-50 slope direction assessment
5. Implement price position relative to EMA-50
6. Implement pullback depth measurement relative to recent swing range
7. Combine sub-scores into final score: strong bullish (4+ signals) → +0.8 to +1.0, moderate → +0.3 to +0.7, neutral → -0.2 to +0.2, bearish mirrors
8. Set DirectionalState based on final score sign and magnitude
9. Write unit tests: strong bullish candles → high positive score, strong bearish → high negative score, flat candles → neutral, insufficient data → 0.0/neutral

**Acceptance criteria:**
- Module is stateless (no instance state between calls)
- Returns score in [-1.0, +1.0]
- Returns neutral on insufficient data
- Does not import any other Q module
- Tests pass: `pytest tests/test_q1_trend.py -v`

### Task 4: Q2 Key Levels Module

**Requirements:** R2, R3

**Depends on:** Task 1, Task 2

**Description:** Implement the Q2 module that evaluates proximity and reaction to significant support/resistance levels.

**Files to create:**
- `core/questions/modules/q2_levels.py`
- `tests/test_q2_levels.py`

**Sub-tasks:**
1. Create `Q2Levels` class extending `QuestionModule` with `module_id = "q2_levels"`
2. Implement swing high/low identification from last 50 bars (local extrema with ±2 bar confirmation)
3. Implement distance-to-nearest-level calculation normalized by ATR
4. Implement level strength scoring (count of touches/reactions within ATR distance)
5. Implement rejection wick detection at level (wick > 60% of candle range, body on opposite side)
6. Implement reaction strength assessment (body-to-wick ratio)
7. Combine into directional score: at support with rejection → positive, at resistance with rejection → negative, far from levels → 0.0
8. Write unit tests: price at support with rejection wick → positive score, price at resistance → negative, price mid-range → neutral

**Acceptance criteria:**
- Stateless, score in [-1.0, +1.0], neutral on insufficient data
- Level detection uses ATR-normalized distances (not absolute pips)
- Tests pass: `pytest tests/test_q2_levels.py -v`

### Task 5: Q3 Liquidity Module

**Requirements:** R2, R3

**Depends on:** Task 1, Task 2

**Description:** Implement the Q3 module that detects liquidity sweeps and volume characteristics.

**Files to create:**
- `core/questions/modules/q3_liquidity.py`
- `tests/test_q3_liquidity.py`

**Sub-tasks:**
1. Create `Q3Liquidity` class extending `QuestionModule` with `module_id = "q3_liquidity"`
2. Implement sweep detection: wick beyond previous swing high/low, close back inside range
3. Implement sweep distance measurement in ATR multiples
4. Implement close-back-inside-range confirmation
5. Implement volume spike detection (tick_volume vs 20-bar average, threshold: 1.5x)
6. Combine into directional score: bullish sweep (swept low, closed back) → positive, bearish sweep → negative
7. Write unit tests: candle sweeping below previous low then closing inside → positive score, sweep above high → negative, no sweep → neutral

**Acceptance criteria:**
- Stateless, score in [-1.0, +1.0], neutral on insufficient data
- Sweep detection uses structural swing points (not arbitrary bars)
- Tests pass: `pytest tests/test_q3_liquidity.py -v`

### Task 6: Q4 Confirmation Module

**Requirements:** R2, R3

**Depends on:** Task 1, Task 2

**Description:** Implement the Q4 module that evaluates candlestick pattern confirmation using the existing pattern registry.

**Files to create:**
- `core/questions/modules/q4_confirmation.py`
- `tests/test_q4_confirmation.py`

**Sub-tasks:**
1. Create `Q4Confirmation` class extending `QuestionModule` with `module_id = "q4_confirmation"`
2. Import `patterns.registry.detect_all` (read-only access to existing pattern detection)
3. Map detected patterns to directional scores: strong bullish patterns (engulfing, morning star) → +0.7 to +1.0, weak bullish (hammer) → +0.3 to +0.5, bearish mirrors
4. Weight by pattern confidence from `Signal.confidence`
5. Implement candle body percentage check (strong close > 60% body)
6. Implement consecutive directional closes bonus
7. If multiple patterns detected, use highest-confidence pattern's score
8. Write unit tests: bullish engulfing candles → positive score, bearish engulfing → negative, flat candles → neutral

**Acceptance criteria:**
- Stateless, score in [-1.0, +1.0], neutral on insufficient data
- Correctly integrates with existing `patterns/registry.py` (read-only)
- Tests pass: `pytest tests/test_q4_confirmation.py -v`

### Task 7: Q5 Momentum Module

**Requirements:** R2, R3

**Depends on:** Task 1, Task 2

**Description:** Implement the Q5 module that evaluates strength and direction of current price momentum.

**Files to create:**
- `core/questions/modules/q5_momentum.py`
- `tests/test_q5_momentum.py`

**Sub-tasks:**
1. Create `Q5Momentum` class extending `QuestionModule` with `module_id = "q5_momentum"`
2. Implement average body size vs ATR ratio (normalized momentum indicator)
3. Implement consecutive directional closes counter (3+ = strong signal)
4. Implement body size trend detection (increasing bodies = accelerating)
5. Implement opposing wick pressure measurement (high opposing wicks = weakening)
6. Implement rate-of-change over 5 and 10 bars (close[n] - close[n-5]) / ATR
7. Combine sub-scores: strong bullish momentum (4+ signals) → +0.7 to +1.0, moderate → +0.3, stalling → 0.0, bearish mirrors
8. Write unit tests: progressive bullish candles with increasing bodies → high positive, choppy candles → near zero, strong bearish → negative

**Acceptance criteria:**
- Stateless, score in [-1.0, +1.0], neutral on insufficient data
- Momentum is ATR-normalized (works across all symbols)
- Tests pass: `pytest tests/test_q5_momentum.py -v`

### Task 8: Q6 Timing Module

**Requirements:** R2, R3

**Depends on:** Task 1, Task 2

**Description:** Implement the Q6 module that evaluates session timing and time-of-day suitability.

**Files to create:**
- `core/questions/modules/q6_timing.py`
- `tests/test_q6_timing.py`

**Sub-tasks:**
1. Create `Q6Timing` class extending `QuestionModule` with `module_id = "q6_timing"`
2. Implement session detection from `current_time_s`: London (07:00–16:00 UTC), New York (12:00–21:00 UTC), Asian (22:00–07:00 UTC)
3. Implement kill zone detection: London open (07:00–09:00 UTC), NY open (12:00–14:00 UTC), overlap (12:00–16:00 UTC)
4. Implement dead zone detection: Asian range for majors (22:00–05:00 UTC)
5. Implement day-of-week filter: Friday after 20:00 UTC → penalty
6. Combine into magnitude score (always DirectionalState.NEUTRAL): kill zone → +0.8 to +1.0, active session → +0.4 to +0.7, dead zone → -0.5 to -1.0
7. Write unit tests: timestamp during London open → high score, timestamp during Asian dead zone → negative, Friday evening → negative

**Acceptance criteria:**
- Stateless, score in [-1.0, +1.0], DirectionalState always NEUTRAL
- Session boundaries are UTC-based
- Tests pass: `pytest tests/test_q6_timing.py -v`

### Task 9: Q7 Risk Module

**Requirements:** R2, R3

**Depends on:** Task 1, Task 2

**Description:** Implement the Q7 module that evaluates current risk conditions including volatility, spread, and adverse movement potential.

**Files to create:**
- `core/questions/modules/q7_risk.py`
- `tests/test_q7_risk.py`

**Sub-tasks:**
1. Create `Q7Risk` class extending `QuestionModule` with `module_id = "q7_risk"`
2. Implement spread-as-percentage-of-ATR assessment (spread/ATR > 0.3 = unfavorable)
3. Implement volatility assessment: current ATR vs 50-bar average ATR (too high or too low = penalty)
4. Implement adverse movement detection: sharp same-direction bars (overextension risk)
5. Implement RR feasibility check: can a 2:1 RR be achieved given current ATR and typical SL distance?
6. Implement consecutive same-direction bars counter (5+ = overextension warning)
7. Combine into magnitude score (always DirectionalState.NEUTRAL): favorable → +0.6 to +1.0, acceptable → +0.2, unfavorable → -0.5, dangerous → -1.0
8. Write unit tests: tight spread + moderate vol → positive, wide spread → negative, extreme vol → negative

**Acceptance criteria:**
- Stateless, score in [-1.0, +1.0], DirectionalState always NEUTRAL
- Spread assessment is ATR-relative (not absolute pips)
- Tests pass: `pytest tests/test_q7_risk.py -v`


### Task 10: Module Registry

**Requirements:** R4, R8

**Depends on:** Task 3, Task 4, Task 5, Task 6, Task 7, Task 8, Task 9

**Description:** Implement the module registry that auto-discovers and exposes all seven Q modules for the orchestrator.

**Files to modify:**
- `core/questions/modules/__init__.py`

**Sub-tasks:**
1. Import all seven module classes (Q1Trend through Q7Risk)
2. Instantiate `ALL_MODULES: list[QuestionModule]` with all seven instances
3. Create `MODULE_MAP: dict[str, QuestionModule]` mapping module_id → instance
4. Add validation: assert exactly 7 modules registered, all module_ids unique
5. Write test in `tests/test_q_registry.py`: verify 7 modules loaded, all have unique IDs, all implement evaluate()

**Acceptance criteria:**
- Registry exposes exactly 7 modules
- All module_ids are unique strings matching "q1_trend" through "q7_risk"
- Tests pass: `pytest tests/test_q_registry.py -v`

### Task 11: Strategy Profiles

**Requirements:** R6

**Depends on:** Task 1

**Description:** Implement the three strategy profiles (Scalping, Intraday, Swing) and the profile loader with validation.

**Files to create:**
- `core/questions/profiles/scalping.py`
- `core/questions/profiles/intraday.py`
- `core/questions/profiles/swing.py`

**Files to modify:**
- `core/questions/profiles/__init__.py`

**Sub-tasks:**
1. Define `SCALPING` StrategyProfile instance in `scalping.py` with weights summing to 1.0, thresholds, and sensitivity params per design doc
2. Define `INTRADAY` StrategyProfile instance in `intraday.py`
3. Define `SWING` StrategyProfile instance in `swing.py`
4. Implement `validate_profile(profile) → list[str]` in `profiles/__init__.py`: checks 7 weights defined, each in [0,1], sum=1.0 (±0.001 tolerance), thresholds in valid range, strictness 1–5
5. Implement `load_profile(name: str) → StrategyProfile` in `profiles/__init__.py`: loads by name, validates, raises ValueError on invalid
6. Write tests in `tests/test_q_profiles.py`: all three profiles pass validation, invalid profile (weights don't sum) is rejected, load_profile returns correct instance

**Acceptance criteria:**
- All three profiles pass `validate_profile()` without errors
- Weights sum to exactly 1.0 for each profile
- Invalid profiles are rejected with descriptive error messages
- Tests pass: `pytest tests/test_q_profiles.py -v`

### Task 12: Decision Aggregator

**Requirements:** R5

**Depends on:** Task 1, Task 11

**Description:** Implement the Decision Aggregator that computes weighted sums and applies threshold logic to produce TradeDecisions.

**Files to create:**
- `core/questions/aggregator.py`
- `tests/test_q_aggregator.py`

**Sub-tasks:**
1. Implement `DecisionAggregator` class with `aggregate(results, profile, symbol, timestamp_s) → TradeDecision` method
2. Implement score clamping: each module score clamped to [-1.0, +1.0] before weighting
3. Implement module exclusion: modules with confidence=0.0 are excluded, remaining weights re-normalized
4. Implement weighted sum calculation: sum(score_i × weight_i) for active modules
5. Implement threshold comparison: weighted_sum > long_threshold → ENTER_LONG, < short_threshold → ENTER_SHORT, else NO_TRADE
6. Populate TradeDecision with full breakdown (per-module weighted scores, excluded list)
7. Write unit tests: all modules positive above threshold → ENTER_LONG, all negative below threshold → ENTER_SHORT, mixed → NO_TRADE, one module excluded → re-normalization works correctly

**Acceptance criteria:**
- Weighted sum is bounded by [-1.0, +1.0] when all weights sum to 1.0
- Threshold logic is strictly greater/less (not >=, <=)
- Re-normalization preserves relative weight proportions
- Tests pass: `pytest tests/test_q_aggregator.py -v`

### Task 13: Orchestrator

**Requirements:** R4, R9

**Depends on:** Task 2, Task 10, Task 12

**Description:** Implement the central Orchestrator that coordinates module evaluation and produces the final TradeDecision with full traceability.

**Files to create:**
- `core/questions/orchestrator.py`
- `tests/test_q_orchestrator.py`

**Sub-tasks:**
1. Implement `Orchestrator.__init__(profile: StrategyProfile)` storing profile, creating aggregator instance, loading module registry
2. Implement `evaluate_bar(candles, closed_i, symbol, bid, ask, current_time_s) → TradeDecision`
3. In evaluate_bar: build MarketData via adapter, invoke all modules via safe_evaluate, delegate to aggregator
4. Implement decision report emission: log per-module scores, weights, contributions, and final decision when `Q_MODULE_DEBUG_LOGS` is True
5. Implement structured logging: `[Q_DECISION]` and `[Q_MODULES]` log lines at INFO level
6. Export public API from `core/questions/__init__.py`: Orchestrator, load_profile, ModuleOutput, TradeDecision, MarketData
7. Write integration test: provide synthetic candles → orchestrator returns valid TradeDecision with all 7 module results populated

**Acceptance criteria:**
- Orchestrator invokes all 7 modules and produces a complete TradeDecision
- Failed modules are isolated (orchestrator never crashes)
- Decision report contains per-module breakdown
- Tests pass: `pytest tests/test_q_orchestrator.py -v`

### Task 14: Legacy Decision Adapter

**Requirements:** R7

**Depends on:** Task 13

**Description:** Implement the adapter that maps TradeDecision to the existing UnifiedDecision/Decision wire format for the execution layer.

**Files to create:**
- `core/questions/legacy_adapter.py`
- `tests/test_q_legacy_adapter.py`

**Sub-tasks:**
1. Implement `adapt_trade_decision_to_unified(td, candles, closed_i, symbol, bid, ask, current_time_s, config_module) → UnifiedDecision`
2. Map ENTER_LONG → Decision(should_trade=True, signal=Signal(side=BUY, pattern="Q_SYSTEM_SIGNAL", ...))
3. Map ENTER_SHORT → Decision(should_trade=True, signal=Signal(side=SELL, ...))
4. Map NO_TRADE → Decision(should_trade=False, reason="q_system_no_trade (score=X.XXX)")
5. Extract pattern name from Q4 module reasoning if available (for Signal.pattern field)
6. Build BarEvaluationContext from raw inputs
7. Return UnifiedDecision with last_completed_stage="q_system_complete"
8. Write tests: ENTER_LONG → valid UnifiedDecision with should_trade=True and BUY signal, NO_TRADE → should_trade=False, signal fields populated correctly

**Acceptance criteria:**
- Output UnifiedDecision is compatible with existing execution layer
- Signal.side matches TradeAction direction
- Decision.reason includes profile name and score
- Tests pass: `pytest tests/test_q_legacy_adapter.py -v`

### Task 15: Runtime Integration and Config

**Requirements:** R7

**Depends on:** Task 14

**Description:** Add the config flags and runtime dispatch logic that allows switching between the existing pipeline and the Q-system.

**Files to modify:**
- `core/config.py` (add new config flags)
- `core/runtime/live_scanner.py` (add dispatch logic)

**Sub-tasks:**
1. Add to `core/config.py`: `DECISION_SYSTEM = "PIPELINE"`, `Q_STRATEGY_PROFILE = "intraday"`, `Q_MODULE_TIMEOUT_SECONDS = 5.0`
2. In `core/runtime/live_scanner.py`, locate the call to `process_bar()` and wrap it in a dispatch function
3. Implement dispatch: if `DECISION_SYSTEM == "QUESTIONS"` → import and call Orchestrator.evaluate_bar() → adapt to UnifiedDecision; else → existing process_bar()
4. Ensure the dispatch is lazy-import (Q-system modules only imported when activated)
5. Verify that when `DECISION_SYSTEM = "PIPELINE"` (default), behavior is identical to current system (no regression)
6. Write integration test: mock config with DECISION_SYSTEM="QUESTIONS", verify orchestrator is called; mock with "PIPELINE", verify process_bar is called

**Acceptance criteria:**
- Default behavior unchanged (DECISION_SYSTEM="PIPELINE")
- Switching to "QUESTIONS" activates Q-system without modifying any `core/pipeline/*` files
- Lazy imports prevent Q-system code from loading when not active
- Tests pass: `pytest tests/test_q_runtime_dispatch.py -v`

### Task 16: Observability Integration

**Requirements:** R9

**Depends on:** Task 15

**Description:** Integrate the Q-system with existing observability infrastructure (dashboard, decision audit, logging).

**Files to modify:**
- `core/questions/orchestrator.py` (add dashboard + audit calls)

**Files to create:**
- `tests/test_q_observability.py`

**Sub-tasks:**
1. Add dashboard integration: call `record_cycle()` equivalent for Q-system evaluations
2. Add rejection tracking: when NO_TRADE, record rejection reason and score for dashboard
3. Add decision audit integration: when DECISION_AUDIT_ENABLED and action is ENTER_LONG/ENTER_SHORT, write JSONL record with full module breakdown
4. Implement structured log format: `[Q_DECISION]` line with symbol, profile, action, score; `[Q_MODULES]` line with per-module scores and weights; `[Q_THRESHOLD]` line with thresholds and result
5. Respect `ESSENTIAL_LOGS` flag: only emit at INFO when True
6. Respect `Q_MODULE_DEBUG_LOGS` flag: emit verbose per-module reasoning at DEBUG
7. Write tests: verify log output format, verify audit JSONL structure, verify dashboard counters increment

**Acceptance criteria:**
- Q-system decisions appear in decision audit trail (same directory as existing)
- Dashboard tracks Q-system cycles and rejections separately from pipeline
- Log format is structured and parseable
- Tests pass: `pytest tests/test_q_observability.py -v`

### Task 17: Integration Tests

**Requirements:** R1, R4, R5, R7

**Depends on:** Task 16

**Description:** End-to-end integration tests verifying the complete Q-system flow from candles to execution-ready Decision.

**Files to create:**
- `tests/test_q_integration.py`

**Sub-tasks:**
1. Test full flow: synthetic bullish candles during London session → Orchestrator → ENTER_LONG TradeDecision → valid UnifiedDecision with BUY signal
2. Test full flow: synthetic bearish candles → ENTER_SHORT → valid UnifiedDecision with SELL signal
3. Test full flow: flat/choppy candles → NO_TRADE → UnifiedDecision with should_trade=False
4. Test module fault isolation: mock one module to raise exception → orchestrator still returns valid decision with 6 modules
5. Test profile switching: same candles with different profiles produce different decisions (due to different thresholds)
6. Test coexistence: verify existing pipeline process_bar() still works unchanged when DECISION_SYSTEM="PIPELINE"
7. Test adapter output compatibility: verify UnifiedDecision from Q-system has all fields expected by execution layer

**Acceptance criteria:**
- All integration tests pass without MT5 connection
- Q-system and existing pipeline produce valid outputs independently
- Module faults are isolated and don't crash the system
- Tests pass: `pytest tests/test_q_integration.py -v`

### Task 18: Property-Based Tests

**Requirements:** R1, R2, R5

**Depends on:** Task 17

**Description:** Hypothesis-based property tests verifying correctness invariants of the Q-system.

**Files to create:**
- `tests/test_q_properties.py`

**Sub-tasks:**
1. Property: For any valid MarketData, every module score is in [-1.0, +1.0] (generate random candle arrays via hypothesis)
2. Property: Same MarketData + same StrategyProfile → same TradeDecision (determinism)
3. Property: Aggregator weighted sum is always in [-1.0, +1.0] when weights sum to 1.0 (generate random scores)
4. Property: weighted_sum > long_threshold ↔ ENTER_LONG; weighted_sum < short_threshold ↔ ENTER_SHORT (threshold consistency)
5. Property: If all module scores are 0.0, action is always NO_TRADE
6. Property: Profile with weights not summing to 1.0 is rejected by validate_profile()
7. Property: Calling evaluate_bar() never modifies any external state (verify config module unchanged after call)

**Acceptance criteria:**
- All properties hold for 200+ generated examples each
- No flaky failures (deterministic seeds where needed)
- Tests pass: `pytest tests/test_q_properties.py -v`
