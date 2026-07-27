# Research Engine — Architecture

**Version:** 1.0  
**Status:** Design (pre-implementation)  
**Basis:** Repository Audit (19 data assets), Question Bank (19 research questions)  
**Date:** 2026-07-19

---

## 1. Purpose

### Why the Research Engine Exists

The trading system generates rich, structured data at every decision cycle — scoring components, regime classifications, confidence estimates, shadow trade outcomes, execution results, and causal relationships. This data is currently persisted but largely unconsumed.

The Research Engine exists to transform historical decision data into validated knowledge about:
- What the system does well
- What is degrading
- Where edge exists and where it doesn't
- Whether the system's beliefs are calibrated
- How to improve without guessing

### What Problem It Solves

Without a Research Engine, strategy improvements rely on:
- Manual inspection of logs
- Intuition-driven parameter changes
- Undocumented experiments that cannot be reproduced

The Research Engine replaces this with:
- Systematic investigation driven by questions
- Reproducible experiments with recorded parameters
- Evidence-based findings with statistical support
- Historical memory that prevents repeating failed ideas

### Separation Principle

> **Execution produces decisions. Research produces knowledge.**

The Execution Engine answers: "Should I trade now?"  
The Research Engine answers: "Is the way I trade working?"

These must be separate systems because:
- Research requires hindsight; execution requires foresight
- Research can tolerate latency; execution cannot
- Research must be free to explore bad ideas safely
- Execution must never be disrupted by analytical workloads

---

## 2. System Boundary

### The Research Engine DOES:

- Read historical data from existing persistence layers (JSONL, S3)
- Join correlated records across layers (via correlation_id)
- Execute analytical experiments against historical data
- Evaluate hypotheses about strategy behaviour
- Measure system performance across multiple dimensions
- Generate structured research reports
- Store validated findings in a knowledge base
- Track which questions have been investigated and what was learned

### The Research Engine DOES NOT:

- Place trades or modify orders
- Modify live engine parameters during execution
- Bypass risk controls or guard chains
- Automatically deploy strategy changes to production
- Read or write to the live decision ledger during trading
- Import or depend on the live runtime orchestrator
- Require the trading engine to be running

---

## 3. High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     TRADING ENGINE (live)                         │
│  Market Data → Engine A → Risk → Execution → Trade Management   │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼ (writes, one-way)
┌─────────────────────────────────────────────────────────────────┐
│                    PERSISTENCE LAYER                              │
│                                                                   │
│  events/          decision_trace/       decision_ledger/         │
│  execution_context/  shadow_trades/     trade_truth/             │
│  trade_truth_graph/  execution_results/ opportunity_assessment/  │
│  learning/           trade_journal/                              │
│                                                                   │
│  Storage: Local JSONL (primary) + S3 mirror (secondary)         │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼ (reads, one-way)
┌─────────────────────────────────────────────────────────────────┐
│                    RESEARCH ENGINE                                │
│                                                                   │
│  ┌──────────────┐  ┌──────────────────┐  ┌──────────────────┐  │
│  │ Data Access  │  │ Correlation      │  │ Question         │  │
│  │ Layer        │→ │ Engine           │← │ Registry         │  │
│  └──────────────┘  └────────┬─────────┘  └──────────────────┘  │
│                              │                                    │
│                              ▼                                    │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │              EXPERIMENT RUNNER                             │   │
│  │  Hypotheses → Datasets → Analysis → Statistical Tests    │   │
│  └────────────────────────────┬─────────────────────────────┘   │
│                               │                                   │
│              ┌────────────────┼────────────────┐                 │
│              ▼                ▼                 ▼                 │
│  ┌──────────────┐  ┌──────────────────┐  ┌──────────────────┐  │
│  │ Metrics      │  │ Report           │  │ Knowledge        │  │
│  │ Engine       │  │ Generator        │  │ Base             │  │
│  └──────────────┘  └──────────────────┘  └──────────────────┘  │
│                                                                   │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼ (outputs)
┌─────────────────────────────────────────────────────────────────┐
│                    RESEARCH OUTPUTS                               │
│                                                                   │
│  Findings → Evidence Reports → Recommendations                  │
│       ↓                                                          │
│  Human Review → Validated Improvements → Config Change           │
└─────────────────────────────────────────────────────────────────┘
```

---

## 4. Core Components

### 4.1 Data Access Layer

**Responsibility:** Provide normalised read-only access to all persistence layers.

| Capability | Description |
|-----------|-------------|
| Read local JSONL | Parse `logs/` directory trees |
| Read S3 data | Fetch from `s3://trading-bot-data-mk1/` partitions |
| Query DuckDB | Analytical queries on loaded datasets |
| Schema normalisation | Consistent field naming across sources |
| Date range filtering | Load only relevant time windows |
| Symbol filtering | Load per-symbol or cross-symbol |

