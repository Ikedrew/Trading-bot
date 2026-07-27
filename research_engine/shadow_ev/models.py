"""
Shadow EV Models — Three alternative Expected Value calculations.

Completely isolated from production execution logic.
Consumes only existing decision evidence (decision_trace fields).

MODEL A — Empirical Historical EV:
    p_success = pattern historical win rate (from shadow trade outcomes)
    EV = p_success × RR - (1 - p_success) × 1.0

MODEL B — Bayesian EV:
    Combines historical evidence with a conservative prior.
    Avoids overreacting to small samples.
    p_success = (history_wr × n + prior × prior_weight) / (n + prior_weight)

MODEL C — Regime/Strategy Conditional EV:
    Uses conditional probability: P(win | pattern, regime, direction)
    Falls back to less specific conditionals when data is sparse.

All models produce: (p_success, ev, action) where action = EXECUTE if ev > 0.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


_RR3_PATTERNS = frozenset({"THREE_WHITE_SOLDIERS", "THREE_BLACK_CROWS"})
_BASE_RR = 2.0
_P_MIN = 0.05
_P_MAX = 0.90
_DAMPENING = {"STRUCTURED": 0.03, "TRANSITIONAL": 0.10, "CHOP": 0.20}

# Bayesian prior
_PRIOR_WIN_RATE = 0.30  # Conservative base rate (from Q2 overall win rate)
_PRIOR_WEIGHT = 10      # Equivalent to 10 virtual observations
_MIN_SAMPLES_EMPIRICAL = 5
_MIN_SAMPLES_CONDITIONAL = 8


def _get_rr(pattern: str) -> float:
    return 3.0 if pattern in _RR3_PATTERNS else _BASE_RR


def _clamp(p: float) -> float:
    return max(_P_MIN, min(_P_MAX, p))


# ═══════════════════════════════════════════════════════════════════════════════
# ASSESSMENT SCHEMA
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class ShadowEVAssessment:
    """Shadow EV output for one decision."""
    entity_id: str = ""
    symbol: str = ""
    timestamp_utc: str = ""
    pattern: str = ""
    direction: str = ""
    regime: str = ""
    market_state: str = ""
    rr: float = 0.0

    # Production EV (replicated)
    existing_p: float = 0.0
    existing_ev: float = 0.0
    existing_action: str = "NO_TRADE"

    # Model A
    model_a_p: float = 0.0
    model_a_ev: float = 0.0
    model_a_action: str = "NO_TRADE"

    # Model B
    model_b_p: float = 0.0
    model_b_ev: float = 0.0
    model_b_action: str = "NO_TRADE"

    # Model C
    model_c_p: float = 0.0
    model_c_ev: float = 0.0
    model_c_action: str = "NO_TRADE"

    # Disagreement
    disagreement: bool = False
    disagreement_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "symbol": self.symbol,
            "timestamp_utc": self.timestamp_utc,
            "pattern": self.pattern,
            "direction": self.direction,
            "regime": self.regime,
            "market_state": self.market_state,
            "rr": round(self.rr, 3),
            "existing_p": round(self.existing_p, 4),
            "existing_ev": round(self.existing_ev, 6),
            "existing_action": self.existing_action,
            "model_a_p": round(self.model_a_p, 4),
            "model_a_ev": round(self.model_a_ev, 6),
            "model_a_action": self.model_a_action,
            "model_b_p": round(self.model_b_p, 4),
            "model_b_ev": round(self.model_b_ev, 6),
            "model_b_action": self.model_b_action,
            "model_c_p": round(self.model_c_p, 4),
            "model_c_ev": round(self.model_c_ev, 6),
            "model_c_action": self.model_c_action,
            "disagreement": self.disagreement,
            "disagreement_reason": self.disagreement_reason,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# PRODUCTION MODEL (replicated for comparison)
# ═══════════════════════════════════════════════════════════════════════════════

def _existing_model(trace: dict[str, Any]) -> tuple[float, float]:
    """Replicate production p_success + EV."""
    score = float(trace.get("score_neutral", 0.0))
    strat_conf = float(trace.get("strategy_confidence", 0.0))
    ms = trace.get("market_state", "TRANSITIONAL")
    conf = float(trace.get("confirmation_score", 1.0) or 1.0)
    pattern = trace.get("pattern_name", "")
    rr = _get_rr(pattern)

    dampening = {"STRUCTURED": 0.05, "TRANSITIONAL": 0.20, "CHOP": 0.25}.get(ms, 0.20)
    p_base = (score * 0.6) + (strat_conf * 0.4)
    conf_mod = 0.5 + (0.5 * conf)
    p = max(0.10, min(0.85, p_base * conf_mod * (1.0 - dampening)))
    ev = (p * rr) - ((1.0 - p) * 1.0)
    return p, ev


# ═══════════════════════════════════════════════════════════════════════════════
# MODEL A: EMPIRICAL HISTORICAL
# ═══════════════════════════════════════════════════════════════════════════════

def _model_a(
    trace: dict[str, Any],
    pattern_win_rates: dict[str, float],
) -> tuple[float, float]:
    """
    Model A: p_success = empirical pattern win rate.
    Applies mild market state dampening.
    """
    pattern = trace.get("pattern_name", "")
    ms = trace.get("market_state", "TRANSITIONAL")
    rr = _get_rr(pattern)

    if pattern in pattern_win_rates:
        p_base = pattern_win_rates[pattern]
    else:
        p_base = _PRIOR_WIN_RATE

    dampening = _DAMPENING.get(ms, 0.10)
    p = _clamp(p_base * (1.0 - dampening))
    ev = (p * rr) - ((1.0 - p) * 1.0)
    return p, ev


# ═══════════════════════════════════════════════════════════════════════════════
# MODEL B: BAYESIAN
# ═══════════════════════════════════════════════════════════════════════════════

def _model_b(
    trace: dict[str, Any],
    pattern_win_rates: dict[str, float],
    pattern_counts: dict[str, int],
) -> tuple[float, float]:
    """
    Model B: Bayesian-adjusted probability.
    Combines empirical evidence with conservative prior, weighted by sample size.
    """
    pattern = trace.get("pattern_name", "")
    ms = trace.get("market_state", "TRANSITIONAL")
    rr = _get_rr(pattern)

    n = pattern_counts.get(pattern, 0)
    empirical_wr = pattern_win_rates.get(pattern, _PRIOR_WIN_RATE)

    # Bayesian posterior: weighted average of empirical + prior
    # More samples → more weight on empirical; fewer → prior dominates
    if n >= _MIN_SAMPLES_EMPIRICAL:
        p_base = (empirical_wr * n + _PRIOR_WIN_RATE * _PRIOR_WEIGHT) / (n + _PRIOR_WEIGHT)
    else:
        p_base = _PRIOR_WIN_RATE

    dampening = _DAMPENING.get(ms, 0.10)
    p = _clamp(p_base * (1.0 - dampening))
    ev = (p * rr) - ((1.0 - p) * 1.0)
    return p, ev


# ═══════════════════════════════════════════════════════════════════════════════
# MODEL C: CONDITIONAL
# ═══════════════════════════════════════════════════════════════════════════════

def _model_c(
    trace: dict[str, Any],
    conditional_win_rates: dict[str, float],
    conditional_counts: dict[str, int],
    pattern_win_rates: dict[str, float],
) -> tuple[float, float]:
    """
    Model C: Regime/pattern/direction conditional probability.
    Uses most specific conditional available, falls back to less specific.
    """
    pattern = trace.get("pattern_name", "")
    regime = trace.get("regime", "UNKNOWN")
    ms = trace.get("market_state", "TRANSITIONAL")
    rr = _get_rr(pattern)

    # Try most specific: regime + pattern
    key_specific = f"{regime}|{pattern}"
    if conditional_counts.get(key_specific, 0) >= _MIN_SAMPLES_CONDITIONAL:
        p_base = conditional_win_rates[key_specific]
    # Fallback: pattern only
    elif pattern in pattern_win_rates:
        p_base = pattern_win_rates[pattern]
    else:
        p_base = _PRIOR_WIN_RATE

    dampening = _DAMPENING.get(ms, 0.10)
    p = _clamp(p_base * (1.0 - dampening))
    ev = (p * rr) - ((1.0 - p) * 1.0)
    return p, ev


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN ASSESSMENT FUNCTION
# ═══════════════════════════════════════════════════════════════════════════════

def compute_shadow_ev(
    trace: dict[str, Any],
    pattern_win_rates: dict[str, float],
    pattern_counts: dict[str, int],
    conditional_win_rates: dict[str, float],
    conditional_counts: dict[str, int],
) -> ShadowEVAssessment:
    """
    Compute all shadow EV models for one decision trace.

    Args:
        trace: Single decision_trace record.
        pattern_win_rates: {pattern_name: win_rate} from historical data.
        pattern_counts: {pattern_name: sample_count}.
        conditional_win_rates: {regime|pattern: win_rate}.
        conditional_counts: {regime|pattern: sample_count}.
    """
    pattern = trace.get("pattern_name", "")
    rr = _get_rr(pattern)

    assessment = ShadowEVAssessment(
        entity_id=trace.get("entity_id", ""),
        symbol=trace.get("symbol", ""),
        timestamp_utc=trace.get("timestamp_utc", ""),
        pattern=pattern,
        direction=trace.get("side", "") or "",
        regime=trace.get("regime", ""),
        market_state=trace.get("market_state", ""),
        rr=rr,
    )

    # Production model
    p_ex, ev_ex = _existing_model(trace)
    assessment.existing_p = p_ex
    assessment.existing_ev = ev_ex
    assessment.existing_action = "EXECUTE" if ev_ex > 0 else "NO_TRADE"

    # Model A
    p_a, ev_a = _model_a(trace, pattern_win_rates)
    assessment.model_a_p = p_a
    assessment.model_a_ev = ev_a
    assessment.model_a_action = "EXECUTE" if ev_a > 0 else "NO_TRADE"

    # Model B
    p_b, ev_b = _model_b(trace, pattern_win_rates, pattern_counts)
    assessment.model_b_p = p_b
    assessment.model_b_ev = ev_b
    assessment.model_b_action = "EXECUTE" if ev_b > 0 else "NO_TRADE"

    # Model C
    p_c, ev_c = _model_c(trace, conditional_win_rates, conditional_counts, pattern_win_rates)
    assessment.model_c_p = p_c
    assessment.model_c_ev = ev_c
    assessment.model_c_action = "EXECUTE" if ev_c > 0 else "NO_TRADE"

    # Disagreement
    actions = {assessment.existing_action, assessment.model_a_action, assessment.model_b_action, assessment.model_c_action}
    if len(actions) > 1:
        assessment.disagreement = True
        approvers = []
        if assessment.model_a_action == "EXECUTE" and assessment.existing_action == "NO_TRADE":
            approvers.append("A")
        if assessment.model_b_action == "EXECUTE" and assessment.existing_action == "NO_TRADE":
            approvers.append("B")
        if assessment.model_c_action == "EXECUTE" and assessment.existing_action == "NO_TRADE":
            approvers.append("C")
        if approvers:
            assessment.disagreement_reason = f"Models {','.join(approvers)} approve; CURRENT rejects"
        elif assessment.existing_action == "EXECUTE":
            rejectors = []
            if assessment.model_a_action == "NO_TRADE":
                rejectors.append("A")
            if assessment.model_b_action == "NO_TRADE":
                rejectors.append("B")
            if assessment.model_c_action == "NO_TRADE":
                rejectors.append("C")
            assessment.disagreement_reason = f"CURRENT approves; Models {','.join(rejectors)} reject"

    return assessment
