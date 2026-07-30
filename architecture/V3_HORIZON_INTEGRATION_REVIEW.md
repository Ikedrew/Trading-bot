# V3 Risk Geometry + Horizon Integration Review

**Date:** 2026-07-28
**Decision:** Horizon should become a core V3 Risk Model input

---

## 1. What Is Horizon's Intended Role?

### Current Horizon Implementation

The Horizon framework is substantial — a complete multi-module system:

| Module | Purpose |
|---|---|
| `horizon_classifier.py` | Determines SCALP/INTRADAY/EXTENDED eligibility from context |
| `horizon_profiles.py` | Defines expected behaviour per horizon (hold time, SL source, RR) |
| `horizon_trade_builder.py` | Constructs SL/TP per horizon (M5→2R, M15→3R, H1→4R) |
| `horizon_execution_profile.py` | Execution parameters (trailing, break-even, time exits) |
| `horizon_manager.py` | Singleton profile resolver |
| `execution_authority.py` | Portfolio allocation guard |
| `research_contract.py` | Expected vs observed behaviour validation |
| `shadow_evaluation.py` | Shadow trade analysis per horizon |
| Experiments A-D | Single-variable isolation (duration, stop, target, exit policy) |

### Current Status

- SCALP is the ONLY permitted horizon for execution
- INTRADAY and EXTENDED are shadow-only
- Shadow trades already create horizon variants (hshadow_ prefix)
- SV1 proved: M15 structure stop (+0.47R improvement, p<0.001) but variant still -0.34R

### Recommended Role

**Option C: Risk Model Input.**

Reasoning:

| Option | Verdict | Why |
|---|---|---|
| A) Research-only tool | TOO LIMITED | Horizon already has execution profiles and SL/TP logic |
| B) Context feature | INSUFFICIENT | Horizon defines trade lifecycle, not just a label |
| **C) Risk model input** | **CORRECT** | Horizon determines stop distance → which determines spread/risk → which determines viability |
| D) Complete lifecycle manager | PREMATURE | Need to prove concept at one horizon first |

**Horizon as Risk Model Input means:** Given V3 location context (inside OB, discount zone), Horizon determines the appropriate stop distance, target distance, and expected hold time. This is exactly the "adaptive risk geometry" the architecture needs.

---

## 2. Can Horizon Solve the Current Risk Geometry Problem?

### The Problem

| Metric | Current (SCALP) | Cause |
|---|---|---|
| Stop distance | 2.2 pips (M5 candle) | M5 geometry is tiny |
| Spread/risk | 48% | Spread overwhelms small stops |
| TP hit rate | 0% | Target unreachable at 2:1 RR |
| Timeout rate | 95.6% | Price doesn't reach target in 60 bars |
| Cost-adjusted EV | -0.81R | Spread destroys any raw signal |

### What SV1 Already Proved

SV1 tested EXACTLY this question with n=323 paired trades:

| Metric | SCALP (M5 SL) | INTRADAY (M15 SL) | Improvement |
|---|---|---|---|
| Risk distance | 2.56 pips | 5.64 pips | 2.2× wider |
| **Spread/risk** | **78.8%** | **30.5%** | **-48.3pp** |
| Cost-adjusted EV | -0.81R | **-0.34R** | **+0.47R** |
| Walk-forward validated | — | ✅ CI > 0 | YES |

**The M15 structure stop reduces cost burden by 2.6× and improves EV by +0.47R (p<0.001, walk-forward confirmed).**

### Can Horizon Close the Remaining Gap?

Current gap with M15 geometry: -0.34R (still negative).
V3 inside-OB signal: +0.071R.
Combined: -0.34R + 0.07R = -0.27R (still negative).

But if INTRADAY horizon is used with location-filtered entries:
- Spread/risk at M15 structure: 30.5%
- Required raw EV to break even: +0.31R
- Current inside-OB signal: +0.07R (need +0.24R more)

The gap is smaller but still exists. However:
- SV1 tested ALL entries (no location filter)
- Filtering to inside-OB only may improve the INTRADAY variant's EV further
- The 95.6% timeout may reduce with location-aware exits

**Verdict: Horizon solves HALF the problem (cost reduction). V3 location solves the other half (signal improvement). Together they may reach viability.**

---

## 3. Proposed Risk Model Integration

### Correct Architecture

