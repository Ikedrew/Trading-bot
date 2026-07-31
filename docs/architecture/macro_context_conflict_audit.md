# Macro Context Conflict Handling Audit

---

## 1. Are the 7 MacroAlignment States Sufficient?

### Verdict: Mostly, but with one structural ambiguity.

The 7 states form a clean ordinal scale:

```
FA (+0.15) → SA (+0.10) → PA (+0.05) → N (0) → PO (-0.05) → SO (-0.10) → FO (-0.15)
```

This covers the **extremes** and the **midpoints** well. However:

### Gap: The "Conflicted" Case

The current NEUTRAL state conflates two different situations:

| Situation | Current State | Market Reality |
|---|---|---|
| All three layers NEUTRAL | NEUTRAL | "Macro has no opinion — genuine absence of signal" |
| MN BULLISH + W1 BEARISH + D1 NEUTRAL | NEUTRAL | "Macro is CONFLICTED — layers disagree with each other" |
| MN BULLISH + W1 NEUTRAL + D1 BEARISH | NEUTRAL | "Macro is CONFLICTED — edges cancel" |

These are qualitatively different:
- **All neutral** = no information → confidence stays at base → acceptable
- **Conflicted** = active disagreement between layers → uncertainty is HIGH → different from "no opinion"

### Recommendation: Split NEUTRAL into NEUTRAL and CONFLICTED

| State | Definition | Modifier |
|---|---|---|
| NEUTRAL | All three layers are NEUTRAL (no directional signal exists) | 0.00 |
| CONFLICTED | Layers disagree with each other (aligned + opposing present simultaneously) | -0.05 |

A conflicted macro deserves a small negative modifier because uncertainty itself is a form of headwind — the market story is unclear, which reduces conviction.

**Updated state count: 8**

---

## 2. How Should Missing/Low-Quality Data Affect Alignment?

### Scenarios

| Scenario | Cause | Frequency |
|---|---|---|
| MN1 snapshot is None | First startup, no monthly data fetched yet | Rare (once per cold start) |
| W1 snapshot stale (>3 weeks old) | Weekend + holiday gap | Occasional |
| D1 snapshot has 0 confidence | Insufficient daily bars for analysis | Session open on first day |
| MN/W1 trend_strength < 0.3 | Weak/inconclusive direction | Common (many months/weeks are sideways) |

### Design Rules for Missing Data

| Rule | Effect |
|---|---|
| If a layer's snapshot is None → treat as NEUTRAL | Missing = no opinion, not opposition |
| If a layer's trend_strength < 0.3 → treat as NEUTRAL | Weak conviction ≠ opposition |
| If a layer is stale (bar_time too old) → treat as NEUTRAL with flag | Stale data shouldn't influence confidence |
| If ALL layers are missing → alignment = NEUTRAL, modifier = 0.00 | Absence of data = absence of influence |

### Implementation

```python
def _classify_layer(trend: str, strength: float, trade_direction: str, available: bool) -> str:
    if not available or strength < 0.3:
        return "NEUTRAL"
    if trend == trade_direction:
        return "ALIGNED"
    if trend != "" and trend != "NEUTRAL" and trend != trade_direction:
        return "OPPOSING"
    return "NEUTRAL"
```

**Key principle:** Missing or weak data always degrades to NEUTRAL — it never creates false alignment or false opposition.

### Staleness Flag

The `MacroAlignment` output should include a `data_quality` field:

```python
data_quality: str = "COMPLETE"  # COMPLETE / PARTIAL / STALE / UNAVAILABLE
```

| Quality | Meaning | Confidence Cap |
|---|---|---|
| COMPLETE | All three layers have fresh, high-confidence data | No cap |
| PARTIAL | One or more layers are NEUTRAL due to low strength (not missing) | No cap (expected) |
| STALE | One or more layers have stale snapshots | Max modifier ±0.05 (reduced influence) |
| UNAVAILABLE | No macro data at all (cold start) | Modifier = 0.00 (no influence) |

---

## 3. Should MN1/W1/D1 Have Equal Weighting?

### Analysis

| Factor | MN1 | W1 | D1 |
|---|---|---|---|
| Relevance to intraday trade | LOW — monthly trend can be wrong for weeks | MEDIUM — weekly structure persists for days | HIGH — today's bias is the most proximal context |
| Signal stability | Very stable (changes once per month) | Stable (changes once per week) | Volatile (can change intraday) |
| False signal cost | Low — rarely wrong, but rarely helpful for timing | Medium | Higher — daily can whipsaw |
| Historical predictive value (general) | Weak for entry timing, strong for direction probability | Moderate for both | Strong for session context |

