# OBSERVER INTERACTION MODEL

**Status:** Design document. Approval required before implementation.
**Purpose:** Define how the Observer should behave as an architectural assistant.
**Principle:** The Observer is system memory. Kiro is reasoning. Together: "Ask me anything."

---

## 1. Observer Mission

### What the Observer is NOT

**Not a command interface.** A command interface requires the user to know the right syntax. The user must already know what to ask and how to ask it. This puts the burden on the human.

**Not a search engine.** A search engine returns documents that might contain the answer. The user must still read, interpret, and connect the results. It finds haystacks, not needles.

### What the Observer IS

**An architectural assistant's memory.** It already knows:
- What components exist and what they own
- Where evidence lives for any system behaviour
- How to retrieve and structure evidence for reasoning
- What it doesn't know (and says so)

The interaction model is:

```
Human asks any question about the machine
    ↓
Kiro (reasoning) determines what to investigate
    ↓
Observer (memory) retrieves structured evidence
    ↓
Kiro (reasoning) explains the finding
```

The Observer removes manual archaeology. Instead of the human (or Kiro) searching through 24 datasets and 84 architecture documents, the Observer knows which source answers which question and returns structured evidence ready for reasoning.

### The Target Experience

The user types freely:
```
"What exists for Discord?"
"Why is the bot not profitable?"
"How does the decision pipeline actually work?"
```

Kiro uses the Observer to investigate, then explains the finding with evidence citations.

---

## 2. Natural Language Question Model

### No Prefixes Required

