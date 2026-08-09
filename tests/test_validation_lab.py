"""Tests for V10 Validation Lab."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from research_engine.v10.validation_lab import ValidationRun, ValidationDecision, ValidationRunner
from research_engine.v10.validation_lab.models import ValidationStatus
from research_engine.v10.validation_lab.replay_engine import ReplayEngine
from research_engine.v10.validation_lab.comparison_engine import compare_metrics
from research_engine.v10.validation_lab.regression_checker import check_regressions


# ═══════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════

def _make_event(trade_id="pos_1", r=-1.0, pnl=-0.5, regime="TRENDING", session="LONDON", score=0.55):
    return {
        "trade_id": trade_id,
        "execution": {
            "ticket": 1, "symbol": "EURUSD", "direction": "BUY",
            "entry_price": 1.1, "exit_price": 1.099, "entry_time": 1784808000.0,
            "exit_time": 1784809000.0, "stop_loss": 1.098, "take_profit": 1.103,
            "gross_profit": pnl, "commission": -0.04, "swap": 0.0,
            "net_realised_pnl": pnl - 0.04, "r_multiple": r,
            "volume": 0.01, "duration_seconds": 1000, "exit_reason": "STOP_LOSS",
        },
        "decision": {"strategy": "REV", "score": score, "confidence": 0.7,
                     "decision_type": "sym_cycle", "components": {}, "weakest_component": "",
                     "ev": None, "p_success": None},
        "market": {"regime": regime, "session": session, "volatility": "NEUTRAL",
                   "trend_state": "BULLISH", "higher_timeframe_bias": "BULLISH",
                   "h4_phase": "IMPULSE", "h1_clarity": 0.6},
        "strategy": {"family": "REV", "pattern": "HAMMER", "conditions_met": 2,
                     "strategy_confidence": 0.7, "opportunity_quality": 0.55,
                     "opportunity_type": "ZONE_REACTION"},
        "quality": {"anomaly": False, "anomaly_reasons": [], "governance_status": "WARNING",
                    "data_completeness": "COMPLETE", "missing": [], "join_method": "sym_cycle",
                    "pnl_source": "MT5_BROKER"},
    }


@pytest.fixture
def universe_file(tmp_path):
    """Create synthetic universe."""
    events = []
    for i in range(30):
        r = 1.5 if i % 3 == 0 else -1.0
        pnl = 0.75 if r > 0 else -0.5
        regime = "TRENDING" if i < 20 else "RANGING"
        score = 0.4 + (i * 0.01)
        events.append(_make_event(f"pos_{i}", r=r, pnl=pnl, regime=regime, score=score))
    f = tmp_path / "universe.jsonl"
    f.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
    return str(f)


# ═══════════════════════════════════════════════════════════════
# MODELS (1-3)
# ═══════════════════════════════════════════════════════════════

class TestModels:
    def test_validation_run_creation(self):
        run = ValidationRun(validation_id="VAL_001", candidate_id="C1", baseline_id="B1")
        assert run.validation_id == "VAL_001"
        assert run.created_at != ""
        assert run.status == "CREATED"

    def test_required_fields(self):
        run = ValidationRun(validation_id="V1")
        d = run.to_dict()
        assert "validation_id" in d
        assert "baseline_metrics" in d
        assert "candidate_metrics" in d
        assert "decision" in d

    def test_status_lifecycle(self):
        run = ValidationRun(validation_id="V2")
        assert run.status == ValidationStatus.CREATED
        run.status = ValidationStatus.RUNNING
        assert run.status == "RUNNING"
        run.status = ValidationStatus.COMPLETED
        assert run.status == "COMPLETED"


# ═══════════════════════════════════════════════════════════════
# REPLAY ENGINE (4-6)
# ═══════════════════════════════════════════════════════════════

class TestReplayEngine:
    def test_loads_universe(self, universe_file):
        replay = ReplayEngine(universe_file=universe_file)
        assert len(replay.events) == 30

    def test_candidate_changes_apply(self, universe_file):
        replay = ReplayEngine(universe_file=universe_file)
        baseline = replay.baseline_metrics()
        # Score threshold filter should reduce trade count
        candidate = replay.candidate_metrics(changes={"score_threshold": 0.55})
        assert candidate["count"] < baseline["count"]

    def test_baseline_unchanged(self, universe_file):
        replay = ReplayEngine(universe_file=universe_file)
        baseline1 = replay.baseline_metrics()
        # Run candidate
        replay.candidate_metrics(changes={"stop_multiplier": 2.0})
        # Baseline should be identical after candidate run
        baseline2 = replay.baseline_metrics()
        assert baseline1["expectancy_r"] == baseline2["expectancy_r"]
        assert baseline1["count"] == baseline2["count"]


# ═══════════════════════════════════════════════════════════════
# CANDIDATE RUNNER (7-8)
# ═══════════════════════════════════════════════════════════════

class TestCandidateExecution:
    def test_candidate_executes(self, universe_file, tmp_path):
        runner = ValidationRunner(universe_file=universe_file, reports_dir=str(tmp_path))
        result = runner.validate(
            candidate_id="TEST_C1",
            changes={"stop_multiplier": 1.5},
            baseline_id="BASE_001",
        )
        assert result.status == ValidationStatus.COMPLETED
        assert result.decision != ""

    def test_results_generated(self, universe_file, tmp_path):
        runner = ValidationRunner(universe_file=universe_file, reports_dir=str(tmp_path))
        result = runner.validate(
            candidate_id="TEST_C2",
            changes={"score_threshold": 0.50},
            baseline_id="BASE_001",
        )
        assert result.baseline_metrics.get("count", 0) > 0
        assert result.candidate_metrics.get("count", 0) > 0


# ═══════════════════════════════════════════════════════════════
# COMPARISON (9-10)
# ═══════════════════════════════════════════════════════════════

class TestComparison:
    def test_metrics_compare(self):
        baseline = {"expectancy_r": -0.14, "profit_factor": 1.2, "win_rate": 0.36, "count": 94}
        candidate = {"expectancy_r": 0.05, "profit_factor": 1.5, "win_rate": 0.42, "count": 80}
        result = compare_metrics(baseline, candidate)
        assert result["changes"]["expectancy_r"]["delta"] == pytest.approx(0.19, abs=0.001)
        assert "expectancy_r" in result["improved_metrics"]

    def test_delta_calculated(self):
        baseline = {"expectancy_r": 0.10, "profit_factor": 1.5, "win_rate": 0.5, "count": 50}
        candidate = {"expectancy_r": 0.08, "profit_factor": 1.3, "win_rate": 0.48, "count": 50}
        result = compare_metrics(baseline, candidate)
        assert result["changes"]["expectancy_r"]["delta"] < 0
        assert "expectancy_r" in result["degraded_metrics"]


# ═══════════════════════════════════════════════════════════════
# REGRESSION (11-13)
# ═══════════════════════════════════════════════════════════════

class TestRegression:
    def test_regression_detected(self):
        baseline = {"expectancy_r": 0.10, "profit_factor": 1.5, "win_rate": 0.50, "count": 50}
        candidate = {"expectancy_r": -0.10, "profit_factor": 0.8, "win_rate": 0.35, "count": 50}
        result = check_regressions(baseline, candidate)
        assert result["regressions_detected"] is True
        assert result["status"] in ("REGRESSION_DETECTED", "SEVERE_REGRESSION")

    def test_improvement_accepted(self):
        baseline = {"expectancy_r": -0.14, "profit_factor": 1.0, "win_rate": 0.36, "count": 50}
        candidate = {"expectancy_r": 0.05, "profit_factor": 1.4, "win_rate": 0.42, "count": 50}
        result = check_regressions(baseline, candidate)
        assert result["regressions_detected"] is False
        assert result["status"] == "PASS"

    def test_mixed_outcome(self):
        baseline = {"expectancy_r": 0.10, "profit_factor": 1.5, "win_rate": 0.50, "count": 50}
        candidate = {"expectancy_r": 0.15, "profit_factor": 1.6, "win_rate": 0.40, "count": 50}
        result = check_regressions(baseline, candidate)
        # Win rate dropped > 5pp
        assert result["regressions_detected"] is True
        assert any(r["metric"] == "win_rate" for r in result["regressions"])


# ═══════════════════════════════════════════════════════════════
# GOVERNANCE (14-15)
# ═══════════════════════════════════════════════════════════════

class TestGovernance:
    def test_confidence_included(self, universe_file, tmp_path):
        runner = ValidationRunner(universe_file=universe_file, reports_dir=str(tmp_path))
        result = runner.validate("G1", changes={}, baseline_id="B1")
        assert result.confidence in ("HIGH", "MEDIUM", "LOW")

    def test_sample_size_included(self, universe_file, tmp_path):
        runner = ValidationRunner(universe_file=universe_file, reports_dir=str(tmp_path))
        result = runner.validate("G2", changes={}, baseline_id="B1")
        assert result.sample_size > 0


# ═══════════════════════════════════════════════════════════════
# SEGMENTATION (16)
# ═══════════════════════════════════════════════════════════════

class TestSegmentation:
    def test_filtered_population(self, universe_file, tmp_path):
        runner = ValidationRunner(universe_file=universe_file, reports_dir=str(tmp_path))
        result = runner.validate(
            "SEG1", changes={}, baseline_id="B1",
            filters={"regime": "TRENDING"},
        )
        # Filtered population should be smaller
        assert result.sample_size <= 30
        assert result.sample_size > 0


# ═══════════════════════════════════════════════════════════════
# REPORTING (17-19)
# ═══════════════════════════════════════════════════════════════

class TestReporting:
    def test_report_generated(self, universe_file, tmp_path):
        runner = ValidationRunner(universe_file=universe_file, reports_dir=str(tmp_path))
        result = runner.validate("RPT1", changes={"stop_multiplier": 1.2}, baseline_id="B1")
        # Check report files exist
        files = list(tmp_path.glob("*.json"))
        assert len(files) >= 1

    def test_report_includes_baseline(self, universe_file, tmp_path):
        runner = ValidationRunner(universe_file=universe_file, reports_dir=str(tmp_path))
        result = runner.validate("RPT2", changes={}, baseline_id="MY_BASE")
        assert result.baseline_id == "MY_BASE"
        # Check JSON report
        files = list(tmp_path.glob("*.json"))
        data = json.loads(files[0].read_text(encoding="utf-8"))
        assert data["baseline_id"] == "MY_BASE"

    def test_report_includes_candidate(self, universe_file, tmp_path):
        runner = ValidationRunner(universe_file=universe_file, reports_dir=str(tmp_path))
        result = runner.validate("RPT3", changes={"score_threshold": 0.6}, baseline_id="B1")
        assert result.candidate_id == "RPT3"


# ═══════════════════════════════════════════════════════════════
# INTEGRATION (20)
# ═══════════════════════════════════════════════════════════════

class TestIntegration:
    def test_full_flow(self, universe_file, tmp_path):
        """Finding → hypothesis → candidate → validation."""
        from research_engine.v10.optimisation import (
            ResearchHypothesis, OptimisationCandidate, HypothesisEngine,
        )
        from research_engine.v10.optimisation.candidate_builder import build_candidate

        # Create candidate
        candidate = build_candidate(
            candidate_id="INT_TEST",
            hypothesis_id="HYP_R2_001",
            baseline_id="V10_BASELINE",
            component="StopPlacement",
            changes={"stop_multiplier": 1.3},
            expected_outcome="Wider stops improve survival",
        )

        # Validate
        runner = ValidationRunner(universe_file=universe_file, reports_dir=str(tmp_path))
        result = runner.validate(
            candidate_id=candidate.candidate_id,
            changes=candidate.changes,
            baseline_id=candidate.baseline_id,
        )
        assert result.status == ValidationStatus.COMPLETED
        assert result.decision in (
            ValidationDecision.IMPROVED,
            ValidationDecision.NO_IMPROVEMENT,
            ValidationDecision.REGRESSION,
            ValidationDecision.INCONCLUSIVE,
        )


# ═══════════════════════════════════════════════════════════════
# LIVE DATA (bonus)
# ═══════════════════════════════════════════════════════════════

class TestLiveData:
    def test_live_validation(self):
        universe = Path("data/research/research_universe.jsonl")
        if not universe.exists():
            pytest.skip("Research universe not available")
        runner = ValidationRunner()
        result = runner.validate(
            candidate_id="LIVE_TEST",
            changes={"stop_multiplier": 1.5},
            baseline_id="V10_BASELINE_LIVE",
        )
        assert result.status == ValidationStatus.COMPLETED
        assert result.sample_size > 0
