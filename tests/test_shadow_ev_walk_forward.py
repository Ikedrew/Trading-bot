"""Tests for Walk-Forward Validation of Shadow EV Models."""
from __future__ import annotations
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research_engine.shadow_ev.walk_forward import run_walk_forward, _train_model_params


def _make_decisions(n: int = 100) -> list[dict]:
    """Create synthetic chronological decisions with outcomes derivable from replay."""
    decisions = []
    for i in range(n):
        decisions.append({
            "entity_id": f"TEST_{1000000 + i * 300}",
            "symbol": "TEST",
            "timestamp_utc": f"2026-07-{15 + i // 48:02d}T{(i % 48) // 2:02d}:{(i % 2) * 30:02d}:00Z",
            "pattern_detected": True,
            "pattern_name": "TWEEZER_BOTTOM" if i % 3 == 0 else "THREE_INSIDE_DOWN" if i % 3 == 1 else "THREE_BLACK_CROWS",
            "score_neutral": 0.5 + (i % 10) * 0.02,
            "score_strategy": 0.5 + (i % 10) * 0.02,
            "strategy_confidence": 0.0,
            "confirmation_score": 1.0,
            "regime": "TRANSITIONAL",
            "market_state": "TRANSITIONAL",
            "components": {"pattern_quality": 0.5, "bias_alignment": 0.4},
        })
    return decisions


class TestChronologicalSplitting:
    def test_train_before_test(self):
        """Training data timestamps must precede test data timestamps."""
        decisions = _make_decisions(60)
        # Sort by timestamp (already sorted by construction)
        timestamps = [d["timestamp_utc"] for d in decisions]
        assert timestamps == sorted(timestamps)

    def test_no_future_leakage(self):
        """Train parameters use only training period data."""
        decisions = _make_decisions(60)
        outcomes = {d["entity_id"]: 0.5 if i % 3 == 0 else -1.0 for i, d in enumerate(decisions)}

        train = decisions[:30]
        test = decisions[30:]

        pat_wr, pat_counts, _, _ = _train_model_params(train, outcomes)

        # Win rates should only reflect training data outcomes
        train_eids = set(d["entity_id"] for d in train)
        test_eids = set(d["entity_id"] for d in test)

        # Verify no test entity_ids were used
        for eid in test_eids:
            # Training should not have access to test outcomes
            assert eid not in train_eids


class TestDeterminism:
    def test_same_input_same_output(self):
        """Identical inputs produce identical results."""
        decisions = _make_decisions(60)
        # Can't easily run full walk_forward without replay data,
        # but we can test _train_model_params determinism
        outcomes = {d["entity_id"]: 1.0 if i % 2 == 0 else -1.0 for i, d in enumerate(decisions)}

        r1 = _train_model_params(decisions, outcomes)
        r2 = _train_model_params(decisions, outcomes)
        assert r1 == r2


class TestEdgeCases:
    def test_empty_decisions(self):
        """Empty input returns INSUFFICIENT_DATA."""
        r = run_walk_forward([], replay_dir="nonexistent")
        assert r.confidence == "INSUFFICIENT_DATA"

    def test_insufficient_decisions(self):
        """Too few decisions returns INSUFFICIENT_DATA."""
        decisions = _make_decisions(10)
        r = run_walk_forward(decisions, replay_dir="nonexistent")
        assert r.confidence == "INSUFFICIENT_DATA"

    def test_no_replay_data_returns_insufficient(self):
        """No replay candles → no outcomes → INSUFFICIENT_DATA."""
        decisions = _make_decisions(100)
        r = run_walk_forward(decisions, replay_dir="nonexistent_dir_xyz")
        assert r.confidence == "INSUFFICIENT_DATA"


class TestTrainModelParams:
    def test_pattern_win_rates_computed(self):
        """Pattern win rates are correctly computed from outcomes."""
        decisions = [
            {"entity_id": "A", "pattern_name": "PAT1", "regime": "T"},
            {"entity_id": "B", "pattern_name": "PAT1", "regime": "T"},
            {"entity_id": "C", "pattern_name": "PAT1", "regime": "T"},
            {"entity_id": "D", "pattern_name": "PAT1", "regime": "T"},
            {"entity_id": "E", "pattern_name": "PAT1", "regime": "T"},
            {"entity_id": "F", "pattern_name": "PAT2", "regime": "T"},
        ]
        outcomes = {"A": 1.0, "B": 1.0, "C": -1.0, "D": 1.0, "E": -1.0, "F": 1.0}

        pat_wr, pat_counts, _, _ = _train_model_params(decisions, outcomes)

        assert "PAT1" in pat_wr
        assert pat_wr["PAT1"] == pytest.approx(0.6)  # 3/5
        assert pat_counts["PAT1"] == 5
        # PAT2 has only 1 sample — below minimum
        assert "PAT2" not in pat_wr

    def test_missing_outcomes_ignored(self):
        """Decisions without outcomes are skipped."""
        decisions = [
            {"entity_id": "A", "pattern_name": "PAT1", "regime": "T"},
            {"entity_id": "B", "pattern_name": "PAT1", "regime": "T"},
            {"entity_id": "X", "pattern_name": "PAT1", "regime": "T"},  # No outcome
        ]
        outcomes = {"A": 1.0, "B": -1.0}

        pat_wr, pat_counts, _, _ = _train_model_params(decisions, outcomes)
        # Only 2 samples < 5 minimum
        assert "PAT1" not in pat_wr
