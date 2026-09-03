"""
Regression: test execution must never write into production runtime paths.

Issue 3 (broker-close forensic audit): synthetic test-fixture trades
(today_trade / survived_1 / survived_2 / pos_100 / t1) previously leaked into
committed production `logs/trade_truth`, `logs/risk_deviation`, etc., because
tests called persist_trade() which pivoted to the trade_truth / risk_deviation
production writers without path redirection.

Fix:
  * tests/conftest.py: an autouse fixture redirects every runtime persistence
    sink to a per-test temp directory.
  * core/trade_journal.persist_trade now routes trade_truth through the same
    `_get_trade_truth_dir()` indirection already used for the journal dir.

These tests prove the regression is closed.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.trade_journal import build_trade_record, persist_trade
from core.trade_management.position import Position, PositionStatus
from strategy.signals import Side


def _fixture_position(trade_id: str = "today_trade") -> Position:
    """Replicates the exact fixture shape that previously contaminated the
    production tree (EURUSD, entry 1.1000, exit 1.1050, initial_sl 1.0950)."""
    return Position(
        position_id=trade_id,
        symbol="EURUSD",
        side=Side.BUY,
        magic=713001,
        entry_price=1.1000,
        initial_sl=1.0950,
        initial_tp=1.1100,
        stop_loss=1.0950,
        take_profit=1.1100,
        volume=0.10,
        open_time=1717400000.0,
        status=PositionStatus.CLOSED,
        mt5_ticket=12345,
        deal_id=12345,
        order_id=99999,
        pattern_tag="ENGULFING_BULLISH",
        max_favourable_price=1.1050,
    )


def _fixture_record(trade_id: str = "today_trade", pnl: float = 40.0):
    return build_trade_record(
        position=_fixture_position(trade_id),
        exit_price=1.1050,
        exit_time=1717403600.0,
        close_reason="take_profit",
        realised_pnl_override=pnl,
    )


def _files_under(path: Path) -> set[Path]:
    if not path.exists():
        return set()
    return {p for p in path.rglob("*") if p.is_file()}


class TestRuntimePathIsolation:
    def test_persist_trade_cannot_write_production_paths(self, tmp_path):
        """Running the exact persistence path that previously contaminated the
        repo must not create ANY file under the repository logs/ tree."""
        logs_root = ROOT / "logs"
        before = _files_under(logs_root)

        assert persist_trade(_fixture_record("survived_1")) is True
        assert persist_trade(_fixture_record("survived_2")) is True

        after = _files_under(logs_root)
        assert after == before, (
            "test persist_trade() wrote into the repository logs/ tree: "
            f"{sorted(str(p.relative_to(ROOT)) for p in (after - before))}"
        )

        # The records landed in the isolated per-test sinks instead.
        assert (tmp_path / "trade_truth" / "EURUSD").exists()
        assert (tmp_path / "risk_deviation" / "EURUSD").exists()
        assert (tmp_path / "trade_journal").exists()

    def test_production_trade_truth_untouched_after_full_pipeline(self):
        """Even without asserting on tmp_path, the autouse isolation guarantees
        production trade_truth stays byte-identical after the full pipeline."""
        truth_root = ROOT / "logs" / "trade_truth"
        before = _files_under(truth_root)

        for tid in ("today_trade", "pos_100", "t1"):
            assert persist_trade(_fixture_record(tid, pnl=50.0)) is True

        after = _files_under(truth_root)
        assert after == before

    def test_persist_trade_routes_truth_via_indirection(self, tmp_path):
        """persist_trade must write trade_truth to _get_trade_truth_dir(), never
        to the hard-coded production path (this fails before the fix)."""
        from core.trade_journal import _get_trade_truth_dir

        truth_dir = _get_trade_truth_dir()  # redirected to tmp by autouse fixture
        assert truth_dir == tmp_path / "trade_truth"

        assert persist_trade(_fixture_record("pos_route")) is True

        produced = list((truth_dir / "EURUSD").glob("*.jsonl"))
        assert produced, "trade_truth record was not written to the isolated dir"
        assert not (ROOT / "logs" / "trade_truth" / "EURUSD" / "2026-06-03.jsonl").exists()

    def test_config_level_truth_dir_cannot_bypass_isolation(self, monkeypatch, tmp_path):
        """Even setting TRADE_TRUTH_DIR back at the production path must not let
        test persistence reach the repository tree: the isolation fixture keeps
        the effective write target inside the per-test temp dir."""
        from core.trade_journal import _get_trade_truth_dir
        import core.config as _cfg_mod

        monkeypatch.setattr(
            _cfg_mod,
            "TRADE_TRUTH_DIR",
            str(ROOT / "logs" / "trade_truth"),
            raising=False,
        )

        assert _get_trade_truth_dir() == tmp_path / "trade_truth"
        assert persist_trade(_fixture_record("pos_cfg_bypass")) is True
        leaked = ROOT / "logs" / "trade_truth" / "EURUSD" / "2026-06-03.jsonl"
        assert not leaked.exists(), "production trade_truth must never receive test records"

    def test_excursion_persist_cannot_write_production_paths(self, tmp_path):
        """Regression (found during verification): persist_excursion() previously
        wrote {ticket}.json checkpoints into the repository logs/position_excursion
        tree (overwriting the tracked 99999.json runtime record). The config-level
        POSITION_EXCURSION_DIR hook must keep it inside the per-test temp dir."""
        from types import SimpleNamespace

        from core.trade_management import excursion_state

        # S3 mirror must be force-disabled for tests (conftest guarantees this).
        assert excursion_state._s3_mirror_enabled() is False

        exc_root = ROOT / "logs" / "position_excursion"
        before = _files_under(exc_root)

        pos = SimpleNamespace(
            mt5_ticket=424242,
            position_id="pos_exc_iso",
            symbol="EURUSD",
            side=Side.BUY,
            entry_price=1.1000,
            max_favourable_price=1.1050,
            max_adverse_price=1.0990,
            trade_identity=None,
        )
        excursion_state.persist_excursion(pos)

        after = _files_under(exc_root)
        assert after == before, (
            "persist_excursion() wrote into the repository logs/position_excursion tree: "
            f"{sorted(str(p.relative_to(ROOT)) for p in (after - before))}"
        )

        # The checkpoint landed in the isolated per-test sink instead.
        assert (tmp_path / "position_excursion" / "424242.json").exists()
        assert not (exc_root / "424242.json").exists()

    def test_all_runtime_sinks_redirect_via_injection_hooks(self, tmp_path):
        """Every runtime persistence sink identified in the forensic audit must
        resolve inside the per-test temp dir while tests run. A future refactor
        that removes an injection hook (or adds a new hard-coded sink without
        extending the conftest fixture) fails here."""
        import core.config as cfg
        import core.decision_trace
        import core.market_context.persistence as mc_persistence
        import core.research_events
        import core.shadow_trades
        import research_engine.lifecycle.candidate_evaluation_bridge as eval_bridge
        import research_engine.lifecycle.orchestrator as lifecycle_orchestrator
        import strategy.trace_activation

        def _inside(p) -> bool:
            return str(tmp_path) in str(p)

        assert _inside(core.shadow_trades._LOCAL_DIR)
        assert _inside(strategy.trace_activation._TRACE_LOG)
        assert _inside(core.research_events._EVENT_DIR)
        assert _inside(core.decision_trace._LOCAL_DIR)
        assert _inside(mc_persistence._LOCAL_DIR)
        assert _inside(eval_bridge._EVALUATIONS_DIR)
        assert _inside(lifecycle_orchestrator._REPORT_DIR)
        # Lazy-getattr config fallbacks (created by the isolation fixture):
        assert _inside(getattr(cfg, "DRAWDOWN_PEAK_FILE", ""))
        assert _inside(getattr(cfg, "HEARTBEAT_FILE", ""))
        assert _inside(getattr(cfg, "DAILY_LOSS_STATE_FILE", ""))
        assert _inside(getattr(cfg, "EQUITY_CURVE_FILE", ""))

    def test_default_quarantine_store_is_isolated(self, tmp_path):
        """QuarantineStore binds local_dir=_LOCAL_DIR at class-definition time,
        so a plain module-attr patch is ineffective; the conftest fixture wraps
        __init__. A default-constructed store (e.g. from trade_truth quarantine)
        must therefore still land inside the per-test temp dir."""
        from core.contracts.quarantine import QuarantineStore

        assert str(tmp_path) in str(QuarantineStore()._local_dir)

    def test_research_and_decision_writes_stay_out_of_repo(self, tmp_path):
        """Regression (found during full-suite verification): research_events,
        decision_trace, shadow_trades, strategy_trace, market_context,
        research_lifecycle evaluations and hypothesis reports all previously
        wrote into the repository logs/ and reports/ trees during tests."""
        from types import SimpleNamespace

        from core import decision_trace as dt
        from core import research_events as rev

        logs_root = ROOT / "logs"
        reports_root = ROOT / "reports"
        before_logs = _files_under(logs_root)
        before_reports = _files_under(reports_root)

        rev.persist_guard_event(
            symbol="EURUSD",
            cycle_id=0,
            correlation_id="isolation_probe",
            guard_name="iso_guard",
            allowed=True,
            reason="test isolation probe",
        )
        stub_trace = SimpleNamespace(
            symbol="EURUSD",
            timestamp_utc="2026-06-03T00:00:00+00:00",
            to_dict=lambda: {"symbol": "EURUSD"},
        )
        dt.persist_decision_trace(stub_trace)

        assert list((tmp_path / "research_events").glob("*.jsonl"))
        assert (tmp_path / "decision_trace" / "EURUSD").exists()
        assert _files_under(logs_root) == before_logs, (
            "research/decision test writes leaked into the repository logs/ tree"
        )
        assert _files_under(reports_root) == before_reports