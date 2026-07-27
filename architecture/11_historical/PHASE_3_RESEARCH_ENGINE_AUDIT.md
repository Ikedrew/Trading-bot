# PHASE 3: RESEARCH ENGINE AUDIT

**Date:** 2026-07-23
**Question:** Can the Research Engine now explain the complete path from market opportunity to capital allocation to realised outcome?
**Answer:** **NOT YET.** The Research Engine has loaders for 4 of 9 required datasets. It cannot access Opportunity, Assessment, Portfolio Ranking, Shadow Comparison, or Protection Audit data. The join chain has gaps. New loaders and questions are needed.

---

## 1. Research Engine Current Capability

### Existing Data Access (loaders.py)

| Loader | Dataset | Status | Records Available |
|--------|---------|--------|-------------------|
| `load_shadow_trades()` | Shadow trades | ✅ Working | 1,232 |
| `load_trade_truth()` | Trade truth | ✅ Working | 35 |
| `load_decision_ledger()` | Decision ledger | ✅ Working | 8,155 |
| `load_decision_trace()` | Decision trace | ✅ Working | ~5,000 |

### Missing Data Access (no loaders exist)

| Dataset | Storage Location | Loader Status |
|---------|-----------------|---------------|
| **Opportunities** | `logs/opportunities/{SYMBOL}/{DATE}.jsonl` | ❌ NO LOADER |
| **Assessments** | `logs/assessments/{SYMBOL}/{DATE}.jsonl` | ❌ NO LOADER |
| **Portfolio Rankings** | `logs/portfolio_rankings/{DATE}.jsonl` | ❌ NO LOADER |
| **Shadow Comparison** | `logs/portfolio_shadow/{DATE}.jsonl` | ❌ NO LOADER |
| **Execution Results** | `logs/execution_results/{SYMBOL}/{DATE}.jsonl` | ❌ NO LOADER |
| **Protection Audit** | `logs/protection_audit/{SYMBOL}/{DATE}.jsonl` | ❌ NO LOADER |
| **Risk Deviation** | `logs/risk_deviation/{SYMBOL}/{DATE}.jsonl` | ❌ NO LOADER |
| **Execution Context** | `logs/execution_context/{SYMBOL}/{DATE}.jsonl` | ❌ NO LOADER |
| **Decision Audit** | `logs/decision_audit/{SYMBOL}_{DATE}.jsonl` | ❌ NO LOADER |

### Existing Question Registry

25 research questions (Q1–Q25). Status:
- **READY:** Q1, Q19, Q20, Q21, Q22, Q23, Q24, Q25 (8 questions)
- **NOT_IMPLEMENTED:** Q2–Q5, Q6–Q15, Q17–Q18 (14 questions)
- **BLOCKED:** Q10, Q16 (2 questions — awaiting live trade data)
- **DEPRECATED:** 0

---

## 2. Dataset Integration Status

### Complete Inventory (all persistence layers)

