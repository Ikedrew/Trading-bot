# TARGET DATA ARCHITECTURE — IMPLEMENTATION MAP

> **Authoritative blueprint for the rebuilt Data/Observation layer.** This document is the single source of truth for how the new Data architecture maps onto the existing Trading system, what already exists, what is future work, what is deferred, and what remains undecided. It is a **blueprint for later implementation**, not an implementation itself.

---

## STATUS-LABEL LEGEND

Each claim below is tagged with one of the following statuses:

- **FIXED DECISION** — an architecture decision already made (see §5). Must not be reconsidered.
- **FORENSICALLY CONFIRMED** — established by direct code/data inspection in Phase 0.
- **ALREADY IMPLEMENTED** — verified present and wired in the current codebase.
- **PARTIALLY IMPLEMENTED** — core capability exists in the current codebase but a required sub-aspect is missing or only partially satisfied.
- **IMPLEMENTED BUT NEEDS CORRECTION / RE-HOME** — capability exists in the current codebase but must be corrected or re-homed to satisfy the target architecture.
- **FUTURE IMPLEMENTATION** — required by the target architecture but not yet implemented; safe to implement later under authorised work.
- **KNOWN DEFECT — DEFERRED** — a real defect observed in live code that is intentionally NOT fixed during architecture mapping or subsequent Phase-0/reconciliation work unless separately authorised.
- **UNDECIDED** — genuinely not yet decided; do not invent.
- **DO NOT INVENT** — explicitly out of scope here; left for a later design decision.

---

## 1. Purpose and scope

**Purpose:** Define the target Data/Observation architecture and map every element of that target onto the existing codebase as one of the statuses above. This document lets an implementer see, at a glance, what already exists, what is future work, what must be corrected/re-homed, and what remains undecided. This update (Phase 1A) reclassifies items that the original draft overstated as absent/future but which are **already implemented** in the live path.

**Scope boundaries (TRADING / DATA / RESEARCH):**
- TRADING — makes decisions and executes. Lives in `core/pipeline/`, `core/runtime/`, `risk/`, `core/trade_management/`, `execution/`, `core/v10/`, `core/horizon/`. **Not redesigned by this architecture; only observed.**
- DATA — records the complete story of what Trading did, and the parallel Shadow story. Lives (target) in a Data-owned layer and persistence. **Redesigned from a clean slate (corrective work on existing capabilities where appropriate).**
- RESEARCH — consumes the Data layer to measure, diagnose and improve Trading. Lives in `research_engine/`, `analysis/`. **Not redesigned; only its future inputs are specified.**

**Out of scope (explicit Non-Goals):** changing trading logic, strategy logic, risk logic, execution logic, position management, schemas, persistence behaviour, wiring, runtime behaviour, or any IDs. See §18.

---

## 2. Authority / source-of-truth statement

**This document is the authoritative implementation blueprint** for the TARGET DATA ARCHITECTURE (the Data/Observation layer).

- It supersedes any conflicting prose in forensic/audit/reconciliation reports for *intent* and *status*, but **never contradicts forensic evidence**. Where a forensic report and this map appear to conflict, the forensic evidence (direct code reading) wins and this map must be corrected.
- It is **distinguishable from** forensic/audit documents (`PRODUCTION_INTELLIGENCE_READINESS_REVIEW.md`, `FIELD_POPULATION_AUDIT.md`, `EVENT_IDENTITY_OWNERSHIP_AUDIT.md`, etc.) which are point-in-time evidence, not implementation prescriptions. Those documents remain valid evidence sources cited herein.
- **Source of truth for decisions:** only the two FIXED DECISIONS in §5 and the FORENSICALLY CONFIRMED facts in Phase 0 may constrain implementation. UNDECIDED rows require a separate decision before implementation.
- **Current reality vs. target gap:** the default assumption must be **RETAIN** existing implementation. "Does not exist" and "exists but does not yet satisfy the target architecture" are distinct; this map distinguishes them explicitly.

---

## 3. Executive Summary

The bot is live on MetaTrader 5 (Pepperstone), M5/Scalp, producing 24 persisted datasets with a 3-identifier spine (`correlation_id` + `entity_id` + `decision_id`) — currently `observation_id`-rooted for V10 reasoning. The EXECUTE path is fully reconstructable (10/10 transitions). The gaps that matter for the Data rebuild are: (a) **identity is fragmented** across `entity_id`/`observation_id`/`decision_id`/`correlation_id`/`shadow_trade_id`/`trade_id` with no single canonical `opportunity_id` propagated across datasets (the `opportunity_id` exists, but only on the Opportunity object); (b) **Shadow is already wired** (via `BarProvider.fetch_bar`) but is **poll-coupled and shares the live RiskManager**; (c) **commission/swap/fee are available from the broker but discarded** on the close/journal path; (d) **per-sibling (non-primary) REASONED enrichment is missing** (siblings are only stamped `REJECTED(pattern_not_selected)`); and (e) **passed_identification_condition is not yet implemented as a combined predicate**. (a)–(d) are already-implemented capabilities needing correction/extension; (e) and SHADOW INITIALISATION are genuinely future. A NEW standalone SEEN record is **not** required: SEEN facts are already captured in `Opportunity.DETECTED` and via the CANDLE event.

### What is ALREADY IMPLEMENTED in the live path (not to be re-implemented)
- Opportunity object + lifecycle/primary selection (`core/opportunity/opportunity.py:58-146`, `core/runtime/live_scanner.py:855-935`).
- per-pattern `opportunity_id` and separate Opportunity per detected pattern (`core/opportunity/factory.py:63`, `core/runtime/live_scanner.py:565-610`).
- Opportunity persistence (`core/opportunity/persistence.py:38,78`).
- CANDLE production, config-gated (`mt5_data.py:148-229`, `emit_candle:214-218`, `ENABLE_CANDLE_REPLAY_CACHE`).
- existing Shadow production wiring (`bar_provider.py:125-151`).
- the four Shadow-related systems (§4).
- the replay legacy path (`replay_scanner.py:54-220`, `replay_runtime.py:36-155`).
- the existing startup recovery mechanism (`startup_recovery.py:30-232`).
- the four hidden persistence sinks (§11).

