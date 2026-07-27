# PHASE 2: OPPORTUNITY LAYER DESIGN

**Date:** 2026-07-23
**Objective:** Design the Opportunity abstraction — separating market intelligence ("what exists") from trade decisions ("what to do about it").
**Scope:** Architecture audit and design recommendation. No code changes.

---

## 1. Current Architecture Findings

### The Candidate Creation Chain (as-is)

```
bar_provider.fetch_bar()
  → candles[], closed_i
    │
    ▼
pre_engine_gates.evaluate_pre_engine_gates()
  → evaluate_closed_bar(candles, closed_i)           [patterns/registry.py]
    → detect_all() runs ALL registered PatternDetector instances
    → Returns list[Signal] (0 to N signals per bar)
    │
    ├── If 0 signals → PATTERN_REJECT (bar discarded entirely)
    └── If 1+ signals → raw_patterns passed to engine
          │
          ▼
run_new_engine(detected_patterns=raw_patterns)
  → _select_best_pattern(patterns)                   [picks ONE, discards rest]
    → run_strategy_activation()                      [classifies strategy type]
    → H1 BOS structural gate                         [hard reject before assessment]
    → _compute_all_scores()                          [10-factor scoring]
    → MarketStateEngine.evaluate()                   [state classification]
    │
    ▼
  OpportunityAssessment constructed                  [frozen analysis snapshot]
    │
    ▼
  compute_execution_policy()                         [EV gate, market state gate]
    → RiskManager._execute_risk()                    [SL/TP/sizing → OrderIntent]
    → compute_expected_value()                       [probability estimation]
    │
    ▼
  Return {"action": "EXECUTE", "intent": OrderIntent} or {"action": "NO_TRADE"}
```

### Where "Opportunity" Currently Lives Implicitly

| Current Object | Closest To | Missing From True Opportunity |
|---------------|-----------|-------------------------------|
| `Signal` | Raw detection event | No market context, no evidence, no confidence beyond pattern |
| `OpportunityAssessment` | Assessed opportunity | Created too late (after gates discard), per-pattern only, includes strategy decisions |
| `_cycle_candidates[]` | Opportunity pool | Built from engine outputs (post-decision), not from observations |
| `OpportunityPool` (ranker) | Portfolio ranking | Runs after execution, passive only |

### Key Insight

**The system does not have a distinct Opportunity object.** What it has is a `Signal` (too raw) and an `OpportunityAssessment` (too decision-laden). The gap between them is where the Opportunity layer should live.

---

## 2. Where Opportunity Creation Currently Happens

### Current: Pattern Detection (pre_engine_gates.py, line ~127)

```python
_raw_patterns = _detect_patterns(candles, closed_i)
```

This is the moment the market says "something interesting happened." But:
- No persistence
- No context attachment
- No lifecycle tracking
- Immediately discarded if engine rejects

### Current: Inside run_new_engine (after scoring)

```python
_opportunity = OpportunityAssessment(
    symbol=symbol, pattern=authoritative_pattern, ...
)
```

This is the closest to an Opportunity object, but it's created:
- After pattern selection (only 1 of N patterns)
- After strategy classification (a decision, not observation)
- After H1 BOS gate (rejected opportunities never get one)
- Inside the engine (coupled to execution flow)

### What Gets Discarded Before Opportunity Exists

| Information Lost | Where | Why |
|-----------------|-------|-----|
| Bars with no M5 pattern | pre_engine_gates | Pattern gate hard-rejects |
| Alternative patterns on same bar | _select_best_pattern | Only 1 selected, rest silently dropped |
| Swing-blocked opportunities | H1 BOS gate | Returns NO_TRADE before assessment created |
| Context-rich but patternless setups | Pattern gate | System requires M5 candlestick pattern as entry ticket |

---

## 3. Current Problems / Gaps

### Problem 1: Information Loss Before Portfolio Evaluation

The portfolio ranker (`opportunity_ranker.py`) only sees candidates that survived all gates. If EURUSD had a swing-blocked TWEEZER_TOP and NZDUSD had a weak THREE_BLACK_CROWS that passed all gates, the portfolio layer never knows the EURUSD opportunity existed. It cannot compare quality.

### Problem 2: Single Pattern Per Bar Per Symbol

