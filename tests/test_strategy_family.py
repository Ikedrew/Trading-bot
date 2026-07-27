"""
Tests for core/strategy_family/ — Strategy Family Layer.

Verifies:
    - Registry loads correctly
    - Every known pattern maps to a valid family
    - Unknown patterns fail safely (return None, not raise)
    - Future families exist but have no patterns
    - PASSTHROUGH mode does not affect decisions
    - Research-gated activation requires valid metadata
    - Diagnostics produce expected output
    - PatternClassification returns correct structure
"""

import pytest

from core.strategy_family import (
    EMPTY_FAMILIES,
    FAMILY_REGISTRY,
    EligibilityReason,
    FamilyEligibility,
    FamilySelectionResult,
    PatternClassification,
    ResearchValidation,
    StrategyFamily,
    StrategyFamilyAuthority,
    classify_pattern,
    format_diagnostic_report,
    format_pattern_report,
    get_all_known_patterns,
    get_family_distribution,
    get_patterns_for_family,
    get_summary_dict,
    is_known_pattern,
)


# ═══════════════════════════════════════════════════════════════════════════════
# REGISTRY TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestFamilyRegistry:
    """Tests for the pattern-to-family registry."""

    def test_registry_loads(self):
        """Registry must be a non-empty dict."""
        assert isinstance(FAMILY_REGISTRY, dict)
        assert len(FAMILY_REGISTRY) == 14

    def test_all_patterns_map_to_valid_family(self):
        """Every entry must map to a StrategyFamily enum member."""
        for pattern, family in FAMILY_REGISTRY.items():
            assert isinstance(family, StrategyFamily), (
                f"Pattern '{pattern}' maps to non-StrategyFamily: {family}"
            )

    def test_reversal_patterns_count(self):
        """12 patterns should be classified as REVERSAL."""
        reversal = get_patterns_for_family(StrategyFamily.REVERSAL)
        assert len(reversal) == 12

    def test_momentum_patterns_count(self):
        """2 patterns should be classified as MOMENTUM."""
        momentum = get_patterns_for_family(StrategyFamily.MOMENTUM)
        assert len(momentum) == 2

    def test_specific_reversal_patterns(self):
        """Verify specific patterns are in REVERSAL family."""
        expected_reversal = [
            "TWEEZER_TOP", "TWEEZER_BOTTOM", "HAMMER", "HANGING_MAN",
            "INVERTED_HAMMER", "SHOOTING_STAR", "MORNING_STAR", "EVENING_STAR",
            "THREE_INSIDE_UP", "THREE_INSIDE_DOWN",
            "BULLISH_ENGULFING", "BEARISH_ENGULFING",
        ]
        for pattern in expected_reversal:
            assert classify_pattern(pattern) == StrategyFamily.REVERSAL, (
                f"{pattern} should be REVERSAL"
            )

    def test_specific_momentum_patterns(self):
        """Verify specific patterns are in MOMENTUM family."""
        assert classify_pattern("THREE_WHITE_SOLDIERS") == StrategyFamily.MOMENTUM
        assert classify_pattern("THREE_BLACK_CROWS") == StrategyFamily.MOMENTUM

    def test_unknown_pattern_returns_none(self):
        """Unknown patterns must return None, not raise."""
        result = classify_pattern("TOTALLY_FAKE_PATTERN")
        assert result is None

    def test_unknown_pattern_empty_string(self):
        """Empty string is unknown."""
        assert classify_pattern("") is None

    def test_is_known_pattern(self):
        """is_known_pattern returns correct boolean."""
        assert is_known_pattern("HAMMER") is True
        assert is_known_pattern("FAKE_PATTERN") is False

    def test_get_all_known_patterns(self):
        """Returns all 14 patterns."""
        patterns = get_all_known_patterns()
        assert len(patterns) == 14
        assert "HAMMER" in patterns
        assert "THREE_WHITE_SOLDIERS" in patterns


