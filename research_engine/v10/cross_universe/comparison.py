"""
Cross-Universe Comparison.

Structures observations from multiple universes into explicit
cross-universe comparisons that preserve ownership boundaries.

Does NOT interpret or explain relationships. Only exposes them
in a comparable structure for downstream classification.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from research_engine.v10.cross_universe.tracer import LifecycleTrace, UniversePresence


# ═══════════════════════════════════════════════════════════════════════════════
# COMPARISON DIMENSIONS
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class ComparisonDimension:
    """One dimension of a cross-universe comparison."""
    name: str
    universes_involved: list[str]
    values: dict[str, Any]  # universe → extracted value
    comparable: bool = True  # False if one or more values are missing

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "universes_involved": self.universes_involved,
            "values": self.values,
            "comparable": self.comparable,
        }


@dataclass
class CrossUniverseComparison:
    """
    Structured cross-universe comparison for one entity.

    Exposes relevant facts from each universe in a comparable structure
    without reinterpreting ownership.
    """
    entity_id: str
    trace_status: str
    dimensions: list[ComparisonDimension] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "trace_status": self.trace_status,
            "dimensions": [d.to_dict() for d in self.dimensions],
            "summary": self.summary,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# COMPARISON BUILDER
# ═══════════════════════════════════════════════════════════════════════════════


class ComparisonBuilder:
    """
    Builds structured cross-universe comparisons from lifecycle traces.

    Extracts relevant fields from each universe and places them into
    explicit comparison dimensions that downstream classifiers can consume.

    Usage:
        builder = ComparisonBuilder()
        comparison = builder.compare(trace)
    """

    def compare(self, trace: LifecycleTrace) -> CrossUniverseComparison:
        """
        Build a cross-universe comparison from a lifecycle trace.

        Extracts key facts from each universe and structures them into
        comparable dimensions.
        """
        if trace.trace_status == "EMPTY":
            return CrossUniverseComparison(
                entity_id=trace.entity_id,
                trace_status="EMPTY",
            )

        dimensions = []

        # Decision action vs Risk control result
        dim = self._compare_decision_risk(trace)
        if dim:
            dimensions.append(dim)

        # Strategy vs Market alignment
        dim = self._compare_strategy_market(trace)
        if dim:
            dimensions.append(dim)

        # Risk authorisation vs Execution presence
        dim = self._compare_risk_execution(trace)
        if dim:
            dimensions.append(dim)

        # Execution vs Outcome presence
        dim = self._compare_execution_outcome(trace)
        if dim:
            dimensions.append(dim)

        # Decision action vs Outcome result
        dim = self._compare_decision_outcome(trace)
        if dim:
            dimensions.append(dim)

        # Market state vs Outcome result
        dim = self._compare_market_outcome(trace)
        if dim:
            dimensions.append(dim)

        # Build summary
        summary = {
            "present_universes": [
                k for k, v in trace.universes.items()
                if v.presence == UniversePresence.PRESENT
            ],
            "missing_universes": [
                k for k, v in trace.universes.items()
                if v.presence == UniversePresence.MISSING
            ],
            "comparable_dimensions": len([d for d in dimensions if d.comparable]),
            "incomplete_dimensions": len([d for d in dimensions if not d.comparable]),
        }

        return CrossUniverseComparison(
            entity_id=trace.entity_id,
            trace_status=trace.trace_status,
            dimensions=dimensions,
            summary=summary,
        )

    # ─── DIMENSION EXTRACTORS ─────────────────────────────────────────────────

    def _compare_decision_risk(self, trace: LifecycleTrace) -> ComparisonDimension | None:
        """Compare Decision terminal action against Risk control result."""
        decision = self._get_record(trace, "decision")
        risk = self._get_record(trace, "risk")

        values: dict[str, Any] = {}
        comparable = True

        if decision:
            values["decision_action"] = decision.get("action")
            values["decision_terminal_stage"] = decision.get("terminal_stage")
        else:
            comparable = False

        if risk:
            values["risk_control_result"] = risk.get("risk_control_result")
            values["risk_control_reason"] = risk.get("risk_control_reason")
        else:
            comparable = False

        if not values:
            return None

        return ComparisonDimension(
            name="decision_vs_risk",
            universes_involved=["DECISION", "RISK"],
            values=values,
            comparable=comparable,
        )

    def _compare_strategy_market(self, trace: LifecycleTrace) -> ComparisonDimension | None:
        """Compare Strategy selection against Market state."""
        strategy = self._get_record(trace, "strategy")
        market = self._get_record(trace, "market")

        values: dict[str, Any] = {}
        comparable = True

        if strategy:
            values["strategy_family"] = strategy.get("family")
            values["strategy_confidence"] = strategy.get("confidence")
        else:
            comparable = False

        if market:
            values["market_regime"] = market.get("regime")
            values["market_volatility"] = market.get("volatility_state")
        else:
            comparable = False

        if not values:
            return None

        return ComparisonDimension(
            name="strategy_vs_market",
            universes_involved=["STRATEGY", "MARKET"],
            values=values,
            comparable=comparable,
        )

    def _compare_risk_execution(self, trace: LifecycleTrace) -> ComparisonDimension | None:
        """Compare Risk authorisation against Execution presence."""
        risk = self._get_record(trace, "risk")
        execution = self._get_record(trace, "execution")

        values: dict[str, Any] = {}
        comparable = True

        if risk:
            values["risk_control_result"] = risk.get("risk_control_result")
        else:
            comparable = False

        values["execution_present"] = execution is not None
        if execution:
            values["execution_r_multiple"] = execution.get("r_multiple")

        if not risk and not execution:
            return None

        return ComparisonDimension(
            name="risk_vs_execution",
            universes_involved=["RISK", "EXECUTION"],
            values=values,
            comparable=comparable,
        )

    def _compare_execution_outcome(self, trace: LifecycleTrace) -> ComparisonDimension | None:
        """Compare Execution presence against Outcome."""
        execution = self._get_record(trace, "execution")
        outcome = self._get_record(trace, "outcome")

        values: dict[str, Any] = {}

        values["execution_present"] = execution is not None
        values["outcome_present"] = outcome is not None

        if outcome:
            values["outcome_r_multiple"] = outcome.get("r_multiple")
            values["outcome_exit_reason"] = outcome.get("exit_reason")

        if not execution and not outcome:
            return None

        return ComparisonDimension(
            name="execution_vs_outcome",
            universes_involved=["EXECUTION", "OUTCOME"],
            values=values,
            comparable=(execution is not None),
        )

    def _compare_decision_outcome(self, trace: LifecycleTrace) -> ComparisonDimension | None:
        """Compare Decision action against realised Outcome."""
        decision = self._get_record(trace, "decision")
        outcome = self._get_record(trace, "outcome")

        values: dict[str, Any] = {}
        comparable = True

        if decision:
            values["decision_action"] = decision.get("action")
            values["decision_score"] = decision.get("score")
        else:
            comparable = False

        if outcome:
            values["outcome_r_multiple"] = outcome.get("r_multiple")
        else:
            comparable = False

        if not values:
            return None

        return ComparisonDimension(
            name="decision_vs_outcome",
            universes_involved=["DECISION", "OUTCOME"],
            values=values,
            comparable=comparable,
        )

    def _compare_market_outcome(self, trace: LifecycleTrace) -> ComparisonDimension | None:
        """Compare Market state against realised Outcome."""
        market = self._get_record(trace, "market")
        outcome = self._get_record(trace, "outcome")

        values: dict[str, Any] = {}
        comparable = True

        if market:
            values["market_regime"] = market.get("regime")
            values["market_session"] = market.get("session")
        else:
            comparable = False

        if outcome:
            values["outcome_r_multiple"] = outcome.get("r_multiple")
        else:
            comparable = False

        if not values:
            return None

        return ComparisonDimension(
            name="market_vs_outcome",
            universes_involved=["MARKET", "OUTCOME"],
            values=values,
            comparable=comparable,
        )

    # ─── HELPERS ──────────────────────────────────────────────────────────────

    def _get_record(self, trace: LifecycleTrace, universe_key: str) -> dict[str, Any] | None:
        """Get the record from a trace for a given universe (lowercase key)."""
        obs = trace.universes.get(universe_key)
        if obs and obs.presence == UniversePresence.PRESENT:
            return obs.record
        return None