When multiple patterns fire (e.g., TWEEZER_TOP + EVENING_STAR + HANGING_MAN all on the same GBPUSD bar), only one proceeds. The system cannot evaluate whether a different pattern + strategy combination would produce a better opportunity.

### Problem 3: Discovery Coupled To Decision

Pattern detection, scoring, risk evaluation, and execution all happen in one synchronous call (`run_new_engine`). There is no pause between "I found something" and "should I trade it?" The system cannot accumulate opportunities over time or compare across bars.

### Problem 4: No Opportunity Persistence

Detected opportunities are ephemeral — they exist only as function-local variables during one cycle iteration. There is no storage layer for:
- "What opportunities were available this hour?"
- "How many opportunities did we reject vs execute?"
- "What was the best opportunity we missed?"

### Problem 5: Symbol Loop Order Determines Winner

Because opportunities are processed sequentially (EURUSD first, NZDUSD last), and execution happens inline, the portfolio layer cannot perform true cross-symbol comparison before committing capital.

### Problem 6: Decision Layers Cannot Be Independently Tested

Scoring, risk, and EV are all computed inside `run_new_engine()`. You cannot test "given this opportunity, what does the risk layer say?" without running the full engine pipeline.

---

## 4. Recommended Opportunity Schema

### Design Principles

1. An Opportunity is NOT a trade — it represents "the market presented something"
2. No execution fields (SL, TP, volume, order type)
3. No final decision fields (should_trade, block_reason)
4. Immutable after creation (enrichment creates new versions, never mutates)
5. Every detected opportunity is persisted (even if later rejected)

### Schema

```python
@dataclass(frozen=True)
class Opportunity:
    """
    A market observation that may warrant a trading decision.
    
    Created at detection time. Enriched by assessment.
    Never contains execution parameters or final decisions.
    """

    # ═══════════════════════════════════════════════════════════════════
    # A) MARKET OBSERVATION — What was seen
    # ═══════════════════════════════════════════════════════════════════

    # Identity
    opportunity_id: str              # Unique ID: f"{symbol}_{bar_time}_{pattern}"
    symbol: str                      # Trading pair
    direction: str                   # "BUY" or "SELL" (implied by pattern)
    
    # What triggered this opportunity
    pattern: str                     # Pattern name (e.g., "TWEEZER_TOP")
    pattern_confidence: float        # Pattern detector confidence (0.0–1.0)
    
    # When
    detected_at_bar_time: int        # Unix seconds of the bar that triggered detection
    detected_at_utc: str             # ISO timestamp of detection moment
    
    # Source timeframe
    detection_timeframe: str         # "M5" (currently always M5)
    
    # Raw candle context (the trigger bar)
    trigger_candle_open: float
    trigger_candle_high: float
    trigger_candle_low: float
    trigger_candle_close: float

    # ═══════════════════════════════════════════════════════════════════
    # B) EVIDENCE — Supporting/contradicting context
    # ═══════════════════════════════════════════════════════════════════

    # Structure alignment (from HTF context)
    h4_regime: str                   # "TRENDING" | "RANGE" | "TRANSITIONAL"
    h4_regime_confidence: float      # 0.0–1.0
    h1_direction: str                # "BULLISH" | "BEARISH" | "NEUTRAL"
    h1_bos_confirmed: bool           # H1 break-of-structure seen
    h1_swing_structure: str          # "HH_HL" | "LH_LL" | "MIXED"
    m15_structure_quality: float     # 0.0–1.0

    # Trend alignment
    trend_direction: str             # "ALIGNED" | "OPPOSING" | "NEUTRAL"
    trend_source: str                # "H1_PHASE" | "M5_EMA50"
    
    # Market context
    market_state: str                # "STRUCTURED" | "TRANSITIONAL" | "CHOP"
    volatility_quality: float        # 0.0–1.0 (directional quality of recent price action)
    chop_clarity: float              # 0.0–1.0 (inverse of noise)
    
    # Bias alignment
    bias_direction: str              # Current bias FSM state
    bias_phase: str                  # "BUILDING" | "CONFIRMED" | "EXPIRED"
    bias_alignment: float            # 0.0–1.0 (pattern direction vs bias)

    # ═══════════════════════════════════════════════════════════════════
    # C) CONFIDENCE — Quantified strength
    # ═══════════════════════════════════════════════════════════════════

    # Composite confidence (derived from evidence)
    overall_confidence: float        # 0.0–1.0 (weighted combination of evidence)
    
    # Component scores (the 10-factor breakdown)
    evidence_scores: dict[str, float]  # All scoring components (each 0.0–1.0)
    
    # Uncertainty measure
    uncertainty: float               # 0.0–1.0 (how ambiguous is this opportunity?)

    # ═══════════════════════════════════════════════════════════════════
    # D) METADATA — Lifecycle tracking
    # ═══════════════════════════════════════════════════════════════════

    # Lifecycle
    state: str                       # DETECTED | ASSESSED | RANKED | APPROVED | REJECTED | EXECUTED | EXPIRED
    
    # Sibling awareness
    sibling_patterns: tuple[str, ...]  # Other patterns detected on same bar (for context)
    cycle_id: int                    # Which scan cycle produced this
    
    # Linkage
    entity_id: str                   # For joining to decision_audit, trade_truth
```