class TestFutureFamily:
    """Tests for families that exist but have no patterns."""

    def test_continuation_has_no_patterns(self):
        """CONTINUATION should exist but have 0 patterns."""
        patterns = get_patterns_for_family(StrategyFamily.CONTINUATION)
        assert patterns == []

    def test_breakout_has_no_patterns(self):
        """BREAKOUT should exist but have 0 patterns."""
        patterns = get_patterns_for_family(StrategyFamily.BREAKOUT)
        assert patterns == []

    def test_mean_reversion_has_no_patterns(self):
        """MEAN_REVERSION should exist but have 0 patterns."""
        patterns = get_patterns_for_family(StrategyFamily.MEAN_REVERSION)
        assert patterns == []

    def test_empty_families_set(self):
        """EMPTY_FAMILIES should contain families with zero patterns."""
        assert StrategyFamily.CONTINUATION in EMPTY_FAMILIES
        assert StrategyFamily.BREAKOUT in EMPTY_FAMILIES
        assert StrategyFamily.MEAN_REVERSION in EMPTY_FAMILIES
        assert StrategyFamily.REVERSAL not in EMPTY_FAMILIES
        assert StrategyFamily.MOMENTUM not in EMPTY_FAMILIES

    def test_family_distribution_includes_zeros(self):
        """get_family_distribution must include zero-count families."""
        dist = get_family_distribution()
        assert dist["CONTINUATION"] == 0
        assert dist["BREAKOUT"] == 0
        assert dist["MEAN_REVERSION"] == 0
        assert dist["REVERSAL"] == 12
        assert dist["MOMENTUM"] == 2


# ═══════════════════════════════════════════════════════════════════════════════
# AUTHORITY TESTS — PASSTHROUGH MODE
# ═══════════════════════════════════════════════════════════════════════════════


class TestPassthroughMode:
    """Tests for PASSTHROUGH mode (default, no filtering)."""

    def setup_method(self):
        self.authority = StrategyFamilyAuthority()

    def test_default_mode_is_passthrough(self):
        """Default mode must be PASSTHROUGH."""
        assert self.authority.mode == "PASSTHROUGH"

    def test_all_families_eligible_in_passthrough(self):
        """PASSTHROUGH returns all families as eligible."""
        result = self.authority.evaluate(regime="RANGE", phase="REVERSAL")
        assert len(result.eligible_families) == len(StrategyFamily)
        for family in StrategyFamily:
            assert result.is_eligible(family)

    def test_no_families_rejected_in_passthrough(self):
        """PASSTHROUGH should reject nothing."""
        result = self.authority.evaluate(regime="TRENDING", phase="IMPULSE")
        assert result.rejected_families == ()

    def test_passthrough_mode_label(self):
        """Result mode should be PASSTHROUGH."""
        result = self.authority.evaluate()
        assert result.mode == "PASSTHROUGH"

    def test_passthrough_does_not_affect_decisions(self):
        """Calling evaluate with any context should not change eligibility."""
        contexts = [
            {"regime": "RANGE", "phase": "REVERSAL"},
            {"regime": "TRENDING", "phase": "IMPULSE"},
            {"regime": "TRANSITIONAL", "phase": "CONSOLIDATION"},
            {"regime": "", "phase": ""},
        ]
        for ctx in contexts:
            result = self.authority.evaluate(**ctx)
            assert len(result.eligible_families) == len(StrategyFamily)
            assert result.rejected_families == ()

    def test_context_recorded_in_result(self):
        """Context should be stored in the result."""
        result = self.authority.evaluate(regime="RANGE", phase="REVERSAL")
        assert result.context_used["regime"] == "RANGE"
        assert result.context_used["phase"] == "REVERSAL"

    def test_eligibility_reason_is_always_eligible(self):
        """All eligibility entries should have ALWAYS_ELIGIBLE reason."""
        result = self.authority.evaluate()
        for elig in result.all_eligibility:
            assert elig.reason == EligibilityReason.ALWAYS_ELIGIBLE


# ═══════════════════════════════════════════════════════════════════════════════
# AUTHORITY TESTS — PATTERN CLASSIFICATION
# ═══════════════════════════════════════════════════════════════════════════════


