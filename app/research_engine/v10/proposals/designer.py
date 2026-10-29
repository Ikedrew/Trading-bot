"""
Candidate Designer.

Transforms a research proposal into a concrete, governed candidate
definition suitable for experimentation.

Candidate design is an explicit governance input — not automatic
interpretation. A human or governance process specifies the concrete
change to test.

Supported change types:
    POPULATION_FILTER — exclude records matching a condition
    THRESHOLD_CHANGE — adjust a numeric threshold (expressed as filter)
    RISK_PARAMETER — mark for specialised risk evaluation
    POSITION_SIZING — mark for position-sizing evaluation
    CODE_CHANGE — explicitly blocked (requires implementation-specific testing)
    UNSUPPORTED — explicitly blocked

The designer validates that a candidate is internally coherent and
experimentable before allowing it to proceed to the experiment runner.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from research_engine.v10.proposals.model import (
    Candidate,
    ChangeProposal,
)


# ═══════════════════════════════════════════════════════════════════════════════
# CHANGE TYPE VOCABULARY
# ═══════════════════════════════════════════════════════════════════════════════


class ChangeType:
    POPULATION_FILTER = "POPULATION_FILTER"
    THRESHOLD_CHANGE = "THRESHOLD_CHANGE"
    RISK_PARAMETER = "RISK_PARAMETER"
    POSITION_SIZING = "POSITION_SIZING"
    CODE_CHANGE = "CODE_CHANGE"
    UNSUPPORTED = "UNSUPPORTED"


# Change types that can be evaluated by the current experiment runner
_EXPERIMENTABLE_TYPES = {ChangeType.POPULATION_FILTER, ChangeType.THRESHOLD_CHANGE}

# Filter operators supported in declarative configuration
_VALID_OPERATORS = {"==", "!=", ">", ">=", "<", "<=", "in", "not_in"}


# ═══════════════════════════════════════════════════════════════════════════════
# DESIGN STATUS
# ═══════════════════════════════════════════════════════════════════════════════


class DesignStatus:
    UNDEFINED = "UNDEFINED"
    DESIGNED = "DESIGNED"
    EXPERIMENTABLE = "EXPERIMENTABLE"
    BLOCKED = "BLOCKED"


# ═══════════════════════════════════════════════════════════════════════════════
# VALIDATION RESULT
# ═══════════════════════════════════════════════════════════════════════════════


class CandidateDesignResult:
    """Result of candidate design validation."""

    def __init__(self, valid: bool = True, candidate: Candidate | None = None, errors: list[str] | None = None):
        self.valid = valid
        self.candidate = candidate
        self.errors = errors or []

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "errors": self.errors,
            "candidate": self.candidate.to_dict() if self.candidate else None,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# CANDIDATE DESIGNER
# ═══════════════════════════════════════════════════════════════════════════════


class CandidateDesigner:
    """
    Creates concrete, validated candidate definitions from proposals.

    The designer validates:
        - proposal exists and is governed
        - change_type is recognized
        - configuration is valid for the change_type
        - target metric is specified
        - the candidate is internally coherent
        - experimentability is determined

    NEVER modifies the trading bot. NEVER executes trades.
    """

    def design(
        self,
        proposal: ChangeProposal,
        change_type: str,
        configuration: dict[str, Any],
        target_metric: str = "mean_r",
        expected_effect: str = "increase",
        minimum_improvement: float = 0.0,
        critical_metrics: list[str] | None = None,
        description: str = "",
        hypothesis: str = "",
    ) -> CandidateDesignResult:
        """
        Design a concrete candidate from a proposal + explicit specification.

        Args:
            proposal: The source ChangeProposal.
            change_type: One of ChangeType constants.
            configuration: Declarative change configuration.
            target_metric: Primary metric to evaluate.
            expected_effect: "increase", "decrease", "reduce_variance".
            minimum_improvement: Threshold for validation.
            critical_metrics: Metrics that must not regress.
            description: Human-readable description.
            hypothesis: What we expect to happen.

        Returns:
            CandidateDesignResult with either a valid candidate or errors.
        """
        errors: list[str] = []

        # Validate proposal
        if not proposal.proposal_id:
            errors.append("Proposal has no proposal_id")

        # Validate change type
        if change_type not in (
            ChangeType.POPULATION_FILTER, ChangeType.THRESHOLD_CHANGE,
            ChangeType.RISK_PARAMETER, ChangeType.POSITION_SIZING,
            ChangeType.CODE_CHANGE, ChangeType.UNSUPPORTED,
        ):
            errors.append(f"Unrecognized change_type: {change_type}")

        # Validate configuration for supported types
        if change_type in (ChangeType.POPULATION_FILTER, ChangeType.THRESHOLD_CHANGE):
            config_errors = self._validate_filter_config(configuration)
            errors.extend(config_errors)

        # Validate target metric
        if not target_metric:
            errors.append("target_metric is required")

        # Check experimentability
        if change_type in _EXPERIMENTABLE_TYPES and not errors:
            design_status = DesignStatus.EXPERIMENTABLE
        elif change_type in (ChangeType.CODE_CHANGE, ChangeType.UNSUPPORTED):
            design_status = DesignStatus.BLOCKED
            if not errors:
                errors.append(
                    f"Change type '{change_type}' cannot be evaluated by the current "
                    f"experiment infrastructure. Implementation-specific testing required."
                )
        elif errors:
            design_status = DesignStatus.BLOCKED
        else:
            design_status = DesignStatus.DESIGNED

        if errors:
            return CandidateDesignResult(valid=False, errors=errors)

        # Create candidate
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        cid = f"cand_{proposal.proposal_id}_{uuid.uuid4().hex[:6]}"

        candidate = Candidate(
            candidate_id=cid,
            proposal_id=proposal.proposal_id,
            candidate_version="1",
            description=description or proposal.problem_statement,
            hypothesis=hypothesis or proposal.hypothesis,
            change_type=change_type,
            configuration=configuration,
            target_metric=target_metric,
            expected_effect=expected_effect,
            minimum_improvement=minimum_improvement,
            critical_metrics=critical_metrics or [],
            design_status=design_status,
            source_proposal_id=proposal.proposal_id,
            source_finding_ids=proposal.source_finding_ids,
            source_feedback_ids=proposal.source_feedback_ids,
            universe_versions=proposal.universe_versions,
            population_versions=proposal.population_versions,
            created_at=now,
        )

        return CandidateDesignResult(valid=True, candidate=candidate)

    def build_filter(self, configuration: dict[str, Any]) -> Any:
        """
        Build a deterministic filter function from declarative configuration.

        Returns a callable suitable for ExperimentRunner.run_filter_experiment().

        Configuration format:
            {"field": "regime", "operator": "!=", "value": "TRANSITIONAL"}
        """
        field_name = configuration.get("field", "")
        operator = configuration.get("operator", "")
        value = configuration.get("value")

        if operator == "==":
            return lambda r: r.get(field_name) == value
        elif operator == "!=":
            return lambda r: r.get(field_name) != value
        elif operator == ">":
            return lambda r: (r.get(field_name) or 0) > value
        elif operator == ">=":
            return lambda r: (r.get(field_name) or 0) >= value
        elif operator == "<":
            return lambda r: (r.get(field_name) or 0) < value
        elif operator == "<=":
            return lambda r: (r.get(field_name) or 0) <= value
        elif operator == "in":
            return lambda r: r.get(field_name) in (value if isinstance(value, (list, set)) else [value])
        elif operator == "not_in":
            return lambda r: r.get(field_name) not in (value if isinstance(value, (list, set)) else [value])
        else:
            raise ValueError(f"Unsupported operator: {operator}")

    def _validate_filter_config(self, config: dict[str, Any]) -> list[str]:
        """Validate a declarative filter configuration."""
        errors: list[str] = []
        if not config.get("field"):
            errors.append("configuration.field is required for POPULATION_FILTER")
        if not config.get("operator"):
            errors.append("configuration.operator is required for POPULATION_FILTER")
        elif config["operator"] not in _VALID_OPERATORS:
            errors.append(f"configuration.operator '{config['operator']}' is not supported. Valid: {sorted(_VALID_OPERATORS)}")
        if "value" not in config:
            errors.append("configuration.value is required for POPULATION_FILTER")
        return errors
