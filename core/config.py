"""MK1 live trading configuration (Pepperstone MT5)."""

from __future__ import annotations

import os as _os

# Load .env file (project root) for secrets/configuration.
# python-dotenv reads .env without affecting system environment.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv not required — env vars can be set directly

import MetaTrader5 as mt5

SYMBOL = "EURUSD", "GBPUSD", "USDJPY","USDCHF","USDCAD", "AUDUSD", "NZDUSD"

# Multi-symbol support: list of symbols to evaluate sequentially.
# When populated, overrides SYMBOL for batch/replay runs.
SYMBOLS = ["EURUSD", 
           "GBPUSD", 
           "USDJPY", 
           "USDCHF", 
           "USDCAD", 
           "AUDUSD", 
           "NZDUSD",
           "NAS100",
           "US500",
           "XAUUSD",
]

# Canonical symbols (broker-agnostic base names).
# The symbol resolver maps these to broker-specific names at runtime.
# These are the SYSTEM TRUTH — never include broker suffixes here.
CANONICAL_SYMBOLS = [
    "EURUSD",
    "GBPUSD",
    "USDJPY",
    "USDCHF",
    "USDCAD",
    "AUDUSD",
    "NZDUSD",
    "NAS100",
    "US500",
    "XAUUSD",
]


TIMEFRAME: int = mt5.TIMEFRAME_M5


# ==========================================
# RUNTIME CONTROL SWITCHES
# ==========================================
# All toggles that gate subsystem behaviour.
# Toggle these for FREE RUN (safe decision pipeline testing):
#   EVENT_STREAM_ENABLED=False, EVENT_STREAM_S3_MIRROR=False,
#   EXECUTION_ENABLED=False, DRY_RUN=True

# --- Execution modes ---
REPLAY_MODE = False                 # When True, runs replay-only (no live feed or broker)
DRY_RUN = False                      # When True, simulates execution without sending orders
VALIDATION_MODE = True              # When True, enables runtime safety + consistency checks

# --- Event persistence ---
EVENT_STREAM_ENABLED = True         # Enables/disables local JSONL event logging (events/*.jsonl)
EVENT_STREAM_S3_MIRROR = True       # Enables/disables S3 batch event persistence
DECISION_AUDIT_ENABLED = True       # Enables/disables decision audit trail persistence

# --- Broker execution ---
EXECUTION_ENABLED = True           # Enables/disables live order placement (master kill for broker I/O)
TRADE_MANAGEMENT_ENABLED = True    # Enables/disables SL/TP modifications (post-entry management)
POSITION_CLOSE_ENABLED = True      # Enables/disables broker close actions

# --- External side effects ---
ALERTING_ENABLED = True            # Enables/disables Discord/webhook alerts
METRICS_ENABLED = True              # Enables/disables metrics emission (equity curve, dashboard)
# ==========================================

# Essential logs: when True, core trading events are emitted
# (ENGINE_START, HEARTBEAT, BIAS_CHANGE, SETUP_FOUND, ENTRY_SIGNAL, TRADE_EXECUTED, etc.)
# When False, these are suppressed entirely.
ESSENTIAL_LOGS = True

# --- Individual debug log switches (non-essential, granular control) ---
FULL_DEBUG_REPLAY = False       # FULL_DEBUG per-bar dumps in run_replay
FULL_DEBUG_LIVE = False         # FULL_DEBUG per-bar dumps in run_live
TICK_READY_LOGS = False         # TICK_READY tick-level readiness logs
Q_MODULE_DEBUG_LOGS = False     # Raw Q1–Q7 orchestrator print output
LEGACY_DECISION_LOGS = False    # brain_logger.py / log_decision output
DRY_RUN_EXECUTION_LOGS = True   # "[DRY RUN] Trade blocked" output

# --- New Pipeline (feature flag) ---
USE_NEW_PIPELINE = True         # New engine is now the SOLE execution authority
ALLOW_LEGACY_FALLBACK = False   # When False, new engine failure blocks trading (fail-safe). When True, falls back to old pipeline.
ENABLE_LEGACY_SHADOW_PIPELINE = False  # When True, old pipeline runs as shadow (comparison). When False, old pipeline never executes during live trading.

# --- V10 Engine Mode ---
# Controls which decision engine the live scanner uses.
# "V10"    — V10Pipeline is the active trading brain (default)
# "LEGACY" — Existing New Engine remains active (V10 unused)
ENGINE_MODE = "V10"

# --- V10 S3 Production Write Guard ---
# ALL three conditions must be True for S3 writes to v10-engine bucket.
# This prevents tests, replay, development, and accidental scripts from polluting production data.
LIVE_MODE = True                        # True only in live trading runtime (False in test/replay/dev)
ALLOW_PRODUCTION_S3_WRITE = True        # Explicit opt-in for production S3 writes

