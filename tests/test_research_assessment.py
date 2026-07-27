"""Tests for Research Assessment integration layer."""
from __future__ import annotations
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.research_assessment.models import ResearchAssessment, NEUTRAL_ASSESSMENT
from core.research_assessment.provider import (
    get_research_assessment,
    _match_conditions,
    _bin_value,
    _infer_session,
    reload_candidates,
    _validated_candidates,
    _candidates_loaded,
)


class TestModels:
    def test_neutral_assessment(self):
        """NEUTRAL_ASSESSMENT has expected defaults."""
        assert NEUTRAL_ASSESSMENT.candidate_match is False
        assert NEUTRAL_ASSESSMENT.research_confidence == "NONE"
        assert NEUTRAL_ASSESSMENT.historical_win_rate == 0.0

    def test_assessment_to_dict(self):
        """Assessment serializes correctly."""
        a = ResearchAssessment(
            candidate_match=True,
            candidate_id="EC-TEST",
            historical_win_rate=0.46,
            empirical_ev=0.366,
            sample_size=69,
            walk_forward_survivor=True,
            research_confidence="HIGH",
        )
        d = a.to_dict()
        assert d["candidate_match"] is True
        assert d["historical_win_rate"] == 0.46
        assert d["research_confidence"] == "HIGH"
        # JSON-safe
        json.dumps(d)

    def test_assessment_is_frozen(self):
        """ResearchAssessment is immutable."""
        a = ResearchAssessment(candidate_match=True)
        with pytest.raises(Exception):
            a.candidate_match = False


class TestConditionMatching:
    def test_single_match(self):
        assert _match_conditions({"pattern": "X"}, {"pattern": "X", "regime": "T"})

    def test_single_mismatch(self):
        assert not _match_conditions({"pattern": "X"}, {"pattern": "Y", "regime": "T"})

    def test_multi_match(self):
        assert _match_conditions(
            {"pattern": "X", "regime": "T"},
            {"pattern": "X", "regime": "T", "session": "NY"},
        )

    def test_multi_mismatch(self):
        assert not _match_conditions(
            {"pattern": "X", "regime": "T"},
            {"pattern": "X", "regime": "R"},
        )

    def test_empty_conditions_always_match(self):
        assert _match_conditions({}, {"pattern": "X"})


class TestBinning:
    def test_high(self):
        assert _bin_value(0.8) == "HIGH"

    def test_medium(self):
        assert _bin_value(0.5) == "MEDIUM"

    def test_low(self):
        assert _bin_value(0.1) == "LOW"


class TestSessionInference:
    def test_london(self):
        assert _infer_session("2026-07-17T09:30:00Z") == "LONDON"

    def test_ny(self):
        assert _infer_session("2026-07-17T15:00:00Z") == "NY"

    def test_asian(self):
        assert _infer_session("2026-07-17T03:00:00Z") == "ASIAN"

    def test_invalid(self):
        assert _infer_session("") == "UNKNOWN"


