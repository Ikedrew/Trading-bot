# OBSERVER INVESTIGATION MODE

**Status:** Design document. Defines how objective-driven investigations work.
**Principle:** Observer retrieves evidence. Kiro reasons. Owner decides.
**Constraint:** Observer remains read-only. Never modifies, never invents.

---

## 1. What Investigation Mode Is

Investigation Mode handles questions that do NOT have a single data source answer. The user states an OBJECTIVE, not a query. The system must determine what to look at, gather evidence from multiple domains, identify patterns, and construct an explanation.

### Examples of Investigation Objectives

- "Why are trades low?"
- "What is preventing profitability?"
- "What should I validate next?"
- "Is this system improving?"
- "What would a senior engineer fix first?"

These differ from simple queries ("What's the win rate?") because:
- No single Observer method answers them
- Multiple evidence sources are required
- Causal reasoning must connect evidence points
- The answer may involve uncertainty and competing hypotheses

---

## 2. How It Works (Division of Labour)

```
Owner states objective
    │
    ▼
Kiro (reasoning layer)
    │ 1. Decomposes objective into sub-questions
    │ 2. Identifies which Observer evidence is needed
    │ 3. Calls Observer methods
    │ 4. Receives structured evidence
    │ 5. Reasons across evidence
    │ 6. Constructs explanation
    │ 7. Presents findings + confidence + unknowns
    │
    ▼
Observer (evidence layer)
    │ - Retrieves data when asked
    │ - Reports what exists and what doesn't
    │ - Never interprets, never recommends
    │
    ▼
Owner receives investigation report
```

### Who Does What

| Step | Owner | Kiro | Observer |
|:----:|:-----:|:----:|:--------:|
| State objective | ✅ | — | — |
| Decompose into sub-questions | — | ✅ | — |
| Determine evidence needed | — | ✅ | — |
| Retrieve evidence | — | — | ✅ |
| Report missing evidence | — | — | ✅ |
| Reason across evidence | — | ✅ | — |
| Assess confidence | — | ✅ | — |
| Present findings | — | ✅ | — |
| Decide action | ✅ | — | — |

---

## 3. Investigation Workflow

### Step 1: Objective Decomposition

Kiro breaks the objective into answerable sub-questions.

**Example:** "Why are trades low?"

Decomposition:
1. How many trades have occurred? (PERFORMANCE)
2. What is the EXECUTE rate? (DECISION_ENGINE)
3. What stage rejects the most opportunities? (DECISION_ENGINE)
4. Are guards blocking approved trades? (RISK_SYSTEM)
5. Are patterns being detected? (STRATEGY)
6. Is the bot actually running during market hours? (RUNTIME)

Each sub-question maps to an Observer call.

### Step 2: Evidence Collection Plan

For each sub-question, identify:
- Which Observer method answers it
- What dataset contains the evidence
- What time range is relevant
- What constitutes "sufficient" evidence

**Example plan:**

| Sub-question | Observer Call | Dataset | Time Range |
|:-------------|:-------------|:--------|:----------:|
| Trade count | `obs.trades()` | trade_journal | Last 30 days |
| EXECUTE rate | `obs.explain()` aggregate | decision_ledger | Last 30 days |
| Rejection stages | `obs.explain()` multiple symbols | decision_trace | Latest day |
| Guard blocks | `obs.guards()` | decision_ledger RISK_BLOCK | All time |
| Pattern detection rate | count PATTERN_REJECT vs total | decision_ledger | Last 30 days |
| Bot uptime | `obs.state()` + heartbeat history | heartbeat | Last 7 days |

### Step 3: Evidence Retrieval

Kiro calls Observer methods. Observer returns structured data.

If evidence is missing, Observer reports:
```
{
    "available": false,
    "reason": "No trade_journal records in requested time range",
    "suggestion": "Bot may not have been running during this period"
}
```

### Step 4: Pattern Recognition

Kiro examines evidence for:
- **Bottlenecks** — which stage filters the most? (e.g., risk stage: 996 rejections)
- **Anomalies** — is something unusual? (e.g., 0 EXECUTE in 3 days)
- **Trends** — is it getting better or worse? (requires temporal comparison)
- **Correlations** — do problems concentrate in specific symbols/sessions/patterns?

