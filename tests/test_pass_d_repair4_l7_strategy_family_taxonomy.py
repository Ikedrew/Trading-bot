"""Pass D Repair 4 — L7 current StrategyFamily taxonomy coverage.

L7's strategy coverage was previously derived from a stale hard-coded legacy
list ``{"REVERSAL", "CONTINUATION", "FALSE_BREAK"}`` inside
``experiment_base.check_readiness``.  That list mixed legacy names (REVERSAL,
CONTINUATION) with one current name and omitted the newer V10 families
(LIQUIDITY_SWEEP_REVERSAL, TREND_CONTINUATION, BREAKOUT_EXPANSION,
MEAN_REVERSION, RANGE_REACTION).

These tests prove L7's strategy-family denominator now derives from the current
authoritative ``core.v10.strategy_family.StrategyFamily`` enum: every current
family appears exactly once, legacy aliases resolve deterministically, retired
and unknown values fail closed, and snapshot evidence stays isolated with no
filesystem/S3/legacy-loader escape.
"""

from __future__ import annotations

import builtins
import pathlib

import pytest

from core.v10.strategy_family import StrategyFamily

from research_engine.experiments import strategy_family_taxonomy as T
from research_engine.experiments.experiment_base import (
    ReadinessStatus,
    check_readiness,
)


def _rows(*strategy_ids: str) -> list[dict]:
    return [{"identity": {"strategy_id": sid}} for sid in strategy_ids]


# ─── authoritative taxonomy source ────────────────────────────────────────────


def test_l7_derives_expected_families_from_current_authority():
    expected = tuple(
        family.value for family in StrategyFamily if family is not StrategyFamily.NONE
    )
    assert T.CURRENT_STRATEGY_FAMILIES == expected
    assert T.CURRENT_STRATEGY_FAMILIES == (
        "LIQUIDITY_SWEEP_REVERSAL",
        "FALSE_BREAK",
        "TREND_CONTINUATION",
        "BREAKOUT_EXPANSION",
        "MEAN_REVERSION",
        "RANGE_REACTION",
    )


def test_authority_is_core_v10_not_legacy_layer():
    assert T.StrategyFamily.__module__ == "core.v10.strategy_family"


def test_every_current_family_appears_exactly_once():
    families = T.CURRENT_STRATEGY_FAMILIES
    assert len(families) == len(set(families)) == 6
    assert "NONE" not in families


def test_deterministic_ordering():
    assert T.CURRENT_STRATEGY_FAMILIES == tuple(
        f.value for f in StrategyFamily if f is not StrategyFamily.NONE
    )


# ─── retired / alias / unknown / missing handling ─────────────────────────────


def test_retired_families_do_not_count_as_current():
    assert T.RETIRED_FAMILIES.isdisjoint(T.CURRENT_STRATEGY_FAMILIES)
    for retired in T.RETIRED_FAMILIES:
        assert T.normalize_strategy_family(retired) is None
        assert T.classify_strategy_family(retired) == (T.RETIRED, None)


def test_legacy_aliases_map_deterministically():
    assert T.LEGACY_ALIASES == {
        "REVERSAL": "LIQUIDITY_SWEEP_REVERSAL",
        "CONTINUATION": "TREND_CONTINUATION",
    }
    assert T.normalize_strategy_family("REVERSAL") == "LIQUIDITY_SWEEP_REVERSAL"
    assert T.normalize_strategy_family("CONTINUATION") == "TREND_CONTINUATION"
    assert T.classify_strategy_family("REVERSAL") == (
        T.LEGACY_ALIAS,
        "LIQUIDITY_SWEEP_REVERSAL",
    )


def test_unknown_family_fails_closed():
    assert T.normalize_strategy_family("TOTALLY_FAKE_FAMILY") is None
    assert T.classify_strategy_family("TOTALLY_FAKE_FAMILY") == (T.UNKNOWN, None)


def test_none_is_not_a_clean_strategy():
    assert T.normalize_strategy_family("NONE") is None
    assert T.classify_strategy_family("NONE") == (T.NONE, None)


def test_missing_family_not_observed():
    assert T.classify_strategy_family(None) == (T.MISSING, None)
    assert T.classify_strategy_family("") == (T.MISSING, None)
    assert T.normalize_strategy_family(None) is None
    assert T.normalize_strategy_family("") is None


# ─── observed-family coverage ─────────────────────────────────────────────────


def test_observed_current_family_counted_correctly():
    rows = _rows("LIQUIDITY_SWEEP_REVERSAL", "FALSE_BREAK", "MEAN_REVERSION")
    observed = T.observed_current_families(row["identity"]["strategy_id"] for row in rows)
    assert observed == ("LIQUIDITY_SWEEP_REVERSAL", "FALSE_BREAK", "MEAN_REVERSION")


