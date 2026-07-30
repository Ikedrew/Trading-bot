# Project Decision Tree

---

## Current Stage: FOUNDATION VALIDATION — CONCLUDED

**Date:** 2026-07-27
**Result:** ❌ STAGE 0 FAILED
**Architecture:** M5 candlestick pattern detection + 10-factor scoring + strategy activation
**Proven:** Entry signal has no directional predictive value. No filter creates positive EV after costs.

---

## Decision Tree

```
┌─────────────────────────────────────────────────────────┐
│  STAGE 0: FOUNDATION VALIDATION — CONCLUDED             │
│                                                         │
│  Tests completed:                                       │
│  ✅ CE1 — Cost-adjusted EV = -0.70R                     │
│  ✅ EQ1 — Zero positive subsets across all dimensions   │
│  ✅ MS1-3 — Signal has no direction at any scale        │
│  ✅ SV1 — Structure geometry helps costs, not signal    │
│  ✅ EI10 — Best pre-registered filter still negative    │
│                                                         │
│  RESULT: ❌ FAIL                                        │
│  The current information set cannot identify            │
│  profitable entries after transaction costs.            │
│                                                         │
│  DECISION: Architecture transition required.            │
└───────────────────────┬─────────────────────────────────┘
                        │
                        ▼
┌───────────────────────────────────────────────────────┐
│  TRANSITION: ARCHITECTURAL RESEARCH                    │
│                                                       │
│  Current entry model: FALSIFIED                       │
│  (Pattern shape does not predict direction)           │
│                                                       │
│  Next steps (ordered):                                │
│                                                       │
│  □ EI1 — Bar-1 velocity test (last quick check)      │
│    "Does first-bar movement predict outcome?"         │
│    If YES → time-based exit research path             │
│    If NO → confirm signal is purely random            │
│                                                       │
│  □ PATH 1 — Context as signal                         │
│    Direction from H1 structure (not pattern)          │
│    Pattern becomes timing trigger only                │
│    Requires: new directional source                   │
│                                                       │
│  □ PATH 2 — Higher timeframe                          │
│    Move signal detection to H1/H4                     │
│    Where movement scale >> spread                     │
│    Requires: new pattern/signal detection             │
│                                                       │
│  □ PATH 3 — Probabilistic model                       │
│    Replace deterministic direction assignment          │
│    with statistical probability estimation            │
│    Requires: ML infrastructure                        │
└───────────────────────────────────────────────────────┘
```

---

## Current Status

| Stage | Status | Evidence |
|-------|--------|----------|
| **Stage 0** | ⏳ **IN PROGRESS** | CE1/EQ1/MS1-3 complete (all negative). EI experiments designed but not run. |
| Stage 1 | 🔒 LOCKED | Requires Stage 0 pass |
| Stage 2 | 🔒 LOCKED | Requires Stage 1 |
| Stage 3 | 🔒 LOCKED | Requires Stage 2 |
| Stage 4 | 🔒 LOCKED | Requires Stage 3 |

---

## Remaining Foundation Tests

| Test | Purpose | Data Available? | Estimated Result |
|------|---------|----------------|-----------------|
| **EI6** | risk≥6 pips filter | ✅ 100% | Known: raw EV=-0.02R, cost=-0.17R → adj EV≈-0.19R. Likely FAIL. |
| **EI1** | Bar-1 velocity predicts outcome | ✅ 100% (state_progression) | UNKNOWN — not yet computed |
| **EI10** | Combined filter (risk + phase + alignment + score) | ✅ 89% | UNKNOWN — subset may be small |
| **E5** | Walk-forward validation | ✅ Runner exists | Only needed if EI finds positive subset |

---

## What Happens If Foundation Fails

If all EI experiments produce negative cost-adjusted EV:

**The conclusion is:**

> "The M5 candlestick pattern architecture does not contain exploitable directional information at a level that overcomes FX transaction costs on 3-10 pip risk geometry."

**This is not a failure of the research engine.** The research engine correctly identified this truth. The system successfully:
- Built comprehensive observation infrastructure
- Collected 867 CURRENT-epoch trades with full lifecycle data
- Ran controlled experiments with proper statistical methodology
- Eliminated epoch contamination
- Proved negative EV across all tested dimensions
- Identified the root cause (zero directional signal + catastrophic cost ratio)

**The failure is in the hypothesis itself** — that M5 candlestick patterns predict short-term FX price direction with sufficient reliability to overcome the bid-ask spread.

---

## Re-Architecture Options (if Foundation fails)

| Option | What Changes | Risk | Evidence Needed |
|--------|-------------|------|----------------|
| **A) H1/H4 timeframe** | Pattern detection on higher TF where moves >> spread | Medium | Requires new pattern detector + new data + new validation cycle |
| **B) Order flow / volume** | Replace candlestick patterns with order flow signals | High | Requires new data source (not available on retail MT5) |
| **C) Statistical entry** | Replace pattern detection with mean-reversion or momentum models | Medium | Requires ML/statistical modelling layer |
| **D) Wider risk geometry** | Keep M5 patterns but use M15/H1 structure for SL (10-20+ pips) | Low | Only changes risk/levels.py — testable with current infra |
| **E) Halt project** | Conclude FX scalping with candlestick patterns is not viable | — | Current evidence already supports this |

**Option D is the lowest-risk next step** if EI experiments fail — it keeps the existing detection but changes where the SL is placed (using M15/H1 structure levels instead of M5 candle geometry). This has partial evidence from the Horizon INTRADAY results (11.3-pip SL reduces cost ratio to 12%).

---

## Success Criteria (Gate to Stage 1)

All of the following must be TRUE:

```
□ A specific filter combination identified
□ Cost-adjusted EV > 0 in the filtered subset
□ 95% confidence interval entirely above zero
□ n ≥ 100 in the filtered subset
□ Walk-forward (last 40%) confirms positive EV
□ Filter variables are all knowable at entry time (no look-ahead)
□ Effect is not explained by a single outlier trade
```

If even ONE criterion fails, the foundation remains unvalidated and the project cannot proceed to optimisation.

---

## Timeline

| Action | When | Duration |
|--------|------|----------|
| Run EI6, EI1, EI10 | Now | ~1 hour |
| Evaluate results | Immediately after | 30 min |
| If positive: run E5 walk-forward | Same day | 30 min |
| **Decision point** | **Today** | — |
| If FAIL: evaluate Option D (wider SL geometry) | Next session | 2-4 hours |
| If Option D FAIL: project architecture decision | — | — |
