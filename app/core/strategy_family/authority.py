"""
Strategy Family Authority — Determines which strategy families are eligible
for the current market context.

CURRENT MODE: PASSTHROUGH
    All families are always eligible. No filtering occurs.
    The authority exists as architecture scaffold only.

FUTURE MODE: RESEARCH_GATED
    Phase research (M9, M10) validates which families work in which phases.
    Authority restricts pattern detection to eligible families.
    Requires: validated evidence, promoted through decision gates.

This component:
    - Does NOT make trading decisions
    - Does NOT block execution
    - Does NOT modify scores
    - Only CLASSIFIES eligibility for downstream consumption

Activation requires (from strategy_context_alignment.md):
    - n >= 100 in specific phase x family combination
    - EV significantly > 0 (p < 0.05)
    - Walk-forward validated
    - Promoted through research decision gates
"""

from __future__ import annotations

import logging
from typing import Any

from core.strategy_family.models import (
    EligibilityReason,
    FamilyEligibility,
    FamilySelectionResult,
    PatternClassification,
    ResearchValidation,
    StrategyFamily,
)
from core.strategy_family.registry import (
    FAMILY_REGISTRY,
    EMPTY_FAMILIES,
    classify_pattern,
    get_family_distribution,
)

logger = logging.getLogger(__name__)


