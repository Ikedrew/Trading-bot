# V2 Discovery Engine

## Purpose

The V2 Discovery Engine analyses linked V2Opportunity records to determine whether the captured market context contains predictive information about trade outcomes.

It answers four research questions and produces a consolidated verdict:
- **A)** "Predictive information discovered" — further validation warranted
- **B)** "No available feature predicts outcome" — architecture lacks exploitable edge

This is research infrastructure only. It never modifies trading behaviour.

---

## Architecture

```
V2Opportunity Records (linked with outcomes)
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│               V2 Discovery Engine                       │
│                                                         │
│  ┌──────────────────┐  ┌──────────────────────────┐    │
│  │  CQ1: Feature    │  │  CQ2: Context            │    │
│  │  Analyser        │  │  Combiner                │    │
│  │                  │  │                          │    │
│  │  Individual      │  │  Hypothesis-driven       │    │
│  │  feature → EV    │  │  combination testing     │    │
│  └──────────────────┘  └──────────────────────────┘    │
│                                                         │
│  ┌──────────────────┐  ┌──────────────────────────┐    │
│  │  CQ3: Environ.   │  │  CQ4: Probability        │    │
│  │  Classifier      │  │  Model                   │    │
│  │                  │  │                          │    │
│  │  Favourable      │  │  Frequency-based         │    │
│  │  conditions      │  │  estimation + calibration│    │
│  └──────────────────┘  └──────────────────────────┘    │
│                                                         │
│  ┌─────────────────────────────────────────────────┐    │
│  │  Discovery Report — Consolidated verdict        │    │
│  └─────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────┘
    │
    ▼
analysis/reports/v2_discovery_*.json
```

---

## Research Questions

### CQ1 — Which individual features predict outcome?

**Module:** `research_engine/v2_discovery/feature_analyser.py`

Analyses 26 features (16 categorical + 10 continuous):

| Category | Features |
|---|---|
| H4 | regime, trend_direction, structure_state, volatility_state |
| H1 | bias, bos_confirmed, bos_direction, structure_type |
| Location | near_support, near_resistance, order_block_present |
| M15 | structure_state |
| M5 | pattern_detected, pattern_direction, pattern_quality |
| Execution | session, spread_atr_ratio, atr, volatility |
| Risk | risk_distance_pips, proposed_direction |
| Candle | candle_range, body_ratio, wick_ratio |

**Metrics per category:**
- Sample size
- Win rate
- Raw EV
- Cost-adjusted EV (raw - 0.48R spread cost)
- 95% confidence interval
- p-value vs baseline (z-test)
- Significance flag

**Continuous features** are bucketed into quartiles before analysis.

---

### CQ2 — Which combinations create predictive value?

**Module:** `research_engine/v2_discovery/context_combiner.py`

Tests 8 pre-registered hypotheses (theory-driven, not brute-forced):

| ID | Combination | Rationale |
|---|---|---|
| COMBO_1 | H4 trending + H1 BOS + M15 support + M5 pattern | Multi-TF alignment |
| COMBO_2 | H4 trending + H1 bias + London + low spread | Trend + session + cost |
| COMBO_3 | H1 BOS + order block + pattern | Structure break + institutional level |
| COMBO_4 | H4 ranging + support + BUY | Mean reversion long |
| COMBO_5 | H4 ranging + resistance + SELL | Mean reversion short |
| COMBO_6 | High quality + support + London | Best trigger + location + liquidity |
| COMBO_7 | H1 CHOCH + H4 direction + pattern | Reversal hypothesis |
| COMBO_8 | Low vol + tight spread + structure | Calm market signal clarity |

**Validation:**
- 70/30 train/test split
- In-sample AND out-of-sample metrics
- Degradation tracking (IS vs OOS)
- Minimum 30 samples per split

---

### CQ3 — What conditions create favourable environments?

**Module:** `research_engine/v2_discovery/environment_classifier.py`

8 environment dimensions:

| Dimension | States |
|---|---|
| volatility_regime | HIGH / MEDIUM / LOW |
| spread_environment | TIGHT / NORMAL / WIDE |
| session | LONDON / NY / ASIA / OFF |
| h4_regime | TRENDING / RANGING / TRANSITIONAL |
| structure_proximity | AT_SUPPORT / AT_RESISTANCE / NO_STRUCTURE |
| risk_geometry | TIGHT / MODERATE / WIDE |
| h1_alignment | ALIGNED / COUNTER / NEUTRAL |
| volatility_score | HIGH_TRAD / MED_TRAD / LOW_TRAD |

