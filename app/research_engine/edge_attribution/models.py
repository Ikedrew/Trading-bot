"""
Edge Attribution Schema — Data model for opportunity analysis.

Defines EdgeAttributionRecord and ConditionPerformance for measuring
which market conditions contribute to positive expectancy.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


# ═══════════════════════════════════════════════════════════════════════════════
# STATISTICAL HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

_MIN_SAMPLES_HIGH = 50
_MIN_SAMPLES_MEDIUM = 20
_MIN_SAMPLES_LOW = 5


def _confidence_level(n: int) -> str:
    if n >= _MIN_SAMPLES_HIGH:
        return "HIGH"
    elif n >= _MIN_SAMPLES_MEDIUM:
        return "MEDIUM"
    elif n >= _MIN_SAMPLES_LOW:
        return "LOW"
    return "INSUFFICIENT"


def _compute_stats(r_values: list[float]) -> dict[str, Any]:
    """Compute performance statistics for a group of R-multiples."""
    if not r_values:
        return {"n": 0, "wr": 0.0, "avg_r": 0.0, "ev": 0.0, "pf": 0.0, "total_r": 0.0, "confidence": "INSUFFICIENT"}
    n = len(r_values)
    wins = [r for r in r_values if r > 0]
    losses = [r for r in r_values if r < 0]
    wr = len(wins) / n
    lr = len(losses) / n
    aw = sum(wins) / len(wins) if wins else 0.0
    al = abs(sum(losses) / len(losses)) if losses else 0.0
    ev = (wr * aw) - (lr * al)
    gw = sum(wins)
    gl = abs(sum(losses))
    pf = gw / gl if gl > 0 else (float("inf") if gw > 0 else 0.0)
    return {
        "n": n, "wr": round(wr, 4), "avg_r": round(sum(r_values) / n, 4),
        "ev": round(ev, 4), "pf": round(pf, 2), "total_r": round(sum(r_values), 2),
        "confidence": _confidence_level(n),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# EDGE ATTRIBUTION RECORD
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class EdgeAttributionRecord:
    """One decision with all condition tags and outcome."""
    entity_id: str = ""
    timestamp_utc: str = ""
    symbol: str = ""

    # Opportunity
    pattern: str = ""
    strategy: str = ""
    direction: str = ""

    # Market context
    regime: str = ""
    volatility_state: str = ""
    market_state: str = ""
    session: str = ""

    # Structure (binned from components)
    htf_alignment_bin: str = ""   # HIGH / MEDIUM / LOW
    trend_alignment_bin: str = "" # HIGH / MEDIUM / LOW
    bias_alignment_bin: str = ""  # HIGH / MEDIUM / LOW

    # Quality
    score_bin: str = ""           # HIGH / MEDIUM / LOW
    confirmation_bin: str = ""    # STRONG / WEAK

    # Outcome
    result_r: float = 0.0
    win: bool = False


def _bin_value(val: float, thresholds: tuple[float, float] = (0.33, 0.66)) -> str:
    """Bin a 0-1 value into HIGH/MEDIUM/LOW."""
    if val >= thresholds[1]:
        return "HIGH"
    elif val >= thresholds[0]:
        return "MEDIUM"
    return "LOW"


def build_attribution_record(trace: dict[str, Any], outcome_r: float) -> EdgeAttributionRecord:
    """Build an EdgeAttributionRecord from a decision trace + counterfactual outcome."""
    components = trace.get("components", {})

    # Infer session from timestamp
    ts = trace.get("timestamp_utc", "")
    session = "UNKNOWN"
    if ts and len(ts) >= 13:
        try:
            hour = int(ts[11:13])
            if 0 <= hour < 7:
                session = "ASIAN"
            elif 7 <= hour < 12:
                session = "LONDON"
            elif 12 <= hour < 14:
                session = "OVERLAP"
            elif 14 <= hour < 21:
                session = "NY"
            else:
                session = "OFF_SESSION"
        except ValueError:
            pass

    return EdgeAttributionRecord(
        entity_id=trace.get("entity_id", ""),
        timestamp_utc=ts,
        symbol=trace.get("symbol", ""),
        pattern=trace.get("pattern_name", ""),
        strategy=trace.get("selected_strategy") or "NONE",
        direction="BUY" if "BULL" in trace.get("pattern_name", "").upper() or trace.get("pattern_name", "") in (
            "TWEEZER_BOTTOM", "MORNING_STAR", "HAMMER", "INVERTED_HAMMER",
            "THREE_WHITE_SOLDIERS", "THREE_INSIDE_UP", "BULLISH_ENGULFING"
        ) else "SELL",
        regime=trace.get("regime", "UNKNOWN") or "UNKNOWN",
        volatility_state=trace.get("volatility_state", "") or "UNKNOWN",
        market_state=trace.get("market_state", "UNKNOWN") or "UNKNOWN",
        session=session,
        htf_alignment_bin=_bin_value(components.get("htf_alignment", 0.0)),
        trend_alignment_bin=_bin_value(components.get("trend_alignment", 0.0)),
        bias_alignment_bin=_bin_value(components.get("bias_alignment", 0.0)),
        score_bin=_bin_value(trace.get("score_neutral", 0.0), (0.40, 0.55)),
        confirmation_bin="STRONG" if components.get("confirmation_pre", 0.0) >= 0.5 else "WEAK",
        result_r=outcome_r,
        win=outcome_r > 0,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# CONDITION PERFORMANCE
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class ConditionPerformance:
    """Performance of one condition value (e.g., regime=TRENDING)."""
    feature: str
    value: str
    stats: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"feature": self.feature, "value": self.value, **self.stats}


@dataclass
class FeatureImportance:
    """Importance ranking for one feature."""
    feature: str
    impact: str = "LOW"  # HIGH / MEDIUM / LOW
    ev_spread: float = 0.0  # max_ev - min_ev across values
    best_value: str = ""
    best_ev: float = 0.0
    worst_value: str = ""
    worst_ev: float = 0.0
    reliable: bool = False  # True if best value has n >= 20

    def to_dict(self) -> dict[str, Any]:
        return {
            "feature": self.feature, "impact": self.impact,
            "ev_spread": round(self.ev_spread, 4),
            "best_value": self.best_value, "best_ev": round(self.best_ev, 4),
            "worst_value": self.worst_value, "worst_ev": round(self.worst_ev, 4),
            "reliable": self.reliable,
        }
