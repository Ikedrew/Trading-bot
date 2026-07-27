# Observability Architecture — Multi-Environment

## Version: 1.0
## Date: 2026-07-23

---

## 1. Principle

Every trade decision includes a complete reasoning chain from opportunity detection through policy evaluation to execution outcome. An operator can reconstruct "why this happened" for any trade in any environment at any time.

---

## 2. Required Fields on Every Record

| Field | Purpose | Example |
|-------|---------|---------|
| `environment_id` | Which environment made this decision | `"retail_growth_v1"` |
| `opportunity_id` | Link to shared market assessment | `"EURUSD_1784752800"` |
| `correlation_id` | Unique per decision-execution chain | `"COR-20260722-1-EURUSD-ACFE"` |
| `profile_version` | Which policy config was active | `"1.0.0"` |
| `timestamp_utc` | When (UTC) | `1784742067` |

---

## 3. Discord Architecture

### Current (Single Environment)
```
#system-status     → startup/shutdown
#heartbeat         → alive signal
#errors            → exceptions
#decision-log      → trade decisions
#trade-execution   → fills
#risk-log          → guard blocks
#market-context    → regime changes
#performance-summary → daily P&L
```

### Multi-Environment Extension
```
#platform-status   → all environments startup/shutdown/health
#[env]-decisions   → per-environment trade decisions
#[env]-execution   → per-environment fills
#[env]-risk        → per-environment guard blocks
#[env]-performance → per-environment daily P&L
#market-context    → shared (universal)
#opportunities     → shared (what the engine sees)
```

**Tag format in shared channels:**
```
[retail_growth_v1] TRADE OPEN | EURUSD SELL @ 1.14108
[ftmo_v1] REJECTED | EURUSD | daily_loss_limit
```

---

## 4. Operator Dashboard Requirements

### Per-Environment View
- Active/paused status
- Open positions (with P&L)
- Today's trades + win rate
- Drawdown vs limit
- Daily P&L vs limit
- Last trade time
- Environment-specific alerts

### Global View
- All environments summary table
- Total capital deployed
- Total positions across all envs
- Opportunities detected vs accepted (conversion rate per env)
- Which environments are performing best/worst

### Forensic View (per-trade)
- Opportunity: what the market showed
- Decision: what this environment decided + why
- Execution: fill quality
- Outcome: R-multiple, P&L
- Comparison: how other environments handled the same opportunity

---

## 5. Decision Trace Content

Every decision (ACCEPT or REJECT) persists:

```json
{
    "environment_id": "retail_growth_v1",
    "profile_version": "1.0.0",
    "opportunity_id": "EURUSD_1784752800",
    "correlation_id": "COR-20260722-1-EURUSD-ACFE",
    
    "opportunity_summary": {
        "symbol": "EURUSD",
        "pattern": "THREE_BLACK_CROWS",
        "score": 0.672,
        "regime": "TRANSITIONAL",
        "confidence": 0.30
    },
    
    "policy_evaluation": {
        "ev_gate_enabled": true,
        "ev_gate_result": "PASS",
        "ev_value": 0.000016,
        "p_success": 0.308,
        "score_threshold": 0.35,
        "score_result": "PASS",
        "session_guard": "PASS"
    },
    
    "risk_evaluation": {
        "sl_pips": 3.1,
        "tp_pips": 9.3,
        "rr": 3.0,
        "volume": 0.01,
        "min_sl_guard": "PASS",
        "sizing_mode": "FIXED"
    },
    
    "guard_chain": {
        "daily_limit": "PASS",
        "cooldown": "PASS",
        "correlation": "PASS",
        "portfolio": "PASS",
        "prop_firm": "N/A"
    },
    
    "final_decision": "EXECUTE",
    "reasoning": "All gates passed. EV positive. Setup quality moderate."
}
```

---

## 6. Alert Escalation

| Severity | Condition | Action |
|----------|-----------|--------|
| INFO | Trade opened/closed | Discord per-env channel |
| WARNING | Guard block, unusual slippage | Discord risk channel |
| CRITICAL | Drawdown approaching limit | Discord + SMS/push |
| EMERGENCY | Prop firm rule violation risk | Kill switch + all channels |

---

## 7. Metrics Collection

| Metric | Granularity | Storage |
|--------|-------------|---------|
| Win rate | Per env, per symbol, per pattern | Daily aggregation |
| Expectancy (R) | Per env, rolling 20/50/100 trades | Equity curve file |
| Drawdown | Per env, real-time | In-memory + checkpoint |
| Execution quality | Per env, per broker | Slippage journal |
| Opportunity conversion | Per env (accepted/total) | Decision funnel |
| Guard hit rate | Per env, per guard | Rejection counters |
