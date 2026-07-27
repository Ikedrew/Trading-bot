# FOUNDATION KNOWLEDGE CARD

**Type:** Factual snapshot. Observer memory layer.
**Purpose:** Answer "What system am I looking at?"
**Last validated:** 2026-07-25 (from repository + runtime state)

---

## 1. System Identity

| Property | Value | Source | Confidence |
|:---------|:------|:-------|:----------:|
| System name | MK1 Trading Bot | Heartbeat `strategy` field | HIGH |
| Strategy identifier | `momentum_v1` | `runtime/heartbeat.json` → strategy | HIGH |
| Broker | Pepperstone | `config.MT5_TERMINAL_PATH` contains "Pepperstone" | HIGH |
| Trading terminal | MetaTrader 5 (terminal64.exe) | `config.MT5_TERMINAL_PATH` | HIGH |
| Magic number | 713001 | `config.BOT_MAGIC` | HIGH |
| Account environment | UNKNOWN | Not persisted. Heartbeat does not record demo/live. | UNVERIFIED |
| Account currency | UNKNOWN | Not persisted anywhere in config or datasets. | UNVERIFIED |
| Execution environment | Windows VM | `runtime/heartbeat.json` exists at Windows path | HIGH |
| Code version | UNKNOWN | No VERSION file. No git commit in heartbeat. | UNVERIFIED |

### Unknowns

- Account type (demo/live/prop) — requires MT5 API query or manual confirmation.
- Account currency — required to interpret PnL in meaningful units.
- Code version — no deployment marker exists.

---

## 2. Market Universe

| Property | Value | Source | Confidence |
|:---------|:------|:-------|:----------:|
| Supported symbols | EURUSD, GBPUSD, USDJPY, USDCHF, USDCAD, AUDUSD, NZDUSD | `config.SYMBOLS` | HIGH |
| Symbol count | 7 | `config.SYMBOLS` length | HIGH |
| Execution timeframe | M5 (5-minute) | `config.TIMEFRAME = 5` | HIGH |
| Higher timeframe context | H4, H1, M15 | Code evidence: `core/timeframes/`, decision_trace regime_source="H4" | HIGH |
| Correlation group 1 | EURUSD, GBPUSD, AUDUSD, NZDUSD | `config.CORRELATION_GROUPS[0]` | HIGH |
| Correlation group 2 | USDJPY, USDCHF, USDCAD | `config.CORRELATION_GROUPS[1]` | HIGH |
| Trading sessions | LONDON, NY, ASIA, OFF_SESSION | Code: `risk/session_guard.py`, execution_context session_state field | HIGH |

### Unknowns

- Exact HTF refresh frequency (how often H4/H1/M15 are recomputed) — not in config, implicit in code.
- Session transition times (DST-sensitive) — defined in session_guard code, not in config.

---

## 3. Execution Posture

### Current State

| Property | Value | Source | Confidence |
|:---------|:------|:-------|:----------:|
| Execution mode | REPLAY | `config.REPLAY_MODE = True` | HIGH |
| Execution enabled | True | `config.EXECUTION_ENABLED = True` | HIGH |
| Dry run | False | `config.DRY_RUN = False` | HIGH |
| Position sizing mode | FIXED | `config.POSITION_SIZING_MODE = "FIXED"` | HIGH |
| Fixed lot size | 0.01 | `config.FIXED_LOT = 0.01` | HIGH |
| Max open positions | 1 | `config.MAX_OPEN_POSITIONS = 1` | HIGH |

### Operational Meaning

Execution is enabled and dry run is off, but REPLAY_MODE is active. This means the system processes historical candle data, not a live market feed. Decisions and persistence records represent strategy evaluation against historical bars — not live broker interactions. The 31 trades in the journal were executed during live periods (REPLAY_MODE may have been toggled).

### Unknowns

- Whether REPLAY_MODE was active during the 31 recorded trades, or whether those trades represent a prior live session.

---

## 4. Strategy Architecture

### Current State

