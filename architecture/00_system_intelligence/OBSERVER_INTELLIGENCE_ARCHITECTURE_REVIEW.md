# OBSERVER INTELLIGENCE ARCHITECTURE REVIEW

**Status:** Architectural checkpoint. Approval required before v2 implementation.
**Question:** Can this architecture become a true System Intelligence Assistant?

---

## 1. Can This Architecture Answer Arbitrary Questions?

### Current State: NO — not arbitrary.

The v1 Observer handles ~10 predefined intent categories via keyword matching. It answers questions it has been programmed to recognise. It cannot:

- Handle questions it has never seen before
- Combine evidence from multiple domains in a single answer
- Reason about temporal relationships ("Was this better last week?")
- Answer comparative questions ("Which symbol performs best in LONDON session?")
- Explain emergent patterns it hasn't been told to look for

### What Would Make It Arbitrary-Capable:

The system needs to transition from **pattern-matched dispatch** to **knowledge-graph traversal**. Instead of "if question contains keyword X → call function Y", it needs:

1. **A queryable knowledge model** — structured representation of what the system knows, indexed by concept rather than by keyword.
2. **A composable evidence collector** — ability to pull from multiple sources and join results for a single answer.
3. **A reasoning engine that can chain** — "This caused that, which led to this outcome, because of that constraint."

### The Gap

| v1 (Current) | v2 (Required) |
|:---:|:---:|
| Fixed intent categories | Open-ended concept recognition |
| One evidence source per answer | Multi-source joins within one answer |
| Hardcoded reasoning paths | Composable reasoning chains |
| Answers what it was built to answer | Answers what the knowledge model can support |

---

## 2. Additional Layers Required

### Layer 0: Concept Model (Missing)

Before intent classification, the system needs a model of the CONCEPTS it understands — not just keywords.

```
Current: "blocked" → guards intent
Needed:  "blocked" → concept: EXECUTION_PREVENTION
         → owned by: Risk domain
         → evidence in: decision_ledger (RISK_BLOCK), decision_trace (terminal_stage)
         → related concepts: guard_chain, veto_authority, risk_flag
         → can join to: opportunity (what was blocked), shadow_trades (what would have happened)
```

The concept model is a structured ontology of the trading system's vocabulary, not a keyword list.

### Layer 2.5: Evidence Composition (Missing)

Between routing and reasoning, an intermediate layer is needed that can:

- Collect from multiple datasets for one question
- Join records across datasets using identity keys (entity_id, correlation_id, cycle_id)
- Filter by time range
- Aggregate (count, average, group-by)

Current state: each intent handler does its own bespoke data retrieval. There is no reusable composition layer.

### Layer 3.5: Uncertainty Quantification (Missing)

The reasoning layer currently produces explanations with binary confidence. It needs:

- "I found 3 records supporting this conclusion" (strong)
- "I found 1 record but the entity_id didn't match" (weak)
- "I found no direct evidence but can infer from related data" (uncertain)
- "This question requires data I cannot access" (impossible)

### Layer 4.5: Advisory Gate (Missing — see Section 6)

Between reasoning and response, a gate that determines:
- Is this an investigation? → present facts
- Is this analysis? → present interpretation
- Is this advisory? → present recommendation with evidence + caveats

---

## 3. What Should Remain Outside the Observer

| Capability | Inside Observer? | Why |
|-----------|:----------------:|-----|
| Reading datasets | ✅ | Core function |
| Explaining decisions | ✅ | Core function |
| Detecting health issues | ✅ | Core function |
| Recommending config changes | ⚠️ Conditional | Only with evidence + explicit advisory flag |
| Executing config changes | ❌ Never | Human authority |
| Trading decisions | ❌ Never | Decision engine authority |
| Risk overrides | ❌ Never | Guard chain authority |
| Creating new datasets | ❌ Never | Persistence ownership |
| Modifying architecture docs | ❌ Never | Human authority |
| Predicting future market behaviour | ❌ Never | Outside system scope |
| Generating strategy ideas without evidence | ❌ Never | Must be evidence-backed |

### The Boundary

The Observer is an intelligence ANALYST, not an OPERATOR. It understands the machine and explains it. It does not operate the machine or pretend to know things it hasn't observed.

---

## 4. How Knowledge Sources Should Interact

### Knowledge Taxonomy

