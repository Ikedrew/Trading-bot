# Research Engine Phase 0 — Repository Audit

**Generated:** 2026-07-19  
**Objective:** Discover every existing data asset that could feed a future Research Engine  
**Method:** Full repository scan — persistence layers, event streams, features, schemas, consumers

---

## Research Asset Inventory

### Tier 1: Immediately Usable (rich schema, consistent, production-proven)

| # | Asset | Location | Schema Fields | Frequency | Research Value | Ready? |
|---|-------|----------|---------------|-----------|---------------|--------|
| 1 | **Decision Trace** | `logs/decision_trace/{SYM}/{DATE}.jsonl` + S3 | entity_id, action, terminal_stage, terminal_reason, stages_reached/passed, pattern (name/quality/count), regime (+confidence), selected_strategy, score_neutral/strategy/delta, components dict, weights_used, weakest_component, largest_drag, threshold_gap, closest_flip, ev, htf_alignment | Every engine evaluation | **5/5** — Richest per-decision feature set | ✅ Yes |
| 2 | **Decision Ledger** | `logs/decision_ledger/{SYM}/{DATE}.jsonl` + S3 | timestamp, symbol, cycle_id, decision outcome, reason, regime, session_state, signal_score, signal_type, pattern_state, risk_state (drawdown/daily_loss/exposure), execution_intent, reasoning dict, uncertainty dict, score_attribution dict, causal_signature, correlation_id, entity_id, decision_latency_ms | Every cycle (every symbol) | **5/5** — Complete decision history with reasoning | ✅ Yes |
| 3 | **Shadow Trades** | `logs/shadow_trades/{SYM}/{DATE}.jsonl` + S3 | trade_id, correlation_id, symbol, strategy_id, cycle_id, timestamp_decision, entry_intent_price, sl/tp_intent, direction, position_size, pattern, score, htf_snapshot, entry_bar_index, exit_price, exit_timestamp, pnl_r_multiple, mfe_r, mae_r, exit_reason, bars_held, trade_state_progression[] | Every EXECUTE signal (simulated lifecycle) | **5/5** — R-multiple outcomes without broker execution | ✅ Yes |
| 4 | **Execution Context** | `logs/execution_context/{SYM}/{DATE}.jsonl` + S3 | correlation_id, symbol, timestamp_utc, market_access (session, spread, spread_atr_ratio, bid, ask), infrastructure (latency_ms, feed_state, tick_age_ms, bars_since_gap), risk_environment (drawdown_pct, daily_loss_pct, open_positions, correlation_exposure), events_ref | Every decision cycle + every EXECUTE | **5/5** — Environmental context for causal analysis | ✅ Yes |
| 5 | **Opportunity Assessment** | `logs/opportunity_assessment_log/{SYM}/{DATE}.jsonl` + S3 | Full OpportunityAssessment dataclass: market context, scoring components, HTF analysis, strategy classification, confidence metrics | Every engine evaluation producing an assessment | **5/5** — Pre-decision market analysis | ✅ Yes |
| 6 | **Event Stream (CANDLE)** | `events/{DATE}.jsonl` + S3 | ts_utc_ms, symbol, OHLCV payload | Every closed M5 bar | **4/5** — Raw market data (core for backtesting) | ✅ Yes |
| 7 | **Event Stream (FEATURE_UPDATE)** | `events/{DATE}.jsonl` + S3 | ts_utc_ms, symbol, ATR, ATR_ratio, structure_clarity, sweep detection, candle_overlap_ratio | On material change (materiality gate) | **4/5** — Computed features at observation time | ✅ Yes |

### Tier 2: Valuable with Joining (requires correlation_id linkage)

| # | Asset | Location | Schema Fields | Frequency | Research Value | Ready? |
|---|-------|----------|---------------|-----------|---------------|--------|
| 8 | **Trade Truth (v3)** | `logs/trade_truth/{SYM}/{DATE}.jsonl` + S3 | trade_id, correlation_id, symbol, entry/exit_fill_price, volume, slippage, spread, timestamps, pnl_realised, r_multiple_realised, commission, swap, net_profit, exit_reason | Every real trade close | **5/5** — Ground truth outcomes (actual P&L) | ✅ Yes |
| 9 | **Trade Truth Graph (v2)** | `logs/trade_truth_graph/{SYM}/{DATE}.jsonl` + S3 | graph_node_id, trade_id, correlation_id, temporal (event_window), refs (events/context/shadow/truth), relationships (session/regime/pattern/symbol), edges[] | Every trade close | **4/5** — Causal relationships | ✅ Yes |
| 10 | **Execution Results** | `logs/execution_results/{SYM}/{DATE}.jsonl` + S3 | timestamp, symbol, cycle_id, result_ok, retcode, deal, order, fill_price, slippage, side, volume, entry_reference, sl, tp, pattern, decision_id, correlation_id | Every broker call | **4/5** — Execution quality analysis | ✅ Yes |
| 11 | **Trade Journal** | `logs/trade_journal/{DATE}.jsonl` | trade_id, position_ticket, symbol, magic, pattern_name, direction, entry/exit_time, duration, entry/exit_price, volume, realised_pnl, commission, swap, net_pnl, close_reason, sl, tp, max_favourable_price | Every trade close (local only) | **4/5** — Complete trade record | ✅ Yes |
| 12 | **Learning Records** | `logs/learning/{DATE}.jsonl` + S3 | decision_id, thesis, evidence_quality, uncertainty_score, outcome, calibration_result, insights, metadata | Per-decision analysis (when learning engine runs) | **4/5** — Calibration quality | ✅ Yes |

