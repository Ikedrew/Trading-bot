"""
Tests for core/stability/cohort_key.py

100% deterministic. No mocks. No dependencies.
"""

from __future__ import annotations

from types import SimpleNamespace

from core.stability.cohort_key import build_cohort_key


def _make_decision(**kwargs) -> SimpleNamespace:
    return SimpleNamespace(**kwargs)


# ─── NORMAL COMPOSITION ───────────────────────────────────────────────────────


class TestNormalComposition:
    def test_standard_key(self):
        d = _make_decision(
            confirmation_strength="STRONG",
            entry_timing="EARLY",
            market_regime="TRENDING",
        )
        assert build_cohort_key(d) == "STRONG+EARLY+TRENDING"

    def test_weak_late_ranging(self):
        d = _make_decision(
            confirmation_strength="WEAK",
            entry_timing="LATE",
            market_regime="RANGING",
        )
        assert build_cohort_key(d) == "WEAK+LATE+RANGING"

    def test_mid_timing(self):
        d = _make_decision(
            confirmation_strength="STRONG",
            entry_timing="MID",
            market_regime="TRENDING",
        )
        assert build_cohort_key(d) == "STRONG+MID+TRENDING"


# ─── LOWERCASE NORMALIZATION ──────────────────────────────────────────────────


class TestLowercaseNormalization:
    def test_lowercase_converted_to_upper(self):
        d = _make_decision(
            confirmation_strength="strong",
            entry_timing="early",
            market_regime="trending",
        )
        assert build_cohort_key(d) == "STRONG+EARLY+TRENDING"

    def test_mixed_case(self):
        d = _make_decision(
            confirmation_strength="Strong",
            entry_timing="Early",
            market_regime="Trending",
        )
        assert build_cohort_key(d) == "STRONG+EARLY+TRENDING"


# ─── WHITESPACE TRIMMING ─────────────────────────────────────────────────────


class TestWhitespaceTrimming:
    def test_leading_trailing_spaces_stripped(self):
        d = _make_decision(
            confirmation_strength=" STRONG ",
            entry_timing=" EARLY ",
            market_regime=" TRENDING ",
        )
        assert build_cohort_key(d) == "STRONG+EARLY+TRENDING"

    def test_tabs_and_newlines_stripped(self):
        d = _make_decision(
            confirmation_strength="\tSTRONG\n",
            entry_timing="\nEARLY\t",
            market_regime="\tTRENDING\n",
        )
        assert build_cohort_key(d) == "STRONG+EARLY+TRENDING"


# ─── MISSING FIELDS ──────────────────────────────────────────────────────────


class TestMissingConfirmation:
    def test_none_confirmation(self):
        d = _make_decision(
            confirmation_strength=None,
            entry_timing="EARLY",
            market_regime="TRENDING",
        )
        assert build_cohort_key(d) == "UNKNOWN+EARLY+TRENDING"

    def test_missing_attribute_confirmation(self):
        d = _make_decision(entry_timing="EARLY", market_regime="TRENDING")
        assert build_cohort_key(d) == "UNKNOWN+EARLY+TRENDING"


class TestMissingTiming:
    def test_none_timing(self):
        d = _make_decision(
            confirmation_strength="STRONG",
            entry_timing=None,
            market_regime="TRENDING",
        )
        assert build_cohort_key(d) == "STRONG+UNKNOWN+TRENDING"

    def test_missing_attribute_timing(self):
        d = _make_decision(confirmation_strength="STRONG", market_regime="TRENDING")
        assert build_cohort_key(d) == "STRONG+UNKNOWN+TRENDING"


class TestMissingRegime:
    def test_none_regime(self):
        d = _make_decision(
            confirmation_strength="STRONG",
            entry_timing="EARLY",
            market_regime=None,
        )
        assert build_cohort_key(d) == "STRONG+EARLY+UNKNOWN"

    def test_missing_attribute_regime(self):
        d = _make_decision(confirmation_strength="STRONG", entry_timing="EARLY")
        assert build_cohort_key(d) == "STRONG+EARLY+UNKNOWN"


class TestAllMissing:
    def test_all_none(self):
        d = _make_decision(
            confirmation_strength=None,
            entry_timing=None,
            market_regime=None,
        )
        assert build_cohort_key(d) == "UNKNOWN+UNKNOWN+UNKNOWN"

    def test_no_attributes_at_all(self):
        d = SimpleNamespace()
        assert build_cohort_key(d) == "UNKNOWN+UNKNOWN+UNKNOWN"


# ─── EMPTY STRING FALLBACK ────────────────────────────────────────────────────


class TestEmptyStringFallback:
    def test_empty_string_becomes_unknown(self):
        d = _make_decision(
            confirmation_strength="",
            entry_timing="",
            market_regime="",
        )
        assert build_cohort_key(d) == "UNKNOWN+UNKNOWN+UNKNOWN"

    def test_whitespace_only_becomes_unknown(self):
        d = _make_decision(
            confirmation_strength="   ",
            entry_timing="\t",
            market_regime="\n",
        )
        assert build_cohort_key(d) == "UNKNOWN+UNKNOWN+UNKNOWN"


# ─── PARTIAL OBJECT ───────────────────────────────────────────────────────────


class TestPartialObject:
    def test_only_one_attribute(self):
        d = _make_decision(market_regime="TRENDING")
        assert build_cohort_key(d) == "UNKNOWN+UNKNOWN+TRENDING"

    def test_dict_like_object_without_attrs(self):
        """Plain object with no matching attributes."""
        d = object()
        assert build_cohort_key(d) == "UNKNOWN+UNKNOWN+UNKNOWN"


# ─── NON-STRING VALUES ────────────────────────────────────────────────────────


class TestNonStringValues:
    def test_integer_converts(self):
        d = _make_decision(
            confirmation_strength=1,
            entry_timing=2,
            market_regime=3,
        )
        assert build_cohort_key(d) == "1+2+3"

    def test_float_converts(self):
        d = _make_decision(
            confirmation_strength=1.5,
            entry_timing=2.0,
            market_regime=3.0,
        )
        assert build_cohort_key(d) == "1.5+2.0+3.0"

    def test_bool_converts(self):
        d = _make_decision(
            confirmation_strength=True,
            entry_timing=False,
            market_regime=True,
        )
        assert build_cohort_key(d) == "TRUE+FALSE+TRUE"

    def test_zero_is_not_falsy(self):
        """0 converts to '0', not 'UNKNOWN'."""
        d = _make_decision(
            confirmation_strength=0,
            entry_timing=0,
            market_regime=0,
        )
        assert build_cohort_key(d) == "0+0+0"
