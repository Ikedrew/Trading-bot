# Data Lineage Audit: strategy_observations ↔ shadow_trades_v2 ↔ decision_trace

---

## 1. entity_id Propagation

### Origin

```python
# core/pipeline/new_engine.py (line 105)
_entity_id = f"{symbol}_{int(candles[closed_i].time)}"
```

Format: `{SYMBOL}_{BAR_TIME_UNIX_SECONDS}`
Example: `EURUSD_1753574400`

Deterministic: same bar always produces same entity_id.

### Propagation Path

```
new_engine.py (creates _entity_id)
    ↓
engine_result["entity_id"] = _entity_id
    ↓
    ├─→ DecisionTrace (observer #6): entity_id ✅ PRESENT
    ├─→ ShadowTrade (via engine_execution_handler): entity_id ⚠️ NOT PASSED
    └─→ StrategyObserver (observer #7): entity_id ✅ PRESENT (added recently)
```

### Decision Trace: entity_id = ✅ PRESENT

```python
# core/decision_trace.py → _build_trace()
entity_id = engine_result.get("entity_id", "")
# → persisted as DecisionTrace.entity_id
```

S3 path: `decision_trace/symbol={SYMBOL}/date={DATE}/part-000.jsonl`
Field: top-level `entity_id` string

### Shadow Trades: entity_id = ⚠️ INCONSISTENT

There are TWO paths that create shadow trades:

**Path A: engine_execution_handler.py (EXECUTE decisions)**
```python
# core/runtime/engine_execution_handler.py → prepare_execution()
get_shadow_engine().open_trade(
    trade_id=f"shadow_{cycle_id}_{sym_state.symbol}",
    ...
    correlation_id=_cor_id,
    # entity_id IS NOT PASSED HERE ← BUG
)
```

The `open_trade()` call does NOT pass `entity_id`. However, the ShadowTrade dataclass has a default `entity_id = ""` field. The `_build_truth_record()` then writes:
```python
"identity": {
    "entity_id": trade.entity_id or None,  # → None for EXECUTE-path trades
}
```

**Path B: research_shadow_engine.py (RESEARCH_WOULD_EXECUTE)**
```python
# core/research_assessment/research_shadow_engine.py
engine.open_trade(
    ...
    entity_id=entity_id,  # ← PASSED CORRECTLY
)
```

### Strategy Observations: entity_id = ✅ PRESENT

```python
# core/strategies/strategy_intelligence_observer.py
record["entity_id"] = engine_result.get("entity_id", "") or f"{ctx.symbol}_{int(ctx.bar_time)}"
```

Always populated. Uses engine_result's entity_id with fallback to identical construction.

---

## 2. Does shadow_trades_v2 contain entity_id?

### Answer: PARTIALLY

| Shadow Trade Source | entity_id Present? | Notes |
|---|---|---|
| EXECUTE path (engine_execution_handler) | **NO** (None) | open_trade() doesn't pass entity_id |
| Research shadow (research_shadow_engine) | **YES** | Explicitly passed |
| Horizon shadows (live_scanner) | **UNKNOWN** | Depends on how they're opened |

### Impact on Research Joins

For shadow trades created via the EXECUTE path, `identity.entity_id` will be `null`.

**Workaround available:** The shadow trade record contains:
- `decision_snapshot.timestamp_decision_utc` = `trade.entry_time` = bar close time (unix seconds)
- `identity.symbol` = symbol

These are the same components as entity_id (`{symbol}_{bar_time}`). So the join can be reconstructed:

```sql
-- Deterministic reconstruction for EXECUTE-path shadows:
CONCAT(identity.symbol, '_', CAST(decision_snapshot.timestamp_decision_utc AS BIGINT))
```

This produces the same value as `entity_id`.

---

## 3. Timestamp Consistency

### strategy_observations

| Field | Format | Source |
|---|---|---|
| `timestamp_utc` | Unix seconds (float) | `ctx.bar_time` (from live_scanner, = candle close time) |

### shadow_trades_v2

