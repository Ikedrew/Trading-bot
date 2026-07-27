"""
Tests for core/stability/policy_registry.py

Static integrity validation only. No mocks. No runtime behavior.
"""

from __future__ import annotations

from core.stability.policy_registry import POLICY_REGISTRY


# ─── ALLOWED VALUES ───────────────────────────────────────────────────────────

ALLOWED_POLICIES = {"RUNNER_MODE", "NORMAL_MODE", "PROTECT_MODE", "BLOCK_MODE"}


# ─── STRUCTURAL TESTS ─────────────────────────────────────────────────────────


class TestRegistryExists:
    def test_registry_is_defined(self):
        assert POLICY_REGISTRY is not None

    def test_registry_is_dict(self):
        assert isinstance(POLICY_REGISTRY, dict)

    def test_registry_is_not_empty(self):
        assert len(POLICY_REGISTRY) > 0


class TestKeyFormat:
    def test_all_keys_are_strings(self):
        for key in POLICY_REGISTRY:
            assert isinstance(key, str), f"Key {key!r} is not a string"

    def test_all_keys_have_exactly_3_segments(self):
        for key in POLICY_REGISTRY:
            segments = key.split("+")
            assert len(segments) == 3, (
                f"Key {key!r} has {len(segments)} segments, expected 3"
            )

    def test_all_key_segments_are_uppercase(self):
        for key in POLICY_REGISTRY:
            for segment in key.split("+"):
                assert segment == segment.upper(), (
                    f"Segment {segment!r} in key {key!r} is not uppercase"
                )

    def test_no_key_has_empty_segments(self):
        for key in POLICY_REGISTRY:
            for segment in key.split("+"):
                assert len(segment) > 0, (
                    f"Key {key!r} contains an empty segment"
                )


class TestValueFormat:
    def test_all_values_are_strings(self):
        for key, value in POLICY_REGISTRY.items():
            assert isinstance(value, str), (
                f"Value for {key!r} is {type(value).__name__}, expected str"
            )

    def test_all_values_are_within_allowed_set(self):
        for key, value in POLICY_REGISTRY.items():
            assert value in ALLOWED_POLICIES, (
                f"Value {value!r} for key {key!r} not in allowed set {ALLOWED_POLICIES}"
            )


# ─── SEED KEY TESTS ───────────────────────────────────────────────────────────


class TestSeedKeys:
    def test_strong_early_trending_exists(self):
        assert "STRONG+EARLY+TRENDING" in POLICY_REGISTRY
        assert POLICY_REGISTRY["STRONG+EARLY+TRENDING"] == "RUNNER_MODE"

    def test_strong_mid_trending_exists(self):
        assert "STRONG+MID+TRENDING" in POLICY_REGISTRY
        assert POLICY_REGISTRY["STRONG+MID+TRENDING"] == "NORMAL_MODE"

    def test_strong_late_trending_exists(self):
        assert "STRONG+LATE+TRENDING" in POLICY_REGISTRY
        assert POLICY_REGISTRY["STRONG+LATE+TRENDING"] == "NORMAL_MODE"

    def test_moderate_early_trending_exists(self):
        assert "MODERATE+EARLY+TRENDING" in POLICY_REGISTRY
        assert POLICY_REGISTRY["MODERATE+EARLY+TRENDING"] == "NORMAL_MODE"

    def test_moderate_mid_trending_exists(self):
        assert "MODERATE+MID+TRENDING" in POLICY_REGISTRY
        assert POLICY_REGISTRY["MODERATE+MID+TRENDING"] == "NORMAL_MODE"

    def test_moderate_late_trending_exists(self):
        assert "MODERATE+LATE+TRENDING" in POLICY_REGISTRY
        assert POLICY_REGISTRY["MODERATE+LATE+TRENDING"] == "PROTECT_MODE"

    def test_weak_early_trending_exists(self):
        assert "WEAK+EARLY+TRENDING" in POLICY_REGISTRY
        assert POLICY_REGISTRY["WEAK+EARLY+TRENDING"] == "PROTECT_MODE"

    def test_weak_late_trending_exists(self):
        assert "WEAK+LATE+TRENDING" in POLICY_REGISTRY
        assert POLICY_REGISTRY["WEAK+LATE+TRENDING"] == "PROTECT_MODE"

    def test_weak_late_ranging_exists(self):
        assert "WEAK+LATE+RANGING" in POLICY_REGISTRY
        assert POLICY_REGISTRY["WEAK+LATE+RANGING"] == "BLOCK_MODE"

    def test_weak_mid_ranging_exists(self):
        assert "WEAK+MID+RANGING" in POLICY_REGISTRY
        assert POLICY_REGISTRY["WEAK+MID+RANGING"] == "PROTECT_MODE"


class TestFallbackSeed:
    def test_unknown_fallback_exists(self):
        assert "UNKNOWN+UNKNOWN+UNKNOWN" in POLICY_REGISTRY
        assert POLICY_REGISTRY["UNKNOWN+UNKNOWN+UNKNOWN"] == "NORMAL_MODE"


# ─── DETERMINISM TEST ─────────────────────────────────────────────────────────


class TestDeterminism:
    def test_same_key_always_returns_same_value(self):
        """Registry is static — repeated access yields identical results."""
        for key in POLICY_REGISTRY:
            assert POLICY_REGISTRY[key] == POLICY_REGISTRY[key]

    def test_registry_not_modified_by_access(self):
        """Accessing the registry does not change its size."""
        original_len = len(POLICY_REGISTRY)
        _ = POLICY_REGISTRY.get("NONEXISTENT+KEY+HERE")
        assert len(POLICY_REGISTRY) == original_len
