# Strategy Intelligence Dataset

## Why This Dataset Exists

The Strategy Intelligence Loop generates observations each market cycle recording which strategy conditions were present. These observations must become a permanent research dataset — not ephemeral in-memory data — so that research queries can answer: "When these conditions appeared historically, what happened afterwards?"

This dataset sits alongside existing persistence layers:

```
s3://trading-bot-data-mk1/
    events/                      ← Market observations (candle, feature, session)
    assessments/                 ← Opportunity assessment snapshots
    trade_truth_graph/           ← Causal relationship graph
    strategy_observations/       ← Strategy condition occurrences (NEW)
```

---

## Storage Location

### Local (primary truth):
```
logs/strategy_observations/{SYMBOL}/{YYYY-MM-DD}.jsonl
```

### S3 (secondary mirror, Athena-queryable):
```
s3://trading-bot-data-mk1/strategy_observations/symbol={SYMBOL}/date={YYYY-MM-DD}/part-000.jsonl
```

### Partitioning:
- Hive-compatible: `symbol=X/date=Y/`
- Same format as events/, assessments/, trade_truth_graph/
- JSONL (one observation per line)
- ContentType: application/x-ndjson

---

## Schema (strategy_observation_v1)

| Field | Type | Description |
|-------|------|-------------|
| schema_version | string | "strategy_observation_v1" |
| observation_id | string | UUID — unique per observation |
| timestamp_utc | double | Unix seconds when observed |
| symbol | string | Trading pair (partition key) |
| cycle_id | int | Processing cycle number |
| market_phase | string | IMPULSE/PULLBACK/CONSOLIDATION/EXHAUSTION/REVERSAL |
| h4_regime | string | TRENDING/RANGING/TRANSITIONAL |
| h1_bias | string | BULLISH/BEARISH/NEUTRAL |
| direction | string | Unified direction |
| detected_pattern | string | Pattern name if detected |
| pattern_in_triggers | boolean | Is pattern in strategy's trigger set? |
| strategy_family | string | REVERSAL/MOMENTUM/CONTINUATION/BREAKOUT/etc. |
| candidate_strategies | array | Strategies evaluated with eligibility |
| strategy_conditions | object | Condition evaluation details |
| conditions_passed | int | Count of conditions that passed |
| conditions_failed | int | Count that explicitly failed |
| conditions_missing | int | Count with no data available |
| missing_data | array | Field names with missing data |
| evaluation_status | string | FULLY_MET/PARTIALLY_MET/NOT_MET/INCOMPLETE |
| confidence | double | Fraction of required conditions passed |
| tradability_score | double | Market tradability assessment |
| eligible_by_phase | boolean | Are environment conditions satisfied? |

---

## How Observations Flow Into S3

```
Each Market Cycle
    ↓
StrategyObserver.observe()
    ↓
StrategyObservation (in-memory)
    ↓
build_observation_record() → flat dict
    ↓
persist_strategy_observation()
    ├─ Local: logs/strategy_observations/{SYMBOL}/{DATE}.jsonl (PRIMARY)
    └─ S3: strategy_observations/symbol={SYMBOL}/date={DATE}/part-000.jsonl (MIRROR)
```

The S3 mirror is gated by `config.EVENT_STREAM_S3_MIRROR`. If disabled, only local persistence occurs. S3 failures never affect the trading pipeline (fire-and-forget pattern matching all other writers).

---

## How Athena Queries It

### Table Definition:
```sql
CREATE EXTERNAL TABLE IF NOT EXISTS trading_bot.strategy_observations (
    schema_version string,
    observation_id string,
    timestamp_utc double,
    market_phase string,
    h4_regime string,
    strategy_family string,
    evaluation_status string,
    confidence double,
    detected_pattern string,
    conditions_passed int,
    conditions_failed int,
    ...
)
PARTITIONED BY (symbol string, date string)
ROW FORMAT SERDE 'org.openx.data.jsonserde.JsonSerDe'
LOCATION 's3://trading-bot-data-mk1/strategy_observations/'
```

### Example: Today's observations
```sql
SELECT symbol, strategy_family, market_phase, evaluation_status, confidence
FROM trading_bot.strategy_observations
WHERE date = '2026-07-27'
ORDER BY timestamp_utc DESC
LIMIT 50;
```

### Example: Strategy occurrence frequency
```sql
SELECT strategy_family, evaluation_status, COUNT(*) AS observations
FROM trading_bot.strategy_observations
GROUP BY strategy_family, evaluation_status
ORDER BY observations DESC;
```

---

## How Future Outcome Linking Joins With Trade Truth

When outcome linkage is automated, observations join to shadow trade outcomes:

```sql
SELECT
    so.strategy_family,
    so.market_phase,
    so.evaluation_status,
    so.confidence,
    st.pnl_r_multiple,
    st.exit_reason
FROM trading_bot.strategy_observations so
JOIN trading_bot.shadow_trades_v2 st
    ON so.symbol = st.symbol
    AND ABS(so.timestamp_utc - st.entry_time) < 300
WHERE so.evaluation_status = 'FULLY_MET';
```

This creates the evidence pairs needed for research validation.

---

## Safety Boundaries

This dataset is PURELY OBSERVATIONAL:
- Written by `observation_persistence.py` (no pipeline imports)
- Read by research queries (offline)
- Never imported by DecisionEngine, RiskManager, or ExecutionOrchestrator
- S3 failure never affects trading
- Local write failure never affects trading