class StrategyFamilyAuthority:
    """
    Determines which strategy families are eligible for the current market context.

    Current implementation: PASSTHROUGH (all families always eligible).
    Future: research-driven filtering based on validated phase x family evidence.

    Usage:
        authority = StrategyFamilyAuthority()

        # Classify a detected pattern
        classification = authority.classify("TWEEZER_BOTTOM")
        # -> PatternClassification(family=REVERSAL, confidence=1.0, ...)

        # Evaluate eligibility for a market context
        result = authority.evaluate(regime="RANGE", phase="REVERSAL")
        if result.is_eligible(StrategyFamily.REVERSAL):
            # proceed with reversal patterns
    """

    def __init__(self, *, mode: str = "PASSTHROUGH") -> None:
        """
        Initialize authority.

        Args:
            mode: Operating mode.
                  "PASSTHROUGH" — all families eligible (current default)
                  "RESEARCH_GATED" — uses research evidence (future)
        """
        self._mode = mode
        self._phase_family_rules: dict[str, list[StrategyFamily]] = {}
        self._research_validations: dict[str, ResearchValidation] = {}

    @property
    def mode(self) -> str:
        return self._mode

    # ═══════════════════════════════════════════════════════════════════════════
    # PATTERN CLASSIFICATION
    # ═══════════════════════════════════════════════════════════════════════════

    def classify(self, pattern: str) -> PatternClassification:
        """
        Classify a detected pattern into its strategy family.

        Args:
            pattern: The pattern name (e.g. "TWEEZER_BOTTOM", "HAMMER")

        Returns:
            PatternClassification with family, confidence, and reasoning.

        Example:
            >>> authority = StrategyFamilyAuthority()
            >>> result = authority.classify("TWEEZER_BOTTOM")
            >>> result.family
            StrategyFamily.REVERSAL
            >>> result.confidence
            1.0
        """
        family = classify_pattern(pattern)

        if family is not None:
            return PatternClassification(
                pattern=pattern,
                family=family,
                confidence=1.0,
                reason=f"Pattern classified as {family.value} family",
                known=True,
            )

        return PatternClassification(
            pattern=pattern,
            family=None,
            confidence=0.0,
            reason=f"Pattern '{pattern}' not found in registry",
            known=False,
        )

    # ═══════════════════════════════════════════════════════════════════════════
    # FAMILY ELIGIBILITY EVALUATION
    # ═══════════════════════════════════════════════════════════════════════════

    def evaluate(
        self,
        *,
        regime: str = "",
        phase: str = "",
        symbol: str = "",
        cycle_id: int = 0,
    ) -> FamilySelectionResult:
        """
        Evaluate which strategy families are eligible for the current context.

        In PASSTHROUGH mode: returns all families as eligible.
        In RESEARCH_GATED mode: would filter based on validated evidence.

        Args:
            regime: H4 regime (TRENDING/RANGE/TRANSITIONAL)
            phase: Market phase (IMPULSE/PULLBACK/CONSOLIDATION/EXHAUSTION/REVERSAL)
            symbol: Trading symbol
            cycle_id: Current processing cycle

        Returns:
            FamilySelectionResult with eligibility for each family.
        """
        if self._mode == "PASSTHROUGH":
            return self._passthrough_result(regime, phase)

        return self._research_gated_result(regime, phase)

    def _passthrough_result(self, regime: str, phase: str) -> FamilySelectionResult:
        """All families eligible — no filtering."""
        all_families = tuple(StrategyFamily)
        eligibility = tuple(
            FamilyEligibility(
                family=f,
                eligible=True,
                reason=EligibilityReason.ALWAYS_ELIGIBLE,
                confidence=0.0,
                evidence_source="passthrough_mode",
            )
            for f in all_families
        )

        return FamilySelectionResult(
            eligible_families=all_families,
            rejected_families=(),
            all_eligibility=eligibility,
            selected_family=None,
            confidence=0.0,
            reasons=("PASSTHROUGH mode: all families eligible",),
            mode="PASSTHROUGH",
            context_used={"regime": regime, "phase": phase},
            metadata={"total_families": len(all_families)},
        )

    def _research_gated_result(self, regime: str, phase: str) -> FamilySelectionResult:
        """
        Future: filter families based on research evidence.

        NOT ACTIVE. Placeholder for when M9/M10 research produces
        validated, promoted findings.
        """
        if not self._phase_family_rules:
            logger.warning(
                "[STRATEGY_FAMILY] RESEARCH_GATED mode requested but no rules loaded. "
                "Falling back to PASSTHROUGH."
            )
            return self._passthrough_result(regime, phase)

        # When rules exist, filter based on phase
        eligible_for_phase = self._phase_family_rules.get(phase, [])

        if not eligible_for_phase:
            # No rules for this phase — allow all (insufficient evidence)
            logger.info(
                "[STRATEGY_FAMILY] No rules for phase '%s'. Allowing all families.", phase
            )
            return self._passthrough_result(regime, phase)

        eligible = []
        rejected = []
        all_eligibility = []

        for f in StrategyFamily:
            if f in eligible_for_phase:
                eligible.append(f)
                all_eligibility.append(FamilyEligibility(
                    family=f,
                    eligible=True,
                    reason=EligibilityReason.PHASE_MATCH,
                    confidence=0.8,
                    evidence_source="research_rules",
                ))
            elif f in EMPTY_FAMILIES:
                rejected.append(f)
                all_eligibility.append(FamilyEligibility(
                    family=f,
                    eligible=False,
                    reason=EligibilityReason.NO_PATTERNS_AVAILABLE,
                    confidence=1.0,
                    evidence_source="registry",
                ))
            else:
                rejected.append(f)
                all_eligibility.append(FamilyEligibility(
                    family=f,
                    eligible=False,
                    reason=EligibilityReason.PHASE_MISMATCH,
                    confidence=0.8,
                    evidence_source="research_rules",
                ))

        return FamilySelectionResult(
            eligible_families=tuple(eligible),
            rejected_families=tuple(rejected),
            all_eligibility=tuple(all_eligibility),
            selected_family=eligible[0] if len(eligible) == 1 else None,
            confidence=0.8,
            reasons=(f"RESEARCH_GATED: phase '{phase}' favours {[f.value for f in eligible]}",),
            mode="RESEARCH_GATED",
            context_used={"regime": regime, "phase": phase},
            metadata={"rules_applied": True, "phase_lookup": phase},
        )

    # ═══════════════════════════════════════════════════════════════════════════
    # RESEARCH RULE LOADING (FUTURE ACTIVATION)
    # ═══════════════════════════════════════════════════════════════════════════

    def load_research_rules(
        self,
        rules: dict[str, list[str]],
        validation: ResearchValidation | None = None,
    ) -> bool:
        """
        Load validated phase -> family rules from the research pipeline.

        Rules are ONLY activated if validation metadata is supplied and passes
        all required checks. Without valid validation, rules are stored but
        the authority remains in PASSTHROUGH mode.

        Args:
            rules: e.g. {"REVERSAL": ["REVERSAL"], "IMPULSE": ["MOMENTUM"]}
            validation: Research validation metadata proving statistical rigour.

        Returns:
            True if rules were activated, False if stored but not activated.
        """
        # Store rules regardless
        self._phase_family_rules = {
            phase: [StrategyFamily(f) for f in families]
            for phase, families in rules.items()
        }

        if validation is None:
            logger.warning(
                "[STRATEGY_FAMILY] Rules loaded WITHOUT validation metadata. "
                "Cannot activate RESEARCH_GATED mode. Rules stored for reference only."
            )
            return False

        if not validation.is_valid:
            logger.warning(
                "[STRATEGY_FAMILY] Validation FAILED: sample=%d (need %d), "
                "p=%.4f (need <0.05), walk_forward=%s (need True). "
                "Rules stored but NOT activated.",
                validation.actual_sample_size,
                validation.minimum_sample_size,
                validation.p_value,
                validation.walk_forward_validated,
            )
            self._research_validations["latest"] = validation
            return False

        # Validation passed — activate
        self._mode = "RESEARCH_GATED"
        self._research_validations["latest"] = validation
        logger.info(
            "[STRATEGY_FAMILY] Rules ACTIVATED. Mode: RESEARCH_GATED. "
            "Loaded %d phase rules from '%s'. Sample: %d, p: %.4f.",
            len(self._phase_family_rules),
            validation.experiment_source,
            validation.actual_sample_size,
            validation.p_value,
        )
        return True

    # ═══════════════════════════════════════════════════════════════════════════
    # DIAGNOSTICS
    # ═══════════════════════════════════════════════════════════════════════════

    def get_diagnostic(self) -> dict[str, Any]:
        """Return diagnostic information about authority state."""
        distribution = get_family_distribution()
        active_families = [f.value for f in StrategyFamily if distribution.get(f.value, 0) > 0]
        inactive_families = [f.value for f in StrategyFamily if distribution.get(f.value, 0) == 0]

        return {
            "mode": self._mode,
            "active_families": active_families,
            "inactive_families": inactive_families,
            "rules_loaded": len(self._phase_family_rules),
            "phase_rules": {
                p: [f.value for f in fs]
                for p, fs in self._phase_family_rules.items()
            },
            "validation": {
                k: {
                    "sample_size": v.actual_sample_size,
                    "minimum_required": v.minimum_sample_size,
                    "p_value": v.p_value,
                    "walk_forward": v.walk_forward_validated,
                    "is_valid": v.is_valid,
                    "source": v.experiment_source,
                }
                for k, v in self._research_validations.items()
            },
            "total_patterns_classified": len(FAMILY_REGISTRY),
            "family_distribution": distribution,
        }