### What Is Explicitly NOT On Opportunity

| Field | Why Excluded | Where It Belongs |
|-------|-------------|------------------|
| `sl` | Execution parameter | TradeIntent (created by RiskManager) |
| `tp` | Execution parameter | TradeIntent |
| `volume` | Execution parameter | TradeIntent |
| `should_trade` | Final decision | ExecutionAuthority |
| `ev` | Derived from probability model | Assessment/Decision layer |
| `p_success` | Model output, not observation | Probability layer |
| `block_reason` | Decision outcome | Decision layer |
| `strategy` | Classification decision | Strategy layer (can enrich Opportunity) |

---

## 5. Recommended Opportunity Lifecycle

### States

```
DETECTED
  │ Pattern fires on bar close. Opportunity created with raw observation + evidence.
  │ ALL detected patterns become opportunities (not just the "best").
  │
  ▼
ASSESSED
  │ Engine enriches with scores, strategy classification, confidence.
  │ Opportunity is now fully characterized but no decision made.
  │
  ▼
RANKED
  │ Portfolio layer compares all assessed opportunities across symbols.
  │ Assigns rank_score, position (1st, 2nd, ...), and relative quality.
  │
  ▼
APPROVED ──────── or ──────── REJECTED
  │                              │
  │ Portfolio selects this       │ Outranked, blocked by guard,
  │ for execution attempt.       │ or policy rejection.
  │                              │
  ▼                              ▼
EXECUTED                       (persisted with rejection reason)
  │
  │ Broker fills. Becomes a live position.
  │ Links to trade_truth via entity_id.
  │
  ▼
(tracked via trade lifecycle)
```

### Expiry

An opportunity in DETECTED or ASSESSED state that is not ranked within 1 cycle transitions to EXPIRED. This prevents stale opportunities from accumulating.

### Where Each Transition Happens

| Transition | Owner | Trigger |
|-----------|-------|---------|
| → DETECTED | Pattern Detection Layer | Pattern fires on bar close |
| → ASSESSED | Scoring Engine | Scores computed, evidence attached |
| → RANKED | Portfolio Intelligence | All cycle opportunities compared |
| → APPROVED | Execution Authority | Best opportunity passes all guards |
| → REJECTED | Portfolio / Guards | Outranked, guard block, policy block |
| → EXECUTED | Execution Orchestrator | Broker fill confirmed |
| → EXPIRED | Lifecycle Manager | Not ranked within TTL (1 cycle) |

---

## 6. Recommended Module Ownership

### New Module: `core/opportunity/`

```
core/opportunity/
  ├── __init__.py
  ├── opportunity.py          # Opportunity dataclass + OpportunityState enum
  ├── factory.py              # Creates Opportunity from Signal + context
  ├── pool.py                 # OpportunityPool: per-cycle collection
  └── persistence.py          # JSONL writer: logs/opportunities/{SYMBOL}/{date}.jsonl
```

### Ownership Boundaries

