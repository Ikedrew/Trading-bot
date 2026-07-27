# HORIZON EXECUTION POLICY

**Date:** 2026-07-23
**Status:** Architecture specification. No implementation.
**Purpose:** Define the complete execution policy between Horizon Classification and Execution Guard.
**Contract for:** HorizonExecutionAuthority (Phase 2 implementation)

---

## 1. Portfolio Capacity

### 1.1 Hard Limits

| Limit | Value | Rationale |
|-------|-------|-----------|
| MAX_TOTAL_POSITIONS | 21 | 7 symbols x 3 horizons = theoretical max |
| MAX_POSITIONS_PER_SYMBOL | 3 | One per horizon (SCALP, INTRADAY, EXTENDED) |
| MAX_POSITIONS_PER_HORIZON_PER_SYMBOL | 1 | Uniqueness constraint |

### 1.2 Uniqueness Constraint

The position key is:

```
(symbol, horizon) → at most 1 open position
```

This means:
- EURUSD may have at most 1 SCALP, 1 INTRADAY, 1 EXTENDED
- Never 2 SCALPs on the same symbol
- Never 2 INTRADAYs on the same symbol
- Never 2 EXTENDED on the same symbol

### 1.3 Why This Controls Exposure While Allowing Scale

**Controlled because:**
- Per-symbol cap of 3 prevents concentration
- Per-horizon uniqueness prevents duplicating the same thesis
- Portfolio cap of 21 prevents over-leverage

**Scalable because:**
- Each horizon represents a genuinely different trade thesis
- SCALP targets a 45-minute move on M5 structure
- INTRADAY targets a multi-hour move on M15/H1 structure
- EXTENDED targets a multi-day move on H1/H4 structure
- These are NOT duplicates — they have different SL/TP, hold times, and structure sources

**Why not just increase lot size instead?**
- Larger single position = single point of failure
- Multi-horizon = diversification across timeframe structure
- If SCALP stops out, INTRADAY may still be valid (different SL level)
- Correlation is reduced because exits are independent

---

## 2. Horizon Eligibility — The Independence Principle

### 2.1 Core Decision

**Each eligible horizon is an independent opportunity.**

Horizons do NOT compete with each other. They do NOT replace each other. They coexist.

### 2.2 Rationale

A SCALP and an INTRADAY on the same symbol in the same direction are:
- Using different structure levels for SL
- Targeting different price distances for TP
- Expecting different holding durations
- Managed with different BE/trailing/time rules

They share an entry signal but diverge immediately on risk and management.

### 2.3 Eligibility Scenarios

| Classification Result | Policy Action |
|----------------------|---------------|
| Only SCALP eligible | Attempt SCALP (if slot open) |
| SCALP + INTRADAY eligible | Attempt BOTH independently (if slots open) |
| SCALP + EXTENDED eligible | Attempt BOTH independently (if slots open) |
| All three eligible | Attempt ALL THREE independently (if slots open) |
| None eligible | No execution. No shadow creation for execution path. |

### 2.4 "Attempt" Means

Each eligible horizon passes through the execution authority independently:
1. Is this horizon in PERMITTED_HORIZONS?
2. Is the (symbol, horizon) slot empty?
3. Is the symbol below MAX_POSITIONS_PER_SYMBOL?
4. Is the portfolio below MAX_TOTAL_POSITIONS?

If ALL pass → execute.
If ANY fail → that horizon is blocked. Others proceed independently.

### 2.5 Execution Order Within One Cycle

When multiple horizons are eligible in the same cycle:

```
Order: SCALP → INTRADAY → EXTENDED
```

Rationale:
- SCALP has tightest SL and shortest hold — lowest risk if wrong
- INTRADAY is next
- EXTENDED is largest commitment — evaluated last

If SCALP fills but INTRADAY fails authority check (e.g., symbol now at 3/3),
INTRADAY is simply blocked. No rollback of SCALP.

---

## 3. Existing Position Behaviour — Coexistence Rules

### 3.1 Core Rule

