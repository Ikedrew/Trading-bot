# V3 Architecture Alignment Review

**Date:** 2026-07-28
**Purpose:** Evaluate whether proposed V3 architecture is justified by research evidence

---

## 1. Architecture vs Research Findings

### Market Understanding Engine (HTF Structure Authority)

**Proposed role:** H4/H1 trend, BOS, CHOCH, market phase as authoritative context.

**Research evidence:**

| Finding | Source | Result |
|---|---|---|
| H4 regime predicts outcome | V2 CQ1 (n=437) | **REJECTED** — 0 significance |
| H1 bias improves outcomes | V2 CQ1 (n=437) | **REJECTED** — counter > aligned |
| H1 BOS improves outcomes | V2 CQ2 (n=437) | **REJECTED** — 0/8 combos validate |
| HTF alignment creates edge | V2 CQ2 (n=437) | **REJECTED** — no combination works |
| H1 range position separates EV | V3 Pass 2 (n=105) | YELLOW — discount +0.08R vs premium -0.05R (n=6 discount) |

**Verdict: PARTIALLY SUPPORTED — Structure useful for LOCATION measurement but NOT for directional authority.**

The research conclusively rejects HTF structure as a directional filter (V2 proved this). But H1/M15 swing levels ARE useful as location boundaries — they define WHERE price sits, not WHERE it's going. The architecture should retain HTF structure for location measurement but NOT as a directional trading gate.

---

### Market Location Engine (OB, FVG, Liquidity, Premium/Discount)

**Proposed role:** Institutional zones and location classification as primary context.

**Research evidence:**

| Finding | Source | Result |
|---|---|---|
| Price inside Order Block | V3 Pass 2 (n=23) | **GREEN** — +0.071R, CI > 0, WR=65.2% |
| M15 discount zone | V3 Pass 2 (n=59) | YELLOW — WR=62.7% (best zone) |
| OB presence vs absence | V3 Pass 2 (n=121 vs 37) | YELLOW — +0.10R to +0.14R effect |
| Equal lows presence | V3 Pass 2 (n=148 vs 10) | YELLOW — +0.18R effect |
| Price inside FVG | V3 Pass 2 (n=34) | YELLOW — WR=64.7%, EV=-0.014R |
| FVG below price | V3 Pass 2 (n=129) | YELLOW — -0.02R (least negative) |

**Verdict: STRONGEST SUPPORTED COMPONENT.**

Market Location is the ONLY area of the entire research program that has produced a statistically positive signal. The inside-OB finding (+0.071R) is the first positive EV measurement across all V1/V2/V3 research. The location gradient (discount > mid > premium) is consistent and theoretically sound. This should be the FOUNDATION of V3.

---

### Market Behaviour Engine (Trend/Range/Transition/Volatility)

**Proposed role:** Classify current market behaviour to adapt strategy.

**Research evidence:**

| Finding | Source | Result |
|---|---|---|
| Regime classification separates outcomes | V2 CQ1 (n=437) | **REJECTED** — 0 significance |
| Volatility state improves outcomes | V2 CQ3 (n=437) | **REJECTED** — 0 environments significant |
| Session timing creates edge | V2 CQ3 (n=437) | **REJECTED** — 0 environments significant |
| ATR-based volatility helps | V3 features | Neutral — no measured separation |

**Verdict: UNPROVEN — current evidence does NOT support this as a useful component.**

The market has been 92% RANGE during the entire observation period. There is insufficient trending data to validate whether behaviour classification helps. The V2 research with n=437 thoroughly tested regime/session/volatility and found zero predictive value. This component should be deprioritised until regime diversity increases.

---

### Opportunity Assessment Engine (Context Qualification Before Entry)

**Proposed role:** Determine if conditions represent a high-quality environment before looking for entries.

**Research evidence:**

The V3 findings strongly support this architectural change:

| V2 approach | V3 evidence |
|---|---|
| Pattern → Trade (anywhere) | -0.70R after costs |
| Pattern at institutional zone (inside OB) | +0.071R raw |
| Pattern in discount zone | WR=62.7% vs 38.9% in premium |

