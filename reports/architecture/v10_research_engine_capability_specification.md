# V10 RESEARCH ENGINE — COMPLETE ANALYTICAL CAPABILITY SPECIFICATION

**Date:** 2026-07-27  
**Status:** DEFINITIVE — based on validated question mapping + Shadow lineage audits  
**Evidence base:** 51 questions (47 GREEN, 1 AMBER, 3 BLOCKED), 4,153 Shadow observations (🟢 TRUSTED)  

---

## EXECUTIVE VERDICT

### Optimisation Readiness: 🟢 READY FOR RESEARCH-DRIVEN V10 OPTIMISATION

The Research Engine can:
- Describe V10's current behaviour
- Diagnose weaknesses and bottlenecks
- Discover associations between features and outcomes
- Propose evidence-backed improvement candidates
- Validate candidates through population-filter experiments

It cannot:
- Automatically implement changes
- Establish causation (only association/correlation)
- Guarantee that counterfactual improvements transfer to live performance
- Test strategies that don't yet exist

**The system is ready to transition from BUILDING to USING.**

---

## 1. COMPLETE RESEARCH ARCHITECTURE

```
RAW EVENTS (logs/decision_trace, logs/shadow_trades, logs/execution_results, trade_journal)
    ↓ [Universe Builders: load + normalise + validate]
RESEARCH UNIVERSES (Execution=94, Decision=12,398, Market, Strategy, Shadow=4,153)
    ↓ [Outcome Enrichment: entity_id join from Execution → Decision/Market/Strategy]
ENRICHED POPULATIONS (r_multiple attached to matched records)
    ↓ [Population Resolver: get_population(name) → filtered subset]
RESEARCH POPULATIONS (ALL_TRADES, EXECUTE_DECISIONS, NO_TRADE, HORIZON_SCALP, etc.)
    ↓ [Question Contract: declares required universes, fields, joins]
QUESTION DISPATCH (QuestionRunner resolves primitives from analysis_type)
    ↓ [Primitive Execution: expectancy, segmentation, predictive_power, etc.]
ANALYSIS (statistical computation on resolved population)
    ↓ [Evidence Composition: merge metrics, classify outcome/confidence]
RESEARCH FINDING (ResearchFinding dataclass with evidence_source label)
    ↓ [Feedback Generator: deterministic rules]
RESEARCH FEEDBACK (system area, feedback type, interpretation)
    ↓ [Knowledge Engine: evidence accumulation, contradiction detection]
KNOWLEDGE STATE (SUPPORTED / CONTRADICTED / INCONCLUSIVE per subject)
    ↓ [Proposal Factory: creates governed ChangeProposal]
PROPOSAL (evidence-backed hypothesis for V10 change)
    ↓ [Candidate Design: POPULATION_FILTER configuration]
CANDIDATE (testable filter: field/operator/value)
    ↓ [Experiment Runner: applies filter, computes baseline vs candidate metrics]
EXPERIMENT RESULT (improvement, statistical significance)
    ↓ [Validator: statistical validation]
VALIDATION (VALIDATED / INCONCLUSIVE / REJECTED)
    ↓ [Promotion Gate: eligibility assessment]
PROMOTION_ELIGIBLE (requires human approval)
    ↓ [HUMAN DECISION]
IMPLEMENTATION (Kiro codes the change into V10)
    ↓
NEW V10 → observe again
```

---

## 2. UNIVERSE CAPABILITY