**Existing positions NEVER block a different horizon on the same symbol.**

Each horizon slot is independent. Occupancy of one does not affect another.

### 3.2 Decision Matrix

| Existing Positions | New Signal | Authority Decision | Reason |
|---|---|---|---|
| EURUSD: SCALP open | SCALP qualifies | **BLOCKED** | (symbol, SCALP) slot occupied |
| EURUSD: SCALP open | INTRADAY qualifies | **ALLOWED** | (symbol, INTRADAY) slot empty |
| EURUSD: SCALP open | EXTENDED qualifies | **ALLOWED** | (symbol, EXTENDED) slot empty |
| EURUSD: EXTENDED open | SCALP qualifies | **ALLOWED** | (symbol, SCALP) slot empty |
| EURUSD: INTRADAY open | EXTENDED qualifies | **ALLOWED** | (symbol, EXTENDED) slot empty |
| EURUSD: all 3 open | Any horizon qualifies | **BLOCKED** | All slots occupied (3/3) |
| 21 positions total | Any symbol+horizon | **BLOCKED** | Portfolio at capacity |
| EURUSD: SCALP open | SCALP qualifies (same direction) | **BLOCKED** | Duplicate slot |
| EURUSD: SCALP open | SCALP qualifies (opposite direction) | **BLOCKED** | Slot occupied regardless of direction |

### 3.3 Direction Independence

The uniqueness constraint is `(symbol, horizon)` — NOT `(symbol, horizon, direction)`.

If EURUSD SCALP BUY is open, a new EURUSD SCALP SELL is **BLOCKED**.

Rationale:
- Allowing opposite directions on the same horizon creates a synthetic hedge
- Hedging dilutes edge and complicates management
- If the signal reversed, the correct action is to CLOSE the existing position (future: reversal logic)
- Phase 1-2: positions are managed independently; reversal logic is a Phase 5+ feature

### 3.4 No Replacement Logic

The authority does NOT:
- Close an existing position to make room for a new one
- Upgrade a SCALP to an INTRADAY
- Merge positions across horizons
- Transfer stop loss levels between horizons

Each position is born, managed, and dies independently.

---

## 4. Position Lifecycle — Exit and Re-entry

### 4.1 Exit Independence

When a position closes, it frees ONLY its own slot.

```
State: EURUSD [SCALP open] [INTRADAY open] [EXTENDED open]

Event: SCALP hits TP (closes)

Result: EURUSD [SCALP: EMPTY] [INTRADAY open] [EXTENDED open]
        - INTRADAY and EXTENDED are completely unaffected
        - SCALP slot is now available for a new SCALP
```

### 4.2 Re-entry Rules

After a horizon closes:
- The (symbol, horizon) slot is immediately freed
- A new position on that same (symbol, horizon) may open on the next signal
- No cooldown between horizons (the per-symbol TRADE_COOLDOWN applies to the symbol as a whole)
- The trade cooldown timer starts from the CLOSE of the last position on that symbol

### 4.3 Lifecycle State Diagram

```
                ┌──────────────────────────────────────┐
                │       (symbol, horizon) SLOT          │
                └───────────────┬──────────────────────┘
                                │
                    ┌───────────┴───────────┐
                    ▼                       ▼
              ┌─────────┐            ┌──────────┐
              │  EMPTY  │            │ OCCUPIED │
              └────┬────┘            └─────┬────┘
                   │                       │
      Signal eligible +            Position closes
      Authority allows             (TP/SL/Time/Manual)
                   │                       │
                   ▼                       ▼
              ┌─────────┐            ┌──────────┐
              │OCCUPIED │            │  EMPTY   │
              └─────────┘            └──────────┘
```

### 4.4 Cascade Rules

- Closing SCALP does NOT trigger closing INTRADAY or EXTENDED
- Closing EXTENDED does NOT trigger closing SCALP or INTRADAY
- No "if one horizon fails, close all" logic
- Each position manages itself via its own HorizonProfile rules

