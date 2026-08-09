"""
Research Governance — Confidence Engine.

Assigns confidence scores to research findings based on multiple factors:
    - sample size
    - effect size (magnitude of the finding)
    - consistency (recommendation strength)
    - data completeness
    - limitations count
"""

from __future__ import annotations

from typing import Any


class ConfidenceEngine:
    """
    Assigns statistical confidence to research findings.

    Score range: 0.0 - 1.0
    Levels: HIGH (>= 0.7), MEDIUM (0.4-0.7), LOW (< 0.4)
    """

    def assess(
        self,
        sample_size: int = 0,
        effect_size: float = 0.0,
        recommendation: str = "INCONCLUSIVE",
        limitations: list[str] | None = None,
        data_completeness_pct: float = 100.0,
    ) -> dict[str, Any]:
        """
        Assess confidence of a research finding.

        Returns:
            {"confidence": str, "score": float, "factors": list[str]}
        """
        factors = []
        scores = []

        # Factor 1: Sample size (0-1)
        if sample_size >= 50:
            s = 1.0
            factors.append(f"Large sample (n={sample_size})")
        elif sample_size >= 30:
            s = 0.7
            factors.append(f"Adequate sample (n={sample_size})")
        elif sample_size >= 15:
            s = 0.4
            factors.append(f"Limited sample (n={sample_size})")
        elif sample_size >= 10:
            s = 0.25
            factors.append(f"Small sample (n={sample_size})")
        else:
            s = 0.1
            factors.append(f"Insufficient sample (n={sample_size})")
        scores.append(("sample", s, 0.35))

        # Factor 2: Effect size (0-1)
        if effect_size >= 0.5:
            e = 1.0
            factors.append(f"Large effect ({effect_size:.2f})")
        elif effect_size >= 0.2:
            e = 0.7
            factors.append(f"Moderate effect ({effect_size:.2f})")
        elif effect_size >= 0.1:
            e = 0.4
            factors.append(f"Small effect ({effect_size:.2f})")
        else:
            e = 0.15
            factors.append(f"Negligible effect ({effect_size:.2f})")
        scores.append(("effect", e, 0.25))

        # Factor 3: Recommendation clarity (0-1)
        if recommendation in ("SUPPORTED", "REJECTED"):
            r = 0.9
            factors.append(f"Clear conclusion ({recommendation})")
        elif recommendation == "INCONCLUSIVE":
            r = 0.3
            factors.append("Inconclusive result")
        else:
            r = 0.5
        scores.append(("clarity", r, 0.2))

        # Factor 4: Limitations penalty (0-1)
        n_limits = len(limitations or [])
        if n_limits == 0:
            l = 1.0
        elif n_limits == 1:
            l = 0.7
            factors.append(f"{n_limits} limitation noted")
        elif n_limits <= 3:
            l = 0.4
            factors.append(f"{n_limits} limitations noted")
        else:
            l = 0.2
            factors.append(f"{n_limits} limitations — high uncertainty")
        scores.append(("limitations", l, 0.1))

        # Factor 5: Data completeness (0-1)
        dc = min(data_completeness_pct / 100.0, 1.0)
        if dc < 0.8:
            factors.append(f"Incomplete data ({data_completeness_pct:.0f}%)")
        scores.append(("completeness", dc, 0.1))

        # Weighted score
        total_score = sum(value * weight for _, value, weight in scores)
        total_score = round(min(max(total_score, 0.0), 1.0), 3)

        # Classify
        if total_score >= 0.7:
            confidence = "HIGH"
        elif total_score >= 0.4:
            confidence = "MEDIUM"
        else:
            confidence = "LOW"

        return {
            "confidence": confidence,
            "score": total_score,
            "factors": factors,
        }