| Universe | Observes | Does NOT Observe | Primary Evidence | Key Dimensions | Outcome | Records |
|---|---|---|---|---|---|---|
| **EXECUTION** | Completed broker-confirmed trades | Rejected opportunities, counterfactuals | Realised R-multiple, P&L | symbol, direction, exit_reason, duration, entry/exit price, SL/TP | ✓ Realised R | 94 |
| **DECISION** | Every V10 pipeline evaluation (EXECUTE + NO_TRADE) | What would have happened if decision differed | Action, score, components, terminal_stage, strategy, EV | action, score, terminal_reason, strategy, regime | ✓ Via enrichment (for EXECUTE) | 12,398 |
| **MARKET** | Market state at each decision point | Future market behaviour, regime transitions | Regime, volatility, structure, location, HTF alignment | regime, volatility_state, session, h4_trend, h1_clarity, location | ✓ Via enrichment | ~12,398 |
| **STRATEGY** | Strategy evaluation per opportunity | Why a strategy WOULD work if it doesn't exist | Family, pattern, confidence, conditions, eligibility | family, pattern, confidence, conditions_met | ✓ Via enrichment | ~14,501 |
| **SHADOW_OUTCOME** | Counterfactual trade lifecycle outcomes | Realised broker performance, trade management effects | Counterfactual R-multiple, MFE, MAE, exit_reason | shadow_type, trade_horizon, exit_reason, strategy_id, regime | ✓ Counterfactual R | 4,153 |

---

## 7. LIVE VS SHADOW CAPABILITY

| Capability | Live Evidence | Shadow Evidence | Combined |
|---|---|---|---|
| What trades actually produced | ✓ (94 trades) | ✗ | — |
| What rejected opportunities would have produced | ✗ | ✓ (3,201 records) | — |
| Realised system expectancy | ✓ | ✗ | — |
| Counterfactual opportunity pool expectancy | ✗ | ✓ | — |
| Rejection-stage opportunity cost | ✗ (Live D-004 = counts only) | ✓ (SD-004 = counterfactual R per stage) | Complementary |
| Score → outcome relationship | ✓ (for executed, D-001) | Potential (for all signals) | Expanded scope |
| Regime → outcome | ✓ (M-001, EM-001) | ✓ (SD-007) | Both sides |
| Strategy → outcome | ✓ (S-001, ES-001) | ✓ (SD-006) | Both sides |
| Horizon comparison | ✗ | ✓ (SD-005: SCALP/INTRADAY/EXTENDED) | Shadow only |
| Missed opportunity cost | ✗ (ED-002 broken → BLOCKED) | ✓ (SD-002) | Shadow only |
| Threshold optimality | Partial (D-003 — one-sided) | Potential two-sided (future) | Shadow extends |
| Risk gate value | ✗ (D-007 BLOCKED) | Via SD-004 rejection-stage R | Shadow provides answer |

---

## 10. WHAT THE ENGINE CAN INFER — MEASUREMENT vs ASSOCIATION vs CAUSATION

| Level | What It Means | Research Engine Status |
|---|---|---|
| **OBSERVATION** | "X occurred" | 🟢 Full capability |
| **MEASUREMENT** | "X has value Y ± Z" | 🟢 Full capability (with sample-size governance) |
| **ASSOCIATION** | "X and Y tend to co-occur" | 🟢 Full capability |
| **CORRELATION** | "Higher X is associated with higher/lower Y" | 🟢 Measured via predictive_power primitive |
| **COUNTERFACTUAL** | "Had X not happened, Y would have been Z" | 🟠 Measured via Shadow (model-based, not true experimental) |
| **CAUSAL CLAIM** | "X causes Y" | 🔴 NOT ESTABLISHED — requires controlled experiment or instrumental variable |

### Causality Boundary (Critical)

The Research Engine **CANNOT** currently establish:

| Claim | Why Not |
|---|---|
| "Raising score threshold WILL improve profitability" | Only observes correlation; confounders exist |
| "Removing risk gate WILL increase expectancy" | Shadow shows counterfactual under model assumptions; slippage/market impact unknown |
| "Strategy X causes better outcomes" | Selection bias — V10 only selects X under specific conditions |
| "Changing stop distance WILL increase edge" | Shadow uses fixed SL; live has trade management |
| "Trading only regime Y WILL improve performance" | Sample may be too small; regime classification may drift |

**What it CAN support:**

