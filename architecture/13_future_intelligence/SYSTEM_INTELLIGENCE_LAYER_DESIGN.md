# SYSTEM INTELLIGENCE LAYER — ARCHITECTURE DESIGN

**Status:** Design document. No implementation.
**Purpose:** Define an analytical intelligence layer that observes, evaluates, explains, and advises on the trading system.
**Constraint:** This layer NEVER executes trades, modifies configuration, or bypasses controls.

---

## 1. Purpose

The System Intelligence Layer exists to answer one question continuously:

> "How well does this system understand itself, and what should change next?"

It is a senior systems analyst embedded in the architecture. It understands what the system does, why components exist, how information flows, what capabilities are missing, and what improvements would provide value.

It does not act. It observes, analyses, and recommends. Humans decide whether to implement recommendations.

---

## 2. Mission Alignment

The governing principle (from `SYSTEM_INTELLIGENCE_PRINCIPLES.md`):

> "Every meaningful state transition must be observable, attributable, explainable, and learnable."

The System Intelligence Layer is the component that **evaluates whether this principle is being met** and identifies where it is not.

| Principle | Layer's Role |
|-----------|-------------|
| Observable | Detects unobserved transitions |
| Attributable | Identifies broken identity chains |
| Explainable | Finds decisions that cannot be explained |
| Learnable | Determines whether outcomes are being connected to decisions |

---

## 3. System Boundaries

### What It IS

- A read-only analytical observer
- A structured advisor that produces assessments
- A capability evaluator that scores subsystem maturity
- A feature impact analyser that predicts integration complexity
- A research support system that identifies answerable vs unanswerable questions

### What It Is NOT

- Not a trading engine (cannot produce EXECUTE decisions)
- Not a risk controller (cannot veto or block trades)
- Not a configuration manager (cannot modify `config.py`)
- Not an auto-deployer (cannot change live behaviour)
- Not a data modifier (read-only access to all datasets)

### Enforcement

The layer has NO write access to:
- `core/config.py`
- `execution/` modules
- `risk/` modules
- `core/runtime/live_scanner.py`
- Any persistence writer

It has READ access to:
- All 24 persisted datasets
- All architecture documentation
- Source code (static analysis)
- Configuration state
- Research outputs
- Event streams (historical)

---

## 4. Architecture Position

```
MISSION ("Build a trading intelligence system that continuously improves")
    │
    ▼
SYSTEM_INTELLIGENCE_PRINCIPLES.md (governing standard)
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│         SYSTEM INTELLIGENCE LAYER (this design)                  │
│                                                                  │
│  Reads: architecture docs, source code, config, datasets         │
│  Produces: assessments, scores, recommendations, gap reports     │
│  Cannot: trade, modify config, bypass controls                   │
└────────────────────────────────────┬────────────────────────────┘
                                     │ Observes & analyses
                                     ▼
┌─────────────────────────────────────────────────────────────────┐
│                    TRADING SYSTEM                                 │
│                                                                  │
│  config.py → main.py → live_scanner → engine → risk → execution │
│       ↓                                                          │
│  24 persistence datasets → research_engine → reports             │
└─────────────────────────────────────────────────────────────────┘
```

The intelligence layer sits ABOVE the trading system in the governance hierarchy but BELOW the human operator in decision authority. It can recommend but never act.

---

## 5. Core Components

### 5.1 System Mapper

**Responsibility:** Build and maintain a structured model of the system's architecture.

**Inputs:**
- Source code (static analysis of imports, classes, functions)
- Architecture documentation (`architecture/` directory)
- Configuration (`core/config.py` — feature flags, limits, toggles)
- Test files (coverage, ownership boundaries)

**Outputs:**
- Component inventory (what exists)
- Dependency graph (what depends on what)
- Ownership map (which module owns which responsibility)
- Data flow graph (how information moves through the system)
- Decision flow graph (how decisions are made and routed)

**Key questions it answers:**
- What components exist?
- What does each component own?
- How are components connected?
- Where are the boundaries?

---

### 5.2 Capability Analyser

**Responsibility:** Evaluate the maturity and completeness of every subsystem.