class TestPatternClassification:
    """Tests for the classify() method."""

    def setup_method(self):
        self.authority = StrategyFamilyAuthority()

    def test_known_pattern_classified(self):
        """Known pattern returns correct family with confidence 1.0."""
        result = self.authority.classify("TWEEZER_BOTTOM")
        assert result.pattern == "TWEEZER_BOTTOM"
        assert result.family == StrategyFamily.REVERSAL
        assert result.confidence == 1.0
        assert result.known is True
        assert "REVERSAL" in result.reason

    def test_momentum_pattern_classified(self):
        """Momentum pattern correctly identified."""
        result = self.authority.classify("THREE_WHITE_SOLDIERS")
        assert result.family == StrategyFamily.MOMENTUM
        assert result.known is True

    def test_unknown_pattern_safe_failure(self):
        """Unknown pattern returns None family, confidence 0, known=False."""
        result = self.authority.classify("NONEXISTENT_PATTERN")
        assert result.pattern == "NONEXISTENT_PATTERN"
        assert result.family is None
        assert result.confidence == 0.0
        assert result.known is False

    def test_family_name_property(self):
        """family_name property returns string value."""
        known = self.authority.classify("HAMMER")
        assert known.family_name == "REVERSAL"

        unknown = self.authority.classify("FAKE")
        assert unknown.family_name == "UNKNOWN"


# ═══════════════════════════════════════════════════════════════════════════════
# AUTHORITY TESTS — RESEARCH-GATED ACTIVATION
# ═══════════════════════════════════════════════════════════════════════════════


class TestResearchGatedActivation:
    """Tests for research rule loading and validation requirements."""

    def test_load_without_validation_does_not_activate(self):
        """Rules without validation metadata should NOT activate gated mode."""
        authority = StrategyFamilyAuthority()
        activated = authority.load_research_rules(
            rules={"REVERSAL": ["REVERSAL"], "IMPULSE": ["MOMENTUM"]},
            validation=None,
        )
        assert activated is False
        assert authority.mode == "PASSTHROUGH"

    def test_load_with_insufficient_sample_does_not_activate(self):
        """Rules with too few samples should NOT activate."""
        authority = StrategyFamilyAuthority()
        validation = ResearchValidation(
            minimum_sample_size=100,
            actual_sample_size=50,  # Below minimum
            p_value=0.01,
            confidence_interval=(0.1, 0.3),
            walk_forward_validated=True,
            experiment_source="M10_test",
            validation_date="2026-07-27",
        )
        activated = authority.load_research_rules(
            rules={"REVERSAL": ["REVERSAL"]},
            validation=validation,
        )
        assert activated is False
        assert authority.mode == "PASSTHROUGH"

    def test_load_with_high_p_value_does_not_activate(self):
        """Rules with p >= 0.05 should NOT activate."""
        authority = StrategyFamilyAuthority()
        validation = ResearchValidation(
            minimum_sample_size=100,
            actual_sample_size=200,
            p_value=0.15,  # Above threshold
            confidence_interval=(0.1, 0.3),
            walk_forward_validated=True,
            experiment_source="M10_test",
            validation_date="2026-07-27",
        )
        activated = authority.load_research_rules(
            rules={"REVERSAL": ["REVERSAL"]},
            validation=validation,
        )
        assert activated is False
        assert authority.mode == "PASSTHROUGH"

    def test_load_without_walk_forward_does_not_activate(self):
        """Rules without walk-forward validation should NOT activate."""
        authority = StrategyFamilyAuthority()
        validation = ResearchValidation(
            minimum_sample_size=100,
            actual_sample_size=200,
            p_value=0.01,
            confidence_interval=(0.1, 0.3),
            walk_forward_validated=False,  # Not validated
            experiment_source="M10_test",
            validation_date="2026-07-27",
        )
        activated = authority.load_research_rules(
            rules={"REVERSAL": ["REVERSAL"]},
            validation=validation,
        )
        assert activated is False
        assert authority.mode == "PASSTHROUGH"

    def test_valid_research_activates_gated_mode(self):
        """Rules with fully valid metadata should activate RESEARCH_GATED."""
        authority = StrategyFamilyAuthority()
        validation = ResearchValidation(
            minimum_sample_size=100,
            actual_sample_size=250,
            p_value=0.003,
            confidence_interval=(0.1, 0.3),
            walk_forward_validated=True,
            experiment_source="M10_strategy_family_per_phase",
            validation_date="2026-07-27",
        )
        activated = authority.load_research_rules(
            rules={"REVERSAL": ["REVERSAL"], "IMPULSE": ["MOMENTUM"]},
            validation=validation,
        )
        assert activated is True
        assert authority.mode == "RESEARCH_GATED"

    def test_research_gated_filters_families(self):
        """RESEARCH_GATED mode should filter based on loaded rules."""
        authority = StrategyFamilyAuthority()
        validation = ResearchValidation(
            minimum_sample_size=100,
            actual_sample_size=250,
            p_value=0.003,
            confidence_interval=(0.1, 0.3),
            walk_forward_validated=True,
            experiment_source="M10_test",
            validation_date="2026-07-27",
        )
        authority.load_research_rules(
            rules={"REVERSAL": ["REVERSAL"], "IMPULSE": ["MOMENTUM"]},
            validation=validation,
        )

        # Phase with a rule
        result = authority.evaluate(phase="REVERSAL")
        assert StrategyFamily.REVERSAL in result.eligible_families
        assert result.mode == "RESEARCH_GATED"

        # Phase with different rule
        result = authority.evaluate(phase="IMPULSE")
        assert StrategyFamily.MOMENTUM in result.eligible_families

    def test_research_gated_unknown_phase_passthrough(self):
        """Unknown phase in RESEARCH_GATED should fall back to passthrough."""
        authority = StrategyFamilyAuthority()
        validation = ResearchValidation(
            minimum_sample_size=100,
            actual_sample_size=250,
            p_value=0.003,
            confidence_interval=(0.1, 0.3),
            walk_forward_validated=True,
            experiment_source="M10_test",
            validation_date="2026-07-27",
        )
        authority.load_research_rules(
            rules={"REVERSAL": ["REVERSAL"]},
            validation=validation,
        )

        # Unknown phase — should allow all
        result = authority.evaluate(phase="UNKNOWN_PHASE")
        assert len(result.eligible_families) == len(StrategyFamily)


