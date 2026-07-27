# PHASE 4C.4: HORIZON SHADOW OBSERVABILITY

**Date:** 2026-07-24
**Status:** COMPLETE — Structured logging added for horizon shadow creation and outcome events.

---

## 1. Audit Findings (Before Changes)

| Event | Logged? | Format |
|-------|---------|--------|
| Horizon classification (multiple eligible) | ✅ | `[HORIZON] symbol=X eligible=[...] best=Y` |
| Horizon shadow trade CREATION | ❌ Missing | No log |
| Standard shadow trade close | ⚠️ Partial | `[SHADOW_TRADE_CLOSED]` used legacy schema fields |
| Horizon shadow trade OUTCOME | ❌ Missing | No horizon-specific log |

---

## 2. Logging Implementation

### Creation Event (NEW)

**Location:** `core/runtime/engine_execution_handler.py` (inside horizon shadow loop)

**Format:**
```
[HORIZON_SHADOW] symbol=GBPUSD horizon=INTRADAY direction=SELL entry=1.33700 sl=1.33803 tp=1.33391 rr=3.0 sl_source=M15_STRUCTURE created=true
```

**Trigger:** Once per horizon shadow trade opened. Only fires for INTRADAY/EXTENDED (SCALP is the live shadow trade).

### Outcome Event (NEW)

**Location:** `core/shadow_trades.py` `_emit_close_event()` (enhanced)

**Format:**
```
[HORIZON_SHADOW_CLOSED] symbol=GBPUSD horizon=INTRADAY outcome=take_profit r_multiple=3.0000 mfe=3.2000 mae=-0.4000 bars=40 trade_id=shadow_4578_GBPUSD_INTRADAY
```

**Trigger:** When a horizon-tagged shadow trade closes (SL hit, TP hit, or max_bars timeout).

### Standard Shadow (UNCHANGED — enhanced format)

```
[SHADOW_TRADE_CLOSED] trade_id=shadow_4578_GBPUSD symbol=GBPUSD strategy=CONTINUATION r_multiple=2.0000 exit=take_profit bars=8 mfe=2.1000 mae=-0.3000
```

---

## 3. Horizon Identity Propagation

```
HorizonTrade (builder output)
    │ horizon="INTRADAY"
    ▼
ShadowTradeEngine.open_trade()
    │ trade_id="shadow_{cycle}_{SYMBOL}_INTRADAY"
    │ strategy="CONTINUATION_INTRADAY"
    ▼
ShadowTrade (active tracking)
    │ trade_id contains "_INTRADAY"
    ▼
_build_truth_record() (on close)
    │ identity.trade_id="shadow_4578_GBPUSD_INTRADAY"
    │ identity.strategy_id="CONTINUATION_INTRADAY"
    ▼
_emit_close_event()
    │ Detects "_INTRADAY" in trade_id → logs [HORIZON_SHADOW_CLOSED]
    ▼
_persist_shadow_trade()
    │ Written to logs/shadow_trades/{SYMBOL}/{DATE}.jsonl
    ▼
Research Engine
    │ load_shadow_trades() → filter by trade_id pattern
```

**Horizon identity is preserved end-to-end:** trade_id suffix (`_INTRADAY`, `_EXTENDED`) survives from creation through evaluation to persistence and log output.

---

## 4. Example Events

### Creation (logged when horizon shadow opens):
```
INFO [HORIZON_SHADOW] symbol=GBPUSD horizon=INTRADAY direction=SELL entry=1.33700 sl=1.33803 tp=1.33391 rr=3.0 sl_source=M15_STRUCTURE created=true
INFO [HORIZON_SHADOW] symbol=GBPUSD horizon=EXTENDED direction=SELL entry=1.33700 sl=1.33955 tp=1.32680 rr=4.0 sl_source=H1_SWING_STRUCTURE created=true
```

### Outcome (logged when horizon shadow closes):
```
INFO [HORIZON_SHADOW_CLOSED] symbol=GBPUSD horizon=INTRADAY outcome=take_profit r_multiple=3.0000 mfe=3.2000 mae=-0.4000 bars=40 trade_id=shadow_4578_GBPUSD_INTRADAY
INFO [HORIZON_SHADOW_CLOSED] symbol=GBPUSD horizon=EXTENDED outcome=stop_loss r_multiple=-1.0000 mfe=0.5000 mae=-1.1000 bars=12 trade_id=shadow_4578_GBPUSD_EXTENDED
```

---

## 5. Tests

| Test | Verifies | Result |
|------|----------|--------|
| `test_intraday_close_logs_horizon` | INTRADAY outcome produces `[HORIZON_SHADOW_CLOSED]` | ✅ |
| `test_extended_close_logs_horizon` | EXTENDED outcome produces `[HORIZON_SHADOW_CLOSED]` | ✅ |
| `test_standard_shadow_uses_normal_log` | Non-horizon shadow uses `[SHADOW_TRADE_CLOSED]` | ✅ |
| `test_empty_record_does_not_crash` | Empty record safe | ✅ |
| `test_malformed_record_does_not_crash` | Bad data safe | ✅ |
| `test_missing_identity_does_not_crash` | Partial record safe | ✅ |
| `test_horizon_in_trade_id_survives_to_outcome` | Horizon tag detected at close (timeout) | ✅ |

---

## 6. Completion Status

| Component | Status |
|-----------|--------|
| Creation logging | ✅ Implemented |
| Outcome logging (horizon-specific) | ✅ Implemented |
| Standard shadow logging (enhanced) | ✅ Updated to v2 schema |
| Horizon identity propagation | ✅ Verified end-to-end |
| Safe fallback for missing data | ✅ Tested |
| No trading logic changes | ✅ Confirmed |
| No execution changes | ✅ Confirmed |
| No risk changes | ✅ Confirmed |
| Tests passing | ✅ 7/7 new + all existing |

**Phase 4C.4 is COMPLETE.** The system will now visibly report horizon shadow lifecycle events in production logs, making it immediately apparent whether horizon shadows are being created and what their outcomes are.