### Verdict: D1 should have slightly higher weight than MN1/W1

Current design: all three layers contribute equally to the aligned/opposing count. A FULL_ALIGNMENT where MN+W1+D1 agree gives +0.15.

**Alternative: Weighted modifier calculation**

Instead of counting layers, compute:

```
modifier = (monthly_contrib × 0.25) + (weekly_contrib × 0.35) + (daily_contrib × 0.40)
```

Where each layer contributes:
- ALIGNED: +1.0
- NEUTRAL: 0.0
- OPPOSING: -1.0

Then scale to ±0.15 range.

**Example:**
- MN ALIGNED, W1 OPPOSING, D1 ALIGNED:
  - Count method: 2 aligned, 1 opposing → NEUTRAL (cancels) → 0.00
  - Weighted method: (0.25 × 1.0) + (0.35 × -1.0) + (0.40 × 1.0) = +0.30 → scaled to +0.09

The weighted method produces a more nuanced result that reflects D1's higher relevance.

### Recommendation: Use Weighted Approach

| Layer | Weight | Reasoning |
|---|---|---|
| Monthly | 0.25 | Slowest to change, least relevant to session timing |
| Weekly | 0.35 | Multi-day structure — meaningful but not immediate |
| Daily | 0.40 | Most proximal context — today's actual character |

**Formula:**

```python
raw_score = (mn_value * 0.25) + (w1_value * 0.35) + (d1_value * 0.40)
# mn/w1/d1_value: +1.0 (aligned), 0.0 (neutral), -1.0 (opposing)

confidence_modifier = raw_score * 0.15  # Scale to ±0.15 range
confidence_modifier = max(-0.20, min(0.20, confidence_modifier))  # Hard cap
```

This replaces the discrete 7-state lookup table with a continuous calculation that naturally handles partial alignments and mixed signals.

The alignment_state label (for reporting/research) can be derived FROM the raw_score:

```
raw_score >= +0.70  → FULL_ALIGNMENT
raw_score >= +0.40  → STRONG_ALIGNMENT
raw_score >= +0.15  → PARTIAL_ALIGNMENT
raw_score > -0.15 and raw_score < +0.15 → NEUTRAL or CONFLICTED
raw_score <= -0.15  → PARTIAL_OPPOSITION
raw_score <= -0.40  → STRONG_OPPOSITION
raw_score <= -0.70  → FULL_OPPOSITION
```

---

## 4. Cases Where Same State Represents Different Conditions

### Problem Cases in the Current Design

| State | Case A | Case B | Qualitatively Different? |
|---|---|---|---|
| **NEUTRAL** | All layers genuinely neutral (no trend anywhere) | MN BULLISH + D1 BEARISH (cancels) | YES — A is "no information," B is "conflicting information" |
| **STRONG_ALIGNMENT** | MN BULLISH + W1 BULLISH + D1 neutral | MN neutral + W1 BULLISH + D1 BULLISH | YES — first has deeper macro support; second has more proximal support |
| **PARTIAL_OPPOSITION** | MN OPPOSING + W1 neutral + D1 neutral | MN neutral + W1 neutral + D1 OPPOSING | YES — monthly opposition is a slow headwind; daily opposition is immediate pressure |

### Resolution via Weighted Approach

The weighted model (section 3) naturally distinguishes these:

- MN BULLISH + D1 BEARISH: (0.25 × 1.0) + (0.35 × 0) + (0.40 × -1.0) = -0.15 → slight opposition
- All neutral: 0.00 → true neutral
- MN opposing: (0.25 × -1.0) + others 0 = -0.075 → minor headwind
- D1 opposing: (0.40 × -1.0) + others 0 = -0.12 → stronger headwind

The WEIGHTED calculation preserves the distinction automatically without needing additional states.

### Additional Disambiguation: Source Attribution

Add to `MacroAlignment`:

```python
primary_influence: str = ""  # "MONTHLY" / "WEEKLY" / "DAILY" / "MIXED"
# Which layer contributed most to the modifier (absolute contribution)
```

This allows research to distinguish "modified by monthly trend" vs "modified by daily bias" — enabling the question: "Which macro layer is most predictive of outcome?"

---

## 5. Proposed Improvements

### Improvement 1: Replace discrete states with weighted continuous score

**Current:** Count aligned/opposing → lookup table → fixed modifier
**Proposed:** Weighted calculation → continuous modifier → derive label for reporting

**Benefit:** More precise, naturally handles partial/conflicted scenarios, D1 has appropriate higher influence.

