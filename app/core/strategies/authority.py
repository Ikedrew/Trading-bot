"""
Strategy Authority — Determines which strategies are available and eligible.

CURRENT MODE: OBSERVATION
    All strategies are observable but NONE influence execution.
    The authority exists as architecture scaffold only.

This component:
    - Discovers available strategies
    - Retrieves strategies by family or context
    - Provides diagnostics
    - Prevents inactive strategies from becoming active
    - Does NOT make trading decisions
    - Does NOT connect to the decision engine
    - Does NOT activate any strategy

A strategy cannot become ACTIVE unless:
    - Sample size >= 100
    - Expectancy > 0
    - p < 0.05
    - Walk-forward validated
    - Out-of-sample validated
    - Manually promoted through decision gates
"""

from __future__ import annotations

import logging
from typing import Any

from core.strategy_family.models import StrategyFamily
from core.strategies.models import (
    EvidenceStatus,
    StrategyDefinition,
    StrategyEvaluationResult,
    StrategyStatus,
)
from core.strategies.registry import (
    STRATEGY_REGISTRY,
    get_active_strategies,
    get_all_strategies,
    get_status_distribution,
    get_strategies_by_family,
    get_strategies_by_status,
    get_strategy,
)

logger = logging.getLogger(__name__)


class StrategyAuthority:
    """
    Central authority for strategy discovery, evaluation, and lifecycle management.

    Operates in OBSERVATION mode by default — strategies are visible and
    classifiable but cannot influence trading decisions.

    Usage:
        authority = StrategyAuthority()
        strategies = authority.get_available_strategies()
        reversal = authority.get_by_family(StrategyFamily.REVERSAL)
        result = authority.evaluate_context(regime="RANGE", phase="REVERSAL")
    """

    def __init__(self, *, mode: str = "OBSERVATION") -> None:
        """
        Initialize authority.

        Args:
            mode: Operating mode.
                  "OBSERVATION" — strategies visible but inactive (current default)
                  "SHADOW" — strategies generate signals for research (future)
                  "ACTIVE" — strategies influence decisions (far future)
        """
        self._mode = mode

    @property
    def mode(self) -> str:
        return self._mode

    # ═══════════════════════════════════════════════════════════════════════════
    # STRATEGY DISCOVERY
    # ═══════════════════════════════════════════════════════════════════════════

    def get_available_strategies(self) -> list[StrategyDefinition]:
        """Return all registered strategies regardless of status."""
        return get_all_strategies()

    def get_by_family(self, family: StrategyFamily) -> list[StrategyDefinition]:
        """Return all strategies belonging to a given family."""
        return get_strategies_by_family(family)

    def get_by_status(self, status: StrategyStatus) -> list[StrategyDefinition]:
        """Return all strategies with a given lifecycle status."""
        return get_strategies_by_status(status)

    def get_by_id(self, strategy_id: str) -> StrategyDefinition | None:
        """Retrieve a specific strategy by its ID."""
        return get_strategy(strategy_id)

    # ═══════════════════════════════════════════════════════════════════════════
    # CONTEXT EVALUATION
    # ═══════════════════════════════════════════════════════════════════════════

    def evaluate_context(
        self,
        *,
        regime: str = "",
        phase: str = "",
        symbol: str = "",
    ) -> list[StrategyEvaluationResult]:
        """
        Evaluate which strategies are eligible for the current market context.

        In OBSERVATION mode: reports eligibility but does NOT activate anything.
        Eligibility is based on valid_market_phases matching the current phase.

        Args:
            regime: H4 regime (TRENDING/RANGE/TRANSITIONAL)
            phase: Market phase (IMPULSE/PULLBACK/CONSOLIDATION/EXHAUSTION/REVERSAL)
            symbol: Trading symbol

        Returns:
            List of StrategyEvaluationResult for all registered strategies.
        """
        results = []

        for strategy in get_all_strategies():
            eligible = self._check_eligibility(strategy, regime, phase)
            reasons = self._build_reasons(strategy, regime, phase, eligible)

            results.append(StrategyEvaluationResult(
                strategy_id=strategy.strategy_id,
                eligible=eligible,
                confidence=0.0,  # No confidence until research validates
                reasons=tuple(reasons),
                metadata={
                    "mode": self._mode,
                    "status": strategy.status.value,
                    "family": strategy.family_name,
                    "phase_match": phase in strategy.valid_market_phases,
                },
            ))

        return results

    def _check_eligibility(
        self,
        strategy: StrategyDefinition,
        regime: str,
        phase: str,
    ) -> bool:
        """
        Determine if a strategy is eligible for the given context.

        A strategy is eligible if:
        1. Its valid_market_phases includes the current phase (or phase is empty)
        2. It is NOT in DISABLED status

        Note: eligibility does NOT mean activation. In OBSERVATION mode,
        eligible strategies are reported but not executed.
        """
        if strategy.status == StrategyStatus.DISABLED:
            return False

        if not phase:
            return True  # No phase filter = all non-disabled eligible

        return phase in strategy.valid_market_phases

    def _build_reasons(
        self,
        strategy: StrategyDefinition,
        regime: str,
        phase: str,
        eligible: bool,
    ) -> list[str]:
        """Build human-readable reasons for eligibility decision."""
        reasons = []

        if strategy.status == StrategyStatus.DISABLED:
            reasons.append("Strategy is DISABLED")
            return reasons

        if eligible:
            if phase:
                reasons.append(f"Phase '{phase}' is in valid_market_phases")
            else:
                reasons.append("No phase filter applied — default eligible")
        else:
            reasons.append(
                f"Phase '{phase}' not in valid_market_phases "
                f"{list(strategy.valid_market_phases)}"
            )

        reasons.append(f"Mode: {self._mode} (no execution influence)")
        reasons.append(f"Status: {strategy.status.value}")

        if not strategy.trigger_patterns:
            reasons.append("WARNING: No trigger patterns defined in library")

        return reasons

    # ═══════════════════════════════════════════════════════════════════════════
    # RESEARCH PROMOTION GATE
    # ═══════════════════════════════════════════════════════════════════════════

    def load_research_validation(
        self,
        strategy_id: str,
        evidence: EvidenceStatus,
    ) -> bool:
        """
        Attempt to promote a strategy based on research evidence.

        A strategy can only progress if ALL activation criteria are met:
            - sample_size >= 100
            - expectancy_r > 0
            - p_value < 0.05
            - walk_forward_validated = True
            - out_of_sample_validated = True

        Args:
            strategy_id: ID of the strategy to evaluate.
            evidence: Research evidence status.

        Returns:
            True if strategy was promoted, False otherwise.

        NOTE: Even if promoted to VALIDATED, the strategy does NOT become ACTIVE.
              Activation requires a separate manual promotion step.
        """
        strategy = get_strategy(strategy_id)
        if strategy is None:
            logger.warning(
                "[STRATEGY_AUTHORITY] Cannot validate unknown strategy: '%s'",
                strategy_id,
            )
            return False

        if not evidence.meets_activation_criteria:
            logger.info(
                "[STRATEGY_AUTHORITY] Strategy '%s' does NOT meet activation criteria. "
                "Sample: %d, EV: %.3fR, p: %.4f, WF: %s, OOS: %s",
                strategy_id,
                evidence.sample_size,
                evidence.expectancy_r,
                evidence.p_value,
                evidence.walk_forward_validated,
                evidence.out_of_sample_validated,
            )
            return False

        # Evidence passes — strategy could be promoted to VALIDATED
        # But we do NOT mutate frozen dataclasses. We only report.
        logger.info(
            "[STRATEGY_AUTHORITY] Strategy '%s' PASSES activation criteria. "
            "Sample: %d, EV: %.3fR, p: %.4f. "
            "Promotion to VALIDATED is recommended.",
            strategy_id,
            evidence.sample_size,
            evidence.expectancy_r,
            evidence.p_value,
        )
        return True

    # ═══════════════════════════════════════════════════════════════════════════
    # SAFETY CHECKS
    # ═══════════════════════════════════════════════════════════════════════════

    def verify_no_active_strategies(self) -> bool:
        """
        Safety check: confirm no strategies are ACTIVE.

        This should always return True in the current architecture phase.
        If it returns False, something has been incorrectly modified.
        """
        active = get_active_strategies()
        if active:
            logger.error(
                "[STRATEGY_AUTHORITY] SAFETY VIOLATION: %d strategies are ACTIVE "
                "but no strategies should be active in this architecture phase: %s",
                len(active),
                [s.strategy_id for s in active],
            )
            return False
        return True

    # ═══════════════════════════════════════════════════════════════════════════
    # DIAGNOSTICS
    # ═══════════════════════════════════════════════════════════════════════════

    def get_diagnostic(self) -> dict[str, Any]:
        """Return diagnostic information about authority state."""
        all_strategies = get_all_strategies()
        distribution = get_status_distribution()

        strategies_by_family: dict[str, list[str]] = {}
        for s in all_strategies:
            family = s.family_name
            if family not in strategies_by_family:
                strategies_by_family[family] = []
            strategies_by_family[family].append(s.strategy_id)

        return {
            "mode": self._mode,
            "total_strategies": len(all_strategies),
            "status_distribution": distribution,
            "strategies_by_family": strategies_by_family,
            "active_count": distribution.get("ACTIVE", 0),
            "safety_check": self.verify_no_active_strategies(),
            "strategies": [
                {
                    "id": s.strategy_id,
                    "name": s.name,
                    "family": s.family_name,
                    "status": s.status.value,
                    "has_evidence": s.evidence_status.has_evidence,
                    "has_trigger_patterns": len(s.trigger_patterns) > 0,
                    "valid_phases": list(s.valid_market_phases),
                }
                for s in all_strategies
            ],
        }