# --- Research Integration (feature flag) ---
USE_EMPIRICAL_PROBABILITY = False  # When True, EV gate uses empirical pattern win rates from Research Engine. When False (default), existing synthetic p_success formula is used.
RESEARCH_ASSESSMENT_LOGGING = True  # When True, research assessment is computed and logged alongside decisions (observability only, never affects execution)

# --- Market Context Layer (feature flag) ---
MARKET_CONTEXT_ENABLED = True           # When True, MarketContextBuilder runs per cycle (observational only — never affects decisions)
MARKET_CONTEXT_SCORING_ENABLED = False  # When True, engine reads scores from MarketContext (Phase 3 — DO NOT enable without validation)
MARKET_CONTEXT_PERSISTENCE_ENABLED = True  # When True, persists MarketContext on material change (JSONL + S3)

# --- EV Gate (execution experiment) ---
ENABLE_EV_GATE = False                   # When True, only positive-EV trades are allowed to execute. When False, EV is calculated and logged but does NOT block execution.

# --- MT5 runtime lifecycle ---
MT5_CENTRALISED_INIT = True     # When True, main.py owns mt5.initialize/shutdown (single authority)
MT5_TERMINAL_PATH = r"C:\Program Files\Pepperstone MetaTrader 5\terminal64.exe"
MT5_RECONNECT_COOLDOWN_SECONDS = 10.0  # Base seconds between reconnect attempts
MT5_RECONNECT_MAX_COOLDOWN_SECONDS = 60.0  # Maximum backoff cap (seconds)
OBSERVABILITY_VALIDATION_ENABLED = False  # When True, trade event buffer collects events for validation
MULTI_SYMBOL_SCANNER_ENABLED = True  # When True, uses scan-based multi-symbol engine instead of per-symbol loop
ENGINE_STATE_STRICT_VALIDATION = False  # When True, invalid EngineState raises exception (use in dev/test only)

# --- Portfolio Intelligence (ranking authority) ---
PORTFOLIO_RANKING_AUTHORITY = False  # When True, ranking gates execution (only selected symbol may execute). When False (default), ranking is passive observation only.
PORTFOLIO_RANKING_SHADOW_LOG = True  # When True, logs shadow comparison (what executed vs what ranking would have selected). Requires no authority.

# --- Stale data detection ---
STALE_TICK_TIMEOUT_SECONDS = 30.0
STALE_CANDLE_TIMEOUT_SECONDS = 600.0
MARKET_HEARTBEAT_TIMEOUT_SECONDS = 120.0
STALE_ESCALATION_WARNING_SECONDS = 60.0
STALE_ESCALATION_CRITICAL_SECONDS = 300.0
LIVENESS_STALL_THRESHOLD_SECONDS = 10.0  # If loop iteration takes longer than this, flag as STALLED
FEED_STALE_THRESHOLD_SECONDS = 5.0      # If tick delta exceeds this, feed is STALE

CANDLE_COUNT = 300
POLL_SECONDS = 1.0

# Output control:
# - FULL_DEBUG: verbose runtime logs
# - EVENT_ONLY: meaningful state-change events only
# - SILENT: no normal runtime logs
PRINT_MODE = "EVENT_ONLY"

# --- MK1 pipeline ---
MARKET_FILTER_LOOKBACK = 5
# Sum of (high-low) over last N closed bars must exceed this (tune per symbol)
MIN_SUM_RANGE_5BARS = 0.0015
# Net move vs total range; below = too choppy
CHOP_NET_MOVE_RATIO = 0.25

SETUP_MA_PERIOD = 10
# Minimum distance from MA in price (bias must be clear)
SETUP_MIN_DISTANCE_FROM_MA = 0.00008
TREND_EMA_PERIOD = 50

TREND_FILTER_ENABLED = True
CHOP_FILTER_ENABLED = True
PATTERN_CONFIRMATION_ENABLED = True

MIN_SCORE_TO_TRADE = 4.6  # [CALIBRATION TEST] was 5 — revert after 100 cycles
# Max fraction of overlapping adjacent candles in chop window.
CHOP_OVERLAP_RATIO_MAX = 0.7

# Bias state machine (external states: BUILDING/CONFIRMED/EXPIRED)
BIAS_CONFLUENCE_THRESHOLD = 4.0
BIAS_CONFIRMATION_CANDLES = 1
BIAS_LOCK_CANDLES = 3
BIAS_LOCK_SECONDS = 900.0
BIAS_EXPIRY_SECONDS = 7200.0
BIAS_OPPOSITE_STRENGTH_THRESHOLD = 60.0

# Risk: fixed size, minimum reward:risk
FIXED_LOT = 0.01
MIN_RR = 2.0
BASE_RR = 2.0
SL_BUFFER = 0.0002
RR3_PATTERNS = frozenset({"THREE_WHITE_SOLDIERS", "THREE_BLACK_CROWS"})

# Exposure / loop
BOT_MAGIC = 713_001
COOLDOWN_SECONDS = 300.0

