# S3 Persistence Architecture Audit

## Two Buckets

| Bucket | Purpose | Engine |
|---|---|---|
| `trading-bot-data-mk1` | Original bucket — all legacy + shared datasets | Legacy + V10 (shared) |
| `v10-engine` | V10-dedicated bucket — pipeline research data only | V10 only |

---

## Complete Dataset Table

| # | Dataset | S3 Prefix | Purpose | Written By | Read By | Lifecycle Stage | Reconstructs | Classification | Keep/Merge/Remove |
|---|---|---|---|---|---|---|---|---|---|
| 1 | **- 7. Decision Ledger** | `decision_ledger/symbol={S}/date={D}/` | One record per symbol per cycle — complete decision log | `DecisionLedgerWriter.record()` (live_scanner), `persistence_adapter._write_to_ledger()` (V10, broken) | `load_ledger()`, research analysis | Decision | Every decision ever made (EXECUTE + NO_TRADE + all blocks) | **Primary source of truth** | KEEP |
| 2 | **- 8. Decision Audit** | `decision_audit/symbol={S}/date={D}/` | Structured diagnostic of legacy engine evaluation | `persist_decision_audit()`, `persist_new_engine_decision_audit()`, `persist_risk_rejection()` | External analysis | Decision | Score components, intent, timing, risk rejection context | Audit | KEEP |
| 3 | **- 6. Decision Trace** | `decision_trace/symbol={S}/date={D}/` | Full pipeline journey (stages reached, terminal stage, reasoning) | `persist_decision_trace()` | External analysis | Decision | Why the pipeline stopped at each stage | Audit | KEEP |
| 4 | **- 5. Assessments** | `assessments/symbol={S}/date={D}/` | Legacy Assessment object (score, regime, strategy, confirmation) | `persist_assessment()` | External analysis | Market Understanding | Full scoring breakdown of legacy engine | Derived (from engine) | KEEP (legacy reference) |
| 5 | **- 4. Opportunity Assessment** | `opportunity_assessment/symbol={S}/date={D}/` | V3 shadow OpportunityAssessment (horizon, entry, risk, direction) | `persist_opportunity_assessment()` | External analysis | Market Understanding | What the V3 shadow pipeline concluded | Derived (V3 shadow) | KEEP |
| 6 | **- 3. Opportunities** | `opportunities/schema_version=.../symbol={S}/date={D}/` | Opportunity lifecycle (detected → assessed → rejected/executed) | `persist_opportunity()`, `persist_opportunity_batch()` | None built-in | Market Understanding | How many opportunities appeared and their fates | Primary (opportunity lifecycle) | KEEP |
| 7 | **Market Context** | `market_context/schema_version=.../symbol={S}/date={D}/` | V3 MarketContext (HTF structure, regime, location, behaviour) | `MarketContextPersistence.persist()` | None built-in | Market Understanding | Market environment at time of evaluation | Primary | KEEP |
| 8 | **- 11. Execution Context** | `execution_context/symbol={S}/date={D}/` | Pre-execution environment snapshot (session, spread, risk state) | `persist_execution_context()` | `load_execution_contexts()` | Pre-Execution | Exact conditions under which decision was made | Primary | KEEP |
| 9 | **- 12. Execution Results** | `execution_results/symbol={S}/date={D}/` | Every broker execution attempt (ok/fail, retcode, fill, slippage) | `persist_execution_result()` | None built-in | Execution | What the broker actually did | Primary (execution) | KEEP |
| 10 | **- 13. Trade Truth** | `trades/schema_version=.../symbol={S}/date={D}/` | Pure execution reality (fills, R-multiple, PnL, MFE/MAE) | `persist_trade_truth()` | `load_trade_truth()`, edge_attribution | Trade Lifecycle | What actually happened (no strategy or decision context) | **Primary source of truth** (outcomes) | KEEP |
| 11 | **- 14. Trade Journal** | `trade_journal/schema_version=.../symbol={S}/date={D}/` | Complete trade record (entry, exit, P&L, commission, close reason) | `persist_trade()` via TradeLifecycleLogger | `get_trades_today()`, `get_recent_trades()`, risk_deviation | Trade Lifecycle | Full trade lifecycle for operational use | Primary (operational) | KEEP |
| 12 | **Trade Truth Graph** | `trade_truth_graph/symbol={S}/date={D}/` | Relationship graph (temporal, causal, correlation links between trades) | `persist_graph_node()` | `load_graph_local()`, edge_attribution | Learning | How trades relate to each other (no data, only links) | Derived (relationships) | KEEP |
| 13 | **- 9. Protection Audit** | `protection_audit/schema_version=.../symbol={S}/date={D}/` | Post-fill SL/TP broker verification | `_persist_result()` via `verify_protection()` | None built-in | Post-Execution | Whether broker actually has our stops | Audit (safety) | KEEP |
| 14 | **- 10. Risk Deviation** | `risk_deviation/schema_version=.../symbol={S}/date={D}/` | Planned vs actual risk (detects protection failures) | `persist_risk_deviation()` | None built-in | Post-Trade | Was the loss within expected bounds? | Audit (safety) | KEEP |
| 15 | **- 15. Shadow Trades** | `shadow_trades/schema_version=.../symbol={S}/date={D}/` | Simulated trade lifecycle (no broker, paper R-multiples) | `ShadowTradeEngine._persist_shadow_trade()` | None built-in | Shadow/Research | What would have happened if we executed | Primary (simulation) | KEEP |
| 16 | **- 16. Strategy Observations** | `strategy_observations/symbol={S}/date={D}/` | Which strategies were considered and their conditions | `persist_strategy_observation()` | `read_observations_local()` | Market Understanding | Strategy selection reasoning | Audit | KEEP |
| 17 | **- 1. Events** | `events/symbol={S}/date={D}/` | Raw market observations (candles, features, session, infrastructure) | `emit()` via EventStream | `read_stream()` | Raw System | What the bot actually saw (market data + system health) | **Primary source of truth** (observations) | KEEP |
| 18 | **- 17. Portfolio Rankings** | `portfolio_rankings/date={D}/` | Cross-symbol opportunity ranking per cycle | `persist_portfolio_ranking()` | None built-in | Portfolio | Which symbol was best when multiple competed | Audit | KEEP |
| 19 | **- 20. Edge Attribution** | `edge_attribution/schema_version=.../symbol={S}/date={D}/` | Per-trade causal factor decomposition | `persist_attribution()` | `load_attributions()`, edge_optimisation | Learning | Why each outcome happened (factor contributions) | Derived (causal analysis) | KEEP |
| 20 | **- 19. Edge Optimisation** | `edge_optimisation/schema_version=.../date={D}/` | Aggregated statistical edge discovery | `persist_edge_report()` | `load_edge_reports()`, strategy_compiler | Learning | Which features have persistent edges | Derived (from attribution) | KEEP |
| 21 | **- 18. Strategy Compiler** | `strategy_compiler/schema_version=.../date={D}/` | Parameterised strategy definitions from stable edges | `persist_strategy()` | `load_strategies()` | Learning | Generated trading rules | Derived (from edge_optimisation) | KEEP |
| 22 | **Quarantine** | `quarantine/schema_version=.../layer={L}/date={D}/` | Isolated invalid records (contract violations) | `QuarantineStore.quarantine()` | `QuarantineStore.load_quarantined()` | System Integrity | Records that failed validation | Audit (governance) | KEEP |
| 23 | **V10 Decisions** (v10-engine bucket) | `v10/decisions/symbol={S}/date={D}/` | Full V10 pipeline decision record (research-grade) | `upload_decision()` via `persist_v10_full()` | None built-in | V10 Decision | Complete V10 pipeline state for every evaluation | Primary (V10) | KEEP |
| 24 | **V10 Events** (v10-engine bucket) | `v10/events/symbol={S}/date={D}/` | V10 pipeline stage events (reasoning timeline) | `upload_events()` | None built-in | V10 Decision | How V10 reached its conclusion (per-stage breakdown) | Audit (V10) | KEEP |
| 25 | **V10 Executions** (v10-engine bucket) | `v10/executions/symbol={S}/date={D}/` | V10-specific execution attempts | `upload_execution()` | None built-in | V10 Execution | V10 execution bridge results | Primary (V10, not yet wired) | KEEP |
| 26 | **V10 Outcomes** (v10-engine bucket) | `v10/outcomes/symbol={S}/date={D}/` | V10 trade outcomes (R-multiple linked to decision) | `upload_outcome()` | None built-in | V10 Post-Trade | V10 trade performance | Primary (V10, not yet wired) | KEEP |

