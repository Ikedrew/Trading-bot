# SV1 — Structure + Geometry Viability: Implementation Plan

---

## FINDING: No New Infrastructure Needed

The experiment can be implemented **entirely from existing data and existing code.** The INTRADAY horizon shadow trades already ARE the M15-structure variant.

### Existing Data

| Dataset | Description | n | Status |
|---------|-------------|---|--------|
| **SCALP horizon shadows** (hshadow_ without _INTRADAY) | M5 candle SL, 2:1 RR | 401 | ✅ Available |
| **INTRADAY horizon shadows** (hshadow_*_INTRADAY) | M15 structure SL, 3:1 RR | 328 | ✅ Available |
| **Paired trades** (same cycle_id + symbol) | Same entry, different SL geometry | **323** | ✅ Ready |

### What Makes These a Valid Paired Experiment

| Variable | SCALP (Control) | INTRADAY (Variant) | Isolated? |
|----------|----------------|-------------------|-----------|
| Entry signal | Same pattern, same bar | Same pattern, same bar | ✅ Identical |
| Direction | Same | Same | ✅ Identical |
| Score | Same | Same | ✅ Identical |
| H1 bias | Available (100%) | Available (100%) | ✅ Identical context |
| Phase | Available (100%) | Available (100%) | ✅ Identical context |
| **SL distance** | **2.5 pips (M5 candle)** | **7.1 pips (M15 structure)** | ⚠️ CHANGES |
| **RR target** | **2:1** | **3:1** | ⚠️ CHANGES (2 variables!) |
| Max bars | 60 | 60 | ✅ Same |

### Variable Isolation Problem

The existing INTRADAY horizon changes TWO variables simultaneously (SL distance AND RR target). For strict SV1 isolation, the experiment must:
1. Use the INTRADAY SL (M15 structure, 7.1 pip median) ✅
2. But simulate IDENTICAL exit rules (same TP logic or no TP) to isolate SL effect

**Solution:** Use `trade_state_progression` from BOTH variants and simulate identical exit logic (SL-only + timeout, no TP) — exactly as the horizon_controlled experiments already do.

---

## Components to Reuse

| Component | Location | Purpose in SV1 |
|-----------|----------|---------------|
| `load_shadow_trades(epoch='CURRENT')` | `research_engine/experiments/experiment_base.py` | Load CURRENT data |
| `build_fingerprint(epoch='CURRENT')` | Same file | Report metadata |
| `build_report()` | Same file | Standard report generation |
| `_paired_significance()` | `research_engine/experiments/horizon_controlled.py` | Paired t-test |
| `_simulate_exit()` | Same file | Controlled exit simulation |
| `_simulate_trailing()` | Same file | Trailing variant (if testing exit) |
| `_variant_stats()` | Same file | Per-variant summary statistics |
| `TYPICAL_SPREADS` dict | Used in CE1/EQ1 | Per-symbol cost calculation |
| `classify_pattern()` | `core/strategy_family/` | Family classification |
| Data quality classifier | `research_engine/data_quality/classifier.py` | Epoch enforcement |
| Validity gates | `research_engine/validity_gates.py` | Report validation |

---

## New Code Required

### One file: `research_engine/experiments/sv1_structure_geometry.py`

This file will:
1. Load CURRENT-epoch shadow trades
2. Identify SCALP vs INTRADAY paired trades (same cycle_id + symbol)
3. For each pair, extract trade_state_progression from BOTH
4. Simulate IDENTICAL exit logic on both (SL + timeout only, matching the horizon_controlled pattern)
5. Compute cost-adjusted EV for each variant
6. Run paired t-test
7. Apply H1 alignment filter as a sub-analysis
8. Produce standard report via `build_report()`

**Estimated size:** ~200 lines (all reusing existing utilities).

### One test file: `tests/test_sv1_experiment.py`

Verifying:
- Paired trades correctly identified
- SL is the only variable changing
- Cost adjustment applied correctly
- Statistical test computed
- Report passes validity gates

---

## What Already Exists vs What's New

