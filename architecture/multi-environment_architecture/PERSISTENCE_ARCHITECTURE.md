# Persistence Architecture — Multi-Environment

## Version: 1.0
## Date: 2026-07-23

---

## 1. Core Principle

Every persisted record carries a `opportunity_id` (linking to the shared market assessment) and an `environment_id` (identifying which environment made the decision). This enables:
- **Single-opportunity, multi-outcome analysis** ("3 environments saw this setup — 2 accepted, 1 rejected")
- **Per-environment performance tracking** (win rate, expectancy per profile)
- **Cross-environment comparison** ("would FTMO rules have improved retail outcomes?")

---

## 2. Canonical Entities

### Universal (computed once, shared across environments)

| Entity | Storage Path | Partitioning | Contains |
|--------|-------------|--------------|----------|
| **OpportunityAssessment** | `opportunities/symbol={S}/date={D}/` | Symbol + date | Market analysis: score, components, pattern, regime, confidence |
| **MarketContext** | `market_context/symbol={S}/date={D}/` | Symbol + date | H4/H1/M15/M5 snapshot |
| **Event Stream** | `events/date={D}/` | Date | Candles, feature updates |

### Per-Environment (one record per environment per opportunity)

| Entity | Storage Path | Partitioning | Contains |
|--------|-------------|--------------|----------|
| **Decision** | `decisions/env={E}/symbol={S}/date={D}/` | Env + symbol + date | ACCEPT/REJECT + reasoning + opportunity_id |
| **ExecutionResult** | `execution_results/env={E}/symbol={S}/date={D}/` | Env + symbol + date | Fill, slippage, retcode |
| **TradeTruth** | `trades/env={E}/symbol={S}/date={D}/` | Env + symbol + date | Entry/exit, R-multiple, P&L |
| **TradeJournal** | `trade_journal/env={E}/date={D}/` | Env + date | Complete trade record |

---

## 3. ID Relationships

```
opportunity_id (universal)
  ├── decision_id (per env)
  │     ├── correlation_id (per env)
  │     │     ├── execution_id (per env)
  │     │     └── trade_id (per env)
  │     └── trade_truth_id (per env)
  └── (another env's decision_id)
```

**Join keys:**
- `opportunity_id` links all environments' decisions about the same setup
- `correlation_id` links a single environment's decision → execution → outcome
- `environment_id` partitions all queries

---

## 4. S3 Layout (Option C — Shared + Partitioned)

```
s3://trading-bot-data-mk1/
├── opportunities/                     # Universal (ONE per opportunity)
│   └── symbol=EURUSD/
│       └── date=2026-07-23/
│           └── part-000.jsonl
│
├── market_context/                    # Universal
│   └── symbol=EURUSD/
│       └── date=2026-07-23/
│           └── part-000.jsonl
│
├── decisions/                         # Per-environment
│   └── env=retail_growth_v1/
│       └── symbol=EURUSD/
│           └── date=2026-07-23/
│               └── part-000.jsonl
│
├── execution_results/                 # Per-environment
│   └── env=retail_growth_v1/
│       └── symbol=EURUSD/
│           └── date=2026-07-23/
│               └── part-000.jsonl
│
├── trades/                            # Per-environment
│   └── env=retail_growth_v1/
│       └── symbol=EURUSD/
│           └── date=2026-07-23/
│               └── part-000.jsonl
│
└── events/                            # Universal
    └── date=2026-07-23/
        └── part-000.jsonl
```

**Why Option C (shared + partitioned):**
- Opportunities stored ONCE (no duplication, disk-efficient)
- Per-env data partitioned (fast filtered queries)
- Athena can JOIN `opportunities` with any environment's `decisions`
- Adding a new environment = new partition prefix, no schema change
- Historical replay works per-environment or cross-environment

---

## 5. Athena Query Examples

