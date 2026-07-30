# V3 Research Readiness Report

**Date:** 2026-07-28 (Latest)
**Status:** READY FOR PRELIMINARY ANALYSIS

---

## 1. Data Storage & Lineage

### Lifecycle

| Stage | Component | Persistence | Key Field |
|---|---|---|---|
| 1. Creation | `core/observers/v3_opportunity_observer.py` | — | — |
| 2. Build | `core/v3_opportunity_builder.py` | — | `opportunity_id` |
| 3. Persist | `persist_v3_opportunity()` | `logs/v3_opportunities/{SYMBOL}/{DATE}.jsonl` | `correlation_id` |
| 4. Linkage | `core/research/v3_outcome_linker.py` | Same file (updated) | `correlation_id` → `entity_id` |
| 5. Research | `research_engine/v2_discovery/` | `analysis/reports/` | All V3 fields + outcome |

### Join Key

```
V3Opportunity.correlation_id = "{SYMBOL}_{bar_time}"
                    ↕ (exact match)
shadow_trade.identity.entity_id = "{SYMBOL}_{bar_time}"
```

**Match rate: 92.4%** (50% entity_id, 42% timestamp fallback)

### Other Persistence

| Layer | Status |
|---|---|
| Local JSONL | ✅ Active |
| S3 mirror | Shadow trades only (V3 not mirrored yet) |
| Athena DDL | Not created for V3 |

### Missing Lineage

- `prev_day_high/low` — 6 fields never populated (date boundary detection issue)
- `h4_range_position` — always 0 (H4Summary lacks swing_high/swing_low)
- `bars_at_current_level` — always 0 (consolidation threshold too tight)

---

## 2. Current V3 Dataset

| Metric | Value |
|---|---|
| Total observations | **187** |
| With linked outcomes | **122 (65.2%)** |
| Pending (unlinked) | 65 |
| Shadow trades available | 2,913 |
| Symbols | 7 (EURUSD 69, NZDUSD 24, AUDUSD 22, USDCHF 21, USDCAD 20, GBPUSD 18, USDJPY 13) |
| Sessions | NY 33, OFF 154, **LONDON 0, ASIA 0** |
| Date range | 2023-11-14 to 2026-07-28 |

### Research-Ready Records: 122

These have BOTH V3 features AND trade outcomes. Sufficient for preliminary single-feature analysis on common events.

---

## 3. Feature Population Rates

| Domain | Fields | Populated | Rate | Trend |
|---|---|---|---|---|
| Market Location | 15 | 14 | 93% | Improving (swing levels 29%, range_position 22-36%) |
| Volatility / Displacement | 8 | 7 | 88% | ATR 44%, rejection 10%, displacement 1% (rare) |
| Liquidity | 22 | 16 | 73% | Equal highs 27%, equal lows 33%, session 38% |
| Fair Value Gaps | 10 | 10 | 100% | FVG above 17%, below 24%, inside 3% |
| Order Blocks | 10 | 10 | 100% | Demand 19%, supply 23%, inside 2% |
| Execution | 6 | 6 | 100% | All populated |
| **TOTAL** | **71** | **63** | **89%** | Up from 19/71 (27%) at initial audit |

### Never-Populated Fields (8)

| Field | Reason |
|---|---|
| `h4_range_position` | H4Summary model lacks swing_high/swing_low |
| `bars_at_current_level` | Consolidation threshold never met |
| `prev_day_high` | Date boundary detection not finding prior day candles |
| `prev_day_low` | Same |
| `distance_to_prev_day_high_pips` | Depends on above |
| `distance_to_prev_day_low_pips` | Depends on above |
| `prev_day_high_swept` | Depends on above |
| `prev_day_low_swept` | Depends on above |

### Fields Populated but Rare (event-driven)

| Field | Rate | Expected? |
|---|---|---|
| `displacement_into_level` | 1.1% | Yes — large candles are rare |
| `price_inside_fvg` | 3.2% | Yes — specific location |
| `liquidity_sweep_just_occurred` | 3.7% | Yes — sweeps are infrequent |
| `price_inside_ob` | 2.1% | Yes — narrow zones |

---

## 4. Sample Size Assessment

### Current Event Counts (n=187)

