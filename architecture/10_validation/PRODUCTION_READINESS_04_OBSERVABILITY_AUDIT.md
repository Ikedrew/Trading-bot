# Production Readiness Audit #4 — Observability

**Generated:** 2026-07-18  
**Context:** Post live_scanner refactor — all observability channels verified  
**Method:** Grep analysis of every emit/log/Discord/print call across runtime modules

---

## Observability Channel Map

| Channel | Mechanism | Consumer | Still Active? |
|---------|-----------|----------|---------------|
| **Heartbeat File** | JSON file (`logs/heartbeat.json`) | External watchdog | ✅ |
| **Decision Ledger** | JSONL file | Dashboard, analytics | ✅ |
| **Event Stream** (local + S3) | `emit_system_health`, `emit_feed_health`, `emit_feature_update` | Offline analysis | ✅ |
| **Decision Audit** (local + S3) | `persist_decision_audit`, `persist_new_engine_decision_audit` | Forensic reconstruction | ✅ |
| **Discord Alerts** | `_discord_logger.event()` | Operator Discord channels | ✅ |
| **Console Logging** | `logger.info/warning/error` | Process stdout/stderr | ✅ |
| **Console Print** | `print(f"[TAG]...")` | Process stdout (structured) | ✅ |
| **Risk Event Bus** | `emit_risk_guard_result()` | Risk monitoring layer | ✅ |
| **Pipeline Diagnostics** | `emit_pipeline_diagnostics()` | Console + Discord (throttled) | ✅ |
| **Cycle Report** | `emit_cycle_report()` | Console + Discord (per-cycle) | ✅ |
| **Risk Timeline** | `record_risk_snapshot()` | Offline risk analysis | ✅ |
| **Execution Metrics** | `_record_metrics()` in `mt5_execution.py` | Internal counters | ✅ |

---

## Discord Event Coverage

| Event Type | Source Module | Trigger | Throttled? | Fire-and-Forget? |
|-----------|-------------|---------|-----------|-----------------|
| `HEARTBEAT` | `health_monitor.py` | Every 10 cycles | ✅ (1-in-10) | ✅ |
| `RISK_BLOCK` (drawdown) | `cycle_guards.py` | Drawdown limit exceeded | ❌ | ✅ |
| `RISK_BLOCK` (daily_loss) | `cycle_guards.py` | Daily loss breached | ❌ | ✅ |
| `RISK_BLOCK` (guard chain) | `live_scanner.py` | Any runtime guard blocks | ❌ | ✅ |
| `ERROR` (engine crash) | `live_scanner.py` | Engine A exception | ❌ | ✅ |
| `ERROR` (unknown stage) | `live_scanner.py` | Per-symbol catch-all exception | ❌ | ✅ |
| `ERROR` (runtime gap) | `runtime_state_classifier.py` | Gap >60s detected | ❌ | ✅ |
| `ERROR` (execution) | `execution_orchestrator.py` | Broker call exception | ❌ | ✅ |
| `TRADE_DECISION` (success) | `post_execution_handler.py` | Trade executed | ❌ | ✅ |
| `PIPELINE_DROP` | `live_scanner.py` (dead code path) | Advanced-stage rejection | ❌ | ✅ |
| `FEED_STALE` | `bar_provider.py` → `send_discord("errors")` | Feed >30min old | ❌ | ✅ |
| `FEED_STALL` | `bar_provider.py` → `send_discord("errors")` | Stale counter >50 | ❌ | ✅ |
| `MARKET_SNAPSHOT` | `cycle_report.py` | Every 25 cycles (with drops) | ✅ (1-in-25) | ✅ |
| `H4_CONTEXT` | `live_scanner.py` (HTF logging) | First bar or every 50 cycles | ✅ | ✅ |
| Diagnostic report | `pipeline_diagnostics.py` | Every 50 cycles | ✅ (1-in-50) | ✅ |
| Calibration report | `pipeline_diagnostics.py` | Cycle 100 | One-shot | ✅ |
| Dashboard metrics | `pipeline_diagnostics.py` | Every 50 cycles | ✅ | ✅ |

**All Discord events are fire-and-forget.** No Discord failure can affect trading.

---

## Logging Coverage by Pipeline Stage

