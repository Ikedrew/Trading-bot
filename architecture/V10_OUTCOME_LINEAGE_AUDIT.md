# Phase F — Outcome Lineage Audit

---

## Complete Lifecycle Trace

### Opening Trade:

```
V10 observation_id: "abc123"
        │
        ▼
OrderIntent
    .risk_id = "abc123"
    .metadata["decision_id"] = "abc123"
    .metadata["strategy_family"] = "MEAN_REVERSION"
    .metadata["horizon"] = "SCALP"
    .pattern = "MEAN_REVERSION"
        │
        ▼
prepare_execution()
    generates: decision_id (UUID), correlation_id
        │
        ▼
ExecutionOrchestrator.execute_trade()
    passes: decision_id, correlation_id to MT5Execution
    persists: execution_result (with decision_id, correlation_id, pattern)
        │
        ▼
MT5 Execution → broker fill
    returns: result.deal, result.order (broker IDs)
        │
        ▼
TradeIdentity (built in live_scanner ~line 1194):
    correlation_id = cor_id (runtime)
    decision_id = UUID (runtime)
    strategy = V10 strategy_family ← ✓
    pattern = V10 strategy_family ← ✓
    decision_ts_utc = closed_time
        │
        ▼
Position.trade_identity = TradeIdentity
    (carried for entire position lifecycle)
```

### Closing Trade:

```
Trade management detects SL/TP hit or timeout
        │
        ▼
TradeLifecycleLogger._persist_trade_close(position, exit_price, event)
        │
        ▼
build_trade_record(position=...)
    reads: position.trade_identity.correlation_id
    stores: TradeRecord.correlation_id
    stores: TradeRecord.trade_horizon (from position.trade_horizon)
    stores: TradeRecord.pattern_name (from position.pattern_tag = strategy_family)
        │
        ▼
persist_trade_once(record) → logs/trade_journal/{symbol}/{date}.jsonl
```

---

## Fields Surviving to Outcome

| Field | Present in TradeRecord? | Source |
|---|---|---|
| observation_id | **NO** ← GAP | Only in OrderIntent.risk_id (not in TradeIdentity) |
| correlation_id | ✓ | TradeIdentity.correlation_id |
| decision_id (UUID) | ✓ (via correlation_id lookup) | TradeIdentity.decision_id |
| strategy_family | ✓ (as pattern_name) | TradeIdentity.pattern / position.pattern_tag |
| horizon | ✓ (as trade_horizon) | position.trade_horizon (from intent.metadata) |
| entry_price | ✓ | position.entry_price |
| exit_price | ✓ | build_trade_record(exit_price=) |
| stop_loss | ✓ (initial_sl) | position.initial_sl |
| take_profit | ✓ (initial_tp) | position.initial_tp |
| volume | ✓ | position.volume |
| P&L | ✓ (realised_pnl, net_pnl) | Calculated from entry/exit |
| exit_reason | ✓ (close_reason) | Event detail |
| holding_time | ✓ (duration_seconds) | exit_time - entry_time |
| R-multiple | **NOT directly stored** | Can be derived: pnl / risk_distance |

---

## THE GAP: `observation_id` Not in TradeIdentity

**Current TradeIdentity fields:**
```python
correlation_id: str     # Runtime correlation
decision_id: str        # UUID from prepare_execution
cycle_id: int
strategy: str           # V10 strategy_family ✓
pattern: str            # V10 strategy_family ✓
decision_ts_utc: float
```

**Missing:**
```python
observation_id: str     # V10 research root — NOT HERE
```

**Impact:** To trace a trade outcome back to V10 reasoning, a researcher must:
1. Get `correlation_id` from TradeRecord
2. Look up `correlation_id` in the V10 decision ledger entry
3. Read `entity_id` or `correlation_id` field there (which is the observation_id)

This works but requires a JOIN. It would be cleaner if `observation_id` were directly in `TradeIdentity`.

---

## Scenario Verification

### A) V10 EXECUTE → Fill → Close Profit:

```
observation_id → OrderIntent.risk_id → (lives in memory during execution)
                → TradeIdentity.strategy = strategy_family
                → Position.trade_identity carries through lifecycle
                → TradeRecord.correlation_id → (JOIN to V10 record for observation_id)
                → TradeRecord.pattern_name = strategy_family
                → TradeRecord.trade_horizon = horizon
                → TradeRecord.realised_pnl = profit
```

Lineage: **PRESENT but requires one JOIN** for observation_id.

### B) V10 EXECUTE → Broker Rejection:

```
observation_id → OrderIntent.risk_id
              → persist_execution_result() stores:
                  decision_id, correlation_id, pattern, retcode
              → ExecutionOutcome(executed=True, ok=False)
```

Lineage: **FULLY PRESERVED** in execution_result persistence.

### C) V10 NO_TRADE:

```
observation_id → V10DecisionRecord.decision_id = observation_id
              → persist_v10_full() stores complete chain
```

Lineage: **FULLY PRESERVED** — no execution attempt, decision record captures everything.

---

## Recommended Fix (for future Phase)

Add `observation_id` to `TradeIdentity`:

```python
@dataclass(frozen=True)
class TradeIdentity:
    correlation_id: str
    decision_id: str = ""
    observation_id: str = ""      # ← ADD THIS (V10 research root)
    cycle_id: int = 0
    strategy: str = ""
    pattern: str = ""
    decision_ts_utc: float = 0.0
```

Then in live_scanner line ~1194:
```python
_trade_identity = TradeIdentity(
    correlation_id=_cor_id,
    decision_id=_decision_id,
    observation_id=decision.intent.risk_id,  # ← V10 observation_id
    ...
)
```

This would give direct lineage: `TradeRecord → TradeIdentity → observation_id → V10DecisionRecord`.

---

## Final Verdict

| Scenario | observation_id Preserved? | Method |
|---|---|---|
| V10 NO_TRADE | ✓ Direct | V10DecisionRecord.decision_id |
| V10 EXECUTE → rejection | ✓ Direct | execution_result persistence |
| V10 EXECUTE → fill → close | **Via JOIN** | TradeRecord.correlation_id → ledger → observation_id |

**The lineage is TRACEABLE but not direct for closed trades.**
- Strategy family: ✓ Direct (pattern_name)
- Horizon: ✓ Direct (trade_horizon)
- observation_id: Requires one JOIN (correlation_id → ledger)

**Status: YELLOW — functional but not optimal. One field addition to TradeIdentity would make it GREEN.**
