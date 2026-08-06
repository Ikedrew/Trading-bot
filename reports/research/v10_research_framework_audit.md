# V10 Research Framework — Comprehensive Audit

Generated: 2026-08-06

---

## 1. Research Question Inventory

### Implemented V10 Research Questions (with reports)

| ID | Name | Status | Report | Dataset |
|---|---|---|---|---|
| V10-E1 | True System Expectancy | **Implemented** | `v10_e1_system_expectancy_report.json/.md` | research_ready (84 trades) |
| V10-E2 | Pattern Expectancy | **Implemented** | `v10_e2_pattern_expectancy_report.json/.md` | research_ready (84) |
| V10-M1 | Regime Predicts Outcomes | **Implemented** | `v10_m1_regime_expectancy_report.json/.md` | research_ready (84) |
| V10-D1 | Scoring Components Predict R | **Implemented** | `v10_d1_scoring_predictive_power_report.json/.md` | research_ready + decision_trace |
| V10-D2 | EV Calibration | **Implemented** | `v10_d2_ev_calibration_report.json/.md` | research_ready + decision_trace |
| V10-D3 | Decision Threshold Effectiveness | **Implemented** | `v10_d3_decision_threshold_report.json/.md` | research_ready + decision_trace |
| V10-OQ1 | Opportunity Quality Predictive | **Implemented** | `v10_oq1_opportunity_quality_report.json/.md` | research_ready + decision_trace |
| V10-OQ2 | Opportunity Failure Analysis | **Implemented** | `v10_oq2_opportunity_failure_analysis_report.json/.md` | research_ready + decision_trace |
| V10-R1 | Risk Model Effectiveness | **Implemented** | `v10_r1_risk_model_effectiveness_report.json/.md` | research_ready (84) |
| V10-R2 | Stop Placement Effectiveness | **Implemented** | `v10_r2_stop_effectiveness_report.json/.md` | research_ready (84) |
| V10-R2-FX | FX Stop Effectiveness | **Implemented** | `r2_fx_stop_effectiveness_report.json/.md` | FX_ONLY view (82) |
| Anomaly | Dataset Anomaly Analysis | **Implemented** | `anomaly_analysis_report.json/.md` | research_ready (84) |

### Registered V10 Questions (not yet implemented)

| ID | Name | Status | Blocker |
|---|---|---|---|
| V10-E3 | Strategy Family Expectancy | BLOCKED | Strategy field at 14% coverage |
| V10-E4 | Out-of-Sample Validation | BLOCKED | Need 200+ trades |
| V10-M2 | HTF Alignment Value | PARTIAL | HTF alignment not in research dataset |
| V10-M3 | Regime + Volatility Interaction | BLOCKED | Volatility state not in dataset |
| V10-OQ2* | Opportunity Ranking Accuracy | BLOCKED | Shadow ranking data accumulating |
| V10-SC1 | V10 Strategy Family Edge | BLOCKED | Strategy field 14% |
| V10-SC2 | Strategy × Regime Interaction | BLOCKED | Strategy field 14% |
| V10-R2* | Probability of Ruin | BLOCKED | Need 200+ trades |
| V10-R3 | Quality-Scaled Sizing | BLOCKED | Opportunity quality not in dataset |
| V10-X1 | Execution Quality by Session | BLOCKED | Execution results not joined |
| V10-X2 | Protection Verification Reliability | PARTIAL | Protection audit data exists but not joined |
| V10-EX1 | Exit Reason Distribution | READY | (answered within R1) |
| V10-EX2 | Trailing Stop Effectiveness | BLOCKED | Need MFE/MAE from shadow trades |
| V10-EX3 | Horizon Determines Exit Policy | PARTIAL | Horizon at 67% |
| V10-L1 | Pattern Degradation | PARTIAL | Only 14 days of data |
| V10-L2 | Architecture Improvement Tracking | BLOCKED | Need pre-V10 comparable data |

---

## 2. R1 Location

### V10-R1: Risk Model Effectiveness

| Component | Location |
|---|---|
| Report (JSON) | `reports/research/v10_r1_risk_model_effectiveness_report.json` |
| Report (MD) | `reports/research/v10_r1_risk_model_effectiveness_report.md` |
| Implementation | Inline script (run_v10_r1.py — deleted after execution) |
| Dataset | `logs/research_ready_trade_dataset/research_ready_trades.jsonl` |
| Functions used | `statistics.mean/median`, custom `group_metrics()` |
| Conclusion | STOPS_NEED_REVIEW — 81% SL hit rate |
| Key finding | 1-2R bucket: +0.10R expectancy; 2-3R bucket: -0.31R |

**Compatible with current dataset:** YES — uses `research_ready_trades.jsonl` which is current.

### Also: V10-R2 (Stop Placement) and V10-R2-FX (FX Stop Analysis)

| Report | Conclusion |
|---|---|
| `v10_r2_stop_effectiveness_report` | Winners have 30.3 pip stops vs losers 3.4 pips |
| `r2_fx_stop_effectiveness_report` | 32% of FX losses are stop hunts; stops too tight |

