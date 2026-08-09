"""Tests for V10 Research Domain Architecture."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from research_engine.v10.domains.base import ResearchDomain, DomainRegistry, get_default_registry
from research_engine.v10.domains.trade import TradeDomain
from research_engine.v10.domains.decision import DecisionDomain
from research_engine.v10.domains.market import MarketDomain
from research_engine.v10.domains.strategy import StrategyDomain
from research_engine.v10.research_intelligence.question_registry import QuestionRegistry


# ═══════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════

def _sample_event(ticket=1, symbol="EURUSD", regime="TRENDING", strategy="REVERSAL"):
    return {
        "trade_id": f"pos_{ticket}",
        "execution": {
            "ticket": ticket, "symbol": symbol, "direction": "BUY",
            "entry_price": 1.1, "exit_price": 1.099, "entry_time": 1784808000.0,
            "exit_time": 1784809000.0, "stop_loss": 1.098, "take_profit": 1.103,
            "gross_profit": -0.5, "commission": -0.04, "swap": 0.0,
            "net_realised_pnl": -0.54, "r_multiple": -1.0, "volume": 0.01,
            "duration_seconds": 1000, "exit_reason": "STOP_LOSS",
        },
        "decision": {
            "strategy": strategy, "score": 0.55, "confidence": 0.7,
            "decision_type": "sym_cycle", "components": {"loc": 0.6, "struct": 0.5},
            "weakest_component": "struct", "ev": None, "p_success": None,
        },
        "market": {
            "regime": regime, "session": "LONDON", "volatility": "NEUTRAL",
            "trend_state": "BULLISH", "higher_timeframe_bias": "BULLISH",
            "h4_phase": "IMPULSE", "h1_clarity": 0.6,
        },
        "strategy": {
            "family": strategy, "pattern": "HAMMER", "conditions_met": 2,
            "strategy_confidence": 0.7, "opportunity_quality": 0.55,
            "opportunity_type": "ZONE_REACTION",
        },
        "quality": {
            "anomaly": False, "anomaly_reasons": [], "governance_status": "WARNING",
            "data_completeness": "COMPLETE", "missing": [], "join_method": "sym_cycle",
            "pnl_source": "MT5_BROKER",
        },
    }


@pytest.fixture
def sample_universe():
    return [_sample_event(i, regime="TRENDING" if i < 5 else "RANGING") for i in range(10)]


# ═══════════════════════════════════════════════════════════════
# DOMAIN ARCHITECTURE (1-4)
# ═══════════════════════════════════════════════════════════════

class TestDomainsInstantiate:
    def test_all_four_domains(self):
        assert TradeDomain()
        assert DecisionDomain()
        assert MarketDomain()
        assert StrategyDomain()

    def test_shared_interface(self):
        for DomainClass in [TradeDomain, DecisionDomain, MarketDomain, StrategyDomain]:
            d = DomainClass()
            assert isinstance(d, ResearchDomain)
            assert d.domain_id
            assert d.name
            assert d.observation_type
            assert callable(d.build_population)
            assert callable(d.get_questions)
            assert callable(d.coverage_report)

    def test_domain_metadata(self):
        for DomainClass in [TradeDomain, DecisionDomain, MarketDomain, StrategyDomain]:
            m = DomainClass().metadata()
            assert "domain_id" in m
            assert "name" in m
            assert "observation_type" in m

    def test_observation_grain_defined(self):
        assert TradeDomain().observation_type == "completed_trade"
        assert DecisionDomain().observation_type == "decision_event"
        assert MarketDomain().observation_type == "market_state_at_trade"
        assert StrategyDomain().observation_type == "strategy_observation"


# ═══════════════════════════════════════════════════════════════
# QUESTION REGISTRY (5-10)
# ═══════════════════════════════════════════════════════════════

class TestQuestionRegistry:
    def test_existing_questions_remain(self):
        reg = QuestionRegistry()
        for qid in ["E1", "E2", "R1", "R2", "D1", "D2", "D3", "M1", "OQ1", "OQ2"]:
            assert reg.get(qid) is not None, f"{qid} missing from registry"

    def test_questions_have_domain(self):
        reg = QuestionRegistry()
        for q in reg.list_all():
            assert q.domain in ("trade", "decision", "market", "strategy"), \
                f"{q.id} has invalid domain '{q.domain}'"

    def test_trade_questions_resolve(self):
        registry = get_default_registry()
        domain = registry.resolve_question_domain("E1")
        assert domain is not None
        assert domain.domain_id == "trade"

    def test_decision_questions_resolve(self):
        registry = get_default_registry()
        domain = registry.resolve_question_domain("D1")
        assert domain is not None
        assert domain.domain_id == "decision"

    def test_market_questions_resolve(self):
        registry = get_default_registry()
        domain = registry.resolve_question_domain("M1")
        assert domain is not None
        assert domain.domain_id == "market"

    def test_strategy_questions_resolve(self):
        registry = get_default_registry()
        domain = registry.resolve_question_domain("OQ1")
        assert domain is not None
        assert domain.domain_id == "strategy"


# ═══════════════════════════════════════════════════════════════
# EXPERIMENT RUNNER (11-14)
# ═══════════════════════════════════════════════════════════════

class TestDomainExecution:
    def test_trade_question_executes(self):
        universe = Path("data/research/research_universe.jsonl")
        if not universe.exists():
            pytest.skip("Universe not available")
        from research_engine.v10.research_intelligence import ExperimentRunner
        runner = ExperimentRunner()
        result = runner.run("E1")
        assert result.sample_size > 0
        assert not result.error

    def test_decision_question_resolves(self):
        registry = get_default_registry()
        domain = registry.resolve_question_domain("D1")
        assert domain.domain_id == "decision"

    def test_market_question_resolves(self):
        registry = get_default_registry()
        domain = registry.resolve_question_domain("M1")
        assert domain.domain_id == "market"

    def test_strategy_question_resolves(self):
        registry = get_default_registry()
        domain = registry.resolve_question_domain("OQ1")
        assert domain.domain_id == "strategy"


# ═══════════════════════════════════════════════════════════════
# SEGMENTATION (15-17)
# ═══════════════════════════════════════════════════════════════

class TestDomainSegmentation:
    def test_domain_specific_filters(self):
        td = TradeDomain()
        assert "instrument" in td.get_segmentation_filters()
        dd = DecisionDomain()
        assert "confidence" in dd.get_segmentation_filters()

    def test_shared_filters_compatible(self):
        # All domains should support instrument
        for DomainClass in [TradeDomain, DecisionDomain, MarketDomain, StrategyDomain]:
            filters = DomainClass().get_segmentation_filters()
            assert "instrument" in filters

    def test_cross_domain_identifiers(self, sample_universe):
        td = TradeDomain()
        dd = DecisionDomain()
        trade_pop = td.build_population(sample_universe)
        dec_pop = dd.build_population(sample_universe)
        # Both should have same trade_id linkage
        trade_ids = {e["trade_id"] for e in trade_pop}
        dec_trade_ids = {e["trade_id"] for e in dec_pop}
        assert trade_ids == dec_trade_ids


# ═══════════════════════════════════════════════════════════════
# DATA QUALITY (18-20)
# ═══════════════════════════════════════════════════════════════

class TestDataQuality:
    def test_missing_data_reported_as_gap(self, sample_universe):
        dd = DecisionDomain()
        report = dd.coverage_report(sample_universe)
        # Decision domain reports NO_TRADE gap
        assert any("NO_TRADE" in g or "DATA GAP" in g for g in report["gaps"])

    def test_no_fabricated_records(self, sample_universe):
        td = TradeDomain()
        pop = td.build_population(sample_universe)
        # Output count must equal input count (no fabrication)
        assert len(pop) == len(sample_universe)

    def test_coverage_status_values(self, sample_universe):
        for DomainClass in [TradeDomain, DecisionDomain, MarketDomain, StrategyDomain]:
            report = DomainClass().coverage_report(sample_universe)
            assert report["coverage_status"] in ("AVAILABLE", "PARTIAL", "MISSING")


# ═══════════════════════════════════════════════════════════════
# CROSS DOMAIN (21-24)
# ═══════════════════════════════════════════════════════════════

class TestCrossDomain:
    def test_trade_to_decision_linkage(self, sample_universe):
        td = TradeDomain()
        dd = DecisionDomain()
        trades = td.build_population(sample_universe)
        decisions = dd.build_population(sample_universe)
        # Every trade should have a corresponding decision
        for t in trades:
            matching = [d for d in decisions if d["trade_id"] == t["trade_id"]]
            assert len(matching) == 1

    def test_trade_to_strategy_linkage(self, sample_universe):
        td = TradeDomain()
        sd = StrategyDomain()
        trades = td.build_population(sample_universe)
        strategies = sd.build_population(sample_universe)
        trade_ids = {t["trade_id"] for t in trades}
        strat_ids = {s["trade_id"] for s in strategies}
        assert trade_ids == strat_ids

    def test_trade_to_market_linkage(self, sample_universe):
        td = TradeDomain()
        md = MarketDomain()
        trades = td.build_population(sample_universe)
        markets = md.build_population(sample_universe)
        trade_ids = {t["trade_id"] for t in trades}
        market_ids = {m["trade_id"] for m in markets}
        assert trade_ids == market_ids

    def test_multi_domain_linked(self, sample_universe):
        """All four domains can resolve linked observations for the same trade."""
        registry = get_default_registry()
        trade_id = sample_universe[0]["trade_id"]
        for domain in registry.all():
            pop = domain.build_population(sample_universe)
            matching = [e for e in pop if e.get("trade_id") == trade_id]
            assert len(matching) == 1


# ═══════════════════════════════════════════════════════════════
# GOVERNANCE (25-27)
# ═══════════════════════════════════════════════════════════════

class TestDomainGovernance:
    def test_domain_findings_pass_governance(self):
        universe = Path("data/research/research_universe.jsonl")
        if not universe.exists():
            pytest.skip("Universe not available")
        from research_engine.v10.research_intelligence import ExperimentRunner
        runner = ExperimentRunner()
        result = runner.run_with_governance("E1")
        assert "governance" in result
        assert result["governance"]["confidence"]["level"] in ("HIGH", "MEDIUM", "LOW")

    def test_evidence_maturity_works(self):
        universe = Path("data/research/research_universe.jsonl")
        if not universe.exists():
            pytest.skip("Universe not available")
        from research_engine.v10.research_intelligence import ExperimentRunner
        runner = ExperimentRunner()
        result = runner.run_with_governance("E1")
        assert result["governance"]["evidence"]["maturity"] != ""

    def test_finding_history_compatible(self, tmp_path):
        from research_engine.v10.research_governance import FindingHistory
        fh = FindingHistory(history_dir=str(tmp_path))
        fh.append("trade_E1_FULL", {"domain": "trade", "sample": 94})
        assert fh.latest("trade_E1_FULL")["domain"] == "trade"


# ═══════════════════════════════════════════════════════════════
# LAMBDA (28)
# ═══════════════════════════════════════════════════════════════

class TestLambdaCompat:
    def test_domain_campaigns_via_common_interface(self):
        """All domains can be invoked through a single registry interface."""
        registry = get_default_registry()
        events = [_sample_event(i) for i in range(5)]
        for domain in registry.all():
            pop = domain.build_population(events)
            assert len(pop) >= 0  # Should not crash
            questions = domain.get_questions()
            assert len(questions) > 0