---

## Datasets NOT in S3 (Local Only)

| Dataset | Local Path | Purpose | Note |
|---|---|---|---|
| V3 Shadow Market Understanding | `logs/v3_shadow/market_understanding/` | Per-bar V3 pipeline output | Research-only, high volume |
| V3 Shadow Market Context | `logs/v3_shadow/market_context/` | V3 context snapshot | Research-only |
| V3 Shadow Opportunity Assessment | `logs/v3_shadow/opportunity_assessment/` | V3 opportunity assessment | Research-only |
| V3 Shadow Horizon/Entry/Risk/Execution | `logs/v3_shadow/{stage}/` | Each V3 shadow stage | Research-only |
| V10 Local Decisions | `logs/v10_decisions/` | Mirror of v10-engine S3 bucket | Always written |
| V10 Local Events | `logs/v10_events/` | Mirror of v10-engine events | Always written |
| Portfolio Shadow | `logs/portfolio_shadow/` | Shadow comparison output | Research-only |
| Shadow Rooms | `logs/shadow_rooms.jsonl` | Active shadow trade state | Runtime state, not archival |

---

## Lifecycle Validation: Your Hypothesis vs Reality

### Your Proposed Flow

```
MARKET UNDERSTANDING → DECISION MAKING → RISK → EXECUTION → TRADE LIFECYCLE → LEARNING → PORTFOLIO → EVENTS
```