**Outputs:**
- Favourable environments (positive cost-adj EV + significant)
- Unfavourable environments (negative cost-adj EV + significant)
- Ranked by EV magnitude

---

### CQ4 — Can probability be estimated from context?

**Module:** `research_engine/v2_discovery/probability_model.py`

**Method:** Frequency-based cohort matching (interpretable, not black-box ML)

1. Encode features into categorical bins
2. Find historical records with matching feature profiles
3. Progressive relaxation: remove features until cohort size met
4. Compute empirical win probability from matched cohort

**Evaluation:**
- Accuracy vs majority-class baseline
- Brier score (calibration metric)
- Calibration buckets (predicted vs actual by decile)
- Feature importance via leave-one-out degradation

**Decision:** Model is "useful" only if accuracy > baseline + 2%.

---

## Usage

```python
from research_engine.v2_discovery.discovery_report import run_full_discovery, save_report
from core.research.v2_outcome_linker import link_outcomes

# Step 1: Link outcomes to observations
report = link_outcomes(symbol="EURUSD")

# Step 2: Run discovery
discovery = run_full_discovery(report.linked_records, min_sample=30)

# Step 3: Check conclusion
print(discovery.conclusion.outcome)
# "PREDICTIVE_INFORMATION_FOUND" or "NO_PREDICTIVE_VALUE"
print(discovery.conclusion.evidence)
print(discovery.conclusion.next_steps)

# Step 4: Save report
save_report(discovery)
```

---

## Safety Guarantees

| Guarantee | How Enforced |
|---|---|
| Never creates trades | No execution imports in any module |
| Never modifies scoring | No pipeline imports |
| Never promotes automatically | Conclusion is descriptive only |
| Minimum sample sizes | MIN_SAMPLE_SIZE=30 enforced at every computation |
| CURRENT epoch only | Input records must be pre-filtered by caller |
| Confidence intervals | 95% CI on all EV estimates |
| Prevents overfitting | CQ2: out-of-sample validation; CQ4: chronological split |
| No black-box models | All methods are interpretable frequency-based statistics |

---

## Report Output Format

```json
{
    "report_id": "DISCOVERY_20260728_125000",
    "generated_utc": "2026-07-28T12:50:00+00:00",
    "total_records": 867,
    "linked_records": 650,
    "cq1": {
        "features_analysed": 26,
        "significant_features": 2,
        "top_features": [...],
        "conclusion": "..."
    },
    "cq2": {
        "hypotheses_tested": 8,
        "validated_combinations": 1,
        "best_combination": "COMBO_2",
        "best_ev": 0.12,
        "conclusion": "..."
    },
    "cq3": {
        "favourable_environments": 3,
        "unfavourable_environments": 4,
        "best_environments": [...],
        "conclusion": "..."
    },
    "cq4": {
        "model_accuracy": 0.54,
        "baseline_accuracy": 0.52,
        "model_useful": false,
        "brier_score": 0.25,
        "conclusion": "..."
    },
    "conclusion": {
        "outcome": "NO_PREDICTIVE_VALUE",
        "confidence": "HIGH",
        "summary": "...",
        "evidence": ["CQ1: ...", "CQ2: ...", "CQ3: ...", "CQ4: ..."],
        "next_steps": ["...", "..."]
    }
}
```

---

## Test Results

| Suite | Result |
|---|---|
| `test_v2_discovery.py` | **40 passed** |
| Full regression | **3343 passed**, 1 pre-existing failure (unchanged) |
| New regressions | **0** |

---

## Decision Framework

The discovery engine's conclusion determines next research steps:

```
Discovery Outcome
    │
    ├── PREDICTIVE_INFORMATION_FOUND
    │       │
    │       ├── Validate on new epoch (n >= 200)
    │       ├── Walk-forward test
    │       ├── Do NOT implement until n >= 500 validated
    │       └── Research continues
    │
    └── NO_PREDICTIVE_VALUE
            │
            ├── Consider alternative data sources
            ├── Test different market/asset class
            ├── Increase observation granularity
            └── Accept null result if sample sufficient
```

No production changes are made regardless of outcome. The discovery engine produces evidence for the Research Director to evaluate.
