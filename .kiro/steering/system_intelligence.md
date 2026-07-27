---
inclusion: auto
---

# System Intelligence Observer

The trading bot has a System Intelligence Observer — a read-only evidence layer that provides structured access to runtime state, decision history, trade outcomes, and system health.

## Authority Hierarchy

```
Owner (human)           → makes decisions, approves changes
Kiro (AI assistant)     → investigates, reasons, explains, advises
Observer (evidence API) → retrieves trusted system evidence
Trading System          → executes trades, persists data
```

The Observer CANNOT modify configuration, place trades, override risk, or create false explanations. It reads only.

## Available Interface

```python
from system_intelligence import Observer
obs = Observer()
```

| Method | Returns |
|--------|---------|
| `obs.state()` | Runtime status, execution mode, heartbeat, MT5 state, strategy, symbols |
| `obs.config()` | Active configuration: feature flags, limits, guards, horizons |
| `obs.health()` | Dataset freshness for all 24 persistence datasets |
| `obs.explain(symbol)` | Latest decision reasoning: terminal stage, reason, score, evidence chain |
| `obs.explain_by_trade(trade_id)` | Trade lifecycle: entry, exit, R-multiple, close reason, explanation |
| `obs.trades(days=30)` | Performance summary: win rate, avg R, PnL, by-horizon, by-pattern |
| `obs.guards()` | Guard block statistics: counts by guard name and symbol |
| `obs.route(question)` | Domain routing: which evidence sources answer this question |
| `obs.domains_list()` | Lists all 15 architecture domains the Observer understands |

## Investigation Workflow

When the user asks about the trading system:

1. **Classify** the question (runtime, decision, risk, execution, performance, architecture, research, advisory)
2. **Query Observer** for evidence — do not assume answers without checking
3. **Cross-reference** architecture documents for intended design (architecture/ folder)
4. **Compare** intended behaviour ("what should happen") against actual evidence ("what did happen")
5. **Explain** with: finding, evidence source, confidence, unknowns, recommended next step

## Evidence Routing

| Question Type | Primary Evidence Source |
|:---:|:---:|
| Decision questions | `obs.explain(symbol)` → decision_ledger + decision_trace |
| Execution questions | `obs.explain_by_trade(id)` → execution_results + trade_journal |
| Performance questions | `obs.trades()` → trade_journal + trade_truth |
| Health questions | `obs.health()` → file timestamps + record counts |
| Architecture questions | Architecture docs in `architecture/` folder tree |
| Configuration questions | `obs.config()` → core/config.py values |
| Risk/guard questions | `obs.guards()` → decision_ledger RISK_BLOCK records |
| Research questions | `obs.route(question)` → shadow_trades, research_reports |

## Evidence Rules

- The Observer is the source of truth for runtime behaviour
- Never invent information the Observer cannot find
- If evidence does not exist, say: "The system does not persist enough evidence to answer this confidently"
- Every claim must reference an evidence source (dataset, file, or architecture document)

## Advisory Questions

For questions like "Why isn't the bot profitable?" or "What should I improve?":

Structure the response as:

1. **Current state** — what does the evidence show?
2. **Evidence** — specific data points from Observer
3. **Interpretation** — what this means in context
4. **Confidence** — how certain, based on sample size and data quality
5. **Recommended action** — what to investigate or change next

## Key Architecture Documents

| Topic | Location |
|-------|----------|
| Governing principles | `architecture/01_foundation/SYSTEM_INTELLIGENCE_PRINCIPLES.md` |
| Authority hierarchy | `architecture/02_authority/TOP_LEVEL_SYSTEM_AUTHORITY_AUDIT.md` |
| Persistence (24 datasets) | `architecture/07_persistence/PERSISTENCE_ARCHITECTURE_AUDIT_FINAL.md` |
| Field population | `architecture/07_persistence/FIELD_POPULATION_AUDIT.md` |
| Production readiness | `architecture/08_observability/PRODUCTION_INTELLIGENCE_READINESS_REVIEW.md` |
| Horizon policy | `architecture/04_execution/HORIZON_EXECUTION_POLICY.md` |
| System state report | `docs/SYSTEM_STATE_REPORT.md` |
| Observer blueprint | `architecture/00_system_intelligence/OBSERVER_BLUEPRINT.md` |

## Development Rule

When considering new Observer features, ask: "Does this improve evidence retrieval?" If yes, build it. If it duplicates reasoning already provided by Kiro, do not build it. The Observer should become a better map of the machine, not a replacement intelligence system.