```
LAYER 1: DETECTION (creates Opportunity)
  Owner: pre_engine_gates.py (or new opportunity_scanner)
  Input: candles, closed_i, htf_context
  Output: list[Opportunity] in state=DETECTED
  Rule: ONE Opportunity per (symbol, bar_time, pattern) tuple

LAYER 2: ASSESSMENT (enriches Opportunity)
  Owner: run_new_engine (or extracted assessment module)
  Input: Opportunity in state=DETECTED
  Output: Opportunity in state=ASSESSED (with scores + evidence)
  Rule: NEVER discards an opportunity. Weak scores are information, not rejection.

LAYER 3: PORTFOLIO RANKING (compares across symbols)
  Owner: core/pipeline/opportunity_ranker.py (activated, not passive)
  Input: All ASSESSED opportunities from current cycle
  Output: Ranked pool with APPROVED (top-K) and REJECTED (remainder)
  Rule: Executes BEFORE any broker call. Authority over which opportunity proceeds.

LAYER 4: EXECUTION (commits capital)
  Owner: execution_orchestrator.py
  Input: APPROVED opportunity + guard chain validation
  Output: Broker fill or rejection
  Rule: Only processes APPROVED opportunities. Cannot create its own.
```

### Integration Point (Recommended)

```
Current:
  for sym_state in states:
    patterns = detect(...)
    result = run_engine(patterns, ...)  ← assessment + decision + execution bundled
    if result.action == EXECUTE:
      execute(...)

Proposed:
  # PASS 1: Detect + Assess (all symbols)
  opportunities = []
  for sym_state in states:
    patterns = detect(...)
    for pattern in patterns:                    ← ALL patterns, not just "best"
      opp = create_opportunity(pattern, context)
      opp = assess_opportunity(opp, engine)    ← scoring only, no decision
      opportunities.append(opp)

  # PASS 2: Rank + Select (cross-symbol)
  pool = rank_opportunities(opportunities)     ← existing ranker, now authoritative
  approved = pool.approved[:max_open_slots]

  # PASS 3: Execute (approved only)
  for opp in approved:
    if passes_guards(opp):
      execute(opp)
    else:
      opp.state = REJECTED  # guard failure
```

---

## 7. Migration Plan (No Code Yet)

### Phase 2A: Opportunity Object (observational)

**Goal:** Create Opportunity objects alongside existing flow. Log them. Change nothing about execution.

1. Create `core/opportunity/opportunity.py` with the Opportunity dataclass
2. Create `core/opportunity/factory.py` — builds Opportunity from Signal + HTF context
3. In `pre_engine_gates.py`, after pattern detection, create Opportunity objects for ALL patterns
4. Pass them through the engine (engine still picks best_pattern and runs as before)
5. Persist all opportunities to `logs/opportunities/{SYMBOL}/{date}.jsonl`
6. Existing execution path unchanged

**Risk:** Zero. Purely additive logging.

### Phase 2B: Assessment Extraction

**Goal:** Extract the scoring/classification portion of `run_new_engine` into a standalone assessment function that enriches an Opportunity.

1. Create `core/opportunity/assessor.py` — wraps existing `_compute_all_scores`, `run_strategy_activation`, `MarketStateEngine`
2. The assessor takes an Opportunity and returns an enriched Opportunity (state=ASSESSED)
3. `run_new_engine` continues to exist but internally delegates to the assessor
4. Backward compatible — same inputs/outputs

**Risk:** Low. Internal refactor behind existing interface.

### Phase 2C: Portfolio Authority Activation

**Goal:** Move the ranker from passive post-execution to active pre-execution. Implement the two-pass loop.

1. Restructure live_scanner per-symbol loop: evaluate-all-then-rank-then-execute
2. Connect `rank_candidates()` (already implemented) to execution gate
3. Only APPROVED opportunities proceed to guard chain + execution
4. REJECTED opportunities logged with reason ("outranked_by:{symbol}")
5. Honour MAX_OPEN_POSITIONS as global slot count

**Risk:** Moderate. Changes execution ordering. Requires careful testing.

### Phase 2D: Multi-Pattern Opportunities

**Goal:** Evaluate all detected patterns (not just "best"), allowing the portfolio layer to compare across patterns.

1. Remove `_select_best_pattern` — all patterns proceed as separate opportunities
2. Each gets independently assessed and scored
3. Portfolio layer naturally selects the best (highest rank_score wins)
4. Result: 3 patterns on one bar = 3 opportunities, best one rises to top

**Risk:** Moderate. Increases computation per cycle (N patterns × scoring). May need performance guard.

### Dependency Order

