# V10 Macro Context Evidence Contract

## Design Principle

Macro context tells the story BEFORE the trade. It does not decide the trade.

```
MN1 → "What world are we in?"
W1  → "What structural edges define this week?"
D1  → "What happened today and what does today look like?"
─────────────────────────────────────────────────────────
H4  → regime authority (decides)
H1  → structural authority (decides)
M15 → formation authority (decides)
M5  → execution authority (decides)
```

---

## MN1 — Monthly Context

### Market Question

> "Is there a dominant multi-month directional force, or is the market range-bound at the macro scale?"

### Evidence Produced

| Field | Type | Description |
|---|---|---|
| `monthly_trend` | str | BULLISH / BEARISH / NEUTRAL |
| `monthly_trend_strength` | float | 0.0–1.0 (how persistent is the direction?) |
| `monthly_phase` | str | IMPULSE / PULLBACK / EXHAUSTION / CONSOLIDATION |

### What It Answers (and nothing else)

- Is there a secular trend that has persisted for months?
- Is the monthly candle currently impulsing, pulling back, or consolidating?
- How strong is the macro directional force?

### What It Does NOT Answer

- Whether to trade today
- Which direction to trade this session
- Whether a specific H1 setup is valid

### Consumption Rules

| Consumer | How It Uses MN1 | Influence Type |
|---|---|---|
| Strategy confidence modifier | Trade aligned with monthly trend → confidence +0.05–0.10. Trade opposing monthly impulse → confidence -0.05–0.10 | **Confidence** |
| Decision persistence | Recorded for research — "was this trade with or against the monthly?" | **Narrative** |
| Risk sizing | None | **NONE** |
| Strategy selection | None | **NONE** |
| Opportunity engine | None | **NONE** |

### Refresh Cadence

Once per day. Monthly bar moves negligibly within a single trading day.

---

## W1 — Weekly Context

### Market Question

> "What are the structural boundaries this week, and does the week have directional conviction?"

### Evidence Produced

| Field | Type | Description |
|---|---|---|
| `weekly_trend` | str | BULLISH / BEARISH / NEUTRAL |
| `weekly_trend_strength` | float | 0.0–1.0 |
| `weekly_swing_high` | float | Last confirmed weekly swing high (price level) |
| `weekly_swing_low` | float | Last confirmed weekly swing low (price level) |
| `weekly_range_position` | float | 0.0–1.0 (where is price within the weekly range?) |
| `weekly_phase` | str | IMPULSE / PULLBACK / CONSOLIDATION |

### What It Answers

- What are the multi-day structural boundaries visible to all participants?
- Is this week trending or consolidating?
- Is price at the top or bottom of the weekly range?
- Where are the "obvious" levels that attract institutional interest?

### What It Does NOT Answer

- Whether a specific intraday trade is valid
- Where to place a stop (H1 owns that)
- What strategy to select

### Consumption Rules

| Consumer | How It Uses W1 | Influence Type |
|---|---|---|
| Strategy confidence modifier | Strategy direction aligned with weekly trend → +0.10. At weekly extreme in range strategy → +0.10 (supports reversion). Breakout direction aligned with weekly → +0.10 | **Confidence** |
| Decision persistence | Recorded for research — "where was this trade relative to weekly structure?" | **Narrative** |
| Entry engine (target reference) | `weekly_swing_high` / `weekly_swing_low` as EXTENDED horizon target candidates (alternative when H1 targets unavailable) | **Target enrichment** (fallback only) |
| Risk sizing | If trade opposes weekly + monthly alignment → reduce position by 25% | **Risk sizing** (conservative reduction only, never increase) |
| Strategy selection | None | **NONE** |

### Refresh Cadence

Once per H4 bar. Weekly structure changes slowly — no need to recalculate every M5.

---

## D1 — Daily Context

### Market Question

> "What is today's narrative? What range has today established? What direction did yesterday close?"

### Evidence Produced

