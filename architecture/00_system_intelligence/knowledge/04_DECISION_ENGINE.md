# DECISION ENGINE KNOWLEDGE CARD

**Type:** Factual snapshot. Observer memory layer.
**Domain:** 04 DECISION_ENGINE
**Purpose:** Answer "How does the system decide, and why was a specific decision made?"
**Last validated:** 2026-07-25 (from decision_ledger: 2,809 records + decision_trace analysis)

---

## Purpose

The Decision Engine owns the transformation of market opportunities into actionable trade decisions. It produces exactly one of two outcomes per evaluation: **EXECUTE** (create an order) or **NO_TRADE** (do nothing). The decision is the system's single most important output — everything upstream feeds into it, everything downstream depends on it.

---

## Owner Authority

| Component | Authority | Location |
|:----------|:---------|:---------|
| **New Engine v1.2** | Sole decision producer | `core/pipeline/new_engine.py` |
| Scoring engine | Computes 10-component score | `core/pipeline/scoring_engine.py` |
| Strategy classifier | Selects weight profile | `core/pipeline/strategy_classifier.py` |
| Execution policy | EV gate + swing filter + risk check | `core/pipeline/execution_policy.py` |
| Risk manager | Computes OrderIntent (SL/TP/volume) | `risk/manager.py` |

**CAN:** Detect patterns, classify strategies, score opportunities, apply policy, produce OrderIntent.
**CANNOT:** Execute trades, bypass guards, modify config, manage positions.

**Source:** `core/pipeline/new_engine.py` docstring (CAN/CANNOT block). Confidence: HIGH.

---

## Architecture (Decision Flow)

```
M5 Candle closes
    │
    ▼
Signal Orchestrator — pattern detection (strategy/signal_orchestrator.py)
    │ No pattern → PATTERN_REJECT (46% of all decisions)
    ▼
Strategy Classification — CONTINUATION / REVERSAL / FALSE_BREAK
    │ Selects scoring weight profile
    ▼
10-Component Scoring — weighted composite
    │ Score < 0.35 → NO_TRADE (score_below_threshold)
    ▼
Execution Policy — EV gate, swing filter, risk validation
    │ Policy fail → NO_TRADE (with specific reason)
    ▼
Risk Manager — compute SL, TP, volume
    │ Risk check fail → NO_TRADE (risk_rejected)
    ▼
EXECUTE → OrderIntent produced
```

---

## Decision Stages (Detail)

### Stage 1: Pattern Detection

| Property | Value |
|:---------|:------|
| Input | M5 closed candle OHLC |
| Owner | `strategy/signal_orchestrator.py` + `patterns/` (12 pattern modules) |
| Outcome | Pattern found → proceed / No pattern → PATTERN_REJECT |
| Evidence | `decision_ledger.decision=PATTERN_REJECT`, `decision_trace.pattern_name` |

### Stage 2: Strategy Classification

| Property | Value |
|:---------|:------|
| Input | Detected pattern + market context (regime, bias) |
| Owner | `core/pipeline/strategy_classifier.py` |
| Outcome | Classified as CONTINUATION / REVERSAL / FALSE_BREAK / None |
| Evidence | `decision_trace.selected_strategy`, `decision_trace.strategy_confidence` |

### Stage 3: Scoring (10 Components)

| Property | Value |
|:---------|:------|
| Input | Pattern quality + market features |
| Owner | `core/pipeline/scoring_engine.py` |
| Components | pattern_quality, bias_alignment, market_quality, trend_alignment, chop_clarity, volatility_quality, stability_quality, confirmation_pre, htf_alignment, h4_alignment |
| Weights | Global (neutral) OR strategy-specific (if classified) |
| Threshold | 0.35 (referenced in code — not a named config constant) |
| Outcome | Score ≥ threshold → proceed / Below → NO_TRADE |
| Evidence | `decision_trace.score_neutral`, `score_strategy`, `components{}`, `weights_used` |
| Diagnostics | `decision_trace.weakest_component`, `threshold_gap`, `closest_flip_component` |

### Stage 4: Execution Policy

| Property | Value |
|:---------|:------|
| Input | Scored opportunity + market context |
| Owner | `core/pipeline/execution_policy.py` |
| Sub-gates | EV gate (disabled: `ENABLE_EV_GATE=False`), Swing filter (H1 BOS required), Risk validation |
| Outcome | All pass → proceed / Any fail → NO_TRADE |
| Evidence | `decision_trace.terminal_stage` = "ev_policy" or "swing", `terminal_reason` |

### Stage 5: Risk Manager (OrderIntent)

| Property | Value |
|:---------|:------|
| Input | Approved opportunity + price levels |
| Owner | `risk/manager.py` |
| Produces | `OrderIntent(symbol, side, volume, sl, tp, pattern, metadata={horizon: "SCALP"})` |
| Can reject | If MIN_SL_DISTANCE not met |
| Evidence | `decision_trace.terminal_stage` = "risk", `decision_ledger.execution_intent` |

---

## Decision Distribution (From Evidence)

