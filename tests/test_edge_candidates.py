"""Tests for Edge Candidate Generator."""
from __future__ import annotations
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research_engine.edge_attribution.models import EdgeAttributionRecord
from research_engine.edge_candidates.models import EdgeCandidate
from research_engine.edge_candidates.scoring import score_candidate
from research_engine.edge_candidates.generator import generate_candidates, _make_candidate_id


def _rec(pattern="P1", regime="TRANSITIONAL", session="LONDON", r=1.0):
    return EdgeAttributionRecord(
        entity_id=f"T_{id(pattern)}", pattern=pattern, regime=regime,
        session=session, direction="BUY", result_r=r, win=r > 0,
        htf_alignment_bin="MEDIUM", trend_alignment_bin="MEDIUM",
        bias_alignment_bin="MEDIUM", score_bin="MEDIUM", confirmation_bin="STRONG",
        symbol="EURUSD",
    )


class TestCandidateGeneration:
    def test_generates_from_positive_conditions(self):
        """Positive EV conditions generate candidates."""
        records = [_rec(pattern="GOOD", r=1.5) for _ in range(40)]
        records += [_rec(pattern="BAD", r=-1.0) for _ in range(40)]
        result = generate_candidates(records)
        assert result.candidates_accepted > 0
        good_ids = [c.candidate_id for c in result.accepted]
        assert any("GOOD" in cid for cid in good_ids)

    def test_rejects_below_min_sample(self):
        """Groups below 30 samples are rejected."""
        records = [_rec(pattern="RARE", r=2.0) for _ in range(15)]
        records += [_rec(pattern="COMMON", r=-0.5) for _ in range(50)]
        result = generate_candidates(records)
        accepted_patterns = [c.conditions.get("pattern") for c in result.accepted]
        assert "RARE" not in accepted_patterns

    def test_rejects_negative_ev(self):
        """Negative EV conditions are not accepted."""
        records = [_rec(pattern="LOSER", r=-1.0) for _ in range(50)]
        result = generate_candidates(records)
        assert all(c.expectancy > 0 for c in result.accepted)

    def test_no_duplicates(self):
        """Same conditions don't produce duplicate candidates."""
        records = [_rec(pattern="P1", session="LONDON", r=0.5) for _ in range(50)]
        result = generate_candidates(records)
        ids = [c.candidate_id for c in result.accepted]
        assert len(ids) == len(set(ids))

    def test_empty_data(self):
        """Empty input returns no candidates."""
        result = generate_candidates([])
        assert result.candidates_accepted == 0
        assert result.confidence == "INSUFFICIENT"

    def test_deterministic(self):
        """Same input → same output."""
        records = [_rec(pattern="A", r=1.0) for _ in range(50)]
        r1 = generate_candidates(records)
        r2 = generate_candidates(records)
        assert r1.candidates_accepted == r2.candidates_accepted
        if r1.accepted:
            assert r1.accepted[0].candidate_id == r2.accepted[0].candidate_id


class TestScoring:
    def test_high_sample_scores_higher(self):
        """Larger sample → higher confidence score."""
        c_small = EdgeCandidate(sample_size=25, expectancy=0.3, win_rate=0.45, profit_factor=1.5, total_r=7.5)
        c_large = EdgeCandidate(sample_size=150, expectancy=0.3, win_rate=0.45, profit_factor=1.5, total_r=45.0)
        score_candidate(c_small)
        score_candidate(c_large)
        assert c_large.confidence_score > c_small.confidence_score

    def test_dependency_reduces_score(self):
        """Single-pattern dependency reduces score."""
        c = EdgeCandidate(sample_size=50, expectancy=0.1, win_rate=0.35, profit_factor=1.2, total_r=5.0)
        score_candidate(c)
        base = c.confidence_score

        c2 = EdgeCandidate(sample_size=50, expectancy=0.1, win_rate=0.35, profit_factor=1.2, total_r=5.0, single_pattern_dependent=True)
        score_candidate(c2)
        assert c2.confidence_score < base

    def test_overfit_risk_assignment(self):
        """Small sample + single dependency = HIGH overfit risk."""
        c = EdgeCandidate(sample_size=15, expectancy=0.5, single_pattern_dependent=True, single_regime_dependent=True)
        score_candidate(c)
        assert c.overfit_risk == "HIGH"


class TestModels:
    def test_candidate_id_deterministic(self):
        assert _make_candidate_id({"a": "1", "b": "2"}) == _make_candidate_id({"b": "2", "a": "1"})

    def test_validation_spec(self):
        c = EdgeCandidate(candidate_id="TEST", conditions={"pattern": "X"}, sample_size=60)
        spec = c.to_validation_spec()
        assert spec["candidate_id"] == "TEST"
        assert spec["conditions"] == {"pattern": "X"}
        assert spec["validation_required"] is True
        assert spec["training_requirements"]["min_samples"] == 20

    def test_no_production_imports(self):
        import research_engine.edge_candidates.generator as m
        source = Path(m.__file__).read_text(encoding="utf-8")
        assert "from core.pipeline" not in source
        assert "from execution" not in source
        assert "from risk." not in source