Exception (future consideration):
- If the bot implements a "total symbol risk exceeded" circuit breaker,
  it could close all positions on a symbol. But this is not horizon policy —
  it's portfolio risk management (a separate concern).

---

## 5. Risk Allocation

### 5.1 Options Considered

**Option A: Equal risk per position**
- Each position risks RISK_PER_TRADE_PERCENT (e.g., 1%)
- 3 positions on EURUSD = 3% symbol risk
- Simple. Transparent. Predictable.
- Disadvantage: High concentration if all 3 horizons open on same symbol

**Option B: Shared risk per symbol**
- Total symbol risk capped at RISK_PER_TRADE_PERCENT (e.g., 1%)
- Each horizon gets 1/3 = 0.33%
- Disadvantage: Very small position sizes. SCALP becomes uneconomic.

**Option C: Tiered risk by horizon**
- SCALP: 1.0% (tightest SL, fastest resolution)
- INTRADAY: 0.75% (wider SL, longer hold)
- EXTENDED: 0.5% (widest SL, highest uncertainty)
- Total max per symbol: 2.25%
- Advantage: Reflects confidence in shorter horizons. Controls extended risk.

**Option D: Portfolio-adjusted equal risk**
- Each position risks 1%, but MAX_TOTAL_RISK_EXPOSURE_PCT caps aggregate
- If aggregate hits cap, no new positions regardless of horizon
- Advantage: Simple rule, portfolio-level safety net

### 5.2 Recommendation: Option C (Tiered) + Option D (Portfolio Cap)

**Use tiered risk per horizon with a portfolio-level aggregate cap.**

```
RISK_PER_HORIZON = {
    "SCALP": 1.0,        # % of equity
    "INTRADAY": 0.75,    # % of equity
    "EXTENDED": 0.5,     # % of equity
}

MAX_SYMBOL_RISK_PCT = 2.25        # Sum of all horizons on one symbol
MAX_TOTAL_RISK_EXPOSURE_PCT = 6.0 # Aggregate across all positions
```

**Why this works:**
- SCALP has earned the highest risk — fastest feedback loop, most validated
- INTRADAY is newer, less validated — moderate risk
- EXTENDED is least validated — smallest risk
- Portfolio cap prevents runaway exposure even with 21 positions
- Each horizon uses DYNAMIC sizing: `lot_size = risk_pct * equity / (SL_distance * pip_value)`
- Wider SL (EXTENDED) naturally produces smaller lot sizes

### 5.3 Risk Interaction Example

```
EURUSD:
  SCALP     → SL = 3 pips  → lot = 0.33  → risk = 1.0%
  INTRADAY  → SL = 15 pips → lot = 0.05  → risk = 0.75%
  EXTENDED  → SL = 50 pips → lot = 0.01  → risk = 0.5%
  Total EURUSD risk: 2.25%

Portfolio (if 5 symbols × 3 horizons = 15 positions):
  Aggregate risk = ~15 × 0.75 (average) = ~11.25%
  Cap at 6% means max ~8 concurrent positions in practice
  OR: reduce per-horizon risk to fit within cap
```

### 5.4 Phase 1 Implementation Note

Phase 1 (current): FIXED_LOT = 0.01, single SCALP, 1% risk. No change.
Phase 2-3: Introduce tiered risk when INTRADAY is enabled.
The policy is defined NOW so Phase 2 implementation can reference it directly.

---

## 6. Trade Management Ownership

### 6.1 Architectural Rule (Invariant)

```
Every Position belongs to exactly one HorizonProfile.
All management behaviour is resolved through the profile.
No component contains: if horizon == "SCALP"
```

### 6.2 Resolution Pattern

```python
# CORRECT:
profile = horizon_manager.get_profile(position.trade_horizon)
max_time = profile.max_time_in_trade_seconds
be_trigger = profile.break_even_trigger_rr

# FORBIDDEN:
if position.trade_horizon == "SCALP":
    max_time = 2700
elif position.trade_horizon == "INTRADAY":
    max_time = 28800
```

