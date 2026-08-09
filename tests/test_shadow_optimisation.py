"""Tests for V10 Shadow Optimisation Engine."""
import inspect
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from research_engine.v10.shadow import ShadowRunner, ShadowRegistry, ShadowCandidate, ShadowComparison
from research_engine.v10.shadow.models import ShadowStatus
from research_engine.v10.shadow.shadow_decision import apply_candidate_decision
from research_engine.v10.shadow.shadow_outcome import calculate_shadow_outcome, calculate_baseline_outcome
from research_engine.v10.shadow.shadow_comparison import compute_shadow_metrics, evaluate_shadow_evidence
from research_engine.v10.shadow.shadow_report import generate_shadow_dashboard


# ═══════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════

def _make_trade(trade_id="pos_1", symbol="EURUSD", direction="BUY",
                entry=1.1000, stop=1.0980, target=1.1040, exit_price=1.0985,
                r=-0.75, pnl=-0.15, score=0.55, regime="TRENDING"):
    return {
        "trade_id": trade_id, "symbol": symbol, "direction": direction,
        "entry_price": entry, "stop_loss": stop, "take_profit": target,
        "exit_price": exit_price, "realised_r": r, "final_pnl": pnl,
        "score": score, "dt_score_strategy": score,
        "regime": regime, "dt_v10_regime": regime,
        "session": "LONDON",
    }


@pytest.fixture
def runner(tmp_path):
    return ShadowRunner(shadow_dir=str(tmp_path))


# ═══════════════════════════════════════════════════════════════
# CANDIDATE ISOLATION — multiple candidates simultaneously
# ═══════════════════════════════════════════════════════════════

class TestCandidateIsolation:
    def test_multiple_candidates(self, runner):
        runner.start_shadow("C1", {"stop_multiplier": 1.5})
        runner.start_shadow("C2", {"score_threshold": 0.6})
        runner.start_shadow("C3", {"regime_filter": "TRENDING"})
        active = runner.registry.list_active()
        assert len(active) == 3

    def test_candidates_independent(self, runner):
        runner.start_shadow("ISO_A", {"stop_multiplier": 2.0})
        runner.start_shadow("ISO_B", {"score_threshold": 0.7})
        trade = _make_trade(score=0.5)  # Below threshold for B
        comparisons = runner.process_trade(trade)
        # A gets a comparison, B filters it out (NO_TRADE)
        a_comp = [c for c in comparisons if c.candidate_id == "ISO_A"]
        b_comp = [c for c in comparisons if c.candidate_id == "ISO_B"]
        assert len(a_comp) == 1
        assert a_comp[0].shadow_decision == "EXECUTE"
        assert len(b_comp) == 1
        assert b_comp[0].shadow_decision == "NO_TRADE"


# ═══════════════════════════════════════════════════════════════
# NO EXECUTION — prove no broker imports
# ═══════════════════════════════════════════════════════════════

class TestNoExecution:
    def test_shadow_decision_no_broker_imports(self):
        """shadow_decision.py must not import execution or broker modules."""
        import research_engine.v10.shadow.shadow_decision as mod
        source = inspect.getsource(mod)
        # Check actual import lines only (not docstring mentions)
        import_lines = [l for l in source.splitlines() if l.strip().startswith(("import ", "from "))]
        banned = ["MetaTrader5", "mt5", "order_send", "execution"]
        for line in import_lines:
            for term in banned:
                assert term not in line, f"SAFETY VIOLATION: '{term}' imported in shadow_decision.py"

    def test_shadow_runner_no_broker_imports(self):
        """shadow_runner.py must not import execution or broker modules."""
        import research_engine.v10.shadow.shadow_runner as mod
        source = inspect.getsource(mod)
        import_lines = [l for l in source.splitlines() if l.strip().startswith(("import ", "from "))]
        banned = ["MetaTrader5", "mt5", "order_send", "from execution"]
        for line in import_lines:
            for term in banned:
                assert term not in line, f"SAFETY VIOLATION: '{term}' imported in shadow_runner.py"


# ═══════════════════════════════════════════════════════════════
# BASELINE IMMUTABILITY
# ═══════════════════════════════════════════════════════════════

class TestBaselineImmutability:
    def test_trade_not_modified(self, runner):
        runner.start_shadow("IMM_1", {"stop_multiplier": 2.0})
        trade = _make_trade()
        original_stop = trade["stop_loss"]
        original_r = trade["realised_r"]
        runner.process_trade(trade)
        # Original trade dict must not be modified
        assert trade["stop_loss"] == original_stop
        assert trade["realised_r"] == original_r