| # | Dataset | Local | S3 | Loader | Schema Version | Join Keys |
|---|---------|-------|----|----|------|-----|
| 1 | Opportunities | ✅ | ❌ | ❌ | ✅ `opportunity_v1` | `opportunity_id`, `entity_id`, `cycle_id` |
| 2 | Assessments | ✅ | ✅ | ❌ | ✅ `assessment_v1` | `assessment_id`, `opportunity_id`, `entity_id`, `cycle_id` |
| 3 | Portfolio Rankings | ✅ | ✅ | ❌ | ✅ `portfolio_ranking_v1` | `ranking_id`, `cycle_id`, per-candidate `opportunity_id` |
| 4 | Shadow Comparison | ✅ | ❌ | ❌ | ❌ | `cycle_id`, `runtime_session_id` |
| 5 | Decision Ledger | ✅ | ✅ | ✅ | ❌ | `entity_id`, `cycle_id`, `correlation_id` |
| 6 | Decision Audit | ✅ | ✅ | ❌ | ❌ | `decision_id`, `entity_id`, `correlation_id` |
| 7 | Decision Trace | ✅ | ✅ | ✅ | ❌ | `entity_id`, `cycle_id` |
| 8 | Execution Results | ✅ | ✅ | ❌ | ❌ | `decision_id`, `correlation_id`, `entity_id` |
| 9 | Execution Context | ✅ | ✅ | ❌ | ❌ | `correlation_id`, `symbol` |
| 10 | Protection Audit | ✅ | ❌ | ❌ | ❌ | `correlation_id`, `position_ticket` |
| 11 | Risk Deviation | ✅ | ❌ | ❌ | ❌ | `trade_id`, `correlation_id` |
| 12 | Shadow Trades | ✅ | ✅ | ✅ | ❌ | `correlation_id`, `cycle_id` |
| 13 | Trade Truth | ✅ | ✅ | ✅ | ✅ `trade_truth_v3` | `trade_id`, `correlation_id` |
| 14 | Trade Journal | ✅ | ❌ | ❌ | ❌ | `trade_id`, `correlation_id` |

---

## 3. Join Chain Validation

### Target Chain

```
Opportunity → Assessment → Portfolio Ranking → Decision → Execution → Trade Truth
```

### Join Key Availability

| Join | Key | Left Has? | Right Has? | Works? |
|------|-----|-----------|------------|--------|
| Opportunity → Assessment | `opportunity_id` | ✅ | ✅ | ✅ |
| Assessment → Ranking | `opportunity_id` (on candidates) | ✅ | ✅ | ✅ |
| Ranking → Decision | `cycle_id` | ✅ | ✅ | ✅ |
| Decision → Execution Results | `decision_id` + `correlation_id` | ✅ (audit) | ✅ | ✅ |
| Execution Results → Trade Truth | `correlation_id` | ✅ | ✅ | ✅ |
| Trade Truth → Risk Deviation | `trade_id` | ✅ | ✅ | ✅ |
| Shadow Comparison → Ranking | `cycle_id` | ✅ | ✅ | ✅ |

### Broken/Weak Joins

| Join | Issue | Severity |
|------|-------|----------|
| Opportunity → Decision Ledger | Ledger has `entity_id` but NOT `opportunity_id` | LOW (joinable via entity_id) |
| Assessment → Decision Ledger | Ledger has no `assessment_id` | LOW (joinable via entity_id + cycle_id) |
| Ranking → Execution | Ranking has `cycle_id`, execution has `correlation_id` — no direct join | MEDIUM (requires hop through decision_audit) |
| Shadow Comparison → Trade Truth | No direct link — must join via executed_symbols → trade_journal → correlation_id | MEDIUM |
| Protection Audit → Opportunity | No shared key except `correlation_id` (only on EXECUTED) | LOW |

### Orphan Risk

| Dataset | Can Be Orphaned? | When |
|---------|-----------------|------|
| Opportunity | No — always created on pattern detection | — |
| Assessment | Rare — only if engine exception during assessment | Engine crash |
| Ranking | No — created every cycle with candidates | — |
| Decision | No — always finalized via DecisionRecorder | — |
| Shadow Comparison | No — computed from ranking + execution | — |
| Trade Truth | Rare — only if journal write fails | Persistence error |

---

## 4. Existing Questions Classification

### A) Already Answerable (8 questions)

| ID | Question | Data Sources |
|----|----------|-------------|
| Q1 | Which scoring components predict actual R-multiples? | decision_trace + shadow_trades |
| Q19 | What is the system's true edge (EV)? | shadow_trades |
| Q20 | Is score calibrated to observed outcomes? | decision_trace + shadow_trades |
| Q21 | Does calibrated probability improve EV decisions? | shadow_trades + decision_trace |
| Q22 | What EV threshold maximises expectancy? | shadow_trades |
| Q23 | Which regimes actually produce edge? | decision_trace + shadow_trades |
| Q24 | Which strategies contain real expectancy? | decision_trace + shadow_trades |
| Q25 | Where does the bot perform best? | shadow_trades |