### 6.3 What The Profile Owns

| Behaviour | Resolved From Profile | Example SCALP | Example INTRADAY | Example EXTENDED |
|-----------|----------------------|---------------|-----------------|-----------------|
| Break-even trigger | break_even_trigger_rr | 1.0R | 1.5R | 2.0R |
| Break-even buffer | break_even_buffer | 1 pip | 2 pips | 3 pips |
| Trailing stop step | trailing_step | 0 (disabled) | 3 pips | 5 pips |
| Trailing start | trailing_start_rr | 0 (disabled) | 2.0R | 3.0R |
| Partial TP fraction | partial_tp_fraction | 0 (disabled) | 0.5 | 0.5 |
| Max hold time | max_time_in_trade_seconds | 2700s (45m) | 28800s (8h) | 259200s (3d) |
| Risk per trade | risk_per_trade_pct | 1.0% | 0.75% | 0.5% |
| RR target | typical_rr | 2.0 | 3.0 | 4.0 |

### 6.4 Why This Scales

Adding a new horizon (e.g., SWING, POSITION, NEWS) requires:
1. Add a new profile definition (data only)
2. Add to PERMITTED_HORIZONS config
3. Register in HorizonManager

NO changes to:
- TradeStateManager._process_one_position()
- Break-even logic
- Trailing stop logic
- Partial TP logic
- Time exit logic
- Position dataclass
- TradeRecord
- Decision ledger
- Guard chain

The profile IS the configuration. The code reads the profile.

---

## 7. Future Expansion

### 7.1 Adding New Horizons

| Future Horizon | Hold Time | SL Source | RR | Notes |
|----------------|-----------|-----------|-----|-------|
| SWING | 2-10 days | Daily structure | 5:1 | Multi-session, needs overnight risk |
| POSITION | 1-4 weeks | Weekly structure | 8:1 | Trend following, swap-aware |
| NEWS | 1-30 minutes | Pre-event volatility | 1.5:1 | Event-driven, time-bounded |

### 7.2 What Changes When Adding A Horizon

```
1. core/horizon/horizon_profiles.py      → Add descriptive profile (classification)
2. core/horizon/horizon_execution_profile.py → Add execution profile (management params)
3. core/horizon/horizon_manager.py       → Register in DEFAULT_PROFILES
4. core/config.py                        → Add to PERMITTED_HORIZONS when ready
5. core/horizon/horizon_classifier.py    → Add eligibility rules (classification)
6. core/horizon/horizon_trade_builder.py → Add SL/TP builder (shadow trades)
```

What does NOT change:
```
- Position dataclass (trade_horizon is already a string, not enum-locked)
- TradeStateManager (reads profile, doesn't know horizon names)
- Guard chain (checks slot availability, doesn't care which horizons exist)
- Decision ledger (already carries arbitrary horizon string)
- TradeRecord (already carries arbitrary horizon string)
- Execution orchestrator (horizon-agnostic)
- Risk manager (reads risk_per_trade from profile)
```

### 7.3 Why String Not Enum for trade_horizon

`Position.trade_horizon` is `str` not `TradeHorizon` enum because:
- Adding a new horizon does not require modifying Position
- No import dependency from Position → horizon module
- Persistence (JSONL) stores strings naturally
- Enum validation happens at the boundary (HorizonManager.validate_horizon)
- The internal invariant is: "value exists as a key in HorizonManager.all_profiles"

---

## 8. Execution Algorithm

### 8.1 Pseudocode: HorizonExecutionAuthority.evaluate()