| Field | Type | Description |
|---|---|---|
| `daily_bias` | str | BULLISH / BEARISH / NEUTRAL |
| `daily_bias_strength` | float | 0.0–1.0 |
| `daily_high` | float | Today's high (evolves intraday) |
| `daily_low` | float | Today's low (evolves intraday) |
| `daily_prev_close` | float | Yesterday's closing price |
| `daily_range_position` | float | 0.0–1.0 (where in today's range is current price?) |
| `daily_phase` | str | OPENING / IMPULSE / RANGE / EXHAUSTION |
| `daily_atr` | float | Average daily range (for context on whether today is normal or expanded) |

### What It Answers

- Is today a trend day or a range day?
- Where is price relative to today's range?
- Did the market gap above/below yesterday's close?
- Is today's range normal or unusually wide/narrow?

### What It Does NOT Answer

- Which M5 bar to enter on
- Whether H1 BOS is valid
- What strategy to select

### Consumption Rules

| Consumer | How It Uses D1 | Influence Type |
|---|---|---|
| Strategy confidence modifier | Trade direction aligned with daily bias → +0.05. Trade at daily extreme with reversion strategy → +0.10 | **Confidence** |
| Opportunity engine enrichment | `daily_range_position` at extreme (>0.85 or <0.15) added as supporting context to opportunity quality | **Supporting evidence** (not required) |
| Decision persistence | Full daily context recorded — enables research question: "Do trades taken early in the day (near open) perform differently than late?" | **Narrative** |
| Entry engine (target reference) | `daily_high` / `daily_low` as SCALP/INTRADAY target candidates (when H1 targets unavailable) | **Target enrichment** (fallback only) |
| Risk sizing | If daily ATR is 2x normal → reduce position by 25% (unusually volatile day) | **Risk sizing** (protective reduction only) |
| Strategy selection | None | **NONE** |

### Refresh Cadence

Once per H1 bar. Daily range (high/low) evolves throughout the day — needs periodic update.

---

## Influence Matrix

| Macro Layer | Affects Narrative? | Affects Confidence? | Affects Risk Sizing? | Affects Strategy Selection? | Affects Opportunity? | Affects Entry? |
|---|---|---|---|---|---|---|
| **MN1** | YES | YES (±0.10 max) | NO | NO | NO | NO |
| **W1** | YES | YES (±0.10 max) | YES (reduction only) | NO | NO | YES (target fallback) |
| **D1** | YES | YES (±0.10 max) | YES (reduction only) | NO | Supporting only | YES (target fallback) |

### Hard Constraints

1. **No macro layer can prevent a strategy from being selected.** If H4+H1+M15+M5 evidence is sufficient, the trade proceeds. Macro can reduce confidence but never block.

2. **Risk sizing influence is PROTECTIVE only.** Macro can reduce position size (when evidence conflicts) but NEVER increase it. The base sizing comes from the risk engine's account/broker calculations.

3. **Maximum cumulative macro influence on confidence: ±0.20.** Even if MN+W1+D1 all oppose, the maximum confidence penalty is -0.20. A 0.80 confidence trade becomes 0.60 minimum — still tradeable.

4. **Target enrichment is FALLBACK only.** Weekly/daily levels are used as targets ONLY when H1-level targets are unavailable (zero). They never override H1-sourced targets.

---

## Research Value

Every V10 decision record will include macro context, enabling research questions:

| Question | Enabled By |
|---|---|
| "Do trades aligned with the monthly trend have higher R-multiples?" | monthly_trend vs outcome |
| "Do reversion trades at weekly extremes outperform those at weekly equilibrium?" | weekly_range_position vs outcome |
| "Is there a time-of-day edge relative to daily phase?" | daily_phase vs entry_time vs outcome |
| "Do trades against daily bias have higher failure rates?" | daily_bias vs direction vs outcome |
| "Does weekly structure provide better targets than H1?" | weekly_swing_high/low vs actual exit level |

---

## What This Layer Does NOT Become

| Anti-Pattern | Constraint |
|---|---|
| "Monthly must agree for trade" | FORBIDDEN — monthly is narrative, not authority |
| "Weekly trend required for trend continuation" | FORBIDDEN — H4 is trend authority, not weekly |
| "Daily range_position gates opportunity" | FORBIDDEN — M15 range_position is opportunity authority |
| "Macro conflict = no trade" | FORBIDDEN — macro reduces confidence, never blocks |
| "Weekly swing levels replace H1 for stop placement" | FORBIDDEN — H1 owns stop structure. Weekly only enriches targets. |

---

## Minimum Viable Implementation Sequence

| Step | Deliverable | Effort |
|---|---|---|
| 1 | Add `_TF_D1`, `_TF_W1`, `_TF_MN` to TimeframeCache | 10 min |
| 2 | Configure fetch (100 daily, 52 weekly, 24 monthly candles) | 10 min |
| 3 | Reuse `analyze_regime()` for MN → monthly_trend/strength/phase | 15 min |
| 4 | Reuse `analyze_bias()` for W1 → weekly_trend/swings/range_position | 15 min |
| 5 | Reuse `analyze_regime()` + `analyze_bias()` for D1 | 15 min |
| 6 | Build `MacroSnapshot` dataclass, wire into `HTFContext` | 20 min |
| 7 | Add confidence modifiers to strategy engine (post-selection) | 30 min |
| 8 | Add macro fields to decision persistence | 15 min |
| 9 | Add target fallbacks to entry engine | 20 min |
| 10 | Tests | 30 min |

**Total estimated effort:** ~3 hours for full implementation.

---

## Summary

The macro context layer provides **the story** — not the decision. It answers "what world is this trade operating inside?" and enriches every decision record with context that enables future research. It makes existing strategies smarter (via confidence) and more protected (via risk reduction) without making them more restrictive.