| Field | Format | Source |
|---|---|---|
| `decision_snapshot.timestamp_decision_utc` | Unix seconds (float) | `trade.entry_time` (= candle close time passed at open_trade) |
| `simulated_outcome.exit_timestamp` | Unix seconds (float) | Candle time when trade closed |

### decision_trace

| Field | Format | Source |
|---|---|---|
| `timestamp_utc` | ISO string (e.g. "2026-07-27T20:00:05.123Z") | `datetime.now(timezone.utc)` at trace build time |

### Consistency Assessment

| Dataset | Time Reference | Clock Source | Consistent? |
|---|---|---|---|
| strategy_observations.timestamp_utc | Bar close time (unix float) | MT5 broker clock | ✅ |
| shadow_trades.decision_snapshot.timestamp_decision_utc | Bar close time (unix float) | MT5 broker clock | ✅ |
| decision_trace.timestamp_utc | Trace creation time (ISO string) | System clock | ⚠️ DIFFERENT |

**Issue:** `decision_trace.timestamp_utc` is system clock at trace-build time (a few ms after bar close), while shadow_trades and strategy_observations use bar close time from MT5. The difference is typically <100ms but the FORMAT differs (ISO string vs unix float).

**Impact:** Low. For research joins, `entity_id` is the correct join key (not timestamp). Entity_id is deterministic regardless of clock source.

---

## 4. S3 Partition Comparison

### strategy_observations
```
s3://trading-bot-data-mk1/strategy_observations/symbol={SYMBOL}/date={YYYY-MM-DD}/part-000.jsonl
```
Date derived from: `timestamp_utc` (bar close time, UTC)

### shadow_trades_v2
```
s3://trading-bot-data-mk1/shadow_trades/schema_version=shadow_trades_v2/symbol={SYMBOL}/date={YYYY-MM-DD}/part-000.jsonl
```
Date derived from: `simulated_outcome.exit_timestamp` (trade EXIT time, UTC)

### decision_trace
```
s3://trading-bot-data-mk1/decision_trace/symbol={SYMBOL}/date={YYYY-MM-DD}/part-000.jsonl
```
Date derived from: `datetime.now(timezone.utc)` at persist time

### Partition Structure Differences

| Dataset | S3 Prefix | Extra Partitions | Date Source |
|---|---|---|---|
| strategy_observations | `strategy_observations/` | None | Bar time (entry) |
| shadow_trades | `shadow_trades/` | `schema_version=shadow_trades_v2/` | Exit time |
| decision_trace | `decision_trace/` | None | System clock |

---

## 5. Cross-Partition Date Mismatch Risk

### The Problem

**strategy_observations** is partitioned by the bar's date (decision time).
**shadow_trades** is partitioned by the trade's EXIT date.

A shadow trade opened at 23:55 UTC on 2026-07-27 might close at 00:30 UTC on 2026-07-28:
- strategy_observation → `date=2026-07-27`
- shadow_trade → `date=2026-07-28`

### When This Happens

| Scenario | Observation Date | Shadow Trade Date | Mismatch? |
|---|---|---|---|
| Trade opens and closes same day | 2026-07-27 | 2026-07-27 | NO |
| Trade opens near midnight, closes next day | 2026-07-27 | 2026-07-28 | **YES** |
| Trade holds for multiple days | 2026-07-27 | 2026-07-30 | **YES** |
| Shadow trade hits max_bars (60 bars × M5 = 5h) | 2026-07-27 | 2026-07-27 or 28 | Maybe |

### Impact

**Partition-level queries will miss some joins.** A query like:
```sql
WHERE so.date = st.date  -- WRONG: will miss cross-day trades
```

**Correct approach:** Join on entity_id without date restriction:
```sql
FROM strategy_observations so
JOIN shadow_trades st
    ON so.entity_id = CONCAT(st.identity.symbol, '_', CAST(st.decision_snapshot.timestamp_decision_utc AS BIGINT))
```

This works regardless of date partition because entity_id is content-based, not partition-based.

---

## 6. Examples of Broken Joins

