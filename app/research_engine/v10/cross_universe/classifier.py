"""
Cross-Universe Classifier.

Assigns deterministic structural classifications to cross-universe
comparisons based on explicit rules.

Does NOT invent explanations or causal claims. Only describes the
observed structural relationship between universes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from research_engine.v10.cross_universe.comparison import (
    CrossUniverseComparison,
    ComparisonDimension,
)


# ═══════════════════════════════════════════════════════════════════════════════
# CLASSIFICATION VOCABULARY
# ═══════════════════════════════════════════════════════════════════════════════


class Classification:
    """Deterministic structural classification labels."""
    # Alignment
    ALIGNED = "ALIGNED"
    CONTRADICTORY = "CONTRADICTORY"

    # Lifecycle completeness
    COMPLETE_LIFECYCLE = "COMPLETE_LIFECYCLE"
    PARTIAL_LIFECYCLE = "PARTIAL_LIFECYCLE"
    NO_EXECUTION = "NO_EXECUTION"

    # Decision × Risk
    DECISION_EXECUTE_RISK_APPROVED = "DECISION_EXECUTE_RISK_APPROVED"
    DECISION_EXECUTE_RISK_BLOCKED = "DECISION_EXECUTE_RISK_BLOCKED"
    DECISION_NO_TRADE = "DECISION_NO_TRADE"

    # Outcome presence
    OUTCOME_PRESENT = "OUTCOME_PRESENT"
    OUTCOME_MISSING = "OUTCOME_MISSING"
    OUTCOME_POSITIVE = "OUTCOME_POSITIVE"
    OUTCOME_NEGATIVE = "OUTCOME_NEGATIVE"

    # Missing data
    MISSING_DATA = "MISSING_DATA"
    UNDETERMINED = "UNDETERMINED"


@dataclass
class DimensionClassification:
    """Classification for one comparison dimension."""
    dimension_name: str
    classification: str
    rule: str  # Human-readable explanation of the classification rule
    confidence: str = "DETERMINISTIC"  # All rules are deterministic

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension_name": self.dimension_name,
            "classification": self.classification,
            "rule": self.rule,
            "confidence": self.confidence,
        }


@dataclass
class CrossUniverseClassification:
    """Complete classification result for one entity's cross-universe comparison."""
    entity_id: str
    lifecycle_classification: str = ""
    dimension_classifications: list[DimensionClassification] = field(default_factory=list)
    summary_classifications: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "lifecycle_classification": self.lifecycle_classification,
            "dimension_classifications": [d.to_dict() for d in self.dimension_classifications],
            "summary_classifications": self.summary_classifications,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# CLASSIFIER
# ═══════════════════════════════════════════════════════════════════════════════