**Interfaces with:** All persistence directories (read-only)  
**Never writes to:** Any persistence layer consumed by the trading engine

### 4.2 Correlation Engine

**Responsibility:** Join related records across persistence layers using correlation_id.

| Join Path | Source A → Source B |
|-----------|-------------------|
| Decision → Context | `decision_ledger.correlation_id` = `execution_context.correlation_id` |
| Decision → Shadow | `decision_trace.entity_id` ≈ `shadow_trades.trade_id` |
| Decision → Outcome | `decision_ledger.correlation_id` = `trade_truth.correlation_id` |
| Shadow → Truth | `shadow_trades.correlation_id` = `trade_truth.correlation_id` |
| Context → Events | `execution_context.events_ref.last_candle_ts` → `events/` time range |
| Assessment → Decision | `opportunity_assessment.entity_id` = `decision_trace.entity_id` |
| Graph → All | `trade_truth_graph.refs.*` → all layers |

**Output:** Enriched research records with full decision-to-outcome chains.

### 4.3 Question Registry

**Responsibility:** Maintain the set of research questions, their priority, status, and required datasets.

| Field | Description |
|-------|-------------|
| `question_id` | Stable identifier (Q1, Q2, ...) |
| `priority` | P0–P3 |
| `status` | PENDING / IN_PROGRESS / ANSWERED / INVALIDATED |
| `datasets_required` | List of persistence layers needed |
| `last_investigated` | Timestamp of most recent experiment |
| `findings` | Reference to knowledge base entries |
| `schedule` | How often to re-investigate (daily/weekly/monthly) |

**Source of truth:** `architecture/RESEARCH_ENGINE_QUESTION_BANK.md` (initial), evolving over time.

### 4.4 Experiment Runner

**Responsibility:** Execute research jobs against datasets to answer questions.

| Capability | Description |
|-----------|-------------|
| Parameter comparison | Compare outcomes under different parameter values |
| Regime analysis | Segment performance by market regime |
| Feature contribution | Measure which inputs predict which outputs |
| Gate analysis | Evaluate guard efficacy (what was saved vs missed) |
| Rolling window | Compute time-windowed statistics |
| Statistical testing | Significance tests, confidence intervals, calibration curves |

**Contract:**
```
Input:  Question + Dataset + Parameters
Output: ExperimentResult {
    question_id, timestamp, dataset_description,
    parameters, findings, statistical_significance,
    supporting_evidence, limitations
}
```

### 4.5 Metrics Engine

**Responsibility:** Calculate standardised trading performance metrics.

| Metric | Formula | Source |
|--------|---------|--------|
| Expected Value (EV) | (win_rate × avg_win_R) - (loss_rate × avg_loss_R) | shadow_trades / trade_truth |
| Win Rate | wins / total_trades | shadow_trades / trade_truth |
| Average R (win) | mean(R where R > 0) | shadow_trades |
| Average R (loss) | mean(R where R ≤ 0) | shadow_trades |
| Profit Factor | gross_profit / gross_loss | trade_truth |
| Sharpe Ratio | mean(returns) / std(returns) | trade_truth |
| Max Drawdown | peak-to-trough equity decline | trade_journal |
| Calibration Score | |predicted_confidence - actual_win_rate| | decision_ledger + outcomes |
| Pattern Health | rolling win rate per pattern | shadow_trades |

### 4.6 Report Generator

**Responsibility:** Produce human-readable research outputs.

| Report Type | Content | Trigger |
|-------------|---------|---------|
| Experiment Report | Question, method, data, findings, recommendations | Per experiment |
| Edge Report | Current EV, trend, per-symbol/pattern breakdown | Weekly |
| Calibration Report | Confidence vs reality, overconfidence zones | Weekly |
| Degradation Alert | Patterns or regimes showing declining performance | On detection |
| Guard Efficacy | Which guards are saving money vs costing money | Monthly |