### B) Now Answerable Because Of New Datasets (7 questions)

| ID | Question | Previously Blocked By | Now Available From |
|----|----------|----------------------|-------------------|
| Q3 | Missed opportunity cost by terminal stage | Only had decision_trace | + Opportunity (shows what was available) |
| Q5 | Patterns degrading over time | Needed historical assessment | + Assessment (pattern × score over time) |
| Q9 | Best spread/volatility conditions | Needed execution details | + Execution Results (slippage, latency) |
| Q10 | Guard rejections improving performance? | Needed shadow projection | + Shadow Comparison (disagreement analysis) |
| Q11 | True slippage model per symbol | Needed execution_results loader | + Execution Results loader (to be created) |
| Q13 | Optimal trade duration | Only had shadow | + Risk Deviation (actual holding time) |
| Q16 | Shadow vs live correlation | Needed live trades with identity | + Trade Truth (22+ trades with correlation_id) |

### C) Still Missing Data (5 questions)

| ID | Question | Still Needs |
|----|----------|-------------|
| Q2 | Optimal threshold by regime | Needs 100+ live trades per regime (insufficient data) |
| Q4 | Confidence calibration (live) | Needs 100+ live trades (only 22 exist) |
| Q14 | Causal chains for best trades | Needs trade_truth_graph populated (offline only) |
| Q15 | How does the engine learn? | Learning dataset offline only |
| Q17 | Market conditions preceding drawdowns | Needs longer runtime history |

---

## 5. New Portfolio Intelligence Questions

### Q26–Q35: Portfolio Ranking Research (NEW)

| ID | Priority | Question | Data Sources |
|----|----------|----------|-------------|
| Q26 | P0 | Did the ranker identify the best opportunity? | portfolio_rankings + trade_truth |
| Q27 | P0 | If ranking had authority, would results improve? | shadow_comparison + trade_truth |
| Q28 | P0 | How often did execution disagree with ranking? | shadow_comparison |
| Q29 | P1 | Did correlation penalties improve results? | portfolio_rankings (portfolio_context) + trade_truth |
| Q30 | P1 | Did diversification bonuses improve expectancy? | portfolio_rankings + trade_truth |
| Q31 | P1 | Were outranked opportunities actually worse? | portfolio_rankings + market movement data |
| Q32 | P1 | Does portfolio-aware ranking outperform isolated ranking? | portfolio_rankings (final_rank_score vs original) |
| Q33 | P2 | How many opportunities compete per cycle? | portfolio_rankings (total_candidates) |
| Q34 | P2 | What is the typical rank score distribution? | portfolio_rankings |
| Q35 | P2 | Are there symbols that consistently get outranked? | portfolio_rankings |

---

## 6. Research Pipeline Requirements

### New Loaders Needed (add to `research_engine/data_access/loaders.py`)

| Function | Source | Notes |
|----------|--------|-------|
| `load_opportunities(symbol=None)` | `logs/opportunities/{SYMBOL}/{DATE}.jsonl` | Symbol-partitioned |
| `load_assessments(symbol=None)` | `logs/assessments/{SYMBOL}/{DATE}.jsonl` | Symbol-partitioned |
| `load_portfolio_rankings()` | `logs/portfolio_rankings/{DATE}.jsonl` | Date-partitioned only |
| `load_shadow_comparisons()` | `logs/portfolio_shadow/{DATE}.jsonl` | Date-partitioned only |
| `load_execution_results(symbol=None)` | `logs/execution_results/{SYMBOL}/{DATE}.jsonl` | Symbol-partitioned |
| `load_execution_context(symbol=None)` | `logs/execution_context/{SYMBOL}/{DATE}.jsonl` | Symbol-partitioned |
| `load_protection_audit(symbol=None)` | `logs/protection_audit/{SYMBOL}/{DATE}.jsonl` | Symbol-partitioned |
| `load_risk_deviation(symbol=None)` | `logs/risk_deviation/{SYMBOL}/{DATE}.jsonl` | Symbol-partitioned |
| `load_decision_audit(symbol=None)` | `logs/decision_audit/{SYMBOL}_{DATE}.jsonl` | Symbol-partitioned (underscore) |

