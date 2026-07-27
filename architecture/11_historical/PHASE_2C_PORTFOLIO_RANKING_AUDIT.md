# PHASE 2C: PORTFOLIO RANKING AUDIT

**Date:** 2026-07-23
**Question:** Does the current system intelligently choose between opportunities, or does it only decide whether one opportunity is tradable?
**Answer:** The ranking logic EXISTS and is fully implemented, but it runs **after execution** (passive observation only). The system currently does NOT choose between opportunities — it evaluates and executes them sequentially. The ranking output is ephemeral (printed, never persisted).

---

## 1. Current Ranking Architecture

### The Ranker Exists

**Module:** `core/pipeline/opportunity_ranker.py`

It is fully functional:
- Accepts all engine results from a cycle
- Ranks by `EV × market_state_multiplier`
- Assigns SELECTED / OUTRANKED / BLOCKED status
- Produces `OpportunityPool` with sorted candidates
- Has a narrative formatter for console output
- Has a `to_log_dict()` method ready for persistence

### Where It Runs

```
FOR EACH symbol in states:           ← Sequential execution loop
  engine_result = run_new_engine(...)
  if EXECUTE: execute_trade(...)      ← Execution happens HERE (inside loop)
  _cycle_candidates.append(result)

# AFTER ALL SYMBOLS PROCESSED:
_opp_pool = rank_candidates(_cycle_candidates)  ← Ranking happens HERE (post-loop)
print(format_ranking_narrative(_opp_pool))       ← Output printed, never persisted
```

### The Fundamental Problem

Ranking runs AFTER execution. By the time the ranker identifies the best opportunity, trades have already been placed on potentially inferior opportunities that happened to be evaluated first in the loop.

---

## 2. Responsibility Assessment

### What the Ranker Currently Owns (CORRECT)

| Responsibility | Status |
|---------------|--------|
| Cross-symbol comparison | ✅ Compares all candidates in same cycle |
| Rank score computation | ✅ EV × market_state_multiplier |
| Priority ordering | ✅ Sorted by rank_score descending |
| Selection status assignment | ✅ SELECTED / OUTRANKED / BLOCKED |
| Narrative formatting | ✅ Human-readable ranking summary |
| Pool data structure | ✅ OpportunityPool with to_log_dict() |

### What the Ranker Does NOT Own (CORRECT)

| Not Owned | Where It Lives |
|-----------|---------------|
| Raw detection | signal_orchestrator.py |
| Pattern identification | patterns/registry.py |
| Probability calculation | probability_estimator.py |
| Broker execution | execution_orchestrator.py |
| Trade outcome | trade_truth.py |

### What's WRONG: The Ranker Has No Authority

The ranker's output (`pool.selected`) is never read by any execution code. It produces the correct answer ("GBPUSD should be selected over NZDUSD") but the system ignores it. Execution happened before ranking.

---

## 3. Current Data Flow

```
CURRENT (sequential, no selection):

  EURUSD: engine → EXECUTE → fills immediately
  GBPUSD: engine → EXECUTE → fills immediately (even if EURUSD was inferior)
  NZDUSD: engine → EXECUTE → fills immediately
  [all done]
  ranker: "GBPUSD was best" → printed, discarded

TARGET (ranked, selective):

  EURUSD: engine → candidate collected
  GBPUSD: engine → candidate collected
  NZDUSD: engine → candidate collected
  ranker: "GBPUSD ranked #1" → GBPUSD executes
  others: OUTRANKED → logged with reason
```

### Evidence: Multiple Opportunities Evaluated Together

From the Portfolio Selection Audit (docs/PHASE_2_PORTFOLIO_SELECTION_AUDIT.md):
- Cycle 1 (July 22): 6 EXECUTE decisions, 5 fills
- Cycle 449: 2 competing at score 7
- Cycle 4578: 2 competing at score 6

The ranker DOES see multiple candidates. It correctly ranks them. But ranking happens too late.