# --- Strategy Identity / Magic Number Registry (G1) ---
STRATEGY_NAME = "momentum_v1"
MAGIC_NUMBER_REGISTRY = {
    "momentum_v1": 713001,
    "mean_reversion_v1": 713002,
    "breakout_v1": 713003,
    "funded_eval_v1": 713004,
}
COOLDOWN_AFTER_LOSS_SECONDS = 600.0     # Longer cooldown after stop-loss (per-symbol)
TRADE_COOLDOWN_STATE_FILE = "logs/trade_cooldown_state.json"
MAX_OPEN_POSITIONS = 1

# --- Correlation guard (currency exposure + pair clustering) ---
CORRELATION_GUARD_ENABLED = True        # When True, blocks entries exceeding currency limits
MAX_CURRENCY_EXPOSURE_LOTS = 15.0       # Max net exposure per currency (in lots)
MAX_CORRELATION_GROUP_POSITIONS = 2     # Max open positions in same correlation group
CORRELATION_GROUPS = [
    ["EURUSD", "GBPUSD", "AUDUSD", "NZDUSD"],
    ["USDJPY", "USDCHF", "USDCAD"],
    ["NAS100", "US500"],            # Index group
    ["XAUUSD"],                     # Commodity group (uncorrelated)
]

# --- Layer 9: trade management (post-entry only; never influences DecisionEngine) ---
# (TRADE_MANAGEMENT_ENABLED is in RUNTIME CONTROL SWITCHES section above)
# 0 = disabled for each optional behaviour (price/time/risk geometry only).
TM_BREAK_EVEN_TRIGGER_RR = 1.0
TM_BREAK_EVEN_BUFFER_RR = 0.1
TM_TRAILING_STEP = 0.0
TM_TRAILING_START_RR = 0.0
TM_PARTIAL_TP_FRACTION = 0.0
TM_PARTIAL_TP_PATH_FRACTION = 0.0
TM_MAX_TIME_IN_TRADE_SECONDS = 0.0

# --- Replay time window (optional, unix timestamps) ---
# Set to None for full replay (all available candles).
# Set to unix timestamp (int) to slice replay to a specific window.
# Example: replay last 7 days → REPLAY_START_TIME = int(time.time()) - 7*86400
REPLAY_START_TIME = None   # e.g. 1716422400 (unix timestamp)
REPLAY_END_TIME = None     # e.g. 1717027200 (unix timestamp)

# --- Decision audit trail ---
# (DECISION_AUDIT_ENABLED is in RUNTIME CONTROL SWITCHES section above)
DECISION_AUDIT_DIR = "logs/decision_audit"   # Output directory for JSONL files
DECISION_AUDIT_INCLUDE_REJECTIONS = True    # When True, also audit rejected trade intents
DECISION_AUDIT_FLUSH_EVERY_WRITE = True      # fsync after each write (crash resilient)

# --- No-trade alert observability ---
NO_TRADE_ALERT_THRESHOLD = 100          # First alert after N consecutive no-trade cycles
NO_TRADE_ALERT_REPEAT_INTERVAL = 25     # Repeat alert every N cycles while still no-trade

# --- Discord V2 rendering layer (Phase 1 — foundation) ---
ENABLE_DISCORD_V2 = True                    # Discord V2 active — legacy webhooks disabled

# --- Legacy Discord webhook control ---
LEGACY_DISCORD_ENABLED = False              # When False, all send_discord() calls become no-ops (V2 replaces them)

# --- Discord V2 Bot Token (Phase 4 — API delivery) ---
# Load from environment variable for security. Never hardcode.
DISCORD_BOT_TOKEN = _os.getenv("DISCORD_BOT_TOKEN", "")

# --- Discord V2 Live Market Channels (Phase 2) ---
# Channel IDs for editable live market cards (one per symbol).
# Leave empty until Discord bot is connected.
DISCORD_LIVE_CHANNELS: dict = {
    "AUDUSD": "1534253542670602331",
    "EURUSD": "1534253692193083506",
    "GBPUSD": "1534253775219589202",
    "USDJPY": "1534254092619219034",
    "USDCAD": "1534253951371968532",
    "USDCHF": "1534253997140217937",
    "NZDUSD": "1534253857927073912",
    "NAS100": "1534254217936769054",
    "US500": "1534254290816995568",
    "XAUUSD": "1534254416432201898",
}

# --- Discord V2 Consolidated Channels (Phase 3) ---
# Channel IDs for human-question based channels.
# Leave empty until Discord bot is connected.
DISCORD_V2_CHANNELS: dict = {
    "opportunities": "1534256381606232114",      # "What opportunities happened?"
    "opportunity_flow": "1534558081508442153",   # Opportunity lifecycle funnel (prototype)
    "executions": "1534256459121168525",         # "What trades happened?"
    "system": "1534256711664271440",             # "Is the machine alive?"
}

# --- External alerting ---
DISCORD_WEBHOOK_URL = ""                # Discord webhook URL (empty = disabled)

