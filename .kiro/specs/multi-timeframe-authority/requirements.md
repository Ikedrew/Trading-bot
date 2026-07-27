# Requirements Document

## Introduction

A hierarchical multi-timeframe authority system that layers H4, H1, M15, and M1 context on top of the existing M5 execution pipeline. Higher timeframes constrain lower timeframes by providing regime context, directional bias, and structural validation — but never directly trigger trades. M5 remains the sole execution authority. All higher-timeframe analysis is cached and refreshed only on new bar closure for that timeframe, preserving the synchronous single-threaded runtime model.

## Glossary

- **TimeframeCache**: A per-symbol, per-timeframe data structure that stores the latest analyzed snapshot (candles + derived metrics) and refreshes only when a new bar closes on that timeframe.
- **Regime**: The macro market environment classification derived from H4 data (e.g., trending, ranging, volatile, transitional).
- **HTF_Bias**: The directional preference (bullish, bearish, neutral) derived from H1 data, with an associated confidence score.
- **Structure_Context**: Structural validation metrics derived from M15 data (key levels, swing structure, setup quality).
- **Execution_Layer**: The existing M5 pipeline (market_context → scoring → intent_builder) which remains the sole trade trigger authority.
- **Refinement_Layer**: Optional M1 data used only for execution timing refinement (entry precision), never for signal generation.
- **Hierarchical_Constraint**: The principle that higher timeframes restrict what lower timeframes are allowed to do, without directly producing trade signals.
- **Cached_Snapshot**: An analyzed timeframe result that persists until the next bar closes on that timeframe, avoiding redundant computation.

## Requirements

### Requirement 1: Timeframe Cache Infrastructure

**User Story:** As a developer, I want a per-symbol cache that stores analyzed timeframe snapshots and refreshes only on new bar closure, so that the M5 loop can consume higher-timeframe context without fetching data every cycle.

#### Acceptance Criteria

1. THE system SHALL maintain a TimeframeCache per symbol that stores the latest analyzed snapshot for each configured timeframe (H4, H1, M15, M1), where each snapshot contains the analyzer output (regime classification, bias direction with confidence, or structure metrics) and the timestamp of the bar that produced it.
2. WHEN a new bar closes on a given timeframe, THE TimeframeCache SHALL refresh the snapshot for that timeframe and symbol by fetching fresh candles from MT5 and running the corresponding analyzer (H4 → Regime analyzer, H1 → Bias analyzer, M15 → Structure analyzer, M1 → Refinement layer).
3. WHEN no new bar has closed on a given timeframe, THE TimeframeCache SHALL return the previously cached snapshot without fetching or recomputing.
4. THE TimeframeCache SHALL determine new bar closure by comparing the latest candle timestamp against the previously stored timestamp for that timeframe.
5. IF the MT5 candle fetch fails for a higher timeframe, THEN THE TimeframeCache SHALL retain the previous snapshot and log a warning, without blocking M5 execution.
6. IF the TimeframeCache is queried for a timeframe that has no previously cached snapshot (cold start), THEN THE system SHALL return a None snapshot for that timeframe, and the M5 pipeline SHALL proceed without applying HTF constraints for that timeframe until a valid snapshot is available.
7. IF a cached snapshot's bar timestamp is older than 3 times the timeframe's bar duration (e.g., older than 12 hours for H4), THEN THE TimeframeCache SHALL treat the snapshot as stale and attempt a fresh fetch on the next M5 cycle regardless of bar-closure detection.

### Requirement 2: H4 Regime Layer

**User Story:** As a trader, I want the system to classify the macro market environment from H4 data, so that the M5 execution layer only takes trades aligned with the dominant regime.

#### Acceptance Criteria