The data shows that WHERE you trade matters more than WHAT pattern you see. Context qualification BEFORE entry detection is justified.

**Verdict: SUPPORTED by evidence.** The research proves that unfiltered pattern trading is negative-EV, while location-filtered opportunities show positive raw signal. An opportunity assessment layer that requires institutional zone proximity is architecturally correct.

---

### Entry Model (Pattern Role)

**Proposed role:** Patterns as confirmation AFTER context validates, not as standalone signals.

**Research evidence:**

| Hypothesis | Evidence | Verdict |
|---|---|---|
| Pattern = directional signal | V2 CE1: -0.70R (n=867) | **REJECTED** |
| Pattern quality predicts outcome | V2 CQ4: zero importance | **REJECTED** |
| Pattern type matters | V2 CQ1: all negative | **REJECTED** |
| Pattern at OB zone = confirmation | V3: inside OB +0.071R | **SUPPORTED** |

**Verdict: SUPPORTED — Pattern should be confirmation, NOT signal.**

The evidence clearly shows:
- **Option A (Pattern = trade signal):** REJECTED. Proven negative EV.
- **Option B (Pattern = confirmation inside validated context):** SUPPORTED. The only positive finding occurs when a pattern triggers INSIDE an institutional zone.

The architecture correctly relegates patterns to timing/confirmation role.

---

### Risk Model (Stop/Target/Sizing)

**Proposed role:** Determine acceptable expectancy through stop placement, targets, RR, sizing.

**Research evidence:**

| Finding | Source | Result |
|---|---|---|
| Spread = 48% of risk | CE1 research | **CRITICAL PROBLEM** |
| 95.6% timeout rate | V3 Pass 2 | **RR STRUCTURE BROKEN** |
| 0% TP hit rate | V3 Pass 2 | Targets unreachable |
| SV1 structure stop +0.47R | SV1 experiment | Improvement but still negative |
| Tighter SL is spread artefact | Stop distance research | REJECTED |

**Verdict: REQUIRES FUNDAMENTAL RESEARCH.**

The current risk model is demonstrably broken:
- Targets are never reached (0% TP)
- Trades expire via timeout (95.6%)
- Spread consumes half the risk budget

The architecture correctly includes a risk model layer, but the current IMPLEMENTATION needs complete rethinking. Research needed: wider stops (reducing spread/risk), adaptive targets based on location, time-based exits.

---

### Execution Policy (Spread/Slippage/Management)

**Proposed role:** Convert validated opportunities into controlled trades.

**Research evidence:**

- Execution mechanics work correctly (shadow trades fire, lifecycle tracked)
- Spread filtering exists but spread IS the fundamental problem
- No live execution ever tested
- Trade management (trailing, partials) never validated

**Verdict: PREMATURE — no edge exists to execute yet.**

The execution layer is architecturally correct but has no content to execute. Until the combination of Context + Location + Risk produces positive cost-adjusted EV, execution optimisation is premature.

---

### Research Intelligence Engine (Continuous Learning)

**Proposed role:** Separate system that evaluates what works, what fails, and what needs changing.

**Research evidence:**

The research engine has been the most valuable part of the entire project:
- V2 Discovery proved null hypothesis (no M5 pattern edge)
- V3 Discovery found first positive signal (inside OB)
- Outcome linkage works (92.4% match rate)
- Statistical validation framework prevents false discoveries
- Research/production separation prevents overfitting

**Verdict: STRONGLY SUPPORTED.**

The separation between live decision system and research system is architecturally essential and proven valuable. The research engine correctly:
- Prevented false-positive promotions (R3/R4/R5 invalidated)
- Identified composition artefacts (Pass 1 → Pass 2 correction)
- Maintained epoch isolation
- Required minimum sample sizes

---

## 2. Strongest Supported Components (by evidence)

| Rank | Component | Evidence Level | Key Finding |
|---|---|---|---|
| 1 | **Market Location Engine** | GREEN | Inside OB +0.071R (only positive EV found) |
| 2 | **Research Intelligence Engine** | GREEN | Prevented false discoveries, found real signal |
| 3 | **Opportunity Assessment** | SUPPORTED | Location-filtered >> unfiltered trading |
| 4 | **Entry Model (pattern as confirmation)** | SUPPORTED | Pattern alone REJECTED, pattern at zone POSITIVE |

