"""Tests for the horizon_candidates persistence dataset.

Verifies that classify_horizons() results are persisted as one record
per evaluated horizon (eligible AND ineligible), with correct selection
status, lineage, deterministic IDs, and that persistence failure never
affects horizon classification or trading behaviour.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.horizon.horizon_classifier import classify_horizons
from core.horizon.horizon_models import HorizonAssessment
import core.persistence.horizon_candidates_writer as hcw


# ═══════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _redirect_writer(tmp_path):
    """Redirect the writer's local dir to a temp path for every test."""
    original = hcw._LOCAL_DIR
    hcw._LOCAL_DIR = str(tmp_path)
    yield
    hcw._LOCAL_DIR = original


def _persist_classification(
    *,
    selected_horizon: str = "",
    symbol: str = "TEST",
    bar_time: float = 1000.0,
    lineage: dict | None = None,
    **classify_kwargs,
) -> list[dict]:
    """Run classify_horizons + build + persist in one step. Returns records."""
    result = classify_horizons(**classify_kwargs)
    records = hcw.build_horizon_candidate_records(
        assessments=result.assessments,
        selected_horizon=selected_horizon,
        symbol=symbol,
        bar_time=bar_time,
        lineage=lineage,
    )
    hcw.persist_horizon_candidates(candidates=records)
    return records


def _read_records():
    records = []
    for f in Path(hcw._LOCAL_DIR).rglob("*.jsonl"):
        for line in f.read_text(encoding="utf-8").strip().split("\n"):
            if line.strip():
                records.append(json.loads(line))
    return records


def _trending_kwargs():
    """Classification kwargs that make EXTENDED eligible (trending regime)."""
    return dict(
        strategy_type="CONTINUATION",
        strategy_confidence=0.75,
        h4_regime="TRENDING",
        h4_regime_confidence=0.8,
        h1_direction="BULLISH",
        h1_bos_confirmed=True,
        htf_alignment=0.8,
        h4_alignment=0.8,
        market_quality=0.7,
        chop_clarity=0.7,
        volatility_quality=0.7,
        pattern="TEST_PATTERN",
        direction="BUY",
    )


def _range_kwargs():
    """Classification kwargs where EXTENDED is ineligible (range regime)."""
    return dict(
        strategy_type="REVERSAL",
        strategy_confidence=0.6,
        h4_regime="RANGE",
        h4_regime_confidence=0.7,
        h1_direction="NEUTRAL",
        h1_bos_confirmed=False,
        htf_alignment=0.5,
        h4_alignment=0.4,
        market_quality=0.5,
        chop_clarity=0.5,
        volatility_quality=0.5,
        pattern="TEST_PATTERN",
        direction="SELL",
    )


# ═══════════════════════════════════════════════════════════════
# TEST 1 — ALL HORIZONS PERSISTED
# ═══════════════════════════════════════════════════════════════


class TestAllHorizonsPersisted:
    def test_three_records_for_three_horizons(self):
        _persist_classification(**_trending_kwargs())
        records = _read_records()
        assert len(records) == 3

    def test_one_record_per_horizon(self):
        """Each record is an INDEPENDENT record — not a collapsed list."""
        _persist_classification(**_trending_kwargs())
        records = _read_records()
        horizons = [r["horizon"] for r in records]
        assert sorted(horizons) == ["EXTENDED", "INTRADAY", "SCALP"]

    def test_no_nested_horizons_list(self):
        """The dataset must NOT collapse into a single {horizons: [...]} record."""
        _persist_classification(**_trending_kwargs())
        records = _read_records()
        for rec in records:
            assert "horizons" not in rec  # no nested collapse
            assert rec["horizon"] in ("SCALP", "INTRADAY", "EXTENDED")


# ═══════════════════════════════════════════════════════════════
# TEST 2 — INELIGIBLE HORIZONS PRESERVED
# ═══════════════════════════════════════════════════════════════