### Risk behaviour assessment

- `core/pipeline/shadow_rooms.py:_shadow_room_3` calls the **LIVE** `RiskManager.evaluate_signal()` on every pattern-bearing cycle.
- That call routes through `risk/manager.py:_execute_risk` (L322–484), which mutates **module-global** `_rejection_counts` (manager.py L148, 161–163) and the **`risk_metrics` singleton** (risk/metrics.py L34–45; singleton at L118–119).
- The live path can observe the former via `_log_rejection` → `get_rejection_metrics()` (`risk/levels.py` read of manager.py L151–153) for **log-throttling**; the latter has no production reader today (risk/metrics.py `snapshot/log_summary` not imported outside module/tests).
- **Classification: SHARED STATE MUTATION — DIAGNOSTIC IMPACT ONLY.** No live decision/order/sizing/guard/execution/position path reads the mutated state. The mutation does **not** alter trading behaviour. It does, however, violate the future isolation requirement and must be remediated in the target Data/Shadow layer.

---

## 4. Current-state architecture

### What is LIVE (trading path)
```
MT5 Data Feed (core/data/mt5_data.py)
  → BarProvider.fetch_bar (core/runtime/bar_provider.py)
      ├─ tick freshness, bar validation, dedup, stale monitor
      ├─ get_shadow_engine().evaluate_bar(...)        [ALREADY IMPLEMENTED wiring]
      └─ research_shadow_engine.evaluate_research_bar(...)  [ALREADY IMPLEMENTED wiring]
  → live_scanner.py (core/runtime/live_scanner.py)
  → run_v10_cycle() [core/v10/scanner_adapter.py] → V10Pipeline.process() [core/v10/pipeline.py]
  → runtime_guard_chain [risk/runtime_guard_chain.py]
  → execution_orchestrator → MT5Execution
  → trade_state_manager [core/trade_management/manager.py]
```
ENGINE_MODE is V10 (legacy new_engine disabled per live_scanner.py L560–564). Live path observed directly: `live_scanner.py:499–627` (engine mode dispatch), `live_scanner.py:1389–1493` (guard chain), `live_scanner.py:1513+` (execution).

### What is OBSERVATIONAL (never influences trading)
- `core/pipeline/forensic_logger.py` (Discord pair channels only, no persistence) — observer #2.
- `core/pipeline/entity_tracker.py` → writes `logs/entity_events.jsonl` directly (legacy file L206–215) + `emit_entity` (allowlist-rejected).
- `core/pipeline/visibility_layer.py` → writes `logs/visibility_trace.jsonl` directly (`_persist` L167–174).
- `core/pipeline/shadow_rooms.py` → writes `logs/shadow_rooms.jsonl` (`_persist`).
- `core/strategies/strategy_intelligence_observer.py` → `observation_persistence.persist_strategy_observation` (JSONL + S3).
- All wrapped in `try/except: pass`; none return values consumed by Trading.

### What is LEGACY (not active trading path)
- `core/engine.py` `process_bar` + `new_engine.run_new_engine` — guarded out under V10 (`live_scanner.py:560–564`, `live_scanner.py:519–530`), still used by replay only.
- `core/runtime/replay_scanner.py:run_replay_scanner` (L54–220) and `core/runtime/replay_runtime.py:run_replay` (L36–155).