---

## 3. Components Still Hypotheses (require more research)

| Component | Status | What's Missing |
|---|---|---|
| Market Behaviour Engine | UNPROVEN | No trending data; regime shows zero separation |
| Risk Model parameters | NEEDS RESEARCH | Current RR broken (95% timeout); wider stops untested |
| Execution Policy | PREMATURE | No edge to execute; trade management untested |
| H4 structure authority | REJECTED as filter | Only useful for location boundaries |
| FVG as standalone signal | INSUFFICIENT | n=34 inside FVG, approaching but not significant |
| Liquidity sweep timing | INSUFFICIENT | n=11, need 50+ |

---

## 4. Recommended V3 Development Order

Based on evidence strength:

### Phase 1: Location Foundation (EVIDENCE EXISTS)

1. **Order Block proximity as context gate** — only positive finding (+0.071R)
2. **Premium/discount as directional filter** — discount 62.7% WR, premium 38.9%
3. **Validate at n=50** — need 27 more inside-OB events

### Phase 2: Risk Geometry Research (CRITICAL GAP)

4. **Wider stop distance research** — can 2x stop reduce spread/risk to 24%?
5. **Adaptive TP targets** — current targets never reached (0% TP rate)
6. **Time-based exit optimisation** — 95.6% timeout is not a strategy

### Phase 3: Combination Validation (DEPENDENT ON 1+2)

7. **OB + FVG + discount stacking** — does combination reach breakeven?
8. **Entry confirmation type** — which pattern at which zone performs best?
9. **Session validation** — does LONDON/NY change the picture?

### Phase 4: Behaviour Integration (AWAITING DATA)

10. **Regime diversity** — cannot validate until market provides trending data
11. **Volatility adaptation** — context for sizing, not filtering
12. **Transition detection** — when to pause trading

### Phase 5: Execution (ONLY AFTER POSITIVE COST-ADJUSTED EV)

13. **Entry method optimisation** — limit orders vs market
14. **Spread threshold calibration** — based on validated edge magnitude
15. **Live validation** — paper then real

---

## 5. Final Question

> "Does this architecture represent the correct evolution from V2 into V3 based on current evidence?"

### Answer: YES, with modifications.

**The architecture is directionally correct.** The research validates the core V3 thesis:

- **V2 proved:** Patterns alone have no edge. Context alone has no edge. The information set lacked granularity.
- **V3 discovered:** Precise market location (institutional zones) DOES separate outcomes. The first positive signal in all research comes from the Location Engine.

**The architecture correctly:**
- Places Location/Context BEFORE entry (evidence: unfiltered trading = -0.70R, filtered = +0.07R)
- Separates research from production (evidence: prevented false promotions)
- Demotes patterns to confirmation role (evidence: pattern type/quality = zero importance)

**The architecture needs modification on:**

1. **Market Behaviour Engine** should be DEPRIORITISED — regime/session/volatility have been thoroughly tested and rejected as predictive filters. Keep as observation metadata, not as a decision gate.

2. **Risk Model** should be ELEVATED to equal priority with Location — the 95.6% timeout rate and 0% TP rate mean the RR structure is broken regardless of context quality. Even with perfect location, wrong risk geometry kills the trade.

3. **The critical path is:** Location (proven signal) + Risk Geometry (broken, needs research) = potential breakeven or positive EV.

### Architectural Truth

The proposed architecture describes the END STATE correctly. But the CURRENT STATE means:

```
What's validated:     Location Engine → Opportunity Assessment
What's broken:        Risk Model (targets never reached)
What's unproven:      Behaviour Engine (no data)
What's premature:     Execution Policy (no edge to execute)
What's essential:     Research Engine (the only thing creating value)
```

The correct next step is NOT to build the full architecture. It's to solve the **Location + Risk geometry** research question — because that's where the evidence points and where the gap exists.