### Improvement 2: Add CONFLICTED state (split from NEUTRAL)

**Current:** Mixed signals = NEUTRAL (modifier 0.00)
**Proposed:** Mixed signals = CONFLICTED (modifier -0.05)

**Benefit:** Acknowledges that active disagreement between timeframes is a form of uncertainty that deserves recognition (versus genuine absence of signal).

### Improvement 3: Add data_quality field

**Current:** No visibility into whether alignment is based on real data or defaults
**Proposed:** `data_quality: COMPLETE / PARTIAL / STALE / UNAVAILABLE`

**Benefit:** Research can filter "decisions where macro was fully informed" vs "decisions where macro was guessing from defaults." Enables data quality studies.

### Improvement 4: Add primary_influence attribution

**Current:** Modifier is a single number with no explanation of which layer caused it
**Proposed:** `primary_influence: str` identifying the dominant contributing layer

**Benefit:** Research can answer "which timeframe's alignment is most predictive?" and calibrate weights empirically over time.

### Improvement 5: Strength-weighted layer contribution

**Current:** A layer with strength=0.31 contributes the same as strength=0.95
**Proposed:** Layer contribution scales with strength:

```python
layer_value = direction_sign * min(1.0, strength / 0.7)
# strength 0.3 → 0.43x contribution
# strength 0.5 → 0.71x contribution
# strength 0.7 → 1.0x contribution (full)
# strength 1.0 → 1.0x contribution (capped)
```

**Benefit:** A weak monthly trend (0.35 strength) contributes less than a strong monthly trend (0.80 strength) — proportional influence rather than binary.

---

## Summary of Proposed Final Design

```python
def compute_macro_alignment(
    macro: MacroSnapshot,
    trade_direction: str,
) -> MacroAlignment:
    """
    Pure function: macro data + direction → alignment assessment.
    Never gates. Only modifies confidence.
    """
    # 1. Classify each layer (with strength threshold 0.3)
    mn_class = _classify(macro.monthly_trend, macro.monthly_trend_strength, trade_direction)
    w1_class = _classify(macro.weekly_trend, macro.weekly_trend_strength, trade_direction)
    d1_class = _classify(macro.daily_bias, macro.daily_bias_strength, trade_direction)

    # 2. Compute weighted score (strength-adjusted)
    mn_value = _direction_value(mn_class) * _strength_scale(macro.monthly_trend_strength)
    w1_value = _direction_value(w1_class) * _strength_scale(macro.weekly_trend_strength)
    d1_value = _direction_value(d1_class) * _strength_scale(macro.daily_bias_strength)

    raw_score = (mn_value * 0.25) + (w1_value * 0.35) + (d1_value * 0.40)

    # 3. Detect CONFLICTED (aligned + opposing simultaneously)
    has_aligned = "ALIGNED" in (mn_class, w1_class, d1_class)
    has_opposing = "OPPOSING" in (mn_class, w1_class, d1_class)
    is_conflicted = has_aligned and has_opposing

    # 4. Apply conflict penalty
    if is_conflicted and abs(raw_score) < 0.15:
        raw_score -= 0.05 * (1 if raw_score >= 0 else -1)  # Push toward opposition

    # 5. Scale to modifier range and cap
    modifier = raw_score * 0.15
    modifier = max(-0.20, min(0.20, modifier))

    # 6. Derive label
    state = _derive_state(raw_score, is_conflicted)

    # 7. Determine primary influence
    contributions = {"MONTHLY": abs(mn_value * 0.25), "WEEKLY": abs(w1_value * 0.35), "DAILY": abs(d1_value * 0.40)}
    primary = max(contributions, key=contributions.get)

    return MacroAlignment(
        monthly_alignment=mn_class,
        weekly_alignment=w1_class,
        daily_alignment=d1_class,
        alignment_state=state,
        confidence_modifier=round(modifier, 3),
        primary_influence=primary,
        is_conflicted=is_conflicted,
        data_quality=_assess_quality(macro),
        narrative=_build_narrative(state, primary, modifier),
    )
```

---

## Preserved Constraints

| Constraint | Status |
|---|---|
| Macro never gates strategy selection | PRESERVED — modifier applied post-selection only |
| Macro only modifies confidence/risk | PRESERVED — single float output, capped ±0.20 |
| Strategy remains decision authority | PRESERVED — H4/H1/M15/M5 evidence determines selection |
| Confidence floor 0.40 | PRESERVED — trade remains valid even with max opposition |
| Missing data = no influence | PRESERVED — unavailable/weak layers degrade to NEUTRAL |