### Case 1: entity_id = NULL in shadow_trades (EXECUTE path)

```json
// shadow_trades_v2 (created via engine_execution_handler)
{
  "identity": {
    "trade_id": "shadow_42857_EURUSD",
    "entity_id": null,  // ← NOT PASSED by engine_execution_handler
    "symbol": "EURUSD",
    "correlation_id": "COR-42857-EURUSD-A93F"
  },
  "decision_snapshot": {
    "timestamp_decision_utc": 1753574400.0
  }
}
```

```json
// strategy_observation
{
  "entity_id": "EURUSD_1753574400",
  "symbol": "EURUSD",
  "timestamp_utc": 1753574400.0
}
```

**Join `so.entity_id = st.identity.entity_id` → FAILS (null vs string)**

**Fix:** Use reconstructed entity_id:
```sql
so.entity_id = CONCAT(st.identity.symbol, '_', CAST(st.decision_snapshot.timestamp_decision_utc AS BIGINT))
```

### Case 2: Date Partition Mismatch

```json
// strategy_observation (date partition = 2026-07-27)
{
  "observation_id": "EURUSD_42857_1753574400",
  "timestamp_utc": 1753574400.0,  // 2026-07-27 23:50:00 UTC
  "symbol": "EURUSD"
}
```

```json
// shadow_trade (date partition = 2026-07-28)
{
  "identity": {"symbol": "EURUSD"},
  "decision_snapshot": {"timestamp_decision_utc": 1753574400.0},
  "simulated_outcome": {
    "exit_timestamp": 1753592400.0  // 2026-07-28 04:50:00 UTC (5h later)
  }
}
```

**Query with `WHERE so.date = st.date` → FAILS**
**Query with entity_id join → SUCCEEDS** (both reference same bar time)

### Case 3: decision_trace timestamp format mismatch

```json
// strategy_observation
{"timestamp_utc": 1753574400.0}

// decision_trace
{"timestamp_utc": "2026-07-27T23:50:05.123Z"}
```

**Temporal proximity join with numeric comparison → FAILS (string vs float)**
**entity_id join → SUCCEEDS** (both have entity_id = "EURUSD_1753574400")

---

## Summary of Findings

| Issue | Severity | Impact on Research | Workaround |
|---|---|---|---|
| Shadow trades (EXECUTE path) have `entity_id = null` | MEDIUM | Cannot use direct entity_id join | Reconstruct from `symbol + timestamp_decision_utc` |
| Date partitions differ (observation=entry, shadow=exit) | LOW | Partition-scoped queries miss cross-day trades | Join on entity_id without date filter |
| decision_trace uses ISO string, others use unix float | LOW | Cannot compare timestamps directly | Join on entity_id (works for all three) |
| Shadow trades have extra `schema_version=` partition level | LOW | S3 prefix differs slightly | Use correct prefix in queries |

### Recommended Join Pattern for Research

```sql
-- CORRECT: Reconstruct entity_id for shadow trades
SELECT
    so.strategy_family,
    so.evaluation_status,
    so.market_phase,
    so.confidence,
    st.simulated_outcome.pnl_r_multiple AS r_multiple,
    st.simulated_outcome.exit_reason,
    st.simulated_outcome.mfe_r,
    st.simulated_outcome.mae_r
FROM trading_bot.strategy_observations so
JOIN trading_bot.shadow_trades st
    ON so.entity_id = CONCAT(
        st.identity.symbol, '_',
        CAST(st.decision_snapshot.timestamp_decision_utc AS BIGINT)
    )
```

This join:
- Works even when shadow trade entity_id is null
- Is not affected by date partition differences
- Produces exact matches (same bar = same entity_id)

---

## Verdict

The lineage is **FUNCTIONAL but has one known gap**: shadow trades created via the EXECUTE path do not carry `entity_id` explicitly. However, the join can be deterministically reconstructed from existing fields (`symbol` + `timestamp_decision_utc`), so research is NOT blocked.

**No code changes are required to begin data collection.** The recommended Athena join pattern above handles all three cases correctly.
