# HORIZON EXECUTION & TRADE MANAGEMENT ARCHITECTURE

**Date:** 2026-07-25
**Status:** Design document. No implementation yet.
**Goal:** Promote `trade_horizon` to first-class property throughout execution pipeline while preserving current trading behaviour.

---

## 1. Executive Summary

This document defines the architecture for horizon-aware execution and trade management. The system currently classifies opportunities into SCALP/INTRADAY/EXTENDED horizons and tracks shadow outcomes — but execution treats every trade identically (SCALP parameters).

This design enables:
- Multiple positions per symbol (one per horizon)
- Per-horizon trade management (different BE/trailing/time rules)
- Portfolio allocation by horizon
- Graceful activation of higher horizons without code changes (config only)

---

## 2. Component Responsibilities

### 2.1 HorizonExecutionAuthority (NEW)

**File:** `core/horizon/execution_authority.py`

**Owns:**
- Whether a given `(symbol, horizon)` combination is permitted to open
- Portfolio-level horizon allocation limits
- Permitted horizons gating

**Does NOT own:**
- SL/TP calculation (owned by RiskManager / horizon_trade_builder)
- Broker execution (owned by ExecutionOrchestrator)
- Position lifecycle (owned by TradeStateManager)
- Pattern detection or scoring

**Interface:**
```python
class HorizonExecutionAuthority:
    def can_open(
        self,
        symbol: str,
        horizon: str,
        current_positions: list[Position],
    ) -> HorizonPermission:
        """
        Returns:
            HorizonPermission(allowed=True/False, reason="...")
        """
```

**Decision Rules:**
1. `horizon in config.PERMITTED_HORIZONS` → else BLOCKED (horizon_not_permitted)
2. No existing position with same `(symbol, horizon)` → else BLOCKED (duplicate_symbol_horizon)
3. Count positions for symbol < `MAX_POSITIONS_PER_SYMBOL` → else BLOCKED (symbol_limit)
4. Total positions < `MAX_TOTAL_POSITIONS` → else BLOCKED (portfolio_limit)

---

### 2.2 HorizonProfile (ENHANCED)

**File:** `core/horizon/horizon_profiles.py` (already exists — extend)

**Currently owns:** Descriptive metadata (expected hold time, SL source, RR target)

**Will own (future):** Per-horizon TradeManagementConfig parameters:
```python
@dataclass(frozen=True)
class HorizonProfile:
    # ... existing fields ...
    
    # Trade management (Phase 2 — not implemented yet)
    break_even_trigger_rr: float = 0.0
    break_even_buffer: float = 0.0
    trailing_step: float = 0.0
    trailing_start_rr: float = 0.0
    partial_tp_fraction: float = 0.0
    max_time_in_trade_seconds: float = 0.0
```

**NOT migrated yet:** Current TradeManagementConfig remains global. Per-horizon management is Phase 2 of this design.

---

### 2.3 Position (EXTENDED)

**File:** `core/trade_management/position.py`

**New field:**
```python
trade_horizon: str = "SCALP"    # "SCALP" | "INTRADAY" | "EXTENDED"
```

Set by `register_from_execution()` reading from `OrderIntent.metadata["horizon"]`.

---

### 2.4 OrderIntent (CONVENTION)

**File:** `risk/models.py`

**No schema change.** Use existing `metadata: dict` field:
```python
OrderIntent(
    ...,
    metadata={"horizon": "INTRADAY"},
)
```

---

## 3. Sequence Diagrams

### 3.1 Current Flow (SCALP only, unchanged)

```
run_new_engine()
    │ action=EXECUTE, intent=OrderIntent(sl,tp,volume,metadata={})
    ▼
prepare_execution()
    │ correlation_id, decision_id generated
    ▼
Guard Chain (existing)
    │ daily_limit, cooldown, correlation, exposure, regime
    ▼
[NEW] HorizonExecutionAuthority.can_open(symbol, "SCALP", positions)
    │ → allowed=True (default: SCALP always permitted)
    ▼
ExecutionOrchestrator.execute_trade()
    │ → broker order_send
    ▼
register_from_execution()
    │ Position(trade_horizon="SCALP") ← from intent.metadata["horizon"]
    ▼
TradeStateManager manages position
    │ uses global config (Phase 1)
    │ OR uses per-horizon config (Phase 2 — future)
```

### 3.2 Future Flow (Multi-Horizon)

```
run_new_engine()
    │ action=EXECUTE
    ▼
Horizon Selection (which horizon to execute?)
    │ classify_horizons() → eligible=[SCALP, INTRADAY]
    │ Select best eligible permitted horizon
    │ Build SL/TP for selected horizon (horizon_trade_builder)
    ▼
prepare_execution()
    │ OrderIntent(metadata={"horizon": "INTRADAY"})
    ▼
Guard Chain (existing)
    ▼
HorizonExecutionAuthority.can_open("GBPUSD", "INTRADAY", positions)
    │ Checks: permitted? duplicate? symbol_limit? portfolio_limit?
    ▼
ExecutionOrchestrator.execute_trade()
    ▼
Position(trade_horizon="INTRADAY")
    ▼
TradeStateManager → selects INTRADAY management config
    │ BE at 1.5R, trailing at 2R, max 8 hours
```