```
V3 Location Assessment
    │
    │ "Price is inside demand OB at M15 discount zone"
    │
    ▼
Horizon Selection
    │
    │ Context → determines INTRADAY eligible
    │ (M15 structure provides stop, not M5 candle)
    │
    ▼
Adaptive Risk Calculation
    │
    │ Inputs:
    │   - Location: inside OB → high confidence
    │   - Horizon: INTRADAY → M15 structure stop
    │   - ATR: volatility context
    │   - Spread: execution cost
    │   - OB zone boundary: natural stop placement
    │
    │ Output:
    │   stop_distance: beyond OB zone low (structure-based)
    │   target_distance: next liquidity zone or opposing OB
    │   expected_duration: INTRADAY profile (60-480 min)
    │   exit_model: time-based + trailing (INTRADAY profile)
    │   risk_percent: based on confidence + distance
    │
    ▼
Execution Decision
```

### Why This Is Correct

1. **Stop placement from structure, not candle geometry:** OB zone boundary provides a natural stop. If price breaks below the OB zone, the thesis is invalidated. This is a LOGICAL stop, not an arbitrary distance.

2. **Target from opposing liquidity:** The next equal-highs pool or supply OB provides a natural target. This aligns with how institutional order flow moves between liquidity zones.

3. **Duration from horizon profile:** INTRADAY expects 60-480 minutes. This matches the time it takes for price to move between M15 structure levels — much more realistic than 60 M5 bars (5 hours) for a tiny move.

4. **Exit management from profile:** INTRADAY has trailing stop at 2R, break-even at 1.5R, partial TP at 70% of target. These are already defined in config.

---

## 4. Recommended Experiments

### Experiment RG1: Location-Filtered INTRADAY Geometry

**Question:** "Does Inside-OB + INTRADAY stop produce positive cost-adjusted EV?"

**Design:**
- Filter: Only trades where V3 records show `price_inside_ob == True`
- Stop: M15 structure (beyond OB zone boundary)
- Target: 3:1 RR from structure stop
- Duration: 120 bars (INTRADAY profile)
- Compare against: Same trades with SCALP geometry

**Data source:** Existing shadow trade `trade_state_progression` (bar-by-bar R values are stored) — can re-simulate with different SL/TP.

### Experiment RG2: Horizon Duration vs Location

**Question:** "Do trades at institutional zones need more or less time than standard?"

**Design:**
- Group A: Inside-OB trades, standard 60 bars
- Group B: Inside-OB trades, 120 bars (INTRADAY)
- Group C: Inside-OB trades, 180 bars (EXTENDED)
- Compare MFE reached at each duration

### Experiment RG3: Structure-Based Targets

**Question:** "Does targeting the opposing liquidity zone improve over fixed RR?"

**Design:**
- Control: Fixed 3:1 RR target
- Variant: Target = nearest opposing OB/liquidity pool
- Measure: TP hit rate, final R, timeout rate

---

## 5. Relationship Between Location and Horizon

### The Synthesis

```
V3 Location: WHERE (institutional zone, discount, OB boundary)
         ↕
Horizon: HOW FAR (stop distance) and HOW LONG (expected duration)
         ↕
Combined: Trade lifecycle parameters derived from WHERE price is
```

### Example

```
V3 detects:
    - Price inside demand OB at 1.0840-1.0850
    - M15 discount zone (range_position = 0.25)
    - Next supply OB at 1.0880-1.0890
    - Equal highs at 1.0885 (liquidity target)

Horizon determines:
    - SL: below OB low (1.0840) - buffer = 1.0837
    - From entry 1.0845: risk = 8 pips
    - Spread/risk: 1.0 pip / 8.0 pips = 12.5% (viable!)
    - Target: opposing zone at 1.0880 = +35 pips = 4.4:1 RR
    - Duration: INTRADAY (expect resolution in 2-8 hours)
    - Exit: trail from 2R, break-even at 1.5R
```

**At 12.5% spread/risk, even the current raw EV (+0.07R) produces positive cost-adjusted outcome.** The key insight is: structure-based stops at institutional zones create LARGER risk distances that reduce the spread burden below the signal magnitude.

---

## 6. Development Recommendation

### Should Horizon be promoted into V3 Risk Model?

**YES.** It already contains the required components:
- Stop distance calculation per horizon (M5 → M15 → H1)
- Duration expectations (45min → 480min → 4320min)
- Exit management profiles (trailing, break-even, partial TP)
- Research validation framework

