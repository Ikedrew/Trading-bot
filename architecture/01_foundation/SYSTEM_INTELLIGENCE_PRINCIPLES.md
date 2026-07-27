# SYSTEM INTELLIGENCE PRINCIPLES

**Status:** Governing architecture standard. Permanent.
**Audience:** Principal Engineers, Data Architects, Trading System Engineers, Research Engineers.
**Purpose:** Define the standard against which all persistence, research, and production readiness decisions are evaluated.

---

## 1. Self-Understanding Before Self-Improvement

> **A system cannot safely improve what it cannot accurately understand.**

Before adding intelligence, automation, optimisation, or new capabilities, the system must maintain an accurate model of its own architecture, behaviour, data, decision processes, limitations, dependencies, and operational state.

The system must understand itself before attempting to improve itself.

This is the meta-principle that governs how all subsequent principles are applied.

### The Development Order

Every capability passes through this sequence. Steps cannot be skipped.

```
1. Understand  →  2. Observe  →  3. Explain  →  4. Measure  →  5. Improve
```

Not:

```
1. Add feature  →  2. Hope it helps  →  3. Discover consequences later
```

### Four Prerequisite Questions

Every future capability must pass through these questions in order. A capability that cannot satisfy an earlier question must not proceed to a later one.

#### Question 1: Does the system understand this?

Can the system identify:
- What the component does
- Where it belongs in the architecture
- What authority owns it
- What dependencies it has
- How it affects existing behaviour

If the system cannot place a capability within its own architecture model, it cannot safely integrate it.

#### Question 2: Can the system observe this?

Can the system capture:
- Required events and state transitions
- Decisions made by this component
- Outcomes produced
- Failures encountered
- Changes over time

If the system cannot observe a capability's effects, it cannot determine whether it is working.

#### Question 3: Can the system explain this?

Can the system answer:
- Why did this happen?
- What information was available at the time?
- What rules influenced the outcome?
- What alternatives existed?

If the system cannot explain a capability's behaviour, it cannot investigate problems or attribute results.

#### Question 4: Can the system measure this?

Can the system evaluate:
- Whether the capability works as intended
- Whether it improves outcomes
- Whether it introduces problems
- Whether it creates unintended consequences

If the system cannot measure a capability's impact, it has no evidence basis for keeping, modifying, or removing it.

#### Only then: Can the system improve this?

Improvement requires all four predecessors:
- Understanding (where it fits)
- Observation (what it does at runtime)
- Explanation (why it behaves as it does)
- Measurement (whether it helps or harms)

Improvement without these foundations is guessing.

### Feature Review Checklist

Every new feature, subsystem, or capability is reviewed against:

| Dimension | Question |
|-----------|----------|
| Understanding | Does the system know why this exists? Is ownership clear? Is responsibility defined? |
| Observation | What new information does this create? Is the state captured? Is the lifecycle visible? |
| Explanation | Can the system explain the effect of this feature? Can humans understand its impact? |
| Measurement | How will success or failure be evaluated? What metrics change? |
| Improvement | What evidence justifies adding this capability? What hypothesis does it test? |

### Architectural Implication

This principle creates a natural development order:

1. **Understand** the current system (audits, architecture documents, authority maps)
2. **Observe** all transitions (persistence, events, evidence capture)
3. **Explain** all decisions (traces, audits, reasoning, counterfactuals)
4. **Measure** all outcomes (research contracts, observations, reports)
5. **Improve** based on evidence (recommendations, controlled changes, validation)

Components at each level support the levels above them. Persistence supports observation. Observation supports explanation. Explanation supports measurement. Measurement supports improvement.

### Relationship to System Intelligence Layer

The System Intelligence Layer exists to operationalise this principle. Its first responsibility is not optimisation — it is building an accurate understanding of the system itself.

The progression mirrors this principle exactly:

