# Macro Context Interpretation Design

---

## 1. Narrative Contribution Per Timeframe

Each timeframe answers one question and contributes one sentence to the market story.

| Timeframe | Narrative Role | Sentence Template |
|---|---|---|
| **MN1** | The season | "The monthly environment is {BULLISH/BEARISH/NEUTRAL} with {strength} conviction, currently in {phase}." |
| **W1** | The chapter | "This week is {BULLISH/BEARISH/NEUTRAL}, price is at {range_position} within weekly structure ({swing_low} to {swing_high})." |
| **D1** | The page | "Today is {BULLISH/BEARISH/NEUTRAL}, price is at {daily_range_position} of today's range, volatility is {normal/elevated/compressed}." |

### What Each Answers — Nothing More

| Timeframe | Answers | Does NOT Answer |
|---|---|---|
| MN1 | "Is there a dominant multi-month force?" | "Should I trade today?" |
| W1 | "What are the multi-day structural edges?" | "Is this H1 BOS valid?" |
| D1 | "What kind of day is today?" | "Which M5 bar to enter on?" |

---

## 2. Conflict Representation

### Alignment States Between Layers

When macro timeframes disagree with each other or with the trade direction, the system classifies the conflict explicitly rather than hiding it.

```python
@dataclass(frozen=True)
class MacroAlignment:
    """How macro context relates to the proposed trade direction."""

    # Per-layer alignment with trade direction
    monthly_alignment: str = ""     # ALIGNED / OPPOSING / NEUTRAL
    weekly_alignment: str = ""      # ALIGNED / OPPOSING / NEUTRAL
    daily_alignment: str = ""       # ALIGNED / OPPOSING / NEUTRAL

    # Composite
    alignment_state: str = ""       # See section 3 below
    confidence_modifier: float = 0.0  # -0.20 to +0.20
    narrative: str = ""             # Human-readable one-liner
```

### How Alignment Is Determined

For a given trade direction (e.g., BUY):

| Macro Layer | ALIGNED if | OPPOSING if | NEUTRAL if |
|---|---|---|---|
| Monthly | `monthly_trend == "BULLISH"` and strength > 0.3 | `monthly_trend == "BEARISH"` and strength > 0.3 | Trend is NEUTRAL or strength < 0.3 |
| Weekly | `weekly_trend == "BULLISH"` and strength > 0.3 | `weekly_trend == "BEARISH"` and strength > 0.3 | Same |
| Daily | `daily_bias == "BULLISH"` and strength > 0.3 | `daily_bias == "BEARISH"` and strength > 0.3 | Same |

For SELL trades, the logic inverts (BEARISH = aligned, BULLISH = opposing).

---

## 3. Possible Macro Alignment States

Seven distinct states, from strongest alignment to strongest opposition:

| State | Code | Definition | Example |
|---|---|---|---|
| **FULL_ALIGNMENT** | `FA` | All three layers aligned with trade direction | BUY trade, MN BULLISH, W1 BULLISH, D1 BULLISH |
| **STRONG_ALIGNMENT** | `SA` | Two aligned, one neutral | BUY, MN BULLISH, W1 BULLISH, D1 NEUTRAL |
| **PARTIAL_ALIGNMENT** | `PA` | One aligned, others neutral | BUY, MN NEUTRAL, W1 BULLISH, D1 NEUTRAL |
| **NEUTRAL** | `N` | All neutral, or mix of aligned + opposing that cancels | All NEUTRAL, or MN BULLISH + D1 BEARISH + W1 NEUTRAL |
| **PARTIAL_OPPOSITION** | `PO` | One opposing, others neutral | BUY, MN NEUTRAL, W1 BEARISH, D1 NEUTRAL |
| **STRONG_OPPOSITION** | `SO` | Two opposing, one neutral or aligned | BUY, MN BEARISH, W1 BEARISH, D1 NEUTRAL |
| **FULL_OPPOSITION** | `FO` | All three layers opposing trade direction | BUY trade, MN BEARISH, W1 BEARISH, D1 BEARISH |

### State Determination Logic

```
aligned_count = count(ALIGNED in [monthly, weekly, daily])
opposing_count = count(OPPOSING in [monthly, weekly, daily])

if aligned_count == 3: FULL_ALIGNMENT
elif aligned_count == 2 and opposing_count == 0: STRONG_ALIGNMENT
elif aligned_count >= 1 and opposing_count == 0: PARTIAL_ALIGNMENT
elif opposing_count == 3: FULL_OPPOSITION
elif opposing_count == 2 and aligned_count == 0: STRONG_OPPOSITION
elif opposing_count >= 1 and aligned_count == 0: PARTIAL_OPPOSITION
else: NEUTRAL (mixed signals cancel)
```

---

## 4. How Macro Alignment Affects Confidence Only

### Modifier Table