### Tier 3: Supplementary (operational telemetry)

| # | Asset | Location | Schema Fields | Frequency | Research Value | Ready? |
|---|-------|----------|---------------|-----------|---------------|--------|
| 13 | **Event Stream (FEED_HEALTH)** | `events/{DATE}.jsonl` | transition (FRESH_TO_STALE / STALE_TO_FRESH), feed_type, stale_duration | On feed transitions | **3/5** — Data quality signals | ✅ Yes |
| 14 | **Event Stream (SYSTEM_HEALTH)** | `events/{DATE}.jsonl` | incident_type (MT5_DISCONNECT/HOST_SUSPEND/EVENT_LOOP_STALL), gap_minutes | On runtime gaps | **2/5** — Availability metrics | ✅ Yes |
| 15 | **Risk State (drawdown)** | `logs/drawdown_peak.json` | peak_equity, last_updated | On equity high watermark change | **2/5** — Risk state snapshots | ✅ Yes |
| 16 | **Risk State (daily loss)** | `logs/daily_loss_state.json` | date, daily_start_equity, limit_triggered | Daily | **2/5** — Risk state | ✅ Yes |
| 17 | **Engine State** | `logs/state/{SYMBOL}.json` | current_bias, bias_phase, bias_strength, regime_state, volatility_filter, structure_score, last_successful_open_mono | Every checkpoint | **3/5** — FSM state snapshots | ✅ Yes |
| 18 | **Heartbeat** | `logs/heartbeat.json` | timestamp, cycle_id, status, latency_ms, symbols, mt5_state | Every cycle | **1/5** — Liveness only | ✅ Yes |
| 19 | **Replay Data** | `replay_data/{SYM}_{TF}.{ext}` | OHLCV candle arrays (M5, M15, H1, H4) for 7 pairs | Static (historical) | **4/5** — Backtesting dataset | ✅ Yes |

---

## Correlation Spine

All layers are joinable via `correlation_id`:

```
correlation_id (generated per EXECUTE decision)
    │
    ├── Decision Ledger     (decision + reasoning)
    ├── Execution Context   (environment snapshot)
    ├── Shadow Trades       (simulated outcome)
    ├── Trade Truth         (actual outcome)
    ├── Trade Truth Graph   (relationship graph)
    ├── Execution Results   (broker interaction)
    └── Opportunity Assessment (pre-decision analysis)
```

**Every EXECUTE decision produces a linked chain of records across all layers.**

---

## Dependency Map

```
MT5 Terminal (live data)
    ↓
┌──────────────────────────────────────────────────────────────┐
│ EXECUTION ENGINE (live runtime)                               │
│                                                               │
│ Market Data → Features → Engine A → Decision → Risk → Broker │
└──┬───────────┬─────────┬──────────┬─────────┬───────────────┘
   │           │         │          │         │
   ▼           ▼         ▼          ▼         ▼
┌──────────────────────────────────────────────────────────────┐
│ PERSISTENCE LAYER (generated data)                           │
│                                                               │
│ events/ │ decision_trace/ │ decision_ledger/ │ shadow_trades/ │
│ execution_context/ │ trade_truth/ │ trade_journal/ │ learning/ │
│ opportunity_assessment/ │ execution_results/ │ trade_truth_graph/ │
└──┬───────────────────────────────────────────────────────────┘
   │
   ▼
┌──────────────────────────────────────────────────────────────┐
│ CURRENT CONSUMERS                                             │
│                                                               │
│ data_pipeline/query_layer.py (offline unified query)          │
│ data_pipeline/transform_events.py (curated events → Athena)  │
│ core/learning/engine.py (calibration analysis)                │
│ Replay system (historical candle replay)                      │
│ AWS Athena (SQL queries via Glue crawler)                     │
└──────────────────────────────────────────────────────────────┘
   │
   ▼ (FUTURE)
┌──────────────────────────────────────────────────────────────┐
│ RESEARCH ENGINE (potential consumer)                          │
│                                                               │
│ All 19 assets immediately available without instrumentation  │
│ Correlation spine enables cross-layer joins                   │
│ Learning engine provides calibration ground truth             │
│ Shadow trades provide outcome labels without broker risk      │
└──────────────────────────────────────────────────────────────┘
```