---

## 3. Every Research Report

### `reports/research/` (12 reports, 24 files)

| Filename | Date | Purpose | Current? |
|---|---|---|---|
| `v10_e1_system_expectancy_report` | 2026-08-05 | Baseline expectancy | YES |
| `v10_e2_pattern_expectancy_report` | 2026-08-05 | Pattern-level edge | YES |
| `v10_m1_regime_expectancy_report` | 2026-08-05 | Regime prediction | YES |
| `v10_d1_scoring_predictive_power_report` | 2026-08-05 | Score predictiveness | YES |
| `v10_d2_ev_calibration_report` | 2026-08-05 | EV/confidence calibration | YES |
| `v10_d3_decision_threshold_report` | 2026-08-05 | Threshold optimization | YES |
| `v10_oq1_opportunity_quality_report` | 2026-08-05 | Component analysis | YES |
| `v10_oq2_opportunity_failure_analysis_report` | 2026-08-05 | Failure mode diagnosis | YES |
| `v10_r1_risk_model_effectiveness_report` | 2026-08-05 | R:R and exit analysis | YES |
| `v10_r2_stop_effectiveness_report` | 2026-08-05 | Stop distance analysis | YES |
| `r2_fx_stop_effectiveness_report` | 2026-08-06 | FX-specific stop analysis | YES |
| `anomaly_analysis_report` | 2026-08-06 | Anomaly classification | YES |

### Other report locations

| Location | Content |
|---|---|
| `analysis/reports/` | Legacy research (q3-q9 JSONs from old engine) |
| `logs/validated_trade_dataset/validation_summary.json` | Dataset validation stats |
| `logs/research_ready_trade_dataset/research_summary.json` | Research-ready stats |

---

## 4. Research Modules

### Core Research Pipeline (data integrity)

| Module | Purpose | Input | Output |
|---|---|---|---|
| `core/trades_clean.py` | Correct PnL for non-FX instruments | trade_journal | logs/trades_clean/ |
| `core/validated_trade_dataset.py` | 7-check validation pipeline | trade_journal + decision_trace + trades_clean | logs/validated_trade_dataset/ |
| `core/research_ready_dataset.py` | Final integrity filtering (5 gaps) | validated_trade_dataset | logs/research_ready_trade_dataset/ |
| `core/research_anomaly.py` | Anomaly classification + dataset views | research_ready_trades | Annotated views (FULL/FX_ONLY/NORMALISED/INDEX_ONLY) |

### Research Engine (experiment framework)

| Module | Purpose |
|---|---|
| `research_engine/registry/v10_research_registry.py` | V10 question definitions (23 questions) |
| `research_engine/registry/research_question_registry.py` | Original registry (55 questions) |
| `research_engine/registry/research_registry_v1_old_engine.py` | Frozen old engine reference |
| `research_engine/registry/v10_migration_report.py` | Migration mapping (old → V10) |
| `research_engine/experiments/` | 20+ experiment runners (legacy, mostly unused) |
| `research_engine/data_access/loaders.py` | Data loading utilities |
| `research_engine/correlation/linker.py` | Entity ID linkage |
| `research_engine/command_center/` | Research orchestration (planned) |

### V10 Trading Intelligence

| Module | Purpose |
|---|---|
| `core/v10/opportunity_ranking.py` | Active ranking engine (shadow mode) |
| `core/v10/opportunity_ranking_persistence.py` | Shadow ranking data persistence |
| `core/portfolio_ranking/` | Portfolio-aware ranking (context, persistence, shadow comparison) |

### Lambda

| Module | Purpose |
|---|---|
| `lambda/anomaly_analysis/` | AWS Lambda wrapper for anomaly analysis |

---

## 5. Research Dataset Map

### Raw Datasets (produced by live bot)

| Dataset | Location | S3 Mirror | Used By |
|---|---|---|---|
| trade_journal | `logs/trade_journal/{date}.jsonl` | YES (`trade_journal/`) | validated_trade_dataset |
| decision_trace | `logs/decision_trace/{symbol}/{date}.jsonl` | YES (`decision_trace/`) | validated_trade_dataset, D1, D2, D3, OQ1, OQ2 |
| shadow_trades | `logs/shadow_trades/` | YES (`shadow_trades/`) | Old engine experiments |
| opportunities | `logs/opportunities/` | YES | Opportunity lifecycle |
| execution_results | `logs/execution_results/` | YES | X1 (planned) |
| protection_audit | `logs/protection_audit/` | NO | X2 (planned) |
| ranking_shadow | `logs/ranking_shadow/` | NO | OQ2 ranking accuracy (planned) |
| live_market_state | `logs/live_market_state/` | NO | Market context research |

### Derived Datasets (produced by research pipeline)

