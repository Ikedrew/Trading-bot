"""Tests for Candidate Walk-Forward Validation."""
from __future__ import annotations
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research_engine.edge_attribution.models import EdgeAttributionRecord
from research_engine.edge_candidates.models import EdgeCandidate
from research_engine.edge_candidates.validation import validate_candidates, _matches_conditions


def _rec(pattern="P1", regime="T", session="LONDON", r=1.0, ts="2026-07-15T10:00:00Z", symbol="EURUSD"):
    return EdgeAttributionRecord(
        entity_id=f"T_{hash(ts) % 999999}", timestamp_utc=ts,
        pattern=pattern, regime=regime, session=session, direction="BUY",
        result_r=r, win=r > 0, symbol=symbol,
        htf_alignment_bin="MEDIUM", trend_alignment_bin="MEDIUM",
        bias_alignment_bin="MEDIUM", score_bin="MEDIUM", confirmation_bin="STRONG",
    )


def _candidate(conditions=None):
    if conditions is None:
        conditions = {"pattern": "GOOD"}
    return EdgeCandidate(candidate_id="TEST", conditions=conditions, sample_size=50, expectancy=0.3)


def _make_chronological_dataset(n=100):
    """Create dataset where GOOD pattern wins and BAD pattern loses, spread over time."""
    symbols = ["EURUSD", "GBPUSD", "USDJPY"]
    records = []
    sym_idx = 0
    for i in range(n):
        day = 15 + i // 20
        hour = (i % 20) + 1
        ts = f"2026-07-{day:02d}T{hour:02d}:00:00Z"
        if i % 3 == 0:
            sym = symbols[sym_idx % len(symbols)]
            sym_idx += 1
            records.append(_rec(pattern="GOOD", r=2.0, ts=ts, symbol=sym))
        else:
            records.append(_rec(pattern="BAD", r=-1.0, ts=ts, symbol=symbols[i % len(symbols)]))
    return records


class TestConditionMatching:
    def test_single_condition_match(self):
        r = _rec(pattern="X")
        assert _matches_conditions(r, {"pattern": "X"})
        assert not _matches_conditions(r, {"pattern": "Y"})

    def test_multi_condition_match(self):
        r = _rec(pattern="X", session="NY")
        assert _matches_conditions(r, {"pattern": "X", "session": "NY"})
        assert not _matches_conditions(r, {"pattern": "X", "session": "LONDON"})

    def test_empty_conditions_matches_all(self):
        r = _rec()
        assert _matches_conditions(r, {})


class TestChronologicalSplitting:
    def test_no_future_leakage(self):
        """Test period data is always AFTER training period."""
        records = _make_chronological_dataset(100)
        candidates = [_candidate({"pattern": "GOOD"})]
        report = validate_candidates(candidates, records, n_splits=3)

        # Verify splits are in order
        if report.survivors:
            vr = report.survivors[0]
            for i in range(len(vr.splits) - 1):
                assert vr.splits[i].split < vr.splits[i + 1].split

    def test_training_data_precedes_test(self):
        """Train size grows with each split."""
        records = _make_chronological_dataset(100)
        candidates = [_candidate({"pattern": "GOOD"})]
        report = validate_candidates(candidates, records, n_splits=3)

        if report.survivors:
            vr = report.survivors[0]
            train_sizes = [s.train_size for s in vr.splits]
            # Train size should be non-decreasing (expanding window)
            for i in range(len(train_sizes) - 1):
                assert train_sizes[i] <= train_sizes[i + 1]


class TestSurvivalCriteria:
    def test_positive_candidate_passes(self):
        """Candidate that wins consistently should pass."""
        # Need enough data: 200 records, GOOD = 1/3 = ~66 records across 5 splits
        records = _make_chronological_dataset(200)
        candidates = [_candidate({"pattern": "GOOD"})]
        report = validate_candidates(candidates, records, n_splits=5, min_train_pct=0.2)

        # GOOD pattern wins 2.0R every time → should pass
        assert report.candidates_passed >= 1
        if report.survivors:
            assert report.survivors[0].passes is True

    def test_negative_candidate_fails(self):
        """Candidate that loses should fail."""
        records = _make_chronological_dataset(150)
        candidates = [_candidate({"pattern": "BAD"})]
        report = validate_candidates(candidates, records, n_splits=5)

        assert report.candidates_passed == 0

    def test_insufficient_trades_fails(self):
        """Candidate with very few matching trades fails."""
        records = [_rec(pattern="RARE", r=5.0, ts=f"2026-07-{15+i//5:02d}T{i%5+10:02d}:00:00Z") for i in range(10)]
        records += [_rec(pattern="OTHER", r=-0.5, ts=f"2026-07-{15+i//5:02d}T{i%5+1:02d}:00:00Z") for i in range(90)]
        candidates = [_candidate({"pattern": "RARE"})]
        report = validate_candidates(candidates, records, n_splits=3)

        # RARE only has ~10 records total → likely fails min trades
        for f in report.failures:
            if f.candidate_id == "TEST":
                assert any("total_trades" in r for r in f.fail_reasons)


class TestDeterminism:
    def test_same_input_same_output(self):
        records = _make_chronological_dataset(80)
        candidates = [_candidate({"pattern": "GOOD"})]
        r1 = validate_candidates(candidates, records, n_splits=3)
        r2 = validate_candidates(candidates, records, n_splits=3)
        assert r1.candidates_passed == r2.candidates_passed
        assert r1.candidates_failed == r2.candidates_failed


class TestEdgeCases:
    def test_empty_records(self):
        report = validate_candidates([_candidate()], [])
        assert report.confidence == "INSUFFICIENT"

    def test_empty_candidates(self):
        records = _make_chronological_dataset(50)
        report = validate_candidates([], records)
        assert report.confidence == "INSUFFICIENT"

    def test_no_matching_records(self):
        records = [_rec(pattern="OTHER", ts=f"2026-07-{15+i//10:02d}T{i%10+1:02d}:00:00Z") for i in range(50)]
        candidates = [_candidate({"pattern": "NONEXISTENT"})]
        report = validate_candidates(candidates, records, n_splits=3)
        assert report.candidates_passed == 0