# --- Discord structured logging (per-channel webhooks) ---
# Each key maps to a Discord channel. Set webhook URLs to enable routing.
# Leave empty string to disable a specific channel.
DISCORD_WEBHOOKS: dict = {
    "system-status": "https://discord.com/api/webhooks/1517152440372170793/FMFxYQlzLRagbvuu6Dfi4aOeBsek0Ka1B08mNOHSzqZ4X1MWPDJxXck0ZYO3ZLigkTQi",        # Bot startup/shutdown/kill switch
    "heartbeat": "https://discord.com/api/webhooks/1517462536440774676/T1mJOw0KyBXZjaMoxf-1_17Sx_xaOKilyNFtC7QoypbYJtAqRMiZIMgP-gf_6Xux-_H4",            # Periodic alive signal
    "errors": "https://discord.com/api/webhooks/1517158945117180026/yyYxgrWYVJuEwjiH8ZuhMQhA5wSCCfAKxievy6XxKdQzfHddHhHR-_MGp41KzzH66hDv",               # Runtime errors
    "decision-log": "https://discord.com/api/webhooks/1517152101598494880/n0Xvn6ESb7-pdtqtq-aVril5JYHB0CfnJIM21gv3nfLFx6HjIlOlMF-DDRHREAMHqxPx",         # Trade decisions (allow/block + reason)
    "trade-execution": "https://discord.com/api/webhooks/1517151882093527211/0G-0z95Zij-frcVmIZJsNSt2KYz9OIDB32kZnyf3zH6clWi9i9IC8uiUfLYNXboP1dGo",      # Order attempts, fills, modifications
    "risk-log": "https://discord.com/api/webhooks/1517151249135566888/9R5Ue-lfU0xyNKDopG_iFKD0iIu7xY4CMphU0vdbP4zFXiEYsiZzkm54_hOxrfuQjlP0",             # Risk blocks, exposure updates
    "market-context": "https://discord.com/api/webhooks/1517150719948492882/UerwHMq_opoTC9TD2e7S-H3TByFbIQsSXQn0l_BvIqC5Lil4dH8xfmrcOuqwSJpMxEIq",       # Market regime / context changes
    "performance-summary": "https://discord.com/api/webhooks/1517150425156030651/QUprxbreNwxpxvmRijsRiZnVAD43atE_vKXniYg1U7szLZG6bPnjo60CBy8afSOLrppf",  # Daily reports, trade results
    "pnl-drawdown": "https://discord.com/api/webhooks/1517149970170511480/ZRkCb8bm57hBdjbU99VidWo9scLxlnF4a0vIPpqz3IanMLAwUHM5C4tqeaWzCpKyVhTo",         # P&L and drawdown updates
    # --- Per-symbol observability channels (event_observer.py routes here) ---
    # Paste your per-symbol webhook URLs below. Create one Discord channel per pair.
    "eurusd-sb": "https://discord.com/api/webhooks/1519344222866640988/cEiVMOawGsvHZtVS-o0nCKe8u1KnhUlmQ5kx28WBe-P4R-WY_92UJBeaU0_h7rWvFCT7",  # TODO: paste webhook URL for #eurusd-sb channel
    "gbpusd-sb": "https://discord.com/api/webhooks/1519344455260307627/4GnOI35L1EWtx-ey7AX4Y8gYig6QPWqpkw_13cJyFNApuaE8froCk8wLNp0r9Wh78s1u",  # TODO: paste webhook URL for #gbpusd-sb channel
    "usdjpy-sb": "https://discord.com/api/webhooks/1519344609094930483/F8DS4CILW6b5aCzPaQcVNjM8ZLz9smGN70BHQl9cepY3O09pLxc_tHGFQnelc8L1slsF",  # TODO: paste webhook URL for #usdjpy-sb channel
    "usdchf-sb": "https://discord.com/api/webhooks/1519345017502826678/3b-Z-8lmAwfXFlpK6PJEDq4KrTO3bQCAa-zXs2FpP8nq1rbbm0Z5Fd1kxiSnSPu8GfDA",  # TODO: paste webhook URL for #usdchf-sb channel
    "usdcad-sb": "https://discord.com/api/webhooks/1519345647566848192/xMUpATivrRziQzDMSHdlkHpn3Lj8MBD5NPxq8JLBVgt9BVN2_XPo7imoqQdcmeE9XuG8",  # TODO: paste webhook URL for #usdcad-sb channel
    "audusd-sb": "https://discord.com/api/webhooks/1519345196511531018/xQXtufpiKmm41wML5shu_n5cZ05948zX3mlIOSAAaNqLtu_yKs-17sYn78snmAoXRgk1",  # TODO: paste webhook URL for #aurusd-sb channel
    "nzdusd-sb": "https://discord.com/api/webhooks/1519345451911090481/-Owed8AM2yNF-8-DzMglkW8wz6Z3JsrSoc3qB6lTA2SC1Cv6cfsi_bDwmzUkPtFi_Tqe",  # TODO: paste webhook URL for #nzdusd-sb channel
    # --- Per-pair forensic channels (forensic_logger.py routes here) ---
    # These can share the same webhooks as the *-sb channels above,
    # or point to separate forensic-only channels. Paste URLs to enable.
    "pair-eurusd": "https://discord.com/api/webhooks/1519344222866640988/cEiVMOawGsvHZtVS-o0nCKe8u1KnhUlmQ5kx28WBe-P4R-WY_92UJBeaU0_h7rWvFCT7",  # TODO: paste webhook URL for #pair-eurusd (or reuse eurusd-sb)
    "pair-gbpusd": "https://discord.com/api/webhooks/1519344455260307627/4GnOI35L1EWtx-ey7AX4Y8gYig6QPWqpkw_13cJyFNApuaE8froCk8wLNp0r9Wh78s1u",  # TODO: paste webhook URL for #pair-gbpusd
    "pair-usdjpy": "https://discord.com/api/webhooks/1519344609094930483/F8DS4CILW6b5aCzPaQcVNjM8ZLz9smGN70BHQl9cepY3O09pLxc_tHGFQnelc8L1slsF",  # TODO: paste webhook URL for #pair-usdjpy
    "pair-usdchf": "https://discord.com/api/webhooks/1519345017502826678/3b-Z-8lmAwfXFlpK6PJEDq4KrTO3bQCAa-zXs2FpP8nq1rbbm0Z5Fd1kxiSnSPu8GfDA",  # TODO: paste webhook URL for #pair-usdchf
    "pair-usdcad": "https://discord.com/api/webhooks/1519345647566848192/xMUpATivrRziQzDMSHdlkHpn3Lj8MBD5NPxq8JLBVgt9BVN2_XPo7imoqQdcmeE9XuG8",  # TODO: paste webhook URL for #pair-usdcad
    "pair-audusd": "https://discord.com/api/webhooks/1519345196511531018/xQXtufpiKmm41wML5shu_n5cZ05948zX3mlIOSAAaNqLtu_yKs-17sYn78snmAoXRgk1",  # TODO: paste webhook URL for #pair-audusd
    "pair-nzdusd": "https://discord.com/api/webhooks/1519345451911090481/-Owed8AM2yNF-8-DzMglkW8wz6Z3JsrSoc3qB6lTA2SC1Cv6cfsi_bDwmzUkPtFi_Tqe",  # TODO: paste webhook URL for #pair-nzdusd
    # --- Research monitor channel ---
    "research_monitor-shadow-research": "https://discord.com/api/webhooks/1528561546341388432/7L6MPHnf9qZi6XfTozlGKVUX3Kee6xed34RgsPaJ2IY-7qz6kIZtmzwxxCGkWlqTvVG1",  # TODO: paste webhook URL for #research_monitor-shadow-research channel
}

