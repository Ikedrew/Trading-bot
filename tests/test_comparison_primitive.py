"""
Comprehensive tests for the ComparisonPrimitive rewrite.

Covers:
- Large population + small valid analytical sample
- Missing metric values
- Missing group values
- Multiple groups with sufficient data
- Insufficient groups
- Mixed sufficient/insufficient groups
- Completely empty analytical sample
- Single group (no comparison possible)
- Outcome integration (mean_r exposed)
- Effect size calculation
- win_rate per group
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from research_engine.v10.runner.primitives.implementations import ComparisonPrimitive


class TestComparisonAnalyticalSample:
    """Verify analytical_sample reflects actual usable records, not population size."""

    def setup_method(self):
        self.p = ComparisonPrimitive()

    def test_large_population_small_analytical_sample(self):
        """Population 1040, only 40 have both fields."""
        pop = (
            [{"regime": "trending", "r_multiple": 1.5}] * 20
            + [{"regime": "ranging", "r_multiple": -0.5}] * 20
            + [{"regime": "trending"}] * 1000  # missing metric
        )
        r = self.p.analyse(pop, {"group_field": "regime", "metric_field": "r_multiple"})

        assert r.sample_size == 40
        assert r.metrics["population_size"] == 1040
        assert r.metrics["analytical_sample"] == 40
        assert r.metrics["groups_sufficient"] == 2

    def test_all_records_usable(self):
        """Every record has both fields — analytical == population."""
        pop = (
            [{"exit_reason": "tp", "r_multiple": 2.0}] * 30
            + [{"exit_reason": "sl", "r_multiple": -1.0}] * 64
        )
        r = self.p.analyse(pop, {"group_field": "exit_reason", "metric_field": "r_multiple"})

        assert r.sample_size == 94
        assert r.metrics["population_size"] == 94
        assert r.metrics["analytical_sample"] == 94

    def test_zero_analytical_sample_missing_metric(self):
        """All records have group but none have metric."""
        pop = [{"regime": "trending"}] * 100
        r = self.p.analyse(pop, {"group_field": "regime", "metric_field": "r_multiple"})

        assert r.sample_size == 0
        assert r.metrics["analytical_sample"] == 0
        assert r.metrics["population_size"] == 100
        assert r.metrics["records_missing_metric"] == 100

    def test_zero_analytical_sample_missing_group(self):
        """All records have metric but none have group."""
        pop = [{"r_multiple": 1.5}] * 50
        r = self.p.analyse(pop, {"group_field": "regime", "metric_field": "r_multiple"})

        assert r.sample_size == 0
        assert r.metrics["analytical_sample"] == 0
        assert r.metrics["records_missing_group"] == 50

    def test_empty_population(self):
        """Completely empty population."""
        r = self.p.analyse([], {"group_field": "regime", "metric_field": "r_multiple"})

        assert r.sample_size == 0
        assert r.metrics["analytical_sample"] == 0
        assert r.metrics["population_size"] == 0

    def test_dm002_pattern_large_pop_small_paired(self):
        """DM-002: 9121 decisions, only 81 have both regime and r_multiple."""
        pop = (
            [{"regime": "trending", "r_multiple": 1.0}] * 40
            + [{"regime": "ranging", "r_multiple": -0.3}] * 41
            + [{"r_multiple": 0.5}] * 9000  # missing group
        )
        r = self.p.analyse(pop, {"group_field": "regime", "metric_field": "r_multiple"})

        assert r.sample_size == 81
        assert r.metrics["population_size"] == 9081
        assert r.metrics["analytical_sample"] == 81


class TestComparisonGroupHandling:
    """Verify group classification and reporting."""

    def setup_method(self):
        self.p = ComparisonPrimitive()

    def test_multiple_sufficient_groups(self):
        """All groups meet min_per_group threshold."""
        pop = (
            [{"g": "A", "v": 1.0}] * 10
            + [{"g": "B", "v": 0.5}] * 8
            + [{"g": "C", "v": -0.5}] * 12
        )
        r = self.p.analyse(pop, {"group_field": "g", "metric_field": "v", "min_per_group": 3})

        assert r.metrics["groups_discovered"] == 3
        assert r.metrics["groups_sufficient"] == 3
        assert r.metrics["groups_insufficient"] == 0
        assert "A" in r.comparisons
        assert "B" in r.comparisons
        assert "C" in r.comparisons

    def test_all_groups_insufficient(self):
        """No group meets the threshold."""
        pop = (
            [{"g": "A", "v": 1.0}] * 2
            + [{"g": "B", "v": 0.5}] * 2
        )
        r = self.p.analyse(pop, {"group_field": "g", "metric_field": "v", "min_per_group": 5})

        assert r.sample_size == 4
        assert r.metrics["groups_discovered"] == 2
        assert r.metrics["groups_sufficient"] == 0
        assert r.metrics["groups_insufficient"] == 2
        # No comparisons computed
        assert r.comparisons == {}

    def test_mixed_sufficient_insufficient_groups(self):
        """Some groups sufficient, some sparse."""
        pop = (
            [{"g": "A", "v": 1.0}] * 10
            + [{"g": "B", "v": 0.5}] * 2  # insufficient
            + [{"g": "C", "v": -1.0}] * 5
        )
        r = self.p.analyse(pop, {"group_field": "g", "metric_field": "v", "min_per_group": 3})

        assert r.metrics["groups_sufficient"] == 2  # A and C
        assert r.metrics["groups_insufficient"] == 1  # B
        assert "A" in r.comparisons
        assert "C" in r.comparisons
        assert "B" not in r.comparisons
        # B still in sub_sample_sizes
        assert r.sub_sample_sizes["B"] == 2

    def test_single_group_no_comparison(self):
        """Only one group discovered — still computes stats."""
        pop = [{"g": "only", "v": 1.5}] * 20
        r = self.p.analyse(pop, {"group_field": "g", "metric_field": "v", "min_per_group": 3})

        assert r.metrics["groups_discovered"] == 1
        assert r.metrics["groups_sufficient"] == 1
        assert "only" in r.comparisons
        # No effect size with single group
        assert r.effect_sizes == {}

    def test_default_min_per_group_is_3(self):
        """Default min_per_group should be 3."""
        pop = (
            [{"regime": "A", "r_multiple": 1.0}] * 3
            + [{"regime": "B", "r_multiple": -1.0}] * 2  # under threshold
        )
        r = self.p.analyse(pop)  # defaults: regime, r_multiple, min_per_group=3

        assert r.metrics["groups_sufficient"] == 1  # only A
        assert r.metrics["groups_insufficient"] == 1  # B

    def test_custom_min_per_group(self):
        """min_per_group can be overridden."""
        pop = (
            [{"g": "A", "v": 1.0}] * 5
            + [{"g": "B", "v": -1.0}] * 5
        )
        r = self.p.analyse(pop, {"group_field": "g", "metric_field": "v", "min_per_group": 10})

        # Both insufficient at threshold 10
        assert r.metrics["groups_sufficient"] == 0
        assert r.metrics["groups_insufficient"] == 2


class TestComparisonOutcomeIntegration:
    """Verify mean_r and metrics for _determine_outcome integration."""

    def setup_method(self):
        self.p = ComparisonPrimitive()

    def test_mean_r_positive(self):
        """When overall mean > 0.05, expect positive mean_r."""
        pop = (
            [{"g": "A", "v": 2.0}] * 20
            + [{"g": "B", "v": 0.5}] * 20
        )
        r = self.p.analyse(pop, {"group_field": "g", "metric_field": "v", "min_per_group": 3})

        assert r.metrics["mean_r"] > 0.05
        assert r.metrics["overall_mean"] > 0

    def test_mean_r_negative(self):
        """When overall mean < -0.05, expect negative mean_r."""
        pop = (
            [{"g": "A", "v": -2.0}] * 20
            + [{"g": "B", "v": -0.5}] * 20
        )
        r = self.p.analyse(pop, {"group_field": "g", "metric_field": "v", "min_per_group": 3})

        assert r.metrics["mean_r"] < -0.05

    def test_mean_r_neutral(self):
        """Balanced groups → mean_r near zero."""
        pop = (
            [{"g": "A", "v": 1.0}] * 20
            + [{"g": "B", "v": -1.0}] * 20
        )
        r = self.p.analyse(pop, {"group_field": "g", "metric_field": "v", "min_per_group": 3})

        assert abs(r.metrics["mean_r"]) <= 0.05

    def test_group_spread_reported(self):
        """Spread between best and worst group means is reported."""
        pop = (
            [{"g": "winners", "v": 3.0}] * 10
            + [{"g": "losers", "v": -1.0}] * 10
        )
        r = self.p.analyse(pop, {"group_field": "g", "metric_field": "v", "min_per_group": 3})

        assert r.metrics["group_spread"] == 4.0

    def test_no_mean_r_when_no_sufficient_groups(self):
        """When no groups are sufficient, no mean_r should be in metrics."""
        pop = [{"g": "A", "v": 1.0}] * 2
        r = self.p.analyse(pop, {"group_field": "g", "metric_field": "v", "min_per_group": 5})

        assert "mean_r" not in r.metrics

    def test_no_mean_r_when_zero_analytical(self):
        """When zero analytical records, no mean_r."""
        pop = [{"g": "A"}] * 100
        r = self.p.analyse(pop, {"group_field": "g", "metric_field": "v"})

        assert "mean_r" not in r.metrics


class TestComparisonEffectSize:
    """Verify effect size (Cohen's d) calculations."""

    def setup_method(self):
        self.p = ComparisonPrimitive()

    def test_effect_size_two_groups(self):
        """Effect size computed between two largest sufficient groups."""
        pop = (
            [{"g": "A", "v": 2.0}] * 20
            + [{"g": "B", "v": -1.0}] * 15
        )
        r = self.p.analyse(pop, {"group_field": "g", "metric_field": "v", "min_per_group": 3})

        assert "groups" in r.effect_sizes
        assert "mean_difference" in r.effect_sizes
        assert "cohens_d" in r.effect_sizes
        assert r.effect_sizes["mean_difference"] == 3.0  # 2.0 - (-1.0)

    def test_no_effect_size_single_group(self):
        """Single group → no effect size."""
        pop = [{"g": "A", "v": 1.0}] * 20
        r = self.p.analyse(pop, {"group_field": "g", "metric_field": "v", "min_per_group": 3})

        assert r.effect_sizes == {}

    def test_effect_size_uses_sufficient_groups_only(self):
        """Effect size computed from sufficient groups, not sparse ones."""
        pop = (
            [{"g": "big1", "v": 2.0}] * 20
            + [{"g": "big2", "v": -1.0}] * 15
            + [{"g": "tiny", "v": 99.0}] * 1  # insufficient
        )
        r = self.p.analyse(pop, {"group_field": "g", "metric_field": "v", "min_per_group": 3})

        # Effect size between big1 and big2, not tiny
        assert "big1" in r.effect_sizes["groups"] or "big2" in r.effect_sizes["groups"]
        assert "tiny" not in r.effect_sizes["groups"]


class TestComparisonEvidence:
    """Verify evidence narratives and warnings."""

    def setup_method(self):
        self.p = ComparisonPrimitive()

    def test_evidence_for_divergent_groups(self):
        """Large spread → evidence mentions difference."""
        pop = (
            [{"g": "good", "v": 3.0}] * 10
            + [{"g": "bad", "v": -2.0}] * 10
        )
        r = self.p.analyse(pop, {"group_field": "g", "metric_field": "v", "min_per_group": 3})

        assert any("differ" in e for e in r.evidence)

    def test_evidence_for_similar_groups(self):
        """Small spread → evidence mentions similarity."""
        pop = (
            [{"g": "A", "v": 0.5}] * 10
            + [{"g": "B", "v": 0.52}] * 10
        )
        r = self.p.analyse(pop, {"group_field": "g", "metric_field": "v", "min_per_group": 3})

        assert any("similar" in e for e in r.evidence)

    def test_warning_for_excluded_groups(self):
        """Insufficient groups reported in warnings."""
        pop = (
            [{"g": "A", "v": 1.0}] * 10
            + [{"g": "tiny", "v": 5.0}] * 1
        )
        r = self.p.analyse(pop, {"group_field": "g", "metric_field": "v", "min_per_group": 3})

        assert any("tiny" in w for w in r.warnings)

    def test_warning_zero_analytical(self):
        """Zero analytical → warning with diagnostic info."""
        pop = [{"regime": "trending"}] * 100
        r = self.p.analyse(pop, {"group_field": "regime", "metric_field": "r_multiple"})

        assert len(r.warnings) >= 1
        assert "regime" in r.warnings[0] or "r_multiple" in r.warnings[0]


class TestComparisonWinRate:
    """Verify win_rate per group."""

    def setup_method(self):
        self.p = ComparisonPrimitive()

    def test_win_rate_all_positive(self):
        """All values positive → win_rate = 1.0."""
        pop = [{"g": "X", "v": 1.0}] * 10
        r = self.p.analyse(pop, {"group_field": "g", "metric_field": "v", "min_per_group": 3})

        assert r.comparisons["X"]["win_rate"] == 1.0

    def test_win_rate_all_negative(self):
        """All values negative → win_rate = 0.0."""
        pop = [{"g": "X", "v": -1.0}] * 10
        r = self.p.analyse(pop, {"group_field": "g", "metric_field": "v", "min_per_group": 3})

        assert r.comparisons["X"]["win_rate"] == 0.0

    def test_win_rate_mixed(self):
        """Mixed positive/negative → correct ratio."""
        pop = [{"g": "X", "v": 1.0}] * 6 + [{"g": "X", "v": -1.0}] * 4
        r = self.p.analyse(pop, {"group_field": "g", "metric_field": "v", "min_per_group": 3})

        assert r.comparisons["X"]["win_rate"] == 0.6


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v", "--tb=short", "-p", "no:conftest"]))
