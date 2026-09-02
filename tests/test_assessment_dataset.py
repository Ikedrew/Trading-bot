"""
Tests for Phase 2B Assessment Dataset.

Covers:
    - Assessment schema compliance (version fields, no execution fields)
    - Builder extracts correct data from engine results
    - Builder returns None when engine didn't score
    - Persistence writes valid JSONL
    - S3 mirror is called correctly
    - Join keys are populated
    - Serialization produces valid JSON
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.assessment.assessment import Assessment, SCHEMA_VERSION, DATASET_VERSION
from core.assessment.builder import build_assessment
from core.assessment.persistence import persist_assessment


# ═══════════════════════════════════════════════════════════════════════════════
# TEST DATA
# ═══════════════════════════════════════════════════════════════════════════════

def _engine_result_execute() -> dict:
    """Full engine result for an EXECUTE decision."""
    return {
        "action": "EXECUTE",
        "pattern": "TWEEZER_TOP",
        "side": "SELL",
        "score": 0.62,
        "components": {
            "pattern_quality": 0.8,
            "bias_alignment": 0.7,
            "market_quality": 0.5,
            "trend_alignment": 0.6,
            "chop_clarity": 0.65,
            "volatility_quality": 0.55,
            "bias_stability": 0.7,
            "confirmation_pre": 0.6,
            "htf_alignment": 0.75,
            "h4_alignment": 0.8,
        },
        "score_neutral": 0.58,
        "score_strategy": 0.62,
        "delta": 0.04,
        "strategy": "CONTINUATION",
        "strategy_confidence": 0.72,
        "activation_regime": "TRENDING",
        "activation_regime_confidence": 0.85,
        "weights_used": "strategy_specific",
        "p_success": 0.48,
        "probability_source": "synthetic_v1",
        "probability_model_version": "1.0.0",
        "ev": 0.000142,
        "ev_positive": True,
        "ev_reward": 0.00065,
        "ev_risk": 0.00032,
        "rr_effective": 2.03,
        "market_state": "STRUCTURED",
        "market_state_confidence": 0.78,
        "uncertainty": MagicMock(uncertainty_score=0.25, confidence_modifier=-0.05),
        "confirmation_score": 0.72,
        "confirmation_strength": "STRONG",
        "reasoning": MagicMock(narrative="Strong continuation with H4 trend alignment"),
        "policy_reasoning": "All gates passed",
        "attribution": MagicMock(contributions=[
            MagicMock(to_dict=lambda: {"factor": "h4_alignment", "contribution": 0.12}),
            MagicMock(to_dict=lambda: {"factor": "bias_alignment", "contribution": 0.09}),
        ]),
        "entity_id": "GBPUSD_1784809820",
        "correlation_id": "COR-20260723-4578-GBPUSD-0DCA",
        "cycle_id": 4578,
    }


def _engine_result_no_trade() -> dict:
    """Engine result for a NO_TRADE (scored but rejected by policy)."""
    result = _engine_result_execute()
    result["action"] = "NO_TRADE"
    result["reason"] = "ev_policy_blocked: NEGATIVE_EXPECTED_VALUE"
    result["ev"] = -0.00015
    result["ev_positive"] = False
    result["correlation_id"] = ""
    return result


def _engine_result_no_pattern() -> dict:
    """Engine result when no pattern detected (no scoring)."""
    return {
        "action": "NO_TRADE",
        "reason": "no_viable_pattern",
        "score": 0.0,
        "components": {},
    }


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: SCHEMA COMPLIANCE
# ═══════════════════════════════════════════════════════════════════════════════

class TestSchemaCompliance:
    """Assessment schema meets persistence standards."""

    def test_schema_version_present(self):
        result = _engine_result_execute()
        a = build_assessment(engine_result=result, symbol="GBPUSD", cycle_id=4578, bar_time=1784809820)
        assert a.schema_version == "assessments_v1"

    def test_dataset_version_present(self):
        result = _engine_result_execute()
        a = build_assessment(engine_result=result, symbol="GBPUSD", cycle_id=4578, bar_time=1784809820)
        assert a.dataset_version == 1

    def test_constants_match(self):
        assert SCHEMA_VERSION == "assessments_v1"   # canonical (unified from singular)
        assert DATASET_VERSION == 1                  # clean V1 baseline (was "2026.1")

    def test_no_execution_fields(self):
        """Assessment NEVER contains SL, TP, volume, or order details."""
        result = _engine_result_execute()
        a = build_assessment(engine_result=result, symbol="GBPUSD", cycle_id=4578, bar_time=1784809820)
        d = a.to_dict()
        forbidden = ["sl", "tp", "volume", "lot_size", "order_type",
                     "stop_loss", "take_profit", "entry_price",
                     "should_trade", "block_reason"]
        for field in forbidden:
            assert field not in d, f"Forbidden field '{field}' found on Assessment"

    def test_hive_partition_fields_present(self):
        """Symbol and date extractable for Hive partitioning."""
        result = _engine_result_execute()
        a = build_assessment(engine_result=result, symbol="EURUSD", cycle_id=100, bar_time=1784809820)
        assert a.symbol == "EURUSD"
        assert a.assessed_at_utc != ""  # Date extractable from ISO timestamp


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: BUILDER
# ═══════════════════════════════════════════════════════════════════════════════

class TestBuilder:
    """Assessment builder extracts correct data from engine results."""

    def test_execute_result_produces_assessment(self):
        result = _engine_result_execute()
        a = build_assessment(engine_result=result, symbol="GBPUSD", cycle_id=4578, bar_time=1784809820)
        assert a is not None
        assert a.assessment_id == "GBPUSD_1784809820_TWEEZER_TOP_assessment"
        assert a.opportunity_id == "GBPUSD_1784809820_TWEEZER_TOP"

    def test_scoring_extracted(self):
        result = _engine_result_execute()
        a = build_assessment(engine_result=result, symbol="GBPUSD", cycle_id=4578, bar_time=1784809820)
        assert a.score_neutral == 0.58
        assert a.score_strategy == 0.62
        assert a.score_delta == 0.04
        assert len(a.components) == 10
        assert a.components["pattern_quality"] == 0.8

    def test_strategy_extracted(self):
        result = _engine_result_execute()
        a = build_assessment(engine_result=result, symbol="GBPUSD", cycle_id=4578, bar_time=1784809820)
        assert a.selected_strategy == "CONTINUATION"
        assert a.strategy_confidence == 0.72
        assert a.regime == "TRENDING"
        assert a.regime_confidence == 0.85
        assert a.weights_used == "strategy_specific"

    def test_probability_extracted(self):
        result = _engine_result_execute()
        a = build_assessment(engine_result=result, symbol="GBPUSD", cycle_id=4578, bar_time=1784809820)
        assert a.p_success == 0.48
        assert a.probability_source == "synthetic_v1"

    def test_ev_extracted(self):
        result = _engine_result_execute()
        a = build_assessment(engine_result=result, symbol="GBPUSD", cycle_id=4578, bar_time=1784809820)
        assert a.ev == 0.000142
        assert a.ev_positive is True
        assert a.rr_effective == 2.03

    def test_uncertainty_extracted(self):
        result = _engine_result_execute()
        a = build_assessment(engine_result=result, symbol="GBPUSD", cycle_id=4578, bar_time=1784809820)
        assert a.uncertainty_score == 0.25
        assert a.confidence_modifier == -0.05

    def test_confirmation_extracted(self):
        result = _engine_result_execute()
        a = build_assessment(engine_result=result, symbol="GBPUSD", cycle_id=4578, bar_time=1784809820)
        assert a.confirmation_score == 0.72
        assert a.confirmation_strength == "STRONG"

    def test_reasoning_extracted(self):
        result = _engine_result_execute()
        a = build_assessment(engine_result=result, symbol="GBPUSD", cycle_id=4578, bar_time=1784809820)
        assert "continuation" in a.reasoning_narrative.lower()

    def test_attribution_extracted(self):
        result = _engine_result_execute()
        a = build_assessment(engine_result=result, symbol="GBPUSD", cycle_id=4578, bar_time=1784809820)
        assert len(a.evidence_contributions) == 2
        assert a.evidence_contributions[0]["factor"] == "h4_alignment"

    def test_no_trade_result_produces_assessment(self):
        """NO_TRADE results also produce assessments (for research)."""
        result = _engine_result_no_trade()
        a = build_assessment(engine_result=result, symbol="NZDUSD", cycle_id=100, bar_time=1784800000)
        assert a is not None
        assert a.ev_positive is False

    def test_no_pattern_returns_none(self):
        """Engine result without scoring → no assessment (nothing to evaluate)."""
        result = _engine_result_no_pattern()
        a = build_assessment(engine_result=result, symbol="EURUSD", cycle_id=50, bar_time=1784800000)
        assert a is None

    def test_market_context_captured(self):
        result = _engine_result_execute()
        a = build_assessment(
            engine_result=result, symbol="GBPUSD", cycle_id=4578,
            bar_time=1784809820, bid=1.33400, ask=1.33420,
        )
        assert a.bid_at_assessment == 1.33400
        assert a.ask_at_assessment == 1.33420

    def test_runtime_session_id_captured(self):
        result = _engine_result_execute()
        a = build_assessment(
            engine_result=result, symbol="GBPUSD", cycle_id=4578,
            bar_time=1784809820, runtime_session_id="abc123def456",
        )
        assert a.runtime_session_id == "abc123def456"


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: JOIN KEYS
# ═══════════════════════════════════════════════════════════════════════════════

class TestJoinKeys:
    """Assessment contains correct join keys for lifecycle linkage."""

    def test_entity_id_present(self):
        result = _engine_result_execute()
        a = build_assessment(engine_result=result, symbol="GBPUSD", cycle_id=4578, bar_time=1784809820)
        assert a.entity_id == "GBPUSD_1784809820"

    def test_correlation_id_on_execute(self):
        result = _engine_result_execute()
        a = build_assessment(engine_result=result, symbol="GBPUSD", cycle_id=4578, bar_time=1784809820)
        assert a.correlation_id == "COR-20260723-4578-GBPUSD-0DCA"

    def test_correlation_id_empty_on_no_trade(self):
        result = _engine_result_no_trade()
        a = build_assessment(engine_result=result, symbol="NZDUSD", cycle_id=100, bar_time=1784800000)
        assert a.correlation_id == ""

    def test_opportunity_id_links_to_opportunity(self):
        """assessment.opportunity_id matches opportunity.opportunity_id format."""
        result = _engine_result_execute()
        a = build_assessment(engine_result=result, symbol="GBPUSD", cycle_id=4578, bar_time=1784809820)
        assert a.opportunity_id == "GBPUSD_1784809820_TWEEZER_TOP"

    def test_cycle_id_present(self):
        result = _engine_result_execute()
        a = build_assessment(engine_result=result, symbol="GBPUSD", cycle_id=4578, bar_time=1784809820)
        assert a.cycle_id == 4578


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: SERIALIZATION
# ═══════════════════════════════════════════════════════════════════════════════

class TestSerialization:
    """Assessment serializes to valid JSON."""

    def test_to_dict_produces_flat_dict(self):
        result = _engine_result_execute()
        a = build_assessment(engine_result=result, symbol="GBPUSD", cycle_id=4578, bar_time=1784809820)
        d = a.to_dict()
        assert isinstance(d, dict)
        assert d["schema_version"] == "assessments_v1"
        assert d["symbol"] == "GBPUSD"

    def test_json_serializable(self):
        result = _engine_result_execute()
        a = build_assessment(engine_result=result, symbol="GBPUSD", cycle_id=4578, bar_time=1784809820)
        serialized = json.dumps(a.to_dict(), default=str)
        parsed = json.loads(serialized)
        assert parsed["assessment_id"] == "GBPUSD_1784809820_TWEEZER_TOP_assessment"
        assert parsed["ev"] == 0.000142


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: PERSISTENCE
# ═══════════════════════════════════════════════════════════════════════════════

class TestPersistence:
    """Assessment persisted to JSONL correctly."""

    def test_local_jsonl_written(self, tmp_path):
        result = _engine_result_execute()
        a = build_assessment(engine_result=result, symbol="NZDUSD", cycle_id=100, bar_time=1784809820)

        with patch("core.assessment.persistence._LOCAL_DIR", str(tmp_path / "assessments")):
            with patch("core.assessment.persistence._write_s3"):
                persist_assessment(a)

        files = list((tmp_path / "assessments" / "NZDUSD").glob("*.jsonl"))
        assert len(files) == 1

        with open(files[0]) as f:
            record = json.loads(f.read().strip())

        assert record["schema_version"] == "assessments_v1"
        assert record["symbol"] == "NZDUSD"
        assert record["score_strategy"] == 0.62
        assert record["ev"] == 0.000142

    @patch("core.assessment.persistence._write_s3")
    def test_s3_mirror_called(self, mock_s3):
        result = _engine_result_execute()
        a = build_assessment(engine_result=result, symbol="GBPUSD", cycle_id=4578, bar_time=1784809820)

        with patch("core.assessment.persistence._LOCAL_DIR", "/tmp/test_assess"):
            with patch("core.assessment.persistence.os.open", return_value=99):
                with patch("core.assessment.persistence.os.write"):
                    with patch("core.assessment.persistence.os.fsync"):
                        with patch("core.assessment.persistence.os.close"):
                            with patch("core.assessment.persistence.Path.mkdir"):
                                persist_assessment(a)

        mock_s3.assert_called_once()
        call_args = mock_s3.call_args
        assert call_args[0][0] == "GBPUSD"  # symbol
        assert "assessments_v1" in call_args[0][2]  # line contains schema_version
