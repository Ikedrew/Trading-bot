# BOT ARCHITECTURE OWNERSHIP MAP

**Generated:** 2026-07-14  
**Status:** Definitive pre-refactoring reference  
**Verified against:** Live repository (all imports traced via grep, runtime flow confirmed)

---

## 1. Startup & Bootstrap System

### Responsibility
Initialize MT5, validate configuration, resolve symbols, acquire locks, run self-tests, dispatch to execution mode.

### Files
- `main.py` — entry point, signal handlers, mode dispatch
- `core/config.py` — all configuration values
- `core/config_validation.py` — validate_and_freeze_config()
- `core/config_profile_loader.py` — load_and_apply_profile()
- `core/mt5_validation.py` — validate_account()
- `core/startup_self_test.py` — run_startup_self_test()
- `core/strategy_identity.py` — resolve_strategy_identity()
- `core/runtime/instance_lock.py` — acquire/release_instance_lock()
- `core/symbol_resolver.py` — resolve_all()
- `core/loop.py` — thin dispatcher (run_live → run_live_scanner)
- `core/log_router.py` — StructuredLogger

### Runtime Position
First. Runs once at process start before any trading loop.

### Authority Status
PRIMARY AUTHORITY

### Keep / Move / Delete
- KEEP: All files
- DELETE: None
- MOVE: None

### Refactor Risk
Low

### Requires Human Review
NO

---

## 2. Configuration System

### Responsibility
Single source of truth for all runtime parameters, feature flags, thresholds, and broker configuration.

### Files
- `core/config.py` — all config values
- `core/config_validation.py` — validation logic
- `core/config_profile_loader.py` — profile switching
- `core/constants/` — shared constants
- `profiles/` — config profile files

### Runtime Position
Loaded at import time. Referenced by every other system.

### Authority Status
PRIMARY AUTHORITY

### Keep / Move / Delete
- KEEP: All
- Key flags: `USE_NEW_PIPELINE=True`, `ENABLE_LEGACY_SHADOW_PIPELINE=False`, `ALLOW_LEGACY_FALLBACK=False`

### Refactor Risk
High (changing config affects entire system)

### Requires Human Review
YES — Several legacy config values (`MIN_SCORE_TO_TRADE`, `BIAS_CONFLUENCE_THRESHOLD`, `SETUP_MA_PERIOD`) only serve the disabled legacy pipeline. Safe to remove AFTER legacy pipeline deletion.

---

## 3. Market Data System

### Responsibility
Fetch live ticks and candles from MT5. Normalize timestamps. Detect stale feeds.

### Files
- `data/mt5_data.py` — MT5DataFeed (ticks, candles, tick normalization)
- `core/stale_monitor.py` — StaleDataMonitor
- `core/mt5_connection.py` — health checks, reconnect
- `core/mt5_timeout.py` — mt5_call timeout wrapper

### Runtime Position
Called every cycle in live_scanner per symbol. First data acquisition step.

### Authority Status
PRIMARY AUTHORITY

### Keep / Move / Delete
- KEEP: All
- Note: `_TICK_UTC_OFFSET_SECONDS` measured at first tick, subtracted from tick timestamps. Candle timestamps remain broker-local. `_closed_time_utc` conversion applied in live_scanner for age calculations.

### Refactor Risk
Medium (timestamp handling is critical)

### Requires Human Review
NO

---

## 4. Feature Processing System

### Responsibility
Compute market features from raw candle/tick data. Produce scores and classifications.

### Files (Active — New Engine)
- `core/pipeline/new_engine.py` — 10-factor scoring, dual score
- `core/pipeline/strategy_weights.py` — weight profiles per strategy
- `core/pipeline/strategy_classifier.py` — StrategyType classification
- `core/pipeline/market_state_engine.py` — STRUCTURED/TRANSITIONAL/CHOP
- `core/pipeline/swing_context.py` — macro swing structure
- `core/pipeline/bias_fsm.py` — bias state machine (new engine version)
- `strategy/selection_activation.py` — strategy activation pipeline
- `strategy/eligibility_activation.py` — eligibility check
- `strategy/gating_activation.py` — gating rules
- `strategy/mapping_activation.py` — strategy mapping
- `strategy/regime_activation.py` — regime-based activation
- `strategy/schema_activation.py` — activation schema
- `strategy/structure_bias_scoring.py` — structure/bias scores (optional, try/except)