class TestIneligiblePreserved:
    def test_ineligible_extended_still_persisted(self):
        """Range regime makes EXTENDED ineligible — it must still appear."""
        _persist_classification(**_range_kwargs())
        records = _read_records()
        extended = [r for r in records if r["horizon"] == "EXTENDED"]
        assert len(extended) == 1  # NOT silently discarded
        assert extended[0]["eligible"] is False

    def test_ineligible_marked_ineligible(self):
        _persist_classification(**_range_kwargs())
        records = _read_records()
        extended = [r for r in records if r["horizon"] == "EXTENDED"][0]
        assert extended["selection_status"] == "INELIGIBLE"

    def test_default_classification_persists_all_three(self):
        """Even with empty context, classify_horizons returns 3 — all persisted."""
        _persist_classification()
        records = _read_records()
        assert len(records) == 3


# ═══════════════════════════════════════════════════════════════
# TEST 3 — CONFIDENCE PRESERVED
# ═══════════════════════════════════════════════════════════════


class TestConfidencePreserved:
    def test_each_horizon_retains_own_confidence(self):
        result = classify_horizons(**_trending_kwargs())
        _persist_classification(**_trending_kwargs())
        records = _read_records()
        by_horizon = {r["horizon"]: r for r in records}
        for assessment in result.assessments:
            assert by_horizon[assessment.horizon]["confidence"] == pytest.approx(
                assessment.confidence
            )

    def test_confidences_differ_across_horizons(self):
        """Horizons genuinely produce different confidences — not substituted."""
        _persist_classification(**_trending_kwargs())
        records = _read_records()
        confs = {r["horizon"]: r["confidence"] for r in records}
        # At least SCALP and EXTENDED should differ in trending regime
        assert confs["SCALP"] != confs["EXTENDED"]

    def test_classifier_confidence_not_recalculated(self):
        """Persisted confidence == classifier's rounded value, verbatim."""
        result = classify_horizons(**_trending_kwargs())
        _persist_classification(**_trending_kwargs())
        records = _read_records()
        by_horizon = {r["horizon"]: r for r in records}
        for assessment in result.assessments:
            # Classifier already rounds to 4dp — persisted value must match
            assert by_horizon[assessment.horizon]["confidence"] == assessment.confidence


# ═══════════════════════════════════════════════════════════════
# TEST 4 — REASONING / EVIDENCE PRESERVED
# ═══════════════════════════════════════════════════════════════


class TestReasoningEvidencePreserved:
    def test_reasoning_survives_serialization(self):
        _persist_classification(**_trending_kwargs())
        records = _read_records()
        for rec in records:
            # The classifier's per-assessment reasoning is a STRING
            assert isinstance(rec["reasoning"], str)
            assert len(rec["reasoning"]) > 0

    def test_evidence_survives_serialization(self):
        _persist_classification(**_trending_kwargs())
        records = _read_records()
        for rec in records:
            assert isinstance(rec["evidence"], dict)
            # Eligible horizons carry the full context evidence
            if rec["eligible"]:
                assert "h4_regime" in rec["evidence"]
                assert "h1_direction" in rec["evidence"]

    def test_ineligible_evidence_carries_failure_reason(self):
        """Ineligible assessments carry requirement-failure evidence."""
        _persist_classification(**_range_kwargs())
        records = _read_records()
        extended = [r for r in records if r["horizon"] == "EXTENDED"][0]
        assert not extended["eligible"]
        # The classifier's ineligible evidence includes the requirement
        assert "requirement" in extended["evidence"] or "h4_regime" in extended["evidence"]

    def test_reasoning_matches_classifier_verbatim(self):
        result = classify_horizons(**_trending_kwargs())
        _persist_classification(**_trending_kwargs())
        records = _read_records()
        by_horizon = {r["horizon"]: r for r in records}
        for assessment in result.assessments:
            assert by_horizon[assessment.horizon]["reasoning"] == assessment.reasoning


# ═══════════════════════════════════════════════════════════════
# TEST 5 — SELECTED HORIZON
# ═══════════════════════════════════════════════════════════════


