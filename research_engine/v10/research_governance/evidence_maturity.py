"""
Research Governance — Evidence Maturity & Decision Readiness.

Separates three independent assessments:
    1. STATISTICAL CONFIDENCE — how reliable is the measurement?
    2. EVIDENCE MATURITY — how much evidence has accumulated?
    3. DECISION READINESS — can we act on this finding?

Evidence maturity states:
    EXPLORATORY   — very early, directional signal only
    EARLY         — enough for directional evidence
    DEVELOPING    — repeated evidence with reasonable consistency
    STRONG        — substantial and stable
    LONG_RUN      — large sustained evidence across time

Decision statuses:
    INVESTIGATE       — worth looking into, not enough to act
    PROMISING         — positive early signal, continue collecting
    CONTINUE_TESTING  — consistent signal, needs more data
    SUPPORTED         — enough evidence to act
    REJECTED          — enough evidence to stop
    EARLY_FAILURE     — strong negative signal despite small sample
    INCONCLUSIVE      — cannot determine direction

Each finding also produces a NEXT STEP explaining what evidence is needed.
"""

from __future__ import annotations

from typing import Any


# ═══════════════════════════════════════════════════════════════
# EVIDENCE MATURITY
# ═══════════════════════════════════════════════════════════════

def assess_maturity(
    sample_size: int,
    effect_size: float = 0.0,
    consistency: float = 0.0,
) -> str:
    """
    Assess evidence maturity based on sample and effect characteristics.

    NOT a single threshold — considers effect magnitude to determine
    whether the current sample is meaningful for this specific finding.
    """
    if sample_size < 5:
        return "EXPLORATORY"

    # Large effects can be directionally meaningful with fewer trades
    if sample_size < 15:
        if abs(effect_size) >= 0.3:
            return "EARLY"
        return "EXPLORATORY"

    if sample_size < 30:
        if consistency >= 0.6 or abs(effect_size) >= 0.2:
            return "DEVELOPING"
        return "EARLY"

    if sample_size < 60:
        if consistency >= 0.7:
            return "STRONG"
        return "DEVELOPING"

    # 60+ trades with reasonable consistency
    if consistency >= 0.6:
        return "LONG_RUN"
    return "STRONG"


# ═══════════════════════════════════════════════════════════════
# DECISION STATUS
# ═══════════════════════════════════════════════════════════════

def assess_decision(
    sample_size: int,
    effect_size: float,
    confidence_score: float,
    maturity: str,
    is_deterioration: bool = False,
    baseline_delta: float | None = None,
) -> dict[str, str]:
    """
    Determine practical decision status.

    This is NOT the same as statistical confidence.
    A finding can have LOW confidence but still be PROMISING.
    A finding can have LOW confidence but still be EARLY_FAILURE.

    Returns:
        {"status": str, "reason": str}
    """
    # Early failure detection — large negative effects warrant attention even with small samples
    if is_deterioration and abs(effect_size) >= 0.25 and sample_size >= 10:
        return {
            "status": "EARLY_FAILURE",
            "reason": "Large negative deterioration observed. Performance materially worse than baseline.",
        }

    # Baseline comparison shortcut
    if baseline_delta is not None and baseline_delta < -0.3 and sample_size >= 12:
        return {
            "status": "EARLY_FAILURE",
            "reason": f"Candidate shows {baseline_delta:+.2f}R deterioration vs baseline.",
        }

    # Insufficient evidence for any practical decision
    if sample_size < 5:
        return {
            "status": "INVESTIGATE",
            "reason": "Too few observations for any directional assessment.",
        }

    # Decision logic by maturity level
    if maturity == "EXPLORATORY":
        if abs(effect_size) >= 0.2:
            return {
                "status": "PROMISING" if effect_size > 0 else "INVESTIGATE",
                "reason": "Directional signal detected but sample too small to confirm.",
            }
        return {
            "status": "INVESTIGATE",
            "reason": "Early data available. Direction unclear.",
        }

    if maturity == "EARLY":
        if effect_size > 0.1:
            return {
                "status": "PROMISING",
                "reason": "Positive early signal with meaningful effect size.",
            }
        if effect_size < -0.15:
            return {
                "status": "EARLY_FAILURE",
                "reason": "Negative early signal. Consider stopping this candidate.",
            }
        return {
            "status": "INVESTIGATE",
            "reason": "Early evidence is mixed. More data needed for direction.",
        }

    if maturity == "DEVELOPING":
        if effect_size > 0.05 and confidence_score >= 0.4:
            return {
                "status": "CONTINUE_TESTING",
                "reason": "Consistent positive improvement. Continue validation.",
            }
        if effect_size < -0.1:
            return {
                "status": "REJECTED",
                "reason": "Developing evidence shows consistent negative performance.",
            }
        return {
            "status": "CONTINUE_TESTING",
            "reason": "Results are developing. Insufficient clarity for final decision.",
        }

    if maturity in ("STRONG", "LONG_RUN"):
        if effect_size > 0 and confidence_score >= 0.5:
            return {
                "status": "SUPPORTED",
                "reason": "Strong evidence supports this finding.",
            }
        if effect_size < -0.05:
            return {
                "status": "REJECTED",
                "reason": "Strong evidence rejects this hypothesis.",
            }
        return {
            "status": "INCONCLUSIVE",
            "reason": "Large sample but effect is negligible.",
        }

    return {"status": "INCONCLUSIVE", "reason": "Unable to determine from available evidence."}


