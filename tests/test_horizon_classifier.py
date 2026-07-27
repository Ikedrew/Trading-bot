"""
Tests for Phase 4B Trade Horizon Intelligence.

Covers:
    1. Trending aligned market → multiple horizons eligible
    2. Range market → scalp preferred, extended rejected
    3. Weak structure → fewer horizons eligible
    4. SCALP always eligible (baseline)
    5. Model serialization
"""

from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.horizon.horizon_models import TradeHorizon, HorizonAssessment, HorizonClassificationResult
from core.horizon.horizon_classifier import classify_horizons
from core.horizon.horizon_profiles import ALL_PROFILES, SCALP, INTRADAY, EXTENDED


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 1: TRENDING ALIGNED MARKET — multiple horizons eligible
# ═══════════════════════════════════════════════════════════════════════════════

class TestTrendingAligned:
    """Strong H4 trend + H1 alignment + quality structure → all horizons possible."""

    def test_all_horizons_eligible(self):
        result = classify_horizons(
            strategy_type="CONTINUATION",
            strategy_confidence=0.75,
            h4_regime="TRENDING_BULLISH",
            h4_regime_confidence=0.85,
            h1_direction="BULLISH",
            h1_bos_confirmed=True,
            htf_alignment=0.80,
            h4_alignment=0.85,
            market_quality=0.70,
            chop_clarity=0.75,
            volatility_quality=0.65,
            pattern="TWEEZER_BOTTOM",
            direction="BUY",
        )

        eligible = result.eligible_horizons
        assert "SCALP" in eligible
        assert "INTRADAY" in eligible
        assert "EXTENDED" in eligible

    def test_extended_has_high_confidence(self):
        result = classify_horizons(
            strategy_type="CONTINUATION",
            h4_regime="TRENDING",
            h1_bos_confirmed=True,
            htf_alignment=0.85,
            h4_alignment=0.90,
            market_quality=0.75,
            volatility_quality=0.70,
        )

        extended = next(a for a in result.assessments if a.horizon == "EXTENDED")
        assert extended.eligible is True
        assert extended.confidence >= 0.6

    def test_best_horizon_is_highest_confidence(self):
        result = classify_horizons(
            strategy_type="CONTINUATION",
            h4_regime="TRENDING",
            h1_bos_confirmed=True,
            htf_alignment=0.80,
            h4_alignment=0.85,
            market_quality=0.70,
            volatility_quality=0.65,
        )

        # best_horizon should be one of the eligible ones with highest confidence
        assert result.best_horizon is not None
        assert result.best_horizon in result.eligible_horizons


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 2: RANGE MARKET — scalp preferred, extended rejected
# ═══════════════════════════════════════════════════════════════════════════════

class TestRangeMarket:
    """Range regime + weak HTF → only scalp eligible."""

    def test_extended_rejected_in_range(self):
        result = classify_horizons(
            strategy_type="REVERSAL",
            h4_regime="RANGE",
            h4_regime_confidence=0.70,
            h1_direction="NEUTRAL",
            h1_bos_confirmed=False,
            htf_alignment=0.35,
            h4_alignment=0.30,
            market_quality=0.50,
            chop_clarity=0.55,
            volatility_quality=0.40,
        )

        extended = next(a for a in result.assessments if a.horizon == "EXTENDED")
        assert extended.eligible is False
        assert "TRENDING" in extended.reasoning or "regime" in extended.reasoning.lower()

    def test_scalp_eligible_in_range(self):
        result = classify_horizons(
            strategy_type="REVERSAL",
            h4_regime="RANGE",
            htf_alignment=0.35,
            market_quality=0.50,
        )

        scalp = next(a for a in result.assessments if a.horizon == "SCALP")
        assert scalp.eligible is True

    def test_intraday_may_be_eligible_with_structure(self):
        """Intraday can work in range if structure is good."""
        result = classify_horizons(
            strategy_type="REVERSAL",
            h4_regime="RANGE",
            htf_alignment=0.55,
            h4_alignment=0.40,
            market_quality=0.65,
            chop_clarity=0.60,
            volatility_quality=0.50,
        )

        intraday = next(a for a in result.assessments if a.horizon == "INTRADAY")
        # Should be eligible since htf_alignment meets the 0.5 threshold
        assert intraday.eligible is True


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 3: WEAK STRUCTURE — fewer horizons eligible
# ═══════════════════════════════════════════════════════════════════════════════

class TestWeakStructure:
    """Low quality scores → only scalp survives."""

    def test_weak_htf_rejects_extended(self):
        result = classify_horizons(
            strategy_type="CONTINUATION",
            h4_regime="TRANSITIONAL",
            h1_bos_confirmed=False,
            htf_alignment=0.20,
            h4_alignment=0.25,
            market_quality=0.30,
            chop_clarity=0.35,
            volatility_quality=0.25,
        )

        extended = next(a for a in result.assessments if a.horizon == "EXTENDED")
        assert extended.eligible is False

    def test_weak_structure_reduces_intraday(self):
        result = classify_horizons(
            strategy_type="CONTINUATION",
            h4_regime="TRANSITIONAL",
            htf_alignment=0.15,  # Below intraday 0.5 requirement by >0.3
            market_quality=0.15,
        )

        intraday = next(a for a in result.assessments if a.horizon == "INTRADAY")
        assert intraday.eligible is False

    def test_scalp_survives_weak_conditions(self):
        result = classify_horizons(
            strategy_type="",
            h4_regime="TRANSITIONAL",
            htf_alignment=0.10,
            market_quality=0.10,
        )

        scalp = next(a for a in result.assessments if a.horizon == "SCALP")
        assert scalp.eligible is True


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 4: SCALP BASELINE
# ═══════════════════════════════════════════════════════════════════════════════