# ═══════════════════════════════════════════════════════════════════════════════
# RESEARCH VALIDATION MODEL TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestResearchValidation:
    """Tests for the ResearchValidation gating model."""

    def test_valid_when_all_criteria_met(self):
        v = ResearchValidation(
            minimum_sample_size=100,
            actual_sample_size=150,
            p_value=0.01,
            confidence_interval=(0.05, 0.25),
            walk_forward_validated=True,
            experiment_source="M10",
            validation_date="2026-07-27",
        )
        assert v.is_valid is True

    def test_invalid_sample_size(self):
        v = ResearchValidation(
            minimum_sample_size=100,
            actual_sample_size=99,
            p_value=0.01,
            confidence_interval=(0.05, 0.25),
            walk_forward_validated=True,
            experiment_source="M10",
            validation_date="2026-07-27",
        )
        assert v.is_valid is False

    def test_invalid_p_value(self):
        v = ResearchValidation(
            minimum_sample_size=100,
            actual_sample_size=150,
            p_value=0.05,  # Not strictly < 0.05
            confidence_interval=(0.05, 0.25),
            walk_forward_validated=True,
            experiment_source="M10",
            validation_date="2026-07-27",
        )
        assert v.is_valid is False

    def test_invalid_walk_forward(self):
        v = ResearchValidation(
            minimum_sample_size=100,
            actual_sample_size=150,
            p_value=0.01,
            confidence_interval=(0.05, 0.25),
            walk_forward_validated=False,
            experiment_source="M10",
            validation_date="2026-07-27",
        )
        assert v.is_valid is False