class CrossUniverseClassifier:
    """
    Assigns deterministic structural classifications to cross-universe comparisons.

    Every classification is produced by an explicit rule.
    No probabilistic, model-based, or causal inference is performed.

    Usage:
        classifier = CrossUniverseClassifier()
        result = classifier.classify(comparison)
    """

    def classify(self, comparison: CrossUniverseComparison) -> CrossUniverseClassification:
        """Classify all dimensions of a cross-universe comparison."""
        result = CrossUniverseClassification(entity_id=comparison.entity_id)

        # Lifecycle classification
        result.lifecycle_classification = self._classify_lifecycle(comparison)

        # Per-dimension classifications
        for dim in comparison.dimensions:
            dc = self._classify_dimension(dim)
            if dc:
                result.dimension_classifications.append(dc)

        # Summary: collect unique classifications
        result.summary_classifications = list({
            dc.classification for dc in result.dimension_classifications
        })
        if result.lifecycle_classification:
            result.summary_classifications.insert(0, result.lifecycle_classification)

        return result

    def _classify_lifecycle(self, comparison: CrossUniverseComparison) -> str:
        """Classify the overall lifecycle completeness."""
        if comparison.trace_status == "EMPTY":
            return Classification.MISSING_DATA

        present = comparison.summary.get("present_universes", [])

        has_execution = "execution" in present
        has_outcome = "outcome" in present
        has_decision = "decision" in present

        if has_execution and has_outcome and has_decision:
            return Classification.COMPLETE_LIFECYCLE
        elif has_decision and not has_execution:
            return Classification.NO_EXECUTION
        else:
            return Classification.PARTIAL_LIFECYCLE

    def _classify_dimension(self, dim: ComparisonDimension) -> DimensionClassification | None:
        """Classify a single comparison dimension using deterministic rules."""
        if dim.name == "decision_vs_risk":
            return self._classify_decision_risk(dim)
        elif dim.name == "execution_vs_outcome":
            return self._classify_execution_outcome(dim)
        elif dim.name == "decision_vs_outcome":
            return self._classify_decision_outcome(dim)
        elif dim.name == "risk_vs_execution":
            return self._classify_risk_execution(dim)
        elif dim.name == "strategy_vs_market":
            return self._classify_strategy_market(dim)
        elif dim.name == "market_vs_outcome":
            return self._classify_market_outcome(dim)
        return None

    # ─── DIMENSION RULES ──────────────────────────────────────────────────────

    def _classify_decision_risk(self, dim: ComparisonDimension) -> DimensionClassification:
        """
        Rule: Decision action vs Risk control result.

        DECISION_EXECUTE_RISK_APPROVED: Decision=EXECUTE AND Risk=APPROVED
        DECISION_EXECUTE_RISK_BLOCKED: Decision=EXECUTE AND Risk=BLOCKED (contradictory)
        DECISION_NO_TRADE: Decision=NO_TRADE (risk may or may not have evaluated)
        """
        if not dim.comparable:
            return DimensionClassification(
                dimension_name=dim.name,
                classification=Classification.MISSING_DATA,
                rule="One or more universes missing from comparison",
            )

        action = dim.values.get("decision_action")
        risk_result = dim.values.get("risk_control_result")

        if action == "NO_TRADE":
            return DimensionClassification(
                dimension_name=dim.name,
                classification=Classification.DECISION_NO_TRADE,
                rule="Decision terminal action is NO_TRADE",
            )

        if action == "EXECUTE" and risk_result == "APPROVED":
            return DimensionClassification(
                dimension_name=dim.name,
                classification=Classification.DECISION_EXECUTE_RISK_APPROVED,
                rule="Decision=EXECUTE AND Risk=APPROVED",
            )

        if action == "EXECUTE" and risk_result == "BLOCKED":
            return DimensionClassification(
                dimension_name=dim.name,
                classification=Classification.DECISION_EXECUTE_RISK_BLOCKED,
                rule="Decision=EXECUTE AND Risk=BLOCKED (contradictory state)",
            )

        return DimensionClassification(
            dimension_name=dim.name,
            classification=Classification.UNDETERMINED,
            rule=f"Unrecognised combination: action={action}, risk={risk_result}",
        )

    def _classify_execution_outcome(self, dim: ComparisonDimension) -> DimensionClassification:
        """
        Rule: Execution presence vs Outcome presence.

        OUTCOME_PRESENT: Both execution and outcome exist
        OUTCOME_MISSING: Execution exists but outcome doesn't
        NO_EXECUTION: No execution record
        """
        exe_present = dim.values.get("execution_present", False)
        out_present = dim.values.get("outcome_present", False)

        if exe_present and out_present:
            r = dim.values.get("outcome_r_multiple")
            if r is not None and r > 0:
                return DimensionClassification(
                    dimension_name=dim.name,
                    classification=Classification.OUTCOME_POSITIVE,
                    rule="Execution completed with positive outcome",
                )
            elif r is not None and r <= 0:
                return DimensionClassification(
                    dimension_name=dim.name,
                    classification=Classification.OUTCOME_NEGATIVE,
                    rule="Execution completed with negative outcome",
                )
            return DimensionClassification(
                dimension_name=dim.name,
                classification=Classification.OUTCOME_PRESENT,
                rule="Execution and outcome both present",
            )

        if exe_present and not out_present:
            return DimensionClassification(
                dimension_name=dim.name,
                classification=Classification.OUTCOME_MISSING,
                rule="Execution present but outcome missing",
            )

        return DimensionClassification(
            dimension_name=dim.name,
            classification=Classification.NO_EXECUTION,
            rule="No execution record present",
        )

    def _classify_decision_outcome(self, dim: ComparisonDimension) -> DimensionClassification:
        """
        Rule: Decision action vs Outcome result.

        Reports the observed relationship between decision and economic result.
        Does NOT claim the decision was "correct" or "incorrect".
        """
        if not dim.comparable:
            return DimensionClassification(
                dimension_name=dim.name,
                classification=Classification.MISSING_DATA,
                rule="Decision or Outcome data missing",
            )

        action = dim.values.get("decision_action")
        r = dim.values.get("outcome_r_multiple")

        if action == "EXECUTE" and r is not None:
            if r > 0:
                return DimensionClassification(
                    dimension_name=dim.name,
                    classification=Classification.OUTCOME_POSITIVE,
                    rule="Decision=EXECUTE, Outcome positive",
                )
            else:
                return DimensionClassification(
                    dimension_name=dim.name,
                    classification=Classification.OUTCOME_NEGATIVE,
                    rule="Decision=EXECUTE, Outcome negative",
                )

        return DimensionClassification(
            dimension_name=dim.name,
            classification=Classification.UNDETERMINED,
            rule=f"action={action}, r_multiple={r}",
        )

    def _classify_risk_execution(self, dim: ComparisonDimension) -> DimensionClassification:
        """
        Rule: Risk authorisation vs Execution presence.

        ALIGNED: Risk approved AND execution occurred
        CONTRADICTORY: Risk blocked AND execution occurred
        NO_EXECUTION: Risk approved but no execution
        """
        if not dim.comparable:
            return DimensionClassification(
                dimension_name=dim.name,
                classification=Classification.MISSING_DATA,
                rule="Risk data missing",
            )

        risk_result = dim.values.get("risk_control_result")
        exe_present = dim.values.get("execution_present", False)

        if risk_result == "APPROVED" and exe_present:
            return DimensionClassification(
                dimension_name=dim.name,
                classification=Classification.ALIGNED,
                rule="Risk approved AND execution present",
            )

        if risk_result == "BLOCKED" and exe_present:
            return DimensionClassification(
                dimension_name=dim.name,
                classification=Classification.CONTRADICTORY,
                rule="Risk blocked BUT execution present (unexpected)",
            )

        if risk_result == "APPROVED" and not exe_present:
            return DimensionClassification(
                dimension_name=dim.name,
                classification=Classification.NO_EXECUTION,
                rule="Risk approved but no execution record",
            )

        if risk_result == "BLOCKED" and not exe_present:
            return DimensionClassification(
                dimension_name=dim.name,
                classification=Classification.ALIGNED,
                rule="Risk blocked AND no execution (expected)",
            )

        return DimensionClassification(
            dimension_name=dim.name,
            classification=Classification.UNDETERMINED,
            rule=f"risk={risk_result}, execution_present={exe_present}",
        )

    def _classify_strategy_market(self, dim: ComparisonDimension) -> DimensionClassification:
        """
        Rule: Strategy vs Market — reports observed combination.

        Does NOT determine whether the strategy is suitable for the market.
        That is a cross-universe research conclusion requiring Outcome evidence.
        """
        if not dim.comparable:
            return DimensionClassification(
                dimension_name=dim.name,
                classification=Classification.MISSING_DATA,
                rule="Strategy or Market data missing",
            )

        # Report as observed combination — no suitability judgement
        return DimensionClassification(
            dimension_name=dim.name,
            classification=Classification.ALIGNED,
            rule="Strategy and Market observations both present (no suitability judgement)",
        )

    def _classify_market_outcome(self, dim: ComparisonDimension) -> DimensionClassification:
        """
        Rule: Market state vs Outcome.

        Reports whether outcome data exists alongside market data.
        Does NOT attribute outcome to market state (that is causal inference).
        """
        if not dim.comparable:
            return DimensionClassification(
                dimension_name=dim.name,
                classification=Classification.MISSING_DATA,
                rule="Market or Outcome data missing",
            )

        r = dim.values.get("outcome_r_multiple")
        if r is not None and r > 0:
            return DimensionClassification(
                dimension_name=dim.name,
                classification=Classification.OUTCOME_POSITIVE,
                rule="Market state observed, outcome positive (no causal claim)",
            )
        elif r is not None and r <= 0:
            return DimensionClassification(
                dimension_name=dim.name,
                classification=Classification.OUTCOME_NEGATIVE,
                rule="Market state observed, outcome negative (no causal claim)",
            )

        return DimensionClassification(
            dimension_name=dim.name,
            classification=Classification.UNDETERMINED,
            rule="Market present but outcome r_multiple unavailable",
        )