| Phase | Role | Principle Alignment |
|:-----:|:----:|:-------------------:|
| 1. Observer | Builds system model | Understanding |
| 2. Analyst | Evaluates health from data | Observation + Explanation |
| 3. Advisor | Recommends changes with evidence | Measurement |
| 4. Improvement Assistant | Proposes validated changes | Improvement |

No phase may be entered until the previous phase is operating correctly.

---

## 2. Core Principle

> **Every meaningful state transition in the trading system must be observable, attributable, explainable, and learnable.**

This is the single technical requirement from which all architecture decisions follow.

### Observable

The system records what happened. The information available at the moment of decision is preserved as an immutable snapshot. Important transitions leave persistent evidence that survives process restart, VM failure, and time.

A transition that cannot be observed did not happen from the perspective of research, debugging, and improvement.

### Attributable

Every important event carries stable identity. Relationships between stages can be reconstructed through join keys. Data lineage is preserved from origin through transformation to final persistence. A record without identity cannot participate in the knowledge graph.

### Explainable

The system can explain why it progressed. The system can explain why it stopped. Decisions carry reasons, evidence references, and the threshold conditions that determined the outcome. Explanation is not optional metadata — it is a structural requirement of persistence.

### Learnable

Outcomes can be compared against the decisions that produced them. Historical behaviour informs future behaviour. Changes to the system are driven by evidence, not assumption. A system that cannot learn from its own history cannot survive market evolution.

---

## 3. System Philosophy

The objective is not: "Build a bot that trades."

The objective is: **"Build a trading intelligence system that can continuously understand, evaluate, and improve its own behaviour."**

### Why This Matters

Markets change. Statistical edges degrade. Regime characteristics evolve. Volatility structures shift. Liquidity patterns are non-stationary. A system optimised for today's conditions will underperform under tomorrow's conditions unless it can detect the degradation and adapt.

A system that only executes cannot detect when its assumptions become invalid. A system that only records cannot determine which records matter. A system that only explains cannot act on explanations.

The complete requirement is: observe, attribute, explain, learn, and improve — continuously, autonomously, with evidence.

---

## 4. Decision Lifecycle Principle

Every opportunity passes through a deterministic lifecycle:

```
Market State
      │
      ▼
Opportunity Detection     "The market presented something potentially interesting."
      │
      ▼
Assessment                "This is how interesting it is, and why."
      │
      ▼
Decision                  "This is what we will do, and why."
      │
      ├──────────────────────────────┐
      ▼                              ▼
  EXECUTE                    NO_TRADE / REJECTED
      │                              │
      ▼                              ▼
  Execution                    Shadow / Counterfactual
      │                              │
      ▼                              ▼
  Outcome                      "What would have happened?"
      │
      ▼
  Learning                   "What should change?"
```

### Information Requirements Per Transition

| Transition | Required Information |
|:----------:|:------------------:|
| Market → Opportunity | Market regime, structure, session, volatility. What the bot saw. |
| Opportunity → Assessment | Pattern, score components, evidence weights, confidence, probability. |
| Assessment → Decision | Threshold comparison, strategy selection, risk evaluation, guard results. |
| Decision → Execution | Correlation identity, execution context (bid/ask/spread/latency), order intent. |
| Execution → Outcome | Broker fill price, execution status, protection verification, actual SL/TP. |
| Outcome → Learning | R-multiple, MFE, MAE, duration, exit reason, causal attribution. |

If any transition lacks its required information, the system cannot explain what happened at that stage.

---

## 5. Decision Observability Standard

Every meaningful decision — whether EXECUTE, NO_TRADE, or BLOCKED — must answer these seven questions:

1. **What did the system see?** (Market state, regime, structure, pattern)
2. **What information was available?** (Features, indicators, HTF context)
3. **What rules were active?** (Strategy, thresholds, guards, configuration)
4. **What evidence influenced the decision?** (Score components, probabilities, risk assessment)
5. **What alternatives existed?** (Other symbols, other horizons, other strategies)
6. **Why did the system choose this path?** (Terminal stage, reason code, threshold comparison)
7. **What happened afterwards?** (Outcome for executed; counterfactual for rejected)