```
function evaluate(symbol, eligible_horizons, current_positions) → list[HorizonPermission]:
    results = []
    
    for horizon in sort_by_priority(eligible_horizons):  # SCALP first
        permission = check_single(symbol, horizon, current_positions)
        results.append(permission)
    
    return results


function check_single(symbol, horizon, current_positions) → HorizonPermission:
    // Gate 1: Is this horizon permitted?
    if horizon not in config.PERMITTED_HORIZONS:
        return BLOCKED(reason="horizon_not_permitted")
    
    // Gate 2: Is (symbol, horizon) slot available?
    if any(p.symbol == symbol and p.trade_horizon == horizon for p in current_positions):
        return BLOCKED(reason="slot_occupied")
    
    // Gate 3: Is symbol below per-symbol cap?
    symbol_count = count(p for p in current_positions where p.symbol == symbol)
    if symbol_count >= MAX_POSITIONS_PER_SYMBOL:
        return BLOCKED(reason="symbol_limit_reached")
    
    // Gate 4: Is portfolio below total cap?
    if len(current_positions) >= MAX_TOTAL_POSITIONS:
        return BLOCKED(reason="portfolio_full")
    
    // Gate 5: Is aggregate risk within budget?
    profile = horizon_manager.get_profile(horizon)
    projected_risk = current_aggregate_risk + profile.risk_per_trade_pct
    if projected_risk > MAX_TOTAL_RISK_EXPOSURE_PCT:
        return BLOCKED(reason="risk_budget_exceeded")
    
    return ALLOWED(horizon=horizon, profile=profile)
```

### 8.2 Execution Sequence Diagram

```
┌──────────┐     ┌────────────────┐     ┌──────────────────────┐     ┌─────────────┐
│  Engine  │     │ Classification │     │ ExecutionAuthority    │     │ Orchestrator│
│ (EXECUTE)│     │                │     │                      │     │             │
└────┬─────┘     └───────┬────────┘     └──────────┬───────────┘     └──────┬──────┘
     │                   │                         │                        │
     │ signal detected   │                         │                        │
     ├──────────────────►│                         │                        │
     │                   │ classify_horizons()     │                        │
     │                   ├────────────────────────►│                        │
     │                   │                         │                        │
     │                   │ eligible=[SCALP,INTRA]  │                        │
     │                   │◄────────────────────────┤                        │
     │                   │                         │                        │
     │                   │     For each eligible:  │                        │
     │                   │                         │                        │
     │                   │── check SCALP ─────────►│                        │
     │                   │                         │ slot empty? ✓          │
     │                   │                         │ symbol < 3? ✓          │
     │                   │                         │ portfolio < 21? ✓      │
     │                   │   SCALP: ALLOWED        │ risk budget? ✓         │
     │                   │◄────────────────────────┤                        │
     │                   │                         │                        │
     │                   │── check INTRADAY ──────►│                        │
     │                   │                         │ slot empty? ✓          │
     │                   │   INTRADAY: ALLOWED     │ symbol < 3? ✓          │
     │                   │◄────────────────────────┤                        │
     │                   │                         │                        │
     │                   │                         │                        │
     │  For each ALLOWED horizon:                  │                        │
     │  ─── build intent (horizon SL/TP) ──────────────────────────────────►│
     │                                             │                        │ execute()
     │                                             │                        │ register()
     │                                             │                        │
```

### 8.3 State Transition Diagram — Portfolio

```
Portfolio State Machine (per symbol):

    ┌───────────────────────────────────────────────────┐
    │            SYMBOL SLOT MATRIX                      │
    │                                                   │
    │  SCALP:    [ EMPTY | OCCUPIED ]                   │
    │  INTRADAY: [ EMPTY | OCCUPIED ]                   │
    │  EXTENDED: [ EMPTY | OCCUPIED ]                   │
    │                                                   │
    │  Transitions:                                     │
    │    EMPTY → OCCUPIED: Authority allows + fill      │
    │    OCCUPIED → EMPTY: Position closes (any reason) │
    │                                                   │
    │  Constraints:                                     │
    │    sum(OCCUPIED) ≤ MAX_POSITIONS_PER_SYMBOL (3)   │
    │    Each slot independently managed                │
    └───────────────────────────────────────────────────┘

Global:
    sum(all OCCUPIED across all symbols) ≤ MAX_TOTAL_POSITIONS (21)
```

---

## 9. Portfolio State Examples

### Example A: Normal Multi-Horizon Portfolio

