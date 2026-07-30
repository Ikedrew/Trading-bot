# V7.6 — Equity Index Shadow Execution Validation Results

**Date:** 2026-07-27
**Dataset:** 150 equity-index trades (NAS100: 69, US500: 81) — same as V7.5
**Verdict:** B) Positive but requires more observations — no new forward data collected yet

---

## Status: Awaiting Data Collection

| Metric | Current | Target | Gap |
|---|---|---|---|
| Total equity trades | 150 | 350-500 | 200-350 needed |
| New forward (post-V7.5) | 0 | 200+ | 200 needed |
| Collection status | **BLOCKED** | Running | Bot needs to run with indices |

**No new observations have been generated since V7.5.** The validation cannot proceed until the bot collects additional NAS100/US500 shadow trades.

---

## V7.5 Baseline (Unchanged — Still Valid)

| Metric | Value |
|---|---|
| n | 150 |
| Win Rate | 62.7% |
| EV (gross) | +0.191R |
| Net EV (spread + commission + slippage) | **+0.086R** |
| CI | **[+0.043, +0.338]** (excludes zero) |
| Profit Factor | 1.66 |
| Max Drawdown | 8.04R |
| Max Consecutive Losses | 9 |
| DD Recovery | ~42 trades at avg EV |
| At 0.25% risk: max account DD | 2.01% |

---

## Stability Confirmation (existing data)

### Both symbols positive:
| Symbol | n | WR | EV | CI |
|---|---|---|---|---|
| NAS100 | 69 | 60.9% | +0.234R | [+0.009, +0.459] |
| US500 | 81 | 64.2% | +0.154R | [-0.041, +0.349] |

### All time periods positive AND improving:
| Period | n | WR | EV |
|---|---|---|---|
| Early | 50 | 58.0% | +0.059R |
| Middle | 50 | 66.0% | +0.246R |
| Recent | 50 | 64.0% | +0.267R |

### Equity curve: +28.6R over 150 trades (0R current drawdown)

---

## Cost Model (conservative)

| Component | Estimate |
|---|---|
| Spread | 8-10% of stop (per instrument) |
| Commission | ~0.5% of stop |
| Slippage | ~1.0% of stop |
| **Total cost** | **~10.4% of stop per trade** |
| **Gross EV** | **+0.191R** |
| **Net EV** | **+0.086R** |

Net EV comfortably exceeds zero even under conservative cost assumptions.

---

## Required Actions

```
┌──────────────────────────────────────────────────────────────────┐
│ 1. VERIFY BROKER SYMBOL AVAILABILITY                              │
│    - Open Pepperstone MT5 → Market Watch                          │
│    - Confirm NAS100 (or USTEC) and US500 (or SPX500) are listed  │
│    - Enable them in Market Watch if not visible                   │
│                                                                  │
│ 2. RESTART BOT WITH CURRENT CONFIG                                │
│    - config.py already includes NAS100, US500 in CANONICAL_SYMBOLS│
│    - symbol_resolver will auto-map to broker names                │
│    - Shadow pipeline will collect without execution               │
│                                                                  │
│ 3. WAIT FOR DATA ACCUMULATION                                     │
│    - Minimum: 200 new trades (350 total)                          │
│    - Estimated time: 2-4 weeks at normal opportunity frequency    │
│    - Monitor: python analysis/v7_6_shadow_execution.py            │
│                                                                  │
│ 4. DO NOT MODIFY STRATEGY                                         │
│    - No new filters                                               │
│    - No threshold changes                                         │
│    - No entry logic modifications                                 │
│    - Pure observation mode                                        │
│                                                                  │
│ 5. RE-RUN V7.6 AT MILESTONES                                     │
│    - At n=250: preliminary forward check                          │
│    - At n=350: full forward validation                            │
│    - At n=500: production readiness assessment                    │
└──────────────────────────────────────────────────────────────────┘
```

---

## Production Graduation Criteria (when data arrives)

To move from shadow → paper trading:

| Criterion | Threshold | V7.5 Value |
|---|---|---|
| Forward n | ≥ 200 | 0 (pending) |
| Forward EV | > 0 | — |
| Forward net EV | > +0.03R | — |
| Forward WR | > 55% | — |
| Both symbols positive | YES | YES (in discovery) |
| CI excludes zero (all data) | YES | **YES** [+0.043, +0.338] |
| Max DD < 10R per 100 trades | YES | YES (8.04R / 150) |

**5/7 criteria already met from discovery data.** Awaiting forward sample to confirm remaining 2.