# ═══════════════════════════════════════════════════════════════
# CANDIDATE APPLICATION
# ═══════════════════════════════════════════════════════════════

class TestCandidateApplication:
    def test_stop_multiplier_widens_stop(self):
        trade = _make_trade(entry=1.1000, stop=1.0980, direction="BUY")
        result = apply_candidate_decision(trade, {"stop_multiplier": 2.0})
        # Original risk = 0.002, doubled = 0.004, new stop = 1.1 - 0.004 = 1.096
        assert result["stop_loss"] == pytest.approx(1.096, abs=0.0001)
        assert result["decision"] == "EXECUTE"

    def test_score_threshold_filters(self):
        trade = _make_trade(score=0.4)
        result = apply_candidate_decision(trade, {"score_threshold": 0.5})
        assert result["decision"] == "NO_TRADE"

    def test_regime_filter(self):
        trade = _make_trade(regime="RANGING")
        result = apply_candidate_decision(trade, {"regime_filter": "TRENDING"})
        assert result["decision"] == "NO_TRADE"


# ═══════════════════════════════════════════════════════════════
# BASELINE / SHADOW PAIRING
# ═══════════════════════════════════════════════════════════════

class TestPairing:
    def test_same_opportunity(self, runner):
        runner.start_shadow("PAIR_1", {"stop_multiplier": 1.5})
        trade = _make_trade(trade_id="pos_42")
        comparisons = runner.process_trade(trade)
        assert len(comparisons) == 1
        c = comparisons[0]
        assert c.trade_id == "pos_42"
        assert c.baseline_entry == trade["entry_price"]
        assert c.shadow_entry == trade["entry_price"]


# ═══════════════════════════════════════════════════════════════
# DIVERGENT DECISIONS
# ═══════════════════════════════════════════════════════════════

class TestDivergentDecisions:
    def test_baseline_execute_shadow_no_trade(self, runner):
        """Candidate filters out a trade the baseline took."""
        runner.start_shadow("DIV_1", {"score_threshold": 0.8})
        trade = _make_trade(score=0.5)
        comparisons = runner.process_trade(trade)
        c = comparisons[0]
        assert c.baseline_decision == "EXECUTE"
        assert c.shadow_decision == "NO_TRADE"
        assert c.shadow_r == 0.0  # No trade = no R


# ═══════════════════════════════════════════════════════════════
# OUTCOME CALCULATION
# ═══════════════════════════════════════════════════════════════

class TestOutcomeCalculation:
    def test_winning_trade(self):
        dec = {"decision": "EXECUTE", "entry_price": 1.1, "stop_loss": 1.098,
               "take_profit": 1.104, "direction": "BUY"}
        result = calculate_shadow_outcome(dec, actual_exit_price=1.103)
        # Move = 0.003, risk = 0.002, R = 1.5
        assert result["r_multiple"] == pytest.approx(1.5, abs=0.01)

    def test_losing_trade_hits_stop(self):
        dec = {"decision": "EXECUTE", "entry_price": 1.1, "stop_loss": 1.098,
               "take_profit": 1.104, "direction": "BUY"}
        result = calculate_shadow_outcome(dec, actual_exit_price=1.097)
        assert result["r_multiple"] == -1.0  # Clamped to -1R at stop

    def test_no_trade_returns_zero(self):
        dec = {"decision": "NO_TRADE", "entry_price": 1.1, "stop_loss": 1.098,
               "take_profit": 1.104, "direction": "BUY"}
        result = calculate_shadow_outcome(dec, actual_exit_price=1.105)
        assert result["r_multiple"] == 0.0


# ═══════════════════════════════════════════════════════════════
# PROGRESSIVE EVIDENCE
# ═══════════════════════════════════════════════════════════════

class TestProgressiveEvidence:
    def test_small_sample_exploratory(self, runner):
        runner.start_shadow("PROG_1", {"stop_multiplier": 1.5})
        for i in range(5):
            runner.process_trade(_make_trade(f"pos_{i}", exit_price=1.101))
        evidence = runner.get_evidence(runner.registry.list_active()[0].shadow_id)
        assert evidence["maturity"] in ("EXPLORATORY", "EARLY")

    def test_larger_sample_develops(self, runner):
        sc = runner.start_shadow("PROG_2", {"stop_multiplier": 1.3})
        for i in range(25):
            exit_p = 1.102 if i % 3 == 0 else 1.097
            runner.process_trade(_make_trade(f"pos_{i}", exit_price=exit_p))
        evidence = runner.get_evidence(sc.shadow_id)
        assert evidence["maturity"] in ("EARLY", "DEVELOPING", "STRONG")