| Alignment State | Confidence Modifier | Reasoning |
|---|---|---|
| FULL_ALIGNMENT | **+0.15** | Maximum tailwind — all macro layers support direction |
| STRONG_ALIGNMENT | +0.10 | Strong tailwind — two layers support |
| PARTIAL_ALIGNMENT | +0.05 | Mild tailwind — one layer supports |
| NEUTRAL | 0.00 | No macro signal — base confidence unchanged |
| PARTIAL_OPPOSITION | -0.05 | Mild headwind — one layer opposes |
| STRONG_OPPOSITION | -0.10 | Headwind — two layers oppose |
| FULL_OPPOSITION | **-0.15** | Maximum headwind — all layers oppose |

### Application Rules

1. **Applied AFTER strategy selection, NEVER before.** Strategy is selected based on H4/H1/M15/M5 evidence alone. Macro modifier adjusts the final confidence.

2. **Cumulative cap: ±0.20.** Even with additional factors (e.g., daily ATR elevated), total macro influence never exceeds ±0.20.

3. **Floor: 0.40.** No trade's confidence drops below 0.40 due to macro opposition. If base confidence is 0.50 and macro gives -0.15, result is 0.40 (not 0.35). Below 0.40, the trade would be meaningless.

4. **Ceiling: 1.00.** Confidence cannot exceed 1.00.

### Additional Modifiers (within ±0.20 total cap)

| Condition | Modifier | Reasoning |
|---|---|---|
| Daily ATR ratio > 2.0 (unusually volatile day) | -0.05 | Wide stops, unpredictable movement |
| Weekly range_position extreme AND reversion strategy | +0.05 | Multi-day overextension supports reversion |
| Weekly range_position extreme AND continuation strategy | -0.05 | May be at weekly exhaustion |

---

## 5. How Macro Information Appears in DecisionTrace

### Terminal Report Addition

After `[FINAL ACTION]`, a new section:

```
[V10 MACRO CONTEXT]
  Monthly: BULLISH (strength=0.65, phase=IMPULSE)
  Weekly:  BEARISH (strength=0.42, swing: 1.0850–1.0920, pos=0.78)
  Daily:   NEUTRAL (strength=0.15, range: 1.0870–1.0905, pos=0.62)
  Alignment: PARTIAL_OPPOSITION (monthly aligned, weekly opposing, daily neutral)
  Confidence modifier: -0.05
  Narrative: "Monthly bullish but weekly structure opposing — mixed macro"
```

### Decision Record (persistence)

```json
{
  "macro_context": {
    "monthly_trend": "BULLISH",
    "monthly_strength": 0.65,
    "monthly_phase": "IMPULSE",
    "weekly_trend": "BEARISH",
    "weekly_strength": 0.42,
    "weekly_swing_high": 1.0920,
    "weekly_swing_low": 1.0850,
    "weekly_range_position": 0.78,
    "daily_bias": "NEUTRAL",
    "daily_strength": 0.15,
    "daily_range_position": 0.62,
    "daily_atr_ratio": 1.1
  },
  "macro_alignment": {
    "trade_direction": "BUY",
    "monthly_alignment": "ALIGNED",
    "weekly_alignment": "OPPOSING",
    "daily_alignment": "NEUTRAL",
    "alignment_state": "NEUTRAL",
    "confidence_modifier": -0.05,
    "narrative": "Monthly bullish but weekly structure opposing"
  }
}
```

### Event Stream

New event type (not a pipeline stage — emitted alongside V10_DECISION_COMPLETE):

```json
{
  "event_type": "V10_MACRO_CONTEXT",
  "observation_id": "...",
  "symbol": "EURUSD",
  "timestamp_utc": 1785400000.0,
  "engine_version": "V10",
  "payload": {
    "alignment_state": "NEUTRAL",
    "confidence_modifier": -0.05,
    "monthly_trend": "BULLISH",
    "weekly_trend": "BEARISH",
    "daily_bias": "NEUTRAL"
  }
}
```

### Research Query Enablement

The persistence format enables:

```sql
SELECT
    macro_alignment.alignment_state,
    AVG(outcome.r_multiple) as avg_r,
    COUNT(*) as n
FROM v10_decisions d
JOIN v10_outcomes o ON d.observation_id = o.observation_id
GROUP BY macro_alignment.alignment_state
ORDER BY avg_r DESC
```

This answers: "Do FULL_ALIGNMENT trades outperform NEUTRAL or OPPOSING trades?"

---

## Design Summary

```
MacroSnapshot (data)
    ↓
MacroAlignment (interpretation, relative to trade direction)
    ↓
confidence_modifier (single float, ±0.20 max)
    ↓
Applied to strategy_confidence AFTER selection
    ↓
Persisted in decision record + reported in terminal
```

The interpretation layer is a pure function:
- Input: `MacroSnapshot` + `trade_direction`
- Output: `MacroAlignment` (alignment_state + confidence_modifier + narrative)
- Side effects: none
- Can be tested in isolation
- Never touches strategy selection, opportunity, entry, or risk logic