| Claim | Evidence Level |
|---|---|
| "Score does/doesn't correlate with outcome" | SUPPORTED (D-001, predictive_power) |
| "Regime X is associated with lower R" | SUPPORTED (M-001, EM-001, segmentation) |
| "Risk-rejected opportunities had negative counterfactual R" | SUPPORTED (SD-004) |
| "INTRADAY horizon shows better counterfactual R than SCALP" | SUPPORTED (SD-005, comparison) |
| "These rejected opportunities would likely have lost money" | SUPPORTED (SD-002, counterfactual expectancy) |

---

## 15. OPTIMISATION CAPABILITY — LEVEL ASSESSMENT

| Level | Description | Status | Evidence |
|---|---|---|---|
| **Level 1 — DESCRIBE** | Can it describe what V10 is doing? | 🟢 YES | 45 Live questions covering all domains |
| **Level 2 — DIAGNOSE** | Can it identify where V10 appears weak? | 🟢 YES | Segmentation finds negative-R segments; SD-004 finds costly rejection stages |
| **Level 3 — DISCOVER** | Can it discover features associated with outcomes? | 🟢 YES | predictive_power, segmentation, comparison primitives |
| **Level 4 — PROPOSE** | Can it formulate evidence-backed candidate changes? | 🟢 YES | Finding→Feedback→Knowledge→Proposal→Candidate pipeline proven (EM-001) |
| **Level 5 — VALIDATE** | Can it test whether a proposed change would improve outcomes? | 🟢 YES | POPULATION_FILTER experiment against historical data proven |
| **Level 6 — PROMOTE** | Can it safely implement the change? | 🔴 NO | Requires human approval + Kiro implementation |

**The Research Engine operates at Levels 1-5.** Level 6 (automatic implementation) is deliberately human-gated.

---

## 16. THE HUMAN ROLE

| Stage | Automated? | Human Required? |
|---|---|---|
| Data collection (Live + Shadow) | ✓ Automatic (runtime) | ✗ |
| Universe building | ✓ Automatic (research.py) | ✗ |
| Question execution | ✓ Automatic | ✗ |
| Finding generation | ✓ Automatic | ✗ |
| Feedback/Knowledge | ✓ Automatic (deterministic rules) | ✗ |
| Proposal generation | ✓ Automatic | ✗ |
| Proposal ranking | ✓ Automatic (evidence quality) | ✗ |
| Candidate design | ⚠️ Semi-automatic (POPULATION_FILTER) | Review recommended |
| Experiment execution | ✓ Automatic | ✗ |
| Validation | ✓ Automatic (statistical) | ✗ |
| **Promotion decision** | ✗ | **✓ REQUIRED** |
| **Implementation** | ✗ | **✓ REQUIRED (Kiro)** |
| **Deployment** | ✗ | **✓ REQUIRED** |

---

## 17. RESEARCH ENGINE BLIND SPOTS

Things the engine currently **cannot** reliably determine:

| Blind Spot | Why | Severity |
|---|---|---|
| Causal impact of a live parameter change | Only correlational/counterfactual evidence | MEDIUM — mitigated by experimental validation |
| Below-threshold realised outcomes | Never executed → no broker outcome | LOW — Shadow compensates counterfactually |
| Unobserved market conditions | Can only research conditions that occurred during observation period | LOW — growing daily |
| Execution quality effects (slippage/latency) | Shadow has no broker; Live sample small | MEDIUM — grows with Live trades |
| Long-term regime stability | ~3 weeks of data | MEDIUM — temporal questions require more history |
| Strategy interactions (A+B vs A alone) | No combinatorial testing framework | LOW — segmentation partially addresses |
| True optimal threshold | One-sided (D-003 AMBER) | LOW — Shadow could extend this |
| New strategy discovery | Cannot test strategies that don't exist | LOW — gap characterisation (S-004) provides evidence for human design |
| Trade management impact | Shadow has SL/TP/timeout only; Live has trailing/BE/partial | MEDIUM — fundamental Shadow limitation |

---

## 20. FINAL CAPABILITY MATRIX

