"""
Shadow EV Promotion Monitor — Tracks empirical vs synthetic performance.

Observational only. Never affects execution.
Accumulates dual EV comparison data, evaluates promotion criteria,
and emits Discord notifications at milestones.

Usage (from live_scanner after dual EV computation):
    from core.research_assessment.promotion_monitor import record_comparison
    record_comparison(dual_ev_dict)
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_STATE_FILE = "logs/research_monitor/promotion_state.json"
_MILESTONES = (100, 250, 500, 750, 1000)
_MIN_DECISIONS_FOR_PROMOTION = 500
_MIN_MATCH_RATE = 0.05  # At least 5% of decisions must match a candidate
_PERSIST_INTERVAL_SECONDS = 300  # Save state every 5 minutes


# ═══════════════════════════════════════════════════════════════════════════════
# PROMOTION STATE
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class PromotionState:
    """Accumulated shadow EV monitor state. Persisted across restarts."""
    decisions_processed: int = 0
    research_matches: int = 0
    synthetic_approvals: int = 0
    research_approvals: int = 0
    agreement_count: int = 0
    disagreement_count: int = 0
    research_would_execute: int = 0
    research_would_reject: int = 0
    # Research outcome tracking (from research shadow trades)
    research_trades_completed: int = 0
    research_trades_won: int = 0
    research_total_r: float = 0.0
    last_milestone_notified: int = 0
    promotion_status: str = "COLLECTING"  # COLLECTING / CANDIDATE / NOT_READY
    last_updated: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "decisions_processed": self.decisions_processed,
            "research_matches": self.research_matches,
            "synthetic_approvals": self.synthetic_approvals,
            "research_approvals": self.research_approvals,
            "agreement_count": self.agreement_count,
            "disagreement_count": self.disagreement_count,
            "research_would_execute": self.research_would_execute,
            "research_would_reject": self.research_would_reject,
            "research_trades_completed": self.research_trades_completed,
            "research_trades_won": self.research_trades_won,
            "research_total_r": round(self.research_total_r, 4),
            "last_milestone_notified": self.last_milestone_notified,
            "promotion_status": self.promotion_status,
            "last_updated": self.last_updated,
            "match_rate": round(self.research_matches / self.decisions_processed, 4) if self.decisions_processed > 0 else 0.0,
            "research_win_rate": round(self.research_trades_won / self.research_trades_completed, 4) if self.research_trades_completed > 0 else 0.0,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PromotionState":
        return cls(
            decisions_processed=int(data.get("decisions_processed", 0)),
            research_matches=int(data.get("research_matches", 0)),
            synthetic_approvals=int(data.get("synthetic_approvals", 0)),
            research_approvals=int(data.get("research_approvals", 0)),
            agreement_count=int(data.get("agreement_count", 0)),
            disagreement_count=int(data.get("disagreement_count", 0)),
            research_would_execute=int(data.get("research_would_execute", 0)),
            research_would_reject=int(data.get("research_would_reject", 0)),
            research_trades_completed=int(data.get("research_trades_completed", 0)),
            research_trades_won=int(data.get("research_trades_won", 0)),
            research_total_r=float(data.get("research_total_r", 0.0)),
            last_milestone_notified=int(data.get("last_milestone_notified", 0)),
            promotion_status=str(data.get("promotion_status", "COLLECTING")),
            last_updated=float(data.get("last_updated", 0.0)),
        )


# ═══════════════════════════════════════════════════════════════════════════════
# MONITOR SINGLETON
# ═══════════════════════════════════════════════════════════════════════════════

_lock = threading.Lock()
_state: PromotionState | None = None
_startup_notified: bool = False
_last_persist_time: float = 0.0


def _load_state() -> PromotionState:
    """Load persisted state or create fresh."""
    global _state
    if _state is not None:
        return _state

    path = Path(_STATE_FILE)
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            _state = PromotionState.from_dict(data)
            logger.info("[PROMOTION_MONITOR] restored state: %d decisions", _state.decisions_processed)
        except Exception:
            _state = PromotionState()
    else:
        _state = PromotionState()

    return _state


def _persist_state() -> None:
    """Persist current state to disk."""
    if _state is None:
        return
    try:
        path = Path(_STATE_FILE)
        path.parent.mkdir(parents=True, exist_ok=True)
        _state.last_updated = time.time()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(_state.to_dict(), f, indent=2)
    except Exception:
        pass  # Persistence failure must never affect runtime


def _emit_discord(event_type: str, data: dict[str, Any]) -> None:
    """Emit a Discord notification via the existing router. Never raises."""
    try:
        from core.log_router import route_event
        route_event("RESEARCH_MONITOR", data)
    except Exception:
        pass


def _check_milestones(state: PromotionState) -> None:
    """Check if a milestone has been reached and emit notification."""
    for milestone in _MILESTONES:
        if state.decisions_processed >= milestone and state.last_milestone_notified < milestone:
            state.last_milestone_notified = milestone
            match_rate = state.research_matches / state.decisions_processed if state.decisions_processed > 0 else 0
            _emit_discord("RESEARCH_MONITOR", {
                "event": "MILESTONE",
                "decisions": state.decisions_processed,
                "research_matches": state.research_matches,
                "match_rate": f"{match_rate:.1%}",
                "disagreements": state.disagreement_count,
                "research_would_execute": state.research_would_execute,
                "status": state.promotion_status,
            })
            _persist_state()  # Immediate persist on milestone
            break  # Only one milestone per call


def _evaluate_promotion(state: PromotionState) -> str:
    """Evaluate whether promotion criteria are met. Returns status string."""
    if state.decisions_processed < _MIN_DECISIONS_FOR_PROMOTION:
        return "COLLECTING"

    match_rate = state.research_matches / state.decisions_processed if state.decisions_processed > 0 else 0
    if match_rate < _MIN_MATCH_RATE:
        return "NOT_READY"

    # Research must show advantage (more would-execute disagreements than would-reject)
    if state.research_would_execute > state.research_would_reject:
        return "CANDIDATE"

    return "NOT_READY"


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════════

def record_comparison(dual_ev: dict[str, Any] | None) -> None:
    """
    Record one dual EV comparison into the promotion monitor.

    Called after every decision where dual EV was computed.
    Thread-safe. Never raises. Never affects execution.

    Args:
        dual_ev: Output of DualEVComparison.to_dict(), or None if unavailable.
    """
    global _last_persist_time

    if dual_ev is None:
        return

    try:
        with _lock:
            state = _load_state()
            state.decisions_processed += 1

            if dual_ev.get("candidate_match"):
                state.research_matches += 1

            if dual_ev.get("synthetic_positive"):
                state.synthetic_approvals += 1

            if dual_ev.get("empirical_positive"):
                state.research_approvals += 1

            exec_diff = dual_ev.get("execution_difference", "AGREE")
            if exec_diff == "AGREE":
                state.agreement_count += 1
            else:
                state.disagreement_count += 1
                if exec_diff == "RESEARCH_WOULD_EXECUTE":
                    state.research_would_execute += 1
                elif exec_diff == "RESEARCH_WOULD_REJECT":
                    state.research_would_reject += 1

            # Check milestones
            _check_milestones(state)

            # Evaluate promotion
            new_status = _evaluate_promotion(state)
            if new_status != state.promotion_status:
                old_status = state.promotion_status
                state.promotion_status = new_status
                if new_status == "CANDIDATE":
                    _emit_discord("RESEARCH_MONITOR", {
                        "event": "PROMOTION_CANDIDATE",
                        "decisions": state.decisions_processed,
                        "research_matches": state.research_matches,
                        "research_would_execute": state.research_would_execute,
                        "message": "Research model has met minimum promotion criteria. Review recommended.",
                    })
                _persist_state()  # Immediate persist on status change

            # Persist on time interval
            _should_persist = False
            now_ts = time.time()
            if now_ts - _last_persist_time >= _PERSIST_INTERVAL_SECONDS:
                _should_persist = True
            if _should_persist:
                _last_persist_time = now_ts
                _persist_state()

    except Exception:
        pass  # Monitor failure must NEVER affect trading


def emit_startup_notification() -> None:
    """Emit startup notification. Call once at system startup."""
    global _startup_notified
    if _startup_notified:
        return
    _startup_notified = True

    try:
        with _lock:
            state = _load_state()

        _emit_discord("RESEARCH_MONITOR", {
            "event": "STARTUP",
            "mode": "observation_only",
            "empirical_execution": "DISABLED",
            "decisions_so_far": state.decisions_processed,
            "status": state.promotion_status,
        })
    except Exception:
        pass


def get_state() -> dict[str, Any]:
    """Return current promotion state as dict (for observability/testing)."""
    with _lock:
        state = _load_state()
        return state.to_dict()


def reset_state() -> None:
    """Reset all state (for testing). Not for production use."""
    global _state, _startup_notified, _last_persist_time
    with _lock:
        _state = PromotionState()
        _startup_notified = False
        _last_persist_time = 0.0
        _persist_state()


def record_research_outcome(*, r_multiple: float, exit_reason: str = "") -> None:
    """
    Record a completed research shadow trade outcome.

    Called when a research shadow trade closes (SL/TP/timeout).
    Updates the promotion state with actual measured R-multiples.

    Never raises. Never affects execution.
    """
    try:
        with _lock:
            state = _load_state()
            state.research_trades_completed += 1
            state.research_total_r += r_multiple
            if r_multiple > 0:
                state.research_trades_won += 1
            _persist_state()
    except Exception:
        pass