### 3.3 Portfolio State Example

```
Portfolio (MAX_TOTAL=21, PER_SYMBOL=3):

EURUSD:
  Position 1: SCALP    BUY  entry=1.1000  sl=1.0997  tp=1.1006  (BE active)
  Position 2: INTRADAY BUY  entry=1.1000  sl=1.0985  tp=1.1045  (trailing active)
  Position 3: EXTENDED BUY  entry=1.1000  sl=1.0950  tp=1.1200  (holding)

GBPUSD:
  Position 4: SCALP    SELL entry=1.3370  sl=1.3373  tp=1.3364
  Position 5: INTRADAY SELL entry=1.3370  sl=1.3382  tp=1.3325

Total: 5/21 positions
```

---

## 4. Required Schema Changes

### 4.1 Position Dataclass

```python
# core/trade_management/position.py
@dataclass
class Position:
    ...
    trade_horizon: str = "SCALP"    # NEW — "SCALP" | "INTRADAY" | "EXTENDED"
```

**Migration:** Default "SCALP" means all existing positions and recovered positions automatically get SCALP behaviour (no change).

### 4.2 OrderIntent Metadata Convention

```python
# No schema change — use existing metadata dict
OrderIntent(
    metadata={"horizon": "SCALP"}    # CONVENTION: horizon key in metadata
)
```

### 4.3 TradeRecord

```python
# core/trade_journal.py
@dataclass(frozen=True)
class TradeRecord:
    ...
    trade_horizon: str = ""    # NEW — propagated from Position.trade_horizon
```

### 4.4 Decision Ledger execution_intent

```python
# Already a dict — add key:
_cycle_decision["execution_intent"] = {
    "side": ...,
    "volume": ...,
    "sl": ...,
    "tp": ...,
    "pattern": ...,
    "horizon": "SCALP",    # NEW
}
```

### 4.5 Config

```python
# core/config.py additions:
PERMITTED_HORIZONS = ["SCALP"]           # Which horizons may execute (expand over time)
MAX_TOTAL_POSITIONS = 21                 # Portfolio hard cap (7 symbols × 3 horizons)
MAX_POSITIONS_PER_SYMBOL = 3             # Max concurrent positions on one pair
# MAX_POSITIONS_PER_HORIZON = 7          # Optional: cap any single horizon portfolio-wide
```

---

## 5. Integration Points With Existing Horizon Classification

### Current Classification Output (already running)

```python
HorizonClassificationResult:
    assessments: [
        HorizonAssessment(horizon="SCALP", eligible=True, confidence=0.75),
        HorizonAssessment(horizon="INTRADAY", eligible=True, confidence=0.62),
        HorizonAssessment(horizon="EXTENDED", eligible=False, confidence=0.20),
    ]
```

### Integration With HorizonExecutionAuthority

```python
# In live_scanner EXECUTE path (future):
_horizon_class = classify_horizons(...)
_eligible = _horizon_class.eligible_horizons  # e.g., ["SCALP", "INTRADAY"]

# Filter by permitted horizons
_permitted = [h for h in _eligible if h in config.PERMITTED_HORIZONS]

# Check authority for each permitted horizon
for _h in _permitted:
    _permission = _horizon_authority.can_open(symbol, _h, all_positions)
    if _permission.allowed:
        # Build SL/TP for this horizon
        _intent = build_horizon_intent(symbol, direction, _h, structure_data)
        # Execute
        break
```

### Integration With Shadow Tracking

Shadow trades (already decoupled) continue running for ALL eligible horizons regardless of which horizon is executed. This ensures research data accumulates for non-executed horizons.

---

## 6. Phased Implementation Plan

### Phase 1: Schema & Propagation (Zero behaviour change)

**Goal:** Add `trade_horizon` field to Position, TradeRecord, and decision_ledger. Default to "SCALP". No new guards.

| Step | Change | Risk |
|------|--------|------|
| 1a | Add `trade_horizon: str = "SCALP"` to Position | Zero (default matches current) |
| 1b | Set `OrderIntent.metadata["horizon"] = "SCALP"` in RiskManager | Zero (metadata already a dict) |
| 1c | Read `metadata["horizon"]` in `register_from_execution()` → set on Position | Zero |
| 1d | Add `trade_horizon` to TradeRecord, propagate from Position | Zero |
| 1e | Add `horizon` key to decision_ledger execution_intent dict | Zero |
| 1f | Add config: `PERMITTED_HORIZONS = ["SCALP"]` | Zero (config only) |