The user should never need to type `ask` or `explain` or any command prefix. The Observer (through Kiro's steering file) handles any natural text.

### How It Works

The user types anything. Kiro classifies the intent using its own language understanding (not the Observer's keyword matcher). Kiro then calls the appropriate Observer method(s) to gather evidence.

The Observer's existing interface is sufficient:

| Kiro Needs | Observer Provides |
|:----------:|:-----------------:|
| "Is it running?" | `obs.state()` |
| "Why didn't X trade?" | `obs.explain(symbol)` |
| "Performance data" | `obs.trades()` |
| "What's blocking trades?" | `obs.guards()` |
| "Where does Y belong?" | `obs.route(question)` + `obs.domains_list()` |
| "Is data healthy?" | `obs.health()` |
| "What config is active?" | `obs.config()` |

For questions the Observer cannot directly answer (advisory, architectural, comparative), Kiro uses Observer evidence PLUS architecture documents PLUS its own reasoning.

### The Key Insight

The Observer does not need to understand natural language. **Kiro already does.** The Observer needs to be a reliable, comprehensive, structured evidence source that Kiro can query programmatically.

---

## 3. Question Classification System

Classification happens in Kiro (not the Observer). The Observer provides evidence regardless of question type.

### Investigation: "What exists?"

**User intent:** Discover system capabilities, components, or data.

**Examples:**
- "What notification systems exist?"
- "What datasets are persisted?"
- "How does execution work?"
- "What exists for Discord?"

**Observer role:** `obs.route(question)` identifies relevant domains. `obs.domains_list()` shows all domains. Architecture documents provide structural detail.

**Evidence sources:** Architecture docs, source code structure, domain model.

---

### Explanation: "Why did something happen?"

**User intent:** Understand causality — what led to an outcome.

**Examples:**
- "Why didn't EURUSD trade today?"
- "Why was this opportunity rejected?"
- "Why did this trade lose?"
- "Why is the spread guard blocking so much?"

**Observer role:** `obs.explain(symbol)` provides the causal chain. `obs.guards()` provides rejection statistics. `obs.explain_by_trade(id)` provides trade outcome reasoning.

**Evidence sources:** decision_ledger, decision_trace, execution_results, trade_truth.

---

### Analysis: "What does the evidence show?"

**User intent:** Aggregate understanding — patterns, trends, comparisons.

**Examples:**
- "Which guard blocks the most trades?"
- "Which patterns perform best?"
- "Has win rate improved over time?"
- "How many weeks of data exist?"

**Observer role:** `obs.trades()` provides aggregates. `obs.guards()` provides block counts. `obs.health()` provides data volume/freshness.

**Evidence sources:** trade_journal (aggregated), decision_ledger (aggregated), dataset file counts.

---

### Advisory: "What should I do?"

**User intent:** Get a recommendation supported by evidence.

**Examples:**
- "What should I improve next?"
- "What prevents profitability?"
- "Is the system ready for production?"
- "Should I enable INTRADAY?"

**Observer role:** Provides evidence that Kiro reasons over. Observer does NOT produce recommendations itself — it supplies the data.

**Evidence sources:** All sources combined. Kiro synthesises using Observer evidence + architecture knowledge + production readiness scores.

---

### Architecture: "Where does this belong?"

**User intent:** Navigate the system — find ownership, location, responsibility.

**Examples:**
- "Where should Discord improvements live?"
- "Which component owns trade management?"
- "What will this change affect?"
- "Where is the risk authority documented?"

**Observer role:** `obs.route(question)` identifies the relevant domain(s). `obs.domains_list()` shows the full domain map. Architecture documents provide detailed ownership.

**Evidence sources:** Domain model, architecture documents (especially 02_authority/).

---

## 4. Evidence Discovery Model

### Evidence Layers (What the Observer Knows)

| Layer | What It Contains | How Observer Accesses It |
|:-----:|:----------------:|:------------------------:|
| **Architecture** | Design intent, ownership, boundaries | Read architecture/ markdown files |
| **Configuration** | Active rules, limits, flags | `obs.config()` → import core.config |
| **Runtime** | Process health, MT5 state | `obs.state()` → runtime/heartbeat.json |
| **Decision** | Per-cycle decisions + reasoning | `obs.explain(sym)` → decision_ledger + decision_trace |
| **Execution** | Broker interactions | `obs.explain_by_trade(id)` → execution_results |
| **Trade** | Completed outcomes | `obs.trades()` → trade_journal |
| **Research** | Shadow results, horizon readiness | `obs.route("research")` → shadow_trades, research_reports |
| **Health** | Data pipeline status | `obs.health()` → file timestamps + counts |

### Discovery Logic

For any question, the Observer finds evidence by:
1. Identifying which **concepts** are involved (via domain keywords)
2. Mapping concepts to **datasets** (via domain.evidence_sources)
3. Retrieving **records** from those datasets
4. Returning structured evidence with source citations

---

## 5. Response Structure

Every interaction between Kiro and the user (when using Observer evidence) should follow this structure:

### 1. Understanding
State what the question is about and which type it is.

*"You're asking about decision outcomes for EURUSD — this is an explanation question."*

### 2. Investigation Path
State which evidence sources were consulted.

*"I checked the decision_ledger and decision_trace for EURUSD."*

### 3. Evidence Found
Present the facts from the system — no interpretation yet.

*"Latest decision: NO_TRADE. Terminal stage: swing. Reason: h1_swing_bearish."*

### 4. Explanation
Interpret what the evidence means.

*"The pattern was detected and scored well (0.55), but the swing filter blocked it because H1 structure doesn't confirm a BUY direction."*

### 5. Unknowns
State what cannot be determined from available evidence.

*"I cannot determine whether H1 structure has since changed — this requires live market data."*

### 6. Recommendation (only when the question is advisory)
*"Consider reviewing the swing filter's strictness — it blocked 40% of scored opportunities this week."*

---

## 6. Help System Design

When the user asks for help, the response should teach interaction style, not list commands.

```
observer> help

I understand questions about the trading system. Ask me naturally:

  INVESTIGATE:  "What notification systems exist?"
                "How does the decision pipeline work?"

  EXPLAIN:      "Why didn't GBPUSD trade?"
                "Why did my last trade lose?"

  ANALYSE:      "What's my win rate?"
                "Which guard blocks the most?"

  ADVISE:       "What should I focus on next?"
                "Is the system ready for live trading?"

  NAVIGATE:     "Where does Discord belong?"
                "Which component owns risk?"

I use these evidence sources:
  - 24 persistence datasets (decisions, trades, shadows, events)
  - Architecture documentation (ownership, design, policy)
  - Runtime state (heartbeat, config, health)

I will always tell you where I found the answer and what I cannot determine.
```

---

## 7. Domain Discovery (Validated Against Repository)

Verified by inspecting actual source tree:

| Domain | Key Packages | Evidence Sources |
|--------|:------------|:-----------------|
| Configuration | `core/config.py` | `obs.config()` |
| Runtime | `core/runtime/` (12 modules) | `obs.state()`, heartbeat |
| Decision | `core/pipeline/` (47 modules) | `obs.explain()`, decision_ledger, decision_trace |
| Risk | `risk/` (19 modules) | `obs.guards()`, decision_ledger RISK_BLOCK |
| Execution | `execution/` (3 modules) | execution_results, execution_context |
| Trade Management | `core/trade_management/` (6 modules) | trade_journal |
| Market Intelligence | `core/market_context/`, `core/timeframes/`, `core/horizon/` | market_context, decision_trace regime fields |
| Persistence | `core/persistence/`, `core/storage/`, 23 writers | `obs.health()`, all datasets |
| Research | `research_engine/` | shadow_trades, research_reports |
| Learning | `core/learning/`, `core/edge_attribution.py`, `core/edge_optimisation.py` | edge_attribution, learning |
| Observability | `core/event_stream.py`, `core/event_bus.py` | events/ |
| Portfolio | `core/portfolio_ranking/` | portfolio_rankings, portfolio_shadow |
| Infrastructure | `main.py`, `core/mt5_connection.py` | heartbeat |
| Patterns | `patterns/`, `strategy/` | decision_trace pattern fields, opportunities |
| External | Discord notifier, S3/AWS, MT5 API | Not directly queryable (external systems) |

**15 domains validated.** The Observer's `domains.py` already maps all 15 with keywords, evidence sources, and authority files.

---

## 8. Boundaries

### The Observer CAN:

| Capability | Method |
|:----------:|:------:|
| Retrieve runtime state | `obs.state()` |
| Show active configuration | `obs.config()` |
| Check dataset health | `obs.health()` |
| Explain specific decisions | `obs.explain(symbol)` |
| Explain trade outcomes | `obs.explain_by_trade(id)` |
| Summarise performance | `obs.trades()` |
| Show guard statistics | `obs.guards()` |
| Route questions to domains | `obs.route(question)` |
| List known domains | `obs.domains_list()` |

### The Observer CANNOT:

| Forbidden | Reason |
|:---------:|:------:|
| Modify configuration | Human authority |
| Place trades | Execution authority |
| Override risk controls | Guard chain authority |
| Invent evidence | Must cite sources |
| Make market predictions | Outside system scope |
| Reason about questions | Kiro's responsibility |
| Generate recommendations | Kiro's responsibility |

### The Boundary Line

The Observer provides EVIDENCE. Kiro provides REASONING. The human provides DECISIONS.

---

## 9. Implementation Recommendation

### Current Capability Assessment

| Capability | Status | Gap |
|:----------:|:------:|:---:|
| Runtime state retrieval | ✅ Complete | None |
| Configuration inspection | ✅ Complete | None |
| Dataset health checks | ✅ Complete | None |
| Decision explanation | ✅ Complete | No time-range filtering |
| Trade analysis | ✅ Complete | No time-range comparison |
| Guard analysis | ✅ Complete | No time-range filtering |
| Domain routing | ✅ Complete | No concept-level routing (keywords only) |
| Natural language handling | ✅ Handled by Kiro via steering file | None |

### What Is NOT Missing

- Natural language understanding (Kiro handles this)
- Reasoning over evidence (Kiro handles this)
- Advisory generation (Kiro handles this)
- Arbitrary question handling (Kiro handles this)

### What IS Actually Missing (Observer-level gaps)

| # | Gap | Impact | Fix |
|:-:|:----|:------:|:----|
| 1 | No time-range filtering | Cannot answer "last week vs this week" | Add optional `date_from`/`date_to` params to trades/guards/explain |
| 2 | No aggregate decision stats | Cannot answer "what % of opportunities pass scoring?" | Add `obs.decision_summary()` |
| 3 | No architecture doc search | Cannot answer "where is X documented?" without reading files | Add `obs.find_doc(topic)` |

### Recommended Implementation Order

1. **Add time-range parameters** to `trades()`, `guards()`, `explain()` — allows temporal analysis
2. **Add `decision_summary()`** — counts decisions by type/stage (enables funnel analysis)
3. **Add `find_doc(topic)`** — searches architecture doc titles/headers for topic keywords

**Total effort: ~3-4 hours for all three.**

### Validation Questions (Test Manually Before Building)

Ask these questions via Kiro (using the existing steering file) and identify where evidence retrieval fails:

1. "How many weeks of validation data do I have?"
2. "Has win rate improved over the last 2 weeks?"
3. "What percentage of opportunities get past scoring?"
4. "Where is the Discord notification system documented?"
5. "What would a senior engineer fix first?"

If Kiro + Observer can answer them NOW → no new code needed.
If evidence retrieval fails → that's what to build next.

---

*End of Observer Interaction Model.*
