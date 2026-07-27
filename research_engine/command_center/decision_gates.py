"""
Research Decision Gates — Determines whether research evidence is strong enough
to justify strategy logic changes.

Separates:
    1. Historical Result — what previous research discovered
    2. Current Evidence Status — whether current data supports acting on that finding

This module is PURELY REPORTING. It does NOT:
    - Modify trading logic, scoring, execution, or risk
    - Apply changes to strategy
    - Run experiments

It ONLY evaluates whether the current data collection state meets the
requirements for safe strategy modification.

Usage:
    from research_engine.command_center.decision_gates import evaluate_decision_gates
    gates = evaluate_decision_gates(data_health, dashboard, reports)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from research_engine.command_center.command_models import DataHealth


# ═══════════════════════════════════════════════════════════════════════════════
# DECISION STATUS
# ═══════════════════════════════════════════════════════════════════════════════


class ResearchDecisionStatus(str, Enum):
    """Whether research evidence currently supports action."""
    PROMOTE = "PROMOTE"           # Evidence sufficient to promote/enable
    MODIFY = "MODIFY"             # Evidence supports conditional modification
    MONITOR = "MONITOR"           # Evidence exists but not yet actionable
    BLOCKED = "BLOCKED"           # Structurally blocked (missing infrastructure)
    INVALIDATED = "INVALIDATED"   # Historical finding has been disproven
    NEEDS_DATA = "NEEDS_DATA"     # Insufficient current data to validate


# ═══════════════════════════════════════════════════════════════════════════════
# GATE REQUIREMENT
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class GateRequirement:
    """A single requirement for a decision gate."""
    field_name: str
    required_pct: float     # 0.0 - 1.0
    current_pct: float      # 0.0 - 1.0
    met: bool

    @property
    def display(self) -> str:
        icon = "Y" if self.met else "X"
        return f"{self.field_name} {self.current_pct*100:.0f}% / {self.required_pct*100:.0f}% [{icon}]"


# ═══════════════════════════════════════════════════════════════════════════════
# RESEARCH DECISION
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class ResearchDecision:
    """Decision gate evaluation for a single research question."""
    question_id: str
    title: str
    historical_status: str = ""         # e.g. "COMPLETE", "PROMOTE_CALIBRATION"
    historical_result: str = ""         # e.g. "+0.55R EV", "WEIGHT_ADJUSTMENT"
    current_status: ResearchDecisionStatus = ResearchDecisionStatus.NEEDS_DATA
    confidence: str = ""                # HIGH / MEDIUM / LOW / INSUFFICIENT
    can_change_strategy_logic: bool = False
    blocking_requirements: list[GateRequirement] = field(default_factory=list)
    recommended_action: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "title": self.title,
            "historical_status": self.historical_status,
            "historical_result": self.historical_result,
            "current_status": self.current_status.value,
            "confidence": self.confidence,
            "can_change_strategy_logic": self.can_change_strategy_logic,
            "blocking_requirements": [
                {"field": r.field_name, "required": r.required_pct,
                 "current": r.current_pct, "met": r.met}
                for r in self.blocking_requirements
            ],
            "recommended_action": self.recommended_action,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# PROMOTION READINESS SUMMARY
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class PromotionReadinessSummary:
    """Overall promotion readiness assessment."""
    strategy_changes_allowed: bool = False
    reason: str = ""
    required_before_changes: list[str] = field(default_factory=list)
    safe_actions: list[str] = field(default_factory=list)
    unsafe_actions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_changes_allowed": self.strategy_changes_allowed,
            "reason": self.reason,
            "required_before_changes": self.required_before_changes,
            "safe_actions": self.safe_actions,
            "unsafe_actions": self.unsafe_actions,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# DECISION GATE REPORT (Section 12)
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class DecisionGateReport:
    """Complete Section 12 output."""
    decisions: list[ResearchDecision] = field(default_factory=list)
    promotion_summary: PromotionReadinessSummary = field(default_factory=PromotionReadinessSummary)

    def to_dict(self) -> dict[str, Any]:
        return {
            "decisions": [d.to_dict() for d in self.decisions],
            "promotion_summary": self.promotion_summary.to_dict(),
        }


# ═══════════════════════════════════════════════════════════════════════════════
# GATE THRESHOLDS
# ═══════════════════════════════════════════════════════════════════════════════

# Coverage requirements for different research categories
_GATE_LINEAGE = 0.80
_GATE_OUTCOME = 0.95
_GATE_STRATEGY = 0.50
_GATE_HORIZON = 0.50
_GATE_REGIME = 0.80
_GATE_PHASE = 0.80
_MIN_SAMPLES_PROMOTE = 200
_MIN_SAMPLES_MODIFY = 100
_MIN_SAMPLES_MONITOR = 50


# ═══════════════════════════════════════════════════════════════════════════════
# GATE EVALUATION
# ═══════════════════════════════════════════════════════════════════════════════


def evaluate_decision_gates(
    data_health: DataHealth,
    dashboard: dict[str, Any] | None,
    reports: dict[str, dict[str, Any]],
) -> DecisionGateReport:
    """
    Evaluate decision gates for all research questions.

    For each question, determines:
        - Historical finding (preserved, never overwritten)
        - Current evidence status (can we ACT on that finding?)
        - Whether strategy logic changes are safe

    Returns a DecisionGateReport with per-question decisions
    and an overall promotion readiness summary.
    """
    # Extract current coverage from data_health
    coverage = _extract_coverage(data_health)

    # Build per-question decisions
    decisions: list[ResearchDecision] = []

    if dashboard:
        for qid, info in sorted(dashboard.get("questions", {}).items()):
            decision = _evaluate_single_question(qid, info, reports.get(qid), coverage, data_health)
            decisions.append(decision)

    # Build promotion summary
    promotion_summary = _build_promotion_summary(coverage, data_health, decisions)

    return DecisionGateReport(
        decisions=decisions,
        promotion_summary=promotion_summary,
    )


def _extract_coverage(data_health: DataHealth) -> dict[str, float]:
    """Extract coverage percentages into a flat dict for gate checks."""
    cov: dict[str, float] = {
        "entity_id": data_health.lineage_coverage.pct,
        "outcome": data_health.outcome_coverage.pct,
    }
    for field in data_health.context_fields:
        # Normalize field names
        name = field.name.lower().replace(" ", "_")
        cov[name] = field.pct
    cov["contamination"] = 0.0 if data_health.contamination_count == 0 else 1.0
    cov["record_count"] = float(data_health.record_count)
    return cov


def _evaluate_single_question(
    qid: str,
    info: dict[str, Any],
    report: dict[str, Any] | None,
    coverage: dict[str, float],
    data_health: DataHealth,
) -> ResearchDecision:
    """Evaluate decision gate for a single research question."""
    title = info.get("question", "")
    historical_status = info.get("recommendation", info.get("status", ""))
    historical_result = _extract_historical_result(qid, info, report)

    # Determine which gates apply to this question
    requirements = _get_requirements_for_question(qid, coverage, data_health)

    # All requirements met?
    all_met = all(r.met for r in requirements)
    unmet = [r for r in requirements if not r.met]

    # Determine current status
    current_status, confidence, can_change = _classify_decision(
        qid, historical_status, all_met, unmet, coverage, data_health,
    )

    # Recommended action
    if can_change:
        recommended_action = f"Evidence supports action on {qid}. Review finding before applying."
    elif unmet:
        worst = max(unmet, key=lambda r: r.required_pct - r.current_pct)
        recommended_action = f"Collect data: {worst.field_name} at {worst.current_pct*100:.0f}% (need {worst.required_pct*100:.0f}%)"
    elif current_status == ResearchDecisionStatus.BLOCKED:
        recommended_action = "Resolve structural blocker before proceeding."
    elif current_status == ResearchDecisionStatus.INVALIDATED:
        recommended_action = "Finding invalidated. Do not act on historical result."
    else:
        recommended_action = "Continue monitoring. Collect more data."

    return ResearchDecision(
        question_id=qid,
        title=title,
        historical_status=historical_status,
        historical_result=historical_result,
        current_status=current_status,
        confidence=confidence,
        can_change_strategy_logic=can_change,
        blocking_requirements=requirements,
        recommended_action=recommended_action,
    )


def _get_requirements_for_question(
    qid: str,
    coverage: dict[str, float],
    data_health: DataHealth,
) -> list[GateRequirement]:
    """Determine which gate requirements apply to a question."""
    reqs: list[GateRequirement] = []

    # All questions need lineage
    lineage_pct = coverage.get("entity_id", 0.0)
    reqs.append(GateRequirement("entity_id", _GATE_LINEAGE, lineage_pct, lineage_pct >= _GATE_LINEAGE))

    # Q19 (EV), Q20 (calibration), Q22 (threshold) need full validation
    ev_questions = {"Q19", "Q20", "Q21", "Q22", "E1", "E5", "D2", "D3"}
    # Q24 (strategy edge) needs strategy coverage
    strategy_questions = {"Q24", "Q2", "E3", "S1", "S5", "L7"}
    # Q23 (regime edge), Q6 (regime accuracy) need regime
    regime_questions = {"Q23", "Q6", "M1", "M2", "M4"}
    # Phase research
    phase_questions = {"Q8", "M3", "M6", "M7", "M8", "S4"}
    # Execution questions need trade truth
    execution_questions = {"Q11", "Q12", "Q16", "Q17", "X1", "X4", "X5"}
    # Risk/ruin questions need full outcome + sample
    risk_ruin_questions = {"R3", "R4", "R5"}
    # Promotion intelligence needs validated EV + strategy + lineage
    promotion_questions = {"P1", "L7"}

    if qid in ev_questions:
        outcome_pct = coverage.get("outcome", 0.0)
        strategy_pct = coverage.get("strategy", 0.0)
        horizon_pct = coverage.get("trade_horizon", 0.0)
        reqs.append(GateRequirement("outcome", _GATE_OUTCOME, outcome_pct, outcome_pct >= _GATE_OUTCOME))
        reqs.append(GateRequirement("strategy", _GATE_STRATEGY, strategy_pct, strategy_pct >= _GATE_STRATEGY))
        reqs.append(GateRequirement("trade_horizon", _GATE_HORIZON, horizon_pct, horizon_pct >= _GATE_HORIZON))
        reqs.append(GateRequirement("no_contamination", 0.0, coverage.get("contamination", 0.0),
                                    coverage.get("contamination", 0.0) == 0.0))
        reqs.append(GateRequirement("min_samples_200", 200.0, coverage.get("record_count", 0.0),
                                    coverage.get("record_count", 0.0) >= _MIN_SAMPLES_PROMOTE))

    if qid in strategy_questions:
        strategy_pct = coverage.get("strategy", 0.0)
        reqs.append(GateRequirement("strategy", _GATE_STRATEGY, strategy_pct, strategy_pct >= _GATE_STRATEGY))

    if qid in regime_questions:
        regime_pct = coverage.get("h4_regime", 0.0)
        reqs.append(GateRequirement("h4_regime", _GATE_REGIME, regime_pct, regime_pct >= _GATE_REGIME))

    if qid in phase_questions:
        phase_pct = coverage.get("market_phase", 0.0)
        reqs.append(GateRequirement("market_phase", _GATE_PHASE, phase_pct, phase_pct >= _GATE_PHASE))

    if qid in execution_questions:
        # Execution research needs trade_truth (approximated as 0 if not enough)
        # Use a simple "has any trade truth" gate
        reqs.append(GateRequirement("trade_truth", 0.5, 0.0, False))

    if qid in risk_ruin_questions:
        outcome_pct = coverage.get("outcome", 0.0)
        reqs.append(GateRequirement("outcome", _GATE_OUTCOME, outcome_pct, outcome_pct >= _GATE_OUTCOME))
        reqs.append(GateRequirement("min_samples_200", 200.0, coverage.get("record_count", 0.0),
                                    coverage.get("record_count", 0.0) >= _MIN_SAMPLES_PROMOTE))

    if qid in promotion_questions:
        outcome_pct = coverage.get("outcome", 0.0)
        strategy_pct = coverage.get("strategy", 0.0)
        reqs.append(GateRequirement("outcome", _GATE_OUTCOME, outcome_pct, outcome_pct >= _GATE_OUTCOME))
        reqs.append(GateRequirement("strategy", _GATE_STRATEGY, strategy_pct, strategy_pct >= _GATE_STRATEGY))
        reqs.append(GateRequirement("no_contamination", 0.0, coverage.get("contamination", 0.0),
                                    coverage.get("contamination", 0.0) == 0.0))
        reqs.append(GateRequirement("min_samples_200", 200.0, coverage.get("record_count", 0.0),
                                    coverage.get("record_count", 0.0) >= _MIN_SAMPLES_PROMOTE))

    return reqs


def _classify_decision(
    qid: str,
    historical_status: str,
    all_met: bool,
    unmet: list[GateRequirement],
    coverage: dict[str, float],
    data_health: DataHealth,
) -> tuple[ResearchDecisionStatus, str, bool]:
    """
    Classify current decision status.

    Returns: (status, confidence, can_change_strategy_logic)
    """
    # Invalidated hypotheses
    if historical_status in ("INVALIDATED", "REJECTED"):
        return ResearchDecisionStatus.INVALIDATED, "", False

    # Structurally blocked (execution questions without live data)
    if historical_status == "BLOCKED":
        return ResearchDecisionStatus.BLOCKED, "", False

    # Insufficient data
    if not all_met:
        return ResearchDecisionStatus.NEEDS_DATA, "INSUFFICIENT", False

    # All gates passed — determine action level
    record_count = int(coverage.get("record_count", 0))

    if record_count >= _MIN_SAMPLES_PROMOTE and data_health.contamination_count == 0:
        confidence = "HIGH"
    elif record_count >= _MIN_SAMPLES_MODIFY:
        confidence = "MEDIUM"
    elif record_count >= _MIN_SAMPLES_MONITOR:
        confidence = "LOW"
    else:
        confidence = "INSUFFICIENT"
        return ResearchDecisionStatus.NEEDS_DATA, confidence, False

    # Promotion-worthy questions
    promote_statuses = {"PROMOTE_CALIBRATION", "POSITIVE_EDGE", "WEIGHT_ADJUSTMENT"}
    if historical_status in promote_statuses and confidence == "HIGH":
        return ResearchDecisionStatus.PROMOTE, confidence, True

    # Completed research with sufficient confidence
    if historical_status in ("COMPLETE", "POSITIVE_EDGE") and confidence in ("HIGH", "MEDIUM"):
        return ResearchDecisionStatus.MODIFY, confidence, confidence == "HIGH"

    # Otherwise monitor
    return ResearchDecisionStatus.MONITOR, confidence, False


def _extract_historical_result(qid: str, info: dict[str, Any], report: dict[str, Any] | None) -> str:
    """Extract a human-readable historical result string."""
    recommendation = info.get("recommendation", "")

    if report:
        finding = report.get("finding", "")
        if finding and len(finding) <= 80:
            return finding
        # Try metrics
        metrics = report.get("metrics", {})
        ev = metrics.get("expected_value")
        if ev is not None:
            return f"EV {ev:+.4f}R"
        wr = metrics.get("win_rate")
        if wr is not None:
            return f"WR {wr:.1%}"

    # Fall back to recommendation
    if recommendation:
        return recommendation
    return info.get("status", "")


def _build_promotion_summary(
    coverage: dict[str, float],
    data_health: DataHealth,
    decisions: list[ResearchDecision],
) -> PromotionReadinessSummary:
    """Build overall promotion readiness from all decisions."""

    # Check core requirements
    lineage_ok = coverage.get("entity_id", 0.0) >= _GATE_LINEAGE
    strategy_ok = coverage.get("strategy", 0.0) >= _GATE_STRATEGY
    horizon_ok = coverage.get("trade_horizon", 0.0) >= _GATE_HORIZON
    regime_ok = coverage.get("h4_regime", 0.0) >= _GATE_REGIME
    no_contamination = data_health.contamination_count == 0
    sufficient_samples = data_health.record_count >= _MIN_SAMPLES_PROMOTE

    all_core_met = all([lineage_ok, strategy_ok, horizon_ok, no_contamination, sufficient_samples])

    # Required before changes
    required: list[str] = []
    if not lineage_ok:
        required.append("entity lineage accumulation")
    if not strategy_ok:
        required.append("clean strategy field")
    if not horizon_ok:
        required.append("horizon separation")
    if not regime_ok:
        required.append("MarketContext population (H4 regime)")
    if not no_contamination:
        required.append("contamination cleanup")
    if not sufficient_samples:
        required.append(f"minimum {_MIN_SAMPLES_PROMOTE} samples (current: {data_health.record_count})")

    # Check if deployment-safety questions (R3, R4, E5) have been answered
    deployment_safe_ids = {"R3", "R4", "E5", "P1"}
    deployment_answered = sum(1 for d in decisions if d.question_id in deployment_safe_ids and d.can_change_strategy_logic)
    if deployment_answered < len(deployment_safe_ids):
        required.append(f"deployment safety questions answered ({deployment_answered}/{len(deployment_safe_ids)} of R3/R4/E5/P1)")

    # Determine overall
    if all_core_met and deployment_answered >= 3:
        allowed = True
        reason = "All core requirements met. Strategy changes supported by evidence."
    elif all_core_met:
        allowed = False
        reason = "Core data requirements met but deployment safety questions unanswered."
    else:
        allowed = False
        reason = "Current dataset is post-migration incomplete."

    safe_actions = ["collect data", "monitor system", "validate pipelines"]
    unsafe_actions = ["remove patterns", "change scoring weights", "alter regime logic"]

    if allowed:
        safe_actions.extend(["promote validated findings", "adjust pattern weights", "tune EV thresholds"])
        unsafe_actions = ["deploy without shadow validation"]

    return PromotionReadinessSummary(
        strategy_changes_allowed=allowed,
        reason=reason,
        required_before_changes=required,
        safe_actions=safe_actions,
        unsafe_actions=unsafe_actions,
    )
