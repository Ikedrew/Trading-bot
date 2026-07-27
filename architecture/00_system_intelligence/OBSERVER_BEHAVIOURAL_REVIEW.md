# OBSERVER BEHAVIOURAL REVIEW

**Status:** Governing philosophy for all future Observer development.
**Based on:** Real validation questions asked during system development.
**Principle:** The Observer is trusted memory. The reasoning layer synthesises. The human decides.

---

## 1. Question Classification From Validation

Every question asked during validation falls into one of five categories. These are not arbitrary — they represent the five distinct cognitive needs a system owner has.

### Class 1: Factual Questions

**What the user is really asking:** "Tell me a specific piece of information that exists somewhere in the system."

**Examples observed:**
- "Is the bot running?"
- "What configuration is active?"
- "Which symbols are enabled?"
- "What is the current heartbeat status?"
- "How many trades exist in the journal?"

**Evidence required:** Single source, single read. No joins, no aggregation.

**Reasoning required:** None. The answer IS the evidence.

**What a good answer looks like:**
```
Status: SHUTDOWN
Last heartbeat: 2026-07-24T23:58:01Z
Mode: REPLAY
Symbols: 7 (EURUSD, GBPUSD, USDJPY, USDCHF, USDCAD, AUDUSD, NZDUSD)
```

**Where Observer succeeds:** Completely. `obs.state()`, `obs.config()`, `obs.health()` answer these directly.

**Where it falls short:** Nowhere. Factual questions are fully served.

---

### Class 2: Explanation Questions

**What the user is really asking:** "Something happened (or didn't happen). I need to understand the causal chain that produced this outcome."

**Examples observed:**
- "Why didn't EURUSD trade today?"
- "Why was this opportunity rejected?"
- "Why did my last trade lose?"
- "Explain my entire trading pipeline to a new engineer."

**Evidence required:** Multi-source. Requires following the identity chain across datasets (decision_ledger → decision_trace → execution_results → trade_truth).

**Reasoning required:** Significant. Must construct a causal narrative: "This happened BECAUSE that happened BECAUSE the system was in state X."

**What a good answer looks like:**
```
EURUSD detected THREE_WHITE_SOLDIERS (pattern passed).
Scored 0.55 (above threshold).
Terminal stage: swing.
Reason: H1 structure is bearish — BUY blocked without Break of Structure.
The pattern and score were fine, but multi-timeframe confirmation failed.
```

**Where Observer succeeds:** `obs.explain(symbol)` provides the terminal stage, reason, and supporting fields. The causal chain is reconstructable.

**Where it falls short:**
- Cannot show the FULL pipeline progression (which stages passed vs failed)
- Cannot show what WOULD have happened (needs shadow trade cross-reference)
- Cannot show temporal context ("this has been happening all week")

---

### Class 3: Comparison Questions

**What the user is really asking:** "I need to understand the relationship between two or more things — across time, across symbols, or across configurations."

**Examples observed:**
- "Has win rate improved over the last 2 weeks?"
- "Which pattern performs best?"
- "Which guard blocks the most?"
- "How many weeks of validation data do I have?"
- "Is the system improving?"

**Evidence required:** Aggregate data with grouping. Often requires time-windowed queries (this week vs last week). May require cross-dataset joins.

**Reasoning required:** Moderate. Must compute metrics, identify trends, and determine whether differences are meaningful or noise.

**What a good answer looks like:**
```
Win rate:
  Week 1 (Jul 15-21): 40% (5 trades)
  Week 2 (Jul 22-24): 33% (6 trades)
  
Sample too small for statistical significance.
Recommend 50+ trades minimum before drawing conclusions.
```

**Where Observer succeeds:** `obs.trades()` provides by-pattern and by-horizon breakdowns. `obs.guards()` provides block counts.

**Where it falls short:**
- No time-range filtering (cannot compare week-over-week)
- No statistical significance assessment
- No trend detection (only snapshots, not progressions)

---

### Class 4: Investigation Questions

**What the user is really asking:** "I don't know exactly what to look for. I need the system to help me explore a problem space."

**Examples observed:**
- "What exists for Discord?"
- "Where should I add a new feature?"
- "What part of the architecture is weakest?"
- "What prevents this system from being profitable?"
- "What would a senior engineer fix first?"

