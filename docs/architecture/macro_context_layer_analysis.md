# V10 Macro Context Layer — Design Analysis

## Purpose

Provide the "beginning of the market story" — what larger environment are we operating inside? Not a filter, not a gate, but a LENS through which existing strategy evidence is interpreted.

---

## 1. Architectural Placement

### Recommendation: Extend the existing TimeframeCache with a `MacroSnapshot`

The macro context should live as a **new snapshot type within the existing `HTFContext` interface** — not as a separate system.

```
Current HTFContext:
    regime: RegimeSnapshot (H4)
    bias: BiasSnapshot (H1)
    structure: StructureSnapshot (M15)

Proposed HTFContext:
    macro: MacroSnapshot (MN/W1/D1)  ← NEW
    regime: RegimeSnapshot (H4)
    bias: BiasSnapshot (H1)
    structure: StructureSnapshot (M15)
```

**Why here and not elsewhere:**

| Option | Verdict | Reasoning |
|---|---|---|
| Inside `MarketContext` | NO | MarketContext is rebuilt every M5 cycle — macro doesn't change that often |
| Inside `V10MarketState` | POSSIBLE | Would work but couples macro to V10 specifically |
| As a new `MacroSnapshot` in `HTFContext` | **YES** | Same lifecycle as H4/H1/M15 — cached, refreshed on new bar, consumed read-only |
| New standalone system | NO | Unnecessary complexity — the TimeframeCache already handles multi-TF caching |

The TimeframeCache already knows how to:
- Detect new bar closures per timeframe
- Fetch candles from MT5
- Run analyzers
- Cache snapshots

Adding D1/W1/MN is a natural extension of this existing system.

---

## 2. Timeframe Responsibilities

### Monthly (MN1)

**What it should answer:** "What is the dominant multi-month narrative?"

| Useful Data | Why | What It Should NOT Control |
|---|---|---|
| Monthly trend direction (BULLISH/BEARISH/NEUTRAL) | Tells you if the macro force favours longs or shorts | Never gate a trade — monthly can be wrong for weeks |
| Monthly trend strength (0–1) | Distinguishes strong secular trends from weak drift | Never override H4/H1 structure decisions |
| Monthly range position (0–1) | Where in the multi-month range is current price? | Never become a required strategy condition |
| Monthly candle phase (IMPULSE/PULLBACK/CONSOLIDATION) | Is the month trending or consolidating? | Never block intraday strategies |

**Refresh frequency:** Once per day (monthly bar changes extremely slowly)

### Weekly (W1)

**What it should answer:** "What is the weekly structural narrative?"

| Useful Data | Why | What It Should NOT Control |
|---|---|---|
| Weekly trend direction | This week's directional bias — more responsive than monthly | Never become a hard filter |
| Weekly swing_high / swing_low | Major structural boundaries visible to all participants | Never replace H1 structure authority |
| Weekly range_position | Where is price within the weekly range? | Never gate opportunity |
| Weekly displacement (did this week have a large move?) | Context for whether current price action is continuation or reaction | Never override M15/M5 signals |

**Refresh frequency:** Once per H4 bar (weekly changes slowly)

### Daily (D1)

**What it should answer:** "What happened today and yesterday? What is the daily structural context?"

| Useful Data | Why | What It Should NOT Control |
|---|---|---|
| Daily bias (BULLISH/BEARISH/NEUTRAL) | Today's directional lean — most relevant macro signal for intraday | Never gate — daily can be neutral while intraday has clear structure |
| Daily range (high/low of current + previous day) | Defines the intraday playground — Asia/London/NY ranges nest inside daily | Never replace H1 swing structure for stop placement |
| Daily range_position | Where in today's range is price? | Never override M15 range_position |
| Previous day close level | Key reference for continuation vs reversal | Information only |
| Daily phase (IMPULSE/RANGE/OPENING) | Is today trending or ranging? | Context — not a gate |

**Refresh frequency:** Once per H1 bar (daily updates with each H1 close)

---

## 3. Existing Data Audit

### MT5 Data Availability

| Timeframe | MT5 Constant | Candles Available? | Fetch Method |
|---|---|---|---|
| D1 | 16408 | YES — `copy_rates_from_pos(symbol, 16408, 0, N)` | Same as H4/H1/M15 |
| W1 | 32769 | YES — same API | Same |
| MN1 | 49153 | YES — same API | Same |