# --- EngineState warm-start persistence ---
ENGINE_STATE_WARM_START_ENABLED = True       # When True, persist/restore EngineState on shutdown/startup
ENGINE_STATE_PERSIST_DIR = "logs/state"      # Directory for state snapshot files
ENGINE_STATE_MAX_AGE_SECONDS = 86400         # Reject snapshots older than this (24h default)
CHECKPOINT_INTERVAL_CYCLES = 50              # Persist EngineState every N cycles (0 = disabled)

# --- Risk coverage validation ---
STRICT_RISK_COVERAGE = False  # When True, startup fails if any pattern lacks SL/TP rules

# --- Exposure guard safety ---
STRICT_EXPOSURE_GUARDS = True  # When True, unknown exposure state blocks all trading (fail-closed)

# --- Position sizing mode ---
POSITION_SIZING_MODE = "FIXED"       # "FIXED" or "DYNAMIC" (risk-based)
RISK_PER_TRADE_PERCENT = 0.25         # Account risk % per trade (used in DYNAMIC mode only)

# --- Drawdown protection guard ---
ENABLE_DRAWDOWN_GUARD = False           # When True, blocks trades if drawdown exceeds threshold
MAX_DRAWDOWN_PERCENT = 10.0             # Maximum allowed drawdown % from equity high watermark

# --- Daily loss limit guard ---
ENABLE_DAILY_LOSS_LIMIT = True          # When True, blocks new entries when daily loss exceeds threshold
DAILY_LOSS_LIMIT_PERCENT = 4.0          # Maximum daily loss % (prop firm: 4%, retail: 8%)
DAILY_RESET_HOUR_UTC = 0                # Hour (UTC) at which daily P&L resets (0 = midnight)
DAILY_LOSS_STATE_FILE = "logs/daily_loss_state.json"  # Persistence file path