1. THE H4 Regime analyzer SHALL classify the market into one of: TRENDING_BULLISH, TRENDING_BEARISH, RANGING, VOLATILE, or TRANSITIONAL, and SHALL return a confidence score between 0.0 and 1.0 indicating classification certainty.
2. THE H4 Regime analyzer SHALL compute regime classification using H4 candle structure including: EMA slope direction, higher-high/higher-low sequences, ATR expansion/contraction relative to a rolling ATR average, and range-bound detection based on price containment within a defined percentage of recent high-low range.
3. WHEN the H4 regime is RANGING, THE M5 execution layer SHALL subtract a configurable scoring penalty (MTF_H4_RANGING_SCORE_PENALTY) from the confluence score for trend-following setups.
4. WHEN the H4 regime is VOLATILE, THE M5 execution layer SHALL add the configurable value MTF_H4_VOLATILE_MIN_SCORE_INCREASE to the minimum score threshold required for trade entry.
5. WHEN a new H4 bar closes, THE TimeframeCache SHALL refresh the H4 Regime snapshot for the affected symbol.
6. THE H4 Regime analyzer SHALL be a stateless pure function that accepts a list of H4 candles and returns a regime classification enum plus a confidence score between 0.0 and 1.0.
7. IF the H4 Regime analyzer receives fewer candles than required for classification (fewer than MTF_H4_CANDLE_COUNT), THEN THE H4 Regime analyzer SHALL return TRANSITIONAL with a confidence score of 0.0.

### Requirement 3: H1 Bias Layer

**User Story:** As a trader, I want the system to determine directional preference from H1 data, so that M5 trades are only taken in the direction supported by the hourly bias.

#### Acceptance Criteria

1. THE H1 Bias analyzer SHALL produce a directional bias (BULLISH, BEARISH, or NEUTRAL) with a confidence score between 0.0 and 1.0.
2. THE H1 Bias analyzer SHALL derive bias from H1 candle structure including: EMA position and slope, swing structure (HH/HL or LH/LL), momentum characteristics, and key level proximity.
3. WHEN the H1 bias is NEUTRAL, THE M5 execution layer SHALL add the configurable value MTF_H1_NEUTRAL_MIN_SCORE_INCREASE to the minimum confluence score required for trade entry.
4. WHEN the H1 bias contradicts the M5 signal direction and the M5 confluence score is at or below the configurable override threshold (MTF_H1_CONTRADICTION_THRESHOLD), THEN THE M5 execution layer SHALL block the trade.
5. WHEN the H1 bias aligns with the M5 signal direction, THE M5 scoring engine SHALL add the configurable bonus (MTF_H1_ALIGNED_BONUS) to the confluence score.
6. WHEN a new H1 bar closes, THE TimeframeCache SHALL refresh the H1 Bias snapshot for the affected symbol.
7. THE H1 Bias analyzer SHALL be a stateless pure function that accepts a list of H1 candles and returns bias direction plus confidence score.
8. IF the H1 Bias analyzer receives fewer candles than required for analysis (fewer than MTF_H1_CANDLE_COUNT), THEN THE H1 Bias analyzer SHALL return NEUTRAL with a confidence score of 0.0.

### Requirement 4: M15 Structure Layer

**User Story:** As a trader, I want the system to validate trade setups against M15 structural context, so that M5 entries only occur at structurally significant locations.

#### Acceptance Criteria

1. THE M15 Structure analyzer SHALL identify key structural elements: swing highs/lows, support/resistance levels, and order blocks from M15 candle data.
2. THE M15 Structure analyzer SHALL produce a structure quality score between 0.0 and 1.0 indicating how favorable the current price location is for trade entry.
3. WHEN the M15 structure quality score is below the configurable minimum threshold (MTF_M15_MIN_STRUCTURE_QUALITY), THE M5 execution layer SHALL block trade entry regardless of M5 confluence score.
4. WHEN the M15 structure quality score is at or above a configurable high-quality threshold (MTF_M15_HIGH_QUALITY_THRESHOLD), THE M5 scoring engine SHALL add the configurable bonus (MTF_M15_HIGH_QUALITY_BONUS) to the confluence score.
5. WHEN a new M15 bar closes, THE TimeframeCache SHALL refresh the M15 Structure snapshot for the affected symbol.
6. THE M15 Structure analyzer SHALL be a stateless pure function that accepts a list of M15 candles and current price, and returns structural metrics including the structure quality score.
7. IF the M15 Structure analyzer receives fewer candles than required for analysis (fewer than MTF_M15_CANDLE_COUNT), THEN THE M15 Structure analyzer SHALL return a structure quality score of 0.0.

### Requirement 5: M1 Refinement Layer (Optional)

**User Story:** As a trader, I want optional M1 data for execution timing refinement, so that entries can be placed at more precise price levels when conditions allow.

#### Acceptance Criteria

