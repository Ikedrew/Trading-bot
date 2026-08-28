from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from core import config
from core.engine import EngineState
from core.engine_state import validate_engine_state
from core.decision_audit import persist_decision_audit, persist_risk_rejection
from core.decision_ledger import get_ledger, DecisionOutcome
from core.runtime.shutdown import is_shutdown_requested, interruptible_sleep
from core.state_persistence import save_engine_states
from core.event_bus import (
    EventState,
    TradeLifecycleLogger,
    emit_bias_events,
    emit_setup_events,
    log_runtime_exception,
    set_active_symbol,
)
from core.mt5_connection import (
    MT5_CONNECTED,
    MT5_DISCONNECTED,
    reconcile_state_sanity,
)
from core.runtime.mt5_health import MT5HealthManager
from core.runtime.cycle_guards import CycleGuards
from core.pipeline.observers import ObserverRegistry, ObserverContext
from core.runtime.health_monitor import HealthMonitor
from core.pipeline.cycle_report import emit_cycle_report
from core.pipeline.pipeline_diagnostics import emit_pipeline_diagnostics
from core.runtime.bar_provider import BarProvider
from core.evaluation.evaluation_runner import evaluate as run_evaluation, EvaluationContext
from core.runtime.pre_engine_gates import evaluate_pre_engine_gates
from risk.runtime_guard_chain import evaluate_runtime_guards
from core.runtime.decision_recorder import DecisionRecorder
from core.runtime.execution_context_builder import build_cycle_context
from execution.execution_orchestrator import ExecutionOrchestrator
from execution.post_execution_handler import emit_post_trade_success, emit_post_trade_failure
from core.runtime.engine_outcome_handler import handle_no_trade_outcome
from core.runtime.engine_execution_handler import prepare_execution
from core.runtime.scanner_init import initialize_symbol_states
from core.runtime.runtime_state_classifier import RuntimeStateClassifier
from core.runtime.tick_monitor import TickMonitor
from core.stale_monitor import StaleDataMonitor
from core.trade_management import (
    TradeManagementConfig,
    TradeStateManager,
)
from core.event_bus import emit_event

from data.mt5_data import MT5DataFeed
from execution.mt5_execution import MT5Execution
from risk.models import OrderIntent
from strategy.signals import Side

from core.runtime.runtime_utils import (
    _build_risk_manager,
    _build_trade_management_config,
)

from core.runtime.risk_event_emitter import emit_risk_guard_result
from core.event_stream import emit_feature_update
from core.trade_management.tick_driver import drive_tick

from typing import Callable
from risk.manager import RiskManager
from risk.drawdown_guard import DrawdownGuard
from risk.daily_loss_guard import DailyLossGuard
from risk.daily_trade_limit import DailyTradeLimitManager
from core.quiet_period_diagnostics import record_rejection
from risk.trade_cooldown import TradeCooldownManager

logger = logging.getLogger(__name__)   # NOT getlogger

# ─── MULTI-SYMBOL LIVE SCANNER ────────────────────────────────────────────────

@dataclass
class _LiveSymbolState:
    """Per-symbol state for the multi-symbol live scanner."""
    symbol: str
    feed: MT5DataFeed
    engine_state: EngineState
    event_state: EventState
    risk: RiskManager
    trade_manager: TradeStateManager | None
    stale_monitor: StaleDataMonitor
    tf_cache: "TimeframeCache | None" = None  # MTF: None when MTF_ENABLED=False
    market_context_builder: Any = None  # MarketContext: None when MARKET_CONTEXT_ENABLED=False
    last_closed_time: int | None = None
    iterations: int = 0