### Files (Legacy — Gated, Currently Disabled)
- `core/engine.py` — process_bar() (old pipeline entry)
- `core/pipeline/scoring_engine.py` — old integer scoring
- `core/pipeline/market_context.py` — old market context
- `core/pipeline/structure_analysis.py` — old structure (includes bias FSM)
- `core/pipeline/confirmations.py` — old confirmation gates
- `core/pipeline/trade_quality.py` — old quality gates
- `core/pipeline/strategy_detection.py` — old pattern/strategy detection
- `core/pipeline/decision_engine.py` — old DecisionEngine
- `core/pipeline/pipeline_authority.py` — old authority
- `core/pipeline/finish_params.py` — old FinishParams
- `core/pipeline/intent_builder.py` — old build_intent
- `core/pipeline/dashboard.py` — old cycle counters
- `core/pipeline/filter_stats.py` — old filter stats
- `core/pipeline/bias_context.py` — old bias context
- `core/pipeline/bias_thresholds.py` — old thresholds
- `core/pipeline/structure_confidence.py` — old structure confidence
- `core/pipeline/eligibility.py` — old eligibility
- `core/pipeline_types.py` — UnifiedDecision types
- `core/stability/` — stability gate system
- `core/state/` — StateSnapshot, StateDelta
- `core/voters/` — voter system
- `core/drift/` — drift detection
- `core/features/engine.py` — compute_features (old)
- `strategy/chop_filter.py` — old chop filter
- `strategy/market_filter.py` — old market filter
- `strategy/trend_filter.py` — old trend filter
- `strategy/setup.py` — old setup detection

### Runtime Position
New engine: called per symbol per new bar inside live_scanner.  
Legacy: NOT called when `ENABLE_LEGACY_SHADOW_PIPELINE=False`.

### Authority Status
- New Engine: PRIMARY AUTHORITY
- Legacy Pipeline: LEGACY (gated, preserved for replay/comparison)

### Keep / Move / Delete
- KEEP: All new engine files
- KEEP (gated): All legacy files (needed for replay mode, historical comparison)
- DELETE: `core/pipeline/runner.py` (zero imports), `core/pipeline/_DEPRCATED_intelligence_engine.py` (deprecated)

### Refactor Risk
High (this is the core decision logic)

### Requires Human Review
YES — Decision needed: Should the legacy pipeline be permanently deleted or kept indefinitely for replay? If deleted, ~25 files can be removed.

---

## 5. Decision System

### Responsibility
Evaluate opportunities, apply policy gates, produce EXECUTE/NO_TRADE decisions with full audit trail.

### Files
- `core/pipeline/execution_policy.py` — EV-first policy gates
- `core/pipeline/expected_value.py` — EV computation
- `core/pipeline/control_layer.py` — final control gate
- `core/models/opportunity_assessment.py` — OpportunityAssessment
- `core/decision_audit.py` — decision audit persistence
- `core/decision_ledger.py` — decision ledger (every cycle)
- `core/decision_trace.py` — DecisionTrace + DecisionFunnel
- `core/correlation.py` — correlation_id generation

### Runtime Position
Inside new_engine evaluation. Policy computed twice (pre-risk and post-EV).

### Authority Status
PRIMARY AUTHORITY

### Keep / Move / Delete
- KEEP: All

### Refactor Risk
Medium

### Requires Human Review
NO

---

## 6. Strategy / Pattern System

### Responsibility
Detect candlestick patterns. Classify into strategies.

### Files
- `strategy/signal_orchestrator.py` — evaluate_closed_bar (pattern gate)
- `strategy/signals.py` — Signal, Side
- `strategy/trace_activation.py` — strategy trace logging
- `patterns/` (all 12 files) — pattern detection implementations
- `patterns/registry.py` — pattern registry
- `patterns/ids.py` — pattern name constants

### Runtime Position
Called per new bar. Pattern gate runs BEFORE new engine.

### Authority Status
PRIMARY AUTHORITY

### Keep / Move / Delete
- KEEP: All

### Refactor Risk
Low

### Requires Human Review
NO

---

## 7. Risk System

### Responsibility
Compute SL/TP/position sizing. Validate exposure limits. Guard against excessive risk.

### Files
- `risk/manager.py` — RiskManager.evaluate() (assessment-based)
- `risk/levels.py` — build_sl_tp (SLTP rules)
- `risk/models.py` — OrderIntent
- `risk/decision.py` — RiskDecision (accept/reject)
- `risk/position_sizing.py` — volume_for_risk
- `risk/metrics.py` — risk metrics
- `risk/guards.py` — count_bot_positions
- `risk/spread_guard.py` — spread check (in execution)
- `risk/drawdown_guard.py` — DrawdownGuard
- `risk/daily_loss_guard.py` — DailyLossGuard
- `risk/daily_trade_limit.py` — DailyTradeLimitManager
- `risk/trade_cooldown.py` — TradeCooldownManager
- `risk/correlation_guard.py` — check_correlation
- `risk/portfolio_exposure_guard.py` — check_portfolio_exposure
- `risk/regime_guard.py` — check_regime
- `risk/session_guard.py` — check_session
- `risk/risk_summary.py` — risk summary emission
- `risk/risk_timeline.py` — risk timeline

