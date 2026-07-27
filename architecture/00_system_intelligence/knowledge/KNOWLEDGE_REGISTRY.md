# OBSERVER KNOWLEDGE REGISTRY

**Type:** Index. Maps every knowledge domain the Observer owns.
**Purpose:** Validate the complete knowledge map before expanding cards.
**Rule:** No domain exists unless it has identified evidence sources.

---

## Registry

| ID | Domain | Card | Status |
|:--:|:-------|:-----|:------:|
| 01 | FOUNDATION | `01_FOUNDATION.md` | ✅ Created |
| 02 | STRATEGY | `02_STRATEGY.md` | Pending |
| 03 | MARKET_CONTEXT | `03_MARKET_CONTEXT.md` | Pending |
| 04 | DECISION_ENGINE | `04_DECISION_ENGINE.md` | Pending |
| 05 | RISK_SYSTEM | `05_RISK_SYSTEM.md` | Pending |
| 06 | EXECUTION_SYSTEM | `06_EXECUTION_SYSTEM.md` | Pending |
| 07 | TRADE_LIFECYCLE | `07_TRADE_LIFECYCLE.md` | Pending |
| 08 | DATA_PERSISTENCE | `08_DATA_PERSISTENCE.md` | Pending |
| 09 | RESEARCH_ENGINE | `09_RESEARCH_ENGINE.md` | Pending |
| 10 | PERFORMANCE_ANALYTICS | `10_PERFORMANCE_ANALYTICS.md` | Pending |
| 11 | CONFIGURATION | `11_CONFIGURATION.md` | Pending |
| 12 | RUNTIME_OPERATIONS | `12_RUNTIME_OPERATIONS.md` | Pending |
| 13 | OBSERVABILITY | `13_OBSERVABILITY.md` | Pending |
| 14 | LEARNING_AND_IMPROVEMENT | `14_LEARNING_AND_IMPROVEMENT.md` | Pending |

---

## Domain Definitions

### 01 FOUNDATION

**Purpose:** What the system IS — identity, universe, posture, maturity.

**Questions it answers:**
- What system is this?
- What broker/platform?
- Which symbols are enabled?
- What mode is active?
- What is currently enabled vs disabled?
- How much evidence exists?

**Evidence sources:** `runtime/heartbeat.json`, `core/config.py`, `obs.state()`, `obs.config()`, `obs.health()`, `obs.trades()`

**Authority documents:** `architecture/02_authority/TOP_LEVEL_SYSTEM_AUTHORITY_AUDIT.md`

**Confidence:** HIGH

---

### 02 STRATEGY

**Purpose:** What trading approach the system uses — patterns, signals, scoring philosophy.

**Questions it answers:**
- What patterns does the system detect?
- How are signals generated?
- What scoring model is used?
- What strategy classification exists?
- What are the weight profiles?
- What is the entry thesis?

**Evidence sources:** `strategy/signal_orchestrator.py`, `core/pipeline/scoring_engine.py`, `core/pipeline/strategy_classifier.py`, `logs/decision_trace/` (components, strategy fields)

**Authority documents:** `architecture/04_execution/HORIZON_EXECUTION_ARCHITECTURE.md`, `core/pipeline/new_engine.py` docstring

**Confidence:** HIGH (code evidence), MEDIUM (no plain-English strategy description exists)

---

### 03 MARKET_CONTEXT

**Purpose:** How the system understands market conditions — regimes, structure, timeframes.

**Questions it answers:**
- What regime is active?
- How are timeframes used?
- What is H4/H1/M15 responsibility?
- How does market context influence decisions?
- What is the bias FSM?

**Evidence sources:** `core/market_context/`, `core/timeframes/`, `logs/market_context/`, `decision_trace` regime/htf fields

**Authority documents:** `architecture/06_market_intelligence/CURRENT_MARKET_CONTEXT_ARCHITECTURE.md`

**Confidence:** HIGH

---

### 04 DECISION_ENGINE

**Purpose:** How the system produces EXECUTE or NO_TRADE decisions.

**Questions it answers:**
- How does the decision pipeline work?
- What stages exist?
- Where can a decision be rejected?
- What is the scoring threshold?
- What is the EV gate?
- What is the swing filter?
- Why was a specific decision made?

**Evidence sources:** `core/pipeline/new_engine.py`, `core/pipeline/execution_policy.py`, `logs/decision_ledger/`, `logs/decision_trace/`, `obs.explain()`

**Authority documents:** `core/pipeline/new_engine.py` docstring (CAN/CANNOT), `architecture/03_decision/DECISION_EXPLAINABILITY_AUDIT.md`

**Confidence:** HIGH

---

### 05 RISK_SYSTEM

**Purpose:** How the system prevents unsafe trades — guards, sizing, limits.

**Questions it answers:**
- Which guards exist?
- Which guards are enabled?
- What blocked a specific trade?
- What limits apply?
- How is position size calculated?
- What is the veto chain order?

**Evidence sources:** `risk/runtime_guard_chain.py`, `risk/manager.py`, `logs/decision_ledger/` (RISK_BLOCK), `obs.guards()`, `obs.config()`

**Authority documents:** `architecture/02_authority/TOP_LEVEL_SYSTEM_AUTHORITY_AUDIT.md` §2.6, `risk/runtime_guard_chain.py` docstring

**Confidence:** HIGH

---

### 06 EXECUTION_SYSTEM

**Purpose:** How the system submits orders and handles broker responses.

**Questions it answers:**
- How are orders submitted?
- What happens on broker rejection?
- What was the fill price?
- Was there slippage?
- Was SL/TP protection verified?