---

## Missing Links

| # | Gap | Description | Impact |
|---|-----|-------------|--------|
| 1 | **Execution Results — no consumer** | Every broker call is persisted but never analysed | Slippage patterns, fill rate, retcode distribution unexamined |
| 2 | **Opportunity Assessment — no consumer** | Rich pre-decision analysis written but never read back | Market classification effectiveness unmeasured |
| 3 | **Trade Truth Graph — no consumer** | Causal graph built but never queried | Relationship analysis unavailable |
| 4 | **Learning Records — no feedback loop** | Calibration analysis runs but results never influence scoring | One-way observation, not adaptive |
| 5 | **Feature materiality changes — not correlated to decisions** | FEATURE_UPDATE events and decision_trace exist but no explicit join | Cannot easily measure "did this feature change cause a different decision?" |
| 6 | **Event types silently rejected** | Legacy event types (DECISION, EXECUTION, OUTCOME, RISK_CHECK) are dropped by allowlist | Some historical data pathways produce nothing |
| 7 | **Shadow trade MFE/MAE — not aggregated** | Individual trade progressions stored but no cross-trade analysis | Optimal exit timing analysis missing |

---

## Final Summary Table

| Asset | Producer | Consumer | Research Value | Ready? |
|-------|----------|----------|---------------|--------|
| Decision Trace | ObserverRegistry → decision_trace module | Console funnel only | **5/5** | ✅ |
| Decision Ledger | DecisionRecorder | learning/engine (partial), query_layer | **5/5** | ✅ |
| Shadow Trades | ShadowTradeEngine | query_layer (offline) | **5/5** | ✅ |
| Execution Context | execution_context_builder, engine_execution_handler | query_layer (offline) | **5/5** | ✅ |
| Opportunity Assessment | new_engine pipeline | **No consumer** | **5/5** | ✅ |
| Trade Truth | TradeStateManager lifecycle | query_layer (offline) | **5/5** | ✅ |
| Event Stream (CANDLE) | bar_provider → event_stream | replay, query_layer | **4/5** | ✅ |
| Event Stream (FEATURE) | features/engine → event_stream | query_layer | **4/5** | ✅ |
| Execution Results | execution_orchestrator | **No consumer** | **4/5** | ✅ |
| Trade Truth Graph | graph builder | **No consumer** | **4/5** | ✅ |
| Trade Journal | TradeStateManager | daily P&L, risk guards | **4/5** | ✅ |
| Learning Records | learning/engine | **No consumer** | **4/5** | ✅ |
| Replay Data | Static historical | Replay system | **4/5** | ✅ |
| Engine State | state_persistence | Startup restore | **3/5** | ✅ |
| Feed Health Events | tick_monitor | query_layer | **3/5** | ✅ |
| Risk State Files | Guards | Guards (startup restore) | **2/5** | ✅ |
| Heartbeat | health_monitor | External watchdog | **1/5** | ✅ |

---

## Conclusion

### "If a Research Engine were added today, what existing assets could it immediately leverage without requiring any additional instrumentation?"

**All 19 identified data assets are immediately consumable.**

Specifically, the following are production-ready for a Research Engine with zero additional code:

1. **Decision Trace** (richest per-decision feature vector — 30+ fields per evaluation)
2. **Decision Ledger** (every cycle with outcome, reasoning, uncertainty, and score attribution)
3. **Shadow Trades** (simulated R-multiple outcomes with full lifecycle progression)
4. **Execution Context** (frozen environment snapshot at decision time)
5. **Opportunity Assessment** (pre-decision market analysis with scoring components)
6. **Trade Truth** (ground-truth outcomes for live trades)
7. **Event Stream** (CANDLE + FEATURE_UPDATE — raw market observations)
8. **Replay Data** (7 pairs × 4 timeframes — historical backtesting)
9. **Learning Records** (calibration quality analysis)
10. **Execution Results** (broker interaction quality)
11. **Trade Truth Graph** (causal relationship network)

**The correlation_id spine enables cross-layer joins** connecting decisions → context → outcomes → analysis across all layers without any additional instrumentation.

**The system already produces 5/5 research-value data at every decision cycle.** A Research Engine could begin consuming immediately by reading the existing JSONL files and S3 mirrors.