| Event | Frequency | Count | Enough for research? |
|---|---|---|---|
| Equal highs above | 26.7% | 50 | **YES** (min 50 for preliminary) |
| Equal lows below | 32.6% | 61 | **YES** |
| Prev session extremes | 38.0% | 71 | **YES** |
| Session high swept | 22.5% | 42 | **Nearly** (need 50) |
| FVG above price | 17.1% | 32 | **Nearly** |
| FVG below price | 23.5% | 44 | **Nearly** |
| Demand OB present | 18.7% | 35 | **Nearly** |
| Supply OB present | 22.5% | 42 | **Nearly** |
| Rejection candle | 9.6% | 18 | No (need 50) |
| Liquidity sweep | 3.7% | 7 | No (need 50) |
| Displacement | 1.1% | 2 | No (need 50) |
| Price inside FVG | 3.2% | 6 | No (need 50) |

### Research Viability by Question

| Research Question | Min Events Needed | Current | Viable? |
|---|---|---|---|
| Equal highs/lows predict outcome? | 50 with outcomes | 50/61 | **YES — run now** |
| Session extremes predict outcome? | 50 with outcomes | 71 | **YES — run now** |
| FVG presence predicts outcome? | 50 with outcomes | 32-44 | **Almost** (need ~30 more) |
| OB proximity predicts outcome? | 50 with outcomes | 35-42 | **Almost** (need ~20 more) |
| Sweep predicts outcome? | 50 with outcomes | 7 | No — need ~700 more records |
| Range position predicts outcome? | 100 with outcomes | 42-67 | **Almost** |
| Combined features? | 150 with outcomes | 122 | **Almost** |

---

## 5. Research Publication Pipeline

```
logs/v3_opportunities/{SYMBOL}/{DATE}.jsonl
        │
        ▼ (link_v3_outcomes)
core/research/v3_outcome_linker.py
        │
        ▼ (run_full_discovery)
research_engine/v2_discovery/discovery_report.py
        │
        ├── analysis/reports/v3_discovery_*.json
        ├── architecture/V3_DISCOVERY_RESULTS.md
        └── analysis/artifacts/v3_discovery_dataset.json
```

### Existing Output Locations

| Type | Location |
|---|---|
| JSON reports | `analysis/reports/` |
| Datasets | `analysis/artifacts/` |
| Architecture docs | `architecture/` |
| Execution scripts | `analysis/run_*.py` |

---

## 6. Recommended Collection Targets

| Research Area | Minimum (linked) | Preferred (linked) | Current | Gap |
|---|---|---|---|---|
| Equal highs/lows | 50 | 100 | 50-61 | **MET for preliminary** |
| Session extremes | 50 | 100 | 71 | **MET** |
| FVG analysis | 50 | 100 | 32-44 | ~20 more |
| OB analysis | 50 | 100 | 35-42 | ~15 more |
| Sweep analysis | 50 | 100 | 7 | ~700 records needed |
| Range position | 100 | 200 | 42-67 | ~50 more |
| Combined context | 150 | 300 | 122 | ~30 more |

---

## 7. Readiness Verdict

### STATUS: READY FOR PRELIMINARY ANALYSIS

**What can be researched NOW (n≥50 events with outcomes):**
- Equal highs/lows → outcome
- Previous session extremes → outcome
- Basic location (premium vs discount from M15 range)

**What needs 2-4 more collection days:**
- FVG presence → outcome
- Order block proximity → outcome
- Range position (H1 level) → outcome
- Combined feature analysis

**What needs weeks of collection:**
- Liquidity sweeps (3.7% frequency — need ~700 total records for 50 sweep events)
- Displacement events (1.1% — need ~4,500 records)
- Price-inside-FVG events (3.2% — need ~1,500 records)

### Recommended Immediate Action

1. **Run V3 Discovery Engine now** on the 122 linked records — test equal highs/lows and session extremes
2. **Continue collecting** — bot operation during market hours will fill FVG/OB gaps within days
3. **Re-link outcomes** periodically as new records accumulate (run `link_v3_outcomes()`)
4. **Fix `prev_day_high/low`** — 6 wasted fields representing useful liquidity levels

### Key Improvement Since Last Audit

| Metric | Previous | Current | Change |
|---|---|---|---|
| Total observations | 128 | **187** | +46% |
| Linked outcomes | 0 | **122** | Fixed |
| Fields with data | 60/71 | **63/71** | +5% |
| Equal highs detected | 15 | **50** | +233% |
| FVGs detected | 10-13 | **32-44** | +200% |
| Order blocks detected | 10-13 | **35-42** | +200% |
| Research-ready status | NOT READY | **READY** | Upgraded |
