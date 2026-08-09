# V10 PnL Normalisation Report

Generated: 2026-08-09T00:10:49.712757+00:00
**Status: PASS**

## Canonical PnL Definition

```
net_realised_pnl = gross_profit + commission + swap + fees
```

## Before (Governance Warning)

| Metric | Value |
|---|---|
| MT5 field | broker_net_profit (all 106 entries) |
| Research field | broker_pnl (94 trades, gross only) |
| MT5 total | $685.49 |
| Research total | $812.38 |
| Difference | 18.5% |
| Note | Comparing different populations and different PnL definitions |

## After (Canonical net_realised_pnl)

| Metric | Value |
|---|---|
| Canonical field | net_realised_pnl = gross_profit + commission + swap + fees |
| MT5 net total | $714.27 |
| Research net total | $714.27 |
| Difference | $0.00 (0.00%) |
| Population | 94 matched trades (same population) |

---