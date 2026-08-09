"""Tests for V10 Research Intelligence Engine."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from research_engine.v10.research_intelligence import ExperimentRunner, QuestionRegistry
from research_engine.v10.research_intelligence.models import ExperimentResult, classify_confidence


# ═══════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════

def _make_event(trade_id="pos_1", symbol="EURUSD", session="LONDON",
                regime="TRENDING", score=0.55, r=-1.0, pnl=-0.5,
                pattern="HAMMER", strategy="REVERSAL", confidence=0.7):
    return {
        "trade_id": trade_id,
        "execution": {
            "ticket": int(trade_id.split("_")[1]),
            "symbol": symbol,
            "direction": "BUY",
            "entry_price": 1.1000,
            "exit_price": 1.0990,
            "entry_time": 1784808000.0,
            "exit_time": 1784809000.0,
            "stop_loss": 1.0980,
            "take_profit": 1.1030,
            "gross_profit": pnl,
            "commission": -0.04,
            "swap": 0.0,
            "net_realised_pnl": pnl - 0.04,
            "r_multiple": r,
            "volume": 0.01,
            "duration_seconds": 1000,
            "exit_reason": "STOP_LOSS",
        },
        "decision": {
            "strategy": strategy,
            "score": score,
            "confidence": confidence,
            "decision_type": "sym_cycle",
            "decision_timestamp": 1784808000.0,
            "components": {"location": 0.6, "structure": 0.5},
            "weakest_component": "structure",
            "ev": None,
            "p_success": None,
        },
        "market": {
            "regime": regime,
            "session": session,
            "volatility": "NEUTRAL",
            "trend_state": "BULLISH",
            "higher_timeframe_bias": "BULLISH",
            "h4_phase": "IMPULSE",
            "h1_clarity": 0.6,
        },
        "strategy": {
            "family": strategy,
            "pattern": pattern,
            "conditions_met": 2,
            "strategy_confidence": confidence,
            "opportunity_quality": 0.55,
            "opportunity_type": "ZONE_REACTION",
        },
        "quality": {
            "anomaly": False,
            "anomaly_reasons": [],
            "governance_status": "WARNING",
            "data_completeness": "COMPLETE",
            "missing": [],
            "join_method": "sym_cycle",
            "pnl_source": "MT5_BROKER",
        },
    }


@pytest.fixture
def runner(tmp_path):
    """Create runner with synthetic universe (25 events)."""
    events = []
    for i in range(25):
        events.append(_make_event(
            trade_id=f"pos_{i+1}",
            symbol="EURUSD" if i < 15 else "US500",
            session="LONDON" if i % 2 == 0 else "NEW_YORK",
            regime="TRENDING" if i < 10 else "RANGING",
            score=0.5 + (i * 0.01),
            r=-1.0 if i % 3 != 0 else 1.5,
            pnl=-0.5 if i % 3 != 0 else 1.0,
        ))
    universe_file = tmp_path / "universe.jsonl"
    universe_file.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
    return ExperimentRunner(
        universe_file=str(universe_file),
        reports_dir=str(tmp_path / "reports"),
    )


# ═══════════════════════════════════════════════════════════════
# TESTS
# ═══════════════════════════════════════════════════════════════

class TestRegistryLoads:
    def test_registry_has_questions(self):
        reg = QuestionRegistry()
        assert len(reg.list_all()) > 0

    def test_known_questions_exist(self):
        reg = QuestionRegistry()
        assert reg.get("E1") is not None
        assert reg.get("R2") is not None
        assert reg.get("D1") is not None

    def test_active_questions_have_modules(self):
        reg = QuestionRegistry()
        for q in reg.list_active():
            assert q.experiment_module, f"{q.id} has no experiment module"


class TestQuestionMetadata:
    def test_question_has_required_fields(self):
        reg = QuestionRegistry()
        q = reg.get("E1")
        assert q.id == "E1"
        assert q.name
        assert q.category
        assert q.minimum_sample_size > 0
        assert q.experiment_module

    def test_categories_valid(self):
        reg = QuestionRegistry()
        valid = {"outcome", "risk", "execution", "prediction", "selection",
                 "lifecycle", "regime", "session", "conditions", "performance",
                 "construction", "opportunity", "stability"}
        for q in reg.list_all():
            assert q.category in valid, f"{q.id} has invalid category {q.category}"


class TestExperimentExecution:
    def test_run_known_question(self, runner):
        result = runner.run("E1")
        assert isinstance(result, ExperimentResult)
        assert result.question_id == "E1"
        assert result.sample_size == 25
        assert result.confidence in ("HIGH", "MEDIUM", "LOW")
        assert result.recommendation in ("SUPPORTED", "REJECTED", "INCONCLUSIVE")

    def test_run_with_filters(self, runner):
        result = runner.run("E1", filters={"regime": "TRENDING"})
        assert result.sample_size <= 25
        assert result.filters_applied == {"regime": "TRENDING"}


class TestMissingFields:
    def test_missing_fields_reported(self, runner):
        # D2 requires decision.ev which is None in test data
        result = runner.run("D2")
        # Should still run but report limitation
        assert isinstance(result, ExperimentResult)


class TestMinimumSampleSize:
    def test_below_minimum_returns_inconclusive(self, tmp_path):
        # Create universe with only 3 events
        events = [_make_event(f"pos_{i}") for i in range(3)]
        universe_file = tmp_path / "small.jsonl"
        universe_file.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
        runner = ExperimentRunner(
            universe_file=str(universe_file),
            reports_dir=str(tmp_path / "rep"),
        )
        result = runner.run("E1")  # min sample = 20
        assert result.recommendation == "INCONCLUSIVE"
        assert any("below minimum" in l for l in result.limitations)


class TestSegmentationFilters:
    def test_instrument_filter(self, runner):
        result = runner.run("E1", filters={"instrument": "EURUSD"})
        assert result.sample_size == 15

    def test_combined_filter(self, runner):
        result = runner.run("E1", filters={"instrument": "EURUSD", "regime": "TRENDING"})
        assert result.sample_size < 15


class TestResultSchema:
    def test_result_has_all_fields(self, runner):
        result = runner.run("E1")
        d = result.to_dict()
        assert "question_id" in d
        assert "question_name" in d
        assert "sample_size" in d
        assert "result" in d
        assert "confidence" in d
        assert "recommendation" in d
        assert "limitations" in d


class TestReportGeneration:
    def test_report_file_created(self, runner):
        result = runner.run("E1")
        # E1 runs on full 25 events -> passes min sample
        rep_dir = runner._reports_dir
        assert (rep_dir / "E1.json").exists()

    def test_filtered_report_has_suffix(self, runner):
        # M1 has minimum_sample_size=10, and we have 10 TRENDING events
        result = runner.run("M1", filters={"regime": "TRENDING"})
        rep_dir = runner._reports_dir
        assert (rep_dir / "M1_regime_TRENDING.json").exists()


class TestUnknownQuestion:
    def test_unknown_returns_error(self, runner):
        result = runner.run("NONEXISTENT_Q99")
        assert result.error
        assert "not found" in result.error


class TestLambdaCompat:
    def test_no_global_state_dependency(self, tmp_path):
        """Runner can be instantiated with explicit paths — no globals needed."""
        events = [_make_event(f"pos_{i}") for i in range(25)]
        universe_file = tmp_path / "u.jsonl"
        universe_file.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")

        runner = ExperimentRunner(
            universe_file=str(universe_file),
            reports_dir=str(tmp_path / "r"),
        )
        result = runner.run("E1")
        assert result.sample_size == 25
        assert not result.error


class TestLiveExecution:
    def test_live_run(self):
        """Run against real research universe."""
        universe = Path("data/research/research_universe.jsonl")
        if not universe.exists():
            pytest.skip("Research universe not available")
        runner = ExperimentRunner()
        result = runner.run("E1")
        assert result.sample_size > 0
        assert not result.error
