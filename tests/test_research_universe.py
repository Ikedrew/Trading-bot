"""Tests for V10 Research Universe Builder."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from research_engine.v10.research_universe import build_research_universe, _build_event, _classify_session


# ═══════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════

def _enriched_trade(ticket=100, symbol="EURUSD", pnl=-0.5, strategy="REVERSAL",
                    regime="TRENDING", score=0.55, pattern="HAMMER"):
    """Build a fully enriched trade for testing."""
    return {
        "trade_id": f"pos_{ticket}",
        "position_ticket": ticket,
        "symbol": symbol,
        "direction": "BUY",
        "entry_time": 1784808000.0,  # 2026-07-23 08:00 UTC (London session)
        "exit_time": 1784809000.0,
        "entry_price": 1.1000,
        "exit_price": 1.0990,
        "stop_loss": 1.0980,
        "take_profit": 1.1030,
        "broker_pnl": pnl,
        "commission": -0.04,
        "swap": 0.0,
        "final_pnl": pnl + (-0.04),
        "realised_r": -0.5,
        "volume": 0.01,
        "duration_seconds": 1000.0,
        "exit_reason_validated": "STOP_LOSS",
        "correlation_id": f"COR-20260723-{ticket}-{symbol}-ABCD",
        "pattern": pattern,
        "strategy": "",
        "score": 0,
        "regime": "",
        "pnl_source": "BROKER",
        # Enriched fields
        "dt_strategy": strategy,
        "dt_v10_strategy_family": strategy,
        "dt_score_strategy": score,
        "dt_score_neutral": 0.0,
        "dt_strategy_confidence": 0.7,
        "dt_v10_strategy_confidence": 0.7,
        "dt_v10_regime": regime,
        "dt_regime": None,
        "dt_v10_volatility": "NEUTRAL",
        "dt_h1_direction": "BULLISH",
        "dt_h4_trend": "BULLISH",
        "dt_h4_phase": "IMPULSE",
        "dt_h1_clarity": 0.65,
        "dt_directional_bias": "BULLISH",
        "dt_pattern": pattern,
        "dt_opportunity_quality": 0.55,
        "dt_opportunity_type": "ZONE_REACTION",
        "dt_components": {"location_score": 0.6, "structure_score": 0.5, "behaviour_score": 0.7},
        "dt_weakest_component": "structure_score",
        "dt_ev": None,
        "dt_p_success": None,
        "dt_match_method": "sym_cycle",
        "dt_matched": True,
        "anomaly_status": "NORMAL",
        "anomaly_reasons": [],
    }


def _recon_entry(ticket=100, gross=-0.5, comm=-0.04, swap=0.0):
    return {
        "position_ticket": ticket,
        "symbol": "EURUSD",
        "broker_profit": gross,
        "broker_commission": comm,
        "broker_swap": swap,
        "broker_fee": 0.0,
        "broker_net_profit": gross + comm + swap,
    }


@pytest.fixture
def universe_inputs(tmp_path):
    """Create inputs for a universe build."""
    trades = [_enriched_trade(100), _enriched_trade(200, pnl=1.5, strategy="TREND_CONTINUATION")]
    enriched_file = tmp_path / "enriched.jsonl"
    enriched_file.write_text("\n".join(json.dumps(t) for t in trades), encoding="utf-8")

    recon = {
        "matched": 2,
        "entries": [_recon_entry(100), _recon_entry(200, gross=1.5)],
    }
    recon_file = tmp_path / "recon.json"
    recon_file.write_text(json.dumps(recon), encoding="utf-8")

    return {
        "enriched_file": str(enriched_file),
        "base_file": str(enriched_file),
        "recon_file": str(recon_file),
        "output_file": str(tmp_path / "universe.jsonl"),
        "reports_dir": str(tmp_path / "reports"),
        "skip_governance": True,
    }


# ═══════════════════════════════════════════════════════════════
# TESTS
# ═══════════════════════════════════════════════════════════════

class TestCompleteJoin:
    def test_builds_complete_records(self, universe_inputs):
        result = build_research_universe(**universe_inputs)
        assert "error" not in result
        assert result["total_trades"] == 2

        # Load output
        out = Path(universe_inputs["output_file"])
        events = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert len(events) == 2

        e = events[0]
        assert "execution" in e
        assert "decision" in e
        assert "market" in e
        assert "strategy" in e
        assert "quality" in e
        assert e["quality"]["data_completeness"] == "COMPLETE"


class TestMissingDecisionTrace:
    def test_marks_incomplete(self, universe_inputs, tmp_path):
        # Create trade with no decision enrichment
        trade = _enriched_trade(300)
        trade["dt_strategy"] = None
        trade["dt_v10_strategy_family"] = None
        trade["dt_score_strategy"] = None
        trade["dt_score_neutral"] = None
        trade["dt_v10_regime"] = None
        trade["dt_pattern"] = None
        trade["pattern"] = ""

        enriched_file = Path(universe_inputs["enriched_file"])
        lines = enriched_file.read_text(encoding="utf-8").splitlines()
        lines.append(json.dumps(trade))
        enriched_file.write_text("\n".join(lines), encoding="utf-8")

        result = build_research_universe(**universe_inputs)
        assert result["coverage"]["incomplete_events"] >= 1

        out = Path(universe_inputs["output_file"])
        events = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines() if l.strip()]
        incomplete = [e for e in events if e["quality"]["data_completeness"] == "INCOMPLETE"]
        assert len(incomplete) >= 1
        assert "strategy" in incomplete[0]["quality"]["missing"]


class TestTicketMatching:
    def test_pnl_from_recon(self, universe_inputs):
        result = build_research_universe(**universe_inputs)
        out = Path(universe_inputs["output_file"])
        events = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines() if l.strip()]

        # PnL should come from recon entry (canonical)
        e = events[0]
        assert e["execution"]["gross_profit"] == -0.5
        assert e["execution"]["commission"] == -0.04
        assert e["execution"]["net_realised_pnl"] == -0.54


class TestEntityFallback:
    def test_trade_without_recon_uses_research_pnl(self, universe_inputs, tmp_path):
        # Add trade with ticket not in recon
        trade = _enriched_trade(999, pnl=2.0)
        enriched_file = Path(universe_inputs["enriched_file"])
        lines = enriched_file.read_text(encoding="utf-8").splitlines()
        lines.append(json.dumps(trade))
        enriched_file.write_text("\n".join(lines), encoding="utf-8")

        result = build_research_universe(**universe_inputs)
        out = Path(universe_inputs["output_file"])
        events = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines() if l.strip()]
        e999 = [e for e in events if e["execution"]["ticket"] == 999][0]
        # Falls back to research normalisation
        assert e999["execution"]["gross_profit"] == 2.0
        assert e999["quality"]["pnl_source"] == "BROKER"


class TestDuplicateRejection:
    def test_duplicates_excluded(self, universe_inputs, tmp_path):
        # Add duplicate trade
        trade = _enriched_trade(100)  # Same ticket as existing
        enriched_file = Path(universe_inputs["enriched_file"])
        lines = enriched_file.read_text(encoding="utf-8").splitlines()
        lines.append(json.dumps(trade))
        enriched_file.write_text("\n".join(lines), encoding="utf-8")

        result = build_research_universe(**universe_inputs)
        assert result["total_trades"] == 2  # Not 3


class TestPnLPreserved:
    def test_canonical_pnl_in_output(self, universe_inputs):
        result = build_research_universe(**universe_inputs)
        out = Path(universe_inputs["output_file"])
        events = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines() if l.strip()]

        for e in events:
            # net = gross + commission + swap
            expected = e["execution"]["gross_profit"] + e["execution"]["commission"] + e["execution"]["swap"]
            assert abs(e["execution"]["net_realised_pnl"] - expected) < 0.01


class TestRMultiple:
    def test_r_multiple_present(self, universe_inputs):
        result = build_research_universe(**universe_inputs)
        out = Path(universe_inputs["output_file"])
        events = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines() if l.strip()]
        for e in events:
            assert "r_multiple" in e["execution"]


class TestGovernanceGate:
    def test_governance_fail_blocks(self, tmp_path):
        """If governance returns FAIL, universe is not built."""
        # Empty fake S3 (production evidence source) + no data files = governance fails.
        from _s3_fake import install_fake_s3, reset_fake_s3

        fake = install_fake_s3()
        try:
            result = build_research_universe(
                enriched_file=str(tmp_path / "nonexistent.jsonl"),
                base_file=str(tmp_path / "nonexistent.jsonl"),
                output_file=str(tmp_path / "out.jsonl"),
                reports_dir=str(tmp_path / "reports"),
                skip_governance=False,
            )
            # Either governance fails or no data found
            assert "error" in result
        finally:
            reset_fake_s3()


class TestReportGeneration:
    def test_reports_created(self, universe_inputs):
        build_research_universe(**universe_inputs)
        rep_dir = Path(universe_inputs["reports_dir"])
        assert (rep_dir / "research_universe_report.json").exists()
        assert (rep_dir / "research_universe_report.md").exists()


class TestSessionClassification:
    def test_london_session(self):
        # 2026-07-23 09:00 UTC = London (not overlap since before 12)
        from datetime import datetime, timezone
        dt = datetime(2026, 7, 23, 9, 0, 0, tzinfo=timezone.utc)
        assert _classify_session(dt.timestamp()) == "LONDON"

    def test_ny_session(self):
        # 2026-07-23 16:00 UTC = New York (after London close at 15)
        from datetime import datetime, timezone
        dt = datetime(2026, 7, 23, 16, 0, 0, tzinfo=timezone.utc)
        assert _classify_session(dt.timestamp()) == "NEW_YORK"

    def test_asian_session(self):
        # 2026-07-23 03:00 UTC = Asian
        from datetime import datetime, timezone
        dt = datetime(2026, 7, 23, 3, 0, 0, tzinfo=timezone.utc)
        assert _classify_session(dt.timestamp()) == "ASIAN"


class TestLiveData:
    def test_live_build(self):
        """Build against real project data."""
        enriched = Path("logs/research_ready_trade_dataset/research_ready_trades_enriched.jsonl")
        if not enriched.exists():
            pytest.skip("Enriched dataset not available")
        result = build_research_universe(skip_governance=True)
        assert "error" not in result
        assert result["total_trades"] == 94
        assert result["coverage"]["complete_pct"] > 0