MT5 provides all three timeframes via the identical `copy_rates_from_pos` API already used by the TimeframeCache.

### Existing Analyzers That Could Be Reused

| Analyzer | Currently Used For | Applicable to MN/W1/D1? |
|---|---|---|
| `h4_regime.analyze_regime()` | H4 candles → RegimeSnapshot (trend, strength, phase) | **YES** — same algorithm works on any timeframe's candles |
| `h1_bias.analyze_bias()` | H1 candles → BiasSnapshot (direction, BOS, swings) | **YES** — swing/BOS detection is timeframe-agnostic |
| `m15_structure.analyze_structure()` | M15 candles → StructureSnapshot (quality, S/R) | **YES** — support/resistance detection works on any bars |

**Key insight:** The existing analyzers are NOT timeframe-specific — they operate on lists of `Candle` objects. The same `analyze_bias(daily_candles)` would produce a daily BiasSnapshot with trend direction, BOS, and swing levels.

---

## 4. Minimum Viable Schema

```python
@dataclass(frozen=True)
class MacroSnapshot:
    """MN/W1/D1 macro context — the story before H4."""

    # Monthly
    monthly_trend: str = ""              # BULLISH / BEARISH / NEUTRAL
    monthly_trend_strength: float = 0.0  # 0.0–1.0
    monthly_phase: str = ""              # IMPULSE / PULLBACK / CONSOLIDATION

    # Weekly
    weekly_trend: str = ""               # BULLISH / BEARISH / NEUTRAL
    weekly_trend_strength: float = 0.0
    weekly_swing_high: float = 0.0       # Key structural boundary
    weekly_swing_low: float = 0.0        # Key structural boundary
    weekly_range_position: float = 0.0   # 0.0–1.0

    # Daily
    daily_bias: str = ""                 # BULLISH / BEARISH / NEUTRAL
    daily_bias_strength: float = 0.0
    daily_high: float = 0.0              # Today's high
    daily_low: float = 0.0               # Today's low
    daily_prev_close: float = 0.0        # Yesterday's close
    daily_range_position: float = 0.0    # Where in today's range

    # Meta
    bar_time: int = 0                    # Timestamp of latest daily close
    stale: bool = False                  # True if data is old (weekend etc)
```

**Field count:** 16 — deliberately minimal. Each field has a clear purpose and producer.

---

## 5. Strategy Consumption Model

### Principle: Macro context INFORMS, never GATES

Macro context should change the CONFIDENCE or INTERPRETATION of existing evidence — not add new required conditions.

### TREND_CONTINUATION

| Macro Signal | Interpretation |
|---|---|
| Monthly + Weekly + Daily all aligned with H4 trend | HIGHEST confidence continuation — "all timeframes agree" |
| Daily opposing H4 | Lower confidence — intraday counter-trend pull may interrupt |
| Weekly at swing extreme in trend direction | Potential exhaustion — reduce confidence |
| Monthly pullback into weekly trend | Strong continuation context — "buying the dip at macro scale" |

**Implementation:** Add confidence modifier (±0.1–0.2) based on macro alignment. Never block selection.

### MEAN_REVERSION

| Macro Signal | Interpretation |
|---|---|
| Daily range_position at extreme | Supports reversion thesis — today's move is stretched |
| Weekly range_position at extreme | STRONGER reversion signal — multi-day overextension |
| Monthly trend STRONG in direction of extension | CAUTION — may be trending, not reverting |
| Weekly trend NEUTRAL | Ideal environment for reversion |

**Implementation:** If weekly/daily aligned with reversion thesis → boost confidence. If monthly strong against reversion → reduce confidence.

### RANGE_REACTION

| Macro Signal | Interpretation |
|---|---|
| Weekly range defined (swing_high + swing_low both present) | The range is ESTABLISHED at multi-week scale — high confidence |
| Daily within weekly range boundaries | Range is stable today — supports reaction thesis |
| Monthly trend STRONG | The "range" might be a pullback in a trend — reduce confidence |

**Implementation:** Weekly range presence → significant confidence boost. Monthly trend opposing → caution signal.

### LIQUIDITY_SWEEP_REVERSAL

| Macro Signal | Interpretation |
|---|---|
| Daily/Weekly swing levels = the liquidity targets | Macro swings are WHERE liquidity clusters |
| Price swept above weekly_swing_high then rejected | HIGHEST conviction sweep — multi-week stops taken |
| Monthly trend opposing the sweep direction | Supports reversal — macro favours the new direction |