1. THE M1 Refinement layer SHALL be disabled by default and enabled via a configuration flag.
2. WHEN enabled, THE M1 Refinement layer SHALL provide tick-level context (recent M1 candles) to the execution layer for entry timing optimization.
3. THE M1 Refinement layer SHALL NOT generate trade signals, modify scoring, or influence the trade/no-trade decision.
4. THE M1 Refinement layer SHALL only be consulted AFTER the M5 pipeline has already decided to trade (should_trade=True).
5. IF M1 data is unavailable, THE system SHALL proceed with standard M5 execution without delay or error.

### Requirement 6: Pipeline Integration

**User Story:** As a developer, I want the multi-timeframe context to integrate cleanly into the existing M5 pipeline, so that higher-timeframe constraints influence scoring and gating without restructuring the pipeline.

#### Acceptance Criteria

1. THE multi-timeframe system SHALL be activated via a configuration flag (MTF_ENABLED) and SHALL NOT affect pipeline behavior when disabled.
2. WHEN MTF_ENABLED is True, THE M5 pipeline SHALL consume cached HTF snapshots at the start of each bar evaluation, before the scoring stage.
3. THE HTF context SHALL influence the M5 pipeline through: scoring bonuses/penalties, minimum score threshold adjustments, and directional gating (block trades against HTF bias).
4. THE HTF context SHALL NOT modify EngineState fields that are owned by the existing M5 pipeline stages.
5. THE multi-timeframe system SHALL reside in a separate module directory (core/timeframes/) and SHALL NOT modify existing pipeline stage files.
6. THE integration point SHALL be a single function call in core/engine.py that injects HTF context into the scoring/gating logic.

### Requirement 7: Data Fetching for Multiple Timeframes

**User Story:** As a developer, I want the system to fetch candle data for H4, H1, M15, and M1 timeframes from MT5, so that higher-timeframe analyzers have the data they need.

#### Acceptance Criteria

1. THE system SHALL fetch candles for each configured timeframe using the existing MT5DataFeed.copy_rates_closed() method.
2. THE system SHALL fetch higher-timeframe candles only when a new bar is detected for that timeframe (not every M5 cycle).
3. THE system SHALL configure the number of candles to fetch per timeframe independently (e.g., 100 H4 bars, 200 H1 bars, 200 M15 bars).
4. IF a higher-timeframe fetch fails, THE system SHALL log a warning and continue using the previously cached snapshot.
5. THE system SHALL detect new bar closure for each timeframe by comparing the latest candle timestamp against the cached timestamp.

### Requirement 8: Configuration

**User Story:** As a developer, I want all multi-timeframe parameters to be configurable, so that the system can be tuned without code changes.

#### Acceptance Criteria

1. THE system SHALL provide the following configuration flags: MTF_ENABLED (bool), MTF_H4_CANDLE_COUNT (int), MTF_H1_CANDLE_COUNT (int), MTF_M15_CANDLE_COUNT (int), MTF_M1_ENABLED (bool), MTF_M1_CANDLE_COUNT (int).
2. THE system SHALL provide scoring influence parameters: MTF_H4_VOLATILE_SCORE_PENALTY (float), MTF_H1_ALIGNED_BONUS (float), MTF_H1_CONTRADICTION_THRESHOLD (float), MTF_M15_MIN_STRUCTURE_QUALITY (float), MTF_M15_HIGH_QUALITY_BONUS (float).
3. THE system SHALL provide threshold adjustment parameters: MTF_H4_VOLATILE_MIN_SCORE_INCREASE (float), MTF_H1_NEUTRAL_MIN_SCORE_INCREASE (float).
4. ALL configuration parameters SHALL have sensible defaults that preserve existing behavior when MTF_ENABLED is False.
5. THE configuration SHALL be validated at startup alongside existing config validation.

### Requirement 9: Observability

**User Story:** As a developer, I want visibility into higher-timeframe state changes and their influence on M5 decisions, so that I can debug and tune the multi-timeframe system.

#### Acceptance Criteria

1. WHEN a higher-timeframe snapshot refreshes, THE system SHALL emit a structured log event indicating the timeframe, symbol, and new classification/bias/score.
2. WHEN HTF context modifies the M5 scoring (bonus or penalty), THE system SHALL include the HTF influence in the decision audit trail.
3. WHEN HTF context blocks a trade (directional contradiction or structure quality veto), THE system SHALL log the block reason with the HTF state that caused it.
4. THE system SHALL provide a periodic summary of HTF state per symbol (similar to the existing dashboard).