# --- Daily trade limit guard (A4) ---
DAILY_TRADE_LIMIT_ENABLED = True        # When True, blocks entries when daily trade count reached
MAX_TRADES_PER_DAY_TOTAL = 20           # Maximum total trades per day (all symbols combined)
MAX_TRADES_PER_DAY_PER_SYMBOL = 5       # Maximum trades per day per individual symbol
DAILY_TRADE_LIMIT_STATE_FILE = "logs/daily_trade_limit_state.json"  # Persistence file path

# --- Portfolio exposure guard (A5) ---
PORTFOLIO_EXPOSURE_GUARD_ENABLED = True  # When True, blocks entries when portfolio exposure limits reached
MAX_TOTAL_OPEN_POSITIONS = 3            # Maximum concurrent open positions across all symbols
MAX_TOTAL_RISK_EXPOSURE_PCT = 3.0       # Maximum aggregate portfolio risk % (sum of all position risks)

# --- Horizon execution policy ---
# Which horizons are permitted to execute live trades.
# Phase 1: Only SCALP executes. INTRADAY/EXTENDED remain shadow-only.
# Expand this list to enable higher horizons (requires validated shadow data).
PERMITTED_HORIZONS = ["SCALP", "INTRADAY", "EXTENDED"]

# Horizon Execution Authority (Phase 2)
# Controls portfolio allocation across horizons.
HORIZON_AUTHORITY_ENABLED = True         # When True, authority checks run before guard chain
HORIZON_MAX_TOTAL_POSITIONS = 21         # Portfolio hard cap (7 symbols x 3 horizons)
HORIZON_MAX_POSITIONS_PER_SYMBOL = 3     # Per-symbol cap (one per horizon)

# --- Horizon-specific trade management (Phase 3B) ---
# Each horizon has independent trade management parameters.
# SCALP uses current global TM_* values as baseline.
# INTRADAY/EXTENDED have their own rules (inactive until added to PERMITTED_HORIZONS).
HORIZON_TRADE_MANAGEMENT = {
    "SCALP": {
        "break_even_trigger_rr": 1.0,    # Move SL to BE at 1R profit (matches TM_BREAK_EVEN_TRIGGER_RR)
        "break_even_buffer_rr": 0.1,     # 0.1R beyond entry (matches TM_BREAK_EVEN_BUFFER_RR)
        "trailing_step": 0.0,            # Disabled (matches TM_TRAILING_STEP)
        "trailing_start_rr": 0.0,        # Disabled (matches TM_TRAILING_START_RR)
        "partial_tp_fraction": 0.0,      # Disabled (matches TM_PARTIAL_TP_FRACTION)
        "partial_tp_path_fraction": 0.0, # Disabled (matches TM_PARTIAL_TP_PATH_FRACTION)
        "max_time_in_trade_seconds": 0.0,  # Disabled (matches TM_MAX_TIME_IN_TRADE_SECONDS)
        # NOTE: Hard time exit for SCALP is currently disabled to preserve existing behaviour.
        # Future: set to 5400.0 (90 minutes) once validated via shadow data.
    },
    "INTRADAY": {
        "break_even_trigger_rr": 1.5,    # Move SL to BE at 1.5R profit
        "break_even_buffer_rr": 0.15,    # 0.15R beyond entry
        "trailing_step": 0.0003,         # 3 pip trailing step
        "trailing_start_rr": 2.0,        # Start trailing at 2R
        "partial_tp_fraction": 0.5,      # Close 50% at TP1
        "partial_tp_path_fraction": 0.7, # TP1 = 70% of the way to full TP
        "max_time_in_trade_seconds": 14400.0,  # 240 minutes — thesis expiry
    },
    "EXTENDED": {
        "break_even_trigger_rr": 2.0,    # Move SL to BE at 2R profit
        "break_even_buffer_rr": 0.2,     # 0.2R beyond entry
        "trailing_step": 0.0005,         # 5 pip trailing step
        "trailing_start_rr": 3.0,        # Start trailing at 3R
        "partial_tp_fraction": 0.5,      # Close 50% at TP1
        "partial_tp_path_fraction": 0.6, # TP1 = 60% of the way to full TP
        "max_time_in_trade_seconds": 43200.0,  # 720 minutes — thesis expiry
    },
}

# --- Market regime guard (I2) ---
REGIME_GUARD_ENABLED = False             # When True, blocks entries in adverse market regimes
BLOCKED_REGIMES = ["VOLATILE", "CHOPPY"]  # Regimes that block execution (uppercase strings)

# --- Process watchdog / heartbeat (E2) ---
HEARTBEAT_ENABLED = True                # When True, bot writes heartbeat file each cycle
HEARTBEAT_FILE = "runtime/heartbeat.json"  # Path to heartbeat file
WATCHDOG_POLL_INTERVAL_SECONDS = 15     # How often watchdog checks heartbeat
HEARTBEAT_STALE_THRESHOLD_SECONDS = 120  # Heartbeat age (s) before considered stale
MAX_RESTARTS_PER_HOUR = 5              # Crash-loop protection limit
BOT_START_COMMAND = ["python", "main.py"]  # Command to restart the bot

