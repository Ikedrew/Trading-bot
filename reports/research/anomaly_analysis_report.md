# Research Dataset Anomaly Analysis

Generated: 2026-08-06T00:57:21.522075+00:00

## Dataset Summary

| Dataset | Trades | Description |
|---|---|---|
| Full | 84 | All validated trades (source of truth) |
| FX Only | 82 | Core forex system |
| Normalised | 77 | Without extreme events |
| Index Only | 2 | Index trades separately |
| Flagged | 7 | Trades with anomalies |

## Flagged Trade Summary

| Reason | Count |
|---|---|
| EXTREME_PNL | 4 |
| EXTREME_R_MULTIPLE | 3 |
| NON_FX_INSTRUMENT | 2 |

## Impact Analysis

| Metric | Full | FX Only | Normalised | Index Only |
|---|---|---|---|---|
| count | 84 | 82 | 77 | 2 |
| win_rate | 38% | 37% | 38% | 100% |
| total_pnl | $1637.94 | $-77.51 | $4.59 | $1715.45 |
| average_r | -0.1380 | -0.1944 | -0.1350 | 2.1769 |
| median_r | -1.0000 | -1.0000 | -1.0000 | 2.1769 |
| profit_factor | 17.1600 | 0.2400 | 1.2800 | 999.0000 |

## Key Questions Answered

### 1. Is V10 profitable because of repeatable behaviour?
YES — Normalised dataset (no extremes) shows $4.59 profit

### 2. Are results dependent on extreme events?
Extreme events contribute $1633.35 to total PnL (full=$1637.94, normalised=$4.59)
**YES** — extreme events dominate results

### 3. Are FX and index behaviour different?
FX average R: -0.1944 | Index average R: 2.1769
**YES** — significantly different behaviour

### 4. Should future research separate instruments?
Not critical yet — index sample too small for reliable separate analysis

## Flagged Trades Detail

| Trade ID | Symbol | R | PnL | Reasons |
|---|---|---|---|---|
| pos_53303078 | GBPUSD | -4.50 | $-1.44 | EXTREME_R_MULTIPLE |
| pos_53860621 | USDJPY | -1.04 | $-58.00 | EXTREME_PNL |
| pos_53892087 | USDCHF | -4.29 | $-1.33 | EXTREME_R_MULTIPLE |
| pos_54025076 | USDCAD | +5.45 | $2.67 | EXTREME_R_MULTIPLE |
| pos_54607268 | AUDUSD | -1.17 | $-24.00 | EXTREME_PNL |
| pos_82095735 | US500 | +2.11 | $843.48 | EXTREME_PNL, NON_FX_INSTRUMENT |
| pos_82098818 | US500 | +2.25 | $871.97 | EXTREME_PNL, NON_FX_INSTRUMENT |

---
*Anomaly classification is for research filtering only. No trades were removed or modified.*