### Step 5: Hypothesis Formation

Based on patterns, Kiro forms hypotheses:

```
Hypothesis 1: "Trades are low because the risk stage (MIN_SL_DISTANCE) rejects 60% of scored opportunities."
Evidence: 996 risk rejections out of 1,611 engine-evaluated decisions.
Confidence: HIGH (large sample).

Hypothesis 2: "The swing filter may be too conservative, blocking 355 opportunities that passed scoring."
Evidence: 355 swing rejections with scores above threshold.
Confidence: MEDIUM (would need shadow outcome comparison to confirm these were profitable).
```

### Step 6: Investigation Report

Kiro presents:

```
INVESTIGATION: "Why are trades low?"

FINDINGS:
1. EXECUTE rate is 1.5% (42 of 2,809 decisions).
2. 46% of cycles detect no pattern at all (PATTERN_REJECT).
3. Of opportunities that DO get scored, 62% are rejected by risk stage (MIN_SL_DISTANCE).
4. 22% are rejected by swing filter (H1 structure).
5. Only 10% of scored opportunities reach EXECUTE.

PRIMARY CAUSE:
The risk stage MIN_SL_DISTANCE check rejects the majority of viable opportunities.
This happens when the calculated stop-loss is less than 3.0 pips from entry.

SECONDARY CAUSE:
The swing filter requires H1 Break of Structure confirmation.
In RANGING regimes, this confirmation rarely exists, blocking directional trades.

CONFIDENCE: HIGH (2,809 decision records available)

UNKNOWNS:
- Whether rejected opportunities would have been profitable (requires shadow outcome analysis)
- Whether the 3.0 pip minimum is appropriate for all symbols

SUGGESTED INVESTIGATION:
- Compare guard-blocked opportunities against shadow trade outcomes
- Review MIN_SL_DISTANCE threshold per symbol (JPY pairs have different pip scale)
```

---

## 4. Observer's Role (Boundaries)

### What Observer Provides in Investigation Mode

| Capability | Method | Output |
|:-----------|:-------|:-------|
| Trade counts + performance | `obs.trades(days)` | Win rate, R, PnL, by-pattern, by-horizon |
| Decision breakdown | `obs.explain(symbol)` per symbol | Decision type, reason, stage, evidence |
| Guard statistics | `obs.guards()` | Block counts by guard, by symbol |
| System health | `obs.health()` | Dataset freshness, record counts |
| Runtime status | `obs.state()` | Running/shutdown, mode, last active |
| Configuration | `obs.config()` | All active flags, limits, guards |
| Domain routing | `obs.route(question)` | Which domains own this evidence |

### What Observer Does NOT Do

| Forbidden | Why | Who Does It Instead |
|:----------|:----|:-------------------:|
| Form hypotheses | Requires judgement | Kiro |
| Compare periods | Requires temporal reasoning | Kiro (using multiple obs.trades() calls) |
| Assess severity | Requires context + goals | Kiro |
| Recommend actions | Requires risk assessment | Kiro |
| Predict outcomes | Requires speculation | Nobody (not supported) |

---

## 5. Investigation Types

### Type A: Diagnostic ("What's wrong?")

**Trigger:** "Why are trades low?" / "What's preventing profitability?" / "Why did performance drop?"

**Workflow:**
1. Gather performance baseline (obs.trades)
2. Gather decision funnel (obs.explain across symbols)
3. Gather guard impact (obs.guards)
4. Identify largest filter/bottleneck
5. Report cause + evidence

### Type B: Readiness ("Am I ready?")

**Trigger:** "Is this ready for production?" / "How many more weeks of data?" / "What should I validate?"

**Workflow:**
1. Gather evidence landscape (obs.health — sample sizes)
2. Gather performance metrics (obs.trades — statistical significance)
3. Gather shadow validation (shadow trade counts + activation readiness)
4. Compare against minimum thresholds (from knowledge cards)
5. Report readiness assessment + gaps

### Type C: Improvement ("What should I fix?")

**Trigger:** "What would a senior engineer fix?" / "What has the highest value?" / "Where is the biggest problem?"