### Runtime Position
Risk evaluation inside new_engine. Runtime guards in live_scanner after engine returns EXECUTE.

### Authority Status
PRIMARY AUTHORITY

### Keep / Move / Delete
- KEEP: All

### Refactor Risk
Low

### Requires Human Review
NO

---

## 8. Execution System

### Responsibility
Submit orders to MT5 broker. Handle fills, requotes, timeouts. Report execution results.

### Files
- `execution/mt5_execution.py` — MT5Execution.execute() / place_market()
- `core/position_ownership.py` — ownership validation
- `core/slippage_monitor.py` — slippage recording

### Runtime Position
Called AFTER all guards pass. Last step before broker interaction.

### Authority Status
PRIMARY AUTHORITY

### Keep / Move / Delete
- KEEP: All

### Refactor Risk
Low

### Requires Human Review
NO

---

## 9. Persistence System

### Responsibility
Persist decision records, trade outcomes, event streams, and analytics to local JSONL and S3.

### Files
- `core/event_stream.py` — unified event persistence (allowlist-gated)
- `core/storage/s3_batch_writer.py` — S3 batch writer
- `core/decision_audit.py` — decision audit JSONL + S3
- `core/decision_ledger.py` — decision ledger JSONL + S3
- `core/decision_trace.py` — decision trace JSONL (local only)
- `core/execution_context.py` — execution context JSONL + S3
- `core/shadow_trades.py` — shadow trade JSONL + S3
- `core/trade_truth.py` — trade truth JSONL + S3
- `core/trade_truth_graph.py` — graph nodes JSONL + S3
- `core/edge_attribution.py` — edge attribution JSONL + S3
- `core/edge_optimisation.py` — edge optimisation JSONL + S3
- `core/strategy_compiler.py` — strategy compiler JSONL + S3
- `core/state_persistence.py` — EngineState warm start
- `core/persistence/opportunity_assessment_writer.py` — assessment JSONL
- `core/persistence/decision_trace_writer.py` — trace writer
- `core/learning/store.py` — learning records JSONL + S3
- `core/trade_journal.py` — trade journal
- `core/equity_curve_tracker.py` — equity curve
- `core/aws_uploader.py` — no-op adapter (legacy interface)

### Runtime Position
Called from multiple points: after engine evaluation, after execution, on trade close.

### Authority Status
PRIMARY AUTHORITY

### Keep / Move / Delete
- KEEP: All active persistence modules
- Note: `core/aws_uploader.py` is a no-op but kept for interface compatibility

### Refactor Risk
Medium (S3 architecture guard enforces allowlist)

### Requires Human Review
NO

---

## 10. Observability System

### Responsibility
Explain decisions, measure uncertainty, attribute scores, enable learning/replay.

### Files
- `core/reasoning/` — DecisionReasoning (3 files)
- `core/uncertainty/` — UncertaintyAssessment (3 files)
- `core/attribution/` — ScoreAttribution (3 files)
- `core/learning/` — Learning engine + calibration (5 files)
- `core/pipeline/event_observer.py` — Discord state-change alerts
- `core/pipeline/forensic_logger.py` — per-pair gate trace
- `core/pipeline/entity_tracker.py` — entity lifecycle tracking
- `core/pipeline/visibility_layer.py` — design vs reality trace
- `core/pipeline/trade_narrative.py` — trade narrative formatting
- `core/pipeline/output_router.py` — routing to Discord/AWS
- `core/pipeline/timestamps.py` — tri-timestamp formatting
- `core/pipeline/divergence_logger.py` — divergence logging
- `core/pipeline/opportunity_ranker.py` — opportunity ranking
- `core/pipeline/scoring_inputs.py` — scoring input logging
- `core/discord_notifier.py` — Discord webhook
- `core/log_router.py` — StructuredLogger
- `core/quiet_period_diagnostics.py` — rejection tracking
- `core/heartbeat.py` — heartbeat writing
- `core/watchdog.py` — external watchdog process
- `core/dashboard_metrics.py` — dashboard emission
- `core/audit_persistence.py` — S3/local audit inspector
- `core/offline_query.py` — offline query tool
- `core/causal/` — causal replay engine

### Runtime Position
Passive observers. Called after engine evaluation. Never affect decisions.

### Authority Status
SUPPORTING COMPONENT (never gates or blocks)