| Stage | Logger Module | Log Level | Tags | Active? |
|-------|-------------|-----------|------|---------|
| Tick fetch fail | `live_scanner.py` | INFO | `[LIVE_SCANNER]` | ✅ |
| Tick stale | `tick_monitor.py` | WARNING | `[STALE_DATA]` | ✅ |
| Tick recovery | `tick_monitor.py` | INFO | `[STALE_DATA]` | ✅ |
| Bar provision | `bar_provider.py` | print | `[CANDLE SELECT]`, `[MARKET STATE]`, `[BAR CHECK]` | ✅ |
| Feed stale | `bar_provider.py` | print | `[FEED BLOCKED]`, `[BAR STALL]` | ✅ |
| Session check | `pre_engine_gates.py` | print | `[SESSION CHECK]`, `[PIPELINE ENTRY]` | ✅ |
| Pattern detection | `pre_engine_gates.py` | print | `[PATTERN RESULT]`, `[PATTERN GATE]` | ✅ |
| Engine A result | `live_scanner.py` | print | `[NEW ENGINE]` | ✅ |
| Bias FSM | `live_scanner.py` | print | `[BIAS FSM]` | ✅ |
| Guard block | `live_scanner.py` | INFO | `[STATE] BLOCKED_*` | ✅ |
| Execution entry | `live_scanner.py` | INFO | `[STATE] ENTRY` | ✅ |
| Execution result | `execution_orchestrator.py` | INFO | `[STATE]` | ✅ |
| MT5 health | `mt5_health.py` | INFO | `[LIVE_SCANNER] RECONNECT/DISCONNECTED` | ✅ |
| Runtime gap | `runtime_state_classifier.py` | WARNING | `[RUNTIME_CLASSIFIER]` | ✅ |
| Symbol init | `scanner_init.py` | INFO/ERROR | `[SYMBOL_INIT]`, `[SYMBOL_RESOLUTION]` | ✅ |
| Reconciliation | `live_scanner.py` | INFO | `[RECONCILIATION_START/COMPLETE]` | ✅ |
| State checkpoint | `live_scanner.py` | INFO | `[STATE_CHECKPOINT]` | ✅ |
| Shutdown | `live_scanner.py` | INFO | `[SHUTDOWN]` | ✅ |

---

## Health Monitor Verification

| Function | Active? | Trigger | Output |
|----------|---------|---------|--------|
| `write_heartbeat("alive")` | ✅ | Every cycle | `logs/heartbeat.json` |
| `write_heartbeat("mt5_disconnected")` | ✅ | MT5 health fail | `logs/heartbeat.json` |
| `write_heartbeat("drawdown_blocked")` | ✅ | Cycle-level drawdown block | `logs/heartbeat.json` |
| `log_heartbeat()` | ✅ | Every cycle | Event bus |
| `log_liveness_status("OK"/"STALLED")` | ✅ | Every cycle | Event bus |
| No-trade alert | ✅ | After threshold consecutive no-trade cycles | Logger + `emit_quiet_period_alert()` |
| Discord heartbeat | ✅ | Every 10 cycles | Discord `HEARTBEAT` event |

---

## Diagnostic Output Verification

| Diagnostic | Owner | Trigger | Output | Active? |
|-----------|-------|---------|--------|---------|
| Decision Funnel | `pipeline_diagnostics.py` | Every 50 cycles | Console print | ✅ |
| Score Pressure Report | `pipeline_diagnostics.py` | Every 50 cycles (if rejections) | Console print | ✅ |
| Component Pressure Report | `pipeline_diagnostics.py` | Every 100 cycles | Console print | ✅ |
| Calibration Report | `pipeline_diagnostics.py` | Cycle 100 | Console + Discord | ✅ |
| Paper Outcome Report | `pipeline_diagnostics.py` | Every 100 cycles | Console print | ✅ |
| Dashboard Metrics | `pipeline_diagnostics.py` | Every 50 cycles | Discord | ✅ |
| Opportunity Ranking | `live_scanner.py` | End of cycle (if candidates) | Console print | ✅ |
| Cycle Report | `cycle_report.py` | End of cycle | Console + Discord (per-25) | ✅ |
| Filter Hits | Accumulated in `_filter_hits` | Every rejection | Consumed by diagnostics | ✅ |
| Score Tracker | Accumulated in `_score_tracker` | Every scoring | Consumed by diagnostics | ✅ |