**Implementation:** If sweep target was a weekly/daily level → highest confidence. Macro alignment boosts significantly.

### FALSE_BREAK

| Macro Signal | Interpretation |
|---|---|
| The broken level was a daily/weekly structure | FALSE BREAK of major level = many trapped participants = more fuel |
| Daily bias changed after the break failed | Confirms the false break at macro scale |
| Weekly trend opposes the breakout direction | Supports "the break was false" thesis |

**Implementation:** If the level was weekly-scale → boost confidence. If macro opposes break direction → boost confidence.

### BREAKOUT_EXPANSION

| Macro Signal | Interpretation |
|---|---|
| Monthly/Weekly consolidation → daily displacement | The breakout is SIGNIFICANT — multi-timeframe compression releasing |
| Daily phase = IMPULSE after weekly consolidation | Highest-conviction breakout context |
| Monthly trend ALIGNED with breakout direction | Breakout is WITH macro — higher probability of continuation |
| Monthly trend OPPOSING breakout direction | Counter-trend breakout — lower confidence, smaller targets |

**Implementation:** Macro compression → expansion alignment = massive confidence boost. Counter-macro breakout = reduced targets.

---

## 6. Risks

### Risk 1: Over-filtering (macro becomes a gate)

**Mitigation:** Macro MUST be implemented as confidence modifiers ONLY — never as required conditions. No strategy should fail to select because of macro context. The contract: `macro_context influences confidence, never blocks selection.`

### Risk 2: Duplicating H4

**Mitigation:** Clear responsibility separation:
- H4 = current session regime (what's happening NOW in the 4-hour)
- D1 = today's narrative (what happened today)
- W1 = this week's structure (multi-day boundaries)
- MN = this month's direction (macro bias)

H4 can be RANGING while Daily is TRENDING (today is consolidating within a trend day). These are DIFFERENT observations.

### Risk 3: Conflicting timeframe authority

**Mitigation:** Establish clear hierarchy for CONFLICTS:
- Entry authority: H1/M15/M5 (execution timeframes)
- Regime authority: H4 (session context)
- Narrative authority: D1/W1/MN (story context)

When narrative conflicts with regime, the STRATEGY CONFIDENCE is modified but the strategy SELECTION is not blocked. Example: TREND_CONTINUATION fires (H4+H1 agree), but monthly opposes → confidence drops from 0.80 to 0.65. Still selected, but with lower conviction.

### Risk 4: MN/W1/D1 becoming hard requirements

**Mitigation:** Architectural constraint — the `MacroSnapshot` is consumed ONLY by:
1. Strategy confidence modifiers (post-selection)
2. Decision reporting (observability)
3. Research dataset enrichment

It is NOT consumed by:
- Opportunity engine (not a location signal)
- Strategy required conditions (not a gate)
- Entry engine (not a price level for stop/target)
- Risk engine (not a sizing input)

---

## 7. Implementation Path (not for now)

| Phase | Work | Impact |
|---|---|---|
| 1 | Add `_TF_D1 = 16408` to cache, configure fetch, reuse `analyze_regime` on daily candles | Populates daily trend/strength |
| 2 | Add `_TF_W1 = 32769`, reuse `analyze_bias` on weekly candles → swing levels | Populates weekly structure |
| 3 | Add `_TF_MN = 49153`, reuse `analyze_regime` on monthly candles | Populates monthly trend |
| 4 | Build `MacroSnapshot` from the three snapshots, add to `HTFContext` | Available to pipeline |
| 5 | Add confidence modifiers to strategy engine (post-selection, not pre-) | Strategies use macro |
| 6 | Add macro fields to decision persistence | Research dataset enriched |

**Estimated effort:** Each phase is ~30 minutes. The analyzers already exist — it's primarily wiring.

---

## 8. What This Does NOT Do

- Does NOT add new required strategy conditions
- Does NOT create new opportunity filters
- Does NOT change entry/stop/target placement
- Does NOT modify risk sizing
- Does NOT block any trade the current system would take
- Does NOT require new algorithms — reuses existing H4/H1/M15 analyzers on higher-TF data

It only adds: "here's the larger context this trade exists within" — for confidence adjustment and research enrichment.