| Component | Status | Source | Confidence |
|:----------|:------:|:-------|:----------:|
| Decision pipeline | New Engine v1.2 (sole authority) | `config.USE_NEW_PIPELINE = True` | HIGH |
| Legacy pipeline | Disabled | `config.ALLOW_LEGACY_FALLBACK = False` | HIGH |
| Scoring system | 10-component weighted | Code: `core/pipeline/scoring_engine.py` | HIGH |
| Strategy classification | CONTINUATION / REVERSAL / FALSE_BREAK | Code: `core/pipeline/strategy_classifier.py` | HIGH |
| EV gate | Disabled | `config.ENABLE_EV_GATE = False` | HIGH |
| Market context scoring | Disabled (observational only) | `config.MARKET_CONTEXT_SCORING_ENABLED = False` | HIGH |
| Portfolio ranking | Passive (not gating execution) | `config.PORTFOLIO_RANKING_AUTHORITY = False` | HIGH |
| Horizon model | SCALP only (INTRADAY/EXTENDED shadow) | `config.PERMITTED_HORIZONS = ["SCALP"]` | HIGH |
| Horizon authority | Enabled | `config.HORIZON_AUTHORITY_ENABLED = True` | HIGH |

### Scoring Components (10)

pattern_quality, bias_alignment, market_quality, trend_alignment, chop_clarity, volatility_quality, stability_quality, confirmation_pre, htf_alignment, h4_alignment

Source: `decision_trace.components{}` field.

### Unknowns

- Score threshold value (0.35 referenced in code — not in config.py as a named constant).
- Strategy weight profiles (defined in code, not externally configurable).

---

## 5. Constraint Envelope

### Risk Limits

| Constraint | Value | Source | Confidence |
|:-----------|:------|:-------|:----------:|
| Max open positions (per symbol) | 1 | `config.MAX_OPEN_POSITIONS` | HIGH |
| Max total open positions | 3 | `config.MAX_TOTAL_OPEN_POSITIONS` | HIGH |
| Horizon max total | 21 | `config.HORIZON_MAX_TOTAL_POSITIONS` | HIGH |
| Horizon max per symbol | 3 | `config.HORIZON_MAX_POSITIONS_PER_SYMBOL` | HIGH |
| Max trades per day (total) | 20 | `config.MAX_TRADES_PER_DAY_TOTAL` | HIGH |
| Max trades per day (per symbol) | 5 | `config.MAX_TRADES_PER_DAY_PER_SYMBOL` | HIGH |
| Cooldown after loss | 600 seconds (10 min) | `config.COOLDOWN_AFTER_LOSS_SECONDS` | HIGH |
| Max total risk exposure | 3.0% | `config.MAX_TOTAL_RISK_EXPOSURE_PCT` | HIGH |
| Base RR target | 2.0 | `config.BASE_RR` | HIGH |
| Min RR | 2.0 | `config.MIN_RR` | HIGH |
| SL buffer | 0.0002 (2 pips) | `config.SL_BUFFER` | HIGH |

### Guard Enables

| Guard | Enabled | Source |
|:------|:-------:|:-------|
| Correlation guard | Yes | `config.CORRELATION_GUARD_ENABLED = True` |
| Portfolio exposure guard | Yes | `config.PORTFOLIO_EXPOSURE_GUARD_ENABLED = True` |
| Regime guard | No | `config.REGIME_GUARD_ENABLED = False` |
| Heartbeat/watchdog | Yes | `config.HEARTBEAT_ENABLED = True` |

### Unknowns

- Spread guard threshold (defined in code, not visible in config).
- Session guard hours (defined in code, DST-dependent).
- Weekend protection configuration (enabled/disabled state unclear from config alone).

---

## 6. Maturity State

### Production (Active, Controlling Decisions)

- New Engine v1.2 (decision authority)
- 10-component scoring
- Swing filter (H1 BOS requirement)
- Runtime guard chain (10 guards)
- Horizon authority (SCALP slot management)
- Break-even at 1R (trade management)
- All 24 datasets persisted to S3

### Shadow (Implemented, Observational Only)

- INTRADAY horizon (shadow trades created, not executed)
- EXTENDED horizon (shadow trades created, not executed)
- Portfolio ranking (computes but does not gate)
- Market context scoring (computes but does not influence decisions)
- Research assessment logging

### Disabled