---

## Event Emission Coverage

| Event Type | Emitter | Stage | Active? |
|-----------|---------|-------|---------|
| `DECISION_EVALUATED` | `live_scanner.py` → `emit_event()` | Post-TradeDecision | ✅ |
| Bias events | `live_scanner.py` → `emit_bias_events()` | Post-TradeDecision | ✅ |
| Setup events | `live_scanner.py` → `emit_setup_events()` | Post-TradeDecision | ✅ |
| Trade events (success) | `post_execution_handler.py` → `emit_trade_events()` | Post-execution success | ✅ |
| Trade events (failure) | `post_execution_handler.py` → `emit_trade_events()` | Post-execution failure | ✅ |
| Risk guard result | `tick_monitor.py`, `cycle_guards.py`, `live_scanner.py` | Guard evaluations | ✅ |
| Feed health | `tick_monitor.py` | Stale transitions | ✅ |
| System health | `runtime_state_classifier.py` | Runtime gaps | ✅ |
| Feature update (HTF) | `live_scanner.py` → `emit_feature_update()` | HTF context available | ✅ |
| Risk timeline | `live_scanner.py` → `record_risk_snapshot()` | Every cycle | ✅ |

---

## Observer Registry Coverage

| Observer | Module | Trigger | What it sees |
|----------|--------|---------|-------------|
| Event observer | `core/pipeline/event_observer.py` | Engine result | Decision state changes |
| Forensic logger | `core/pipeline/forensic_logger.py` | Engine result | Full gate trace |
| Entity tracker | `core/pipeline/entity_tracker.py` | Engine result | Continuous state |
| Visibility layer | `core/pipeline/visibility_layer.py` | Engine result | Design vs reality gap |
| Shadow rooms | `core/pipeline/shadow_rooms.py` | Engine result | Parallel compute |
| Decision trace | `core/decision_trace.py` | Engine result | Hierarchical trace |

**All 6 observers fire via `ObserverRegistry.notify_all()` on every engine evaluation.**

---

## Observability Gaps

| Gap | Severity | Impact | Mitigation |
|-----|----------|--------|-----------|
| Execution exception (`executed=False`) has no Discord alert | Low | Operator may not see broker connection errors immediately | `execution_orchestrator.py` already emits Discord ERROR event on exception |
| Decision ledger not finalized on execution exception | Low | Gap in ledger for edge case | Decision audit already persisted before execution; invariant enforcement handles |
| `PIPELINE_DROP` Discord event is in unreachable code | None | Dead code — no impact | Was part of removed legacy NO_TRADE path |

---

## Discord Reliability

| Principle | Status | Evidence |
|-----------|--------|----------|
| All Discord calls are fire-and-forget | ✅ | Every `_dl.event()` is inside `try/except Exception: pass` |
| No Discord return value influences decisions | ✅ | Verified by `test_discord_presentation_contract.py` |
| Discord failure never blocks trading | ✅ | Verified by static analysis test |
| Discord calls appear AFTER persistence | ✅ | StructuredLogger uploads BEFORE Discord (`test_discord_presentation_contract.py`) |
| No direct `send_discord` in live_scanner | ✅ | All via `_discord_logger.event()` or extracted modules |

---

## Final Verdict

| Observability Aspect | Status |
|---------------------|--------|
| Heartbeat (file + event bus) | ✅ Every cycle + early exits |
| Health monitoring (stall, gap, no-trade) | ✅ All detectors active |
| Decision audit trail | ✅ Every decision path persisted |
| Execution metrics | ✅ Every broker interaction recorded |
| Discord operator alerts | ✅ 14+ event types, all fire-and-forget |
| Console diagnostics | ✅ Score pressure, calibration, pipeline trace |
| Event stream (S3) | ✅ Feed health, system health, features |
| Observer pipeline | ✅ 6 observers fire on every engine evaluation |
| Risk event bus | ✅ Every guard evaluation emits |
| Cycle-level reporting | ✅ Drops, ranking, health, diagnostics |

**Full observability is maintained.** The refactor moved event emission to correct owner modules without losing any visibility channel. Every runtime event that was observable before the refactor remains observable after.