### Actual Implementation Flow

```
MARKET UNDERSTANDING
  ├── Events (raw observations — candles, features, session markers)
  ├── Market Context (V3 structured interpretation)
  ├── Opportunities (detected, lifecycle tracked)
  ├── Opportunity Assessment (V3 shadow quality scoring)
  ├── Assessments (legacy engine scoring)
  └── Strategy Observations (which strategies were considered)

DECISION MAKING
  ├── Decision Ledger (one per cycle, every outcome)
  ├── Decision Audit (detailed scoring breakdown)
  ├── Decision Trace (pipeline journey, stages reached)
  └── V10 Decisions + V10 Events (V10-specific pipeline records)

PRE-EXECUTION
  └── Execution Context (frozen environment snapshot)

EXECUTION
  ├── Execution Results (every broker attempt)
  └── V10 Executions (V10-specific, not yet wired)

POST-EXECUTION
  ├── Protection Audit (broker SL/TP verification)
  └── Risk Deviation (planned vs actual risk)

TRADE LIFECYCLE
  ├── Trade Truth (pure execution reality — THE outcome record)
  ├── Trade Journal (operational trade log — P&L, commission, timing)
  └── V10 Outcomes (V10-specific outcome linkage, not yet wired)

SIMULATION
  └── Shadow Trades (paper trades — what would have happened)

LEARNING
  ├── Trade Truth Graph (relationship links between trades)
  ├── Edge Attribution (why each outcome happened)
  ├── Edge Optimisation (aggregated stable edges)
  └── Strategy Compiler (generated strategy definitions)

PORTFOLIO
  └── Portfolio Rankings (cross-symbol opportunity ranking)

SYSTEM INTEGRITY
  └── Quarantine (invalid records isolated for recovery)
```

### Corrections to Your Hypothesis