def run_live_scanner(
    *,
    symbols: list[str] | None = None,
    on_intent: Callable[[OrderIntent], None] | None = None,
    max_iterations: int | None = None,
) -> None:
    """
    Multi-symbol live scanner: processes all symbols in a single loop cycle.
    Each symbol is polled and evaluated independently per iteration.
    """
    symbol_list = symbols or getattr(config, "CANONICAL_SYMBOLS", None) or getattr(config, "SYMBOLS", [])
    execution = MT5Execution(magic=config.BOT_MAGIC)
    _exec_orchestrator = ExecutionOrchestrator(execution, config)

    # ─── SCANNER INITIALIZATION (extracted to core.runtime.scanner_init) ─
    states = initialize_symbol_states(symbols=symbols, execution=execution)

    if not states:
        logger.critical("[LIVE_SCANNER] no symbols initialized — aborting")
        return

    _mode = "PAPER" if getattr(execution, "DRY_RUN", True) else "LIVE"
    logger.info("[LIVE_SCANNER] ENGINE_START | mode=%s | symbols=%d", _mode, len(states))

    # ─── V10 CODE VERSION VERIFICATION ────────────────────────────────
    try:
        import os
        from datetime import datetime, timezone
        from core.v10 import strategy_engine as _se_module
        from core.v10 import entry_engine as _ee_module
        from core.v10 import opportunity_engine as _oe_module

        _se_path = os.path.abspath(_se_module.__file__)
        _ee_path = os.path.abspath(_ee_module.__file__)
        _oe_path = os.path.abspath(_oe_module.__file__)

        def _fmt_mtime(path):
            try:
                mt = os.path.getmtime(path)
                return datetime.fromtimestamp(mt, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            except Exception:
                return "UNKNOWN"

        print("=" * 50)
        print("V10 CODE VERSION")
        print("=" * 50)
        print(f"  strategy_engine: {_fmt_mtime(_se_path)}")
        print(f"    path: {_se_path}")
        print(f"  entry_engine:    {_fmt_mtime(_ee_path)}")
        print(f"    path: {_ee_path}")
        print(f"  opportunity_engine: {_fmt_mtime(_oe_path)}")
        print(f"    path: {_oe_path}")
        print(f"  H4 trend propagation: ACTIVE (all regimes)")
        print(f"  ENGINE_MODE: {getattr(config, 'ENGINE_MODE', '?')}")
        print("=" * 50)
        logger.info(
            "[V10 VERSION] strategy_engine=%s entry_engine=%s opportunity_engine=%s h4_propagation=ACTIVE",
            _fmt_mtime(_se_path), _fmt_mtime(_ee_path), _fmt_mtime(_oe_path),
        )
    except Exception as _ver_exc:
        logger.warning("[V10 VERSION] Could not determine code version: %s", _ver_exc)
        print(f"[V10 VERSION WARNING] Could not verify loaded code: {_ver_exc}")
    # ─── END V10 CODE VERSION ─────────────────────────────────────────

    # ─── V10 BUILD IDENTITY ───────────────────────────────────────────
    try:
        from core.runtime.build_identity import get_build_identity
        _build = get_build_identity()
        print("=" * 50)
        print("V10 BUILD ID")
        print("=" * 50)
        print(f"  git_commit: {_build.git_commit}")
        print(f"  branch:     {_build.branch}")
        print(f"  started_at: {_build.started_at}")
        print("=" * 50)
        logger.info(
            "[V10 BUILD] commit=%s branch=%s started=%s",
            _build.git_commit, _build.branch, _build.started_at,
        )
    except Exception as _build_exc:
        logger.warning("[V10 BUILD] Could not retrieve build identity: %s", _build_exc)
    # ─── END V10 BUILD IDENTITY ───────────────────────────────────────

    # ─── MACRO CONTEXT AVAILABILITY ───────────────────────────────────
    try:
        from core.timeframes.macro_alignment import MacroAlignment  # noqa: F401
        _macro_d1 = bool(getattr(config, "MTF_D1_ENABLED", True))
        _macro_w1 = bool(getattr(config, "MTF_W1_ENABLED", True))
        _macro_mn = bool(getattr(config, "MTF_MN_ENABLED", True))
        print("─" * 50)
        print("MACRO CONTEXT LAYER")
        print("─" * 50)
        print(f"  D1 (Daily):   {'ENABLED' if _macro_d1 else 'DISABLED'}")
        print(f"  W1 (Weekly):  {'ENABLED' if _macro_w1 else 'DISABLED'}")
        print(f"  MN (Monthly): {'ENABLED' if _macro_mn else 'DISABLED'}")
        print(f"  Module loaded: core.timeframes.macro_alignment")
        print("─" * 50)
        logger.info(
            "[MACRO CONTEXT] D1=%s W1=%s MN=%s",
            "ON" if _macro_d1 else "OFF",
            "ON" if _macro_w1 else "OFF",
            "ON" if _macro_mn else "OFF",
        )
    except Exception as _macro_exc:
        logger.warning("[MACRO CONTEXT] Module not available: %s", _macro_exc)
    # ─── END MACRO CONTEXT ────────────────────────────────────────────

    # ─── SYSTEM-LEVEL STATE ───────────────────────────────────────────
    _mt5_health = MT5HealthManager(states, config)
    mt5_state = _mt5_health.mt5_state  # Alias for observability references
    cycle_id = 0
    last_reconcile_time: float = time.time()
    reconcile_interval: float = float(getattr(config, "RECONCILIATION_INTERVAL_SECONDS", 300.0))
    _drawdown_guard = DrawdownGuard()
    _daily_loss_guard = DailyLossGuard()
    _daily_trade_limit = DailyTradeLimitManager()
    _trade_cooldown = TradeCooldownManager()
    # Share cooldown instance with TradeLifecycleLogger for consistent state
    TradeLifecycleLogger._shared_cooldown = _trade_cooldown
    _daily_reset = __import__("core.daily_reset", fromlist=["DailyResetCoordinator"]).DailyResetCoordinator()
    _cycle_guards = CycleGuards(config, _drawdown_guard, _daily_loss_guard, _daily_reset, _daily_trade_limit)
    _observers = ObserverRegistry()
    _health_monitor = HealthMonitor(n_symbols=len(states), config=config)
    _bar_provider = BarProvider(config)
    _tick_monitor = TickMonitor()

    # ─── DECISION LEDGER (per-cycle persistence) ──────────────────────
    _ledger = get_ledger()
    _decision_recorder = DecisionRecorder(_ledger)

    # ─── RUNTIME SESSION IDENTITY (observability — constant per process) ─
    import uuid as _session_uuid
    _runtime_session_id = _session_uuid.uuid4().hex[:12]
    logger.info("[RUNTIME_SESSION] session_id=%s", _runtime_session_id)
    # ─── END RUNTIME SESSION IDENTITY ─────────────────────────────────

    # ─── FILTER HIT MAP (cumulative rejection counters) ───────────────
    _filter_hits: dict[str, int] = {
        "market_context": 0,
        "structure/bias": 0,
        "pattern": 0,
        "confirmation": 0,
        "chop_filter": 0,
        "trend_filter": 0,
        "score_reject": 0,
        "trade_quality": 0,
        "stability_gate": 0,
        "session_guard": 0,
        "daily_trade_limit": 0,
        "cooldown": 0,
        "correlation": 0,
        "exposure": 0,
        "regime_guard": 0,
        "spread_guard": 0,
        "drawdown": 0,
        "daily_loss": 0,
        "trades_executed": 0,
    }

    # ─── DECISION FUNNEL (trace-derived hierarchical diagnostic) ──────
    from core.decision_trace import DecisionFunnel
    _decision_funnel = DecisionFunnel()

    # ─── SCORE PRESSURE TRACKER (confluence scoring audit) ────────────
    _score_tracker: dict[str, list] = {
        "scored_signals": [],       # (symbol, score, threshold, breakdown) — cleared each cycle
        "rejected_scores": [],      # (symbol, score, threshold) — cleared each cycle
        "passed_scores": [],        # (symbol, score, threshold) — cleared each cycle
    }

    # ─── HEARTBEAT HELPER (delegates to HealthMonitor) ────────────────
    def _write_heartbeat(status: str, cid: int, latency_ms: int, n_symbols: int, mt5_st: str) -> None:
        """Write heartbeat file. Delegates to HealthMonitor."""
        _health_monitor.write_heartbeat(status, cid, latency_ms, mt5_st)
    # ─── END HEARTBEAT HELPER ─────────────────────────────────────────

    # ─── SCANNER LOOP ─────────────────────────────────────────────────
    _runtime_classifier = RuntimeStateClassifier()
    try:
        while max_iterations is None or cycle_id < max_iterations:
            # Check for graceful shutdown request (central flag — no circular import)
            if is_shutdown_requested():
                logger.info("[SHUTDOWN] live scanner stopping — shutdown requested")
                break

            cycle_id += 1
            cycle_start = time.time()
            _cycle_had_trade = False

            # ─── LIFECYCLE TRACKING (cycle reporting migration) ────────
            # These replace _cycle_had_trade semantically but _cycle_had_trade
            # is preserved during migration for backward compatibility.
            _cycle_had_execute_decision = False   # Decision engine generated EXECUTE
            _cycle_had_execution_attempt = False  # Passed guards and entered execution layer
            _cycle_had_fill = False               # Broker confirmed successful fill
            # ─── END LIFECYCLE TRACKING INIT ──────────────────────────

            # ─── PERIODIC MEMORY MONITORING (every 100 cycles) ────────
            if cycle_id % 100 == 0:
                try:
                    import os as _os
                    import threading as _thr
                    _rss = 0
                    try:
                        # Linux: read /proc/self/status for VmRSS
                        with open("/proc/self/status") as _f:
                            for _line in _f:
                                if _line.startswith("VmRSS:"):
                                    _rss = int(_line.split()[1]) // 1024  # KB → MB
                                    break
                    except Exception:
                        pass
                    _threads = _thr.active_count()
                    _uptime_h = (time.time() - cycle_start) / 3600.0 if cycle_id == 100 else 0
                    print(f"[MEMORY] cycle={cycle_id} rss_mb={_rss} threads={_threads}")
                except Exception:
                    pass
            # ─── END MEMORY MONITORING ────────────────────────────────

            # ─── RUNTIME STATE CLASSIFIER (extracted) ─────────────────
            _runtime_classifier.check_gap(
                cycle_id=cycle_id, cycle_start=cycle_start,
                mt5_state=mt5_state, config=config,
            )
            # ─── END RUNTIME STATE CLASSIFIER ─────────────────────────

            # ─── SYSTEM HEALTH CHECK ──────────────────────────────────
            if not _mt5_health.check_and_reconnect():
                mt5_state = _mt5_health.mt5_state
                _write_heartbeat("mt5_disconnected", cycle_id, 0, len(states), "DISCONNECTED")
                interruptible_sleep(config.POLL_SECONDS)
                continue
            mt5_state = _mt5_health.mt5_state
            # ─── END SYSTEM HEALTH ────────────────────────────────────

            # ─── CYCLE GUARDS (R4 — extracted to core.runtime.cycle_guards) ─
            _cycle_permission = _cycle_guards.evaluate()
            _dd_result = _cycle_permission.drawdown_result
            _dl_result = _cycle_permission.daily_loss_result

            if not _cycle_permission.cycle_allowed:
                # Drawdown exceeded — skip all trading this cycle
                _write_heartbeat("drawdown_blocked", cycle_id, 0, len(states), mt5_state)
                interruptible_sleep(config.POLL_SECONDS)
                continue

            _daily_loss_blocked = _cycle_permission.daily_loss_blocked
            _kill_active = _cycle_permission.kill_switch_active
            # ─── END CYCLE GUARDS ─────────────────────────────────────

            # ─── PER-SYMBOL PROCESSING ────────────────────────────────
            _cycle_drops = []  # Collects (symbol, stage, reason) per cycle for summary (legacy)
            # ─── LIFECYCLE DROP TRACKING (Phase 3) ─────────────────────
            _cycle_decision_drops = []    # (symbol, stage, reason) — engine NO_TRADE
            _cycle_execution_drops = []   # (symbol, guard, reason) — guard chain blocks
            _cycle_broker_drops = []      # (symbol, retcode, reason) — broker rejections
            # ─── LIFECYCLE SYMBOL TRACKING (Phase 4) ───────────────────
            _cycle_execute_symbols = []   # Symbols with EXECUTE decisions
            _cycle_execution_symbols = [] # Symbols that reached broker execution
            _cycle_filled_symbols = []    # Symbols with confirmed fills
            _cycle_blocked_symbols = []   # Symbols blocked by guard chain
            _cycle_rejected_symbols = []  # Symbols rejected by broker
            # ─── END LIFECYCLE DROP TRACKING ────────────────────────────
            _cycle_candidates = []  # Collects engine results per symbol for opportunity ranking
            _execution_candidates = []  # Collects EXECUTE candidates for active ranking (Phase 2)
            # Clear per-cycle score tracker (prevent unbounded growth)
            _score_tracker["scored_signals"].clear()
            _score_tracker["rejected_scores"].clear()
            _score_tracker["passed_scores"].clear()
            # ─── PER-CYCLE NEW BAR TRACKER ─────────────────────────────
            _this_cycle_new_bars: list[str] = []
            _htf_context = None  # Safe default — overwritten in EXECUTE path if HTF available
            # ─── END TRACKER INIT ─────────────────────────────────────

            for sym_state in states:
              try:
                set_active_symbol(sym_state.symbol)

                # Fetch tick
                try:
                    bid, ask, tick_time = sym_state.feed.last_tick(sym_state.symbol)
                except RuntimeError:
                    logger.info("[LIVE_SCANNER] %s tick fetch failed — skipping", sym_state.symbol)
                    continue

                # ─── TICK FRESHNESS (extracted to core.runtime.tick_monitor) ─
                _tick_result = _tick_monitor.evaluate(
                    symbol=sym_state.symbol,
                    stale_monitor=sym_state.stale_monitor,
                    tick_time=tick_time,
                )
                if not _tick_result.valid:
                    continue
                # ─── END TICK FRESHNESS ────────────────────────────────

                # Trade management tick update (paused when kill switch active)
                drive_tick(sym_state.trade_manager, sym_state.symbol, bid, ask, _kill_active)

                # ─── BAR PROVISION (R5+R21 — extracted to core.runtime.bar_provider) ─
                _bar_result = _bar_provider.fetch_bar(sym_state)
                if _bar_result is None:
                    continue  # Symbol skipped (fetch fail, stale, duplicate, etc.)
                candles = _bar_result.candles
                closed_i = _bar_result.closed_i
                closed_time = _bar_result.closed_time
                _closed_time_utc = _bar_result.closed_time_utc
                _feed_state = _bar_result.feed_state
                _this_cycle_new_bars.append(sym_state.symbol)
                # ─── END BAR PROVISION ────────────────────────────────────

                # ─── PER-CYCLE EXECUTION CONTEXT (R12 — extracted) ─────
                _cor_id_cycle = build_cycle_context(
                    cycle_id=cycle_id,
                    cycle_start=cycle_start,
                    sym_state=sym_state,
                    closed_time=closed_time,
                    bid=bid,
                    ask=ask,
                    tick_time=tick_time,
                    feed_state=_feed_state,
                    dd_result=_dd_result,
                    dl_result=_dl_result,
                )
                # ─── END PER-CYCLE EXECUTION CONTEXT ──────────────────

                # ─── DECISION STATE (R6 — managed by DecisionRecorder) ─
                # Retired runtime-composite observation id (remediation).
                # Populated with the CANONICAL opportunity root after the
                # engine selects the primary pattern; never persisted as a
                # separate competing identity.
                _observation_id_cycle = ""
                _entity_id_cycle = f"{sym_state.symbol}_{int(closed_time)}"
                # Canonical lineage freshness guard (Phase 1 data-capture):
                # reset per symbol/bar BEFORE any downstream consumer. Without
                # this, "_canonical_opp_id" stays bound from a previous
                # symbol/bar and the historical 'in dir()' guards would pass a
                # STALE canonical lineage into shadow opens / execution records.
                # An empty value here means "lineage not established yet" and is
                # the ONLY legitimate reason for an empty canonical ID
                # downstream (pre-pattern gate blocks).
                _canonical_opp_id = ""
                _cycle_decision = _decision_recorder.init_cycle(
                    symbol=sym_state.symbol,
                    cycle_id=cycle_id,
                    regime=sym_state.engine_state.regime_state or "unknown",
                    context_snapshot_id=_cor_id_cycle,
                    drawdown_pct=getattr(_dd_result, "current_drawdown_pct", 0.0) or 0.0,
                    daily_loss_pct=getattr(_dl_result, "current_loss_pct", 0.0) or 0.0,
                )

                def _finalize_decision() -> None:
                    """Delegate to DecisionRecorder.finalize(). Idempotent."""
                    _decision_recorder.finalize(cycle_start=cycle_start)

                # ─── PRE-ENGINE GATES (R7 — extracted to core.runtime.pre_engine_gates) ─
                _gate_result = evaluate_pre_engine_gates(
                    kill_active=_kill_active,
                    daily_loss_blocked=_daily_loss_blocked,
                    candles=candles,
                    closed_i=closed_i,
                    symbol=sym_state.symbol,
                    cycle_id=cycle_id,
                    closed_time=closed_time,
                )
                if not _gate_result.allowed:
                    _cycle_decision["decision"] = getattr(DecisionOutcome, _gate_result.block_outcome, DecisionOutcome.NO_TRADE)
                    _cycle_decision["reason"] = _gate_result.block_reason
                    if _gate_result.block_risk_flag:
                        _cycle_decision["risk_flag"] = _gate_result.block_risk_flag
                    if _gate_result.block_session_state:
                        _cycle_decision["session_state"] = _gate_result.block_session_state
                    _finalize_decision()
                    # Paper engine: advance pending signals on pattern reject
                    if _gate_result.block_outcome == "PATTERN_REJECT":
                        try:
                            from core.pipeline.paper_outcome_engine import get_paper_engine
                            get_paper_engine().evaluate_pending(sym_state.symbol, candles[closed_i].high, candles[closed_i].low, candles[closed_i].close)
                        except Exception:
                            pass
                    continue
                _raw_patterns = _gate_result.raw_patterns
                # ─── END PRE-ENGINE GATES ─────────────────────────────────

                # ─── ENGINE A (sole production authority) ──────────────
                _new_engine_intent = None  # OrderIntent from new engine (if EXECUTE)
                _new_engine_score = 0.0

                # ─── HTF CONTEXT + MARKET CONTEXT (built BEFORE engine) ───
                # Both V10 and legacy engine consume these. Must be fresh per symbol per cycle.
                _new_engine_htf = None
                if sym_state.tf_cache is not None:
                    try:
                        sym_state.tf_cache.update_if_needed(
                            current_time_s=float(closed_time),
                            current_price=bid,
                        )
                        _new_engine_htf = sym_state.tf_cache.get_htf_context(current_price=bid)
                    except Exception:
                        pass  # Proceed without HTF — neutral scores

                _market_context = None
                if sym_state.market_context_builder is not None:
                    try:
                        _market_context = sym_state.market_context_builder.build(
                            htf_context=_new_engine_htf,
                            candles=candles,
                            closed_i=closed_i,
                            engine_state=sym_state.engine_state,
                            cycle_id=cycle_id,
                            current_time_s=float(closed_time),
                            current_price=bid,
                        )
                    except Exception:
                        pass  # Market context failure must never affect trading
                # ─── END HTF + MARKET CONTEXT ─────────────────────────────

                # ─── V10 ENGINE MODE CHECK ────────────────────────────
                _engine_mode = getattr(config, "ENGINE_MODE", "LEGACY")
                if _engine_mode == "V10":
                    try:
                        from core.v10.scanner_adapter import run_v10_cycle
                        # Log context availability for diagnostics
                        _v10_has_htf = _new_engine_htf is not None
                        _v10_has_mc = _market_context is not None
                        _v10_range_pos = 0.0
                        _v10_m15_hi = 0.0
                        _v10_m15_lo = 0.0
                        if _market_context is not None:
                            _m15_s = getattr(_market_context, "m15", None)
                            if _m15_s:
                                _v10_m15_hi = getattr(_m15_s, "swing_high", 0.0) or 0.0
                                _v10_m15_lo = getattr(_m15_s, "swing_low", 0.0) or 0.0
                        logger.info(
                            "[V10 CONTEXT] symbol=%s htf=%s mc=%s m15_hi=%.5f m15_lo=%.5f",
                            sym_state.symbol, _v10_has_htf, _v10_has_mc, _v10_m15_hi, _v10_m15_lo,
                        )
                        _new_result = run_v10_cycle(
                            symbol=sym_state.symbol,
                            candles=candles,
                            closed_i=closed_i,
                            bid=bid,
                            ask=ask,
                            htf_context=_new_engine_htf,
                            market_context=_market_context,
                            engine_state=sym_state.engine_state,
                            config=config,
                            cycle_id=cycle_id,
                        )
                        _new_engine_score = _new_result.get("score", 0.0)
                        print(f"[V10 ENGINE] symbol={sym_state.symbol} action={_new_result['action']} score={_new_engine_score:.3f} reason={_new_result.get('reason', '')}")
                        # ─── LIVE MARKET STATE (snapshot — overwrite per symbol) ──
                        try:
                            from core.live_market_state import update_live_market_state, read_live_market_state, should_notify_discord
                            _prev_market_state = read_live_market_state(sym_state.symbol)
                            update_live_market_state(
                                symbol=sym_state.symbol,
                                cycle_id=cycle_id,
                                bar_time=int(closed_time),
                                v10_pipeline_result=_new_result.get("v10_pipeline_result"),
                                engine_result=_new_result,
                            )
                            # Only trigger Discord when meaningful state changed
                            _new_market_state = read_live_market_state(sym_state.symbol)
                            if should_notify_discord(_prev_market_state, _new_market_state):
                                try:
                                    _dl = getattr(config, "_discord_logger", None)
                                    if _dl is not None:
                                        _dl.event("MARKET_CONTEXT", {"symbol": sym_state.symbol})
                                except Exception:
                                    pass
                        except Exception:
                            pass  # Live state failure must never affect trading
                        # ─── END LIVE MARKET STATE ────────────────────────────────
                    except Exception as _v10_exc:
                        logger.warning("[V10] fallback to legacy: %s", _v10_exc)
                        _engine_mode = "LEGACY"  # Fall through to legacy below
                # ─── END V10 ENGINE MODE ──────────────────────────────
                if _engine_mode != "V10":
                    # LEGACY ENGINE PATH — only runs when ENGINE_MODE != "V10"
                    logger.debug("[ENGINE_MODE] Legacy engine active for %s", sym_state.symbol)
                else:
                    logger.debug("[ENGINE_MODE] V10 active — legacy engine skipped for %s", sym_state.symbol)
                # ─── SHADOW OPPORTUNITY LAYER (Phase 2A — observation only) ─
                # Create Opportunity objects for ALL detected patterns.
                # Purely observational: never affects trading decisions.
                _cycle_opportunities: list = []
                try:
                    from core.opportunity.factory import create_opportunity
                    from core.opportunity.persistence import persist_opportunity_batch

                    _sibling_names = [s.pattern for s in _raw_patterns]
                    # Classify session from bar time (UTC hour)
                    _opp_session = ""
                    try:
                        from datetime import datetime, timezone
                        _opp_hour = datetime.fromtimestamp(float(closed_time), tz=timezone.utc).hour
                        if 0 <= _opp_hour < 7:
                            _opp_session = "ASIA"
                        elif 7 <= _opp_hour < 12:
                            _opp_session = "LONDON"
                        elif 12 <= _opp_hour < 16:
                            _opp_session = "NEW_YORK"
                        elif 7 <= _opp_hour < 16:
                            _opp_session = "OVERLAP"
                        else:
                            _opp_session = "OFF_SESSION"
                    except Exception:
                        pass
                    for _sig in _raw_patterns:
                        _opp = create_opportunity(
                            signal=_sig,
                            symbol=sym_state.symbol,
                            cycle_id=cycle_id,
                            candles=candles,
                            htf_context=_new_engine_htf,
                            engine_state=sym_state.engine_state,
                            sibling_patterns=[p for p in _sibling_names if p != _sig.pattern],
                            bid=bid,
                            ask=ask,
                            session_state=_opp_session,
                            runtime_session_id=_runtime_session_id,
                        )
                        _cycle_opportunities.append(_opp)
                    # Persist all detected opportunities immediately
                    persist_opportunity_batch(_cycle_opportunities)
                except Exception:
                    pass  # Opportunity layer failure must NEVER affect trading
                # ─── END SHADOW OPPORTUNITY LAYER ─────────────────────────
                try:
                    from core.pipeline.new_engine import run_new_engine
                    if _engine_mode == "V10":
                        # V10 already computed _new_result above — skip legacy engine
                        pass
                    else:
                        _new_result = run_new_engine(
                            candles=candles,
                            closed_i=closed_i,
                            symbol=sym_state.symbol,
                            bid=bid,
                            ask=ask,
                            engine_state=sym_state.engine_state,
                            config=config,
                            detected_patterns=_raw_patterns,
                            risk_manager=sym_state.risk,
                            htf_context=_new_engine_htf,
                            cycle_id=cycle_id,
                            market_phase=getattr(_market_context, "phase", None).value if _market_context and hasattr(getattr(_market_context, "phase", None), "value") else None,
                            market_phase_confidence=getattr(_market_context, "phase_confidence", 0.0) if _market_context else 0.0,
                        )
                        _new_engine_score = _new_result.get("score", 0.0)
                        print(f"[NEW ENGINE] symbol={sym_state.symbol} action={_new_result['action']} score={_new_engine_score:.3f} reason={_new_result.get('reason', '')}")
                    # ─── BIAS FSM UPDATE (3.6 — state authority) ──────
                    # Evolves bias state on EVERY bar (not just patterns).
                    # Must run BEFORE execution decision but AFTER scoring.
                    try:
                        from core.pipeline.bias_fsm import update_bias_fsm
                        _best_pat = _new_result.get("_best_pattern")  # May be None
                        _fsm_log = update_bias_fsm(
                            engine_state=sym_state.engine_state,
                            candles=candles,
                            closed_i=closed_i,
                            pattern=_best_pat,
                            current_time_s=float(closed_time),
                        )
                        if _fsm_log.get("transition"):
                            print(f"[BIAS FSM] {sym_state.symbol} | {_fsm_log['transition']} | strength={_fsm_log['new_strength']:.1f} | bias={_fsm_log['new_bias']}")
                    except Exception:
                        pass  # FSM failure must never block execution
                    # ─── END BIAS FSM ─────────────────────────────────
                    # Record candidate for opportunity ranking (passive observation)
                    _new_result["symbol"] = sym_state.symbol
                    _new_result["cycle_id"] = cycle_id
                    _cycle_candidates.append(_new_result)

                    # ─── CANONICAL LINEAGE (remediation) ──────────────────
                    # Mint THE canonical opportunity lineage root from market
                    # data only (symbol, bar close, primary pattern). Shared
                    # by live AND shadow branches. Empty when no pattern —
                    # i.e. lineage not established (pre-engine blocks).
                    _canonical_opp_id = ""
                    try:
                        from core.identity.canonical import make_canonical_opportunity_id
                        # Mint into a throwaway local first: _canonical_opp_id
                        # itself is only bound AFTER the whole mint+fallback
                        # sequence succeeds, so no read of the scoped variable
                        # can ever precede the except-branch freshness reset.
                        _minted_canonical_id = make_canonical_opportunity_id(
                            symbol=sym_state.symbol,
                            bar_time=closed_time,
                            pattern=str(_new_result.get("pattern", "") or ""),
                        )
                        # ── Population-B provenance fallback ──────────────
                        # The V10 decision result's `pattern` is the STRATEGY
                        # FAMILY, which is empty when no strategy matched even
                        # though a genuine pattern WAS detected at the
                        # opportunity layer (same cycle / same symbol / same
                        # bar — no cross-symbol or cross-bar exposure). Propagate
                        # the primary detected signal's canonical root (same
                        # approved authority, same inputs create_opportunity
                        # uses) so detected-but-rejected opportunities retain
                        # their decision-level lineage. Never fires for
                        # Population A (no detected signal → _raw_patterns
                        # empty → root stays "").
                        if not _minted_canonical_id and _raw_patterns:
                            _prim_sig = _raw_patterns[0]
                            _prim_pattern = str(getattr(_prim_sig, "pattern", "") or "")
                            if _prim_pattern:
                                _minted_canonical_id = make_canonical_opportunity_id(
                                    symbol=sym_state.symbol,
                                    bar_time=getattr(_prim_sig, "bar_time", None) or closed_time,
                                    pattern=_prim_pattern,
                                )
                        _canonical_opp_id = _minted_canonical_id
                    except Exception:
                        _canonical_opp_id = ""
                    if _canonical_opp_id:
                        _cycle_decision["canonical_opportunity_id"] = _canonical_opp_id
                    # Retire the composite observation id: every downstream
                    # identity slot now carries THE canonical lineage root.
                    _observation_id_cycle = _canonical_opp_id
                    _new_result["canonical_opportunity_id"] = _canonical_opp_id
                    # V10 research payload travels inside the authoritative
                    # DecisionRecorder ledger row — never a second row.
                    if not _cycle_decision.get("v10"):
                        _cycle_decision["v10"] = _new_result.get("v10_payload")
                    # ─── END CANONICAL LINEAGE ────────────────────────────

                    # ─── ASSESSMENT + HORIZON INTELLIGENCE (Phase 2B+4B) ─────
                    # Build assessment, enrich with horizon classification,
                    # then persist ONCE with all data attached.
                    # Never affects trading decisions.
                    _assessment_record = None
                    try:
                        from core.assessment.builder import build_assessment
                        _assessment_record = build_assessment(
                            engine_result=_new_result,
                            symbol=sym_state.symbol,
                            cycle_id=cycle_id,
                            bar_time=int(closed_time),
                            bid=bid,
                            ask=ask,
                            runtime_session_id=_runtime_session_id,
                        )
                    except Exception:
                        pass  # Assessment build failure must NEVER affect trading

                    # Horizon classification (runs BEFORE persistence)
                    try:
                        from core.horizon.horizon_classifier import classify_horizons
                        _horizon_result = classify_horizons(
                            strategy_type=_new_result.get("strategy", "") or "",
                            strategy_confidence=float(_new_result.get("strategy_confidence", 0.0) or 0.0),
                            h4_regime=_new_result.get("activation_regime", "") or "",
                            h4_regime_confidence=float(_new_result.get("activation_regime_confidence", 0.0) or 0.0),
                            h1_direction="",
                            h1_bos_confirmed=bool(_new_result.get("swing_break_confirmed", False)),
                            htf_alignment=float((_new_result.get("components") or {}).get("htf_alignment", 0.0)),
                            h4_alignment=float((_new_result.get("components") or {}).get("h4_alignment", 0.0)),
                            market_quality=float((_new_result.get("components") or {}).get("market_quality", 0.0)),
                            chop_clarity=float((_new_result.get("components") or {}).get("chop_clarity", 0.0)),
                            volatility_quality=float((_new_result.get("components") or {}).get("volatility_quality", 0.0)),
                            pattern=_new_result.get("pattern", "") or "",
                            direction=_new_result.get("side", "") or "",
                        )
                        # Attach horizon data to assessment BEFORE persistence
                        if _assessment_record is not None:
                            _assessment_record.evidence_contributions.append({
                                "_horizon_classification": _horizon_result.to_dict(),
                            })
                        # Log when multiple horizons are eligible
                        _eligible = _horizon_result.eligible_horizons
                        if len(_eligible) > 1:
                            logger.info(
                                "[HORIZON] %s | eligible=%s | best=%s",
                                sym_state.symbol, _eligible, _horizon_result.best_horizon,
                            )
                    except Exception:
                        pass  # Horizon intelligence must NEVER affect trading

                    # Persist assessment WITH horizon data attached
                    try:
                        if _assessment_record is not None:
                            from core.assessment.persistence import persist_assessment
                            persist_assessment(_assessment_record)
                    except Exception:
                        pass  # Assessment persistence must NEVER affect trading
                    # ─── END ASSESSMENT + HORIZON ──────────────────────────

                    # ─── HORIZON SHADOW TRADES (Phase 4C.3 — ALL opportunities) ─
                    # Create shadow trades for each eligible horizon, regardless of
                    # whether the engine approved execution. This enables research
                    # comparing rejected opportunities across horizons.
                    try:
                        if getattr(config, "SHADOW_RUNTIME_V2_ENABLED", False):
                            # ─── NEW Shadow Runtime path (gated) ──────────
                            # Pre-verdict branch into the NEW per-opportunity
                            # Shadow lineage. Fire-and-forget: any failure is
                            # contained by this block's existing except.
                            from core.shadow.integration import (
                                ShadowV2Handled,
                                handle_live_opportunity_shadow,
                            )

                            handle_live_opportunity_shadow(
                                symbol=sym_state.symbol,
                                cycle_id=cycle_id,
                                closed_time=int(closed_time),
                                candles=candles,
                                closed_i=closed_i,
                                bid=bid,
                                ask=ask,
                                htf_context=_new_engine_htf,
                                new_result=_new_result,
                                horizon_result=_horizon_result if "_horizon_result" in dir() else None,
                                canonical_opportunity_id=_canonical_opp_id,
                                entity_id=_new_result.get("entity_id", ""),
                                # Phase 3 Step 10-B: forward upstream regime/
                                # phase facts from this cycle's assessment when
                                # produced, else engine payload. Empty = absent.
                                regime=(
                                    str(getattr(_assessment_record, "regime", "") or "")
                                    if "_assessment_record" in dir() and _assessment_record is not None
                                    else ""
                                ),
                                h4_regime=(
                                    str(getattr(_assessment_record, "h4_regime", "") or "")
                                    if "_assessment_record" in dir() and _assessment_record is not None
                                    else ""
                                ),
                                market_phase=(
                                    str(getattr(_assessment_record, "market_phase", "") or "")
                                    if "_assessment_record" in dir() and _assessment_record is not None
                                    else ""
                                ),
                            )
                            raise ShadowV2Handled()  # skip legacy writer; caught below

                        from core.horizon.horizon_trade_builder import build_all_horizon_trades
                        from core.shadow_trades import get_shadow_engine

                        _engine_action_for_shadow = _new_result.get("action", "NO_TRADE")
                        _eligible_for_shadow = getattr(_horizon_result, "eligible_horizons", []) if "_horizon_result" in dir() and _horizon_result else []

                        if _eligible_for_shadow and _new_result.get("pattern"):
                            # Gather structure data
                            _sh_m15_support = None
                            _sh_m15_resistance = None
                            _sh_h1_high = None
                            _sh_h1_low = None
                            if _new_engine_htf is not None:
                                _sh_m15 = getattr(_new_engine_htf, "structure", None)
                                if _sh_m15 is not None:
                                    _sh_m15_support = getattr(_sh_m15, "nearest_support", None)
                                    _sh_m15_resistance = getattr(_sh_m15, "nearest_resistance", None)
                                _sh_h1 = getattr(_new_engine_htf, "bias", None)
                                if _sh_h1 is not None:
                                    _sh_h1_high = getattr(_sh_h1, "last_swing_high", None)
                                    _sh_h1_low = getattr(_sh_h1, "last_swing_low", None)

                            # Get direction from assessment (always populated),
                            # fallback to engine result "side" field (EXECUTE only)
                            _assessment_obj = _new_result.get("assessment")
                            _sh_direction = (
                                (getattr(_assessment_obj, "side", "") if _assessment_obj else "")
                                or _new_result.get("side", "")
                                or ""
                            )
                            _sh_entry = ask if _sh_direction == "BUY" else bid
                            _sh_m5_high = candles[closed_i].high if candles and closed_i < len(candles) else None
                            _sh_m5_low = candles[closed_i].low if candles and closed_i < len(candles) else None

                            _sh_trades = build_all_horizon_trades(
                                eligible_horizons=_eligible_for_shadow,
                                symbol=sym_state.symbol,
                                direction=_sh_direction,
                                entry_price=_sh_entry,
                                m5_candle_high=_sh_m5_high,
                                m5_candle_low=_sh_m5_low,
                                m15_nearest_support=_sh_m15_support,
                                m15_nearest_resistance=_sh_m15_resistance,
                                h1_last_swing_high=_sh_h1_high,
                                h1_last_swing_low=_sh_h1_low,
                            )

                            _sh_engine = get_shadow_engine()
                            # Extract H1 bias for shadow trade lineage (safe fallback)
                            _sh_h1_bias_str = ""
                            try:
                                if _new_engine_htf and getattr(_new_engine_htf, "bias", None):
                                    _dir = getattr(_new_engine_htf.bias, "direction", None)
                                    if _dir and hasattr(_dir, "value"):
                                        _sh_h1_bias_str = _dir.value
                            except Exception:
                                pass

                            for _sh_t in _sh_trades:
                                _sh_trade_id = f"hshadow_{cycle_id}_{sym_state.symbol}_{_sh_t.horizon}"
                                # Determine horizon selection status relative to V10's choice
                                _v10_hz_for_shadow = ""
                                _v10_rej_for_shadow = ""
                                _v10_act_for_shadow = _engine_action_for_shadow
                                try:
                                    _pr_shadow = _new_result.get("v10_pipeline_result")
                                    if _pr_shadow and hasattr(_pr_shadow, "horizon"):
                                        _v10_hz_for_shadow = getattr(_pr_shadow.horizon, "horizon_type", "") or ""
                                    if _pr_shadow:
                                        _v10_rej_for_shadow = getattr(_pr_shadow, "rejection_stage", "") or ""
                                except Exception:
                                    pass
                                _hz_status = "SELECTED" if _sh_t.horizon == _v10_hz_for_shadow else "ALTERNATIVE"

                                _sh_engine.open_trade(
                                    trade_id=_sh_trade_id,
                                    cycle_id=cycle_id,
                                    symbol=sym_state.symbol,
                                    direction=_sh_t.direction,
                                    entry_price=_sh_t.entry,
                                    stop_loss=_sh_t.stop_loss,
                                    take_profit=_sh_t.take_profit,
                                    entry_time=float(closed_time),
                                    strategy=_new_result.get("strategy", "") or "",
                                    pattern=_new_result.get("pattern", ""),
                                    score=float(_new_result.get("score", 0.0) or 0.0),
                                    # Remediation Stage 6: the canonical opportunity
                                    # lineage root replaces the retired HORIZON-* label.
                                    # No COR-* is manufactured here — correlation_id
                                    # is technical tracing only and stays empty on
                                    # this pre-verdict shadow path.
                                    correlation_id="",
                                    canonical_opportunity_id=_canonical_opp_id,
                                    entity_id=_new_result.get("entity_id", ""),
                                    regime=_new_result.get("activation_regime", "") or "",
                                    h4_regime=_new_result.get("activation_regime", "") or "",
                                    h1_bias=_sh_h1_bias_str,
                                    market_phase=_new_result.get("market_phase", "") or "",
                                    market_phase_confidence=float(_new_result.get("market_phase_confidence", 0.0) or 0.0),
                                    trade_horizon=_sh_t.horizon,
                                    # Shadow lineage contract
                                    shadow_type="HORIZON_ALTERNATIVE",
                                    v10_selected_horizon=_v10_hz_for_shadow,
                                    horizon_selection_status=_hz_status,
                                    evaluated_horizon=_sh_t.horizon,
                                    horizon_geometry_source="STRUCTURE_BASED",
                                    v10_rejection_stage=_v10_rej_for_shadow,
                                    v10_action=_v10_act_for_shadow,
                                )
                                logger.info(
                                    "[HORIZON_SHADOW] symbol=%s horizon=%s direction=%s "
                                    "entry=%.5f sl=%.5f tp=%.5f rr=%.1f decision=%s created=true",
                                    sym_state.symbol, _sh_t.horizon, _sh_t.direction,
                                    _sh_t.entry, _sh_t.stop_loss, _sh_t.take_profit,
                                    _sh_t.rr, _engine_action_for_shadow,
                                )
                    except Exception:
                        pass  # Horizon shadow creation must NEVER affect trading
                    # ─── END HORIZON SHADOW TRADES ─────────────────────────

                    # ─── OPPORTUNITY LIFECYCLE UPDATE (Phase 2A shadow) ────
                    # Enrich opportunities with engine assessment data and
                    # mark lifecycle transitions. Never affects trading.
                    try:
                        from core.opportunity.opportunity import OpportunityState
                        from core.opportunity.persistence import persist_opportunity_batch as _opp_persist_batch

                        _engine_action = _new_result.get("action", "NO_TRADE")
                        _engine_reason = _new_result.get("reason", "")
                        _engine_pattern = _new_result.get("pattern", "")
                        _engine_components = _new_result.get("components", {})
                        _engine_score = _new_result.get("score", 0.0)
                        _engine_strategy = _new_result.get("strategy", "")
                        _engine_strategy_conf = _new_result.get("strategy_confidence", 0.0)
                        _engine_market_state = _new_result.get("market_state", "")
                        _engine_cor_id = _new_result.get("correlation_id", "")
                        # Extract observation_id from V10 pipeline for Discord traceability
                        _v10_obs_id = ""
                        try:
                            _v10_pr = _new_result.get("v10_pipeline_result")
                            if _v10_pr:
                                _v10_obs_id = _v10_pr.opportunity.observation_id or ""
                        except Exception:
                            pass

                        _updated_opps = []
                        # V10 evaluates the BAR as a unit (not per-pattern).
                        # Select the highest-confidence pattern as the "primary" opportunity.
                        _primary_opp = max(_cycle_opportunities, key=lambda o: o.pattern_confidence) if _cycle_opportunities else None

                        for _opp in _cycle_opportunities:
                            if _opp is _primary_opp:
                                # ─── DISCORD: Emit DETECTED for primary (if meaningful) ─
                                _v10_opp_state = ""
                                try:
                                    _v10_pr = _new_result.get("v10_pipeline_result")
                                    if _v10_pr:
                                        _v10_opp_state = _v10_pr.opportunity.opportunity_state
                                except Exception:
                                    pass
                                if _v10_opp_state in ("WATCHING", "VALID"):
                                    try:
                                        _dl = getattr(config, "_discord_logger", None)
                                        if _dl is not None:
                                            _dl.event("OPPORTUNITY_LIFECYCLE", {
                                                "opportunity_id": _opp.opportunity_id,
                                                "entity_id": _opp.entity_id,
                                                "observation_id": _v10_obs_id,
                                                "cycle_id": _opp.cycle_id,
                                                "symbol": _opp.symbol,
                                                "lifecycle_state": "DETECTED",
                                                "pattern": _opp.pattern,
                                                "pattern_confidence": _opp.pattern_confidence,
                                                "direction": _opp.direction,
                                                "session_at_detection": _opp.session_at_detection,
                                                "h4_regime": _opp.h4_regime,
                                                "h1_direction": _opp.h1_direction,
                                                "strategy_classification": "",
                                                "strategy_confidence": 0.0,
                                                "overall_score": 0.0,
                                                "rejection_reason": "",
                                                "rejection_stage": "",
                                                "correlation_id": "",
                                            })
                                    except Exception:
                                        pass
                                # ─── END DETECTED EMISSION ────────────────────────

                                # Primary opportunity: enrich with V10 pipeline results
                                _opp.evidence_scores = dict(_engine_components) if _engine_components else {}
                                _opp.overall_score = float(_engine_score)
                                _opp.strategy_classification = str(_engine_strategy) if _engine_strategy else ""
                                _opp.strategy_confidence = float(_engine_strategy_conf)
                                _opp.correlation_id = str(_engine_cor_id) if _engine_cor_id else ""
                                # Additive Data/Shadow derivation (FIXED DECISION §5.2).
                                # Observational only — never influences EXECUTE/NO_TRADE/REJECT,
                                # risk, guards, or execution. Derived from already-produced
                                # values only (no re-identification / no re-classification):
                                #   verdict  -> _v10_opp_state  (assess_opportunity, L874)
                                #   eligible -> _eligible_for_shadow (classify_horizons, L727)
                                from core.v10.identification_condition import (
                                    compute_passed_identification_condition,
                                )
                                _opp.passed_identification_condition = (
                                    compute_passed_identification_condition(
                                        identification_verdict=_v10_opp_state,
                                        eligible_horizons=(
                                            _eligible_for_shadow
                                            if "_eligible_for_shadow" in dir() and _eligible_for_shadow
                                            else []
                                        ),
                                    )
                                )

                                if _engine_action == "EXECUTE":
                                    _opp.transition(OpportunityState.ASSESSED)
                                    # Will be updated to EXECUTED after actual fill (below)
                                else:
                                    _opp.transition(
                                        OpportunityState.REJECTED,
                                        rejection_reason=_engine_reason,
                                        rejection_stage="decision_engine",
                                    )
                            else:
                                # Non-primary patterns: rejected at pattern selection
                                _opp.transition(
                                    OpportunityState.REJECTED,
                                    rejection_reason="pattern_not_selected",
                                    rejection_stage="pattern_selection",
                                )
                            _updated_opps.append(_opp)

                        # Persist updated lifecycle states
                        _opp_persist_batch(_updated_opps)

                        # ─── DISCORD: Emit lifecycle event for primary opportunity ───
                        if _primary_opp is not None:
                            try:
                                _dl = getattr(config, "_discord_logger", None)
                                if _dl is not None:
                                    _dl.event("OPPORTUNITY_LIFECYCLE", {
                                        "opportunity_id": _primary_opp.opportunity_id,
                                        "entity_id": _primary_opp.entity_id,
                                        "observation_id": _v10_obs_id,
                                        "cycle_id": _primary_opp.cycle_id,
                                        "symbol": _primary_opp.symbol,
                                        "lifecycle_state": _primary_opp.state,
                                        "pattern": _primary_opp.pattern,
                                        "pattern_confidence": _primary_opp.pattern_confidence,
                                        "direction": _primary_opp.direction,
                                        "session_at_detection": _primary_opp.session_at_detection,
                                        "h4_regime": _primary_opp.h4_regime,
                                        "h1_direction": _primary_opp.h1_direction,
                                        "strategy_classification": _primary_opp.strategy_classification,
                                        "strategy_confidence": _primary_opp.strategy_confidence,
                                        "overall_score": _primary_opp.overall_score,
                                        "rejection_reason": _primary_opp.rejection_reason,
                                        "rejection_stage": _primary_opp.rejection_stage,
                                        "correlation_id": _primary_opp.correlation_id,
                                    })
                            except Exception:
                                pass
                        # ─── END DISCORD LIFECYCLE ────────────────────────────────
                    except Exception:
                        pass  # Opportunity layer must NEVER affect trading
                    # ─── END OPPORTUNITY LIFECYCLE UPDATE ──────────────────
                    # Event observer: emit only on meaningful state change (passive)
                    # Forensic logger, entity tracker, visibility layer,
                    # shadow rooms, decision trace — dispatched via ObserverRegistry
                    _observers.notify_all(ObserverContext(
                        symbol=sym_state.symbol,
                        cycle_id=cycle_id,
                        bar_time=float(closed_time),
                        engine_result=_new_result,
                        engine_state=sym_state.engine_state,
                        candles=candles,
                        closed_i=closed_i,
                        bid=bid,
                        ask=ask,
                        config=config,
                        detected_patterns=_raw_patterns,
                        risk_manager=sym_state.risk,
                        htf_context=_new_engine_htf,
                        runtime_session_id=_runtime_session_id,
                        decision_funnel=_decision_funnel,
                        market_context=_market_context,
                    ))
                    # ─── END OBSERVER DISPATCH ────────────────────────────────
                    if _new_result["action"] == "NO_TRADE":
                        # ─── LIFECYCLE: Decision drop ─────────────────────
                        _cycle_decision_drops.append((sym_state.symbol, "V10", _new_result.get("reason", "?")))
                        handle_no_trade_outcome(
                            new_result=_new_result,
                            new_engine_score=_new_engine_score,
                            symbol=sym_state.symbol,
                            engine_state=sym_state.engine_state,
                            risk=sym_state.risk,
                            cycle_id=cycle_id,
                            closed_time=closed_time,
                            candles=candles,
                            closed_i=closed_i,
                            bid=bid,
                            ask=ask,
                            config=config,
                            runtime_session_id=_runtime_session_id,
                            cycle_decision=_cycle_decision,
                            cycle_drops=_cycle_drops,
                            filter_hits=_filter_hits,
                            observation_id=_observation_id_cycle,
                            correlation_id=_cor_id_cycle,
                        )
                        # NOTE: The legacy V10_PRIMARY shadow for NO_TRADE has been
                        # removed (Phase 1I-C). The canonical Horizon Shadow lineage
                        # above already opens SCALP/INTRADAY/EXTENDED shadows that
                        # record the authoritative live verdict via v10_action.
                        _finalize_decision()
                        continue
                    # EXECUTE path — new engine is the authority
                    # ─── EXEC TRACE: V10 pipeline approved execution ──
                    try:
                        from core.runtime.execution_trace import log_execution_attempt
                        log_execution_attempt(
                            symbol=sym_state.symbol,
                            correlation_id=_new_result.get("correlation_id", "") or f"v10_{sym_state.symbol}_{closed_time}",
                            direction=_new_result.get("side", ""),
                            volume=_new_result.get("volume", 0.0),
                            entry_price=_new_result.get("entry_price", 0.0),
                            stop_loss=_new_result.get("stop_loss", 0.0),
                            take_profit=_new_result.get("take_profit", 0.0),
                            strategy=_new_result.get("strategy", ""),
                            confidence=_new_result.get("score", 0.0),
                        )
                    except Exception:
                        pass
                    # ─── END EXEC TRACE ───────────────────────────────
                    _exec_prep = prepare_execution(
                        new_result=_new_result,
                        new_engine_score=_new_engine_score,
                        new_engine_htf=_new_engine_htf,
                        sym_state=sym_state,
                        cycle_id=cycle_id,
                        closed_time=closed_time,
                        canonical_opportunity_id=_canonical_opp_id,
                        candles=candles,
                        closed_i=closed_i,
                        bid=bid,
                        ask=ask,
                        tick_time=tick_time,
                        feed_state=_feed_state,
                        cycle_start=cycle_start,
                        dd_result=_dd_result,
                        dl_result=_dl_result,
                        runtime_session_id=_runtime_session_id,
                        config=config,
                    )
                    _new_engine_intent = _exec_prep.intent
                    _cor_id = _exec_prep.correlation_id
                    _decision_id = _exec_prep.decision_id

                    # ─── SHADOW RANKING: Collect candidate for post-cycle analysis ─
                    # Observational only. Does NOT defer or block execution.
                    # The ranking result is computed after the symbol loop for
                    # data collection and comparison against actual execution.
                    try:
                        from core.v10.opportunity_ranking import ExecutionCandidate
                        _execution_candidates.append(ExecutionCandidate(
                            symbol=sym_state.symbol,
                            new_result=_new_result,
                            pipeline_result=_new_result.get("v10_pipeline_result"),
                            exec_prep=_exec_prep,
                            sym_state=sym_state,
                            bid=bid,
                            ask=ask,
                            closed_time=float(closed_time),
                            cycle_opportunities=list(_cycle_opportunities),
                            v10_obs_id=_v10_obs_id if "_v10_obs_id" in dir() else "",
                            new_engine_htf=_new_engine_htf,
                            raw_patterns=list(_raw_patterns),
                            correlation_id=_cor_id,
                            decision_id=_decision_id,
                            engine_score=_new_engine_score,
                        ))
                    except Exception:
                        pass  # Shadow ranking collection must NEVER affect trading
                    # ─── END SHADOW RANKING COLLECTION ─────────────────────
                except Exception as _ne_exc:
                    # Engine A failure — block trading (fail-safe)
                    print(f"[ENGINE_A_ERROR] {type(_ne_exc).__name__}: {_ne_exc}")
                    try:
                        _dl = getattr(config, "_discord_logger", None)
                        if _dl is not None:
                            _dl.event("ERROR", {"location": "engine_a", "error_type": type(_ne_exc).__name__, "message": str(_ne_exc)[:200], "details": {"symbol": sym_state.symbol, "cycle": cycle_id, "action": "BLOCKED"}})
                    except Exception:
                        pass
                    # Persist exception in decision ledger
                    try:
                        _cycle_decision["decision"] = DecisionOutcome.NO_TRADE
                        _cycle_decision["reason"] = f"engine_exception:{type(_ne_exc).__name__}:{str(_ne_exc)[:150]}"
                        _cycle_decision["entity_id"] = f"{sym_state.symbol}_{int(closed_time)}"
                        _cycle_decision["pattern_state"] = "detected"
                        _cycle_decision["signal_type"] = _raw_patterns[0].pattern if _raw_patterns else None
                        _cycle_decision["last_stage"] = "exception"
                        _finalize_decision()
                    except Exception:
                        pass  # Exception persistence must never cause a second failure
                    continue  # Skip this symbol entirely — no trading
                # ─── END ENGINE A ─────────────────────────────────────

                # ═══════════════════════════════════════════════════════
                # ENGINE A EXECUTE SETUP
                # ═══════════════════════════════════════════════════════

                # Paper engine: evaluate pending signals (bar advancement)
                try:
                    from core.pipeline.paper_outcome_engine import get_paper_engine
                    get_paper_engine().evaluate_pending(sym_state.symbol, candles[closed_i].high, candles[closed_i].low, candles[closed_i].close)
                except Exception:
                    pass

                # Construct decision object for downstream consumers (risk guards, events, execution)
                class TradeDecision:
                    """Production trade decision contract for runtime guards and event emitters."""
                    should_trade = True
                    intent = _new_engine_intent
                    score = _new_engine_score
                    reason = "new_engine_execute"
                    bias = _new_engine_intent.side
                    bias_phase = "CONFIRMED"
                    bias_validation_score = _new_engine_score
                    structure_ok = True
                    patterns = [_new_engine_intent.pattern]

                decision = TradeDecision()
                _assessment_exec = _new_result.get("assessment")
                score_value = int((_assessment_exec.score_strategy if _assessment_exec else _new_engine_score) * 10)
                bias_value = _assessment_exec.side if _assessment_exec else (_new_engine_intent.side.name if hasattr(_new_engine_intent.side, 'name') else str(_new_engine_intent.side))
                pattern_value = _assessment_exec.pattern if _assessment_exec else _new_engine_intent.pattern
                _eval_unified = None  # Evaluation metadata (set by evaluation runner if enabled)

                # ─── TRADE NARRATIVE (passive, read-only) ─────────────
                _narrative_exec = None
                if _engine_mode != "V10":
                    try:
                        from core.pipeline.trade_narrative import build_trade_narrative
                        _narrative_exec = build_trade_narrative(
                            symbol=sym_state.symbol,
                            decision=_new_result,
                            engine_state=sym_state.engine_state,
                            cycle_id=cycle_id,
                            mt5_time=float(closed_time),
                        )
                        print(_narrative_exec)
                    except Exception:
                        pass  # Narrative failure must never affect execution

                # Route to AWS + Discord (passive)
                try:
                    from core.pipeline.output_router import process_engine_output
                    process_engine_output(
                        symbol=sym_state.symbol,
                        decision=_new_result,
                        engine_state=sym_state.engine_state,
                        cycle_id=cycle_id,
                        audit_output=None,
                        narrative_output=_narrative_exec,
                    )
                except Exception:
                    pass  # Routing failure must never affect execution
                # ─── END NARRATIVE + ROUTING ───────────────────────────

                # ─── ENGINE STATE VALIDATION + HTF CONTEXT ─────────────
                validate_engine_state(
                    sym_state.engine_state,
                    symbol=sym_state.symbol,
                    cycle_id=cycle_id,
                    strict=getattr(config, "ENGINE_STATE_STRICT_VALIDATION", False),
                )

                # MTF: update cache and build context (if enabled)
                _htf_context = None
                if sym_state.tf_cache is not None:
                    try:
                        sym_state.tf_cache.update_if_needed(
                            current_time_s=float(closed_time),
                            current_price=bid,
                        )
                        _htf_context = sym_state.tf_cache.get_htf_context(current_price=bid)
                    except Exception:
                        pass  # Graceful degradation — proceed without HTF

                # HTF observational logging
                if _htf_context is not None and getattr(_htf_context, "regime", None) is not None:
                    _h4r = _htf_context.regime
                    logger.info(
                        "[H4_CONTEXT] symbol=%s regime=%s bias=%s strength=%.3f atr_ratio=%.3f",
                        sym_state.symbol,
                        _h4r.classification.value,
                        getattr(_h4r, "trend_bias", "NEUTRAL"),
                        getattr(_h4r, "trend_strength", 0.0),
                        _h4r.atr_ratio,
                    )
                    try:
                        _h1_snap = getattr(_htf_context, "bias", None)
                        _m15_snap = getattr(_htf_context, "structure", None)
                        emit_feature_update(sym_state.symbol, {
                            "feature_type": "HTF_CONTEXT",
                            "bar_time": closed_time,
                            "cycle_id": cycle_id,
                            "h4_regime": _h4r.classification.value,
                            "h4_bias": getattr(_h4r, "trend_bias", "NEUTRAL"),
                            "h4_strength": round(getattr(_h4r, "trend_strength", 0.0), 4),
                            "h4_atr_ratio": round(_h4r.atr_ratio, 4),
                            "h1_bias": getattr(_h1_snap, "direction", None).value if _h1_snap and getattr(_h1_snap, "direction", None) else "UNKNOWN",
                            "h1_confidence": round(getattr(_h1_snap, "confidence", 0.0), 4) if _h1_snap else 0.0,
                            "m15_quality": round(getattr(_m15_snap, "quality_score", 0.0), 4) if _m15_snap else 0.0,
                            "m5_bias": sym_state.engine_state.current_bias.value if sym_state.engine_state.current_bias else "NONE",
                            "m5_regime": sym_state.engine_state.regime_state,
                        }, source="htf_context")
                    except Exception:
                        pass
                # ─── END ENGINE STATE + HTF ────────────────────────────

                # ─── EVALUATION (legacy shadow — never affects execution) ─
                _eval_result = run_evaluation(EvaluationContext(
                    cycle_id=cycle_id, symbol=sym_state.symbol,
                    closed_time=closed_time, candles=candles, closed_i=closed_i,
                    bid=bid, ask=ask, config=config, risk=sym_state.risk,
                    engine_state=sym_state.engine_state, htf_context=_htf_context,
                    new_engine_result=_new_result if "_new_result" in dir() else None,
                    new_engine_score=_new_engine_score,
                    new_engine_action="EXECUTE",
                ))
                _eval_unified = _eval_result.legacy_unified
                # ─── END EVALUATION ───────────────────────────────────

                # Emit events (observability only — must never block execution)
                try:
                    _bias_str = decision.bias.value if decision.bias else "NONE"
                    _msg = f"DECISION_EVALUATED | {sym_state.symbol} | score={score_value} | bias={_bias_str} | {decision.reason}"
                    emit_event("decision-log", _msg)
                except Exception:
                    pass  # Observability failure must never block execution

                try:
                    emit_bias_events(
                        symbol=sym_state.symbol,
                        event_state=sym_state.event_state,
                        engine_state=sym_state.engine_state,
                        candle_i=closed_i,
                        candle_time=closed_time,
                        bias_value=bias_value,
                        bias_phase=decision.bias_phase,
                        bias_validation_score=decision.bias_validation_score,
                        structure_ok=decision.structure_ok,
                    )
                except Exception:
                    pass  # Observability failure must never block execution

                try:
                    emit_setup_events(
                        symbol=sym_state.symbol,
                        event_state=sym_state.event_state,
                        decision=_new_result,
                        candle_i=closed_i,
                        candle_time=closed_time,
                        bias_value=bias_value,
                        pattern_value=pattern_value,
                        score_value=score_value,
                        decision_reason=decision.reason,
                        should_trade=decision.should_trade,
                    )
                except Exception:
                    pass  # Observability failure must never block execution

                # Decision audit — persist before execution
                if _eval_unified is not None:
                    _decision_id = persist_decision_audit(
                        symbol=sym_state.symbol, cycle_id=cycle_id, decision=_eval_unified,
                        engine_state=sym_state.engine_state, candles=candles,
                        closed_i=closed_i, runtime_mode="LIVE",
                        entity_id=_new_result.get("entity_id", "") if _new_result else "",
                        observation_id=_observation_id_cycle,
                        canonical_opportunity_id=_canonical_opp_id,
                        strategy_ts_utc_ms=_new_result.get("strategy_ts_utc_ms", 0) if _new_result else 0,
                    )
                else:
                    _decision_id = ""

                _cycle_had_trade = True
                _filter_hits["trades_executed"] += 1
               
                # ─── LIFECYCLE: EXECUTE decision confirmed ─────────────
                _cycle_had_execute_decision = True
                _cycle_execute_symbols.append(sym_state.symbol)
                # Track passed score
                _score_layer = getattr(_eval_unified, "score", None) if _eval_unified is not None else None
                if _score_layer is not None and getattr(_score_layer, "evaluated", False):
                    _score_tracker["passed_scores"].append((sym_state.symbol, getattr(_score_layer, "final_score", 0.0), getattr(_score_layer, "min_score_threshold", 5.0)))

                # ─── RUNTIME GUARD CHAIN (R13 — extracted to risk.runtime_guard_chain) ─
                _all_open_positions = []
                for _s in states:
                    if _s.trade_manager is not None:
                        _all_open_positions.extend(_s.trade_manager.positions_open())

                # ─── HORIZON EXECUTION AUTHORITY (Phase 2) ────────────
                # V10 mode: V10 HorizonEngine is the sole horizon authority.
                # Legacy mode: old HorizonExecutionAuthority checks permitted horizons.
                if _engine_mode != "V10":
                  try:
                    from core.horizon.execution_authority import HorizonExecutionAuthority
                    _horizon_authority = HorizonExecutionAuthority()
                    if _horizon_authority.enabled:
                        _horizon_perm = _horizon_authority.can_open(
                            symbol=sym_state.symbol,
                            horizon=decision.intent.metadata.get("horizon", "SCALP") if decision.intent.metadata else "SCALP",
                            current_positions=_all_open_positions,
                        )
                        if not _horizon_perm.allowed:
                            _cycle_decision["decision"] = DecisionOutcome.RISK_BLOCK
                            _cycle_decision["reason"] = f"horizon_authority:{_horizon_perm.reason}"
                            _cycle_decision["risk_flag"] = "horizon_authority"
                            _cycle_decision["signal_score"] = float(score_value)
                            _cycle_decision["pattern_state"] = "detected"
                            _cycle_decision["entity_id"] = _new_result.get("entity_id", "") if "_new_result" in dir() else ""
                            _cycle_decision["correlation_id"] = _cor_id if "_cor_id" in dir() else ""
                            _finalize_decision()
                            continue
                  except Exception:
                    pass  # Authority must NEVER block execution on internal error
                # ─── END HORIZON EXECUTION AUTHORITY ───────────────────

                _guard_chain_result = evaluate_runtime_guards(
                    symbol=sym_state.symbol,
                    intent=decision.intent,
                    daily_trade_limit=_daily_trade_limit,
                    trade_cooldown=_trade_cooldown,
                    all_open_positions=_all_open_positions,
                    candles=candles,
                    closed_i=closed_i,
                    htf_context=_htf_context,
                    engine_state=sym_state.engine_state,
                    config=config,
                )
                if not _guard_chain_result.allowed:
                    _gcr = _guard_chain_result
                    logger.info(
                        "[STATE] symbol=%s | BLOCKED_%s | reason=%s",
                        sym_state.symbol, _gcr.guard_name.upper(), _gcr.reason,
                    )
                    # ─── EXEC TRACE: Guard blocked execution ──────────
                    try:
                        from core.runtime.execution_trace import log_execution_blocked
                        log_execution_blocked(
                            symbol=sym_state.symbol,
                            correlation_id=_cor_id if "_cor_id" in dir() else "",
                            blocker=_gcr.guard_name,
                            reason=_gcr.reason,
                        )
                    except Exception:
                        pass
                    # ─── END EXEC TRACE ───────────────────────────────
                    if _gcr.rejection_code:
                        record_rejection(_gcr.rejection_code)
                    if _gcr.filter_key and _gcr.filter_key in _filter_hits:
                        _filter_hits[_gcr.filter_key] += 1
                    _decision_funnel.record_guard_block(_gcr.guard_name)
                    emit_risk_guard_result(sym_state.symbol, _gcr.guard_name, "REJECTED", _gcr.reason, _gcr.metadata)
                    # ─── LIFECYCLE: Execution drop (guard blocked) ─────
                    _cycle_execution_drops.append((sym_state.symbol, _gcr.guard_name, _gcr.reason))
                    _cycle_blocked_symbols.append(sym_state.symbol)
                    persist_risk_rejection(
                        symbol=sym_state.symbol, cycle_id=cycle_id,
                        guard=_gcr.guard_name, reason=_gcr.reason,
                        correlation_id=_cor_id_cycle,
                        canonical_opportunity_id=_cycle_decision.get("canonical_opportunity_id", ""),
                        metadata=_gcr.metadata,
                    )
                    try:
                        _dl = getattr(config, "_discord_logger", None)
                        if _dl is not None:
                            _dl.event("RISK_BLOCK", {"guard": _gcr.guard_name, "symbol": sym_state.symbol, "reason": _gcr.reason, "details": _gcr.metadata})
                    except Exception:
                        pass
                    _cycle_decision["decision"] = DecisionOutcome.RISK_BLOCK
                    _cycle_decision["reason"] = _gcr.reason
                    _cycle_decision["risk_flag"] = _gcr.guard_name
                    _cycle_decision["signal_score"] = float(score_value)
                    _cycle_decision["pattern_state"] = "detected"
                    _cycle_decision["entity_id"] = _new_result.get("entity_id", "") if "_new_result" in dir() else ""
                    _cycle_decision["correlation_id"] = _cor_id if "_cor_id" in dir() else ""
                    _finalize_decision()

                    # ─── OPPORTUNITY: Mark REJECTED by guard (Phase 2A) ─
                    try:
                        from core.opportunity.opportunity import OpportunityState
                        from core.opportunity.persistence import persist_opportunity
                        for _opp in _cycle_opportunities:
                            if _opp.state == OpportunityState.ASSESSED.value:
                                _opp.transition(
                                    OpportunityState.REJECTED,
                                    rejection_reason=_gcr.reason,
                                    rejection_stage=f"guard_chain:{_gcr.guard_name}",
                                )
                                persist_opportunity(_opp)
                                # ─── DISCORD: Guard REJECTED lifecycle event ──
                                try:
                                    _dl = getattr(config, "_discord_logger", None)
                                    if _dl is not None:
                                        _dl.event("OPPORTUNITY_LIFECYCLE", {
                                            "opportunity_id": _opp.opportunity_id,
                                            "entity_id": _opp.entity_id,
                                            "observation_id": _v10_obs_id,
                                            "cycle_id": _opp.cycle_id,
                                            "symbol": _opp.symbol,
                                            "lifecycle_state": _opp.state,
                                            "pattern": _opp.pattern,
                                            "pattern_confidence": _opp.pattern_confidence,
                                            "direction": _opp.direction,
                                            "session_at_detection": _opp.session_at_detection,
                                            "h4_regime": _opp.h4_regime,
                                            "h1_direction": _opp.h1_direction,
                                            "strategy_classification": _opp.strategy_classification,
                                            "strategy_confidence": _opp.strategy_confidence,
                                            "overall_score": _opp.overall_score,
                                            "rejection_reason": _opp.rejection_reason,
                                            "rejection_stage": _opp.rejection_stage,
                                            "correlation_id": _opp.correlation_id,
                                        })
                                except Exception:
                                    pass
                                # ─── END DISCORD GUARD REJECTED ───────────────
                                break
                    except Exception:
                        pass  # Opportunity layer must NEVER affect trading
                    # ─── END OPPORTUNITY GUARD REJECTION ───────────────
                    continue
                # ─── END RUNTIME GUARD CHAIN ──────────────────────────

                # ─── EXECUTION (R14+R15 — extracted to execution_orchestrator) ─
                # ─── LIFECYCLE: Execution attempt (guards passed) ──────
                _cycle_had_execution_attempt = True
                _cycle_execution_symbols.append(sym_state.symbol)
                logger.info("[STATE] symbol=%s | ENTRY | pattern=%s | score=%d", sym_state.symbol, decision.intent.pattern, score_value)
                # ─── EXEC TRACE: Order being submitted to broker ──────
                try:
                    from core.runtime.execution_trace import log_order_submitted
                    log_order_submitted(
                        symbol=sym_state.symbol,
                        correlation_id=_cor_id if "_cor_id" in dir() else "",
                        direction=decision.intent.side.name if hasattr(decision.intent.side, 'name') else str(decision.intent.side),
                        volume=decision.intent.volume,
                        price=bid if decision.intent.side.name == "BUY" else ask if hasattr(decision.intent.side, 'name') else bid,
                    )
                except Exception:
                    pass
                # ─── END EXEC TRACE ───────────────────────────────────
                _exec_outcome = _exec_orchestrator.execute_trade(
                    intent=decision.intent,
                    symbol=sym_state.symbol,
                    cycle_id=cycle_id,
                    decision_id=_decision_id,
                    correlation_id=_cor_id if "_cor_id" in dir() else "",
                    entity_id=_new_result.get("entity_id", "") if "_new_result" in dir() else "",
                    observation_id=_observation_id_cycle,
                    canonical_opportunity_id=_canonical_opp_id,
                    # Phase 3 Step 4: execution-moment feed facts + planned
                    # risk geometry, derived from this cycle's tick/intent.
                    bid_at_execution=bid if "bid" in dir() else 0.0,
                    ask_at_execution=ask if "ask" in dir() else 0.0,
                    risk_distance=abs(
                        float(decision.intent.entry_reference) - float(decision.intent.sl)
                    ) if getattr(decision.intent, "sl", 0.0) else 0.0,
                    mt5_state=mt5_state,
                )
                if not _exec_outcome.executed:
                    # ─── EXEC TRACE: Order failed ─────────────────────
                    try:
                        from core.runtime.execution_trace import log_order_failed
                        log_order_failed(
                            symbol=sym_state.symbol,
                            correlation_id=_cor_id if "_cor_id" in dir() else "",
                            error_code=0,
                            error_message=_exec_outcome.error or "execution_not_attempted",
                            stage="execute_trade",
                        )
                    except Exception:
                        pass
                    # ─── END EXEC TRACE ───────────────────────────────
                    # ─── FIX: Finalize decision for failed execution attempts ─
                    _cycle_decision["decision"] = DecisionOutcome.NO_TRADE
                    _cycle_decision["reason"] = f"execution_not_attempted:{_exec_outcome.error[:100] if _exec_outcome.error else 'unknown'}"
                    _cycle_decision["risk_flag"] = "execution_failure"
                    _cycle_decision["signal_score"] = float(score_value) if "score_value" in dir() else 0.0
                    _cycle_decision["pattern_state"] = "detected"
                    _cycle_decision["entity_id"] = _new_result.get("entity_id", "") if "_new_result" in dir() else ""
                    _cycle_decision["correlation_id"] = _cor_id if "_cor_id" in dir() else ""
                    _finalize_decision()
                    continue
                result = _exec_outcome.result
                _decision_ts = _exec_outcome.decision_ts_utc_ms
                # ─── END EXECUTION ────────────────────────────────────

                if result.ok:
                    # ─── EXEC TRACE: Order filled ─────────────────────
                    try:
                        from core.runtime.execution_trace import log_order_filled
                        log_order_filled(
                            symbol=sym_state.symbol,
                            correlation_id=_cor_id if "_cor_id" in dir() else "",
                            ticket=getattr(result, 'order', 0) or 0,
                            deal=getattr(result, 'deal', 0) or 0,
                            fill_price=getattr(result, 'price', 0.0) or 0.0,
                            volume=getattr(result, 'volume', 0.0) or 0.0,
                        )
                    except Exception:
                        pass
                    # ─── END EXEC TRACE ───────────────────────────────
                    sym_state.engine_state.last_successful_open_mono = float(closed_time)
                    _daily_trade_limit.record_trade_open(sym_state.symbol)
                    # ─── LIFECYCLE: Broker confirmed fill ──────────────
                    _cycle_had_fill = True
                    _cycle_filled_symbols.append(sym_state.symbol)

                    # ─── OPPORTUNITY: Mark EXECUTED (Phase 2A shadow) ─
                    try:
                        from core.opportunity.opportunity import OpportunityState
                        from core.opportunity.persistence import persist_opportunity
                        for _opp in _cycle_opportunities:
                            if _opp.state == OpportunityState.ASSESSED.value:
                                _opp.transition(
                                    OpportunityState.EXECUTED,
                                    outcome_trade_id=f"pos_{result.deal}" if result.deal else "",
                                    correlation_id=_cor_id if "_cor_id" in dir() else "",
                                )
                                persist_opportunity(_opp)
                                # ─── DISCORD: EXECUTED lifecycle event ────
                                try:
                                    _dl = getattr(config, "_discord_logger", None)
                                    if _dl is not None:
                                        _dl.event("OPPORTUNITY_LIFECYCLE", {
                                            "opportunity_id": _opp.opportunity_id,
                                            "entity_id": _opp.entity_id,
                                            "observation_id": _v10_obs_id,
                                            "cycle_id": _opp.cycle_id,
                                            "symbol": _opp.symbol,
                                            "lifecycle_state": _opp.state,
                                            "pattern": _opp.pattern,
                                            "pattern_confidence": _opp.pattern_confidence,
                                            "direction": _opp.direction,
                                            "session_at_detection": _opp.session_at_detection,
                                            "h4_regime": _opp.h4_regime,
                                            "h1_direction": _opp.h1_direction,
                                            "strategy_classification": _opp.strategy_classification,
                                            "strategy_confidence": _opp.strategy_confidence,
                                            "overall_score": _opp.overall_score,
                                            "correlation_id": _opp.correlation_id,
                                            "outcome_trade_id": _opp.outcome_trade_id,
                                            "entry_price": getattr(decision.intent, "entry_reference", 0.0) if "decision" in dir() else 0.0,
                                            "stop_price": getattr(decision.intent, "sl", 0.0) if "decision" in dir() else 0.0,
                                            "target_price": getattr(decision.intent, "tp", 0.0) if "decision" in dir() else 0.0,
                                            "position_size": getattr(decision.intent, "volume", 0.0) if "decision" in dir() else 0.0,
                                        })
                                except Exception:
                                    pass
                                # ─── END DISCORD EXECUTED ─────────────────
                                break
                    except Exception:
                        pass  # Opportunity layer must NEVER affect trading
                    # ─── END OPPORTUNITY EXECUTED ──────────────────────

                    # Finalize decision: EXECUTE
                    _cycle_decision["decision"] = DecisionOutcome.EXECUTE
                    _cycle_decision["reason"] = "all_guards_passed"
                    _cycle_decision["signal_score"] = float(score_value)
                    _cycle_decision["signal_type"] = decision.intent.pattern
                    _cycle_decision["pattern_state"] = "detected"
                    _cycle_decision["correlation_id"] = _cor_id if "_cor_id" in dir() else _cor_id_cycle
                    _cycle_decision["entity_id"] = _new_result.get("entity_id", "") if "_new_result" in dir() else ""
                    _cycle_decision["execution_intent"] = {
                        "side": decision.intent.side.name if hasattr(decision.intent.side, "name") else str(decision.intent.side),
                        "volume": decision.intent.volume,
                        "sl": decision.intent.sl,
                        "tp": decision.intent.tp,
                        "pattern": decision.intent.pattern,
                        "horizon": decision.intent.metadata.get("horizon", "SCALP") if decision.intent.metadata else "SCALP",
                    }
                    # Attach reasoning (observational — never affects decision)
                    try:
                        _exec_reasoning = _new_result.get("reasoning") if "_new_result" in dir() else None
                        if _exec_reasoning and hasattr(_exec_reasoning, "to_dict"):
                            _cycle_decision["reasoning"] = _exec_reasoning.to_dict()
                        # Attach uncertainty (observational — measures ambiguity)
                        _exec_uncertainty = _new_result.get("uncertainty") if "_new_result" in dir() else None
                        if _exec_uncertainty and hasattr(_exec_uncertainty, "to_dict"):
                            _cycle_decision["uncertainty"] = _exec_uncertainty.to_dict()
                        # Attach score attribution (observational — decomposes score)
                        _exec_attribution = _new_result.get("attribution") if "_new_result" in dir() else None
                        if _exec_attribution and hasattr(_exec_attribution, "to_dict"):
                            _cycle_decision["score_attribution"] = _exec_attribution.to_dict()
                        # Attach dual EV comparison (observational — shadow model comparison)
                        _exec_dual_ev = _new_result.get("dual_ev") if "_new_result" in dir() else None
                        if _exec_dual_ev:
                            _cycle_decision["dual_ev"] = _exec_dual_ev
                            # Feed promotion monitor (never affects execution)
                            try:
                                from core.research_assessment.promotion_monitor import record_comparison
                                record_comparison(_exec_dual_ev)
                            except Exception:
                                pass
                    except Exception:
                        pass
                    _finalize_decision()

                    # Trade manager: register position from execution result
                    if sym_state.trade_manager is not None:
                        try:
                            bid_f, ask_f, _ = sym_state.feed.last_tick(sym_state.symbol)
                            fill_px = float(ask_f if decision.intent.side is Side.BUY else bid_f)

                            # Build immutable TradeIdentity from execution context
                            from core.trade_identity import TradeIdentity
                            _trade_identity = TradeIdentity(
                                correlation_id=_cor_id if "_cor_id" in dir() else "",
                                decision_id=_decision_id if "_decision_id" in dir() else "",
                                canonical_opportunity_id=_canonical_opp_id,
                                observation_id="",
                                cycle_id=cycle_id,
                                strategy=_new_result.get("strategy", "") if "_new_result" in dir() else "",
                                pattern=decision.intent.pattern,
                                decision_ts_utc=float(closed_time),
                            )

                            sym_state.trade_manager.register_from_execution(
                                decision.intent,
                                magic=config.BOT_MAGIC,
                                execution=result,
                                entry_fill_price=fill_px,
                                bid=bid_f,
                                ask=ask_f,
                                trade_identity=_trade_identity,
                            )
                        except Exception:
                            pass

                    # ─── POST-FILL PROTECTION VERIFICATION (Phase 1 hardening) ─
                    # Verify broker actually has SL/TP on the position.
                    # If missing, attempt correction. Never blocks execution.
                    try:
                        from core.protection_verification import verify_protection
                        # MT5 position ticket = order ticket (NOT deal ticket)
                        # result.deal = transaction ID, result.order = position ticket
                        _position_ticket = result.order if result.order else 0
                        if _position_ticket > 0:
                            _prot_result = verify_protection(
                                symbol=sym_state.symbol,
                                position_ticket=_position_ticket,
                                requested_sl=decision.intent.sl,
                                requested_tp=decision.intent.tp,
                                correlation_id=_cor_id if "_cor_id" in dir() else "",
                                execution_module=_exec_orchestrator._execution,
                            )
                            # Persist protection fields to execution record
                            try:
                                from core.persistence.execution_result_writer import persist_execution_result
                                persist_execution_result(
                                    symbol=sym_state.symbol,
                                    cycle_id=cycle_id,
                                    result_ok=result.ok,
                                    retcode=result.retcode,
                                    deal=result.deal,
                                    order=result.order,
                                    comment="protection_verification",
                                    fill_price=float(result.fill_price) if result.fill_price else 0.0,
                                    side=decision.intent.side.name,
                                    volume=decision.intent.volume,
                                    sl=decision.intent.sl,
                                    tp=decision.intent.tp,
                                    pattern=decision.intent.pattern,
                                    decision_id=_decision_id if "_decision_id" in dir() else "",
                                    correlation_id=_cor_id if "_cor_id" in dir() else "",
                                    entity_id=_new_result.get("entity_id", "") if "_new_result" in dir() else "",
                                    observation_id="",
                                    canonical_opportunity_id=_canonical_opp_id,
                                    requested_sl=_prot_result.requested_sl,
                                    broker_confirmed_sl=_prot_result.broker_confirmed_sl,
                                    requested_tp=_prot_result.requested_tp,
                                    broker_confirmed_tp=_prot_result.broker_confirmed_tp,
                                    protection_status=_prot_result.protection_status,
                                    protection_failure_reason=_prot_result.protection_failure_reason,
                                )
                            except Exception:
                                pass  # Protection persistence failure must not affect trading
                    except Exception as _prot_exc:
                        logger.error(
                            "[PROTECTION_VERIFICATION_ERROR] symbol=%s error=%s",
                            sym_state.symbol, _prot_exc,
                        )
                    # ─── END PROTECTION VERIFICATION ──────────────────────

                    # ─── POST-EXECUTION EFFECTS (extracted) ───────────────
                    emit_post_trade_success(
                        symbol=sym_state.symbol,
                        intent=decision.intent,
                        result=result,
                        score_value=score_value,
                        closed_i=closed_i,
                        closed_time=closed_time,
                        bias_value=bias_value,
                        config=config,
                        new_result=_new_result if "_new_result" in dir() else None,
                        unified=_eval_unified,
                        engine_state=sym_state.engine_state,
                    )
                else:
                    # ─── EXEC TRACE: Broker rejected ──────────────────
                    try:
                        from core.runtime.execution_trace import log_order_failed
                        log_order_failed(
                            symbol=sym_state.symbol,
                            correlation_id=_cor_id if "_cor_id" in dir() else "",
                            error_code=getattr(result, "retcode", -1),
                            error_message=getattr(result, "comment", "broker_rejected"),
                            stage="broker_response",
                        )
                    except Exception:
                        pass
                    # ─── END EXEC TRACE ───────────────────────────────
                    _cycle_decision["decision"] = DecisionOutcome.NO_TRADE
                    _cycle_decision["reason"] = "execution_failed:broker_rejected"
                    _cycle_decision["risk_flag"] = "execution_failure"
                    _cycle_decision["entity_id"] = _new_result.get("entity_id", "") if "_new_result" in dir() else ""
                    _cycle_decision["correlation_id"] = _cor_id if "_cor_id" in dir() else ""
                    # ─── LIFECYCLE: Broker drop (rejected) ─────────────
                    _cycle_broker_drops.append((sym_state.symbol, getattr(result, "retcode", -1), "broker_rejected"))
                    _cycle_rejected_symbols.append(sym_state.symbol)
                    _finalize_decision()
                    emit_post_trade_failure(
                        result=result,
                        closed_i=closed_i,
                        closed_time=closed_time,
                        bias_value=bias_value,
                        score_value=score_value,
                    )

              except Exception as exc:
                log_runtime_exception(exc, "UNKNOWN_STAGE", mt5_state)
                try:
                    _dl = getattr(config, "_discord_logger", None)
                    if _dl is not None:
                        _dl.event("ERROR", {"location": "live_scanner:unknown_stage", "error_type": type(exc).__name__, "message": str(exc)[:200], "details": {"symbol": getattr(sym_state, "symbol", None), "cycle": cycle_id}})
                except Exception:
                    pass
                # ─── FIX: Ensure decision ledger is written on exception ─
                try:
                    if not _decision_recorder.is_written:
                        _cycle_decision["decision"] = DecisionOutcome.NO_TRADE
                        _cycle_decision["reason"] = f"exception_in_execute_path:{type(exc).__name__}:{str(exc)[:100]}"
                        _cycle_decision["risk_flag"] = "execution_exception"
                        _finalize_decision()
                except Exception:
                    pass  # Finalization failure must not cause a second crash
                continue
            # ─── END PER-SYMBOL PROCESSING ────────────────────────────

            # ═══════════════════════════════════════════════════════════
            # SHADOW RANKING: Observe, rank, log, compare (never execute)
            # ═══════════════════════════════════════════════════════════
            if _execution_candidates:
                try:
                    from core.v10.opportunity_ranking import rank_for_execution, format_ranking_summary
                    from core.portfolio_ranking.context import build_portfolio_context

                    # Build portfolio context (open positions across all symbols)
                    _all_open_for_ranking = []
                    for _s in states:
                        if _s.trade_manager is not None:
                            _all_open_for_ranking.extend(_s.trade_manager.positions_open())
                    _portfolio_ctx = build_portfolio_context(
                        open_positions=_all_open_for_ranking,
                        daily_risk_used_pct=getattr(_dl_result, "current_loss_pct", 0.0) or 0.0,
                        daily_drawdown_pct=getattr(_dd_result, "current_drawdown_pct", 0.0) or 0.0,
                    )

                    # Rank all EXECUTE candidates
                    _ranked_scores = rank_for_execution(_execution_candidates, _portfolio_ctx)
                    if _ranked_scores:
                        print(format_ranking_summary(_ranked_scores))

                    # Shadow comparison: what did ranking recommend vs what actually executed?
                    _ranking_recommended = _ranked_scores[0].symbol if _ranked_scores else None
                    _actually_executed = _cycle_execute_symbols[0] if _cycle_execute_symbols else None

                    if _ranking_recommended and _actually_executed:
                        _match = _ranking_recommended == _actually_executed
                        print(
                            f"[RANKING_SHADOW] recommended={_ranking_recommended} "
                            f"executed={_actually_executed} match={_match}"
                        )
                    elif _ranking_recommended and not _actually_executed:
                        print(
                            f"[RANKING_SHADOW] recommended={_ranking_recommended} "
                            f"executed=NONE (guards/risk blocked all)"
                        )

                    # Persist shadow ranking for research
                    try:
                        from core.v10.opportunity_ranking_persistence import persist_ranking_shadow
                        persist_ranking_shadow(
                            cycle_id=cycle_id,
                            ranked_scores=_ranked_scores,
                            actually_executed=_actually_executed,
                            runtime_session_id=_runtime_session_id,
                        )
                    except Exception:
                        pass  # Persistence failure must never affect trading

                except Exception as _ranking_exc:
                    print(f"[RANKING_SHADOW_ERROR] {type(_ranking_exc).__name__}: {_ranking_exc}")
            # ═══════════════════════════════════════════════════════════
            # END SHADOW RANKING
            # ═══════════════════════════════════════════════════════════

            # ─── OPPORTUNITY RANKING (passive, post-cycle) ────────────
            if _cycle_candidates:
                try:
                    from core.pipeline.opportunity_ranker import rank_candidates, format_ranking_narrative
                    _opp_pool = rank_candidates(_cycle_candidates)
                    if _opp_pool.total_candidates > 1 or _opp_pool.eligible_count > 0:
                        print(format_ranking_narrative(_opp_pool))
                    # ─── PERSIST PORTFOLIO RANKING (Phase 2C — observation only) ─
                    try:
                        from core.portfolio_ranking.persistence import persist_portfolio_ranking
                        from core.portfolio_ranking.context import build_portfolio_context, enrich_candidate
                        _open_pos_list = []
                        for _s in states:
                            if _s.trade_manager is not None:
                                _open_pos_list.extend(_s.trade_manager.positions_open())
                        _open_count = len(_open_pos_list)

                        # Build portfolio context snapshot
                        _portfolio_ctx = build_portfolio_context(
                            open_positions=_open_pos_list,
                            daily_risk_used_pct=getattr(_dl_result, "current_loss_pct", 0.0) or 0.0,
                            daily_drawdown_pct=getattr(_dd_result, "current_drawdown_pct", 0.0) or 0.0,
                        )

                        # Enrich each candidate with portfolio context
                        _enrichments = []
                        for _c in getattr(_opp_pool, "candidates", []):
                            _e = enrich_candidate(
                                symbol=getattr(_c, "symbol", ""),
                                direction="",  # Direction not on RankedCandidate
                                rank_score=getattr(_c, "rank_score", 0.0),
                                portfolio_ctx=_portfolio_ctx,
                            )
                            _enrichments.append(_e)

                        persist_portfolio_ranking(
                            _opp_pool,
                            runtime_session_id=_runtime_session_id,
                            open_positions_count=_open_count,
                            max_open_positions=int(getattr(config, "MAX_OPEN_POSITIONS", 1)),
                            portfolio_context=_portfolio_ctx,
                            candidate_enrichments=_enrichments,
                        )
                    except Exception:
                        pass  # Ranking persistence must NEVER affect trading
                    # ─── END PERSIST PORTFOLIO RANKING ─────────────────────

                    # ─── SHADOW COMPARISON (Phase 2C-Part3) ───────────────
                    # Compare what actually executed vs what ranking recommends.
                    # Only runs when PORTFOLIO_RANKING_SHADOW_LOG is True.
                    try:
                        if getattr(config, "PORTFOLIO_RANKING_SHADOW_LOG", True):
                            from core.portfolio_ranking.shadow_comparison import (
                                compute_shadow_comparison, persist_shadow_comparison,
                            )
                            # Determine which symbols actually executed this cycle
                            _executed_this_cycle = [
                                c.get("symbol", "")
                                for c in _cycle_candidates
                                if c.get("action") == "EXECUTE"
                                and c.get("symbol", "") in [
                                    s.symbol for s in states
                                    if s.trade_manager and any(
                                        p.open_time >= cycle_start
                                        for p in s.trade_manager.positions_open()
                                    )
                                ]
                            ]
                            # Fallback: use candidates with EXECUTE action
                            if not _executed_this_cycle:
                                _executed_this_cycle = [
                                    c.get("symbol", "")
                                    for c in _cycle_candidates
                                    if c.get("action") == "EXECUTE"
                                ]

                            _shadow = compute_shadow_comparison(
                                pool=_opp_pool,
                                executed_symbols=_executed_this_cycle,
                                cycle_id=cycle_id,
                                runtime_session_id=_runtime_session_id,
                            )
                            persist_shadow_comparison(_shadow)
                    except Exception:
                        pass  # Shadow comparison must NEVER affect trading
                    # ─── END SHADOW COMPARISON ─────────────────────────────
                except Exception:
                    pass  # Ranking failure must never affect execution
            # ─── END OPPORTUNITY RANKING ───────────────────────────────

            # ─── CYCLE REPORT (R16 — extracted to core.pipeline.cycle_report) ─
            emit_cycle_report(
                cycle_id=cycle_id,
                cycle_start=cycle_start,
                n_symbols=len(states),
                cycle_drops=_cycle_drops,
                cycle_had_trade=_cycle_had_trade,
                this_cycle_new_bars=_this_cycle_new_bars,
                filter_hits=_filter_hits,
                states=states,
                htf_context=_htf_context,
                config=config,
                # Lifecycle data (Phase 5)
                cycle_had_execute_decision=_cycle_had_execute_decision,
                cycle_had_execution_attempt=_cycle_had_execution_attempt,
                cycle_had_fill=_cycle_had_fill,
                cycle_execute_symbols=_cycle_execute_symbols,
                cycle_execution_symbols=_cycle_execution_symbols,
                cycle_filled_symbols=_cycle_filled_symbols,
                cycle_blocked_symbols=_cycle_blocked_symbols,
                cycle_rejected_symbols=_cycle_rejected_symbols,
                cycle_decision_drops=_cycle_decision_drops,
                cycle_execution_drops=_cycle_execution_drops,
                cycle_broker_drops=_cycle_broker_drops,
            )
            # ─── END CYCLE REPORT ─────────────────────────────────────

            # ─── NO-TRADE ALERT (delegated to HealthMonitor) ────────────
            # Handled inside _health_monitor.tick() below
            # ─── END NO-TRADE ALERT ───────────────────────────────────

            # ─── PERIODIC RECONCILIATION ──────────────────────────────
            if time.time() - last_reconcile_time >= reconcile_interval:
                last_reconcile_time = time.time()
                for sym_state in states:
                    if sym_state.trade_manager is not None:
                        try:
                            logger.info("[RECONCILIATION_START] symbol=%s", sym_state.symbol)
                            reconcile_state_sanity(sym_state.trade_manager, sym_state.symbol, config.BOT_MAGIC)
                            logger.info("[RECONCILIATION_COMPLETE] symbol=%s", sym_state.symbol)
                        except Exception as exc:
                            logger.error("[RECONCILIATION_ERROR] symbol=%s error=%s", sym_state.symbol, exc)
            # ─── END PERIODIC RECONCILIATION ──────────────────────────

            # ─── RISK TIMELINE SNAPSHOT (observability only) ──────────
            try:
                from risk.risk_summary import get_risk_summary
                from risk.risk_timeline import record_risk_snapshot
                _risk_snap = get_risk_summary(
                    daily_loss_guard=_daily_loss_guard,
                    drawdown_guard=_drawdown_guard,
                )
                record_risk_snapshot(_risk_snap)
            except Exception:
                pass  # Timeline failure must never affect runtime
            # ─── END RISK TIMELINE ────────────────────────────────────

            # ─── HEARTBEAT + LIVENESS (delegated to HealthMonitor) ─────
            _cycle_latency_s = time.time() - cycle_start
            _health_monitor.tick(cycle_id, _cycle_latency_s, mt5_state, _cycle_had_trade, cycle_had_fill=_cycle_had_fill)

            # ─── PIPELINE DIAGNOSTICS (extracted to core.pipeline.pipeline_diagnostics) ─
            emit_pipeline_diagnostics(
                cycle_id=cycle_id,
                decision_funnel=_decision_funnel,
                score_tracker=_score_tracker,
                filter_hits=_filter_hits,
            )
            # ─── END PIPELINE DIAGNOSTICS ─────────────────────────────

            # ─── DECISION LEDGER FLUSH (timer-based) ──────────────────
            try:
                _ledger.tick()
            except Exception:
                pass
            # ─── END LEDGER FLUSH ─────────────────────────────────────

            # ─── PERIODIC STATE CHECKPOINT ─────────────────────────────
            _checkpoint_interval = int(getattr(config, "CHECKPOINT_INTERVAL_CYCLES", 50))
            if _checkpoint_interval > 0 and cycle_id % _checkpoint_interval == 0:
                try:
                    save_engine_states([(s.symbol, s.engine_state) for s in states])
                    logger.info(
                        "[STATE_CHECKPOINT] cycle_id=%d symbols=%d",
                        cycle_id, len(states),
                    )
                except Exception as _cp_exc:
                    logger.warning("[STATE_CHECKPOINT_FAILED] cycle_id=%d error=%s", cycle_id, _cp_exc)
            # ─── END PERIODIC CHECKPOINT ───────────────────────────────

            interruptible_sleep(config.POLL_SECONDS)

    finally:
        for s in states:
            try:
                s.feed.disconnect()
            except Exception:
                pass
        # Flush decision ledger (ensure all buffered records reach disk)
        try:
            _ledger.flush()
        except Exception:
            pass
        # Persist EngineState on graceful shutdown
        try:
            save_engine_states([(s.symbol, s.engine_state) for s in states])
        except Exception:
            pass
        # Emit evaluation shutdown summary
        try:
            from core.evaluation.evaluation_runner import shutdown_evaluation
            shutdown_evaluation(config)
        except Exception:
            pass
        logger.info("[LIVE_SCANNER] shutdown | cycles=%d", cycle_id)