### Join Utilities Needed

```python
def join_opportunity_to_outcome(opportunities, trade_truth) -> list[dict]:
    """Join opportunities to their eventual outcome via entity_id → correlation_id chain."""

def join_ranking_to_outcomes(rankings, trade_truth) -> list[dict]:
    """Join ranking selections to trade outcomes for the SELECTED candidate."""

def compute_counterfactual_ranking(rankings, market_data) -> list[dict]:
    """For OUTRANKED candidates, compute what WOULD have happened."""
```

### New Experiments Needed

| Experiment | Inputs | Output |
|-----------|--------|--------|
| `ranking_accuracy` | portfolio_rankings + trade_truth | % of times best rank = best outcome |
| `shadow_authority_backtest` | shadow_comparison + trade_truth | R-multiple improvement if authority active |
| `portfolio_context_value` | rankings (with/without context) + outcomes | Does correlation penalty improve returns? |

---

## 7. Missing Research Components

| Component | Current State | Needed |
|-----------|-------------|--------|
| Loaders for new datasets | ❌ None exist | 9 new loader functions |
| Join utilities | ❌ None exist | 3 join helper functions |
| Portfolio Intelligence questions | ❌ Not registered | 10 new questions (Q26–Q35) |
| Athena tables for new datasets | ❌ Not provisioned | DDL for opportunities, assessments, rankings |
| Cross-dataset report builder | ⚠️ Exists (report_builder.py) | Extend to include new datasets |
| Counterfactual simulator | ⚠️ Partial (counterfactual/ folder exists) | Connect to Opportunity + shadow data |

---

## 8. Recommended Next Implementation

### Priority Order

1. **Add 9 new loaders** (1 hour) — unlocks all downstream research
2. **Register Q26–Q35** in question_registry.py (30 min)
3. **Implement Q27** (shadow authority backtest) — highest-value single question
4. **Implement Q26** (ranking accuracy) — validates ranking before giving authority
5. **Add join utilities** (1 hour) — enables cross-dataset analysis

### Effort Estimate

| Task | Effort | Value |
|------|--------|-------|
| 9 loaders | 1 hour | Unlocks all research |
| 10 questions registered | 30 min | Defines research agenda |
| Q27 implementation | 2 hours | Validates ranking authority decision |
| Q26 implementation | 2 hours | Measures ranking quality |
| Join utilities | 1 hour | Enables lifecycle queries |
| **Total** | **~7 hours** | **Full intelligence chain researchable** |

---

## 9. Final Answer

**"Can the Research Engine now explain the complete path from market opportunity to capital allocation to realised outcome?"**

**NOT YET — but the data exists.** The persistence layer captures the entire chain:

```
Opportunity (logs/opportunities/) → Assessment (logs/assessments/) →
Portfolio Ranking (logs/portfolio_rankings/) → Shadow Comparison (logs/portfolio_shadow/) →
Decision (logs/decision_ledger/ + logs/decision_trace/) →
Execution (logs/execution_results/) → Protection (logs/protection_audit/) →
Trade Truth (logs/trade_truth/) → Risk Deviation (logs/risk_deviation/)
```

What's missing is the **access layer** — the Research Engine has loaders for only 4 of 14 datasets. Adding 9 loader functions and 3 join utilities (total ~2 hours of implementation) would give the Research Engine complete visibility into the intelligence chain.

The architecture is **research-ready**. The join keys exist on every dataset. The data is being collected in production. The only gap is the plumbing to read and join it.