# ═══════════════════════════════════════════════════════════════
# REGRESSION DETECTION
# ═══════════════════════════════════════════════════════════════

class TestRegressionDetection:
    def test_metrics_include_delta(self, runner):
        sc = runner.start_shadow("REG_1", {"stop_multiplier": 1.5})
        for i in range(10):
            runner.process_trade(_make_trade(f"pos_{i}", exit_price=1.099))
        metrics = runner.get_metrics(sc.shadow_id)
        assert "delta" in metrics
        assert "expectancy_r" in metrics["delta"]


# ═══════════════════════════════════════════════════════════════
# PERSISTENCE
# ═══════════════════════════════════════════════════════════════

class TestPersistence:
    def test_state_survives_reload(self, tmp_path):
        runner1 = ShadowRunner(shadow_dir=str(tmp_path))
        sc = runner1.start_shadow("PERSIST_1", {"stop_multiplier": 1.5})
        runner1.process_trade(_make_trade("pos_1", exit_price=1.102))

        # New runner instance loads from disk
        runner2 = ShadowRunner(shadow_dir=str(tmp_path))
        loaded = runner2.registry.get_candidate(sc.shadow_id)
        assert loaded is not None
        assert loaded.candidate_id == "PERSIST_1"
        comparisons = runner2.registry.get_comparisons(sc.shadow_id)
        assert len(comparisons) == 1


# ═══════════════════════════════════════════════════════════════
# RESTART RECOVERY
# ═══════════════════════════════════════════════════════════════

class TestRestartRecovery:
    def test_active_candidates_resume(self, tmp_path):
        runner1 = ShadowRunner(shadow_dir=str(tmp_path))
        runner1.start_shadow("RESUME_A", {"stop_multiplier": 1.2})
        runner1.start_shadow("RESUME_B", {"score_threshold": 0.6})

        # Simulate restart
        runner2 = ShadowRunner(shadow_dir=str(tmp_path))
        active = runner2.registry.list_active()
        assert len(active) == 2


# ═══════════════════════════════════════════════════════════════
# CANDIDATE LIFECYCLE
# ═══════════════════════════════════════════════════════════════

class TestLifecycle:
    def test_stop_shadow(self, runner):
        sc = runner.start_shadow("LIFE_1", {"stop_multiplier": 1.5})
        runner.stop_shadow(sc.shadow_id)
        assert runner.registry.get_candidate(sc.shadow_id).status == ShadowStatus.COMPLETED


# ═══════════════════════════════════════════════════════════════
# REPORT GENERATION
# ═══════════════════════════════════════════════════════════════

class TestReporting:
    def test_dashboard_generated(self, tmp_path):
        runner = ShadowRunner(shadow_dir=str(tmp_path / "shadow"))
        sc = runner.start_shadow("RPT_1", {"stop_multiplier": 1.5})
        runner.process_trade(_make_trade(exit_price=1.101))

        report = generate_shadow_dashboard(
            runner.registry, reports_dir=str(tmp_path / "reports")
        )
        assert report["total_shadows"] == 1
        assert (tmp_path / "reports" / "shadow_dashboard.json").exists()
        assert (tmp_path / "reports" / "shadow_dashboard.md").exists()


# ═══════════════════════════════════════════════════════════════
# LAMBDA COMPATIBILITY
# ═══════════════════════════════════════════════════════════════

class TestLambdaCompat:
    def test_handler_style_invocation(self, tmp_path):
        """Shadow engine works from a Lambda-style payload."""
        runner = ShadowRunner(shadow_dir=str(tmp_path))
        # Simulate Lambda event
        event = {"candidate_id": "LAMBDA_C1", "changes": {"stop_multiplier": 1.5}}
        runner.start_shadow(event["candidate_id"], event["changes"])
        runner.process_trade(_make_trade(exit_price=1.102))
        evidence = runner.get_evidence(runner.registry.list_active()[0].shadow_id)
        assert evidence["maturity"] != ""