```
EXISTING (reuse directly):
├── Shadow trade data (logs/shadow_trades/) — 323 paired trades
├── load_shadow_trades(epoch='CURRENT') — epoch-safe loading
├── build_fingerprint() / build_report() — standard reporting
├── _paired_significance() — statistical testing
├── _simulate_exit() — bar-by-bar exit simulation
├── TYPICAL_SPREADS — per-symbol cost estimation
├── validity_gates.validate_experiment_report() — report validation
└── DataEpoch.CURRENT filtering — epoch safety

NEW (create):
├── research_engine/experiments/sv1_structure_geometry.py
│   └── run_sv1() → dict (standard report)
│       ├── Load paired SCALP + INTRADAY trades
│       ├── Simulate identical exits on both (isolate SL variable)
│       ├── Compute cost-adjusted EV per variant
│       ├── Apply H1 alignment sub-filter
│       ├── Paired t-test
│       └── Return build_report(...)
├── tests/test_sv1_experiment.py
│   └── Test variable isolation, pairing, stats
└── architecture/SV1_STRUCTURE_GEOMETRY_RESULTS.md (output)
```

---

## Experiment Logic (Pseudocode)

```python
def run_sv1():
    trades = load_shadow_trades(epoch='CURRENT')
    
    # Identify pairs
    scalp_by_key = {f"{cycle}_{symbol}": trade for SCALP trades}
    intra_by_key = {f"{cycle}_{symbol}": trade for INTRADAY trades}
    pairs = scalp_by_key.keys() & intra_by_key.keys()
    
    # For each pair: simulate IDENTICAL exit (SL + timeout, no TP)
    for key in pairs:
        scalp_prog = scalp_by_key[key].state_progression
        intra_prog = intra_by_key[key].state_progression
        
        # Control: exit at SL=-1R (original SCALP risk) or timeout
        ctrl_r = _simulate_exit(scalp_prog, max_bars=60, sl_r=1.0, tp_r=99)
        
        # Variant: exit at SL=-1R (INTRADAY risk) or timeout
        # But INTRADAY progression is normalised to ITS OWN risk
        # So SL=-1R in INTRADAY = hitting M15 structure stop
        var_r = _simulate_exit(intra_prog, max_bars=60, sl_r=1.0, tp_r=99)
        
        # Cost adjustment
        ctrl_cost = spread / scalp_risk_distance
        var_cost = spread / intra_risk_distance  # LOWER (wider risk)
        
        ctrl_adj = ctrl_r - ctrl_cost
        var_adj = var_r - var_cost
    
    # Paired t-test on adj_r differences
    significance = _paired_significance(ctrl_adj_list, var_adj_list)
    
    # Sub-analysis: filter to H1 aligned + PULLBACK phase
    
    return build_report(...)
```

---

## Validation Checklist (before implementation)

| Check | Status | Evidence |
|-------|--------|---------|
| H1 structure exists | ✅ | h1_bias in 100% of INTRADAY trades |
| M15 structure levels exist | ✅ | INTRADAY uses M15 for SL (risk_config_snapshot confirms) |
| Structure stop already computed | ✅ | INTRADAY risk_pips = 7.1 avg (M15-derived) |
| Paired testing supported | ✅ | 323 matched pairs by cycle_id + symbol |
| No production code modified | ✅ | New experiment file only |
| CURRENT epoch enforced | ✅ | load_shadow_trades(epoch='CURRENT') |

### All assumptions verified. Ready to implement.

---

## Risk Assessment

| Risk | Mitigation |
|------|-----------|
| INTRADAY changes 2 variables (SL + RR) | Use trade_state_progression to simulate SL-only exit (no TP) |
| n=323 may be insufficient per sub-cell | Report CI. If CI includes zero, declare CONTINUE not PROMOTE |
| Same max_bars=60 for both (Horizon audit finding) | Acknowledged as limitation. True structure entry would hold longer. |
| Walk-forward not built into first run | Split 60/40 in the experiment itself |

---

## Estimated Implementation Effort

| Task | Time |
|------|------|
| `sv1_structure_geometry.py` | 45 minutes |
| `test_sv1_experiment.py` | 20 minutes |
| Run experiment + produce report | 10 minutes |
| **Total** | **~75 minutes** |