**Evidence required:** Broad. May span architecture documents, runtime data, performance metrics, and code structure simultaneously. The user doesn't know which source has the answer.

**Reasoning required:** High. Must synthesise across multiple evidence layers, identify patterns, weigh significance, and form a coherent investigative narrative.

**What a good answer looks like:**
```
The system's current -0.255 avg R suggests the edge is marginal or negative.
Evidence:
  - 31 trades, 35.5% win rate
  - TWEEZER_TOP is the only consistently positive pattern (+0.45 PnL)
  - 81 guard blocks occurred (investigate whether profitable trades were blocked)
  - Shadow trades show INTRADAY has positive expectancy in shadow mode

Primary investigation paths:
  1. Are guards removing more edge than they protect? (compare blocked vs shadow outcomes)
  2. Is the scoring threshold correct? (decision_trace shows threshold_gap distribution)
  3. Is the SL placement optimal? (trade_truth shows R-distribution)
```

**Where Observer succeeds:** `obs.route(question)` identifies relevant domains. `obs.trades()` + `obs.guards()` provide key metrics.

**Where it falls short:**
- Cannot autonomously identify "what's wrong" — requires Kiro to synthesise
- Cannot cross-reference guard blocks with shadow outcomes in a single query
- Cannot answer "what would a senior engineer do" without domain expertise (Kiro's job)

---

### Class 5: Recommendation Questions

**What the user is really asking:** "Given everything you know about the system, what action should I take?"

**Examples observed:**
- "What should I improve next?"
- "Is the system ready for production?"
- "Should I enable INTRADAY?"
- "How many more weeks of validation do I need?"
- "What has the highest value?"

**Evidence required:** Comprehensive. Requires the full evidence base: performance data, research results, architecture maturity, data quality, shadow outcomes, known gaps.

**Reasoning required:** Very high. Must weigh multiple factors, assess risk, consider confidence levels, identify assumptions, and produce actionable guidance with appropriate caveats.

**What a good answer looks like:**
```
Recommendation: Focus on SL placement before adding more features.

Evidence:
  - Avg R of -0.255 suggests losses are larger than intended
  - risk_deviation dataset tracks actual vs intended loss
  - Break-even buffer is 0.1 (absolute price — likely miscalibrated)
  
Confidence: MEDIUM (31 trades is a small sample)
Assumption: The edge exists but risk management is consuming it.
Risk: If edge doesn't exist, SL changes won't help.
Next validation: Collect 50 more trades, then reassess.
```

**Where Observer succeeds:** Provides all the raw metrics needed (trades, guards, health, config).

**Where it falls short:**
- Cannot weigh importance (that's reasoning, not evidence)
- Cannot assess "readiness" (requires judgement)
- Cannot predict outcomes (only report history)
- These are correctly Kiro's responsibility, not the Observer's

---

## 2. The Investigation Workflow

This is the governing process for how any question should be handled. The Observer and Kiro work together through this workflow. Neither skips steps.

### Step 1: Understand the Objective

Before gathering any evidence, determine:

- **What type of question is this?** (Fact / Explanation / Comparison / Investigation / Recommendation)
- **What is the user actually trying to achieve?** (Not just what they literally asked)
- **What would constitute a satisfying answer?** (Specific data point? Causal chain? Action plan?)

This step belongs to the reasoning layer (Kiro). The Observer is not involved yet.

### Step 2: Determine Relevant Evidence

Based on the question type, identify:

- **Which domains are involved?** (Decision? Risk? Execution? Performance? Architecture?)
- **Which datasets contain the evidence?** (decision_ledger? trade_journal? shadow_trades?)
- **What joins are required?** (symbol? correlation_id? entity_id? cycle_id?)
- **What time range is relevant?** (latest? last week? all history?)

This step is shared: Kiro identifies what's needed, Observer confirms what's available.

### Step 3: Gather Evidence

Retrieve structured data from identified sources:

- Call Observer methods (`obs.explain()`, `obs.trades()`, etc.)
- Read architecture documents where design intent is needed
- Note what was found AND what was expected but missing

This step belongs to the Observer. It returns facts, not interpretations.

### Step 4: Compare and Contextualise

Place evidence in context:

- **Against expectations:** Does observed behaviour match design intent?
- **Against history:** Is this normal or anomalous?
- **Against thresholds:** Are values within acceptable ranges?
- **Against alternatives:** What does the counterfactual show?

This step belongs to the reasoning layer. The Observer provides the raw material; reasoning provides the context.

### Step 5: Explain Findings

Construct the answer:

- State what was found (factual)
- Explain what it means (interpretive)
- Identify what cannot be determined (uncertainty)
- Cite evidence sources (verifiability)

This step belongs to the reasoning layer, using Observer evidence as citations.

### Step 6: Recommend (Only When Asked)

If the question is advisory:

- State the recommendation
- Show supporting evidence
- State confidence level
- State assumptions
- State risks
- State what additional evidence would increase confidence

This step belongs to the reasoning layer. It ONLY activates for recommendation questions, never for factual or explanation questions.

---

## 3. Governing Principles for Future Development

### Principle 1: Evidence Before Reasoning

The Observer must ALWAYS gather evidence before any conclusion is formed. The workflow never jumps from "understand question" to "provide answer" without the evidence step in between.

### Principle 2: Cite Everything

Every claim in an answer must trace back to a specific evidence source. "Win rate is 35.5%" must cite trade_journal. "Swing filter blocked EURUSD" must cite decision_trace.terminal_stage. If it cannot be cited, it cannot be stated as fact.

### Principle 3: Acknowledge Unknowns

If evidence does not exist for part of an answer, say so explicitly. "I cannot determine whether performance has improved because I have no time-range comparison capability" is a better answer than silence or guessing.

### Principle 4: Separate Layers Strictly

| Layer | Responsibility | Never Does |
|:-----:|:--------------|:-----------|
| Observer | Retrieves structured evidence | Reasons, recommends, interprets |
| Kiro | Reasons, explains, recommends | Invents evidence, modifies system |
| Human | Decides, approves, acts | — |

If the Observer starts reasoning, it becomes unreliable (baked-in assumptions that may be wrong). If Kiro starts inventing evidence, it becomes untrustworthy. The separation is the source of trust.

### Principle 5: Never Jump to Conclusions

The investigation workflow exists to prevent premature answers. The most dangerous failure mode is: "Why isn't the bot profitable?" → immediate opinion without checking evidence. The correct behaviour is: check trades, check guards, check decisions, check shadows, THEN synthesise.

### Principle 6: Build Memory, Not Intelligence

Every future Observer enhancement should ask: "Does this make the Observer a better memory?" If yes → build it. If it makes the Observer a reasoning engine → don't build it.

Examples:
- "Add time-range filtering to trades()" → YES (better memory access)
- "Add decision funnel summary()" → YES (better memory access)
- "Add recommendation generator" → NO (that's reasoning, belongs in Kiro)
- "Add natural language understanding" → NO (Kiro already does this)

### Principle 7: Failures Reveal Gaps

When the system cannot answer a question, that failure is diagnostic:
- If evidence exists but Observer can't retrieve it → Observer needs a new accessor method
- If evidence doesn't exist → the trading system needs new persistence
- If evidence exists but reasoning fails → Kiro needs better context (steering file or architecture doc)

Never build a workaround. Fix the actual gap.

---

## 4. What This Means for Future Development

### Do Build (Observer-level improvements)

| Capability | Why |
|:-----------|:----|
| Time-range parameters | Enables comparison questions ("this week vs last week") |
| Decision funnel summary | Enables analysis ("what % pass each stage?") |
| Architecture doc search | Enables navigation ("where is X documented?") |
| Cross-dataset join helpers | Enables deeper explanation chains |

### Do NOT Build (belongs elsewhere)

| Capability | Why Not | Correct Owner |
|:-----------|:--------|:-------------:|
| Question understanding | Kiro already handles natural language | Kiro (LLM) |
| Recommendation generation | Requires judgement beyond evidence | Kiro (LLM) |
| Trend detection | Requires statistical reasoning | Kiro (LLM) |
| "What should I do" answers | Requires domain expertise | Kiro (LLM) |
| Conversation memory | Requires session state | Kiro (built-in) |

### The Litmus Test

Before adding any Observer feature, ask:

> "Is this making the system's memory more accessible, or am I trying to make the Observer think?"

If it's memory access → build it in the Observer.
If it's thinking → it already exists in Kiro.

---

*End of Observer Behavioural Review.*