**Workflow:**
1. Gather full system scan (health + trades + guards + decisions)
2. Identify top 3 issues by impact (largest rejection count, worst R, most blocks)
3. Cross-reference with known unknowns (from knowledge cards)
4. Rank by expected improvement value
5. Report prioritised list with evidence

### Type D: Comparative ("Is it getting better?")

**Trigger:** "Has win rate improved?" / "Is this better than last week?" / "Is the system learning?"

**Workflow:**
1. Gather metrics for period A (obs.trades with time filter)
2. Gather metrics for period B (obs.trades with different time filter)
3. Compute deltas
4. Assess statistical significance (sample size check)
5. Report comparison + confidence

**Current limitation:** Observer v1 has no time-range parameters. Comparative investigations require reading raw JSONL with date filtering (Kiro can do this directly).

---

## 6. Failure Handling

### Insufficient Evidence

```
Objective: "Is the system improving?"
Problem: Only 31 trades exist. Cannot determine trend with statistical confidence.
Response: "Insufficient data. 31 trades across 3 days is too few to identify trends.
           Recommend: collect 100+ trades (approximately 2-3 weeks at current rate) before reassessing."
```

### Missing Data Source

```
Objective: "What is the slippage impact?"
Problem: trade_truth.slippage fields are always 0.0 (MT5 limitation).
Response: "Cannot determine. Slippage data is not available from the current broker integration.
           Alternative: compare execution_results.fill_price vs entry_reference for approximation."
```

### Conflicting Evidence

```
Objective: "Is the scoring threshold correct?"
Problem: Lowering threshold would capture more winners but also more losers.
Response: "Evidence is mixed. Of 355 swing-blocked opportunities, shadow outcomes show:
           - 40% would have been profitable
           - 60% would have hit stop loss
           This suggests the filter adds net value but is not perfectly calibrated.
           Confidence: MEDIUM (shadow data, not live execution)."
```

### Out of Scope

```
Objective: "What will EUR/USD do tomorrow?"
Response: "This question requires market prediction, which is outside the Observer's scope.
           The Observer explains system behaviour — it does not predict market direction."
```

---

## 7. Output Format

Every investigation produces a structured report:

```
INVESTIGATION: [objective restated]

SUB-QUESTIONS INVESTIGATED:
1. [question] → [evidence source] → [finding]
2. [question] → [evidence source] → [finding]
...

FINDINGS:
- [Fact 1 with evidence citation]
- [Fact 2 with evidence citation]

CAUSES IDENTIFIED:
- Primary: [cause] (confidence: HIGH/MEDIUM/LOW)
- Secondary: [cause] (confidence: HIGH/MEDIUM/LOW)

EVIDENCE USED:
- [dataset: record count, time range]
- [dataset: record count, time range]

UNKNOWNS:
- [what cannot be determined and why]

CONFIDENCE: [overall confidence with justification]

NEXT STEPS (only if advisory was requested):
- [action with expected impact]
- [action with expected impact]
```

---

## 8. What This Design Does NOT Include

| Excluded | Why |
|:---------|:----|
| Automated periodic investigations | Owner initiates. System does not self-trigger. |
| Persistent investigation history | Investigations are one-shot. No conversation memory in Observer. |
| Auto-remediation | Observer never acts. Only explains. |
| Machine learning over investigation results | Over-engineering. Kiro's reasoning is sufficient. |
| Custom investigation templates | The decomposition logic lives in Kiro's reasoning, not in code. |

---

## 9. Implementation Implications

This design requires **no new Observer code** for the core workflow. Kiro already has:
- Access to all Observer methods (via steering file)
- Natural language understanding
- Reasoning capability
- The ability to make multiple sequential calls

**The only Observer-level gap that would improve investigations:**

| Gap | Impact | Fix |
|:----|:-------|:----|
| Time-range filtering on `obs.trades(days)` | Cannot compare week-over-week | Already has `days` param — works |
| Decision funnel counts (not just latest) | Cannot show "X% rejected at stage Y" without reading raw JSONL | Add `obs.decision_summary()` |
| Per-symbol decision breakdown | Cannot show which symbols have lowest EXECUTE rate | Add optional `symbol` filter to trades/guards |

These are the ONLY implementation items needed. The investigation workflow itself is Kiro's reasoning — not code to build.

---

*End of Observer Investigation Mode Design.*
