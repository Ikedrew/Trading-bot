# Requirements Document

## Introduction

A modular trading decision architecture based on 7 independent evaluation questions (Q1–Q7). Each question module performs a single aspect of market analysis, consuming only raw market data and returning a score plus directional state. A shared orchestrator aggregates module outputs and produces a final trade decision. Three strategy profiles (Scalping, Intraday, Swing) reuse the same Q1–Q7 modules with different weights, thresholds, and sensitivity parameters. The system coexists alongside the existing pipeline and provides a clear migration path to eventually replace the current scoring/decision logic.

## Glossary

- **Question_Module**: A stateless evaluation unit (Q1–Q7) that consumes market data and returns a score and directional state without depending on any other Question_Module.
- **Orchestrator**: The central coordination layer that invokes all Question_Modules, collects their outputs, and delegates final decision logic to the Decision_Aggregator.
- **Decision_Aggregator**: The component within the Orchestrator responsible for combining weighted Question_Module outputs into a final trade decision.
- **Strategy_Profile**: A named configuration (Scalping, Intraday, or Swing) that defines per-module weights, entry/exit thresholds, and sensitivity parameters.
- **Module_Output**: The structured result returned by a Question_Module, containing a numeric score and a directional state.
- **Directional_State**: An enumerated value representing the module's assessment: bullish, bearish, or neutral.
- **Market_Data**: The input consumed by Question_Modules, consisting of candle arrays, tick data, and price levels sourced from the existing data layer.
- **Score**: A numeric value (float) representing the strength of a Question_Module's assessment within a normalized range.
- **Weight**: A per-module multiplier defined in a Strategy_Profile that scales a Question_Module's Score during aggregation.
- **Threshold**: A numeric boundary defined in a Strategy_Profile that the aggregated score must exceed to trigger a trade entry or exit.
- **Trade_Decision**: The final output of the Orchestrator: enter long, enter short, or no trade.
- **Existing_Pipeline**: The current scoring/decision system in `core/pipeline/` including `scoring_engine`, `decision_engine`, and related modules.

## Requirements

### Requirement 1: Question Module Interface

**User Story:** As a developer, I want all question modules to follow a consistent interface, so that the orchestrator can invoke them uniformly and new modules can be added without changing orchestration logic.

#### Acceptance Criteria

1. THE Question_Module interface SHALL define an `evaluate` method that accepts Market_Data containing at minimum a list of price candles, the current bid price, the current ask price, and a symbol identifier, and returns a Module_Output containing a Score and a Directional_State.
2. WHEN a Question_Module is invoked, THE Question_Module SHALL produce a Module_Output without accessing or depending on any other Question_Module's state or output.
3. THE Question_Module interface SHALL define Score as a float value normalized to the inclusive range -1.0 to +1.0, where values outside this range are clamped to the nearest bound.
4. THE Question_Module interface SHALL define Directional_State as one of: bullish, bearish, or neutral.
5. IF the provided Market_Data contains fewer than 2 candles or is missing any required field, THEN THE Question_Module SHALL return a Module_Output with a Score of 0.0 and a Directional_State of neutral without raising an exception.

### Requirement 2: Question Module Independence

**User Story:** As a developer, I want each question module to be fully independent, so that modules can be developed, tested, and modified in isolation without side effects.

#### Acceptance Criteria

1. THE Question_Module SHALL be stateless, maintaining no internal state between successive invocations.
2. THE Question_Module SHALL consume only Market_Data as input and SHALL NOT import or reference any other Question_Module.
3. WHEN a Question_Module raises an exception, THE Orchestrator SHALL isolate the failure and continue evaluating remaining Question_Modules.

### Requirement 3: Seven Evaluation Modules

**User Story:** As a trader, I want seven distinct evaluation dimensions covering trend, levels, liquidity, confirmation, momentum, timing, and risk, so that trade decisions reflect a comprehensive market assessment.

#### Acceptance Criteria

1. THE system SHALL provide exactly seven Question_Modules: Q1_Higher_Timeframe_Trend, Q2_Key_Levels, Q3_Liquidity, Q4_Confirmation, Q5_Momentum, Q6_Timing, and Q7_Risk.
2. WHEN Market_Data is provided, THE Q1_Higher_Timeframe_Trend module SHALL evaluate the directional bias from a higher timeframe relative to the trading timeframe.
3. WHEN Market_Data is provided, THE Q2_Key_Levels module SHALL evaluate proximity and reaction to significant support and resistance price levels.
4. WHEN Market_Data is provided, THE Q3_Liquidity module SHALL evaluate liquidity conditions including sweep detection and volume characteristics.
5. WHEN Market_Data is provided, THE Q4_Confirmation module SHALL evaluate candlestick pattern confirmation and price action signals.
6. WHEN Market_Data is provided, THE Q5_Momentum module SHALL evaluate the strength and direction of current price momentum.
7. WHEN Market_Data is provided, THE Q6_Timing module SHALL evaluate session timing, time-of-day suitability, and temporal market conditions.
8. WHEN Market_Data is provided, THE Q7_Risk module SHALL evaluate current risk conditions including volatility, spread, and adverse movement potential.

### Requirement 4: Orchestrator Coordination

**User Story:** As a developer, I want a single orchestrator to coordinate all module evaluations and produce a trade decision, so that the decision flow is centralized and easy to reason about.

#### Acceptance Criteria