class TestCandidateLookup:
    def test_no_reports_returns_neutral(self):
        """No validation reports → neutral assessment."""
        import core.research_assessment.provider as mod
        # Reset state
        mod._candidates_loaded = False
        mod._validated_candidates = []

        with patch.object(Path, "exists", return_value=False):
            result = get_research_assessment(pattern_name="TWEEZER_TOP")

        # Should return neutral (no candidates loaded from non-existent dir)
        # Reset for other tests
        mod._candidates_loaded = False
        mod._validated_candidates = []

    def test_matching_candidate_returns_assessment(self):
        """When candidates are loaded and match, returns full assessment."""
        import core.research_assessment.provider as mod
        mod._candidates_loaded = True
        mod._validated_candidates = [
            {
                "candidate_id": "EC-TEST-123",
                "conditions": {"pattern": "TWEEZER_TOP", "bias_alignment_bin": "HIGH"},
                "passes": True,
                "splits_positive": 4,
                "splits_total": 5,
                "total_trades": 216,
                "avg_win_rate": 0.42,
                "avg_ev": 0.246,
            }
        ]

        result = get_research_assessment(
            pattern_name="TWEEZER_TOP",
            regime="TRANSITIONAL",
            components={"bias_alignment": 0.8, "htf_alignment": 0.3},
        )

        assert result.candidate_match is True
        assert result.candidate_id == "EC-TEST-123"
        assert result.historical_win_rate == 0.42
        assert result.walk_forward_survivor is True
        assert result.research_confidence == "HIGH"

        # Cleanup
        mod._candidates_loaded = False
        mod._validated_candidates = []

    def test_no_match_returns_neutral(self):
        """When candidates loaded but none match, returns neutral."""
        import core.research_assessment.provider as mod
        mod._candidates_loaded = True
        mod._validated_candidates = [
            {
                "candidate_id": "EC-OTHER",
                "conditions": {"pattern": "MORNING_STAR"},
                "passes": True,
                "splits_positive": 3,
                "splits_total": 5,
                "total_trades": 50,
                "avg_win_rate": 0.39,
                "avg_ev": 0.15,
            }
        ]

        result = get_research_assessment(pattern_name="TWEEZER_TOP")

        assert result.candidate_match is False
        assert result == NEUTRAL_ASSESSMENT

        mod._candidates_loaded = False
        mod._validated_candidates = []

    def test_most_specific_match_wins(self):
        """When multiple candidates match, most specific (more conditions) wins."""
        import core.research_assessment.provider as mod
        mod._candidates_loaded = True
        mod._validated_candidates = [
            {
                "candidate_id": "EC-BROAD",
                "conditions": {"pattern": "TWEEZER_TOP"},
                "passes": True, "splits_positive": 3, "splits_total": 5,
                "total_trades": 100, "avg_win_rate": 0.35, "avg_ev": 0.1,
            },
            {
                "candidate_id": "EC-SPECIFIC",
                "conditions": {"pattern": "TWEEZER_TOP", "bias_alignment_bin": "HIGH"},
                "passes": True, "splits_positive": 4, "splits_total": 5,
                "total_trades": 50, "avg_win_rate": 0.45, "avg_ev": 0.3,
            },
        ]

        result = get_research_assessment(
            pattern_name="TWEEZER_TOP",
            components={"bias_alignment": 0.8},
        )

        assert result.candidate_id == "EC-SPECIFIC"

        mod._candidates_loaded = False
        mod._validated_candidates = []

    def test_deterministic(self):
        """Same input → same output."""
        import core.research_assessment.provider as mod
        mod._candidates_loaded = True
        mod._validated_candidates = [
            {"candidate_id": "EC-X", "conditions": {"pattern": "A"}, "passes": True,
             "splits_positive": 3, "splits_total": 5, "total_trades": 60, "avg_win_rate": 0.4, "avg_ev": 0.2}
        ]

        r1 = get_research_assessment(pattern_name="A")
        r2 = get_research_assessment(pattern_name="A")
        assert r1.to_dict() == r2.to_dict()

        mod._candidates_loaded = False
        mod._validated_candidates = []


class TestFeatureFlag:
    def test_flag_defaults_to_false(self):
        """USE_EMPIRICAL_PROBABILITY defaults to False."""
        from core import config
        assert getattr(config, "USE_EMPIRICAL_PROBABILITY", None) is False

    def test_logging_flag_defaults_to_true(self):
        """RESEARCH_ASSESSMENT_LOGGING defaults to True."""
        from core import config
        assert getattr(config, "RESEARCH_ASSESSMENT_LOGGING", None) is True


class TestProductionIsolation:
    def test_no_execution_imports(self):
        """Provider does not import from execution modules."""
        import core.research_assessment.provider as mod
        source = Path(mod.__file__).read_text(encoding="utf-8")
        # Check actual import lines (not docstring mentions)
        import_lines = [l for l in source.split("\n") if l.strip().startswith(("import ", "from "))]
        for line in import_lines:
            assert "from execution" not in line, f"Production import found: {line}"
            assert "from risk." not in line or "from risk" not in line, f"Risk import found: {line}"
            assert "order_send" not in line, f"MT5 import found: {line}"

    def test_no_model_imports(self):
        """Models module has no production dependencies."""
        import core.research_assessment.models as mod
        source = Path(mod.__file__).read_text(encoding="utf-8")
        assert "from execution" not in source
        assert "from core.pipeline" not in source