### Keep / Move / Delete
- KEEP: All

### Refactor Risk
Low (all wrapped in try/except: pass)

### Requires Human Review
NO

---

## 11. Testing / Tooling System

### Responsibility
Automated tests, offline analysis, replay tools, calibration.

### Files
- `tests/` — all test files (~80 files)
- `tools/` — replay engine, MTF calibration
- `analysis/` — offline analysis scripts + reports
- `data_pipeline/` — AWS Glue setup, query layer
- `scripts/` — utility scripts
- `architecture/` — architecture contracts + docs
- `core/contracts/` — test-time validators

### Runtime Position
Not on live runtime path. Used for validation and offline analysis.

### Authority Status
SUPPORTING COMPONENT

### Keep / Move / Delete
- KEEP: All

### Refactor Risk
Low

### Requires Human Review
NO

---

# FINAL REFACTORING PLAN

## 1. Immediate Deletions (Safe)

| File | Reason | Risk |
|------|--------|------|
| Done`core/pipeline/trade_lifecycle_fsm.py` | Zero imports | None |
| Done`core/pipeline/trade_profile.py` | Only imported by above | None |
| Done`core/pipeline/runner.py` | Zero imports | None |
| Done`core/pipeline/_DEPRCATED_intelligence_engine.py` | Deprecated, zero imports | None |
| Done`core/observability_validator.py` | Zero imports | None |
| Done`core/calibration/` (5 files) | Self-referencing only | None |
| Done`core/brain_logger.py` | Zero active imports | None |
| Done`core/test_all_channels.py` | Misplaced test script | None |
| Done`core/test_discord.py` | Misplaced test script | None |
| Done`core/test_router.py` | Misplaced test script | None |
| `core/models/identity.py` | Empty | None |
| `core/models/opportunity.py` | Empty | None |
| `core/models/evidence.py` | Empty | None |
| `core/models/evidence_contribution.py` | Empty | None |
| `core/models/uncertainty.py` | Empty | None |
| `core/models/reasoning.py` | Empty | None |
| `core/models/risk_assessment.py` | Empty | None |
| `core/models/execution_intent.py` | Empty | None |
| Done`MagicMock/` | Test artifact | None |
| Done`dir` (root) | Stray file | None |
| Done`for` (root) | Stray file | None |

## 2. Migrations Required Before Deletion

None. All DELETE candidates have zero external dependencies.

## 3. Ownership Conflicts to Resolve

| Conflict | Description | Resolution |
|----------|-------------|-----------|
| Bias FSM dual implementation | `core/pipeline/bias_fsm.py` (new) vs bias FSM inside `core/pipeline/structure_analysis.py` (old) | New engine uses `bias_fsm.py`. Old one is gated. No conflict at runtime. |
| Dashboard counters vs DecisionFunnel | `core/pipeline/dashboard.py` (old counters, dead) vs `core/decision_trace.py` DecisionFunnel (active) | DecisionFunnel is authoritative. Dashboard counters are dead (legacy disabled). |
| `_filter_hits` vs DecisionFunnel | Parallel counting systems in live_scanner | DecisionFunnel now produces the console display. `_filter_hits` remains as redundant parallel (safe to remove after validation). |

## 4. Recommended Refactor Order

1. **Delete proven dead code** (21 files/dirs listed above)
2. **Remove `_filter_hits` parallel counting** from live_scanner (DecisionFunnel now authoritative)
3. **Remove dead Discord diagnostic** (reads from `get_dashboard_metrics()` which is dead)
4. **Remove legacy config values** that only serve disabled pipeline
5. **Decide legacy pipeline fate** — permanent delete or keep for replay?

## 5. Systems That Should Be FROZEN (Do Not Change)

| System | Reason |
|--------|--------|
| New Engine (`new_engine.py`) | Sole execution authority — changes here affect all trading |
| Risk Manager (`risk/manager.py`) | Position sizing + SL/TP — critical financial safety |
| MT5 Execution (`execution/mt5_execution.py`) | Broker interaction — changes can cause real losses |
| Pattern Detection (`patterns/`) | Pattern logic is stable and well-tested |
| Event Stream (`core/event_stream.py`) | S3 persistence — schema changes affect analytics |

## 6. Remaining Unknowns

| Unknown | What's Needed |
|---------|--------------|
| `core/pipeline/execution.py` | Imported by new_engine — needs content verification (may be a utility or dead wrapper) |
| `phase5/` directory | Content unknown — needs listing to determine if dead |
| `architecture/` directory | Contains contracts — verify if any enforce runtime behaviour |
| Legacy pipeline permanent fate | Human decision: delete entirely or preserve for replay? |