### Shadow families currently in runtime · Status: ALREADY IMPLEMENTED
1. **ShadowTradeEngine** — `core/shadow_trades.py` (main shadow; `open_trade`, `evaluate_bar`).
2. **Research shadow engine** — `core/research_assessment/research_shadow_engine.py` (`evaluate_research_bar`).
3. **shadow_rooms** — `core/pipeline/shadow_rooms.py` (legacy 5-room full recompute; observer #5).
4. **Candidate shadow hook** — `research_engine/lifecycle/candidate_shadow_hook.py` (opens `CANDIDATE_{id}` shadows from `engine_execution_handler.py` L232–255).

---

## 5. Fixed Architecture Decisions

> **FIXED DECISION** — must not be reconsidered.

### 5.1 Multi-pattern sibling policy · Status: ALREADY IMPLEMENTED (live path)
**SEPARATE OPPORTUNITY PER PATTERN.** Multiple OPP- records may share the same `seen_key`, but each opportunity represents exactly one defining pattern/candidate lineage.

- Production call site: `core/runtime/live_scanner.py:565–610` — per-pattern loop `for _sig in _raw_patterns:` → `create_opportunity(signal=_sig, …)` (L592–604), batch-persisted at L607.
- Each Opportunity gets a distinct legacy id `f"{symbol}_{bar_time}_{pattern}"` (`core/opportunity/factory.py:63`); `sibling_patterns` captured as a defining fact (factory L95–100) but does **not** collapse siblings.
- Later "primary" selection (`live_scanner.py:865`) does **not** delete siblings — non-primary siblings are transitioned `REJECTED(pattern_not_selected)` (L921–927) and re-persisted as separate records (L931).
- This decision overrides the prior "one opportunity per bar" temptation and the legacy engine's single-assessment-per-bar model.

### 5.2 passed_identification_condition · Status: GENUINELY FUTURE IMPLEMENTATION
(Data/Shadow derivation — **must not alter** live filtering/selection/classification/decision/risk/execution/position management):
```
passed_identification_condition =
    (identification_verdict == VALID)
    AND (at least one horizon is eligible)
```
- `identification_verdict`: `core/v10/opportunity_engine.py:assess_opportunity` (L61–66), surfaced in `_new_result["v10_pipeline_result"].opportunity.opportunity_state` (`live_scanner.py:519–530`), available before the per-pattern loop (L592).
- "at least one horizon eligible": derivable from `HorizonClassificationResult.eligible_horizons` (`core/horizon/horizon_classifier.py:36–100`, consumed at `live_scanner.py:700`, `_eligible_for_shadow` at L727).
- **No existing combined predicate** — closest fragments: verdict check `_v10_opp_state in ("WATCHING","VALID")` (L877); non-empty shadow gate `if _eligible_for_shadow and _new_result.get("pattern")` (L729). Neither combines both terms → additive derivation.

---

## 6. Canonical Data / Lineage Model

The target Data layer must record the complete story of every opportunity across the Live path and every Shadow simulation derived from it.

```
SEEN ─► IDENTIFIED ─► REASONED ─► DECIDED ─► RISK ─► EXECUTED ─► POSITION ─► OUTCOME  (Live)
            │
            └──► SHADOW INITIALISED ─► SHADOW REASONED ─► SHADOW DECIDED ─►
                SHADOW RISK ─► SHADOW EXECUTED ─► SHADOW POSITION ─► SHADOW OUTCOME  (×N simulations)
```

| Stage | Canonical Data record (target) | Canonical identity | Current status |
|---|---|---|---|
| SEEN | Market observation (raw candle/tick + feed health + market context) | `seen_key` = `{symbol}_{bar_time_utc_ms}` | PARTIALLY IMPLEMENTED — SEEN facts already captured in `Opportunity.DETECTED` (`opportunity.py:67-128`: bar_time L69/76, pattern L74, direction L73, trigger_candle L80, bid/ask L85-86, session L88, htf L94-104) and raw market data via the CANDLE event (`mt5_data.py:148-229`, `emit_candle:214-218`); whether SEEN must become a separately-queryable standalone record is UNDECIDED (no new schema invented) |
| IDENTIFIED | Opportunity (pattern, direction, setup, market context, initial geometry) | `opportunity_id` = `{symbol}_{bar_time}_{pattern}` | ALREADY IMPLEMENTED — `Opportunity` dataclass `core/opportunity/opportunity.py:58-146`; per-pattern id `core/opportunity/factory.py:63`; persisted `core/opportunity/persistence.py:38,78`; wired live `core/runtime/live_scanner.py:565-610, 607, 855-935` |
| REASONED | Assessment (scores, components, strategy classification, confidence, evidence, rejections, horizon eligibility) | `observation_id` (V10) | PARTIALLY IMPLEMENTED — assessment attached only to the PRIMARY opportunity (`live_scanner.py:905-910`); per-sibling REASONED enrichment is GENUINELY FUTURE (non-primary siblings only `REJECTED(pattern_not_selected)` L921-927); V10 `OpportunityAssessment` exists (`core/v10/opportunity_engine.py`) |
| DECIDED | Decision (EXECUTE/NO_TRADE/REJECT, reason, confidence, terminal=true) | `observation_id`/`decision_id` | FORENSICALLY CONFIRMED (engine_result → decision_audit/trace/ledger) |
| RISK | Risk decision (guards, exposure, sizing, RiskDecision/OrderIntent) | `decision_id` | FORENSICALLY CONFIRMED (`risk/manager.py`, `runtime_guard_chain.py`) |
| EXECUTED | Execution attempt (broker request, broker response, fill, rejection) | `correlation_id` | FORENSICALLY CONFIRMED (`execution_result_writer.py`, `execution_results/`) |
| POSITION | Position lifecycle (open, protection, modifications, close) | `position_id`/`trade_id` | FORENSICALLY CONFIRMED (`trade_management/manager.py`, `trade_journal`) |
| OUTCOME | Economic result (realised P&L, R-multiple, exit reason, costs, final truth) | `correlation_id`/`trade_id` | FORENSICALLY CONFIRMED (`trade_truth.py`, `trade_journal`) |
| SHADOW | Simulation of the SAME opportunity across N horizons | `shadow_trade_id` sharing the opportunity's canonical id | FORENSICALLY CONFIRMED wiring exists. Horizon-count rule **SUPERSEDED by Phase 1H owner policy freeze** — N ∈ {0..3} eligible∩constructible; SELECTED iff matches V10 selection, else all ALTERNATIVE (`live_scanner.py:729/781/795`); pinned by `tests/test_contract5_policy_freeze.py` (see §9). |

**Lineage rule (target):** every record belonging to an opportunity carries the canonical `opportunity_id`; external IDs (broker order/deal, `shadow_trade_id`) may exist as references but never replace it. Current implementation: `opportunity_id` ALREADY EXISTS and is canonical **within the `Opportunity` object** (`core/opportunity/factory.py:63`, `core/opportunity/opportunity.py:68`) — Opportunity creation, per-pattern id, and persistence are ALREADY IMPLEMENTED (wired live, observation-only via `live_scanner.py:565-610`). However `opportunity_id` is **not yet propagated** as the canonical spine across the other datasets (identity fragmented — see §12). STATUS: PARTIALLY IMPLEMENTED.

---

## 7. Phase 0 — Forensic Truth

> Mapping of the closed Phase-0 unknowns to implementation-map statuses. These are point-in-time evidence and remain valid; they record *presence*, not *absence*.

| # | Unknown | Status | Evidence (files/lines) |
|---|---|---|---|
| 1 | Replay path shares live Opportunity/V10 path? | FORENSICALLY CONFIRMED (NO) | `replay_scanner.py:54–220`, `replay_runtime.py:36–155`, `core.engine.process_bar`; only `live_scanner.py:592` mints Opportunities. |
| 2 | Commission/swap/fee capability? | FORENSICALLY CONFIRMED (capability exists; propagation missing) | `mt5_reconciliation.py:105–170` (reads `deal.commission/swap/fee`); `_aggregate_deals:266–297`; live close `_query_broker_close_history:554–624` omits; `trade_journal` defaults 0.0. |
| 3 | Observer identity usage / hidden sinks? | FORENSICALLY CONFIRMED (4 hidden sinks present) | `entity_tracker.py:206–215`, `visibility_layer.py:36–38/_persist:167–174`, `shadow_rooms.py:_persist`, `strategy_intelligence_observer.py`+`observation_persistence`. |
| 4 | `emit_trade_events` sink + signature mismatch? | FORENSICALLY CONFIRMED (Discord-only; TypeError on success path) | `event_bus.py:195–213` (requires symbol/event_state/decision); `post_execution_handler.py:149–157` (success §5 omits); `TradeLifecycleLogger:369–488` journal-close listener. |
| 5 | Startup recovery identity restoration? | FORENSICALLY CONFIRMED (partial) | `startup_recovery.py:30–232`, `_restore_identity_from_logs:237–285`; restores correlation_id/decision_id/cycle_id/pattern/decision_ts_utc; NOT opportunity_id. |
| 6 | Production CANDLE emission? | FORENSICALLY CONFIRMED (exists, config-gated) | `data/mt5_data.py:_persist_candles_to_cache:148–229` (`emit_candle` L214–218); gated by `ENABLE_CANDLE_REPLAY_CACHE` (L165, default False). |

Remaining forensics closed during Phase-0 reconciliation (not in original UNKNOWN list but resolved):
- Shadow wiring exists via `bar_provider.py:125–151` → CLOSED, was previously mischaracterised as unwired.
- shadow_rooms calls live `RiskManager.evaluate_signal()` → SHARED STATE MUTATION, diagnostic only.

---

## 8. B5 — Position / Startup Recovery

- **Current:** `core/runtime/startup_recovery.py:recover_positions_on_startup` (L30–232) discovers broker positions filtered by `config.BOT_MAGIC` (`mt5.positions_get`, L54–67), dedupes tracked tickets (L74–77), reconstructs `Position` objects (`pos_{ticket}`, deal_id=0, order_id=0 — L120–139), and registers directly into `TradeStateManager._by_id` (L142). Wired from `scanner_init.py:159–169`.
- **Identity restoration:** `_restore_identity_from_logs` (L237–285) scans `logs/execution_results/{SYM}/*.jsonl` newest-first, matching `order_ticket == ticket AND result_ok == True` (L271). Restores `correlation_id`, pattern, `decision_id`, `cycle_id`, `decision_ts_utc`. Does **not** restore `opportunity_id` nor `strategy` (strategy not stored). `observation_id` is present in those records but **not** restored (helper dict omits it; `TradeIdentity` built without `observation_id`, L107–114 → defaults "").
- **Fallbacks:** unrecoverable identity → `trade_identity=None` → journal `correlation_id=""` → `RECOVERED-{trade_id}` (`core/trade_journal.py:442–444`); protection verification uses `RECOVERY-{ticket}` synthetic correlation (`startup_recovery.py:188`).
- **Classification:** startup recovery **mechanism = ALREADY IMPLEMENTED**; `opportunity_id` propagation through recovery is **GENUINELY FUTURE IMPLEMENTATION** (extension into `_restore_identity_from_logs` or a durable ticket→opportunity index; not yet implemented).
- **Known gap:** positions_get does not expose deal_id/order_id; order_id set to 0. **KNOWN DEFECT — DEFERRED** (no remediation authorised).

---

## 9. B7 — Shadow / Data Simulation

### Framing (FIXED)
The future task is **NOT** "wire Shadow." Shadow evaluation is **already wired** into production.

- `core/runtime/bar_provider.py:fetch_bar` L125–151 (steps 5 and 5b) calls `get_shadow_engine().evaluate_bar(...)` (L126–137) and `core/research_assessment/research_shadow_engine.py:evaluate_research_bar(...)` (L139–151), both fire-and-forget, on **every** `fetch_bar` invocation. STATUS: ALREADY IMPLEMENTED wiring.

### Current wiring reality
- `ShadowTradeEngine` opens via `engine_execution_handler.py:232–255` (candidate hooks + V10_PRIMARY shadow) after EXECUTE; closes on broker fills; evaluates via `bar_provider.py`. `core/shadow_trades.py:evaluate_bar` increments `bars_elapsed` per call (L334).
- `shadow_rooms` runs as observer #5 (`ObserverRegistry.notify_all`, `live_scanner.py:967–984`) AFTER engine result, BEFORE guard chain (`L1389–1493`) and execution (`L1513+`), every cycle with a detected pattern, using the **LIVE** `sym_state.risk` (`ObserverContext.risk_manager`, L979). → **SHARED STATE MUTATION** (§3 risk assessment).

### Required corrective actions (re-home / isolate / correct — NOT re-wire from scratch)
- **IMPLEMENTED BUT NEEDS CORRECTION (timing fix DEFERRED):** current Shadow evaluation runs BEFORE the duplicate/new-bar gate (bar_provider L125–137 before dedup return None L196–223), and `evaluate_bar` increments `bars_elapsed` unconditionally per poll (shadow_trades.py L334) → poll-coupled timing inflates `bars_elapsed` (≈60 polls ≈ 30 min at 30 s polling vs intended ~5 h per M5 bar) and distorts `max_bars_timeout` + state-log cadence + R statistics. **KNOWN DEFECT — DEFERRED** (do NOT fix now; do not alter live timing). Future Shadow/Data evaluation should use the authoritative closed-bar boundary (`bar_provider.py` `is_new_bar`, L187/L254) instead of poll cadence.
- **IMPLEMENTED BUT NEEDS CORRECTION / RE-HOME:** `shadow_rooms` calls the **LIVE** `RiskManager.evaluate_signal()` (SHARED STATE MUTATION, §3). Future Data/Shadow simulation MUST NOT call the live `RiskManager` instance or any Trading-owned shared singleton (`_rejection_counts`, `risk_metrics`); must use isolated/private state or pure captured-input computation. The live `RiskManager` must remain untouched.
- RE-HOME existing `evaluate_bar` evaluation onto the authoritative closed-bar boundary.
- ~~SHADOW INITIALISATION horizon-count rule (Contract 5: 2 alt horizons if passed_identification_condition, else 3)~~ **SUPERSEDED — Phase 1H OWNER POLICY FREEZE.** The literal 2-vs-3 wording is ambiguous legacy text and is NOT implemented. Frozen policy: for every opportunity Shadow simulates each horizon in the eligible∩constructible set (N ∈ {0..3}; no artificial minimum or maximum); if V10 produced a horizon selection that sibling is recorded SELECTED and every other eligible/constructible sibling ALTERNATIVE; if no selection exists `v10_selected_horizon=""` and ALL siblings are ALTERNATIVE. The runtime already satisfies this exactly (`live_scanner.py:729` gate, `:781` per-horizon loop, `:795` status ternary); regression-pinned by `tests/test_contract5_policy_freeze.py`.
- All four Shadow families (§4) must be accounted for in the eventual consolidation/retirement inventory — **UNDECIDED** which are retained, re-homed, frozen, or retired.

---

## 10. B8 — Execution / Economic Truth

### Broker execution facts (FORENSICALLY CONFIRMED)
- Execution via `execution/execution_orchestrator.py` → `execution/mt5_execution.py` (`order_send`); broker response captured in `execution_results/` (L10–11, `RESEARCH_ENGINE_PHASE0_REPOSITORY_AUDIT.md:29`).
- Deal records expose `commission`, `swap`, `fee` — read by `core/mt5_reconciliation.py:extract_mt5_deals` (L160–162) and aggregated by `_aggregate_deals` (L266–297).
- Actual fill price available (`fill_price` L36 `FIELD_POPULATION_AUDIT.md`); slippage **NOT** directly exposed by MT5 (trade_truth placeholders 0.0 — `PRODUCTION_INTELLIGENCE_READINESS_REVIEW.md:71,205`; `FIELD_POPULATION_AUDIT.md:39`).

### Current capture gap (PARTIALLY IMPLEMENTED)
- Broker commission/swap/fee capability ALREADY EXISTS (`mt5_reconciliation.py:105-170`, `_aggregate_deals:266-297`) but the live close path `core/trade_management/manager.py:_query_broker_close_history` (L554–624) reads deal fields **excluding** commission/swap/fee, and `TradeLifecycleLogger._persist_trade_close` (`event_bus.py:416–443`) via `core/trade_journal.py:build_trade_record(commission=0.0, swap=0.0)` → **costs always 0.0 today**. This is a **capture + propagation** problem (existing broker fields → close/record path), **not** a missing broker capability. Propagation = GENUINELY FUTURE IMPLEMENTATION; optional empirical broker-population probe = OPTIONAL/DEFERRED.

### Economic truth (FORENSICALLY CONFIRMED)
- `trade_truth` (realised P&L, R-multiple, exit reason, net profit) and `trade_journal` (closed-trade record) are the ground-truth sources (§11 datasets 11, 19; `PRODUCTION_INTELLIGENCE_READINESS_REVIEW.md:34,93`). `TradeLifecycleLogger` is the production journal-close persistence listener (wired at `scanner_init.py:126–130`).

---

## 11. Repository / Persistence Consolidation

Authoritative dataset inventory (24 known) plus the 4 hidden sinks from Phase 0. Statuses distinguish **already-implemented** datasets (incl. the 4 hidden sinks) from the **future** consolidation/retirement work.

| # | Dataset | Producer | Local path | S3 | Schema | Status |
|---|---|---|---|---|---|---|
| 1 | events | `core/event_stream.py` | `events/{D}.jsonl` | `events/symbol={S}/date={D}/` | Envelope | FORENSICALLY CONFIRMED |
| 2 | decision_audit | `core/decision_audit.py` | `logs/decision_audit/{S}_{D}.jsonl` | `decision_audit/symbol={S}/date={D}/` | decision_audit_v1 | FORENSICALLY CONFIRMED |
| 3 | decision_ledger | `core/decision_ledger.py` | `logs/decision_ledger/{S}/{D}.jsonl` | `decision_ledger/symbol={S}/date={D}/` | decision_ledger_v1 | FORENSICALLY CONFIRMED |
| 4 | decision_trace | `core/decision_trace.py` | `logs/decision_trace/{S}/{D}.jsonl` | `decision_trace/symbol={S}/date={D}/` | decision_trace_v1 | FORENSICALLY CONFIRMED |
| 5 | execution_context | `core/execution_context.py` | `logs/execution_context/{S}/{D}.jsonl` | `execution_context/symbol={S}/date={D}/` | execution_context_v1 | FORENSICALLY CONFIRMED |
| 6 | execution_results | `core/persistence/execution_result_writer.py` | `logs/execution_results/{S}/{D}.jsonl` | `execution_results/symbol={S}/date={D}/` | execution_results_v1 | FORENSICALLY CONFIRMED |
| 7 | opportunity_assessment | `core/persistence/opportunity_assessment_writer.py` | `logs/opportunity_assessment_log/{S}/{D}.jsonl` | `opportunity_assessment/symbol={S}/date={D}/` | opportunity_assessment_v1 | FORENSICALLY CONFIRMED |
| 8 | assessments | `core/assessment/persistence.py` | `logs/assessments/{S}/{D}.jsonl` | `assessments/symbol={S}/date={D}/` | assessment_v1 | FORENSICALLY CONFIRMED |
| 9 | shadow_trades | `core/shadow_trades.py` | `logs/shadow_trades/{S}/{D}.jsonl` | `shadow_trades/schema_version=shadow_trades_v2/symbol={S}/date={D}/` | shadow_trades_v2 | FORENSICALLY CONFIRMED |
| 10 | research_shadow_trades | `core/research_assessment/research_shadow_engine.py` | `logs/research_shadow_trades/{S}/{D}.jsonl` | `research_shadow_trades/schema_version=research_shadow_trades_v1/symbol={S}/date={D}/` | research_shadow_trades_v1 | FORENSICALLY CONFIRMED |
| 11 | trade_truth | `core/trade_truth.py` | `logs/trade_truth/{S}/{D}.jsonl` | `trades/schema_version=trade_truth_v3/symbol={S}/date={D}/` | trade_truth_v3 | FORENSICALLY CONFIRMED |
| 12 | trade_truth_graph | `core/trade_truth_graph.py` | `logs/trade_truth_graph/{S}/{D}.jsonl` | `trade_truth_graph/symbol={S}/date={D}/` | trade_truth_graph_v2 | FORENSICALLY CONFIRMED |
| 13 | learning | `core/learning/store.py` | `logs/learning/{D}.jsonl` | `learning/date={D}/` | learning_v1 | FORENSICALLY CONFIRMED |
| 14 | edge_attribution | `core/edge_attribution.py` | `logs/edge_attribution/{S}/{D}.jsonl` | `edge_attribution/schema_version=edge_attribution_v2/symbol={S}/date={D}/` | edge_attribution_v2 | FORENSICALLY CONFIRMED |
| 15 | edge_optimisation | `core/edge_optimisation.py` | `logs/edge_optimisation/{D}.jsonl` | `edge_optimisation/schema_version=edge_optimisation_v2/date={D}/` | edge_optimisation_v2 | FORENSICALLY CONFIRMED |
| 16 | strategy_compiler | `core/strategy_compiler.py` | `logs/strategy_compiler/{D}.jsonl` | `strategy_compiler/schema_version=strategy_compiler_v2/date={D}/` | strategy_compiler_v2 | FORENSICALLY CONFIRMED |
| 17 | market_context | `core/market_context/persistence.py` | `logs/market_context/{S}/{D}.jsonl` | `market_context/schema_version=market_context_v1/symbol={S}/date={D}/` | market_context_v1 | FORENSICALLY CONFIRMED |
| 18 | portfolio_rankings | `core/portfolio_ranking/persistence.py` | `logs/portfolio_rankings/{D}.jsonl` | `portfolio_rankings/date={D}/` | portfolio_ranking_v1 | FORENSICALLY CONFIRMED |
| 19 | trade_journal | `core/trade_journal.py` | `logs/trade_journal/{D}.jsonl` | `trade_journal/schema_version=trade_journal_v1/symbol={S}/date={D}/` | trade_journal_v1 | FORENSICALLY CONFIRMED |
| 20 | opportunities | `core/opportunity/persistence.py` | `logs/opportunities/{S}/{D}.jsonl` | `opportunities/schema_version=opportunities_v1/symbol={S}/date={D}/` | opportunities_v1 | FORENSICALLY CONFIRMED (exists; see also §6 IDENTIFIED ALREADY IMPLEMENTED) |
| 21 | protection_audit | `core/protection_verification.py` | `logs/protection_audit/{S}/{D}.jsonl` | `protection_audit/schema_version=protection_audit_v1/symbol={S}/date={D}/` | protection_audit_v1 | FORENSICALLY CONFIRMED |
| 22 | risk_deviation | `core/risk_deviation.py` | `logs/risk_deviation/{S}/{D}.jsonl` | `risk_deviation/schema_version=risk_deviation_v1/symbol={S}/date={D}/` | risk_deviation_v1 | FORENSICALLY CONFIRMED |
| 23 | quarantine | `core/contracts/quarantine.py` | `logs/quarantine/{LAYER}/{D}.jsonl` | `quarantine/schema_version=quarantine_v1/layer={L}/date={D}/` | quarantine_v1 | FORENSICALLY CONFIRMED |
| 24 | portfolio_shadow | `core/portfolio_ranking/shadow_comparison.py` | `logs/portfolio_shadow/{D}.jsonl` | `portfolio_shadow/schema_version=portfolio_shadow_v1/date={D}/` | portfolio_shadow_v1 | FORENSICALLY CONFIRMED |
| — | entity_events *(HIDDEN)* | `core/pipeline/entity_tracker.py` | `logs/entity_events.jsonl` | None | JSONL | IMPLEMENTED BUT NEEDS CORRECTION (retire/consolidate) |
| — | visibility_trace *(HIDDEN)* | `core/pipeline/visibility_layer.py` | `logs/visibility_trace.jsonl` | None | JSONL | IMPLEMENTED BUT NEEDS CORRECTION (retire/consolidate) |
| — | shadow_rooms *(HIDDEN)* | `core/pipeline/shadow_rooms.py` | `logs/shadow_rooms.jsonl` | None | JSONL | IMPLEMENTED BUT NEEDS CORRECTION (retire/consolidate) |
| — | strategy_observations *(HIDDEN)* | `core/strategies/observation_persistence.py` (+ observer `core/strategies/strategy_intelligence_observer.py`) | Local JSONL + S3 | `core/strategies/observation_persistence.py` | JSONL | IMPLEMENTED BUT NEEDS CORRECTION (retire/consolidate) |

Persistence contract (FORENSICALLY CONFIRMED, `PERSISTENCE_ARCHITECTURE_AUDIT_FINAL.md`): local JSONL is truth, S3 mirrors, append-only, one writer per dataset (`test_s3_architecture_guard.py` allowlist), `EVENT_STREAM_S3_MIRROR` gate, `schema_version` per dataset. New Data-layer datasets must obey the same contract once implemented.

---

## 12. Identifier Containment / Retirement Inventory

| Family | Origin / format | Canonical lineage? | Status |
|---|---|---|---|
| `seen_key` (target `{symbol}_{bar_time_utc_ms}`) | Forensic: implied by SEEN stage; bar_time present on Opportunity (`opportunity.py:69,76`) | Target canonical root | PARTIALLY IMPLEMENTED / UNDECIDED (facts exist; standalone seen_key record UNDECIDED) |
| `observation_id` (sha256(symbol+timestamp)[:16]) | V10 `OpportunityEngine` (`V10_ID_LINEAGE:26-34`) | V10 root, alias of entity_id | FORENSICALLY CONFIRMED (V10) |
| legacy `opportunity_id` (`{symbol}_{bar_time}_{pattern}`) | `core/opportunity/factory.py:63` | Per-FIXED-DECISION sibling id | ALREADY IMPLEMENTED (exists, one per pattern) |
| `entity_id` | alias of `observation_id` (`V10_ID_LINEAGE:108`) | alias | FORENSICALLY CONFIRMED |
| `risk_id` | alias of `observation_id` on OrderIntent (`V10_ID_LINEAGE:109`) | alias | FORENSICALLY CONFIRMED |
| `decision_id` (uuid4) | `prepare_execution` (`V10_ID_LINEAGE:49-51`) | Child, execution-attempt | FORENSICALLY CONFIRMED |
| `correlation_id` (`{cycle_id}_{symbol}_{timestamp}`) | `generate_correlation_id` (`V10_ID_LINEAGE:53-55`) | Child, event-stream spine; empty on NO_TRADE/recovered | FORENSICALLY CONFIRMED |
| `order_id` / `deal_id` | MT5 broker (`V10_ID_LINEAGE:68-69`) | External ref | FORENSICALLY CONFIRMED (external) |
| `position_id` | MT5 broker (`V10_ID_LINEAGE:83`) | Child, position lifecycle | FORENSICALLY CONFIRMED |
| `shadow_trade_id` (`shadow_{cycle}_{symbol}`) | ShadowTradeEngine (`V10_ID_LINEAGE:84`, `core/shadow_trades.py`) | Shadow tracking | FORENSICALLY CONFIRMED |
| candidate `trade_id` (`candidate_{candidate_id}_{cycle}_{symbol}`) | `candidate_shadow_hook.py` | Shadow/candidate | FORENSICALLY CONFIRMED |
| self-minted `entity_id` (`{symbol}_{int(bar_time)}`) | `entity_tracker.py:60`, `visibility_layer.py:66` | Observer-generated | IMPLEMENTED BUT NEEDS CORRECTION (retire/converge to opportunity_id) |
| runtime observation_id (`{symbol}_{cycle}_{bar_time}`) | `strategy_intelligence_observer` | Observer-generated | IMPLEMENTED BUT NEEDS CORRECTION (retire/converge to opportunity_id) |
| recovery synthetic `correlation_id` (`RECOVERY-{ticket}`, journal `RECOVERED-{trade_id}`) | `startup_recovery.py:188`, `core/trade_journal.py:442-444` | Recovery fallback | FORENSICALLY CONFIRMED |

**Canonical convergence target:** every record belonging to an opportunity must carry the single canonical `opportunity_id` (Contract 1). Current state: `opportunity_id` ALREADY EXISTS and is canonical **within the `Opportunity` object** (`core/opportunity/factory.py:63`, `core/opportunity/opportunity.py:68`) but is **not yet propagated** to the other datasets (identity fragmented — see §6 lineage rule). Propagation into non-Opportunity datasets and retirement of observer/self-minted ids: **GENUINELY FUTURE IMPLEMENTATION** (extending already-existing `opportunity_id`, not creating it).

---

## 13. Event / Observability Architecture

- **Event stream** (`core/event_stream.py`): 7 allowlisted event types (CANDLE, FEATURE_UPDATE, FEED_HEALTH, SYSTEM_HEALTH, BIAS, RISK_CHECK, DECISION_STAGE) → `events/{D}.jsonl` + S3 mirror (`v10_persistence_migration_plan.md`, `RESEARCH_ENGINE_PHASE0_REPOSITORY_AUDIT.md:16,20-22`). All other emitters are dropped.
- **event_bus** (`core/event_bus.py`): FROZEN compatibility bridge re-exporting event_stream (`event_bus.py:42-63`).
- **TradeLifecycleLogger** (`core/event_bus.py:369-488`): production `TradeLifecycleListener`, wired `scanner_init.py:126-130`; `ON_TRADE_CLOSE` → `_persist_trade_close:416-443` (journal write path); `realised_pnl_override` from `broker_profit`.
- **Discord-only bridges**: `core/pipeline/forensic_logger.py`, `core/pipeline/entity_tracker.py:_emit_discord`, `core/pipeline/visibility_layer.py:_emit_gap_alert`, `core/pipeline/shadow_rooms.py:_emit_divergence_alert`, `core/event_bus.emit_trade_events` (Discord "trade-execution" channel only, no persistence).
- **KNOWN DEFECT — DEFERRED:** `post_execution_handler.emit_post_trade_success` §5 (`post_execution_handler.py:149-157`) calls `event_bus.emit_trade_events` (`event_bus.py:195-213`, requires `symbol`/`event_state`/`decision`) with only `candle_i/candle_time/...` kwargs → **TypeError after successful fill**; swallowed at `live_scanner.py:1791-1808`. Execution already completed before failure. **Do NOT fix** without separate authorisation (do not touch `event_bus.py` or `post_execution_handler.py`).

---

## 14. Replay Architecture

- **ALREADY IMPLEMENTED (legacy path):** replay is a **separate legacy path**. `core/runtime/replay_scanner.py:run_replay_scanner` (L54-220) and `core/runtime/replay_runtime.py:run_replay` (L36-155) both invoke `core.engine.process_bar` (legacy) — **no Opportunity V10 path** and **no Opportunity construction** in production replay (only `live_scanner.py:592` mints Opportunities).
- Replay persistence: `persist_decision_audit` + `event_bus` emitters only (replay_scanner `L188`, `L159-185`; replay_runtime `L132/L139`, `L103-129`).
- **Implication (FUTURE IMPLEMENTATION, not done now):** replay is NOT a golden-master for IDENTIFIED Opportunity behaviour; no parity integration exists. A future replay-integration decision (explicit design call) would be required to make replay produce Opportunity stories.

---

## 15. Phase 1+ Implementation Roadmap

Ordered gates, each framed as **corrective/extension on existing capabilities** (the default assumption is RETAIN — do not re-create what already exists). **Do NOT implement any of this now.**

1. **Data ownership boundary** — establish the Data layer as the sole writer/record-owner for the target SEEN→OUTCOME + SHADOW story records; isolate from Trading-owned singletons (B7). *Builds on already-implemented Opportunity/persistence; does not re-create them.*
2. **Canonical identity** — propagate the ALREADY-EXISTING `opportunity_id` (`opportunity.py:68`, `factory.py:63`) as the canonical spine into all record types; retire observer/self-minted duplicates (§12). *Extension of an existing id, not a new id.*
3. **Opportunity lifecycle persistence** — terminal-state records for every stage, including early exits (kill-switch/session-block/pre-engine) so no opportunity vanishes (Contract 3). *Per-sibling REASONED enrichment (§6 REASONED) is part of this.*
4. **Shadow re-homing** — re-home `evaluate_bar` to the authoritative closed-bar boundary; sever `shadow_rooms`→ live `RiskManager` call; instantiate isolated/private simulation instances; implement SHADOW INITIALISATION horizon-count rule (Contract 5); account for all four Shadow families (§9).
5. **Economic truth capture** — close commission/swap/fee propagation into TradeRecord/journal (B8). *Capability exists; propagation only.*
6. **Replay parity** (optional, separate decision) — only if an explicit replay-integration decision is made.
7. **Observer/sink consolidation** — merge the 4 hidden sinks (§11, already-implemented) into the authoritative dataset set; retire redundant writers.
8. **Defect remediation** (only with separate authorisation) — emit_trade_events signature mismatch; startup recovery deal_id/order_id=0.

---

## 16. Open Items / Explicitly Deferred Items

### Architecture decisions already made (FIXED DECISION)
- Multi-pattern sibling policy (§5.1, FIXED DECISION) — Status: ALREADY IMPLEMENTED (live path).
- passed_identification_condition (§5.2, FIXED DECISION) — Status: GENUINELY FUTURE IMPLEMENTATION.

### Forensic questions already closed (evidence retained)
- Replay path (§7 #1); commission/swap/fee capability (§7 #2); observer identity + 4 hidden sinks (§7 #3); emit_trade_events sink+defect (§7 #4); startup recovery mechanics (§7 #5); CANDLE production (§7 #6); Shadow wiring (§4, §9); shadow_rooms live-RiskManager (§3, §9).

### Already implemented in the live path (do NOT re-implement)
- Opportunity creation, per-pattern sibling creation, opportunity persistence (§6 IDENTIFIED, §5.1).
- Opportunity lifecycle / primary selection (§1).
- CANDLE production (§7 #6, §6 SEEN).
- existing Shadow wiring (§9).
- the four Shadow-related systems (§4).
- replay legacy path (§14).
- existing startup recovery mechanism (§8).
- existing hidden persistence sinks (§11).

### Partially implemented (exists, but target not yet satisfied)
- Canonical `opportunity_id` propagation across the wider data graph — exists on Opportunity only (§6 lineage rule, §12).
- per-sibling (non-primary) REASONED/assessment enrichment (§6 REASONED).
- startup recovery identity propagation — mechanism exists; `opportunity_id`/strategy not restored (§8).
- SEEN representation — facts in `Opportunity.DETECTED` + CANDLE; standalone SEEN record UNDECIDED (§6 SEEN).
- commission/swap/fee capture+propagation — capability exists; closed-trade path discards them (§10).

### Implemented but needs correction / re-homing
- Poll-coupled Shadow timing → CORRECT to authoritative closed-bar boundary (fix deferred) (§9).
- `shadow_rooms` calling the live `RiskManager` → RE-HOME to isolated/private simulation state (§9 B7).
- Hidden persistence sink consolidation/retirement (§11).
- observer/self-minted identifier convergence onto `opportunity_id` (§12).

### Genuinely future implementation (not yet present)
- `passed_identification_condition` combined predicate (~§5.2).
- SHADOW INITIALISATION horizon-count rule (Contract 5) — RESOLVED by Phase 1H owner policy freeze; runtime already compliant (see §9).
- INTRADAY/EXTENDED Shadow completeness (§9).
- Canonical `opportunity_id` propagation to datasets that currently lack it (§6, §12).
- per-sibling REASONED/assessment enrichment (§6 REASONED).
- pre-engine-exit Opportunity capture where genuinely missing (§3 gap (a), Contract 3).

### Known defects explicitly deferred (KNOWN DEFECT — DEFERRED)
- `emit_trade_events` success-path TypeError (§13) — no fix without separate authorisation.
- Poll-coupled Shadow timing distorting `bars_elapsed`/`max_bars_timeout`/R-stats (§9) — no fix now.
- Startup recovery `deal_id`/`order_id` = 0 (§8) — no fix now.
- MT5 does not expose real slippage → trade_truth slippage/spread placeholders 0.0 (§10) — external broker limitation.

### Optional empirical validations (non-blocking)
- Empirical broker commission/swap/fee population against recorded history (not provable from source).
- Runtime frequency of the emit_trade_events TypeError in error logs (static proof exists; runtime frequency unmeasured).

---

## 17. Testing / Validation Strategy

Future tests (FUTURE IMPLEMENTATION — not authored now):

- **multi-pattern opportunity separation** — assert N patterns ⇒ N distinct Opportunity records with distinct `opportunity_id` (`live_scanner.py:565-610`, `factory.py:63`); assert primary selection does not delete siblings (L921-927, L931).
- **passed_identification_condition** — assert predicate equals `(verdict==VALID) AND (≥1 eligible horizon)` (`opportunity_engine.py:61-66`, `horizon_classifier.py:36-100`); assert it does not alter live decision/risk/execution paths.
- **lineage propagation** — assert `opportunity_id` present on SEEN→OUTCOME + every shadow record; assert no record substitutes `observation_id`/`decision_id`/`correlation_id` for `opportunity_id`.
- **Shadow isolation** — assert shadow evaluation uses an isolated RiskManager instance (no mutation of process-global `_rejection_counts`/`risk_metrics`); assert shadow writes never reach the broker.
- **closed-bar timing** — assert `bars_elapsed` advances once per authoritative closed bar (`bar_provider.is_new_bar`), not per poll.
- **commission/swap/fee propagation** — assert close-path TradeRecord carries non-placeholder commission/swap/fee sourced from broker deal data (`mt5_reconciliation.py:160-162, 266-297`).
- **startup recovery** — assert recovery identity fields restored (`_restore_identity_from_logs:237-285`); assert `opportunity_id` restoration is tracked as a future gap.
- **replay separation** — assert replay path does not construct Opportunity records (golden-master guard: only `live_scanner.py:592` mints; replay `process_bar` does not).
- **persistence consolidation** — assert all 28 datasets (24 + 4 hidden) are owned by exactly one writer and joinable by canonical lineage keys.

---

## 18. Safety / Non-Goals

Creation/editing of this document — and any future implementation derived from it — does **not by itself**:

- change runtime behaviour
- change trading
- change persistence behaviour
- change schemas
- change IDs
- wire or re-wire Shadow
- fix defects (only records them as deferred)
- migrate data
- delete or rename existing architecture documents

Any future step that does touch the items above requires a separate, explicit implementation authorisation distinct from this architecture mapping. This Phase-1A edit only corrected **status labels** in the blueprint; no Trading/DATA/RESEARCH source code, schemas, IDs, persistence, or Shadow wiring were touched.

---

*Document reconciled to current repository state (Phase 1A). Status labels per §0 legend. All citations reference the repository at git HEAD `81227dbd`.*