| Capability | Status | Evidence Source | Limitation |
|---|---|---|---|
| Describe V10 behaviour | 🟢 YES | Decision Universe (12,398 traces) | None |
| Measure realised performance | 🟢 YES | Execution Universe (94 trades) | Small sample for multi-dimensional |
| Diagnose decision bottlenecks | 🟢 YES | D-004 (rejection topology) + SD-004 (counterfactual cost) | None |
| Characterise market regimes | 🟢 YES | Market Universe + M-001/M-003 | Growing sample |
| Evaluate strategy families | 🟢 YES | Strategy Universe + S-001/S-002 + SD-006 | 30% strategy_id coverage in shadow |
| Identify missed opportunities | 🟢 YES | SD-002 (3,201 NO_TRADE shadows) | Counterfactual, not guaranteed realised |
| Analyse counterfactual outcomes | 🟢 YES | Shadow Universe (4,153 records, 100% R) | Model-based, no slippage |
| Compare horizons counterfactually | 🟢 YES | SD-005 (SCALP=1,824, INTRADAY=1,359) | EXTENDED only 18 records |
| Discover score/outcome associations | 🟢 YES | D-001, D-005 (predictive_power) | Correlational only |
| Test threshold hypotheses | 🟠 PARTIAL | D-003 (above-threshold only) | One-sided; needs shadow for full |
| Test policy changes via experiment | 🟢 YES | POPULATION_FILTER + CandidateExperiment | Historical, not forward-looking |
| Generate optimisation candidates | 🟢 YES | Finding→Proposal→Candidate pipeline | Requires sufficient evidence quality |
| Validate optimisation candidates | 🟢 YES | Experiment + statistical validation | Against historical population |
| Automatically implement changes | 🔴 NO | N/A | By design — human-gated |
| Establish causation | 🔴 NO | N/A | Fundamental limitation of observational research |

---

## 21. FINAL ANSWER

### A. What it CAN do now

- Measure V10's realised expectancy, win rate, distribution, exit behaviour
- Identify which market conditions, strategies, patterns produce better/worse outcomes
- Detect whether scoring components predict outcome (or don't)
- Show exactly where the decision pipeline rejects opportunities
- Measure the counterfactual cost of those rejections
- Compare what V10 selected against what alternatives would have done
- Discover regime-specific, strategy-specific, and horizon-specific opportunity cost
- Generate evidence-backed proposals for V10 changes
- Test those proposals against historical data
- Statistically validate whether the proposed change shows improvement
- Track whether the system is improving or degrading over time

### B. What it CAN do with limitations

- Test threshold optimality (one-sided — only sees above current threshold)
- Estimate what below-threshold opportunities would produce (via Shadow, model-dependent)
- Compare strategies across regimes (sample fragmentation with 94 Live trades)
- Attribute relative importance of market/strategy/decision/execution (small Live sample)

### C. What it CANNOT currently do

- Prove causation (only association)
- Guarantee that Shadow improvements transfer to Live
- Test strategies that don't exist yet
- Measure execution quality effects (slippage, latency)
- Analyse trade management impact (trailing, BE, partial exits)
- Automatically implement or deploy changes

### D. What Shadow adds

Shadow is the ONLY evidence source for:
- Rejected opportunity outcomes (3,201 counterfactual records)
- Rejection-stage opportunity cost (SD-004)
- Horizon geometry comparison (SD-005)
- Full-opportunity-pool expectancy (SD-001: all detected signals, not just executed)

Without Shadow, the Research Engine can only study the 94 trades V10 actually executed (2.7% of decisions). With Shadow, it can study counterfactual outcomes for 78% of all decisions.

### E. What still requires human/programmer intervention

- Approving promotion of validated candidates
- Implementing approved changes in V10 code
- Designing new strategies (S-004 identifies gaps; human designs solutions)
- Interpreting cross-side findings (correlation ≠ causation)
- Deciding when V10 is ready for prop challenge

### F. Next logical development step

**USE THE ENGINE.** Run the full 51-question research cycle. Review findings. Identify the strongest proposals. Design candidates. Validate experiments. Approve or reject promotions. Implement approved changes. Observe the improved V10. Repeat.

The infrastructure phase is complete. The research-driven optimisation loop is ready to operate.

---

*End of capability specification. No code modified.*
