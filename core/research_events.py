"""
Research Events — Structured persistence for research-observable bot state.

This module provides fire-and-forget event persistence for bot surfaces
that were previously unobservable by the Research Engine:
    - Guard decisions (cooldown, correlation, position limits)
    - Recovery/restart events
    - Configuration snapshots

CONTRACT:
    - NEVER affects trading decisions
    - NEVER blocks execution
    - NEVER raises exceptions to callers
    - NEVER modifies production state
    - Append-only JSONL persistence
    - Used by Research Engine detectors downstream

Persistence location: logs/research_events/{date}.jsonl
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_EVENT_DIR = Path("logs/research_events")


# ═══════════════════════════════════════════════════════════════════════════════
# MATERIAL CONFIGURATION COVERAGE (Hardening 1.3)
# ═══════════════════════════════════════════════════════════════════════════════
# DECLARED, REVIEWABLE allowlist of the effective configuration values whose
# change can alter the trading/research behaviour a baseline represents. This
# tuple is the single source of truth for config-identity coverage: the payload
# is derived from it, and tests assert the payload matches it exactly.
#
# Classification rule applied (evidence-based — every entry below has a
# production code path that consumes it to decide entry/exit/size/universe or
# to block an entry):
#   MATERIAL  → published here.
#   NON-MATERIAL (paths, AWS/S3, credentials, logging, presentation, Discord,
#   persistence locations, watchdog/heartbeat, MT5 terminal plumbing, stale-feed
#   telemetry, monitoring/alerting, shadow-data routing) → deliberately NOT
#   published. See tests/test_hardening13_config_hash_material_coverage.py
#   (_NON_MATERIAL_CONFIG_REGISTRY) for the full classified inventory.
#   INERT (present in core/config.py but consumed nowhere in production) →
#   deliberately NOT published, and registered explicitly in the same test
#   inventory so a future wiring change fails loudly.
#
# Values are read at CALL time via getattr(core.config, ...) so profile /
# environment overrides applied at startup (core/config_profile_loader.py) are
# reflected — the payload is the EFFECTIVE configuration, not the file literal.
#
# Historical hashes are never recomputed or rewritten: a widening of this
# coverage only affects NEWLY computed hashes (existing persisted baselines keep
# their stored config_hash verbatim). Consumers compare stored vs current and
# fail closed on mismatch (see the Wave 4C.1 staleness gates).

# ── A. Pre-existing material coverage (keys unchanged, verbatim) ─────────────
_MATERIAL_CONFIG_PARAMS: tuple[str, ...] = (
    # Engine / execution mode
    "ENGINE_MODE",
    "DRY_RUN",
    "EXECUTION_ENABLED",
    # Decision cadence / cooldown
    "COOLDOWN_SECONDS",
    "COOLDOWN_AFTER_LOSS_SECONDS",
    # Portfolio exposure
    "MAX_TOTAL_OPEN_POSITIONS",
    "MAX_TOTAL_RISK_EXPOSURE_PCT",
    "MAX_CURRENCY_EXPOSURE_LOTS",
    "MAX_CORRELATION_GROUP_POSITIONS",
    # Trade limits
    "DAILY_TRADE_LIMIT_ENABLED",
    "MAX_TRADES_PER_DAY_TOTAL",
    # Scoring / entry threshold
    "MIN_SCORE_TO_TRADE",
    # Sizing / reward:risk
    "FIXED_LOT",
    "RISK_PER_TRADE_PERCENT",
    "MIN_RR",
    "BASE_RR",
    # Spread guard
    "SPREAD_GUARD_ENABLED",
    "MAX_SPREAD_ATR_RATIO",
    # ── B. Runtime-mode switches of the same class as A (main.py routing) ────
    # Change which runtime/loop and which engine path actually executes:
    #   REPLAY_MODE                    → replay vs live loop (main.py:232-253)
    #   MULTI_SYMBOL_SCANNER_ENABLED   → guarded scanner vs legacy per-symbol loop
    #                                    (main.py:185,232-253)
    "REPLAY_MODE",
    "MULTI_SYMBOL_SCANNER_ENABLED",
    # ── C. Traded universe + strategy bar series ─────────────────────────────
    #   CANONICAL_SYMBOLS / TIMEFRAME / CANDLE_COUNT → which instruments and which
    #   bar series the strategy is evaluated on (main.py:178-194, CANDLE_COUNT and
    #   TIMEFRAME feed the bar provider: core/runtime/bar_provider.py:102,109).
    "CANONICAL_SYMBOLS",
    "TIMEFRAME",
    "CANDLE_COUNT",
    # ── D. Signal/decision gates and scoring inputs ──────────────────────────
    #   TREND_FILTER_ENABLED / TREND_EMA_PERIOD      core/pipeline/trade_quality.py:102-105
    #   CHOP_FILTER_ENABLED / MARKET_FILTER_LOOKBACK / MIN_SUM_RANGE_5BARS /
    #   CHOP_NET_MOVE_RATIO / CHOP_OVERLAP_RATIO_MAX core/pipeline/trade_quality.py:41-50
    #   SETUP_MA_PERIOD / SETUP_MIN_DISTANCE_FROM_MA core/pipeline/strategy_detection.py:40-41
    #   BIAS_*                                        core/pipeline/strategy_detection.py:47-53,
    #                                                 core/pipeline/scoring_inputs.py:46-47
    #   SL_BUFFER / RR3_PATTERNS                      core/runtime/runtime_utils.py:58-59
    #   MAX_OPEN_POSITIONS                            core/pipeline/trade_quality.py:154
    #   ENABLE_EV_GATE                                core/pipeline/new_engine.py:651-674
    "TREND_FILTER_ENABLED",
    "TREND_EMA_PERIOD",
    "CHOP_FILTER_ENABLED",
    "MARKET_FILTER_LOOKBACK",
    "MIN_SUM_RANGE_5BARS",
    "CHOP_NET_MOVE_RATIO",
    "CHOP_OVERLAP_RATIO_MAX",
    "SETUP_MA_PERIOD",
    "SETUP_MIN_DISTANCE_FROM_MA",
    "BIAS_CONFLUENCE_THRESHOLD",
    "BIAS_CONFIRMATION_CANDLES",
    "BIAS_LOCK_CANDLES",
    "BIAS_LOCK_SECONDS",
    "BIAS_EXPIRY_SECONDS",
    "BIAS_OPPOSITE_STRENGTH_THRESHOLD",
    "SL_BUFFER",
    "RR3_PATTERNS",
    "MAX_OPEN_POSITIONS",
    "ENABLE_EV_GATE",
    # ── E. Position sizing + stop/spread geometry guards ─────────────────────
    #   POSITION_SIZING_MODE / RISK_PER_TRADE_PERCENT (A) risk/manager.py:385-409
    #   MIN_SL_GUARD_ENABLED / ADAPTIVE_MIN_SL_ENABLED / MIN_SL_ABSOLUTE_FLOOR_PIPS /
    #   ATR_SL_MULTIPLIER / SPREAD_SL_MULTIPLIER / MIN_SL_PIPS / MIN_SL_PIPS_DEFAULT
    #                                  risk/manager.py:93-136,417-446
    #   MAX_SPREAD_ABSOLUTE(_DEFAULT)  risk/spread_guard.py:33-42
    "POSITION_SIZING_MODE",
    "MIN_SL_GUARD_ENABLED",
    "ADAPTIVE_MIN_SL_ENABLED",
    "MIN_SL_ABSOLUTE_FLOOR_PIPS",
    "ATR_SL_MULTIPLIER",
    "SPREAD_SL_MULTIPLIER",
    "MIN_SL_PIPS",
    "MIN_SL_PIPS_DEFAULT",
    "MAX_SPREAD_ABSOLUTE",
    "MAX_SPREAD_ABSOLUTE_DEFAULT",
    # ── F. Exposure / drawdown / daily-limit guards ──────────────────────────
    #   PORTFOLIO_EXPOSURE_GUARD_ENABLED / STRICT_EXPOSURE_GUARDS
    #                                  risk/portfolio_exposure_guard.py:33-38,
    #                                  risk/guards.py:34-57
    #   CORRELATION_GUARD_ENABLED / CORRELATION_GROUPS
    #                                  risk/correlation_guard.py:44-58
    #   ENABLE_DAILY_LOSS_LIMIT / DAILY_LOSS_LIMIT_PERCENT / DAILY_RESET_HOUR_UTC
    #                                  risk/daily_loss_guard.py:39-48
    #   ENABLE_DRAWDOWN_GUARD / MAX_DRAWDOWN_PERCENT
    #                                  core/runtime/cycle_guards.py:112
    #   MAX_TRADES_PER_DAY_PER_SYMBOL  risk/daily_trade_limit.py:61-66
    "PORTFOLIO_EXPOSURE_GUARD_ENABLED",
    "STRICT_EXPOSURE_GUARDS",
    "CORRELATION_GUARD_ENABLED",
    "CORRELATION_GROUPS",
    "ENABLE_DAILY_LOSS_LIMIT",
    "DAILY_LOSS_LIMIT_PERCENT",
    "DAILY_RESET_HOUR_UTC",
    "ENABLE_DRAWDOWN_GUARD",
    "MAX_DRAWDOWN_PERCENT",
    "MAX_TRADES_PER_DAY_PER_SYMBOL",
    # ── G. Session / regime / weekend gates ─────────────────────────────────
    #   SESSION_GUARD_ENABLED / TRADING_HOURS_START_UTC / TRADING_HOURS_END_UTC /
    #   BLOCK_FRIDAY_AFTER_HOUR / BLOCK_SUNDAY_BEFORE_HOUR
    #                                  risk/session_guard.py:24-61,90
    #   REGIME_GUARD_ENABLED / BLOCKED_REGIMES risk/regime_guard.py:29-45,217-225
    #   FLATTEN_BEFORE_WEEKEND / FRIDAY_FLATTEN_HOUR_UTC /
    #   BLOCK_NEW_TRADES_BEFORE_WEEKEND  core/weekend_protection.py:36-52,124,
    #                                  risk/runtime_guard_chain.py:279-288
    "SESSION_GUARD_ENABLED",
    "TRADING_HOURS_START_UTC",
    "TRADING_HOURS_END_UTC",
    "BLOCK_FRIDAY_AFTER_HOUR",
    "BLOCK_SUNDAY_BEFORE_HOUR",
    "REGIME_GUARD_ENABLED",
    "BLOCKED_REGIMES",
    "FLATTEN_BEFORE_WEEKEND",
    "FRIDAY_FLATTEN_HOUR_UTC",
    "BLOCK_NEW_TRADES_BEFORE_WEEKEND",
    # ── H. Account-policy gates enforced on entries (promoted to material) ───
    #   Consumed by risk/runtime_guard_chain.py:227-276 (invoked from the live
    #   scanner at core/runtime/live_scanner.py:1603) and can BLOCK an entry:
    #   core/prop_firm_rules.py:84,93 · core/challenge_progress_tracker.py:36-84,
    #   :320 · core/consistency_rules.py:33-65,342
    #   NOTE: these are the ENABLE FLAGS + numeric thresholds only. Account /
    #   broker / deployment identity (terminal path, account ids, magic numbers,
    #   challenge calendar dates, starting equity, prop-firm credentials) stays
    #   OUT of the config hash under the existing authority model — the snapshot
    #   carries broker/platform/magic separately in `environment`.
    "PROP_FIRM_RULES_ENABLED",
    "PROP_FIRM_RULE_SET",
    "CHALLENGE_MODE_ENABLED",
    "CHALLENGE_PROFIT_TARGET_PERCENT",
    "CHALLENGE_CONSERVATIVE_THRESHOLD_PERCENT",
    "CHALLENGE_SIZE_REDUCTION_FACTOR",
    "CHALLENGE_PROTECT_MODE_ENABLED",
    "CONSISTENCY_RULES_ENABLED",
    "MAX_DAILY_PROFIT_PERCENT",
    "MAX_SINGLE_DAY_CONTRIBUTION_PERCENT",
    "LOCK_AFTER_DAILY_PROFIT_CAP",
    # ── I. Horizon execution authority ───────────────────────────────────────
    #   core/horizon/execution_authority.py:105-122 (permitted horizons + caps on
    #   open exposures) · core/horizon/horizon_manager.py:71-88
    #   (per-horizon trade-management policy)
    "HORIZON_AUTHORITY_ENABLED",
    "PERMITTED_HORIZONS",
    "HORIZON_MAX_TOTAL_POSITIONS",
    "HORIZON_MAX_POSITIONS_PER_SYMBOL",
    "HORIZON_TRADE_MANAGEMENT",
    # ── J. MTF/HTF context feeding the 10-factor score ───────────────────────
    #   MTF_ENABLED gates TimeframeCache creation (core/runtime/scanner_init.py:163-167);
    #   the resulting HTF context is consumed by the scoring components
    #   (core/pipeline/new_engine.py:730-745,977-1041) — absent context scores
    #   NEUTRAL, so the node enable flags + candle counts change scores.
    "MTF_ENABLED",
    "MTF_H4_ENABLED",
    "MTF_H4_CANDLE_COUNT",
    "MTF_H1_ENABLED",
    "MTF_H1_CANDLE_COUNT",
    "MTF_M15_ENABLED",
    "MTF_M15_CANDLE_COUNT",
    "MTF_M1_ENABLED",
    "MTF_M1_CANDLE_COUNT",
    #   MARKET_CONTEXT_ENABLED: when True the engine's strategy activation reads
    #   regime/BOS authority from H4/H1 market context instead of the M5
    #   classifier (core/pipeline/new_engine.py:142-196,318-319) — a master
    #   switch over which signal source the strategy uses. (The Phase-3 SCORING
    #   flag is separately inert — see the non-material registry.)
    "MARKET_CONTEXT_ENABLED",
    # ── K. Trade management / position lifecycle ─────────────────────────────
    #   TRADE_MANAGEMENT_ENABLED  core/runtime/scanner_init.py:155-161
    #   POSITION_CLOSE_ENABLED    execution/mt5_execution.py:974
    #   STRICT_POSITION_OWNERSHIP core/position_ownership.py:36 (used by
    #                             core/trade_management/manager.py:18)
    #   TM_*                      core/runtime/runtime_utils.py:42-51 →
    #                             TradeStateManager (break-even/trailing/partial
    #                             TP/time-stop geometry change trade outcomes)
    "TRADE_MANAGEMENT_ENABLED",
    "POSITION_CLOSE_ENABLED",
    "STRICT_POSITION_OWNERSHIP",
    "TM_BREAK_EVEN_TRIGGER_RR",
    "TM_BREAK_EVEN_BUFFER_RR",
    "TM_TRAILING_STEP",
    "TM_TRAILING_START_RR",
    "TM_PARTIAL_TP_FRACTION",
    "TM_PARTIAL_TP_PATH_FRACTION",
    "TM_MAX_TIME_IN_TRADE_SECONDS",
)


def _canonicalise_material_value(value: Any) -> Any:
    """
    Canonicalise a configuration value for deterministic hashing.

    Only one transformation is applied: unordered containers (set/frozenset)
    are converted to a SORTED list, because their iteration order is not stable
    across processes (string hash randomisation). Everything else is returned
    unchanged — dict key ordering is already canonicalised by
    ``json.dumps(..., sort_keys=True)`` and JSON list order is the authored,
    semantically meaningful order.
    """
    if isinstance(value, (set, frozenset)):
        return sorted(_canonicalise_material_value(v) for v in value)
    if isinstance(value, dict):
        return {
            str(k): _canonicalise_material_value(v)
            for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_canonicalise_material_value(v) for v in value]
    return value


def _get_material_params() -> dict[str, Any]:
    """
    Extract the effective material trading parameters from config.

    Coverage is DECLARED by ``_MATERIAL_CONFIG_PARAMS`` (Hardening 1.3) — not
    enumerated ad hoc here. Values are read at call time so startup profile /
    environment overrides are reflected.

    A configuration attribute that is absent from the module hashes as ``None``
    (uniform "value unavailable" sentinel) rather than an invented per-key
    default that could silently diverge from the runtime's own fallback.

    Never raises: if the config module cannot be imported at all the payload is
    empty (callers treat the resulting hash as "UNKNOWN"/non-comparable).
    """
    try:
        from core import config
        return {
            name: _canonicalise_material_value(getattr(config, name, None))
            for name in _MATERIAL_CONFIG_PARAMS
        }
    except Exception:
        return {}






def persist_guard_event(
    *,
    symbol: str,
    cycle_id: int,
    correlation_id: str,
    guard_name: str,
    allowed: bool,
    reason: str,
    metadata: dict[str, Any] | None = None,
    direction: str = "",
    pattern: str = "",
) -> None:
    """
    Persist a runtime guard evaluation result for research.

    Called after evaluate_runtime_guards() regardless of outcome.
    Captures both ALLOWED and BLOCKED decisions.
    """
    try:
        event = {
            "event_type": "GUARD_DECISION",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol,
            "cycle_id": cycle_id,
            "correlation_id": correlation_id,
            "guard_name": guard_name,
            "allowed": allowed,
            "reason": reason,
            "direction": direction,
            "pattern": pattern,
            **(metadata or {}),
        }
        _append_event(event)
    except Exception:
        pass  # Must NEVER affect trading


def persist_recovery_event(
    *,
    symbol: str,
    recovered_count: int,
    broker_total: int,
    positions: list[dict[str, Any]] | None = None,
    identity_restored: int = 0,
    identity_failed: int = 0,
    protection_missing: int = 0,
    error: str = "",
) -> None:
    """Persist a startup recovery event for research."""
    try:
        event = {
            "event_type": "RECOVERY",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol,
            "recovered_count": recovered_count,
            "broker_total": broker_total,
            "identity_restored": identity_restored,
            "identity_failed": identity_failed,
            "protection_missing": protection_missing,
            "error": error,
            "positions": positions or [],
        }
        _append_event(event)
    except Exception:
        pass


def persist_config_snapshot(*, correlation_id: str = "", cycle_id: int = 0) -> str:
    """
    Persist a configuration fingerprint for research version attribution.

    Returns the config hash (or "" on failure).
    """
    try:
        config_hash = compute_config_hash()
        event = {
            "event_type": "CONFIG_SNAPSHOT",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "config_hash": config_hash,
            "correlation_id": correlation_id,
            "cycle_id": cycle_id,
            "material_params": _get_material_params(),
        }
        _append_event(event)
        return config_hash
    except Exception:
        return ""


def compute_config_hash() -> str:
    """
    Compute a deterministic hash of the effective material trading config.

    Coverage is DECLARED by ``_MATERIAL_CONFIG_PARAMS`` (Hardening 1.3):
    every value that can change the trading/research behaviour a baseline
    represents. Serialization is canonical (sorted keys; sets sorted to lists),
    so identical material configuration → identical hash, across processes.

    Only newly computed hashes use this coverage: persisted baselines are NEVER
    rewritten, and historical stored hashes are compared verbatim (mismatch
    fails closed in the baseline staleness gates — it is NOT re-hashed).
    """
    try:
        params = _get_material_params()
        content = json.dumps(params, sort_keys=True, default=str)
        return hashlib.sha256(content.encode()).hexdigest()[:16]
    except Exception:
        return "UNKNOWN"


def _append_event(event: dict[str, Any]) -> None:
    """Append a single event to the daily JSONL file. Never raises."""
    try:
        _EVENT_DIR.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        path = _EVENT_DIR / f"{date_str}.jsonl"
        line = json.dumps(event, separators=(",", ":"), default=str) + "\n"
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)
    except Exception:
        pass