# --- Challenge progress tracker (H1) ---
CHALLENGE_MODE_ENABLED = False          # When True, enables challenge-awareness system
CHALLENGE_PROFIT_TARGET_PERCENT = 8.0   # Target profit % to pass the challenge
CHALLENGE_START_DATE = "2026-06-01"     # Challenge start date (ISO format)
CHALLENGE_END_DATE = "2026-07-01"       # Challenge end date (ISO format)
CHALLENGE_START_EQUITY = 0.0            # Starting equity at challenge begin (0 = auto-capture on first run)
CHALLENGE_CONSERVATIVE_THRESHOLD_PERCENT = 80.0  # % of target at which conservative mode activates
CHALLENGE_SIZE_REDUCTION_FACTOR = 0.50  # Position size multiplier in conservative mode
CHALLENGE_PROTECT_MODE_ENABLED = True   # When True, blocks new entries once target achieved
CHALLENGE_PROGRESS_FILE = "runtime/challenge_progress.json"  # Informational progress file

# --- Consistency rules (H2) ---
CONSISTENCY_RULES_ENABLED = False        # When True, enforces prop-firm consistency requirements
MAX_DAILY_PROFIT_PERCENT = 2.0          # Max profit % allowed in a single day before locking
MIN_TRADING_DAYS = 5                    # Minimum trading days required for challenge compliance
MAX_SINGLE_DAY_CONTRIBUTION_PERCENT = 40.0  # Max % of total profit from any single day
LOCK_AFTER_DAILY_PROFIT_CAP = True      # When True, blocks new entries after daily profit cap
CONSISTENCY_STATE_FILE = "runtime/consistency_tracker.json"  # Persistence file

# --- Prop firm rule compliance (H3) ---
PROP_FIRM_RULES_ENABLED = False          # When True, enforces prop firm contract rules pre-trade
PROP_FIRM_RULE_SET = {
    "max_daily_loss_percent": 5.0,
    "max_total_drawdown_percent": 10.0,
    "max_position_hold_minutes": None,
    "blocked_trading_hours": [(22, 24)],
    "max_trades_per_day": 20,
    "max_lot_size": 1.0,
    "allow_weekend_holding": False,
    "allow_news_trading": False,
}

# --- Weekend position protection (H4) ---
FLATTEN_BEFORE_WEEKEND = True           # When True, closes all positions before Friday close
FRIDAY_FLATTEN_HOUR_UTC = 20            # Hour (UTC) to flatten positions on Friday
BLOCK_NEW_TRADES_BEFORE_WEEKEND = True  # When True, blocks new entries after flatten hour
WEEKEND_STATE_FILE = "runtime/weekend_state.json"  # Persistence file

# --- Slippage monitoring (C3) ---
SLIPPAGE_MONITORING_ENABLED = True      # When True, records execution slippage per trade
SLIPPAGE_ALERT_THRESHOLD_PIPS = 0.5     # Alert when mean slippage exceeds this (pips)
SLIPPAGE_JOURNAL_FILE = "runtime/slippage_journal.jsonl"  # JSONL audit trail
SLIPPAGE_MAX_HISTORY = 500              # Max records in rolling stats window

# --- Position ownership validation (B4) ---
STRICT_POSITION_OWNERSHIP = True        # When True, blocks modification of foreign positions

# --- Closed position eviction (B5) ---
POSITION_EVICTION_ENABLED = True        # When True, removes closed positions from memory after delay
POSITION_EVICTION_DELAY_SECONDS = 3600  # Seconds after close before eviction (1 hour)
POSITION_EVICTION_CHECK_INTERVAL = 300  # Seconds between eviction sweeps (5 minutes)

# --- Equity curve tracking (I4) ---
EQUITY_CURVE_ENABLED = True             # When True, records daily equity snapshots
EQUITY_CURVE_FILE = "runtime/equity_curve.jsonl"  # Append-only curve file
SHARPE_DECAY_THRESHOLD = 0.5            # Alert when 30-day Sharpe drops below this

# --- Dashboard performance metrics (F4) ---
DASHBOARD_INCLUDE_PNL_METRICS = True    # When True, dashboard includes P&L performance
DASHBOARD_EMIT_DAILY_SUMMARY = True     # When True, emits daily performance summary

# --- Spread guard (hard pre-execution block) ---
SPREAD_GUARD_ENABLED = True             # When True, blocks trades when spread is unsafe
MAX_SPREAD_ATR_RATIO = 0.30            # Block if spread / risk_distance > this ratio
MAX_SPREAD_ABSOLUTE_DEFAULT = 0.0005    # Global fallback: block if spread > this (price units)