class TestSelectedHorizon:
    def test_selected_marked_when_v10_horizon_available(self):
        _persist_classification(
            selected_horizon="SCALP",
            **_trending_kwargs(),
        )
        records = _read_records()
        selected = [r for r in records if r["selection_status"] == "SELECTED"]
        assert len(selected) == 1
        assert selected[0]["horizon"] == "SCALP"

    def test_eligible_non_selected_marked_rejected(self):
        _persist_classification(
            selected_horizon="SCALP",
            **_trending_kwargs(),
        )
        records = _read_records()
        rejected = [
            r for r in records
            if r["selection_status"] == "REJECTED"
        ]
        # Eligible horizons that weren't selected
        for rec in rejected:
            assert rec["eligible"] is True
            assert rec["horizon"] != "SCALP"

    def test_exactly_one_selected(self):
        _persist_classification(
            selected_horizon="INTRADAY",
            **_trending_kwargs(),
        )
        records = _read_records()
        selected = [r for r in records if r["selection_status"] == "SELECTED"]
        assert len(selected) == 1

    def test_no_selection_yields_not_applicable(self):
        """Legacy path: no V10 horizon selection — eligible horizons are NOT_APPLICABLE."""
        _persist_classification(
            selected_horizon="",  # no V10 pipeline result
            **_trending_kwargs(),
        )
        records = _read_records()
        for rec in records:
            if rec["eligible"]:
                assert rec["selection_status"] == "NOT_APPLICABLE"
            else:
                assert rec["selection_status"] == "INELIGIBLE"

    def test_selection_status_never_invents_selection(self):
        """No re-ranking: selection derives only from the provided selected_horizon."""
        result = classify_horizons(**_trending_kwargs())
        records = hcw.build_horizon_candidate_records(
            assessments=result.assessments,
            selected_horizon="EXTENDED",
            symbol="TEST",
            bar_time=1000.0,
        )
        statuses = {r["horizon"]: r["selection_status"] for r in records}
        assert statuses["EXTENDED"] == "SELECTED"
        # Other eligible horizons rejected — not re-ranked into selection
        for hz in ("SCALP", "INTRADAY"):
            if any(a.horizon == hz and a.eligible for a in result.assessments):
                assert statuses[hz] == "REJECTED"


# ═══════════════════════════════════════════════════════════════
# TEST 6 — LINEAGE
# ═══════════════════════════════════════════════════════════════


class TestLineage:
    def test_observation_id_computed_when_absent(self):
        _persist_classification(**_trending_kwargs())
        records = _read_records()
        for rec in records:
            assert rec["observation_id"] == "TEST.M5.1000"

    def test_missing_canonical_id_is_not_fabricated_from_observation(self):
        _persist_classification(**_trending_kwargs())
        records = _read_records()
        for rec in records:
            assert rec["canonical_opportunity_id"] == ""
            assert rec["canonical_opportunity_id"] != rec["observation_id"]

    def test_canonical_id_passed_via_lineage(self):
        _persist_classification(
            lineage={
                "canonical_opportunity_id": "TEST*1000*TEST_PATTERN",
                "correlation_id": "COR-20260830-42-TEST-AB12",
                "cycle_id": 42,
            },
            **_trending_kwargs(),
        )
        records = _read_records()
        for rec in records:
            assert rec["canonical_opportunity_id"] == "TEST*1000*TEST_PATTERN"
            assert rec["correlation_id"] == "COR-20260830-42-TEST-AB12"
            assert rec["cycle_id"] == 42

    def test_decision_id_empty_when_not_minted(self):
        """decision_id is minted downstream — must remain empty, not invented."""
        _persist_classification(**_trending_kwargs())
        records = _read_records()
        for rec in records:
            assert rec["decision_id"] == ""

    def test_decision_id_populated_when_provided(self):
        _persist_classification(
            lineage={"decision_id": "D123"},
            **_trending_kwargs(),
        )
        records = _read_records()
        for rec in records:
            assert rec["decision_id"] == "D123"


# ═══════════════════════════════════════════════════════════════
# TEST 7 — DETERMINISTIC CANDIDATE ID
# ═══════════════════════════════════════════════════════════════