| Dataset | Location | S3 Mirror | Used By |
|---|---|---|---|
| trades_clean | `logs/trades_clean/{date}.jsonl` | NO | validated_trade_dataset |
| validated_trade_dataset | `logs/validated_trade_dataset/` | NO | research_ready_dataset |
| **research_ready_trade_dataset** | `logs/research_ready_trade_dataset/` | **NO** | ALL V10 research experiments, anomaly analysis |

---

## 6. Missing Research Questions

### Layer 1 — Trade Outcome / Execution Truth

| Missing | What it should investigate | Required data | Expected output |
|---|---|---|---|
| Slippage analysis | Real vs expected entry price | execution_results | Slippage model per symbol/session |
| Fill quality tracking | Rejection rates, partial fills | execution_results | Broker reliability score |
| Commission/swap impact | Real costs vs assumed | MT5 deal history | Net vs gross expectancy |

### Layer 2 — Decision Engine Quality

| Missing | What it should investigate | Required data | Expected output |
|---|---|---|---|
| Score weight optimization | Which weights produce best R-prediction | decision_trace + outcomes | Optimal weight vector |
| Confirmation timing | Is M5 confirmation too early/late? | Tick-level or M1 data | Optimal confirmation delay |
| Multi-candidate cycle analysis | When multiple signals fire, which is best? | ranking_shadow + outcomes | Ranking model validation |

### Layer 3 — Market Understanding

| Missing | What it should investigate | Required data | Expected output |
|---|---|---|---|
| Volatility impact on edge | Does ATR level predict expectancy? | market_context + outcomes | Vol-adjusted sizing |
| Session edge | Do specific hours produce edge? | Entry timestamps + outcomes | Session filter |
| Cross-symbol correlation | Are losses correlated across symbols? | Trade timing + outcomes | Correlation risk |

### Layer 4 — Strategy Intelligence

| Missing | What it should investigate | Required data | Expected output |
|---|---|---|---|
| Strategy family profitability | Which V10 families work? | Strategy field (currently 14%) | Family-level controls |
| Horizon calibration | Are SCALP/INTRADAY durations correct? | Duration + horizon + outcomes | Horizon parameter tuning |
| Entry method effectiveness | CONFIRMATION vs LIMIT vs BREAK | Entry method + outcomes | Method-specific parameters |

---

## 7. Migration Readiness (Lambda)

### Tier 1: Ready to migrate immediately

| Question | Dependencies | Input | Difficulty |
|---|---|---|---|
| **Anomaly Analysis** | None (already built) | research_ready_trades.jsonl | DONE (Lambda package exists) |
| **V10-E1** | None | research_ready_trades.jsonl | EASY — self-contained calculation |
| **V10-E2** | None | research_ready_trades.jsonl | EASY — pattern grouping |
| **V10-R1** | None | research_ready_trades.jsonl | EASY — risk geometry |

### Tier 2: Requires data join (decision_trace needed)

| Question | Additional Input | Difficulty |
|---|---|---|
| V10-M1 | decision_trace (for regime) | MEDIUM — needs S3 multi-file read |
| V10-D1 | decision_trace (for components) | MEDIUM — S3 join on cycle_id |
| V10-D2 | decision_trace (for EV/p_success) | MEDIUM — same as D1 |
| V10-D3 | decision_trace | MEDIUM — builds on D1/D2 |
| V10-OQ1 | decision_trace | MEDIUM — component analysis |
| V10-OQ2 | decision_trace | MEDIUM — failure classification |

### Tier 3: Requires additional infrastructure

| Question | Blocker | Difficulty |
|---|---|---|
| V10-E3, SC1, SC2 | Strategy field population (14% → needs live data accumulation) | HARD (data gap) |
| V10-E4, R2 | Sample size (need 200+ trades) | HARD (time-dependent) |
| V10-EX2 | Shadow trades with MFE/MAE | HARD (bar-by-bar data) |
| V10-X1 | Execution results dataset join | MEDIUM (data exists, needs reader) |

### Migration Priority Ranking

1. **Anomaly Analysis** — already packaged as Lambda ✓
2. **V10-E1** (System Expectancy) — single-file read, pure calculation
3. **V10-E2** (Pattern Expectancy) — same simplicity
4. **V10-R1** (Risk Model) — same simplicity
5. **V10-R2-FX** (FX Stops) — needs anomaly classification for FX filtering
6. **V10-M1** (Regime) — needs decision_trace join from S3
7. **V10-D1** (Scoring) — needs decision_trace join

---

## Summary Statistics

| Category | Count |
|---|---|
| Implemented research questions | 12 |
| Registered V10 questions (total) | 23 |
| Old engine questions (frozen) | 55 |
| Research reports generated | 24 files (12 JSON + 12 MD) |
| Research modules in `research_engine/` | 80+ Python files |
| Raw datasets available | 8 |
| Derived datasets | 3 |
| Lambda packages | 1 (anomaly_analysis) |
| Questions ready for Lambda | 4 (Tier 1) |
| Questions blocked on data | 6+ |