### 4.7 Knowledge Base

**Responsibility:** Persistent store of validated research findings.

| Record Type | Content |
|-------------|---------|
| Experiment | Full experiment record (question, data, params, result) |
| Finding | Validated conclusion with supporting evidence |
| Rejection | Hypothesis tested and rejected (prevents re-investigation) |
| Recommendation | Actionable suggestion with expected impact |

**Storage:** Local JSONL (`logs/research/`) initially. S3 mirror optional.

---

## 5. Data Flow — Complete Lifecycle

```
1. MARKET EVENT
   MT5 terminal delivers tick/bar data
       │
2. TRADING DECISION
   Engine A evaluates → EXECUTE or NO_TRADE
       │
3. PERSISTENCE (immediate, fire-and-forget)
   ├── decision_trace          (full reasoning breakdown)
   ├── decision_ledger         (outcome + metadata)
   ├── execution_context       (environment snapshot)
   ├── shadow_trades           (simulated lifecycle)
   ├── execution_results       (broker interaction)
   ├── trade_truth             (actual outcome)
   ├── opportunity_assessment  (pre-decision analysis)
   └── events/                 (raw observations)
       │
4. RESEARCH DATASET (constructed by Data Access Layer)
   Correlated records: decision + context + assessment + outcome
       │
5. QUESTION (from Question Registry)
   "Which scoring components predict actual R-multiples?"
       │
6. EXPERIMENT (run by Experiment Runner)
   Load dataset → segment by component → correlate with R → test significance
       │
7. EVIDENCE (produced by Metrics Engine)
   "base_score correlates at r=0.42 (p<0.01); regime_bonus at r=0.08 (n.s.)"
       │
8. FINDING (stored in Knowledge Base)
   "base_score is the dominant R predictor; regime_bonus adds no value"
       │
9. HUMAN REVIEW
   Operator reviews finding, assesses confidence, considers trade-offs
       │
10. POSSIBLE STRATEGY IMPROVEMENT
    Config change: increase base_score weight, reduce regime_bonus weight
```

---

## 6. Research Engine Priority Roadmap

### Phase 1 — Validation (Must complete before trusting any research)

| Question | Goal | Success Criteria |
|----------|------|-----------------|
| Q16: Shadow↔Live accuracy | Validate shadow trades as outcome proxy | Correlation > 0.7 between shadow R and live R |
| Q19: True system EV | Confirm system has positive expected value | EV > 0 with p < 0.05 over 50+ trades |
| Q4: Confidence calibration | Confirm engine confidence is meaningful | ECE < 0.15 across decile buckets |

**Deliverable:** "Research outputs are trustworthy" OR "Shadow model needs calibration before proceeding"

### Phase 2 — Understanding (What does the system actually do?)

| Question | Goal |
|----------|------|
| Q1: Component→R correlation | Identify which inputs matter most |
| Q5: Pattern degradation | Detect dying strategies early |
| Q3: Missed opportunity cost | Quantify over-filtering |
| Q6: Regime classifier accuracy | Validate market model |

**Deliverable:** Complete map of "what works, what doesn't, and what's changing"

### Phase 3 — Optimisation (Evidence-based improvement)

| Question | Goal |
|----------|------|
| Q2: Regime-adaptive threshold | Single-parameter improvement |
| Q7: Session edge | Time-based opportunity |
| Q8: HTF alignment value | Multi-timeframe validation |
| Q13: Optimal trade duration | Trade management improvement |

**Deliverable:** Specific, testable parameter recommendations backed by evidence

---

## 7. Relationship With Existing Systems