| Source Type | What It Contains | How Observer Uses It | Update Frequency |
|:-----------:|:----------------:|:--------------------:|:----------------:|
| **Architecture docs** | Design intent, ownership, boundaries | "What SHOULD happen" / "Where does X belong" | Manual (on change) |
| **Configuration** | Current runtime rules | "What IS active right now" | Per-restart |
| **Runtime state** | Process health, MT5 status | "Is the system alive" | Real-time (heartbeat) |
| **Decision evidence** | Per-cycle decisions + reasoning | "What WAS decided and WHY" | Per M5 bar |
| **Execution evidence** | Broker interactions | "What DID the broker do" | Per trade attempt |
| **Trade outcomes** | Realised P&L, R-multiples | "What WAS the result" | Per trade close |
| **Research results** | Shadow trades, horizon readiness, contracts | "What does HISTORY suggest" | Periodic (research runs) |

### Interaction Model

```
Architecture docs (DESIGN INTENT)
         ↕ compared against
Runtime + Config (CURRENT STATE)
         ↕ explains
Decision evidence (WHAT HAPPENED)
         ↕ validated by
Execution + Trade outcomes (WHAT RESULTED)
         ↕ feeds into
Research (WHAT SHOULD CHANGE)
```

The Observer's unique value: it can traverse this entire chain for a single question. No other component has cross-layer visibility.

---

## 5. Question Type Classification

### Investigation: "What happened?"

**Characteristics:** Factual. Verifiable. Single-source or simple join.
**Examples:**
- "What was the last trade on GBPUSD?"
- "How many decisions were made yesterday?"
- "What is the current heartbeat status?"

**Observer behaviour:** Retrieve → Present. Minimal reasoning.

### Explanation: "Why did it happen?"

**Characteristics:** Causal. Requires chain construction. Multi-source joins.
**Examples:**
- "Why didn't EURUSD trade today?"
- "Why was this opportunity rejected?"
- "What caused the execution failure?"

**Observer behaviour:** Retrieve from multiple sources → Build causal chain → Identify terminal cause → Present with evidence.

### Analysis: "What does the data show?"

**Characteristics:** Aggregate. Comparative. May require temporal awareness.
**Examples:**
- "Which pattern has the best win rate?"
- "Is the system performing better or worse than last week?"
- "What percentage of opportunities pass scoring?"

**Observer behaviour:** Retrieve bulk data → Aggregate → Compute metrics → Compare (if temporal) → Present with context.

### Advisory: "What should I do next?"

**Characteristics:** Prescriptive. Requires evidence + judgement. Higher risk of being wrong.
**Examples:**
- "Should I enable INTRADAY?"
- "Is the scoring threshold too aggressive?"
- "What should I investigate?"

**Observer behaviour:** Retrieve evidence → Analyse (as above) → Apply decision framework → Present recommendation WITH evidence + uncertainty + caveats.

### Key Distinction

| Type | Certainty | Risk of Error | Evidence Required |
|:----:|:---------:|:-------------:|:-----------------:|
| Investigation | HIGH | LOW | Single source |
| Explanation | MEDIUM-HIGH | LOW | Multi-source chain |
| Analysis | MEDIUM | MEDIUM | Aggregate + temporal |
| Advisory | LOW-MEDIUM | HIGH | Comprehensive + judgement |

---

## 6. Should Advisory Belong Inside System Intelligence?

### Arguments FOR (include advisory):

- The Observer already has all the evidence needed
- No other component has cross-layer visibility
- Recommendations without evidence are useless anyway
- Separating advisory into another layer creates unnecessary indirection

### Arguments AGAINST (keep separate):

- Advisory carries responsibility — if the Observer recommends wrong, losses result
- Mixing investigation/explanation with recommendations blurs the "read-only" boundary
- Advisory requires a different confidence threshold than explanation
- The human owner may want pure facts without opinion

### Recommendation: INCLUDE, but gate strictly.

Advisory belongs inside System Intelligence BUT:

1. **Advisory is never automatic.** Only triggered by explicit advisory questions.
2. **Advisory always shows evidence.** No recommendation without data.
3. **Advisory always shows uncertainty.** "Based on 180 shadow trades (moderate confidence)..."
4. **Advisory always states reversibility.** "This change can be reverted by setting X=Y."
5. **Advisory never acts.** It recommends. The human decides.