### Are Rejected Alternatives Preserved?

| Question | Answer |
|----------|--------|
| Does the ranker identify alternatives? | ✅ Yes (OUTRANKED status) |
| Are alternatives persisted? | ❌ NO — `_opp_pool` is a local variable, printed then discarded |
| Can we historically query "what was outranked"? | ❌ NO — no persistence |
| Is the ranking score recorded? | ❌ NO — only printed to console |

---

## 4. Persistence Requirements (If First-Class Dataset)

| Criterion | Current Status | Target |
|-----------|---------------|--------|
| `schema_version` | ❌ Absent | `"portfolio_ranking_v1"` |
| `dataset_version` | ❌ Absent | `"2026.1"` |
| Local persistence | ❌ Absent (printed only) | `logs/portfolio_ranking/{DATE}.jsonl` |
| S3 mirror | ❌ Absent | `portfolio_ranking/date={YYYY-MM-DD}/part-000.jsonl` |
| Hive partitioning | ❌ Absent | `date={D}/` (not per-symbol — ranking is cross-symbol) |
| Join keys | ⚠️ `cycle_id` exists on pool | Need: `ranking_id`, `cycle_id`, `runtime_session_id` |
| Dataset ownership | ✅ Module exists | `core/pipeline/opportunity_ranker.py` |
| Research use cases | ✅ Multiple identified | See Section 6 |

---

## 5. Proposed Portfolio Ranking Schema

```python
@dataclass
class PortfolioRankingRecord:
    """One complete ranking event — all candidates from one cycle."""

    # ─── VERSION ──────────────────────────────────────────────────────
    schema_version: str = "portfolio_ranking_v1"
    dataset_version: str = "2026.1"

    # ─── IDENTITY ─────────────────────────────────────────────────────
    ranking_id: str              # Unique: f"ranking_{cycle_id}_{timestamp_ms}"
    cycle_id: int
    runtime_session_id: str
    ranked_at_utc: str           # ISO timestamp

    # ─── POOL SUMMARY ─────────────────────────────────────────────────
    total_candidates: int
    eligible_count: int
    selected_symbol: str         # Winner (or "" if none)
    selected_rank_score: float

    # ─── CANDIDATES ───────────────────────────────────────────────────
    candidates: list[dict]
    # Each candidate contains:
    #   symbol, pattern, strategy, strategy_confidence,
    #   score_neutral, score_strategy, ev, rr_effective,
    #   market_state, rank_score, rank_position,
    #   eligible, block_reason, selection_status,
    #   opportunity_id, assessment_id

    # ─── PORTFOLIO CONTEXT ────────────────────────────────────────────
    open_positions_at_ranking: int
    available_slots: int          # MAX_OPEN_POSITIONS - current open
    correlation_groups_active: list[str]
```

### Partition Strategy

Portfolio ranking is a **cross-symbol** event (one record per cycle, covering ALL symbols). Partitioning by date only:

```
logs/portfolio_ranking/{YYYY-MM-DD}.jsonl           (local)
s3://trading-bot-data-mk1/portfolio_ranking/date={YYYY-MM-DD}/part-000.jsonl  (S3)
```

NOT per-symbol — because ranking compares across symbols.

---

## 6. Research Questions Enabled

| Question | Answerable With Ranking Dataset? |
|----------|------|
| Did we select the best available opportunity? | ✅ Compare `selected` vs rank #2+ on same cycle |
| Did correlation management improve results? | ✅ Check if blocked candidates (correlation) later would have profited |
| Were higher-ranked opportunities actually better? | ✅ Join ranking to trade_truth on correlation_id — compare R-multiples by rank |
| Did the system allocate capital efficiently? | ✅ Sum EV of selected vs sum EV of rejected |
| Were profitable opportunities ignored? | ✅ Join OUTRANKED candidates to price movement data |
| How often do multiple symbols compete? | ✅ `WHERE total_candidates > 1` |
| What % of cycles have eligible candidates? | ✅ `WHERE eligible_count > 0` / total cycles |
| Does symbol loop order create bias? | ✅ Compare rank_position of executed vs actual best |

