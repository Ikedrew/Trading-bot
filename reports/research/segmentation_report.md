# V10 Research Dataset Segmentation Report (V2)

Generated: 2026-08-07T16:40:00.929175+00:00
Source: logs\research_ready_trade_dataset\research_ready_trades.jsonl
Total trades: 94 (Normal: 90, Flagged: 4)
Date range: 2026-07-22 17:40 -> 2026-08-06 04:45

## Anomaly Classification

| Reason | Count |
|---|---|
| EXTREME_PNL | 2 |
| DATA_INCONSISTENCY | 1 |
| EXTREME_R_MULTIPLE | 1 |

Impact: FULL_RAW expectancy=-0.1758R | STANDARD expectancy=-0.2814R | diff=+0.1056R

## Dataset Views

| View | Trades | Win% | Avg R | Expectancy | PF | Total PnL |
|---|---|---|---|---|---|---|
| FULL_RAW | 94 | 36% | -0.18 | -0.18 | 1.7 | $714.27 |
| STANDARD | 90 | 34% | -0.28 | -0.28 | 0.0 | $-977.47 |
| ANOMALY_ONLY | 4 | 75% | +2.20 | +2.20 | inf | $1691.74 |
| FX | 85 | 34% | -0.30 | -0.30 | 0.1 | $-235.51 |
| INDEX | 2 | 0% | -1.02 | -1.02 | 0.0 | $-369.81 |
| COMMODITY | 3 | 67% | +0.86 | +0.86 | 0.1 | $-372.15 |

## Instrument Summary

| Symbol | N | Win% | Avg R | Expectancy | PF | PnL | Confidence |
|---|---|---|---|---|---|---|---|
| NZDUSD | 20 | 50% | +0.17 | +0.17 | 0.1 | $-33.51 | MEDIUM |
| USDCAD | 18 | 33% | -0.27 | -0.27 | 0.0 | $-44.83 | MEDIUM |
| USDCHF | 15 | 33% | -0.50 | -0.50 | 0.5 | $-2.25 | MEDIUM |
| AUDUSD | 14 | 21% | -0.37 | -0.37 | 0.1 | $-23.78 | MEDIUM |
| EURUSD | 8 | 25% | -0.46 | -0.46 | 0.0 | $-94.29 | LOW |
| GBPUSD | 8 | 38% | -0.73 | -0.73 | 0.1 | $-36.47 | LOW |
| XAUUSD | 3 | 67% | +0.86 | +0.86 | 0.1 | $-372.15 | LOW |
| US500 | 2 | 0% | -1.02 | -1.02 | 0.0 | $-369.81 | LOW |
| USDJPY | 2 | 0% | -1.07 | -1.07 | 0.0 | $-0.38 | LOW |

## Instrument Rankings

### By Expectancy

1. **XAUUSD** -- +0.86R (n=3, LOW)
2. **NZDUSD** -- +0.17R (n=20, MEDIUM)
3. **USDCAD** -- -0.27R (n=18, MEDIUM)
4. **AUDUSD** -- -0.37R (n=14, MEDIUM)
5. **EURUSD** -- -0.46R (n=8, LOW)

### By Win Rate

1. **XAUUSD** -- 67% (n=3)
2. **NZDUSD** -- 50% (n=20)
3. **GBPUSD** -- 38% (n=8)
4. **USDCAD** -- 33% (n=18)
5. **USDCHF** -- 33% (n=15)

---
*Views stored in logs/research_views/ for experiment consumption*