**After Phase 1:** Every new trade carries `trade_horizon="SCALP"`. All queries can filter by horizon. Existing tests pass unchanged.

---

### Phase 2: HorizonExecutionAuthority (New guard, still SCALP only)

**Goal:** Add the authority guard that validates `(symbol, horizon)` uniqueness. Still only SCALP permitted.

| Step | Change | Risk |
|------|--------|------|
| 2a | Create `core/horizon/execution_authority.py` | Zero (new module) |
| 2b | Add to guard chain (after portfolio_exposure_guard) | Low (passes every time for SCALP) |
| 2c | Add config: `MAX_TOTAL_POSITIONS=21`, `MAX_POSITIONS_PER_SYMBOL=3` | Low (raises limits) |
| 2d | Tests for authority logic | Zero |

**After Phase 2:** The authority exists and validates but never blocks (because `PERMITTED_HORIZONS=["SCALP"]` and limits are generous). Bot runs identically.

---

### Phase 3: Per-Horizon Trade Management Config

**Goal:** Replace single global `TradeManagementConfig` with per-horizon lookup.

| Step | Change | Risk |
|------|--------|------|
| 3a | Create `HORIZON_MANAGEMENT_CONFIGS` dict in config | Zero (data only) |
| 3b | In `_process_one_position()`, lookup config by `pos.trade_horizon` | Low (SCALP config = current config) |
| 3c | Default fallback: if horizon unknown, use SCALP config | Zero risk |
| 3d | Tests for per-horizon management selection | Zero |

**After Phase 3:** SCALP trades use identical parameters to today. INTRADAY/EXTENDED configs exist but are only used by shadow/future trades.

---

### Phase 4: Enable INTRADAY Execution (Behaviour change)

**Goal:** Allow INTRADAY trades to execute (based on shadow data validation).

| Step | Change | Risk |
|------|--------|------|
| 4a | Set `PERMITTED_HORIZONS = ["SCALP", "INTRADAY"]` | MODERATE (new trade type) |
| 4b | In EXECUTE path: select best permitted horizon (not just SCALP) | MODERATE |
| 4c | Use `horizon_trade_builder` for INTRADAY SL/TP | MODERATE |
| 4d | Enable DYNAMIC position sizing (wider stop = smaller lot) | MODERATE |

**After Phase 4:** Bot takes both SCALP and INTRADAY trades. EXTENDED remains shadow-only.

---

### Phase 5: Enable EXTENDED Execution

Same pattern as Phase 4 but for EXTENDED horizon. Requires validated shadow data showing positive expectancy.

---

## 7. Risks & Mitigations

| Risk | Severity | Mitigation |
|------|----------|-----------|
| Multiple positions increase margin usage | HIGH | Phase 4 only activates with DYNAMIC sizing (constant risk per trade) |
| Position recovery doesn't know horizon | LOW | Default "SCALP" on recovery — matches current behaviour |
| Trade management uses wrong config | MEDIUM | Phase 3 uses exact current config for SCALP — no parameter change |
| Guard chain order matters | LOW | New guard added AFTER existing guards — additive, not replacement |
| Existing MAX_OPEN_POSITIONS=1 conflicts | MEDIUM | Phase 2 raises to MAX_POSITIONS_PER_SYMBOL=3 (only effective when INTRADAY enabled) |
| Correlation guard may block second position on same group | LOW | Already allows MAX_CORRELATION_GROUP_POSITIONS=2 — compatible with multi-horizon |

---

## 8. Compatibility Matrix

| Component | Phase 1 | Phase 2 | Phase 3 | Phase 4 |
|-----------|---------|---------|---------|---------|
| Existing SCALP trades | ✅ Identical | ✅ Identical | ✅ Identical | ✅ Still works |
| Position recovery | ✅ Default SCALP | ✅ | ✅ | ✅ |
| Shadow trade engine | ✅ Unchanged | ✅ | ✅ | ✅ |
| Research engine / loaders | ✅ New field available | ✅ | ✅ | ✅ |
| Horizon classification | ✅ Unchanged | ✅ | ✅ | ✅ |
| Decision ledger | ✅ New field | ✅ | ✅ | ✅ |
| Trade truth | ✅ Unchanged (no horizon field — by design) | ✅ | ✅ | ✅ |
| Protection verification | ✅ Unchanged | ✅ | ✅ | ✅ |
| Risk deviation | ✅ Unchanged | ✅ | ✅ | ✅ |

---

## 9. Success Criteria

After full implementation:

1. Every executed trade carries `trade_horizon` from intent through to closed trade record
2. Multiple positions can exist on the same symbol (one per horizon)
3. Each horizon has independent trade management parameters
4. PERMITTED_HORIZONS controls activation without code changes
5. Shadow tracking continues for all horizons (including non-permitted)
6. Research engine can query: "average R by horizon" across all executed trades
7. Bot remains operational after each phase (no big-bang deployment)