| System | Relationship | Direction | Notes |
|--------|-------------|-----------|-------|
| **Execution Engine** (live_scanner) | Read-only consumer of its outputs | Engine → Persistence → Research | Research never imports live_scanner |
| **Shadow Trade System** | Primary outcome proxy for research | Shadow → Research (read) | Shadow R-multiples are the main "label" for research |
| **Trade Journal** | Ground truth for live outcomes | Journal → Research (read) | Actual P&L for validation |
| **Event Stream** | Raw market observations | Events → Research (read) | CANDLE + FEATURE_UPDATE for context |
| **S3 / Athena** | Long-term queryable storage | S3 → Research (read) | Hive-partitioned JSONL via Athena SQL |
| **DuckDB** | Local analytical engine | Research uses DuckDB internally | Not yet implemented; natural fit |
| **Learning Engine** | Calibration ground truth | Learning → Research (read) | calibration_result provides learning signal |
| **Decision Trace** | Richest per-decision feature set | Trace → Research (primary dataset) | 30+ fields per evaluation |

---

## 8. AWS Evolution Path

### Current State (local-first)

```
Local Research Engine
    ↓ reads
Local JSONL files (logs/)
    ↓ optional DuckDB
Local analytical queries
    ↓ produces
Local research reports (logs/research/)
```

### Near-Term Evolution (S3-backed)

```
Local Research Engine
    ↓ reads
S3 Data Lake (s3://trading-bot-data-mk1/)
    ↓ DuckDB (parquet scans) or Athena SQL
Cross-day analytical queries
    ↓ produces
Research reports → Discord summary notifications
```

### Future State (automated, cloud-native)

```
AWS Lambda / Step Functions
    ↓ scheduled
Athena queries over S3 partitions
    ↓ results
Research findings stored in S3 knowledge base
    ↓ notifications
Discord research alerts
    ↓ human review
Config PRs with evidence attached
```

**Current recommendation:** Start with local-first + DuckDB. The S3 data already exists and is Athena-compatible. Move to cloud when local processing becomes insufficient.

---

## 9. Design Principles

### 1. Separation of Concerns

Research does not control execution. The Research Engine has no import path to `live_scanner.py` or any runtime module. It reads persistence layers only.

### 2. Evidence Before Change

No strategy modification without:
- A defined hypothesis
- A measured experiment
- Statistical significance (or explicit acknowledgment of insufficient data)
- Human review of findings

### 3. Reproducibility

Every experiment must record:
- Dataset description (date range, symbols, filters)
- Parameters used
- Timestamp of execution
- Raw numerical results
- Statistical test results
- Code version (or method reference)

### 4. Historical Memory

- Past experiments are never deleted
- Rejected hypotheses are stored with reasons
- Findings that led to changes are linked to the change
- Failed strategies are remembered to prevent re-investigation

### 5. Explainability

Every finding must show:
- What question it answers
- What data it used
- What method it applied
- What the numbers say
- What confidence level applies
- What limitations exist

### 6. Non-Interference

- Research jobs never run during peak trading hours (or are resource-limited)
- Research never modifies files read by the trading engine
- Research failures never affect trading availability
- Research can be completely disabled without any production impact

---

## 10. Open Questions

| # | Question | Options | Impact |
|---|----------|---------|--------|
| 1 | How frequently should research jobs run? | Nightly / weekly / on-demand | Affects freshness of findings |
| 2 | Should reports require human approval before acting? | Always / only for P0 changes / never | Safety vs speed trade-off |
| 3 | How should experiments be versioned? | Git tags / embedded version / content hash | Reproducibility mechanism |
| 4 | Should knowledge storage remain local or move to S3? | Local-first / S3-first / hybrid | Durability vs simplicity |
| 5 | When (if ever) should automated optimisation be allowed? | Never / after N validated cycles / for P3 questions only | Automation risk vs speed |
| 6 | Should the Research Engine produce Discord alerts? | Only degradation alerts / all findings / none | Operator awareness |
| 7 | How to handle insufficient data for statistical significance? | Report with caveats / defer until sufficient / Bayesian approach | Early-stage research quality |
| 8 | Should DuckDB be embedded or use exported parquet? | Embedded (read JSONL directly) / ETL to parquet first | Performance vs simplicity |

---

## Summary

The Research Engine is a read-only analytical system that transforms the trading bot's existing structured data into validated knowledge. It does not trade. It does not modify the live system. It investigates questions, runs experiments, measures performance, and produces evidence-based recommendations for human review.

All 19 identified data assets are immediately consumable. The correlation_id spine enables cross-layer joins without new instrumentation. Implementation can begin at Phase 1 (validation) using only local JSONL files and standard Python analytics libraries.