# ═══════════════════════════════════════════════════════════════════════════════
# DIAGNOSTICS TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestDiagnostics:
    """Tests for diagnostic output."""

    def test_diagnostic_dict_has_required_keys(self):
        """get_diagnostic() must return expected structure."""
        authority = StrategyFamilyAuthority()
        diag = authority.get_diagnostic()
        assert "mode" in diag
        assert "active_families" in diag
        assert "inactive_families" in diag
        assert "family_distribution" in diag
        assert "total_patterns_classified" in diag
        assert diag["mode"] == "PASSTHROUGH"

    def test_diagnostic_report_contains_mode(self):
        """Formatted report must show current mode."""
        authority = StrategyFamilyAuthority()
        report = format_diagnostic_report(authority)
        assert "PASSTHROUGH" in report

    def test_diagnostic_report_contains_families(self):
        """Formatted report must list active and inactive families."""
        authority = StrategyFamilyAuthority()
        report = format_diagnostic_report(authority)
        assert "REVERSAL" in report
        assert "MOMENTUM" in report
        assert "CONTINUATION" in report
        assert "BREAKOUT" in report
        assert "MEAN_REVERSION" in report

    def test_diagnostic_report_contains_counts(self):
        """Formatted report must show pattern counts."""
        authority = StrategyFamilyAuthority()
        report = format_diagnostic_report(authority)
        assert "12" in report  # REVERSAL count
        assert "2" in report   # MOMENTUM count

    def test_pattern_report_lists_all_families(self):
        """Pattern report must list all families."""
        report = format_pattern_report()
        assert "REVERSAL" in report
        assert "MOMENTUM" in report
        assert "CONTINUATION" in report
        assert "future expansion" in report.lower() or "no patterns" in report.lower()

    def test_summary_dict_structure(self):
        """get_summary_dict must return machine-readable structure."""
        authority = StrategyFamilyAuthority()
        summary = get_summary_dict(authority)
        assert summary["mode"] == "PASSTHROUGH"
        assert summary["total_patterns"] == 14
        assert "REVERSAL" in summary["active_families"]
        assert "MOMENTUM" in summary["active_families"]
        assert "CONTINUATION" in summary["inactive_families"]
        assert summary["library_assessment"]["status"] == "HEAVILY_SKEWED"
        assert summary["library_assessment"]["dominant_family"] == "REVERSAL"

    def test_summary_dict_patterns_by_family(self):
        """Summary must include patterns grouped by family."""
        authority = StrategyFamilyAuthority()
        summary = get_summary_dict(authority)
        assert "HAMMER" in summary["patterns_by_family"]["REVERSAL"]
        assert "THREE_WHITE_SOLDIERS" in summary["patterns_by_family"]["MOMENTUM"]
        assert summary["patterns_by_family"]["CONTINUATION"] == []


# ═══════════════════════════════════════════════════════════════════════════════
# MODEL TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestModels:
    """Tests for data models."""

    def test_strategy_family_enum_values(self):
        """All 5 families must exist."""
        assert StrategyFamily.REVERSAL.value == "REVERSAL"
        assert StrategyFamily.MOMENTUM.value == "MOMENTUM"
        assert StrategyFamily.CONTINUATION.value == "CONTINUATION"
        assert StrategyFamily.BREAKOUT.value == "BREAKOUT"
        assert StrategyFamily.MEAN_REVERSION.value == "MEAN_REVERSION"
        assert len(StrategyFamily) == 5

    def test_family_selection_result_eligible_names(self):
        """eligible_family_names property returns string list."""
        result = FamilySelectionResult(
            eligible_families=(StrategyFamily.REVERSAL, StrategyFamily.MOMENTUM),
            rejected_families=(StrategyFamily.CONTINUATION,),
            all_eligibility=(),
        )
        assert result.eligible_family_names == ["REVERSAL", "MOMENTUM"]
        assert result.rejected_family_names == ["CONTINUATION"]

    def test_family_selection_result_is_eligible(self):
        """is_eligible returns correct boolean."""
        result = FamilySelectionResult(
            eligible_families=(StrategyFamily.REVERSAL,),
            rejected_families=(StrategyFamily.MOMENTUM,),
            all_eligibility=(),
        )
        assert result.is_eligible(StrategyFamily.REVERSAL) is True
        assert result.is_eligible(StrategyFamily.MOMENTUM) is False

    def test_pattern_classification_frozen(self):
        """PatternClassification should be immutable."""
        pc = PatternClassification(
            pattern="HAMMER",
            family=StrategyFamily.REVERSAL,
            confidence=1.0,
            reason="test",
            known=True,
        )
        with pytest.raises(Exception):
            pc.pattern = "CHANGED"  # type: ignore

    def test_family_eligibility_frozen(self):
        """FamilyEligibility should be immutable."""
        fe = FamilyEligibility(
            family=StrategyFamily.REVERSAL,
            eligible=True,
            reason=EligibilityReason.ALWAYS_ELIGIBLE,
        )
        with pytest.raises(Exception):
            fe.eligible = False  # type: ignore