# ═══════════════════════════════════════════════════════════════
# NEXT VALIDATION STEP
# ═══════════════════════════════════════════════════════════════

def next_validation_step(decision_status: str, maturity: str, sample_size: int) -> str:
    """
    Generate the recommended next action based on current state.

    Every non-final finding explains what evidence would resolve it.
    """
    steps = {
        "INVESTIGATE": (
            f"Collect more observations (currently n={sample_size}). "
            f"Look for directional consistency in next 10-15 trades."
        ),
        "PROMISING": (
            f"Continue collecting evidence. Current sample (n={sample_size}) shows positive direction. "
            f"Validate across at least one additional market condition or time period."
        ),
        "CONTINUE_TESTING": (
            f"Run another validation window. Evidence is developing (n={sample_size}). "
            f"Target 50+ trades for stronger conclusion."
        ),
        "SUPPORTED": (
            "Validate against broader populations and forward data. "
            "Consider cross-segment confirmation before implementation."
        ),
        "REJECTED": (
            "Stop pursuing this direction. Investigate root cause of underperformance. "
            "Consider alternative approaches."
        ),
        "EARLY_FAILURE": (
            "Stop candidate immediately. Investigate failure mode. "
            "Do not continue collecting data on a clearly harmful change."
        ),
        "INCONCLUSIVE": (
            f"Identify which missing evidence prevents a decision. "
            f"Current sample (n={sample_size}) at maturity '{maturity}' "
            f"does not show clear directional signal. "
            f"Consider whether the question is answerable with available data."
        ),
    }
    return steps.get(decision_status, "Review finding manually.")


# ═══════════════════════════════════════════════════════════════
# CONSISTENCY ESTIMATOR
# ═══════════════════════════════════════════════════════════════

def estimate_consistency(result_data: dict[str, Any]) -> float:
    """
    Estimate result consistency from available metrics.

    Uses win_rate stability and profit_factor as proxies.
    Returns 0.0-1.0 where 1.0 = perfectly consistent.
    """
    win_rate = result_data.get("win_rate", 0)
    pf = result_data.get("profit_factor", 0)
    expectancy = result_data.get("expectancy_r", result_data.get("expectancy", 0))

    score = 0.0

    # Win rate near extremes = less consistent signal
    if 0.35 <= win_rate <= 0.65:
        score += 0.3
    elif 0.25 <= win_rate <= 0.75:
        score += 0.2

    # Profit factor > 1 = positive consistency
    if pf > 1.5:
        score += 0.4
    elif pf > 1.0:
        score += 0.2

    # Positive expectancy adds consistency
    if expectancy > 0.1:
        score += 0.3
    elif expectancy > 0:
        score += 0.15

    return min(score, 1.0)