- EV gate (config: ENABLE_EV_GATE = False)
- Regime guard (config: REGIME_GUARD_ENABLED = False)
- Legacy pipeline (ALLOW_LEGACY_FALLBACK = False)
- Trailing stop for SCALP (trailing_step = 0.0)
- Partial TP for SCALP (partial_tp_fraction = 0.0)
- Time exit for SCALP (max_time_in_trade_seconds = 0.0)

### Experimental (Being Evaluated via Shadow Data)

- INTRADAY activation readiness (requires 50+ shadow samples)
- EXTENDED activation readiness
- Portfolio ranking authority (awaiting disagreement data)

---

## 7. Evidence Landscape

### Available Evidence

| Source | Records | Time Range | Source |
|:-------|:-------:|:----------:|:-------|
| Total persistence datasets | 24 | — | `obs.health()` |
| Completed trades | 31 | 2026-07-22 to 2026-07-24 | `obs.trades(365)` |
| Patterns observed | 9 distinct | Same period | `obs.trades()` by_pattern |
| Horizons traded | 1 (SCALP only) | Same period | `obs.trades()` by_horizon |
| Decision records (latest day) | 111 | 2026-07-24 | decision_ledger file count |
| Shadow trades (latest day) | 16 | 2026-07-24 | shadow_trades file count |
| Opportunities (latest day) | 88 | 2026-07-24 | opportunities file count |
| Guard blocks (total) | 81 | All recorded | `obs.guards()` |

### Coverage Assessment

| Dimension | Coverage | Confidence for Analysis |
|:----------|:--------:|:----------------------:|
| Trade outcomes | 31 trades | LOW (insufficient for statistical significance) |
| Pattern performance | 9 patterns, max 12 trades per pattern | LOW |
| Guard impact | 81 blocks | MEDIUM (enough to identify blocking patterns) |
| Decision funnel | 111 decisions on one day | MEDIUM |
| Shadow validation | 16 horizon shadows | LOW (insufficient for activation decisions) |

---

## 8. Health Baseline

### Current State (as of last heartbeat)

| Property | Value | Source |
|:---------|:------|:-------|
| Process status | SHUTDOWN | `runtime/heartbeat.json` → status |
| Last active | 2026-07-24T23:58:01Z | `runtime/heartbeat.json` → timestamp_iso |
| Process ID | 9804 | `runtime/heartbeat.json` → pid |
| MT5 connection | UNKNOWN | `runtime/heartbeat.json` → mt5_state |
| Configuration profile | none | `runtime/heartbeat.json` → profile |
| Datasets healthy | 13 of 24 | `obs.health()` |
| Datasets stale | 4 of 24 | `obs.health()` |
| Datasets empty | 7 of 24 | `obs.health()` |

### Interpretation

The bot is not running. Last activity was 2026-07-24. 7 empty datasets are expected for datasets that only populate during specific conditions (e.g., quarantine, edge_optimisation, strategy_compiler require edge/learning events that haven't occurred yet). 4 stale datasets may indicate datasets that were active during live trading but have not received records since shutdown.

This is the EXPECTED state for a system between trading sessions — not an anomaly.

---

## 9. Known Unknowns

| # | Unknown | Why It Matters | How to Validate |
|:-:|:--------|:---------------|:----------------|
| 1 | Account type (demo/live/prop) | Determines whether 31 trades risked real capital | Query MT5 account info or add to config |
| 2 | Account currency | Cannot interpret PnL (-10.89 what?) | Query MT5 account info |
| 3 | Code version | Cannot link behaviour changes to deployments | Add commit hash to heartbeat or create VERSION file |
| 4 | Strategy performance target | Cannot assess "is this working?" without knowing goal | Owner must define target win rate / expectancy |
| 5 | Score threshold | Referenced as 0.35 in code, not in config | Confirm by reading `core/pipeline/scoring_engine.py` |
| 6 | Exact start date of strategy | First trade is 2026-07-22 but bot may have run earlier without trades | Check earliest decision_ledger or heartbeat history |
| 7 | Whether 31 trades are from live or replay | REPLAY_MODE is currently True — unclear if trades were live | Check execution_results.retcode (-1 suggests non-live for some) |
| 8 | Spread guard threshold | Active but threshold not visible in config.py | Read `risk/spread_guard.py` |
| 9 | Session guard hours | Active but times not in config.py | Read `risk/session_guard.py` |

---

*End of Foundation Knowledge Card.*