### Per-Environment Performance
```sql
SELECT 
    t.environment_id,
    COUNT(*) as trades,
    AVG(t.r_multiple) as avg_r,
    SUM(CASE WHEN t.r_multiple > 0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*) as win_rate
FROM trades t
WHERE t.environment_id = 'retail_growth_v1'
  AND t.date >= '2026-07-01'
GROUP BY t.environment_id
```

### Cross-Environment Opportunity Analysis
```sql
SELECT 
    o.opportunity_id,
    o.symbol,
    o.score_neutral,
    d_retail.decision as retail_decision,
    d_ftmo.decision as ftmo_decision,
    t_retail.r_multiple as retail_r,
    t_ftmo.r_multiple as ftmo_r
FROM opportunities o
LEFT JOIN decisions d_retail ON o.opportunity_id = d_retail.opportunity_id 
    AND d_retail.environment_id = 'retail_growth_v1'
LEFT JOIN decisions d_ftmo ON o.opportunity_id = d_ftmo.opportunity_id 
    AND d_ftmo.environment_id = 'ftmo_v1'
LEFT JOIN trades t_retail ON d_retail.correlation_id = t_retail.correlation_id
LEFT JOIN trades t_ftmo ON d_ftmo.correlation_id = t_ftmo.correlation_id
WHERE o.date = '2026-07-23'
```

### "Would the EV gate have helped?"
```sql
SELECT 
    d.ev_would_have_blocked,
    COUNT(*) as trades,
    AVG(t.r_multiple) as avg_r
FROM decisions d
JOIN trades t ON d.correlation_id = t.correlation_id
WHERE d.environment_id = 'retail_growth_v1'
GROUP BY d.ev_would_have_blocked
```

---

## 6. Migration from Current Schema

| Current Path | New Path | Change |
|-------------|----------|--------|
| `logs/trade_truth/{SYMBOL}/{DATE}.jsonl` | `logs/trade_truth/env=default/{SYMBOL}/{DATE}.jsonl` | Add env partition |
| `logs/execution_results/{SYMBOL}/{DATE}.jsonl` | `logs/execution_results/env=default/{SYMBOL}/{DATE}.jsonl` | Add env partition |
| `logs/decision_audit/{SYMBOL}_{DATE}.jsonl` | `logs/decisions/env=default/{SYMBOL}/{DATE}.jsonl` | Rename + partition |

**Phase 0 (no structural change):** Add `environment_id: "default"` field to all new records. Existing records without the field are implicitly `"default"`.

**Phase 1 (directory restructure):** Move to partitioned directories. Old records remain readable (backward-compatible reader falls back to legacy paths).

---

## 7. Record Schemas

### OpportunityAssessment Record (universal)
```json
{
    "opportunity_id": "EURUSD_1784752800",
    "symbol": "EURUSD",
    "timestamp_utc": 1784752800,
    "score_neutral": 0.672,
    "score_strategy": 0.672,
    "pattern": "THREE_BLACK_CROWS",
    "side": "SELL",
    "regime": "TRANSITIONAL",
    "regime_confidence": 0.30,
    "market_state": "TRANSITIONAL",
    "components": {...}
}
```

### Decision Record (per-environment)
```json
{
    "decision_id": "dec_abc123",
    "opportunity_id": "EURUSD_1784752800",
    "environment_id": "retail_growth_v1",
    "profile_version": "1.0.0",
    "decision": "EXECUTE",
    "ev": 0.000016,
    "ev_positive": true,
    "p_success": 0.308,
    "policy_trade_allowed": true,
    "ev_gate_enabled": false,
    "ev_experiment_mode": true,
    "correlation_id": "COR-20260722-1-EURUSD-ACFE"
}
```

### TradeTruth Record (per-environment, unchanged schema)
```json
{
    "schema_version": "trade_truth_v3",
    "environment_id": "retail_growth_v1",
    "identity": {
        "trade_id": "pos_53297241",
        "correlation_id": "COR-20260722-1-EURUSD-ACFE",
        "symbol": "EURUSD"
    },
    "execution": {...},
    "timestamps": {...},
    "outcome": {...},
    "exit": {...}
}
```