A decision that cannot answer all seven questions is incompletely persisted.

---

## 6. The NO_TRADE Principle

A rejected decision is still a decision. The absence of action requires the same level of explanation as the presence of action.

### What Must Be Preserved for NO_TRADE

- Opportunities considered (detected patterns)
- Assessment scores (how close was it?)
- Rejection reason (which specific condition failed)
- Terminal stage (where in the pipeline did it stop?)
- Guard that blocked (if risk-blocked, which guard?)
- Threshold gap (how far from qualification?)
- Counterfactual outcome (what would have happened if executed?)

### Why This Matters for Research

The most valuable research questions are:

- "What profitable trades did we reject?"
- "Which filters reduce expectancy rather than improve it?"
- "Are our thresholds optimal or are they removing edge?"

These questions are unanswerable without persisted NO_TRADE evidence.

---

## 7. Identity and Traceability Principle

### Identifier Hierarchy

| Identifier | Scope | Purpose |
|:----------:|:-----:|:-------:|
| `entity_id` | Per-symbol per-cycle | Links all records from one engine evaluation |
| `opportunity_id` | Per-opportunity | Links detection → assessment → decision |
| `decision_id` | Per-decision-audit | Unique per decision evaluation |
| `correlation_id` | Per-execution-chain | Links decision → execution → outcome (EXECUTE only) |
| `trade_id` | Per-position | Links open → management → close → journal → truth |
| `cycle_id` | Per-scanner-cycle | Groups all activity within one scan iteration |

### Traceability Requirement

A future investigator must be able to follow any trade or decision forward and backward through the entire chain using stable identifiers:

```
Market Context (cycle_id)
      ↓
Opportunity (opportunity_id)
      ↓
Assessment (assessment_id / entity_id)
      ↓
Decision (decision_id / entity_id)
      ↓
Execution (correlation_id)
      ↓
Trade (trade_id / correlation_id)
      ↓
Outcome (correlation_id → trade_truth)
      ↓
Learning (trade_id → edge_attribution)
```

If any link in this chain is broken — if a record exists without a joinable identifier — the system has a traceability failure.

---

## 8. Evidence Principle

Important decisions require preserved evidence.

### EXECUTE Evidence Requirements

| Evidence | Source | Purpose |
|----------|--------|---------|
| Market regime | H4 classifier | Explains environmental context |
| Pattern detection | Signal orchestrator | Explains trigger |
| Score breakdown | 10-component scorer | Explains confidence |
| Strategy selection | Strategy classifier | Explains approach |
| Risk calculation | Risk manager | Explains position size and levels |
| Execution context | Bid/ask/spread/latency | Explains fill environment |
| HTF alignment | Multi-timeframe | Explains structural support |
| Confirmation quality | Candle analysis | Explains entry precision |

### NO_TRADE Evidence Requirements

| Evidence | Source | Purpose |
|----------|--------|---------|
| Rejection reason | Pipeline exit | Explains why stopped |
| Failed requirement | Guard/threshold | Explains what was missing |
| Score at rejection | Scorer output | Explains how close |
| Closest flip component | Diagnostic | Explains what would change the decision |
| Shadow outcome | Shadow engine | Explains counterfactual |

---

## 9. Research Principle

Every persisted dataset exists to answer future questions. Data that cannot answer questions has no research value. Data that answers questions nobody will ask has negligible value.

### Questions the System Must Support

**Decision quality:**
- Why did this trade happen?
- Why did this trade not happen?
- Was the decision correct given the information available?
- Would a different threshold have produced better results?

**Execution quality:**
- Did the broker fill match expectations?
- Are protection levels being honoured?
- Is execution timing affecting outcomes?

**Strategy quality:**
- Which patterns produce positive expectancy?
- Which regimes create edge?
- Which strategies are degrading?
- Which horizons are ready for activation?