```
Phase 2A (object + logging) → zero risk, immediate value
  ↓
Phase 2B (assessment extraction) → internal refactor
  ↓
Phase 2C (portfolio authority) → execution flow change
  ↓
Phase 2D (multi-pattern) → full opportunity model
```

---

## 8. Risks Of Implementing Incorrectly

### Risk 1: Opportunity Explosion

If every bar × every symbol × every pattern creates an opportunity, the system could generate hundreds per cycle. **Mitigation:** Only create opportunities where at least one pattern fires. Current pattern detection is the quality gate.

### Risk 2: Latency Increase

Two-pass evaluation (assess all → rank → execute) adds latency vs single-pass (assess + execute immediately). **Mitigation:** Assessment is pure computation (~1ms per symbol). The main latency is MT5 API calls, which only happen in execution (unchanged).

### Risk 3: Backwards Compatibility Break

Restructuring the loop could break existing behaviour. **Mitigation:** Phase 2A is purely additive. Phase 2B maintains the same interface. Phase 2C is the only structural change, and it should produce identical results for the common case (single qualified opportunity per cycle).

### Risk 4: Over-Engineering

Creating a full opportunity lifecycle for a system that currently processes 7 symbols on M5 may be premature. **Mitigation:** The schema is minimal. The lifecycle is simple (5 states). The value is immediate (forensic analysis of missed opportunities, proper portfolio selection).

### Risk 5: Opportunity ≠ Signal Confusion

Developers may confuse `Signal` (pattern detection event) with `Opportunity` (market intelligence object). **Mitigation:** Clear naming. Signal stays in `strategy/signals.py`. Opportunity lives in `core/opportunity/`. Signal is an INPUT to Opportunity creation.

---

## 9. How This Supports Future Requirements

| Future Need | How Opportunity Layer Enables It |
|------------|----------------------------------|
| Multiple symbols | Pool collects all symbols → ranks across them |
| Multiple strategies | Each pattern can be assessed under different strategy weights |
| Multiple time horizons | Opportunity carries `detection_timeframe` + HTF evidence → horizon classifier can read it |
| Opportunity ranking | RANKED state + rank_score field designed for this |
| Capital allocation | Portfolio layer sees all opportunities → allocates to highest quality |
| Correlation awareness | Pool contains all symbols → correlation check compares before approval |
| Choosing between competing opportunities | APPROVED/REJECTED lifecycle with reasons built into design |
| Historical analysis | All opportunities persisted → "what did we miss?" queries possible |
| Machine learning | Opportunity + outcome pairs provide clean training data |

---

## 10. Relationship To Existing Components

### What Changes

| Component | Current Role | Future Role |
|-----------|-------------|-------------|
| `Signal` | Detection event → enters engine directly | Detection event → creates Opportunity |
| `OpportunityAssessment` | Analysis snapshot (inside engine) | Absorbed into Opportunity.state=ASSESSED |
| `opportunity_ranker.py` | Passive post-execution logger | Active pre-execution authority |
| `_select_best_pattern()` | Picks one pattern, discards rest | Removed (all patterns become opportunities) |
| `pre_engine_gates.py` | Pattern gate + session guard | Session guard only (pattern gate moves to opportunity creation) |
| `run_new_engine()` | Monolithic: detect+score+decide+risk | Score+decide only (receives assessed Opportunity) |

### What Stays The Same

| Component | Unchanged |
|-----------|-----------|
| Pattern detection (`patterns/registry.py`) | Same detectors, same Signal output |
| Risk Manager (`risk/manager.py`) | Still computes SL/TP/sizing for approved opportunities |
| Execution Orchestrator | Still sends orders to MT5 |
| Trade Management | Still manages open positions |
| HTF Cache | Still provides H4/H1/M15 context |
| Scoring components | Same 10 factors, same weights |

---

## Summary

The system already has 80% of the components needed for an Opportunity layer:
- Pattern detection works
- 10-factor scoring works
- Strategy classification works
- HTF context works
- A ranker exists (just needs activation)

What's missing is the **abstraction boundary** — a named object that represents "the market showed us something" before any decision is made about it. Creating this boundary enables:
1. All opportunities to be recorded (not just winners)
2. Cross-symbol comparison before execution
3. Portfolio-level intelligence
4. Clean separation of concerns for testing and evolution