```
EURUSD:
  SCALP     BUY   entry=1.1000 sl=1.0997 tp=1.1006 risk=1.0%  (age: 12min)
  INTRADAY  BUY   entry=1.1000 sl=1.0985 tp=1.1045 risk=0.75% (age: 2hr)
  EXTENDED  BUY   entry=1.1000 sl=1.0950 tp=1.1200 risk=0.5%  (age: 14hr)

GBPUSD:
  SCALP     SELL  entry=1.3370 sl=1.3373 tp=1.3364 risk=1.0%  (age: 5min)
  INTRADAY  SELL  entry=1.3370 sl=1.3385 tp=1.3325 risk=0.75% (age: 45min)

USDJPY:
  SCALP     BUY   entry=150.00 sl=149.97 tp=150.06 risk=1.0%  (age: 3min)

Total positions: 6/21
Total risk: 5.0%
Symbols active: 3/7
```

### Example B: SCALP Closes, INTRADAY Continues

```
BEFORE:
  EURUSD: SCALP(open, 30min) + INTRADAY(open, 3hr)

EVENT:
  SCALP hits TP → closed, profit booked

AFTER:
  EURUSD: SCALP(EMPTY) + INTRADAY(open, 3hr)

NEXT CYCLE:
  New signal detected for EURUSD
  classify_horizons() → SCALP eligible
  Authority: (EURUSD, SCALP) slot EMPTY → ALLOWED
  New SCALP opens independently
  INTRADAY continues with its own management
```

### Example C: Portfolio at Symbol Capacity

```
EURUSD: SCALP(open) + INTRADAY(open) + EXTENDED(open) = 3/3

New signal for EURUSD:
  classify_horizons() → all three eligible
  Authority check:
    SCALP → BLOCKED (slot occupied)
    INTRADAY → BLOCKED (slot occupied)
    EXTENDED → BLOCKED (slot occupied)
  Result: No execution. Shadow trades still created.
```

### Example D: Portfolio at Total Capacity

```
7 symbols × 3 horizons = 21 positions (full)

New signal for NZDUSD:
  Authority: portfolio at 21/21 → ALL BLOCKED
  Even though (NZDUSD, SCALP) slot is empty,
  the portfolio cap prevents opening.
```

### Example E: Risk Budget Exceeded Before Position Cap

```
Config: MAX_TOTAL_RISK_EXPOSURE_PCT = 6.0%

Current state:
  8 positions, aggregate risk = 5.75%

New signal: EURUSD SCALP (would add 1.0% risk)
  Projected risk: 5.75 + 1.0 = 6.75% > 6.0%
  Authority: BLOCKED (risk_budget_exceeded)

New signal: USDJPY EXTENDED (would add 0.5% risk)
  Projected risk: 5.75 + 0.5 = 6.25% > 6.0%
  Authority: BLOCKED (risk_budget_exceeded)

Result: Must wait for existing positions to close before new ones can open.
```

---

## 10. Edge Cases

### 10.1 Simultaneous Signals on Same Symbol

**Scenario:** In one cycle, EURUSD produces EXECUTE and three horizons are eligible.

**Policy:** Process in order SCALP → INTRADAY → EXTENDED. Each is independent.
After SCALP fills, the symbol count increases (1→2→3). The third may be blocked
if it would exceed limits. This is correct — the portfolio constrains naturally.

### 10.2 Contradictory Directions Across Horizons

**Scenario:** Classification says SCALP BUY is eligible, EXTENDED SELL is eligible.

