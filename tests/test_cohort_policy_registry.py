"""
Tests for Cohort Policy Registry.

Validates:
- Exact cohort → policy matching
- Fallback behaviour for unknown cohorts
- Partial match (strength-only, timing-only)
- Policy structure integrity
- Explanation function output
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.cohort_analysis.cohort_policy_types import CohortKey, ManagementPolicy
from tools.cohort_analysis.cohort_policy_registry import (
    get_policy,
    explain_policy_assignment,
    RUNNER_MODE,
    EXTENSION_MODE,
    PROTECT_MODE,
    STANDARD_MODE,
    REDUCED_RUNNER_MODE,
)


class TestExactMatch:

    def test_strong_early_trending_is_runner(self):
        """STRONG + EARLY + TRENDING → RUNNER_MODE."""
        policy = get_policy(CohortKey("STRONG", "EARLY", "TRENDING"))
        assert policy is RUNNER_MODE
        assert policy.name == "RUNNER_MODE"

    def test_strong_mid_trending_is_extension(self):
        """STRONG + MID + TRENDING → EXTENSION_MODE."""
        policy = get_policy(CohortKey("STRONG", "MID", "TRENDING"))
        assert policy is EXTENSION_MODE

    def test_strong_late_trending_is_reduced_runner(self):
        """STRONG + LATE + TRENDING → REDUCED_RUNNER_MODE."""
        policy = get_policy(CohortKey("STRONG", "LATE", "TRENDING"))
        assert policy is REDUCED_RUNNER_MODE


class TestWeakProtection:

    def test_weak_late_ranging_is_protect(self):
        """WEAK + LATE + RANGING → PROTECT_MODE."""
        policy = get_policy(CohortKey("WEAK", "LATE", "RANGING"))
        assert policy is PROTECT_MODE

    def test_weak_mid_trending_is_protect(self):
        """WEAK + MID + TRENDING → PROTECT_MODE."""
        policy = get_policy(CohortKey("WEAK", "MID", "TRENDING"))
        assert policy is PROTECT_MODE

    def test_weak_early_ranging_is_protect(self):
        """WEAK + EARLY + RANGING → PROTECT_MODE."""
        policy = get_policy(CohortKey("WEAK", "EARLY", "RANGING"))
        assert policy is PROTECT_MODE


class TestFallback:

    def test_completely_unknown_returns_standard(self):
        """Fully unknown cohort → STANDARD_MODE."""
        policy = get_policy(CohortKey("UNKNOWN", "UNKNOWN", "UNKNOWN"))
        assert policy is STANDARD_MODE

    def test_invalid_strength_unknown_rest_returns_standard(self):
        """INVALID strength with unknown timing/regime → fallback to PROTECT via strength."""
        policy = get_policy(CohortKey("INVALID", "UNKNOWN", "UNKNOWN"))
        assert policy is PROTECT_MODE  # strength fallback for INVALID


class TestPartialMatch:

    def test_strong_unknown_timing_uses_strength_fallback(self):
        """STRONG + unknown timing + unknown regime → EXTENSION_MODE (strength fallback)."""
        policy = get_policy(CohortKey("STRONG", "UNKNOWN", "UNKNOWN"))
        assert policy is EXTENSION_MODE

    def test_weak_unknown_timing_uses_strength_fallback(self):
        """WEAK + unknown timing + unknown regime → PROTECT_MODE (strength fallback)."""
        policy = get_policy(CohortKey("WEAK", "UNKNOWN", "UNKNOWN"))
        assert policy is PROTECT_MODE


class TestPolicyStructure:

    def test_rr_bias_is_float(self):
        """All policies have float rr_bias."""
        for policy in (RUNNER_MODE, EXTENSION_MODE, PROTECT_MODE, STANDARD_MODE, REDUCED_RUNNER_MODE):
            assert isinstance(policy.rr_bias, float)

    def test_trailing_mode_valid(self):
        """All policies have valid trailing_mode."""
        valid = {"OFF", "LIGHT", "AGGRESSIVE"}
        for policy in (RUNNER_MODE, EXTENSION_MODE, PROTECT_MODE, STANDARD_MODE, REDUCED_RUNNER_MODE):
            assert policy.trailing_mode in valid

    def test_break_even_mode_valid(self):
        """All policies have valid break_even_mode."""
        valid = {"OFF", "EARLY", "DELAYED"}
        for policy in (RUNNER_MODE, EXTENSION_MODE, PROTECT_MODE, STANDARD_MODE, REDUCED_RUNNER_MODE):
            assert policy.break_even_mode in valid

    def test_partial_tp_mode_valid(self):
        """All policies have valid partial_tp_mode."""
        valid = {"OFF", "STANDARD", "AGGRESSIVE"}
        for policy in (RUNNER_MODE, EXTENSION_MODE, PROTECT_MODE, STANDARD_MODE, REDUCED_RUNNER_MODE):
            assert policy.partial_tp_mode in valid

    def test_all_fields_exist(self):
        """All policies have non-empty name and description."""
        for policy in (RUNNER_MODE, EXTENSION_MODE, PROTECT_MODE, STANDARD_MODE, REDUCED_RUNNER_MODE):
            assert len(policy.name) > 0
            assert len(policy.description) > 0
            assert len(policy.notes) > 0


class TestExplanation:

    def test_explain_returns_nonempty_string(self):
        """explain_policy_assignment returns non-empty explanation."""
        cohort = CohortKey("STRONG", "EARLY", "TRENDING")
        explanation = explain_policy_assignment(cohort)

        assert isinstance(explanation, str)
        assert len(explanation) > 50

    def test_explain_contains_cohort_info(self):
        """Explanation includes cohort dimension values."""
        cohort = CohortKey("WEAK", "LATE", "RANGING")
        explanation = explain_policy_assignment(cohort)

        assert "WEAK" in explanation
        assert "LATE" in explanation
        assert "RANGING" in explanation

    def test_explain_contains_policy_name(self):
        """Explanation includes the assigned policy name."""
        cohort = CohortKey("STRONG", "EARLY", "TRENDING")
        explanation = explain_policy_assignment(cohort)

        assert "RUNNER_MODE" in explanation

    def test_explain_with_explicit_policy(self):
        """explain_policy_assignment accepts explicit policy override."""
        cohort = CohortKey("STRONG", "MID", "TRENDING")
        explanation = explain_policy_assignment(cohort, PROTECT_MODE)

        assert "PROTECT_MODE" in explanation