1. WHEN a new bar is received, THE Orchestrator SHALL invoke all seven Question_Modules and collect their Module_Outputs.
2. THE Orchestrator SHALL pass the active Strategy_Profile's weights and thresholds to the Decision_Aggregator.
3. WHEN all Module_Outputs are collected, THE Orchestrator SHALL delegate aggregation to the Decision_Aggregator and return the resulting Trade_Decision.
4. IF a Question_Module raises an exception, THEN THE Orchestrator SHALL assign a neutral Directional_State and a Score of 0.0 for that module and log the failure.

### Requirement 5: Decision Aggregation

**User Story:** As a trader, I want the system to combine module scores using strategy-specific weights and thresholds, so that different trading styles produce appropriately tuned decisions.

#### Acceptance Criteria

1. THE Decision_Aggregator SHALL compute a weighted sum of all Module_Output Scores using the active Strategy_Profile's Weight values, where each Weight is a decimal in the range 0.0 to 1.0 and the sum of all Weights in a Strategy_Profile equals 1.0.
2. WHEN the weighted sum is strictly greater than the Strategy_Profile's long entry Threshold, THE Decision_Aggregator SHALL produce a Trade_Decision of enter long.
3. WHEN the weighted sum is strictly less than the Strategy_Profile's short entry Threshold (a negative value), THE Decision_Aggregator SHALL produce a Trade_Decision of enter short.
4. WHEN the weighted sum is greater than or equal to the short entry Threshold and less than or equal to the long entry Threshold, THE Decision_Aggregator SHALL produce a Trade_Decision of no trade.
5. THE Decision_Aggregator SHALL include the per-module weighted scores, the raw unweighted module scores, and the final aggregated weighted sum in the Trade_Decision output for traceability.
6. IF one or more modules fail to produce a Score within 5 seconds of evaluation start, THEN THE Decision_Aggregator SHALL exclude the non-responding module from the weighted sum, re-normalize the remaining Weights to sum to 1.0, and annotate the Trade_Decision output with the identity of each excluded module.
7. THE Decision_Aggregator SHALL constrain each individual Module_Output Score to the range -1.0 to +1.0 before applying Weights, clamping any value that falls outside this range to the nearest bound.

### Requirement 6: Strategy Profile Configuration

**User Story:** As a trader, I want three strategy profiles with different parameters, so that the same evaluation modules serve scalping, intraday, and swing trading without duplicating logic.

#### Acceptance Criteria

1. THE system SHALL provide three Strategy_Profiles: Scalping, Intraday, and Swing.
2. THE Strategy_Profile SHALL define a Weight as a numeric value between 0.0 and 1.0 (inclusive) for each of the seven Question_Modules, and the sum of all seven Weights SHALL equal 1.0.
3. THE Strategy_Profile SHALL define entry Thresholds for long and short Trade_Decisions as numeric values between 0.0 and 10.0 (inclusive).
4. THE Strategy_Profile SHALL define sensitivity parameters: pip distance tolerance as a non-negative float representing price distance, strictness level as an integer from 1 to 5 (where 1 is least strict and 5 is most strict), and preferred timeframe bias as one of the system-supported timeframe identifiers.
5. WHEN a Strategy_Profile is loaded, THE Orchestrator SHALL validate that all seven Weights are defined, that each Weight is between 0.0 and 1.0, that the seven Weights sum to 1.0, and that Thresholds are numeric values within the valid range.
6. IF Strategy_Profile validation fails, THEN THE Orchestrator SHALL reject the profile, report an error message indicating which fields failed validation, and SHALL NOT proceed with trade evaluation using the invalid profile.
7. THE Strategy_Profile SHALL be defined as a declarative configuration (dictionary or dataclass) and SHALL NOT contain callable functions, class methods that compute trade signals, or references to execution logic.

### Requirement 7: Coexistence with Existing Pipeline

**User Story:** As a developer, I want the new modular system to coexist alongside the existing pipeline, so that I can develop and validate it incrementally without disrupting live trading.

#### Acceptance Criteria

1. THE modular decision system SHALL reside in a separate directory (`core/questions/`) and SHALL NOT modify any existing files in `core/pipeline/`.
2. WHEN the modular decision system is disabled, THE Existing_Pipeline SHALL continue to function without any behavioral change.
3. THE Orchestrator SHALL accept the same Market_Data structures (candle arrays, tick data) used by the Existing_Pipeline without requiring data format changes.
4. THE system SHALL provide a configuration flag to select between the Existing_Pipeline and the modular decision system at runtime.

### Requirement 8: Project Structure

**User Story:** As a developer, I want a clean folder structure that separates modules, orchestration, and configuration, so that the codebase remains navigable and maintainable.

#### Acceptance Criteria

1. THE system SHALL organize Question_Modules in individual files within `core/questions/modules/`, one file per module.
2. THE system SHALL place the Orchestrator in `core/questions/orchestrator.py`.
3. THE system SHALL place the Decision_Aggregator in `core/questions/aggregator.py`.
4. THE system SHALL place Strategy_Profile definitions in `core/questions/profiles/`, one file per profile.
5. THE system SHALL provide a `core/questions/__init__.py` that exports the public interface (Orchestrator, Question_Module base, Strategy_Profile loader).

### Requirement 9: Module Output Traceability

**User Story:** As a developer, I want full visibility into each module's contribution to the final decision, so that I can debug, tune, and validate the system effectively.

#### Acceptance Criteria

1. THE Orchestrator SHALL produce a decision report containing each Question_Module's raw Score, Directional_State, applied Weight, and weighted contribution.
2. THE decision report SHALL include the final aggregated score, the active Strategy_Profile name, and the resulting Trade_Decision.
3. WHEN logging is enabled, THE Orchestrator SHALL emit the decision report for each bar evaluation.