**Policy:** This should not happen — classify_horizons() receives direction from
the parent assessment. All horizons for one opportunity share the same direction.
If a future change allows independent direction per horizon, the uniqueness
constraint `(symbol, horizon)` still prevents conflict (can't have two SCALP).

### 10.3 Position Recovery After Restart

**Scenario:** Bot restarts. 5 positions exist on broker. Recovery runs.

**Policy:**
- Recovered positions default to trade_horizon="SCALP" (Phase 1)
- Phase 4 fix: search execution_results logs for original horizon
- If horizon cannot be determined, default SCALP (safest management rules)
- Recovered positions occupy their slot: prevents opening duplicate

### 10.4 Horizon Disabled While Position Open

**Scenario:** INTRADAY position is open. Admin removes "INTRADAY" from PERMITTED_HORIZONS.

**Policy:**
- Existing position continues to be managed normally
- HorizonProfile still exists (removal from PERMITTED only blocks NEW opens)
- No force-close on permission change
- Position closes naturally via its own management rules

### 10.5 Spread/Slippage Causes Fill Failure

**Scenario:** Authority allows INTRADAY. Broker rejects (spread too wide).

**Policy:**
- (symbol, INTRADAY) slot remains EMPTY (fill never happened)
- No retry on this cycle — next signal can attempt again
- Other horizons (SCALP, EXTENDED) are unaffected by this failure

### 10.6 Partial Close Reduces Volume But Position Stays Open

**Scenario:** INTRADAY partial TP fires. Volume reduced from 0.05 to 0.025.

**Policy:**
- Position remains OPEN (PositionStatus.PARTIAL or OPEN)
- (symbol, INTRADAY) slot remains OCCUPIED
- Cannot open a new INTRADAY until this one fully closes
- Partial close does NOT free the slot

### 10.7 Two Symbols in Same Correlation Group

**Scenario:** EURUSD has SCALP open. GBPUSD SCALP signal arrives.
Correlation guard: MAX_CORRELATION_GROUP_POSITIONS = 2.

**Policy:**
- Horizon authority checks happen BEFORE the execution guard chain
- If correlation guard blocks GBPUSD, that's a guard chain rejection — NOT a horizon policy rejection
- Guard chain is a separate layer with separate responsibility
- Horizon authority only checks: slot, symbol limit, portfolio limit, risk budget

### 10.8 Clock Skew Between Horizons

**Scenario:** SCALP opened 40 minutes ago (near max hold). INTRADAY signal arrives.

**Policy:**
- SCALP about to expire does NOT affect INTRADAY eligibility
- Each position's time management is independent
- If SCALP closes on time exit, slot frees. New SCALP can open if signal persists.

---

## 11. Integration With Existing Guard Chain

### 11.1 Execution Flow With Authority

```
Signal → Assessment → Classification → [NEW] HorizonExecutionAuthority
                                              ↓
                                    For each permitted eligible horizon:
                                              ↓
                              Build OrderIntent (horizon-specific SL/TP)
                                              ↓
                              Existing Guard Chain (unchanged):
                                - Daily trade limit
                                - Trade cooldown
                                - Correlation guard
                                - Portfolio exposure guard
                                - Regime guard
                                - Consistency rules
                                - Weekend protection
                                              ↓
                              ExecutionOrchestrator.execute_trade()
                                              ↓
                              register_from_execution() → Position
```

### 11.2 Authority vs Guard Chain Boundary

| Concern | Owner | When |
|---------|-------|------|
| Is this horizon permitted? | HorizonExecutionAuthority | Before guards |
| Is the (symbol, horizon) slot free? | HorizonExecutionAuthority | Before guards |
| Is the symbol below horizon cap? | HorizonExecutionAuthority | Before guards |
| Is the portfolio below position cap? | HorizonExecutionAuthority | Before guards |
| Is aggregate risk within budget? | HorizonExecutionAuthority | Before guards |
| Is daily trade limit exceeded? | Guard chain | After authority |
| Is trade cooldown active? | Guard chain | After authority |
| Is correlation exposure too high? | Guard chain | After authority |
| Is regime adverse? | Guard chain | After authority |
| Is it weekend? | Guard chain | After authority |

The authority answers: "Is there ROOM for this trade?"
The guard chain answers: "Is it SAFE to take this trade?"

Both must pass for execution.

---

## 12. Configuration Contract

### 12.1 Required Config Values (Phase 2)

```python
# ─── HORIZON EXECUTION POLICY ────────────────────────────────────────────
PERMITTED_HORIZONS = ["SCALP"]                    # Phase 1: only SCALP
# PERMITTED_HORIZONS = ["SCALP", "INTRADAY"]      # Phase 4: enable INTRADAY
# PERMITTED_HORIZONS = ["SCALP", "INTRADAY", "EXTENDED"]  # Phase 5

MAX_TOTAL_POSITIONS = 21                          # Portfolio hard cap
MAX_POSITIONS_PER_SYMBOL = 3                      # Per-symbol cap (one per horizon)

# Risk tiering (Phase 3+)
HORIZON_RISK_PCT = {
    "SCALP": 1.0,
    "INTRADAY": 0.75,
    "EXTENDED": 0.5,
}
MAX_SYMBOL_RISK_PCT = 2.25                        # Max aggregate risk on one symbol
MAX_TOTAL_RISK_EXPOSURE_PCT = 6.0                 # Portfolio-wide risk cap

# Horizon execution authority
HORIZON_AUTHORITY_ENABLED = False                  # Phase 2: enable when ready
HORIZON_AUTHORITY_LOG_ONLY = True                  # Shadow mode: log decisions, don't enforce
```

### 12.2 Activation Sequence

| Phase | PERMITTED_HORIZONS | AUTHORITY_ENABLED | LOG_ONLY | Behaviour |
|-------|-------------------|-------------------|----------|-----------|
| 1 (current) | ["SCALP"] | False | - | No authority. Current behaviour. |
| 2 | ["SCALP"] | True | True | Authority runs, logs, but never blocks |
| 2b | ["SCALP"] | True | False | Authority enforced (still only SCALP) |
| 3 | ["SCALP"] | True | False | Per-horizon management config active |
| 4 | ["SCALP","INTRADAY"] | True | False | INTRADAY execution enabled |
| 5 | ["SCALP","INTRADAY","EXTENDED"] | True | False | Full multi-horizon |

---

## 13. Summary of Decisions

| Question | Decision | Rationale |
|----------|----------|-----------|
| Should horizons compete? | **No.** Independent. | Different trade theses on different structure |
| Should one replace another? | **No.** Coexist. | No upgrade/downgrade logic |
| Can same horizon open twice? | **No.** Uniqueness. | (symbol, horizon) = at most 1 |
| Does closing one affect others? | **No.** Independent. | Each position self-managed |
| Same risk for all horizons? | **No.** Tiered. | Longer hold = higher uncertainty = lower risk |
| Direction matters for slot? | **No.** Direction-agnostic. | (symbol, horizon) regardless of BUY/SELL |
| Authority before or after guards? | **Before.** | Authority checks capacity. Guards check safety. |
| Profile drives management? | **Yes.** Always. | No hardcoded horizon if/else anywhere |
| String or enum for field? | **String.** | Extensibility without schema changes |
| Total cap serves what purpose? | **Portfolio risk.** | Prevents over-leverage even with generous per-symbol |

---

## 14. Appendix: Validation Checklist for Implementation

When implementing HorizonExecutionAuthority, verify:

- [ ] SCALP-only behaviour identical to current (no regression)
- [ ] (symbol, horizon) uniqueness enforced
- [ ] Symbol cap enforced
- [ ] Portfolio cap enforced
- [ ] Risk budget checked (when tiered risk enabled)
- [ ] PERMITTED_HORIZONS gating works
- [ ] LOG_ONLY mode logs but does not block
- [ ] Existing guard chain still runs AFTER authority
- [ ] Authority does not modify Position, OrderIntent, or decision
- [ ] Authority returns a simple ALLOWED/BLOCKED result with reason
- [ ] Shadow trades are NOT affected by authority (always created)
- [ ] Recovered positions occupy their slots
- [ ] Test: attempt duplicate (symbol, horizon) → blocked
- [ ] Test: different horizons same symbol → allowed
- [ ] Test: portfolio at 21 → all blocked
- [ ] Test: removing horizon from PERMITTED → existing positions continue
