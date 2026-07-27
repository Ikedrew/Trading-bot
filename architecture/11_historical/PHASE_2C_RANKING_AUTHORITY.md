# PHASE 2C-PART 3: RANKING AUTHORITY MIGRATION

**Date:** 2026-07-23
**Status:** Shadow comparison implemented. Authority NOT yet active.
**Config:** `PORTFOLIO_RANKING_AUTHORITY = False`, `PORTFOLIO_RANKING_SHADOW_LOG = True`

---

## 1. Old Architecture (Current — Unchanged)

```
FOR EACH symbol (fixed order: EURUSD → GBPUSD → ... → NZDUSD):
  │
  ├── engine evaluates → produces EXECUTE or NO_TRADE
  │
  ├── IF EXECUTE:
  │     ├── prepare_execution (generates correlation_id, decision_id)
  │     ├── guard chain evaluation
  │     ├── execution_orchestrator → broker fill
  │     └── position registration
  │
  └── NEXT symbol (may also execute independently)

POST-LOOP:
  ranking = rank_candidates(all_cycle_candidates)   ← passive observation
  persist_portfolio_ranking(ranking)                 ← persisted (Phase 2C-Part1)
  shadow_comparison(ranking vs actual)              ← NEW (Phase 2C-Part3)
```

**Problem:** First-come-first-served. EURUSD always gets priority. Multiple symbols can execute in the same cycle without comparative evaluation.

---

## 2. New Architecture (Target — Not Yet Active)

```
PASS 1: EVALUATE ALL SYMBOLS (no execution)
  FOR EACH symbol:
    engine evaluates → collects EXECUTE candidates

PASS 2: RANK + SELECT
  ranking = rank_candidates(all_candidates)
  selected = ranking.selected                       ← portfolio authority

PASS 3: EXECUTE SELECTED ONLY
  FOR selected candidate:
    prepare_execution → guards → broker fill
  FOR others:
    log as OUTRANKED (with reason)
```

**Benefit:** Best opportunity gets capital. Others logged for research.

---

## 3. Migration Steps

### Step 1: Shadow Comparison (DONE — Current Phase)

**What:** After the existing loop executes trades normally, the shadow comparison logs what ranking WOULD have chosen differently.

**Files:**
- `core/portfolio_ranking/shadow_comparison.py` — comparison logic
- `core/config.py` — `PORTFOLIO_RANKING_SHADOW_LOG = True`
- `core/runtime/live_scanner.py` — integration point

**Outcome:** Research data accumulates showing agreement/disagreement rate.

### Step 2: Validation Period (NEXT — Data Collection)

**What:** Run for 100+ cycles with shadow logging. Analyse:
- How often does ranking disagree with actual execution?
- When it disagrees, does the ranking's selection produce better outcomes?
- What is the expected improvement from authority activation?

**Criteria for activation:**
- Disagreement rate > 10% (ranking has material opinion)
- Ranking's selections outperform actual executions by > 0.2R average
- No false-negative cases (ranking blocking a clearly profitable trade)

### Step 3: Authority Activation (FUTURE — Requires Loop Restructure)

**What:** Set `PORTFOLIO_RANKING_AUTHORITY = True`. Add pre-execution check inside the per-symbol loop:

```python
# Inside execution path (future implementation):
if config.PORTFOLIO_RANKING_AUTHORITY:
    if sym_state.symbol != _opp_pool.selected.symbol:
        # Log OUTRANKED, skip execution
        continue
```

**Risk:** Moderate. Requires careful handling of:
- Guard chain interactions (guards may block the selected symbol)
- Fallback when selected symbol fails execution
- Multiple-slot scenarios (MAX_OPEN_POSITIONS > 1)

---

## 4. Risk Assessment

| Risk | Severity | Mitigation |
|------|----------|-----------|
| Authority blocks profitable trades | HIGH | Shadow data validates before activation |
| Loop restructure breaks execution | HIGH | Deferred — shadow comparison proves value first |
| Ranking selects symbol that fails guards | MEDIUM | Fallback to next-ranked eligible candidate |
| Shadow comparison misidentifies executed symbols | LOW | Multiple detection methods (EXECUTE action + position open time) |
| Config accidentally enabled | LOW | Default False, requires deliberate change |

---

## 5. Validation Evidence Required

Before setting `PORTFOLIO_RANKING_AUTHORITY = True`, the following must be demonstrated from shadow logs:

| Question | Required Answer | Data Source |
|----------|----------------|-------------|
| How often does ranking disagree? | > 10% of multi-candidate cycles | `logs/portfolio_shadow/` |
| Does ranking's selection outperform? | > 0.2R improvement average | Shadow selection → join to trade_truth |
| Does ranking avoid bad trades? | Selected trades have fewer CRITICAL risk deviations | Shadow + risk_deviation join |
| Are outranked trades actually worse? | Outranked symbols have lower R when tracked | Market movement after outranked opportunities |

---

## 6. Files Created/Modified

| File | Type | Purpose |
|------|------|---------|
| `core/portfolio_ranking/shadow_comparison.py` | NEW | Computes agreement/disagreement between execution and ranking |
| `core/config.py` | MODIFIED | Added `PORTFOLIO_RANKING_AUTHORITY` + `PORTFOLIO_RANKING_SHADOW_LOG` |
| `core/runtime/live_scanner.py` | MODIFIED | Integrated shadow comparison after ranking persistence |
| `tests/test_portfolio_shadow_comparison.py` | NEW | 14 tests covering all comparison scenarios |

---

## 7. Shadow Comparison Data Model

```json
{
  "cycle_id": 4578,
  "runtime_session_id": "abc123",
  "compared_at_utc": "2026-07-23T12:30:50.123Z",
  "actual_executed_symbols": ["NZDUSD"],
  "actual_execution_count": 1,
  "ranking_selected_symbol": "GBPUSD",
  "ranking_selected_rank_score": 0.000142,
  "agreement": false,
  "disagreement_type": "WRONG_SYMBOL",
  "disagreement_detail": "Executed NZDUSD but ranking recommends GBPUSD (rank_score=0.00014200)",
  "total_candidates": 3,
  "eligible_candidates": 2,
  "outranked_symbols": ["NZDUSD"]
}
```

**Storage:** `logs/portfolio_shadow/{YYYY-MM-DD}.jsonl`

**Filtering:** Only persists disagreements or multi-candidate cycles (reduces noise).

---

## 8. Activation Recommendation

**DO NOT activate authority yet.**

The system needs:
1. **50+ shadow comparison records** with disagreements to analyse
2. **Market movement data** after ranking disagreements to prove ranking's recommendations would have been better
3. **Confidence that fallback behaviour** handles edge cases (guard failures, broker rejections of selected symbol)

**Estimated timeline to activation:** 1-2 weeks of live shadow data collection, followed by analysis, followed by implementation of the pre-execution gate.

---

## 9. Current Behaviour Confirmation

| Aspect | Status |
|--------|--------|
| Execution logic | ✅ UNCHANGED |
| Risk management | ✅ UNCHANGED |
| Strategy selection | ✅ UNCHANGED |
| Guard chain | ✅ UNCHANGED |
| Symbol loop order | ✅ UNCHANGED |
| Position limits | ✅ UNCHANGED |
| Ranking authority | ❌ NOT ACTIVE (shadow only) |
| Shadow logging | ✅ ACTIVE (observational) |
| Tests passing | ✅ 145 tests pass |

**Trading behaviour is identical to before this phase.**