def test_duplicate_records_do_not_inflate_family_coverage():
    values = ["FALSE_BREAK"] * 70
    observed = T.observed_current_families(values)
    assert observed == ("FALSE_BREAK",)
    assert len(T.CURRENT_STRATEGY_FAMILIES) == 6  # denominator unchanged


def test_legacy_alias_resolves_into_current_family_not_new_one():
    observed = T.observed_current_families(["REVERSAL", "LIQUIDITY_SWEEP_REVERSAL"])
    assert observed == ("LIQUIDITY_SWEEP_REVERSAL",)


# ─── check_readiness uses the current taxonomy ────────────────────────────────


def test_check_readiness_counts_current_family_as_clean():
    rows = _rows("LIQUIDITY_SWEEP_REVERSAL") * 10
    status, reason, coverage = check_readiness(
        rows, min_samples=1, require_outcome=False, require_strategy=True,
    )
    assert coverage["strategy"] == 1.0
    assert status == ReadinessStatus.READY


def test_check_readiness_counts_legacy_alias_as_clean():
    rows = _rows("REVERSAL") * 10
    _, _, coverage = check_readiness(
        rows, min_samples=1, require_outcome=False, require_strategy=True,
    )
    assert coverage["strategy"] == 1.0


def test_check_readiness_does_not_count_unknown_or_none():
    rows = _rows("NONE", "FAKE", "REVERSAL") * 10
    _, _, coverage = check_readiness(
        rows, min_samples=1, require_outcome=False, require_strategy=True,
    )
    # Only REVERSAL (legacy alias -> current) is clean.
    assert coverage["strategy"] == pytest.approx(1 / 3)


def test_scientific_threshold_unchanged():
    good = _rows("FALSE_BREAK") * 6 + _rows("NONE") * 4
    _, _, cov_good = check_readiness(
        good, min_samples=1, require_outcome=False, require_strategy=True,
    )
    assert cov_good["strategy"] == pytest.approx(0.6)
    status, _, _ = check_readiness(
        good, min_samples=1, require_outcome=False, require_strategy=True,
        strategy_threshold=0.50,
    )
    assert status == ReadinessStatus.READY

    sparse = _rows("FALSE_BREAK") * 3 + _rows("NONE") * 7
    status_sparse, _, cov_sparse = check_readiness(
        sparse, min_samples=1, require_outcome=False, require_strategy=True,
    )
    assert cov_sparse["strategy"] == pytest.approx(0.3)
    assert status_sparse == ReadinessStatus.WAITING_DATA


# ─── snapshot isolation and no IO escape ──────────────────────────────────────


def test_taxonomy_is_stateless_and_snapshot_isolated():
    a = T.observed_current_families(["FALSE_BREAK"])
    b = T.observed_current_families(["RANGE_REACTION"])
    assert a == ("FALSE_BREAK",)
    assert b == ("RANGE_REACTION",)
    assert T.observed_current_families(["FALSE_BREAK"]) == a


def test_no_filesystem_or_s3_access(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("filesystem/network access forbidden")

    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(pathlib.Path, "open", forbidden)
    monkeypatch.setattr(pathlib.Path, "read_text", forbidden)

    assert T.normalize_strategy_family("FALSE_BREAK") == "FALSE_BREAK"
    assert T.observed_current_families(["REVERSAL", "FALSE_BREAK"]) == (
        "LIQUIDITY_SWEEP_REVERSAL",
        "FALSE_BREAK",
    )
    rows = _rows("FALSE_BREAK") * 10
    _, _, coverage = check_readiness(
        rows, min_samples=1, require_outcome=False, require_strategy=True,
    )
    assert coverage["strategy"] == 1.0


def test_no_legacy_taxonomy_loader_escape():
    assert T.StrategyFamily is StrategyFamily
    assert "core.strategy_family" not in T.LEGACY_ALIASES


# ─── non-vacuous regression against the stale taxonomy ────────────────────────


def test_regression_would_fail_under_stale_taxonomy():
    assert "REVERSAL" not in T.CURRENT_STRATEGY_FAMILIES
    assert "LIQUIDITY_SWEEP_REVERSAL" in T.CURRENT_STRATEGY_FAMILIES
    rows = _rows("LIQUIDITY_SWEEP_REVERSAL") * 10
    _, _, coverage = check_readiness(
        rows, min_samples=1, require_outcome=False, require_strategy=True,
    )
    assert coverage["strategy"] == 1.0  # stale list would report 0.0