**System quality:**
- Are decisions consistent over time?
- Is behaviour changing unexpectedly?
- Are certain market conditions causing failures?

If the data cannot answer the question, either the data is missing or the question is unanswerable from historical records.

---

## 10. Production Readiness Standard

A component is not complete unless it satisfies all five conditions:

| # | Condition | Test |
|:-:|:----------|:----:|
| 1 | It can explain its important actions | Can a human understand what it did and why? |
| 2 | It preserves evidence | Is the information at decision time recoverable? |
| 3 | It maintains relationships | Can its records be joined to upstream and downstream? |
| 4 | It supports investigation | Can a failure be traced to root cause? |
| 5 | It contributes to learning | Can outcomes be compared against decisions? |

A feature that passes functional testing but fails any of these conditions is architecturally incomplete.

---

## 11. Architecture Review Framework

Before adding any feature, component, or dataset, evaluate against this checklist:

| # | Question | Required Answer |
|:-:|:---------|:---------------:|
| 1 | Does this create a meaningful decision or state transition? | If yes → requires persistence |
| 2 | Can we identify it with a stable key? | If no → add identifier before building |
| 3 | Can we join it to the existing knowledge graph? | If no → define join keys |
| 4 | Can we explain its effect on outcomes? | If no → add evidence fields |
| 5 | Can we measure whether it helped? | If no → ensure counterfactual path |
| 6 | Can future research evaluate it? | If no → persist research-friendly representation |
| 7 | Does it degrade gracefully? | If no → add fallback/default behaviour |
| 8 | Can it fail without blocking execution? | If no → isolate with fire-and-forget pattern |

Features that cannot answer "yes" to questions 1-6 should not be built until the architecture supports them.

---

## 12. System Intelligence Maturity Model

| Level | Capability | Description | System State |
|:-----:|:----------:|:------------|:------------:|
| 1 | **Acts** | System executes trades based on rules | Baseline |
| 2 | **Records** | System persists decisions, outcomes, and context | Observable |
| 3 | **Explains** | System can articulate why it acted or stopped | Attributable |
| 4 | **Learns** | System compares outcomes to decisions and identifies improvements | Learnable |
| 5 | **Improves** | System modifies its own behaviour based on accumulated evidence | Autonomous |

### Level Definitions

**Level 1 — Acts:** The system can receive market data and produce trade orders. No persistence. No observability. Cannot explain or improve.

**Level 2 — Records:** The system persists every decision, execution, and outcome. Historical data exists but is not actively analysed. Investigations are manual.

**Level 3 — Explains:** The system preserves reasons, evidence, thresholds, and counterfactuals. A human can reconstruct any decision with full context. Research questions are answerable from the data.

**Level 4 — Learns:** The system compares expectations against reality. Research contracts define hypotheses. Observations validate or invalidate them. Degradation is detected. Improvements are identified (but not automatically applied).

**Level 5 — Improves:** The system modifies its own parameters, thresholds, strategy weights, and horizon activation based on accumulated evidence. Changes are evidence-driven, versioned, and reversible. The system evolves without manual intervention.

### Current System State

The system currently operates at **Level 4** (Learns). It:
- Records all 24 datasets (Level 2)
- Explains every decision with evidence chains (Level 3)
- Compares research contracts against observations and produces activation readiness assessments (Level 4)

Level 5 (autonomous improvement) requires: automatic parameter tuning from edge_optimisation outputs, automatic horizon activation from shadow evaluation readiness, and automatic strategy weight adjustment from learning insights. These capabilities exist as research infrastructure but are not yet connected to live execution parameters.

---

## 13. Governance

This document is the permanent standard. All future architecture reviews, production readiness assessments, and persistence audits reference these principles.

Changes to this document require:
- Evidence that the principle is incorrect or insufficient
- Demonstration that the change does not reduce observability, traceability, or learnability
- Review by the system architect

No feature, dataset, or component is exempt from these requirements.

---

*End of System Intelligence Principles.*
