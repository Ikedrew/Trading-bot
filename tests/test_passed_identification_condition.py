"""
Tests for passed_identification_condition (Phase 1B).

Covers the six required semantic combinations plus non-mutation and the
additive field on the existing Opportunity Data record.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.v10.identification_condition import (  # noqa: E402
    compute_passed_identification_condition,
    IDENTIFICATION_VERDICT_INVALID,
    IDENTIFICATION_VERDICT_VALID,
    IDENTIFICATION_VERDICT_WATCHING,
)
from core.opportunity.opportunity import Opportunity  # noqa: E402


# ═══════════════════════════════════════════════════════════════════════════════
# PURE-PREDICATE SEMANTICS (the six required combinations)
# ═══════════════════════════════════════════════════════════════════════════════


def test_valid_plus_eligible_is_true():
    assert compute_passed_identification_condition(
        identification_verdict=IDENTIFICATION_VERDICT_VALID,
        eligible_horizons=["SCALP", "INTRADAY"],
    ) is True


def test_valid_plus_none_eligible_is_false():
    assert compute_passed_identification_condition(
        identification_verdict=IDENTIFICATION_VERDICT_VALID,
        eligible_horizons=[],
    ) is False


def test_watching_plus_eligible_is_false():
    assert compute_passed_identification_condition(
        identification_verdict=IDENTIFICATION_VERDICT_WATCHING,
        eligible_horizons=["SCALP", "INTRADAY"],
    ) is False


def test_watching_plus_none_is_false():
    assert compute_passed_identification_condition(
        identification_verdict=IDENTIFICATION_VERDICT_WATCHING,
        eligible_horizons=[],
    ) is False


def test_invalid_plus_eligible_is_false():
    assert compute_passed_identification_condition(
        identification_verdict=IDENTIFICATION_VERDICT_INVALID,
        eligible_horizons=["SCALP"],
    ) is False


def test_invalid_plus_none_is_false():
    assert compute_passed_identification_condition(
        identification_verdict=IDENTIFICATION_VERDICT_INVALID,
        eligible_horizons=[],
    ) is False


# ═══════════════════════════════════════════════════════════════════════════════
# NON-MUTATION (predicate must not alter its inputs)
# ═══════════════════════════════════════════════════════════════════════════════


def test_predicate_does_not_mutate_inputs():
    verdict = IDENTIFICATION_VERDICT_VALID
    horizons = ["SCALP", "INTRADAY"]
    result = compute_passed_identification_condition(
        identification_verdict=verdict, eligible_horizons=horizons
    )

    assert result is True
    # Inputs untouched.
    assert verdict == IDENTIFICATION_VERDICT_VALID
    assert horizons == ["SCALP", "INTRADAY"]


def test_predicate_lenient_on_sequence_types():
    # Accepts any sequence, including tuples and single-element lists.
    assert compute_passed_identification_condition(
        identification_verdict=IDENTIFICATION_VERDICT_VALID,
        eligible_horizons=("SCALP",),
    ) is True
    assert compute_passed_identification_condition(
        identification_verdict=IDENTIFICATION_VERDICT_VALID,
        eligible_horizons=[],
    ) is False


# ═══════════════════════════════════════════════════════════════════════════════
# ADDITIVE FIELD ON EXISTING OPPORTUNITY DATA RECORD (opportunities_v1)
# ═══════════════════════════════════════════════════════════════════════════════


def _make_opp() -> Opportunity:
    return Opportunity(
        opportunity_id="SYM_1784800000_TWEEZER_TOP",
        symbol="SYM",
        cycle_id=1,
        direction="SELL",
        pattern="TWEEZER_TOP",
        detection_timeframe="M5",
        detected_at_bar_time=1784800000,
        detected_at_utc="2025-01-01T00:00:00.000Z",
    )


def test_opportunity_field_defaults_false():
    opp = _make_opp()
    # Additive: existing constructors see the field default to False.
    assert opp.passed_identification_condition is False


def test_opportunity_field_serializes_via_to_dict():
    opp = _make_opp()
    opp.passed_identification_condition = True
    # asdict (to_dict) must include the additive field.
    assert opp.to_dict()["passed_identification_condition"] is True
