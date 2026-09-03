"""
Shared pytest fixtures for the trading system test suite.

Provides:
- Synthetic candle generation
- Mock MT5 client
- Risk state reset
- Pattern registry loading
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure project root is on path
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data.mt5_data import Candle
from strategy.signals import Side, Signal


# ─── CANDLE FIXTURES ──────────────────────────────────────────────────────────

@pytest.fixture
def make_candle():
    """Factory fixture for creating synthetic candles."""
    def _make(t: int, o: float, h: float, l: float, c: float, tv: int = 0) -> Candle:
        return Candle(time=t, open=o, high=h, low=l, close=c, tick_volume=tv)
    return _make


@pytest.fixture
def bullish_candles(make_candle):
    """Three progressive bullish candles (three white soldiers shape)."""
    return [
        make_candle(1, 1.00, 1.02, 1.00, 1.02),
        make_candle(2, 1.02, 1.04, 1.02, 1.04),
        make_candle(3, 1.04, 1.06, 1.04, 1.06),
    ]


@pytest.fixture
def bearish_candles(make_candle):
    """Three progressive bearish candles (three black crows shape)."""
    return [
        make_candle(1, 1.06, 1.06, 1.04, 1.04),
        make_candle(2, 1.04, 1.04, 1.02, 1.02),
        make_candle(3, 1.02, 1.02, 1.00, 1.00),
    ]


@pytest.fixture
def engulfing_bullish(make_candle):
    """Two candles forming a bullish engulfing pattern."""
    return [
        make_candle(1, 1.10, 1.10, 1.08, 1.08),  # bearish
        make_candle(2, 1.07, 1.12, 1.07, 1.12),  # bullish engulfs
    ]


@pytest.fixture
def flat_candles(make_candle):
    """Zero-range candles (no pattern should fire)."""
    return [
        make_candle(1, 1.10, 1.10, 1.10, 1.10),
        make_candle(2, 1.10, 1.10, 1.10, 1.10),
        make_candle(3, 1.10, 1.10, 1.10, 1.10),
    ]


# ─── PATTERN REGISTRY ─────────────────────────────────────────────────────────

@pytest.fixture(autouse=True, scope="session")
def load_patterns():
    """Ensure pattern registry is loaded for all tests."""
    from patterns.registry import load_all_patterns
    load_all_patterns()


# ─── MOCK MT5 ─────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_mt5():
    """Provide a mock MT5 module for testing without live connection."""
    with patch("risk.guards.mt5") as m:
        yield m


@pytest.fixture
def mock_mt5_account():
    """Mock MT5 with valid account info."""
    with patch("risk.guards.mt5") as m:
        acct = MagicMock()
        acct.balance = 10000.0
        acct.equity = 10000.0
        m.account_info.return_value = acct
        yield m


# ─── RISK STATE RESET ─────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_risk_metrics():
    """Reset risk metrics between tests to prevent state leakage."""
    yield
    try:
        from risk.metrics import risk_metrics
        risk_metrics.reset()
    except Exception:
        pass


@pytest.fixture(autouse=True)
def reset_rejection_metrics():
    """Reset rejection counters between tests."""
    yield
    try:
        from risk.manager import reset_rejection_metrics
        reset_rejection_metrics()
    except Exception:
        pass


# ─── S3 ISOLATION (prevent test contamination of production storage) ──────────

@pytest.fixture(autouse=True)
def disable_s3_mirror_in_tests(monkeypatch):
    """
    Prevent ALL tests from writing to production S3.

    Tests must never interact with the production S3 bucket.
    Local persistence to temp directories is unaffected.
    """
    monkeypatch.setattr("core.config.EVENT_STREAM_S3_MIRROR", False)
    # Durable runtime-state mirrors (excursion checkpoints, engine state).
    monkeypatch.setattr("core.config.POSITION_EXCURSION_S3_MIRROR", False, raising=False)


@pytest.fixture(autouse=True)
def isolate_runtime_persistence_dirs(tmp_path, monkeypatch):
    """
    Redirect every runtime persistence sink to an isolated per-test temp dir.

    Guarantees test execution can never write into production runtime paths
    (logs/, events/, research_data/, S3). This is the direct fix for synthetic
    test fixtures (today_trade / survived_1 / survived_2 / pos_100 / t1 from
    test_trade_journal._make_*) appearing inside committed production
    logs/trade_truth and logs/risk_deviation.

    Sinks redirected here (all via existing path injection hooks — no
    production persistence semantics are modified):
      - trade_journal (logs/trade_journal)
      - trade_truth   (logs/trade_truth — via _get_trade_truth_dir)
      - risk_deviation (logs/risk_deviation)
      - management_actions (logs/management_actions)
      - execution_attempts (logs/execution_attempts)
      - execution_results (logs/execution_results)
      - execution_context (logs/execution_context)
      - event_stream (events/)
      - position_excursion (logs/position_excursion — via config injection)
      - weekend state (runtime/weekend_state.json — via config injection)
      - engine state (logs/state — via config injection)
      - decision trace (logs/decision_trace — via config injection)

    Because the autouse fixture runs for EVERY test, even a test that does not
    opt in cannot contaminate production paths.
    """
    monkeypatch.setattr(
        "core.trade_journal._get_journal_dir", lambda: tmp_path / "trade_journal"
    )
    monkeypatch.setattr(
        "core.trade_journal._get_trade_truth_dir", lambda: tmp_path / "trade_truth"
    )
    monkeypatch.setattr(
        "core.risk_deviation._LOCAL_DIR", str(tmp_path / "risk_deviation")
    )
    monkeypatch.setattr(
        "core.persistence.management_actions_writer._LOCAL_DIR",
        str(tmp_path / "management_actions"),
    )
    monkeypatch.setattr(
        "core.persistence.execution_attempts_writer._LOCAL_DIR",
        str(tmp_path / "execution_attempts"),
    )
    monkeypatch.setattr(
        "core.persistence.execution_result_writer._LOCAL_DIR",
        str(tmp_path / "execution_results"),
    )
    monkeypatch.setattr(
        "core.execution_context._LOCAL_DIR", str(tmp_path / "execution_context")
    )
    monkeypatch.setattr("core.event_stream._EVENT_DIR", tmp_path / "events")
    # Config-level injection hooks (each module resolves these via
    # getattr(config, ...) on every call, so patching core.config is the
    # canonical redirection mechanism — no production code special-casing):
    monkeypatch.setattr(
        "core.config.POSITION_EXCURSION_DIR",
        str(tmp_path / "position_excursion"),
        raising=False,
    )
    monkeypatch.setattr(
        "core.config.WEEKEND_STATE_FILE",
        str(tmp_path / "weekend_state.json"),
        raising=False,
    )
    monkeypatch.setattr(
        "core.config.ENGINE_STATE_PERSIST_DIR",
        str(tmp_path / "state"),
        raising=False,
    )
    monkeypatch.setattr(
        "core.config.DECISION_TRACE_DIR",
        str(tmp_path / "decision_trace"),
        raising=False,
    )
    # Additional runtime-state sinks (module-attr injection hooks — the same
    # pattern the persistence layer already uses; defaults unchanged):
    monkeypatch.setattr("core.shadow_trades._LOCAL_DIR", str(tmp_path / "shadow_trades"))
    monkeypatch.setattr(
        "strategy.trace_activation._TRACE_LOG", tmp_path / "strategy_trace.jsonl"
    )
    monkeypatch.setattr("core.research_events._EVENT_DIR", tmp_path / "research_events")
    monkeypatch.setattr("core.decision_trace._LOCAL_DIR", str(tmp_path / "decision_trace"))
    monkeypatch.setattr(
        "core.market_context.persistence._LOCAL_DIR", str(tmp_path / "market_context")
    )
    monkeypatch.setattr(
        "research_engine.lifecycle.candidate_evaluation_bridge._EVALUATIONS_DIR",
        tmp_path / "research_lifecycle" / "evaluations",
    )
    monkeypatch.setattr(
        "research_engine.lifecycle.orchestrator._REPORT_DIR",
        tmp_path / "reports" / "research" / "lifecycle",
    )
    # QuarantineStore binds its default local_dir at class-definition time
    # (local_dir: str = _LOCAL_DIR), so patching the module attribute is
    # ineffective — wrap __init__ instead, preserving any explicitly injected
    # local_dir (e.g. tests that pass their own tmp dir):
    import core.contracts.quarantine as _qmod

    _orig_q_init = _qmod.QuarantineStore.__init__

    def _isolated_q_init(store_self, *, local_dir=None):
        _orig_q_init(
            store_self,
            local_dir=str(tmp_path / "quarantine") if local_dir is None else local_dir,
        )

    monkeypatch.setattr(_qmod.QuarantineStore, "__init__", _isolated_q_init)
    # Remaining config-hooked runtime state (each module resolves via
    # getattr(config, ...) per call — heartbeat, risk-state and runtime files):
    for _cfg_sink, _name in (
        ("HEARTBEAT_FILE", "heartbeat.json"),
        ("DRAWDOWN_PEAK_FILE", "drawdown_peak.json"),
        ("EQUITY_CURVE_FILE", "equity_curve.jsonl"),
        ("SLIPPAGE_JOURNAL_FILE", "slippage_journal.jsonl"),
        ("CHALLENGE_PROGRESS_FILE", "challenge_progress.json"),
        ("CONSISTENCY_STATE_FILE", "consistency_tracker.json"),
        ("DAILY_RESET_STATE_FILE", "daily_reset_state.json"),
        ("DAILY_LOSS_STATE_FILE", "daily_loss_state.json"),
        ("DAILY_TRADE_LIMIT_STATE_FILE", "daily_trade_limit_state.json"),
        ("TRADE_COOLDOWN_STATE_FILE", "trade_cooldown_state.json"),
        ("KILL_SWITCH_PATH", "kill_switch"),
        ("INSTANCE_LOCK_PATH", "instance.lock"),
    ):
        monkeypatch.setattr(
            f"core.config.{_cfg_sink}", str(tmp_path / _name), raising=False
        )
    yield tmp_path
