"""
Research Governance — Progressive Validation & Optimisation Decisions.

Provides:
    - Finding history (progressive evidence accumulation)
    - Baseline vs candidate comparison
    - Optimisation decision model (target + regression questions)
    - Rollback detection
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from research_engine.v10.base import timestamp_now
from research_engine.v10.research_governance.evidence_maturity import (
    assess_maturity, assess_decision, next_validation_step, estimate_consistency,
)

logger = logging.getLogger(__name__)

_HISTORY_DIR = "data/research/finding_history"


# ═══════════════════════════════════════════════════════════════
# FINDING HISTORY
# ═══════════════════════════════════════════════════════════════

class FindingHistory:
    """
    Retains progressive evaluation history for each finding.

    Each evaluation is appended — never overwrites previous results.
    Enables tracking maturity progression over time.
    """

    def __init__(self, history_dir: str | None = None):
        self._dir = Path(history_dir or _HISTORY_DIR)

    def append(self, finding_id: str, evaluation: dict[str, Any]) -> None:
        """Append an evaluation to a finding's history."""
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._dir / f"{finding_id}.jsonl"
        entry = {
            "timestamp": timestamp_now(),
            **evaluation,
        }
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")

    def load(self, finding_id: str) -> list[dict[str, Any]]:
        """Load full history for a finding."""
        path = self._dir / f"{finding_id}.jsonl"
        if not path.exists():
            return []
        entries = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return entries

    def latest(self, finding_id: str) -> dict[str, Any] | None:
        """Get most recent evaluation."""
        history = self.load(finding_id)
        return history[-1] if history else None


# ═══════════════════════════════════════════════════════════════
# BASELINE COMPARISON
# ═══════════════════════════════════════════════════════════════

def compare_baseline_candidate(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
) -> dict[str, Any]:
    """
    Compare baseline vs candidate results.

    Args:
        baseline: {"expectancy_r": float, "win_rate": float, "profit_factor": float, ...}
        candidate: Same schema as baseline

    Returns:
        Comparison with delta values and decision.
    """
    b_exp = baseline.get("expectancy_r", baseline.get("expectancy", 0)) or 0
    c_exp = candidate.get("expectancy_r", candidate.get("expectancy", 0)) or 0
    b_wr = baseline.get("win_rate", 0) or 0
    c_wr = candidate.get("win_rate", 0) or 0
    b_pf = baseline.get("profit_factor", 0) or 0
    c_pf = candidate.get("profit_factor", 0) or 0

    exp_delta = c_exp - b_exp
    wr_delta = c_wr - b_wr
    pf_delta = c_pf - b_pf

    sample_size = candidate.get("count", candidate.get("sample_size", 0)) or 0
    is_deterioration = exp_delta < -0.1

    # Assess maturity and decision
    consistency = estimate_consistency(candidate)
    maturity = assess_maturity(sample_size, abs(exp_delta), consistency)
    decision = assess_decision(
        sample_size=sample_size,
        effect_size=exp_delta,
        confidence_score=0.5,  # Moderate default for comparison
        maturity=maturity,
        is_deterioration=is_deterioration,
        baseline_delta=exp_delta,
    )

    return {
        "baseline": {
            "expectancy_r": b_exp,
            "win_rate": b_wr,
            "profit_factor": b_pf,
        },
        "candidate": {
            "expectancy_r": c_exp,
            "win_rate": c_wr,
            "profit_factor": c_pf,
            "sample_size": sample_size,
        },
        "delta": {
            "expectancy_r": round(exp_delta, 4),
            "win_rate": round(wr_delta, 4),
            "profit_factor": round(pf_delta, 2),
        },
        "maturity": maturity,
        "decision": decision["status"],
        "reason": decision["reason"],
        "confidence": maturity_to_confidence(maturity),
        "next_step": next_validation_step(decision["status"], maturity, sample_size),
        "limitations": _comparison_limitations(sample_size, maturity),
    }


# ═══════════════════════════════════════════════════════════════
# OPTIMISATION DECISION
# ═══════════════════════════════════════════════════════════════

def evaluate_optimisation(
    target_results: list[dict[str, Any]],
    regression_results: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Evaluate an optimisation campaign with target and regression questions.

    Args:
        target_results: Results for the intended improvement area
                        [{"question_id": str, "effect": float, "sample_size": int, ...}]
        regression_results: Results for system properties that must not degrade
                           Same schema as target_results

    Returns:
        Optimisation decision with status and reasoning.
    """
    # Assess targets
    target_improved = all(r.get("effect", 0) > 0 for r in target_results) if target_results else False
    target_strong = any(r.get("effect", 0) > 0.15 for r in target_results) if target_results else False
    avg_target_sample = (
        sum(r.get("sample_size", 0) for r in target_results) / len(target_results)
    ) if target_results else 0

    # Assess regressions
    regression_detected = any(r.get("effect", 0) < -0.1 for r in regression_results) if regression_results else False
    major_regression = any(r.get("effect", 0) < -0.25 for r in regression_results) if regression_results else False

    # Decision logic
    if major_regression:
        decision = "ROLLBACK"
        reason = "Major regression detected in system properties. Candidate must be reverted."
    elif not target_improved and regression_detected:
        decision = "REJECT"
        reason = "Target did not improve and regression detected."
    elif not target_improved:
        decision = "REJECT"
        reason = "Target question shows no improvement."
    elif regression_detected:
        decision = "INVESTIGATE"
        reason = "Target improved but regression detected in other properties. Needs investigation."
    elif target_strong and avg_target_sample >= 30:
        decision = "KEEP"
        reason = "Target shows strong improvement with no regressions."
    elif target_improved and avg_target_sample >= 15:
        decision = "CONTINUE_TESTING"
        reason = "Target improved but evidence is still developing."
    elif target_improved:
        decision = "PROMISING"
        reason = "Early positive signal on target question."
    else:
        decision = "INVESTIGATE"
        reason = "Mixed results. More data needed."

    return {
        "decision": decision,
        "reason": reason,
        "target_improved": target_improved,
        "target_strong": target_strong,
        "regression_detected": regression_detected,
        "major_regression": major_regression,
        "target_results": target_results,
        "regression_results": regression_results,
        "avg_target_sample": round(avg_target_sample),
    }


# ═══════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════

def maturity_to_confidence(maturity: str) -> str:
    """Map maturity to approximate confidence level."""
    mapping = {
        "EXPLORATORY": "LOW",
        "EARLY": "LOW",
        "DEVELOPING": "MEDIUM",
        "STRONG": "HIGH",
        "LONG_RUN": "HIGH",
    }
    return mapping.get(maturity, "LOW")


def _comparison_limitations(sample_size: int, maturity: str) -> list[str]:
    """Generate limitations list for a comparison."""
    limits = []
    if sample_size < 15:
        limits.append(f"Small sample (n={sample_size}) — result may not generalise")
    if maturity in ("EXPLORATORY", "EARLY"):
        limits.append("Evidence is early-stage — direction may change with more data")
    return limits