**Evidence sources:** `execution/execution_orchestrator.py`, `execution/mt5_execution.py`, `logs/execution_results/`, `logs/execution_context/`, `logs/protection_audit/`

**Authority documents:** `execution/execution_orchestrator.py` docstring (CAN/CANNOT)

**Confidence:** HIGH

---

### 07 TRADE_LIFECYCLE

**Purpose:** How positions are managed from open to close.

**Questions it answers:**
- How is a position managed after opening?
- What triggers break-even?
- What triggers trailing stop?
- What is the time exit?
- Why did a trade close?
- How long do trades last?

**Evidence sources:** `core/trade_management/manager.py`, `logs/trade_journal/`, `logs/trade_truth/`, `obs.trades()`, `obs.explain_by_trade()`

**Authority documents:** `core/trade_management/manager.py` docstring, `architecture/04_execution/HORIZON_EXECUTION_POLICY.md` §6

**Confidence:** HIGH

---

### 08 DATA_PERSISTENCE

**Purpose:** How and where all system data is stored.

**Questions it answers:**
- What datasets exist?
- Where are they stored?
- Are they healthy?
- What schema versions exist?
- Is S3 mirroring working?
- What joins connect datasets?

**Evidence sources:** `obs.health()`, `architecture/07_persistence/PERSISTENCE_ARCHITECTURE_AUDIT_FINAL.md`, `architecture/07_persistence/FIELD_POPULATION_AUDIT.md`

**Authority documents:** `architecture/07_persistence/PERSISTENCE_ARCHITECTURE_AUDIT_FINAL.md`

**Confidence:** HIGH

---

### 09 RESEARCH_ENGINE

**Purpose:** How the system evaluates performance and tests hypotheses.

**Questions it answers:**
- What research experiments exist?
- What do shadow trades show?
- Is INTRADAY ready for activation?
- What are the research contracts?
- How are observations compared to expectations?

**Evidence sources:** `research_engine/`, `logs/shadow_trades/`, `research_reports/`, `core/horizon/research_contract.py`, `core/horizon/shadow_evaluation.py`

**Authority documents:** `architecture/09_research/RESEARCH_ENGINE_ARCHITECTURE.md`

**Confidence:** HIGH

---

### 10 PERFORMANCE_ANALYTICS

**Purpose:** How the system has performed — win rate, R-multiples, pattern effectiveness.

**Questions it answers:**
- What is the win rate?
- What is the average R?
- Which patterns are profitable?
- Which symbols perform best?
- Is performance improving or degrading?
- How does actual compare to expected?

**Evidence sources:** `obs.trades()`, `logs/trade_journal/`, `logs/trade_truth/`, `core/horizon/observation_builder.py`

**Authority documents:** `architecture/08_observability/PRODUCTION_INTELLIGENCE_READINESS_REVIEW.md`

**Confidence:** HIGH (data exists), LOW (sample size: 31 trades)

---

### 11 CONFIGURATION

**Purpose:** What runtime rules are active and how they affect behaviour.

**Questions it answers:**
- What is currently enabled?
- What limits apply?
- What feature flags are active?
- What would change if I modified X?
- What configuration controls this behaviour?

**Evidence sources:** `obs.config()`, `core/config.py`

**Authority documents:** `core/config.py` (single source of all config)

**Confidence:** HIGH

---

### 12 RUNTIME_OPERATIONS

**Purpose:** How the system runs — lifecycle, health, startup, shutdown, recovery.

**Questions it answers:**
- Is the bot running?
- When did it start/stop?
- Is MT5 connected?
- What cycle is it on?
- How does startup recovery work?
- What happens on crash?

**Evidence sources:** `obs.state()`, `runtime/heartbeat.json`, `core/runtime/live_scanner.py`, `core/runtime/startup_recovery.py`, `core/runtime/health_monitor.py`

**Authority documents:** `architecture/02_authority/TOP_LEVEL_SYSTEM_AUTHORITY_AUDIT.md` §2.3

**Confidence:** HIGH

---

### 13 OBSERVABILITY

**Purpose:** How the system makes its behaviour visible — events, logging, diagnostics.

**Questions it answers:**
- What events are emitted?
- What monitoring exists?
- What production readiness score?
- Are there diagnostic gaps?
- What cannot currently be observed?

**Evidence sources:** `events/`, `core/event_stream.py`, `obs.health()`, `architecture/08_observability/PRODUCTION_INTELLIGENCE_READINESS_REVIEW.md`

**Authority documents:** `architecture/08_observability/PRODUCTION_INTELLIGENCE_READINESS_REVIEW.md`

**Confidence:** HIGH

---

### 14 LEARNING_AND_IMPROVEMENT

**Purpose:** How the system learns from outcomes and evolves.

**Questions it answers:**
- What edges have been discovered?
- Which strategies are degrading?
- What does edge attribution show?
- Is the system improving?
- What should change next?

**Evidence sources:** `logs/edge_attribution/`, `logs/edge_optimisation/`, `logs/strategy_compiler/`, `logs/learning/`, `core/horizon/research_report.py`

**Authority documents:** `architecture/09_research/CANDIDATE_PROMOTION_ASSESSMENT.md`

**Confidence:** MEDIUM (learning infrastructure exists but limited data has been processed)

---

## Validation Checklist

| Check | Result |
|:------|:------:|
| Every domain has identified evidence sources | ✅ |
| Every domain has at least one authority document | ✅ |
| Every domain has defined questions it answers | ✅ |
| No domain overlaps completely with another | ✅ |
| All 24 persistence datasets are covered by at least one domain | ✅ |
| All 15 code package domains (from OBSERVER_BLUEPRINT) map to these 14 cards | ✅ |

---

*End of Knowledge Registry.*