| Your Category | Correction |
|---|---|
| "Market Opportunity → Opportunity → Opportunity Assessment → Assessment" | Correct ordering exists but these are PARALLEL datasets, not sequential. `Opportunities` and `Assessments` are written independently. |
| "Decision Trace → Decision Ledger → Decision Audit" | Reversed. `Decision Ledger` is the canonical one-per-cycle record. `Trace` and `Audit` are deeper diagnostics written alongside (not after). |
| "Protection Audit → Risk Deviation" | Correct sequence. Protection audit is post-fill; risk deviation is post-close. |
| "Execution Context → Execution Result" | Correct. Context before execution, result after. |
| "Trades → Trade Journal" | In reality: `Trade Journal` is the operational record (written on close). `Trade Truth` is the clean execution-only record (written separately). Both come from the same close event. |
| "Shadow Trades → Strategy Observation" | These are INDEPENDENT, not sequential. Shadow trades simulate execution; strategy observations record which strategies were considered. |
| "Portfolio Ranking → Portfolio Shadow" | Correct. Ranking is the decision; shadow is the comparison of what ranking suggested vs what happened. |
| "Events" as separate | Events are the FIRST dataset written (raw observation layer) — not last. They ARE the foundation, not a side output. |

---

## Overall Assessment

### Does the S3 structure accurately mirror the bot's lifecycle?

**YES, with one gap.** Every lifecycle stage has at least one persistence layer. The gap: V10 EXECUTE decisions don't reliably produce `execution_results` or `trade_truth` records (due to the observer exception bug identified separately).

### Are there missing persistence points?

| Missing | Impact |
|---|---|
| V10 execution bridge trace | Added in this session (`execution_trace.py`) — not yet persisted to S3 |
| Macro context in decisions | Designed but not yet deployed (Phase 3/4) |
| V10 ledger write bug | `_write_to_ledger` calls `.write()` which doesn't exist on `DecisionLedgerWriter` |

### Are there datasets that overlap significantly?

| Overlap Pair | Assessment |
|---|---|
| **Decision Ledger** ↔ **Decision Audit** | Overlap: both record decisions. Distinction: ledger is one-line summary per cycle; audit has full scoring breakdown. **BOTH justified.** |
| **Trade Truth** ↔ **Trade Journal** | Overlap: both record completed trades. Distinction: Trade Truth is execution-only (no strategy); Trade Journal has full operational context (pattern, commission, swap). **BOTH justified** — Trade Truth is the research-clean record; Journal is operational. |
| **Assessments** ↔ **Opportunity Assessment** | Overlap: both score market quality. Distinction: Assessments are legacy engine; Opportunity Assessment is V3 shadow. **Merge candidate** — but both are observational and low-volume. Keep until V10 stabilises. |
| **V10 Decisions** ↔ **Decision Ledger** | Overlap: V10 persistence_adapter writes BOTH. **However:** ledger write is broken (`.write()` bug). Fix needed, then both serve distinct purposes (ledger = cross-engine; V10 = V10-specific research). |

### Are there datasets that are no longer justified?

| Dataset | Verdict |
|---|---|
| **Assessments** (legacy) | Still written but legacy engine is disabled. **KEEP for now** — provides comparison data until V10 fully validated. Remove after V10 freeze. |
| **V3 Shadow** (local only) | High volume, useful for V10 development. **KEEP until V10 validated.** Then remove. |
| **Strategy Compiler + Edge Optimisation** | Currently NOT actively producing output (no trade volume to analyse). **KEEP infrastructure** — will activate when trade volume increases. |

### Is the architecture stable enough to freeze?

**YES — with two conditions:**

1. **Fix the `_write_to_ledger` bug** (calls `.write()` instead of `.record(**entry)`) — ensures V10 EXECUTE decisions appear in the decision ledger.
2. **Confirm the observer exception fix** (wrap `_observers.notify_all()` in try/except) — ensures V10 EXECUTE reaches execution.

After these two fixes, the architecture is complete and stable. Every lifecycle stage has persistence, every dataset has a clear purpose, and the two-bucket structure (legacy + V10) provides clean separation for the transition period.

**Recommendation: Freeze after the two fixes above. The architecture is sound.**