### Should RG1 include Horizon from the beginning?

**YES.** RG1 is specifically about testing whether INTRADAY geometry solves the cost problem when combined with V3 location. It cannot be answered without Horizon.

### Should risk geometry research happen before or after Horizon integration?

**SIMULTANEOUSLY.** The research IS the integration test:
1. Take existing inside-OB shadow trades
2. Re-simulate with INTRADAY geometry (from horizon_trade_builder.py)
3. Compare cost-adjusted EV

This requires NO production code changes — it uses existing shadow trade `trade_state_progression` data.

### Components Needing Modification

| Component | Change | Reason |
|---|---|---|
| None (for RG1) | — | Research uses existing data + horizon_trade_builder |
| `shadow_evaluation.py` | Add V3 filter | Filter horizon results by inside-OB |
| `horizon_controlled.py` | Add RG1 experiment | New experiment: location-filtered geometry |

---

## Final Output

### 1. Recommended Role of Horizon

**Risk Model Input (Option C):** Horizon determines adaptive stop/target/duration based on V3 location context. It translates "where price is" into "how to manage the trade."

### 2. Updated V3 Architecture (with Horizon)

```
Market Data
    ↓
Market Context Engine (existing)
    ↓
V3 Location Assessment (OB, FVG, liquidity, premium/discount)
    ↓
Opportunity Gate (inside institutional zone? discount zone?)
    ↓
Horizon Assessment (INTRADAY eligible? structure stop available?)
    ↓
Adaptive Risk Model
    │  - Stop: beyond OB zone boundary (structure-based)
    │  - Target: opposing liquidity zone
    │  - Duration: Horizon profile (INTRADAY: 60-480 min)
    │  - Exit: Horizon management (trail, break-even)
    │  - Spread/risk: must be < 20% (viable threshold)
    ↓
Entry Confirmation (pattern at zone = timing trigger)
    ↓
Execution Policy
    ↓
Outcome Learning
```

### 3. Risk Model Design

```
Input:
    location_confidence:  0-1 (from V3: inside_ob, discount, fvg)
    stop_distance_pips:   from horizon trade builder (M15/H1 structure)
    target_distance_pips: from opposing V3 zone (next OB/liquidity)
    spread_pips:          current market spread
    atr:                  volatility normalization
    horizon:              SCALP | INTRADAY | EXTENDED

Output:
    viable:               spread/risk < 20%?
    expected_rr:          target / risk
    expected_duration:    from horizon profile
    position_size:        from risk percentage + distance
    exit_profile:         from HorizonExecutionProfile
```

### 4. Experiments Required

| ID | Question | Priority |
|---|---|---|
| RG1 | Inside-OB + INTRADAY geometry → positive cost-adj EV? | **IMMEDIATE** |
| RG2 | Optimal duration for location-filtered trades | After RG1 |
| RG3 | Structure-based targets vs fixed RR | After RG1 |
| RG4 | Combined OB+FVG+discount with INTRADAY | After RG1 confirms direction |

### 5. Implementation Priority

```
Phase 1: RG1 Research (NO code changes)
    - Use existing trade_state_progression
    - Re-simulate inside-OB trades with M15 stop
    - Determine if cost-adjusted EV > 0

Phase 2: Shadow Integration (minimal code)
    - Add location filter to shadow_evaluation.py
    - Generate INTRADAY shadows only at V3 zones
    - Collect dedicated location+horizon data

Phase 3: V3 Risk Model (new module)
    - Combine V3 location + Horizon stop + Exit profile
    - Produce adaptive risk decisions
    - Shadow-test the combined system

Phase 4: Execution (only after Phase 3 proves profitable)
    - Add INTRADAY to PERMITTED_HORIZONS
    - Enable V3 location gate
    - Live validation
```

---

## Conclusion

Horizon is the missing layer between V3's location intelligence and executable positive expectancy. The framework already exists — it just needs to be connected to V3 location decisions rather than V2 pattern scores.

**The critical experiment (RG1) can be run NOW on existing data with ZERO code changes.** It uses the shadow trade progression data + horizon_trade_builder's M15 geometry. If inside-OB trades at INTRADAY risk distances produce cost-adjusted EV > 0, the V3 system has a path to profitability.
