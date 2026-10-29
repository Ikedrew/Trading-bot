"""
H1: Challenge Progress Tracker — Prop firm challenge awareness system.

Tracks challenge progress toward profit target and automatically:
- Becomes more conservative as target approaches (reduce position size)
- Stops opening new trades once target is achieved (protect mode)
- Produces auditable progress logs

Design:
- Challenge state is ALWAYS recomputed from live account data
- Persistence file is informational only (never trusted as source of truth)
- Conservative mode reduces risk sizing, not strategy scoring
- Protect mode is a HARD BLOCK on new entries
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time as _time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ─── CONFIGURATION ───────────────────────────────────────────────────────────


def _is_enabled() -> bool:
    try:
        from core import config
        return bool(getattr(config, "CHALLENGE_MODE_ENABLED", False))
    except ImportError:
        return False


def _get_profit_target() -> float:
    try:
        from core import config
        return float(getattr(config, "CHALLENGE_PROFIT_TARGET_PERCENT", 8.0))
    except ImportError:
        return 8.0


def _get_start_date() -> str:
    try:
        from core import config
        return str(getattr(config, "CHALLENGE_START_DATE", ""))
    except ImportError:
        return ""


def _get_end_date() -> str:
    try:
        from core import config
        return str(getattr(config, "CHALLENGE_END_DATE", ""))
    except ImportError:
        return ""


def _get_conservative_threshold() -> float:
    try:
        from core import config
        return float(getattr(config, "CHALLENGE_CONSERVATIVE_THRESHOLD_PERCENT", 80.0))
    except ImportError:
        return 80.0


def _get_size_reduction_factor() -> float:
    try:
        from core import config
        return float(getattr(config, "CHALLENGE_SIZE_REDUCTION_FACTOR", 0.50))
    except ImportError:
        return 0.50


def _get_protect_enabled() -> bool:
    try:
        from core import config
        return bool(getattr(config, "CHALLENGE_PROTECT_MODE_ENABLED", True))
    except ImportError:
        return True


def _get_start_equity() -> float:
    """Get configured challenge starting equity. 0 = auto-capture."""
    try:
        from core import config
        return float(getattr(config, "CHALLENGE_START_EQUITY", 0.0))
    except ImportError:
        return 0.0


def _get_persistence_path() -> Path:
    try:
        from core import config
        return Path(getattr(config, "CHALLENGE_PROGRESS_FILE", "runtime/challenge_progress.json"))
    except ImportError:
        return Path("runtime/challenge_progress.json")


# ─── RESULT TYPES ─────────────────────────────────────────────────────────────

REJECT_CHALLENGE_TARGET_ACHIEVED = "CHALLENGE_TARGET_ACHIEVED"


@dataclass(frozen=True)
class ChallengeProgress:
    """Immutable snapshot of challenge progress state."""
    current_profit_percent: float
    target_percent: float
    progress_percent: float
    days_elapsed: int
    days_remaining: int
    conservative_mode: bool
    protect_mode: bool


@dataclass(frozen=True)
class ChallengeGuardResult:
    """Result of challenge guard evaluation."""
    allowed: bool
    reason: str = ""
    current_profit_percent: float = 0.0
    target_percent: float = 0.0
    progress_percent: float = 0.0


# ─── EQUITY-BASED PROFIT CALCULATION ──────────────────────────────────────────

# Module-level cached start equity (set once, persists across calls within session)
_cached_start_equity: float | None = None

_BASELINE_FILE = "runtime/challenge_baseline.json"


def _get_baseline_path() -> Path:
    return Path(_BASELINE_FILE)


def _fetch_current_equity() -> float | None:
    """Fetch current account equity from MT5. Returns None on failure."""
    try:
        import MetaTrader5 as mt5
        from core.mt5_timeout import mt5_call

        info = mt5_call(mt5.account_info)
        if info is None:
            return None
        return float(info.equity)
    except Exception:
        return None


def _resolve_start_equity() -> float | None:
    """
    Resolve the challenge starting equity.

    Priority:
    1. Module-level cache (fastest, within session)
    2. Config CHALLENGE_START_EQUITY (if > 0, explicit user setting)
    3. Persisted baseline file (survives restarts)
    4. Auto-capture from current equity (first run only, then persists)

    Returns None if equity cannot be determined.
    """
    global _cached_start_equity

    # 1. Module cache
    if _cached_start_equity is not None and _cached_start_equity > 0:
        return _cached_start_equity

    # 2. Explicit config
    config_equity = _get_start_equity()
    if config_equity > 0:
        _cached_start_equity = config_equity
        return _cached_start_equity

    # 3. Persisted baseline
    baseline_path = _get_baseline_path()
    try:
        if baseline_path.exists():
            with open(baseline_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            stored = float(data.get("start_equity", 0))
            if stored > 0:
                _cached_start_equity = stored
                logger.info(
                    "[CHALLENGE_BASELINE] loaded from disk start_equity=%.2f",
                    stored,
                )
                return _cached_start_equity
    except Exception as exc:
        logger.warning("[CHALLENGE_BASELINE] load_error=%s", exc)

    # 4. Auto-capture from current equity
    current = _fetch_current_equity()
    if current is not None and current > 0:
        _cached_start_equity = current
        _persist_baseline(current)
        logger.info(
            "[CHALLENGE_BASELINE] auto-captured start_equity=%.2f",
            current,
        )
        return _cached_start_equity

    return None


def _persist_baseline(equity: float) -> bool:
    """Persist challenge starting equity to disk. Atomic write."""
    try:
        path = _get_baseline_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "start_equity": equity,
            "captured_at": datetime.now(tz=timezone.utc).isoformat(),
        }
        json_bytes = json.dumps(data, indent=2).encode("utf-8")
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp", prefix="cb_")
        try:
            os.write(fd, json_bytes)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, str(path))
        return True
    except Exception as exc:
        logger.warning("[CHALLENGE_BASELINE] persist_error=%s", exc)
        return False


def compute_challenge_profit_percent() -> float | None:
    """
    Compute current challenge profit percentage from broker equity.

    Formula:
        profit_pct = (current_equity - start_equity) / start_equity * 100

    Uses EQUITY (includes floating P&L from open positions).
    This aligns with most prop firm evaluation methodologies.

    Returns None if equity data is unavailable.
    """
    start_equity = _resolve_start_equity()
    if start_equity is None or start_equity <= 0:
        return None

    current_equity = _fetch_current_equity()
    if current_equity is None:
        return None

    profit_pct = ((current_equity - start_equity) / start_equity) * 100.0
    return round(profit_pct, 4)


# ─── CORE API ─────────────────────────────────────────────────────────────────

def evaluate_challenge_progress(
    *,
    current_profit_percent: float | None = None,
    reference_date: date | None = None,
) -> ChallengeProgress:
    """
    Evaluate current challenge progress.

    This is the single source of truth for challenge state.
    Always recomputes from live data — never trusts persistence.

    Args:
        current_profit_percent: Current profit % since challenge start.
            If None, fetches from MT5 account (requires starting equity).
        reference_date: Date for time calculations (default: today UTC).

    Returns:
        ChallengeProgress snapshot.
    """
    target = _get_profit_target()
    conservative_threshold = _get_conservative_threshold()

    # Profit calculation — use provided value or compute from broker equity
    if current_profit_percent is not None:
        profit_pct = current_profit_percent
    else:
        computed = compute_challenge_profit_percent()
        profit_pct = computed if computed is not None else 0.0

    # Progress
    if target > 0:
        progress = (profit_pct / target) * 100.0
    else:
        progress = 0.0
    progress = round(max(0.0, progress), 2)

    # Time tracking
    ref_date = reference_date or date.today()
    days_elapsed, days_remaining = _compute_time(ref_date)

    # Conservative mode
    conservative = progress >= conservative_threshold

    # Protect mode
    protect = _get_protect_enabled() and profit_pct >= target

    return ChallengeProgress(
        current_profit_percent=round(profit_pct, 4),
        target_percent=target,
        progress_percent=progress,
        days_elapsed=days_elapsed,
        days_remaining=days_remaining,
        conservative_mode=conservative,
        protect_mode=protect,
    )


def check_challenge_gate(
    *,
    current_profit_percent: float | None = None,
    reference_date: date | None = None,
) -> ChallengeGuardResult:
    """
    Hard execution gate — blocks new entries when challenge target achieved.

    Must be called BEFORE execution.place_market().

    Returns:
        ChallengeGuardResult with allowed=False if protect mode active.
    """
    if not _is_enabled():
        return ChallengeGuardResult(allowed=True, reason="CHALLENGE_MODE_DISABLED")

    progress = evaluate_challenge_progress(
        current_profit_percent=current_profit_percent,
        reference_date=reference_date,
    )

    if progress.protect_mode:
        logger.warning(
            "[CHALLENGE_TARGET_ACHIEVED] Profit: %.2f%% Target: %.2f%% "
            "New trading disabled.",
            progress.current_profit_percent, progress.target_percent,
        )
        return ChallengeGuardResult(
            allowed=False,
            reason=REJECT_CHALLENGE_TARGET_ACHIEVED,
            current_profit_percent=progress.current_profit_percent,
            target_percent=progress.target_percent,
            progress_percent=progress.progress_percent,
        )

    return ChallengeGuardResult(
        allowed=True,
        reason="",
        current_profit_percent=progress.current_profit_percent,
        target_percent=progress.target_percent,
        progress_percent=progress.progress_percent,
    )


def get_effective_risk_percent(base_risk_percent: float) -> float:
    """
    Get the effective risk per trade, applying challenge size reduction.

    Call this BEFORE position sizing to get the adjusted risk.

    Args:
        base_risk_percent: Normal risk per trade (e.g. 1.0%)

    Returns:
        Adjusted risk percent (reduced in conservative mode).
    """
    if not _is_enabled():
        return base_risk_percent

    progress = evaluate_challenge_progress()

    if progress.conservative_mode:
        factor = _get_size_reduction_factor()
        reduced = base_risk_percent * factor
        logger.info(
            "[CHALLENGE_CONSERVATIVE] risk_reduction base=%.2f%% effective=%.2f%% "
            "factor=%.2f progress=%.1f%%",
            base_risk_percent, reduced, factor, progress.progress_percent,
        )
        return reduced

    return base_risk_percent


# ─── TIME CALCULATION ─────────────────────────────────────────────────────────

def _compute_time(ref_date: date) -> tuple[int, int]:
    """
    Compute days elapsed and remaining.

    Returns (days_elapsed, days_remaining).
    If dates are invalid, returns (0, 0).
    """
    start_str = _get_start_date()
    end_str = _get_end_date()

    if not start_str or not end_str:
        return 0, 0

    try:
        start = date.fromisoformat(start_str)
        end = date.fromisoformat(end_str)
    except (ValueError, TypeError):
        return 0, 0

    if end <= start:
        return 0, 0

    elapsed = max(0, (ref_date - start).days)
    remaining = max(0, (end - ref_date).days)

    return elapsed, remaining


# ─── PERSISTENCE (INFORMATIONAL ONLY) ─────────────────────────────────────────

def persist_progress(progress: ChallengeProgress) -> bool:
    """
    Persist challenge progress snapshot. Informational only.

    This is NOT the source of truth — progress is always recomputed
    from live account data. This file is for observability and auditing.
    """
    try:
        path = _get_persistence_path()
        path.parent.mkdir(parents=True, exist_ok=True)

        mode = "PROTECT" if progress.protect_mode else (
            "CONSERVATIVE" if progress.conservative_mode else "NORMAL"
        )

        data = {
            "last_update": datetime.now(tz=timezone.utc).isoformat(),
            "current_profit_percent": progress.current_profit_percent,
            "target_percent": progress.target_percent,
            "progress_percent": progress.progress_percent,
            "days_elapsed": progress.days_elapsed,
            "days_remaining": progress.days_remaining,
            "mode": mode,
        }

        json_bytes = json.dumps(data, indent=2).encode("utf-8")
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp", prefix="cp_")
        try:
            os.write(fd, json_bytes)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, str(path))
        return True

    except Exception as exc:
        logger.warning("[CHALLENGE_PROGRESS] persist_error=%s", exc)
        return False


def log_daily_progress(progress: ChallengeProgress) -> None:
    """Emit structured daily progress log."""
    mode = "PROTECT" if progress.protect_mode else (
        "CONSERVATIVE" if progress.conservative_mode else "NORMAL"
    )
    logger.info(
        "[CHALLENGE_PROGRESS] Current Profit: %.2f%% Target: %.2f%% "
        "Progress: %.1f%% Days Elapsed: %d Days Remaining: %d Mode: %s",
        progress.current_profit_percent, progress.target_percent,
        progress.progress_percent, progress.days_elapsed,
        progress.days_remaining, mode,
    )


# ─── STARTUP VALIDATION ───────────────────────────────────────────────────────

def validate_challenge_config() -> list[str]:
    """
    Validate challenge configuration at startup.

    Returns list of errors (empty = valid).
    Called by I5 self-test when challenge mode is enabled.
    """
    errors: list[str] = []

    if not _is_enabled():
        return errors  # Not enabled — nothing to validate

    target = _get_profit_target()
    if target <= 0:
        errors.append(f"CHALLENGE_PROFIT_TARGET_PERCENT must be > 0 (got {target})")

    start_str = _get_start_date()
    end_str = _get_end_date()

    if not start_str:
        errors.append("CHALLENGE_START_DATE is empty")
    if not end_str:
        errors.append("CHALLENGE_END_DATE is empty")

    if start_str and end_str:
        try:
            start = date.fromisoformat(start_str)
            end = date.fromisoformat(end_str)
            if end <= start:
                errors.append(f"CHALLENGE_END_DATE ({end_str}) must be after CHALLENGE_START_DATE ({start_str})")
        except ValueError as exc:
            errors.append(f"Invalid date format: {exc}")

    factor = _get_size_reduction_factor()
    if factor <= 0 or factor > 1.0:
        errors.append(f"CHALLENGE_SIZE_REDUCTION_FACTOR must be 0 < x <= 1.0 (got {factor})")

    threshold = _get_conservative_threshold()
    if threshold <= 0 or threshold > 100:
        errors.append(f"CHALLENGE_CONSERVATIVE_THRESHOLD_PERCENT must be 0 < x <= 100 (got {threshold})")

    return errors