class TestDeterministicCandidateId:
    def test_candidate_id_format(self):
        _persist_classification(
            lineage={"canonical_opportunity_id": "TEST*1000*PATTERN"},
            **_trending_kwargs(),
        )
        records = _read_records()
        for rec in records:
            assert rec["candidate_id"] == f"TEST*1000*PATTERN:{rec['horizon']}"

    def test_candidate_ids_unique(self):
        _persist_classification(**_trending_kwargs())
        records = _read_records()
        ids = [r["candidate_id"] for r in records]
        assert len(ids) == len(set(ids))

    def test_candidate_id_stable_across_calls(self):
        """Same canonical+horizon → same candidate_id every time."""
        r1 = _persist_classification(
            lineage={"canonical_opportunity_id": "TEST*1000*PATTERN"},
            **_trending_kwargs(),
        )
        r2 = _persist_classification(
            lineage={"canonical_opportunity_id": "TEST*1000*PATTERN"},
            **_trending_kwargs(),
        )
        ids1 = {r["candidate_id"] for r in r1}
        ids2 = {r["candidate_id"] for r in r2}
        assert ids1 == ids2


# ═══════════════════════════════════════════════════════════════
# TEST 8 — PERSISTENCE FAILURE ISOLATION
# ═══════════════════════════════════════════════════════════════


class TestFailureIsolation:
    def test_classification_unchanged_when_writer_fails(self):
        """The classifier result must be identical with or without persistence."""
        kwargs = _trending_kwargs()
        expected = classify_horizons(**kwargs)

        original = hcw.persist_horizon_candidates

        def _failing_persist(**kw):
            raise RuntimeError("simulated disk failure")

        hcw.persist_horizon_candidates = _failing_persist
        try:
            actual = classify_horizons(**kwargs)
        finally:
            hcw.persist_horizon_candidates = original

        # Classification is a pure function — verify it returns the same result
        assert len(actual.assessments) == len(expected.assessments)
        for a, e in zip(actual.assessments, expected.assessments):
            assert a.horizon == e.horizon
            assert a.eligible == e.eligible
            assert a.confidence == e.confidence

    def test_build_records_never_mutates_assessments(self):
        """The record builder must not mutate the original assessment objects."""
        result = classify_horizons(**_trending_kwargs())
        before = [(a.horizon, a.eligible, a.confidence, a.reasoning) for a in result.assessments]
        hcw.build_horizon_candidate_records(
            assessments=result.assessments,
            selected_horizon="SCALP",
            symbol="TEST",
            bar_time=1000.0,
        )
        after = [(a.horizon, a.eligible, a.confidence, a.reasoning) for a in result.assessments]
        assert before == after

    def test_persist_failure_returns_false_not_raise(self):
        def _bad():
            return hcw.persist_horizon_candidates(candidates=[{"symbol": None}])

        # Must return False, never raise — even with malformed input
        # (json.dumps of {"symbol": None} actually succeeds; force a real failure)
        hcw._LOCAL_DIR = None  # Path(None) raises TypeError inside
        try:
            result = hcw.persist_horizon_candidates(
                candidates=[{"symbol": "X", "bar_time": 1.0, "horizon": "SCALP"}]
            )
            assert result is False
        finally:
            hcw._LOCAL_DIR = "."


# ═══════════════════════════════════════════════════════════════
# TEST 9 — SCHEMA / PERSISTENCE LOCATION
# ═══════════════════════════════════════════════════════════════


class TestSchemaAndLocation:
    def test_schema_version_present(self):
        _persist_classification(**_trending_kwargs())
        records = _read_records()
        for rec in records:
            assert rec["schema_version"] == "horizon_candidates_v1"

    def test_written_to_symbol_date_partition(self):
        _persist_classification(bar_time=1000.0)
        expected_dir = Path(hcw._LOCAL_DIR) / "TEST"
        assert expected_dir.exists()
        jsonl_files = list(expected_dir.glob("*.jsonl"))
        assert len(jsonl_files) == 1
        assert jsonl_files[0].stem == "1970-01-01"

    def test_engine_field_present(self):
        _persist_classification(**_trending_kwargs())
        records = _read_records()
        for rec in records:
            assert rec["engine"] == "V10"

    def test_all_json_serialisable(self):
        _persist_classification(**_trending_kwargs())
        records = _read_records()
        for rec in records:
            json.dumps(rec, separators=(",", ":"))

    def test_evaluated_at_utc_present(self):
        _persist_classification(**_trending_kwargs())
        records = _read_records()
        for rec in records:
            assert rec["evaluated_at_utc"]
            assert rec["evaluated_at_utc"].endswith("Z")