# --- Minimum stop-loss distance guard (rejects structurally impossible stops) ---
MIN_SL_GUARD_ENABLED = True             # When True, rejects trades with SL below adaptive minimum
ADAPTIVE_MIN_SL_ENABLED = True          # When True, uses ATR + spread adaptive floor. When False, uses fixed MIN_SL_PIPS.
MIN_SL_ABSOLUTE_FLOOR_PIPS = 3.0       # Hard minimum SL regardless of conditions (safety net)
ATR_SL_MULTIPLIER = 1.0                # ATR(14) × this = market-noise minimum SL (in pips)
SPREAD_SL_MULTIPLIER = 2.0             # Current spread × this = spread-derived minimum SL (in pips)
MIN_SL_PIPS = {                         # Per-symbol FIXED minimum (used when ADAPTIVE_MIN_SL_ENABLED=False)
    "EURUSD": 5.0,
    "GBPUSD": 5.0,
    "USDJPY": 5.0,
    "USDCHF": 5.0,
    "USDCAD": 5.0,
    "AUDUSD": 5.0,
    "NZDUSD": 5.0,
    "NAS100": 10.0,             # ~10 points minimum stop for NAS100
    "US500": 30.0,              # ~3.0 price units (in 0.1-point pips)
    "XAUUSD": 50.0,             # ~0.50 price units (in 0.01-point pips)
}
MIN_SL_PIPS_DEFAULT = 5.0              # Fallback for symbols not in the dict above

# --- Session guard (hard trading hours gate) ---
SESSION_GUARD_ENABLED = False            # When True, blocks entries outside trading hours
TRADING_HOURS_START_UTC = 7             # Earliest hour (UTC) for new entries
TRADING_HOURS_END_UTC = 21              # Latest hour (UTC) for new entries (exclusive)
BLOCK_FRIDAY_AFTER_HOUR = 20            # Block entries after this hour on Friday (UTC)
BLOCK_SUNDAY_BEFORE_HOUR = 22           # Block entries before this hour on Sunday (UTC)
MAX_SPREAD_ABSOLUTE = {                 # Per-symbol absolute spread caps (price units)
    "EURUSD": 0.00030,
    "GBPUSD": 0.00040,
    "USDJPY": 0.040,
    "USDCHF": 0.00040,
    "USDCAD": 0.00040,
    "AUDUSD": 0.00030,
    "NZDUSD": 0.00040,
    "NAS100": 3.0,              # ~1.5 points typical, cap at 3
    "US500": 1.0,               # ~0.4 points typical, cap at 1
    "XAUUSD": 0.50,             # ~0.20 typical, cap at 0.50
}

# --- Candle replay cache ---
ENABLE_CANDLE_REPLAY_CACHE = True      # When True, persist fetched candles to disk for replay
REPLAY_CACHE_DIR = "replay_data"        # Output directory for cached candle data

# --- Unified event stream ---
EVENT_STREAM_DIR = "events"             # Output directory for unified event bus JSONL
# (EVENT_STREAM_S3_MIRROR is in RUNTIME CONTROL SWITCHES section above)

# --- Adapter layer ---
ADAPTER_MODE = True                     # When True, all legacy external sinks are no-ops (only event_stream writes to S3)

# --- Multi-Timeframe Authority System ---
MTF_ENABLED = True                          # Master switch — False = entire MTF system inert
MTF_SHADOW_MODE = True                      # Shadow mode — run dual pipeline, compare, but execute baseline only
MTF_H4_ENABLED = True                        # Enable H4 regime analysis
MTF_H4_CANDLE_COUNT = 100                    # H4 bars to fetch (17 days of data)
MTF_H1_ENABLED = True                        # Enable H1 bias analysis
MTF_H1_CANDLE_COUNT = 200                    # H1 bars to fetch (8 days of data)
MTF_M15_ENABLED = True                       # Enable M15 structure analysis
MTF_M15_CANDLE_COUNT = 200                   # M15 bars to fetch (2 days of data)
MTF_M1_ENABLED = False                       # Optional M1 refinement layer (disabled by default)
MTF_M1_CANDLE_COUNT = 60                     # M1 bars to fetch (1 hour of data)

# Scoring influence
MTF_H4_RANGING_SCORE_PENALTY = 1.0           # Subtracted from score when H4=RANGING
MTF_H4_VOLATILE_MIN_SCORE_INCREASE = 1.0     # Added to min_score when H4=VOLATILE
MTF_H1_ALIGNED_BONUS = 0.5                   # Added to score when H1 bias aligns with signal
MTF_H1_NEUTRAL_MIN_SCORE_INCREASE = 0.5      # Added to min_score when H1=NEUTRAL
MTF_H1_CONTRADICTION_THRESHOLD = 7.0         # Score must exceed this to override H1 contradiction
MTF_M15_MIN_STRUCTURE_QUALITY = 0.3          # Below this = structural block
MTF_M15_HIGH_QUALITY_THRESHOLD = 0.7         # Above this = quality bonus applied
MTF_M15_HIGH_QUALITY_BONUS = 0.5             # Added to score when M15 quality is high
