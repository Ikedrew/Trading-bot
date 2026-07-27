# Shadow EV Model — Research Documentation

## Purpose

Alternative EV calculation models running in parallel with production logic for research comparison. Determines whether the production EV gate is the primary bottleneck preventing profitable trading.

## Architecture

```
research_engine/shadow_ev/
├── __init__.py
├── models.py       # Three alternative EV models + assessment schema
├── replay.py       # Historical replay engine + comparison metrics
├── run_shadow_ev.py  # CLI runner
└── README.md       # This file
```

Completely isolated from production. No imports from `core.pipeline`, `execution`, or `risk`.

## Models

### EXISTING (Production Replication)
```
p_base = score_neutral × 0.6 + strategy_confidence × 0.4
p_success = p_base × confirmation_modifier × (1 - dampening)
EV = p_success × RR - (1 - p_success) × 1.0
```
Problem: strategy_confidence = 0 always → 40% of formula is dead weight.

### MODEL A — Empirical Historical
```
p_success = pattern_historical_win_rate × (1 - dampening)
EV = p_success × RR - (1 - p_success) × 1.0
```
Uses actual observed win rates per pattern. Dampening reduced (0.10 vs 0.20 for TRANSITIONAL).

### MODEL B — Bayesian
```
posterior = (empirical_wr × n + prior × prior_weight) / (n + prior_weight)
p_success = posterior × (1 - dampening)
```
Conservative for small samples (shrinks toward 30% prior). Trusts data as n grows.

### MODEL C — Conditional
```
p_success = P(win | regime, pattern) when enough samples
          → P(win | pattern) fallback
          → 30% prior fallback
```
Uses most specific conditional probability available. Minimum 8 samples for conditional.

## Assumptions

1. Win rate is estimated from counterfactual simulation of ALL decisions (not just executed)
2. Counterfactual uses same SL/TP rules as live engine
3. Dampening in shadow models is reduced (0.10 vs 0.20) to avoid double-penalising
4. RR is identical to production (2.0 base, 3.0 for RR3 patterns)
5. No spread/slippage modelled in counterfactual

## Limitations

1. Uses full-dataset win rates (not walk-forward) — Q4.2 showed walk-forward degrades results
2. THREE_INSIDE_DOWN dominates Model A/B selections — single pattern risk
3. Counterfactual outcomes assume ideal execution (no spread, no slippage)
4. Small TRENDING sample (n=25) limits conditional model precision

## Results Summary (n=3,058)

| Model | Approved | Win Rate | EV/Trade | Total R | Max DD |
|-------|-------:|--------:|---------:|--------:|-------:|
| EXISTING | 273 | 25% | -0.043R | -11.6R | 33.7R |
| MODEL_A | 69 | 46% | +0.366R | +25.2R | 8.0R |
| MODEL_B | 69 | 46% | +0.366R | +25.2R | 8.0R |
| MODEL_C | 77 | 45% | +0.315R | +24.2R | 7.0R |

## Future Replacement Criteria

Shadow EV models should replace production only when:
1. Walk-forward validation shows positive EV across ≥ 3 out-of-sample periods
2. At least 3 different patterns contribute to positive performance
3. Max drawdown < 15R on out-of-sample data
4. Sample size ≥ 200 decisions with outcomes

Currently: walk-forward (Q4.2) shows 0/5 splits positive → NOT ready for production.