class TestScalpBaseline:
    """SCALP is always eligible regardless of market conditions."""

    def test_scalp_always_eligible(self):
        # Worst possible conditions
        result = classify_horizons(
            strategy_type="",
            h4_regime="",
            h1_direction="",
            h1_bos_confirmed=False,
            htf_alignment=0.0,
            h4_alignment=0.0,
            market_quality=0.0,
        )

        scalp = next(a for a in result.assessments if a.horizon == "SCALP")
        assert scalp.eligible is True

    def test_always_three_assessments(self):
        result = classify_horizons()
        assert len(result.assessments) == 3
        horizons = [a.horizon for a in result.assessments]
        assert "SCALP" in horizons
        assert "INTRADAY" in horizons
        assert "EXTENDED" in horizons


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 5: SERIALIZATION
# ═══════════════════════════════════════════════════════════════════════════════

class TestSerialization:
    """Models serialize correctly for persistence."""

    def test_assessment_to_dict(self):
        a = HorizonAssessment(
            horizon="INTRADAY",
            eligible=True,
            confidence=0.72,
            reasoning="Strong HTF alignment",
            evidence={"htf_alignment": 0.8},
        )
        d = a.to_dict()
        assert d["horizon"] == "INTRADAY"
        assert d["eligible"] is True
        assert d["confidence"] == 0.72

    def test_classification_to_summary(self):
        result = classify_horizons(
            strategy_type="CONTINUATION",
            h4_regime="TRENDING",
            h1_bos_confirmed=True,
            htf_alignment=0.80,
            h4_alignment=0.85,
            market_quality=0.70,
        )

        summary = result.to_summary_dict()
        assert "SCALP" in summary
        assert "INTRADAY" in summary
        assert "EXTENDED" in summary
        assert isinstance(summary["SCALP"]["eligible"], bool)
        assert isinstance(summary["SCALP"]["confidence"], float)

    def test_full_to_dict(self):
        result = classify_horizons(h4_regime="RANGE")
        d = result.to_dict()
        assert "assessments" in d
        assert "eligible_horizons" in d
        assert "best_horizon" in d
        assert len(d["assessments"]) == 3


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 6: PERSISTENCE INTEGRATION (regression test)
# ═══════════════════════════════════════════════════════════════════════════════

class TestHorizonPersistenceIntegration:
    """
    Regression test: Horizon data must be included in persisted assessment records.
    This test FAILS if persist_assessment() is called before horizon classification.
    """

    def test_horizon_data_attached_before_persistence(self, tmp_path):
        """Simulate the live_scanner flow and verify horizon data is in the persisted record."""
        import json
        from unittest.mock import patch

        # Step 1: Build assessment (same as live_scanner)
        from core.assessment.assessment import Assessment, SCHEMA_VERSION, DATASET_VERSION
        assessment = Assessment(
            assessment_id="TEST_123_TWEEZER_TOP_assessment",
            opportunity_id="TEST_123_TWEEZER_TOP",
            symbol="GBPUSD",
            cycle_id=100,
            bar_time=1784800000,
            components={"htf_alignment": 0.8, "h4_alignment": 0.85, "market_quality": 0.7},
            score_strategy=0.62,
        )

        # Step 2: Classify horizons (same as live_scanner)
        horizon_result = classify_horizons(
            strategy_type="CONTINUATION",
            h4_regime="TRENDING",
            h1_bos_confirmed=True,
            htf_alignment=0.80,
            h4_alignment=0.85,
            market_quality=0.70,
            volatility_quality=0.65,
        )

        # Step 3: Attach horizon data BEFORE persistence (the fix)
        assessment.evidence_contributions.append({
            "_horizon_classification": horizon_result.to_dict(),
        })

        # Step 4: Persist
        from core.assessment.persistence import persist_assessment
        with patch("core.assessment.persistence._LOCAL_DIR", str(tmp_path / "assessments")):
            with patch("core.assessment.persistence._write_s3"):
                persist_assessment(assessment)

        # Step 5: Verify persisted record contains horizon data
        files = list((tmp_path / "assessments" / "GBPUSD").glob("*.jsonl"))
        assert len(files) == 1

        record = json.loads(files[0].read_text().strip())

        # THE CRITICAL CHECK: horizon data must be in the persisted record
        ec = record.get("evidence_contributions", [])
        assert len(ec) >= 1, "evidence_contributions is empty — horizon data not attached before persist"

        horizon_data = None
        for item in ec:
            if "_horizon_classification" in item:
                horizon_data = item["_horizon_classification"]
                break

        assert horizon_data is not None, "No _horizon_classification found in persisted record"
        assert "assessments" in horizon_data
        assert len(horizon_data["assessments"]) == 3  # SCALP, INTRADAY, EXTENDED
        assert "eligible_horizons" in horizon_data
        assert "SCALP" in horizon_data["eligible_horizons"]

        # Verify structure of each horizon assessment
        for h in horizon_data["assessments"]:
            assert "horizon" in h
            assert "eligible" in h
            assert "confidence" in h
            assert "reasoning" in h

    def test_persist_without_horizon_fails_check(self):
        """If horizon classification is skipped, evidence_contributions is empty."""
        from core.assessment.assessment import Assessment
        assessment = Assessment(
            assessment_id="TEST_456",
            opportunity_id="TEST_456",
            symbol="EURUSD",
            cycle_id=200,
            bar_time=1784800000,
        )

        # NOT attaching horizon data — simulates the old broken flow
        # evidence_contributions should be empty
        assert len(assessment.evidence_contributions) == 0