**Inputs:**
- System Mapper output (component inventory)
- Architecture principles (required capabilities)
- Persistence datasets (what's actually being recorded)
- Field Population Audit (what's populated vs empty)

**Outputs:**
- Per-subsystem capability score (0-100)
- Gap inventory (what's missing per subsystem)
- Maturity assessment (which Level 1-5 each subsystem achieves)

**Subsystems evaluated:**

| Subsystem | Key Questions |
|-----------|--------------|
| Decision System | Can it explain every decision? Are all paths observable? |
| Market Intelligence | Is context sufficient? Are timeframes well-separated? |
| Risk System | Are veto decisions logged? Can rejections be explained? |
| Execution System | Can the decision→broker→outcome chain be traced? |
| Persistence System | Is all required information stored? Are joins reliable? |
| Research System | Can hypotheses be tested? Can improvements be measured? |
| Horizon System | Are all horizons observable? Can activation be assessed? |
| Portfolio System | Can ranking decisions be explained? Is authority validated? |

---

### 5.3 Architecture Auditor

**Responsibility:** Compare intended design against actual implementation.

**Inputs:**
- Architecture documents (intended design)
- Source code (actual implementation)
- Test assertions (enforced contracts)
- Production data (runtime evidence)

**Outputs:**
- Drift report (where implementation diverges from design)
- Debt inventory (where shortcuts were taken)
- Consistency assessment (are documents and code aligned)

**Example findings:**
- "Architecture principle requires all decisions to be explainable. Implementation: EXECUTE decisions are 100% explainable. NO_TRADE decisions are 95% explainable (missing correlation_id on non-executed paths)."
- "REFACTOR_BLUEPRINT describes 15 extraction targets. 15/15 are implemented."
- "HORIZON_EXECUTION_POLICY specifies max 21 positions. Implementation enforces 21 via HorizonExecutionAuthority."

---

### 5.4 Data Health Analyser

**Responsibility:** Continuously assess the quality, completeness, and reliability of persisted data.

**Inputs:**
- All 24 persistence datasets (read-only)
- Schema version contracts
- Field population expectations
- Cross-dataset join paths

**Outputs:**
- Data completeness score per dataset
- Field population rates (% of records with non-null values)
- Join success rates (% of records joinable to upstream/downstream)
- Anomaly detection (unexpected nulls, duplicates, schema violations)
- Freshness assessment (is data flowing? when was last write?)

**Key metrics:**
- Records per day by dataset
- NULL rate per field
- Join coverage (e.g., "98% of trade_journal records have valid correlation_id")
- Schema version distribution
- Partition health (are all expected partitions present)

---

### 5.5 Decision Analyst

**Responsibility:** Evaluate the quality of trading decisions using historical evidence.

**Inputs:**
- decision_ledger (outcomes)
- decision_trace (reasoning)
- shadow_trades (counterfactuals)
- trade_truth (results)
- trade_journal (lifecycle)
- research_contracts (expectations)

**Outputs:**
- Decision quality score (were decisions correct given available information?)
- Rejection analysis (were rejected opportunities actually profitable?)
- Filter impact (which guards add value vs remove edge?)
- Threshold assessment (are scoring thresholds optimal?)
- Regime effectiveness (which conditions produce best decisions?)

**Example assessments:**
- "The scoring threshold of 0.35 rejects 12% of opportunities that would have been profitable. Raising to 0.30 would capture 8% more winners at the cost of 3% more losers."
- "The correlation guard blocked 15 trades this month. 11 of those would have been profitable. Review whether the guard threshold is too aggressive."

---

### 5.6 Feature Impact Analyser

**Responsibility:** When a new feature is proposed, predict its impact on the existing system.

**Inputs:**
- System Mapper output (current architecture)
- Proposed feature specification
- Existing ownership boundaries
- Persistence architecture

**Outputs:**
- Integration complexity assessment
- Affected components list
- New data requirements
- New persistence requirements
- New research questions enabled
- Risk assessment (what could break)
- Architecture alignment score (does this fit existing patterns)

**Template for analysis:**

```
Feature: [name]
Owner subsystem: [which subsystem]
Components affected: [list]
New data created: [dataset/fields]
New questions answerable: [list]
Risks introduced: [list]
Architecture alignment: [HIGH/MEDIUM/LOW]
Recommended approach: [how to implement]
```

---

### 5.7 Recommendation Engine

**Responsibility:** Synthesise findings from all other components into prioritised, actionable recommendations.

**Inputs:**
- All analyser outputs
- Current system scores
- Architecture principles (what's required)
- Historical recommendations (what's already been done)

**Outputs:**
- Prioritised recommendation list (P0-P3)
- Expected impact per recommendation
- Implementation complexity estimate
- Dependencies between recommendations
- "What should we work on next?" answer

**Recommendation format:**

```
Priority: P1
Recommendation: [what to do]
Problem: [what's wrong now]
Impact: [what improves]
Effort: [Low/Medium/High]
Dependencies: [what must exist first]
Subsystem: [owner]
Evidence: [data supporting this recommendation]
```

---

## 6. Data Sources

| Source | Access Pattern | Purpose |
|--------|:-------------:|---------|
| Architecture docs (`architecture/*.md`) | Read file | Understand intended design |
| Source code (`core/`, `risk/`, `execution/`) | Static analysis | Understand actual implementation |
| Configuration (`core/config.py`) | Read | Understand active behaviour flags |
| Persistence (24 datasets, S3/local) | Read JSONL | Evaluate data health and decision quality |
| Research outputs (`research_reports/`) | Read JSON | Understand research findings |
| Test files (`tests/`) | Read | Understand enforced contracts |
| Event stream (events/) | Read | Understand market observations |

---

## 7. Decision Rules

### What It Can Recommend

- Configuration changes (with justification and evidence)
- New persistence fields (with research question they enable)
- Architecture improvements (with integration plan)
- Threshold adjustments (with expected impact)
- Feature priorities (with dependency analysis)
- Research experiments (with hypothesis and data requirements)

### What It Cannot Recommend

- Removing risk controls without evidence of safety
- Enabling unvalidated horizons
- Bypassing the guard chain
- Modifying trade_truth records retroactively
- Any change that reduces observability

### What It Cannot Do

- Execute recommendations automatically
- Modify `core/config.py`
- Write to any persistence dataset
- Send broker orders
- Override human decisions

---

## 8. System Health Scoring Framework

| Dimension | What It Measures | Scoring Method |
|-----------|:----------------:|:--------------:|
| Observability | Can every transition be seen? | % of transitions with persistence |
| Explainability | Can every decision be explained? | % of decisions with reason + evidence |
| Data Completeness | Are all required fields populated? | % of fields non-null when expected |
| Reliability | Does the system behave consistently? | Variance in decision quality over time |
| Maintainability | Can the system be safely modified? | Coupling score + test coverage |
| Research Capability | Can hypotheses be tested? | % of research questions answerable |
| Improvement Capability | Can the system learn from history? | Whether outcomes connect to decisions |

Each dimension produces: current score, trend (improving/stable/degrading), weakest area, and top recommendation.

---

## 9. Future Evolution

### Phase 1: Observer

The intelligence layer reads architecture documents and source code to build a system model. It can answer "what exists?" and "what is each component's purpose?"

### Phase 2: Analyst

The layer reads persistence datasets and production data to evaluate system health. It can answer "how well is the system performing?" and "where are the gaps?"

### Phase 3: Advisor

The layer synthesises analysis into recommendations. It can answer "what should we work on next?" and "what impact would this change have?"

### Phase 4: Controlled Improvement Assistant

The layer proposes specific, validated changes with evidence. A human reviews and approves. The layer can prepare implementation plans, predict impacts, and validate results after deployment. It still cannot act autonomously — but it reduces the human effort required to improve the system.

```
Phase 1: "The system has 24 datasets."
Phase 2: "Dataset #3 has 5% empty correlation_ids."
Phase 3: "Generate correlation_id on all paths. Impact: +3% join coverage. Effort: Medium."
Phase 4: "Here is the implementation. Deploy? [Y/N]. Post-deploy: correlation coverage improved to 99.8%."
```

---

## 10. Implementation Approach

### Where It Lives

```
system_intelligence/
├── __init__.py
├── mapper/                  # System Mapper (architecture model)
│   ├── component_scanner.py
│   ├── dependency_graph.py
│   └── ownership_resolver.py
├── analyser/                # Capability + Data + Decision analysis
│   ├── capability_scorer.py
│   ├── data_health.py
│   ├── decision_quality.py
│   └── architecture_drift.py
├── advisor/                 # Recommendation engine
│   ├── recommendation_engine.py
│   ├── feature_impact.py
│   └── priority_ranker.py
├── reports/                 # Output generation
│   ├── health_report.py
│   ├── capability_report.py
│   └── recommendation_report.py
└── models/                  # Data models
    ├── system_model.py
    ├── capability.py
    └── recommendation.py
```

### Isolation Guarantees

- No imports from `execution/`, `risk/`, or `core/runtime/`
- Read-only access to `logs/` and `research_reports/`
- Cannot instantiate `TradeStateManager`, `ExecutionOrchestrator`, or `RiskManager`
- Cannot call `mt5.*` functions
- Test enforcement: `test_intelligence_isolation.py` verifies no write-path imports

### Integration Points (Read-Only)

| Integration | Method | Purpose |
|-------------|--------|---------|
| Architecture docs | File read | Build system model |
| Source code | AST parsing / grep | Dependency analysis |
| Config state | Import `core.config` (read only) | Feature flag awareness |
| Persistence datasets | `research_engine/data_access/loaders.py` | Data health analysis |
| Research reports | File read from `research_reports/` | Research awareness |

---

## 11. Success Criteria

The System Intelligence Layer is successful when:

1. A human can ask "what should we improve next?" and receive an evidence-based, prioritised answer.
2. New feature proposals can be evaluated for architecture alignment before implementation begins.
3. Data quality issues are detected before they affect research conclusions.
4. Architecture drift is identified before it creates technical debt.
5. The system can explain its own capabilities and limitations to a new team member.

---

## 12. Relationship to Existing Components

| Existing Component | Intelligence Layer Interaction |
|:------------------:|:----------------------------:|
| `SYSTEM_INTELLIGENCE_PRINCIPLES.md` | Evaluates compliance with principles |
| `PRODUCTION_INTELLIGENCE_READINESS_REVIEW.md` | Automates what this review did manually |
| `FIELD_POPULATION_AUDIT.md` | Continuously monitors what this audit measured once |
| `TOP_LEVEL_SYSTEM_AUTHORITY_AUDIT.md` | Maintains awareness of the authority hierarchy |
| `research_engine/` | Consumes research outputs; does not replace research |
| `core/horizon/research_contract.py` | Reads contracts to assess horizon readiness |
| `core/horizon/shadow_evaluation.py` | Reads shadow results to inform activation recommendations |

The intelligence layer does not replace any existing component. It reads their outputs and produces meta-analysis that no individual component can produce alone.

---

*End of System Intelligence Layer Design.*