Implementation: an `AdvisoryGate` that only activates when:
- Question is classified as advisory intent
- Sufficient evidence exists (minimum sample size, minimum confidence)
- The recommendation is within the Observer's domain (system changes, not market predictions)

---

## 7. Minimum Viable Observer v2 Architecture

### What v2 Adds Over v1

| v1 (Current) | v2 (Target) |
|:---:|:---:|
| Keyword-matched intents | Concept-based intent with entity extraction |
| One action per question | Composable multi-source evidence |
| Fixed reasoning per intent | Reasoning chains that follow evidence |
| Text-only responses | Structured responses with evidence citations |
| No temporal awareness | Basic time-range filtering |
| No advisory | Gated advisory with evidence thresholds |

### v2 Component Architecture

```
┌──────────────────────────────────────────────────────┐
│                    OBSERVER v2                         │
│                                                      │
│  ┌──────────────┐                                   │
│  │ Intent Layer │ ← concept model (not just keywords)│
│  └──────┬───────┘                                   │
│         │                                           │
│  ┌──────┴───────┐                                   │
│  │ Router Layer │ ← domain graph (15 domains)       │
│  └──────┬───────┘                                   │
│         │                                           │
│  ┌──────┴───────────┐                               │
│  │ Evidence Composer │ ← multi-source + joins        │
│  └──────┬───────────┘                               │
│         │                                           │
│  ┌──────┴───────┐                                   │
│  │ Reasoning    │ ← causal chains + uncertainty     │
│  └──────┬───────┘                                   │
│         │                                           │
│  ┌──────┴───────┐                                   │
│  │Advisory Gate │ ← evidence threshold + caveats    │
│  └──────┬───────┘                                   │
│         │                                           │
│  ┌──────┴───────┐                                   │
│  │ Response     │ ← structured + citations          │
│  └──────────────┘                                   │
└──────────────────────────────────────────────────────┘
```

### Minimum Implementation for v2

| Component | What It Needs | Effort |
|-----------|:-------------|:------:|
| Concept model | Map of ~50 system concepts with relationships | Medium |
| Entity extraction (enhanced) | Time ranges, comparisons, component names | Low |
| Evidence composer | Generic `query(dataset, filters, joins)` function | Medium |
| Reasoning chains | Template-based chain builder (not AI) | Medium |
| Advisory gate | Threshold check + caveat generator | Low |
| Structured response | JSON output mode + citation format | Low |

### What v2 Does NOT Need

- Machine learning / LLM (deterministic reasoning is sufficient for this domain)
- Real-time streaming (batch query over persisted JSONL is fine)
- External knowledge (the system's own datasets are the only truth source)
- Natural language generation (structured templates with evidence citations are clearer)

---

## 8. Missing Concepts Before Implementation

| # | Missing Concept | Why It Matters | Implementation |
|:-:|:---------------|:---------------|:---------------|
| 1 | **Concept ontology** | Without it, the system can only match keywords, not understand meaning | Define ~50 system concepts with relationships (concept_model.py) |
| 2 | **Time-range parsing** | "yesterday", "last week", "this month" are common in real questions | Add temporal parser to intent layer |
| 3 | **Cross-dataset joins** | Most explanation questions require 2-3 dataset joins | Generic evidence composer with join logic |
| 4 | **Aggregate operations** | Analysis questions need count/avg/groupby | Add aggregate capability to evidence layer |
| 5 | **Confidence propagation** | Each layer should pass confidence forward; final response shows certainty | Add confidence field to Evidence and Explanation |
| 6 | **Evidence citation format** | Every claim must reference source (dataset, file, record) | Standardise citation in response |
| 7 | **Advisory threshold** | When is evidence "sufficient" for a recommendation? | Define minimum sample sizes per advisory type |

---

## 9. Approval Criteria

This architecture is ready for v2 implementation when:

1. ✅ The 5-layer pipeline is validated (done — OBSERVER_QUERY_ARCHITECTURE.md)
2. ✅ The domain routing model is validated (done — 15 domains operational)
3. ✅ The question type taxonomy is defined (done — this document, Section 5)
4. ✅ The advisory boundary is decided (done — include with gate, Section 6)
5. ⬜ The concept ontology is defined (~50 concepts with relationships)
6. ⬜ The evidence composition interface is designed
7. ⬜ The confidence propagation model is specified

**Items 5-7 are pre-implementation design tasks. They should be completed before coding v2.**

---

*End of Observer Intelligence Architecture Review.*
