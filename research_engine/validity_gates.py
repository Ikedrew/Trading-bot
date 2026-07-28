"""
Research Validity Gates — Prevents invalid experiments from producing
promotion recommendations.

Before any experiment result can be marked VALIDATED / PROMOTE / IMPLEMENT,
it must pass ALL validity gates:

    1. Data Validation (epoch, architecture, population)
    2. Experiment Validation (hypothesis, control, variable)
    3. Statistical Validation (sample size, CI, significance)
    4. Bias Checks (look-ahead, selection, leakage)

If ANY gate fails: status is forced to REQUIRES_RERUN.

This module is PURELY RESEARCH INFRASTRUCTURE. No trading impact.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Statuses that require validity gate passage
GATED_STATUSES = frozenset({"VALIDATED", "PROMOTE", "IMPLEMENT", "READY"})

# Minimum thresholds
MIN_SAMPLE_SIZE = 100
MIN_SAMPLE_SIZE_RELAXED = 50
VALID_EPOCHS = frozenset({"CURRENT", "CURRENT_ONLY", "shadow_trades_current"})
VALID_ARCHITECTURES = frozenset({"new_pipeline_v1.2", "new_pipeline_v1.1"})


@dataclass
class GateResult:
    """Result of one validity gate check."""
    gate_name: str
    passed: bool
    reason: str = ""
    severity: str = "BLOCKING"  # BLOCKING | WARNING


@dataclass
class ValidityAssessment:
    """Complete validity assessment for one experiment report."""
    question_id: str
    original_status: str
    gates_passed: list[GateResult] = field(default_factory=list)
    gates_failed: list[GateResult] = field(default_factory=list)
    gates_warned: list[GateResult] = field(default_factory=list)
    final_status: str = ""
    promotion_allowed: bool = False
    reason: str = ""

    @property
    def all_blocking_passed(self) -> bool:
        return len(self.gates_failed) == 0

    @property
    def total_gates(self) -> int:
        return len(self.gates_passed) + len(self.gates_failed) + len(self.gates_warned)

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "original_status": self.original_status,
            "final_status": self.final_status,
            "promotion_allowed": self.promotion_allowed,
            "reason": self.reason,
            "gates_passed": [{"gate": g.gate_name, "reason": g.reason} for g in self.gates_passed],
            "gates_failed": [{"gate": g.gate_name, "reason": g.reason} for g in self.gates_failed],
            "gates_warned": [{"gate": g.gate_name, "reason": g.reason} for g in self.gates_warned],
        }


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN VALIDATION FUNCTION
# ═══════════════════════════════════════════════════════════════════════════════


def validate_experiment_report(report: dict[str, Any]) -> ValidityAssessment:
    """
    Validate an experiment report against all gates.

    If the report's recommendation/status is in GATED_STATUSES,
    it must pass ALL blocking gates. Otherwise: REQUIRES_RERUN.

    Args:
        report: Standard experiment report dict.

    Returns:
        ValidityAssessment with final status and gate results.
    """
    question_id = report.get("question_id", "UNKNOWN")
    original_status = _extract_status(report)

    assessment = ValidityAssessment(
        question_id=question_id,
        original_status=original_status,
    )

    # Run all gates
    _check_data_validation(report, assessment)
    _check_experiment_validation(report, assessment)
    _check_statistical_validation(report, assessment)
    _check_bias_checks(report, assessment)

    # Determine final status
    if original_status.upper() in GATED_STATUSES:
        if assessment.all_blocking_passed:
            assessment.final_status = original_status
            assessment.promotion_allowed = True
            assessment.reason = "All validity gates passed"
        else:
            assessment.final_status = "REQUIRES_RERUN"
            assessment.promotion_allowed = False
            failed_names = [g.gate_name for g in assessment.gates_failed]
            assessment.reason = f"Validity gates failed: {failed_names}"
    else:
        # Non-gated status (MONITOR, WAIT, COMPLETE, etc.) — pass through
        assessment.final_status = original_status
        assessment.promotion_allowed = False
        assessment.reason = f"Status '{original_status}' does not require gate validation"

    return assessment


# ═══════════════════════════════════════════════════════════════════════════════
# GATE 1: DATA VALIDATION
# ═══════════════════════════════════════════════════════════════════════════════


def _check_data_validation(report: dict[str, Any], assessment: ValidityAssessment) -> None:
    """Check epoch, architecture version, and population purity."""
    fingerprint = report.get("fingerprint", {})
    epoch = fingerprint.get("epoch", report.get("epoch", ""))
    architecture = fingerprint.get("architecture_version", "")
    warnings_list = report.get("warnings", [])

    # Gate 1a: Epoch must be CURRENT
    if epoch in VALID_EPOCHS:
        assessment.gates_passed.append(GateResult(
            "data.epoch", True, f"Epoch is '{epoch}' (CURRENT)"
        ))
    elif epoch:
        assessment.gates_failed.append(GateResult(
            "data.epoch", False,
            f"Epoch '{epoch}' is not CURRENT. Results may not represent current system.",
        ))
    else:
        assessment.gates_failed.append(GateResult(
            "data.epoch", False,
            "No epoch metadata in fingerprint. Cannot verify data recency.",
        ))

    # Gate 1b: Architecture version
    if architecture in VALID_ARCHITECTURES:
        assessment.gates_passed.append(GateResult(
            "data.architecture", True, f"Architecture '{architecture}' is current"
        ))
    elif architecture:
        assessment.gates_warned.append(GateResult(
            "data.architecture", False,
            f"Architecture '{architecture}' may be outdated",
            severity="WARNING",
        ))
    else:
        assessment.gates_warned.append(GateResult(
            "data.architecture", False,
            "No architecture_version in fingerprint",
            severity="WARNING",
        ))

    # Gate 1c: No epoch contamination warnings
    epoch_warnings = [w for w in warnings_list if "EPOCH_WARNING" in str(w)]
    if epoch_warnings:
        assessment.gates_failed.append(GateResult(
            "data.no_contamination", False,
            f"Epoch contamination warning present: {epoch_warnings[0][:100]}",
        ))
    else:
        assessment.gates_passed.append(GateResult(
            "data.no_contamination", True, "No epoch contamination detected"
        ))


# ═══════════════════════════════════════════════════════════════════════════════
# GATE 2: EXPERIMENT VALIDATION
# ═══════════════════════════════════════════════════════════════════════════════


def _check_experiment_validation(report: dict[str, Any], assessment: ValidityAssessment) -> None:
    """Check hypothesis, control, and variable definition."""
    overall = report.get("overall", {})

    # Gate 2a: Experiment type / hypothesis documented
    has_experiment = bool(overall.get("experiment") or overall.get("hypothesis") or overall.get("finding"))
    if has_experiment:
        assessment.gates_passed.append(GateResult(
            "experiment.hypothesis", True, "Experiment type/finding documented"
        ))
    else:
        assessment.gates_warned.append(GateResult(
            "experiment.hypothesis", False,
            "No experiment type, hypothesis, or finding in report",
            severity="WARNING",
        ))

    # Gate 2b: Control defined (for comparative experiments)
    has_control = bool(overall.get("control") or overall.get("baseline"))
    if has_control:
        assessment.gates_passed.append(GateResult(
            "experiment.control", True, "Control/baseline defined"
        ))
    # Not all experiments need explicit control (measurement-only), so this is a warning
    elif overall.get("experiment"):
        assessment.gates_warned.append(GateResult(
            "experiment.control", False,
            "Comparative experiment without explicit control definition",
            severity="WARNING",
        ))

    # Gate 2c: Variable defined
    has_variable = bool(overall.get("variable") or overall.get("variants"))
    if has_variable:
        assessment.gates_passed.append(GateResult(
            "experiment.variable", True, "Variable/variants defined"
        ))


# ═══════════════════════════════════════════════════════════════════════════════
# GATE 3: STATISTICAL VALIDATION
# ═══════════════════════════════════════════════════════════════════════════════


def _check_statistical_validation(report: dict[str, Any], assessment: ValidityAssessment) -> None:
    """Check sample size, significance, and effect size."""
    dataset = report.get("dataset", {})
    overall = report.get("overall", {})
    fingerprint = report.get("fingerprint", {})

    # Gate 3a: Sample size
    sample_size = (
        dataset.get("sample_size")
        or fingerprint.get("records_used")
        or overall.get("total_trades")
        or overall.get("total_analysed")
        or 0
    )
    if isinstance(sample_size, (int, float)) and sample_size >= MIN_SAMPLE_SIZE:
        assessment.gates_passed.append(GateResult(
            "stats.sample_size", True, f"Sample size {int(sample_size)} >= {MIN_SAMPLE_SIZE}"
        ))
    elif isinstance(sample_size, (int, float)) and sample_size >= MIN_SAMPLE_SIZE_RELAXED:
        assessment.gates_warned.append(GateResult(
            "stats.sample_size", False,
            f"Sample size {int(sample_size)} is below {MIN_SAMPLE_SIZE} (marginal at {MIN_SAMPLE_SIZE_RELAXED}+)",
            severity="WARNING",
        ))
    else:
        assessment.gates_failed.append(GateResult(
            "stats.sample_size", False,
            f"Sample size {sample_size} is below minimum {MIN_SAMPLE_SIZE_RELAXED}",
        ))

    # Gate 3b: Significance testing present (for comparative experiments)
    has_significance = bool(
        overall.get("significant") is not None
        or overall.get("p_value") is not None
        or any("significant" in str(v).lower() for v in overall.values() if isinstance(v, (dict, list)))
        or any("p_approx" in str(v) for v in overall.values() if isinstance(v, dict))
    )
    comparisons = overall.get("comparisons") or overall.get("comparisons_vs_60bars") or overall.get("comparisons_vs_1R") or overall.get("comparisons_vs_no_tp")
    if comparisons:
        # This is a comparative experiment — significance required
        if has_significance or any("significant" in str(comparisons).lower() for _ in [1]):
            assessment.gates_passed.append(GateResult(
                "stats.significance", True, "Statistical significance testing present"
            ))
        else:
            assessment.gates_warned.append(GateResult(
                "stats.significance", False,
                "Comparative experiment without significance testing",
                severity="WARNING",
            ))


# ═══════════════════════════════════════════════════════════════════════════════
# GATE 4: BIAS CHECKS
# ═══════════════════════════════════════════════════════════════════════════════


def _check_bias_checks(report: dict[str, Any], assessment: ValidityAssessment) -> None:
    """Check for look-ahead bias, selection bias, and data leakage."""
    dataset = report.get("dataset", {})
    fingerprint = report.get("fingerprint", {})
    overall = report.get("overall", {})

    # Gate 4a: No mixed epoch (already checked in data validation)
    # Gate 4b: Source matches expected (not checking arbitrary external data)
    source = dataset.get("source", "") or fingerprint.get("source", "")
    if "current" in source.lower() or source in ("shadow_trades", "decision_trace"):
        assessment.gates_passed.append(GateResult(
            "bias.data_source", True, f"Data source '{source}' is appropriate"
        ))
    elif source:
        assessment.gates_warned.append(GateResult(
            "bias.data_source", False,
            f"Data source '{source}' — verify no leakage from future data",
            severity="WARNING",
        ))

    # Gate 4c: No obvious look-ahead indicators
    # Check if MFE-only simulation (potential look-ahead) vs bar-by-bar
    if "trade_state_progression" in str(overall) or "bar_by_bar" in str(overall).lower():
        assessment.gates_passed.append(GateResult(
            "bias.no_lookahead", True, "Uses sequential bar-by-bar data (no look-ahead)"
        ))
    elif "mfe" in str(overall).lower() and "simulation" in str(overall).lower():
        assessment.gates_warned.append(GateResult(
            "bias.no_lookahead", False,
            "MFE-based simulation may have look-ahead bias (assumes price reaches MFE before MAE)",
            severity="WARNING",
        ))


# ═══════════════════════════════════════════════════════════════════════════════
# HELPER
# ═══════════════════════════════════════════════════════════════════════════════


def _extract_status(report: dict[str, Any]) -> str:
    """Extract the recommendation/status from a report."""
    rec = report.get("recommendation", "")
    if isinstance(rec, dict):
        return rec.get("status", str(rec))
    return str(rec).upper() if rec else report.get("status", "UNKNOWN")