Based on 2,809 total decisions (all recorded history):

| Decision | Count | Percentage | Meaning |
|:---------|:-----:|:----------:|:--------|
| PATTERN_REJECT | 1,288 | 46% | No pattern detected — evaluation never started |
| NO_TRADE | 1,398 | 50% | Pattern detected, but rejected at a later stage |
| RISK_BLOCK | 81 | 3% | Engine approved, but runtime guard chain blocked |
| EXECUTE | 42 | 1% | Full approval — order submitted to broker |

### Terminal Stages (from decision_trace, engine-evaluated cycles only):

| Stage | Count | Meaning |
|:------|:-----:|:--------|
| risk | 996 | Risk manager rejected (usually MIN_SL_DISTANCE) |
| swing | 355 | Swing filter blocked (H1 structure doesn't confirm) |
| execute | 167 | Passed all stages (includes shadow-mode evaluations) |
| unknown | 67 | Stage classification failed |
| ev_policy | 26 | EV gate rejected (when it was briefly enabled) |

**Source:** decision_ledger + decision_trace records. Confidence: HIGH.

---

## Configuration Controls

| Parameter | Value | Effect | Source |
|:----------|:------|:-------|:-------|
| `USE_NEW_PIPELINE` | True | New engine is sole decision authority | config.py |
| `ENABLE_EV_GATE` | False | EV check disabled — does not block trades | config.py |
| `PORTFOLIO_RANKING_AUTHORITY` | False | Ranking is passive, does not gate execution | config.py |
| `MARKET_CONTEXT_SCORING_ENABLED` | False | Market context observed but not scoring-influential | config.py |
| Score threshold | 0.35 | Hardcoded in scoring_engine (not in config) | Code |
| `MIN_RR` | 2.0 | Minimum acceptable reward:risk | config.py |
| `SL_BUFFER` | 0.0002 | Added to SL distance (2 pips) | config.py |

---

## Evidence Sources

| Dataset | What It Shows | Key Fields |
|:--------|:-------------|:-----------|
| `decision_ledger` | Per-cycle outcome | decision, reason, signal_score, risk_flag, execution_intent, causal_signature |
| `decision_trace` | Pipeline diagnostic | terminal_stage, terminal_reason, components, score_strategy, weakest_component, threshold_gap, closest_flip |
| `decision_audit` | Full decision snapshot | should_trade, score, intent, engine_state, trigger_candle, confirmation |
| `opportunity_assessment` | Scored assessment record | components, ev, p_success, strategy, regime |
| `assessments` | Assessment records | Same as above (Phase 2B path) |
| `opportunities` | Detected setups + lifecycle | pattern, state, rejection_reason, rejection_stage |

---

## Questions This Card Answers

| Question | How to Answer |
|:---------|:-------------|
| Why didn't EURUSD trade? | `obs.explain("EURUSD")` → decision + reason + terminal_stage |
| What stage rejected it? | `decision_trace.terminal_stage` |
| What was the score? | `decision_trace.score_strategy` |
| What's the weakest component? | `decision_trace.weakest_component` |
| What would change the decision? | `decision_trace.closest_flip_component` + `closest_flip_delta` |
| What % of opportunities pass scoring? | Count decision_trace where terminal_stage != "scoring" / total |
| How many trades were approved? | decision_ledger count where decision=EXECUTE (42 of 2809 = 1.5%) |
| What pattern was detected? | `decision_trace.pattern_name` |
| What strategy was selected? | `decision_trace.selected_strategy` |

---

## Known Unknowns

| Unknown | Impact | How to Validate |
|:--------|:-------|:----------------|
| Score threshold is hardcoded (0.35) not in config | Cannot determine threshold from config inspection alone | Read `core/pipeline/scoring_engine.py` |
| Strategy weight profiles (CONTINUATION vs REVERSAL vs FALSE_BREAK) | Cannot see exact weights per strategy from persistence | Read `core/pipeline/strategy_weights.py` |
| Whether `unknown` terminal_stage (67 records) represents a bug or edge case | May be masking real rejection reasons | Investigate `core/decision_trace.py` → `_classify_terminal_stage()` |
| EV gate was briefly enabled then disabled (26 ev_policy records) | May confuse analysis if not time-filtered | Filter by date when EV gate was active |
| Voter system (16 modules in core/voters/) influence on scoring | Not visible in persistence — internal to scoring | Read `core/voters/` to understand sub-component contributions |

---

## Related Domains

| Domain | Relationship |
|:-------|:------------|
| 02 STRATEGY | Provides patterns + signals as input to decision engine |
| 03 MARKET_CONTEXT | Provides regime + HTF alignment as scoring inputs |
| 05 RISK_SYSTEM | Receives EXECUTE decisions; can block via guard chain |
| 06 EXECUTION_SYSTEM | Receives approved OrderIntent for broker submission |
| 10 PERFORMANCE_ANALYTICS | Evaluates whether decisions produce profitable outcomes |

---

*End of Decision Engine Knowledge Card.*