---

## 7. Implementation Recommendation

### Phase 2C Implementation (3 parts)

**Part 1: Persist Current Ranking Output (immediate, no behaviour change)**

Add persistence to the existing post-cycle ranking block in live_scanner.py:

```python
# Current (line ~1067):
if _cycle_candidates:
    _opp_pool = rank_candidates(_cycle_candidates)
    print(format_ranking_narrative(_opp_pool))

# Add:
    persist_portfolio_ranking(_opp_pool, runtime_session_id=_runtime_session_id)
```

This requires:
- A `persist_portfolio_ranking()` function (local JSONL + S3 mirror)
- Adding `schema_version`, `dataset_version`, `ranking_id`, `runtime_session_id` to the pool output
- NO change to execution logic

**Effort:** 1-2 hours. **Risk:** Zero (additive logging only).

**Part 2: Enrich Ranking With Portfolio Context (observational)**

Add to the ranking record:
- `open_positions_at_ranking` — how many positions were open when ranking occurred
- `available_slots` — MAX_OPEN_POSITIONS - open count
- `correlation_groups_active` — which correlation groups have exposure
- `opportunity_id` + `assessment_id` on each candidate

**Effort:** 1 hour. **Risk:** Zero.

**Part 3: Activate Ranking Authority (structural change — future phase)**

Move execution from inside the per-symbol loop to AFTER ranking:
1. Collect all candidates (no execution during loop)
2. Rank cross-symbol
3. Execute only `pool.selected` (top-K based on available slots)
4. Mark others as OUTRANKED in Opportunity + Decision records

**Effort:** 4-6 hours. **Risk:** Moderate (changes execution ordering). Requires extensive testing.

### Recommended Sequence

```
Phase 2C-Part1: Persist ranking (NOW — zero risk, enables research)
    ↓
Phase 2C-Part2: Enrich with portfolio context (NOW — zero risk)
    ↓
Phase 2C-Part3: Activate authority (LATER — after sufficient data collected to validate)
```

---

## 8. Gap Summary

| Component | Status | Action Needed |
|-----------|--------|---------------|
| Ranking algorithm | ✅ EXISTS (EV × market_state) | None |
| Cross-symbol comparison | ✅ EXISTS (rank_candidates) | None |
| Data structures | ✅ EXISTS (RankedCandidate, OpportunityPool) | None |
| Selection status | ✅ EXISTS (SELECTED/OUTRANKED/BLOCKED) | None |
| to_log_dict() serialization | ✅ EXISTS | None |
| Narrative formatting | ✅ EXISTS | None |
| **Local persistence** | ❌ MISSING | Create persist function |
| **S3 mirror** | ❌ MISSING | Standard pattern |
| **schema_version** | ❌ MISSING | Add to output |
| **Execution authority** | ❌ MISSING (passive only) | Phase 2C-Part3 (future) |
| **Join keys to Opportunity/Assessment** | ❌ MISSING | Add opportunity_id, assessment_id |
| **Portfolio context** (open positions, slots) | ❌ MISSING | Add to ranking record |

---

## 9. Final Answer

**"Does the current system intelligently choose between opportunities, or does it only decide whether one opportunity is tradable?"**

**The system only decides whether ONE opportunity is tradable.** It processes symbols sequentially and executes each one independently. The ranking engine correctly identifies which opportunity SHOULD have been selected — but it runs too late (post-execution, passive observation only).

The ranking logic is **complete and correct**. What's missing is:
1. **Persistence** — ranking output is ephemeral (printed, discarded)
2. **Authority** — ranking does not gate execution (passive overlay)
3. **Portfolio context** — no awareness of open positions at ranking time

The immediate next step is **persistence** (Phase 2C-Part1) — capturing the ranking dataset so we can prove the value of the ranking logic before giving it execution authority.
