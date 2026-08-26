"""
NEW Shadow Runtime — Per-opportunity horizon-shadow simulation engine.

Contract summary (authorised implementation):
    - child lineage of canonical_opportunity_id; one shadow_trade_id per horizon
    - branch is PRE-VERDICT; live V10 facts are inherited observations only
      (namespaced ``live_facts``) — there is NO shadow decision stage
    - PLAN covers all three horizons every opportunity-cycle, even N=0
    - OPEN embeds the complete immutable construction + provenance + assumptions
    - lifecycle progresses on authoritative closed bars with a durable
      per-simulation watermark (exactly-once per (shadow_trade_id, bar_time))
    - DATA_GAP observations are recorded honestly; missing bars never fabricated
    - PROGRESS checkpoints bound crash-loss windows; CLOSE carries the full
      progression and outcome so reconstruction never needs the runtime alive
    - recovery replays ONLY this domain's event stream

Fire-and-forget: no method here may affect live trading. Callers wrap us in
their own exception isolation; internal failures degrade to dropped shadows.
"""

from __future__ import annotations

import logging
from typing import Any

from core.shadow.assumptions import (
    DEFAULT_CHECKPOINT_INTERVAL,
    build_assumptions,
)
from core.shadow.models import (
    CONSTRUCTION_MODEL_VERSION,
    EXIT_STOP_LOSS,
    EXIT_TAKE_PROFIT,
    EXIT_TIMEOUT,
    HORIZONS,
    M5_BAR_INTERVAL_S,
    SCHEMA_VERSION,
    SIMULATION_MODEL_VERSION,
    LifecycleState,
    market_block,
)
from core.shadow.persistence import (
    ShadowEventWriter,
    get_broker_offset_seconds,
    load_events,
)
from core.trade_truth import compute_mae_r, compute_mfe_r, compute_r_multiple

logger = logging.getLogger(__name__)


def _wall_stamp() -> dict[str, Any]:
    """Runtime processing time from the canonical clock (contract §13)."""
    from core.clock import utc_ms, utc_ms_to_iso

    ms = utc_ms()
    return {"recorded_at_utc_ms": ms, "recorded_at_utc": utc_ms_to_iso(ms)}


class ShadowRuntime:
    """
    NEW per-opportunity Shadow Runtime.

    One instance per process; single writer of the NEW Shadow event stream.
    Not lock-protected by design: production call sites (live_scanner cycle +
    BarProvider closed-bar transition) are sequential.
    """

    def __init__(self, writer: ShadowEventWriter | None = None) -> None:
        self._writer = writer or ShadowEventWriter()
        self._active: dict[str, dict[str, Any]] = {}  # trade_id → sim state
        self._planned_roots: set[str] = set()
        self.recover()

    # ─────────────────────────────────────────────────────────────────────
    # ENVELOPE
    # ─────────────────────────────────────────────────────────────────────

    def _envelope(
        self,
        *,
        event_type: str,
        symbol: str,
        market_time_raw: int,
        broker_offset: int,
        canonical_opportunity_id: str = "",
        shadow_trade_id: str = "",
    ) -> dict[str, Any]:
        ev: dict[str, Any] = {
            "event_type": event_type,
            "schema_version": SCHEMA_VERSION,
            "construction_model_version": CONSTRUCTION_MODEL_VERSION,
            "simulation_model_version": SIMULATION_MODEL_VERSION,
            "canonical_opportunity_id": canonical_opportunity_id,
            "shadow_trade_id": shadow_trade_id,
            "symbol": symbol,
            "broker_offset_seconds": int(broker_offset),
        }
        ev.update(market_block("event_market_time", market_time_raw, broker_offset))
        ev.update(_wall_stamp())
        return ev

    def _write(self, event: dict[str, Any]) -> None:
        self._writer.append(
            event=event,
            symbol=event.get("symbol", "UNKNOWN"),
            market_time_raw=int(event.get("event_market_time", 0)),
            broker_offset_seconds=int(event.get("broker_offset_seconds", 0)),
        )

    # ─────────────────────────────────────────────────────────────────────
    # PLAN + OPEN (branch point entry)
    # ─────────────────────────────────────────────────────────────────────

    def handle_opportunity(self, ctx: dict[str, Any]) -> None:
        """
        Plan + open simulations for one canonical opportunity-cycle.

        ctx keys (assembled by core.shadow.integration):
            canonical_opportunity_id, entity_id, symbol, cycle_id,
            bar_time_raw, direction, pattern, strategy, score,
            regime, h4_regime, h1_bias, market_phase, market_phase_confidence,
            bid, ask, structure {m5_candle_high/low, m15_nearest_support/
            resistance, h1_last_swing_high/low}, eligible_horizons [str],
            horizon_assessments [{horizon, confidence, reasoning}],
            v10_action, v10_rejection_stage, v10_selected_horizon
        """
        root = str(ctx.get("canonical_opportunity_id", "") or "")
        if not root:
            return  # rule: no simulation without a canonical root
        if root in self._planned_roots:
            return  # one PLAN per opportunity-cycle

        symbol = str(ctx.get("symbol", ""))
        bar_time_raw = int(ctx.get("bar_time_raw", 0))
        off = get_broker_offset_seconds()
        direction = str(ctx.get("direction", "") or "").upper()
        if direction not in ("BUY", "SELL") or not symbol or bar_time_raw <= 0:
            return

        eligible = set(ctx.get("eligible_horizons", []) or [])
        assessments = {
            str(a.get("horizon", "")).upper(): a
            for a in (ctx.get("horizon_assessments", []) or [])
        }
        structure = ctx.get("structure", {}) or {}
        plan_id = f"nplan_{ctx.get('cycle_id', 0)}_{symbol}_{bar_time_raw}"

        entries: list[dict[str, Any]] = []
        constructed: list[dict[str, Any]] = []
        for hz in HORIZONS:
            if hz not in eligible:
                a = assessments.get(hz, {})
                entries.append(
                    {
                        "horizon": hz,
                        "state": "NOT_ELIGIBLE",
                        "confidence": a.get("confidence"),
                        "reasoning": a.get("reasoning", ""),
                    }
                )
                continue

            from core.horizon.horizon_trade_builder import build_horizon_trade

            trade = build_horizon_trade(
                horizon=hz,
                symbol=symbol,
                direction=direction,
                entry_price=float(ctx.get("ask" if direction == "BUY" else "bid", 0.0)),
                m5_candle_high=structure.get("m5_candle_high"),
                m5_candle_low=structure.get("m5_candle_low"),
                m15_nearest_support=structure.get("m15_nearest_support"),
                m15_nearest_resistance=structure.get("m15_nearest_resistance"),
                h1_last_swing_high=structure.get("h1_last_swing_high"),
                h1_last_swing_low=structure.get("h1_last_swing_low"),
            )
            if trade is None:
                from core.horizon.horizon_trade_builder import horizon_missing_inputs

                missing = horizon_missing_inputs(
                    hz,
                    direction,
                    m5_candle_high=structure.get("m5_candle_high"),
                    m5_candle_low=structure.get("m5_candle_low"),
                    m15_nearest_support=structure.get("m15_nearest_support"),
                    m15_nearest_resistance=structure.get("m15_nearest_resistance"),
                    h1_last_swing_high=structure.get("h1_last_swing_high"),
                    h1_last_swing_low=structure.get("h1_last_swing_low"),
                )
                entries.append(
                    {
                        "horizon": hz,
                        "state": "ELIGIBLE_BUT_UNCONSTRUCTIBLE",
                        "missing_structure": missing,
                    }
                )
                continue

            entries.append({"horizon": hz, "state": "CONSTRUCTED"})
            constructed.append({"horizon": hz, "trade": trade})

        # ─── PLAN event — always written, even when nothing constructs ────
        plan_ev = self._envelope(
            event_type="PLAN",
            symbol=symbol,
            market_time_raw=bar_time_raw,
            broker_offset=off,
            canonical_opportunity_id=root,
        )
        plan_ev.update(
            {
                "plan_id": plan_id,
                "cycle_id": ctx.get("cycle_id", 0),
                "entity_id": ctx.get("entity_id", ""),
                "direction": direction,
                "entry_price_basis": "ASK" if direction == "BUY" else "BID",
                "horizons": entries,
                "constructed_count": len(constructed),
            }
        )
        self._write(plan_ev)
        self._planned_roots.add(root)
        self._open_constructed(
            ctx=ctx,
            symbol=symbol,
            bar_time_raw=bar_time_raw,
            off=off,
            direction=direction,
            plan_id=plan_id,
            constructed=constructed,
        )

    def _open_constructed(
        self,
        *,
        ctx: dict[str, Any],
        symbol: str,
        bar_time_raw: int,
        off: int,
        direction: str,
        plan_id: str,
        constructed: list[dict[str, Any]],
    ) -> None:
        """Write one immutable OPEN per constructed horizon and activate it."""
        pip = 0.01 if "JPY" in symbol.upper() else 0.0001
        selected_hz = str(ctx.get("v10_selected_horizon", "") or "").upper()
        root = str(ctx.get("canonical_opportunity_id", "") or "")
        for item in constructed:
            hz = item["horizon"]
            t = item["trade"]
            trade_id = f"nshadow_{ctx.get('cycle_id', 0)}_{symbol}_{hz}"
            risk_distance = abs(t.entry - t.stop_loss)
            basis = "ASK" if direction == "BUY" else "BID"
            assumptions = build_assumptions(
                horizon=hz,
                entry_price_basis=basis,
                checkpoint_interval=DEFAULT_CHECKPOINT_INTERVAL,
            )
            lifecycle = LifecycleState(
                max_favourable_price=t.entry,
                max_adverse_price=t.entry,
                last_evaluated_bar_time=bar_time_raw,  # entry bar itself is never evaluated
            )

            ev = self._envelope(
                event_type="OPEN",
                symbol=symbol,
                market_time_raw=bar_time_raw,
                broker_offset=off,
                canonical_opportunity_id=root,
                shadow_trade_id=trade_id,
            )
            ev.update(
                {
                    "plan_id": plan_id,
                    # Phase 3 Step 10-A: top-level basis is populated from the
                    # SAME source of truth used by simulation_assumptions and
                    # market_entry_facts below (direction-derived fill basis),
                    # never null while nested copies carry the value.
                    "entry_price_basis": basis,
                    "identity": {
                        "entity_id": ctx.get("entity_id", ""),
                        "cycle_id": ctx.get("cycle_id", 0),
                        "trade_horizon": hz,
                        "evaluated_horizon": hz,
                        # Phase 3 Step 10-C: provenance distinguishes the
                        # primary-selected horizon's simulation from alternative
                        # horizon simulations instead of labelling both
                        # HORIZON_ALTERNATIVE. Derived ONLY from the inherited
                        # v10_selected_horizon fact — no semantic invention.
                        "shadow_type": (
                            "PRIMARY_HORIZON_SIMULATION"
                            if hz == selected_hz else "HORIZON_ALTERNATIVE"
                        ),
                    },
                    # LIVE FACTS — observations about the live runtime.
                    # NOT shadow decisions. Never modified by this runtime.
                    "live_facts": {
                        "v10_action": ctx.get("v10_action", ""),
                        "v10_rejection_stage": ctx.get("v10_rejection_stage", ""),
                        "v10_selected_horizon": ctx.get("v10_selected_horizon", ""),
                        "horizon_selection_status": (
                            "PRIMARY_SELECTED" if hz == selected_hz else "ALTERNATIVE"
                        ),
                        "pattern": ctx.get("pattern", ""),
                        "strategy": ctx.get("strategy", ""),
                        "score": ctx.get("score", 0.0),
                        "regime": ctx.get("regime", ""),
                        "h4_regime": ctx.get("h4_regime", ""),
                        "h1_bias": ctx.get("h1_bias", ""),
                        "market_phase": ctx.get("market_phase", ""),
                        "market_phase_confidence": ctx.get("market_phase_confidence", 0.0),
                    },
                    "construction": {
                        "direction": direction,
                        "entry_price": t.entry,
                        "stop_loss": t.stop_loss,
                        "take_profit": t.take_profit,
                        "risk_distance": risk_distance,
                        "risk_pips": round(risk_distance / pip, 2) if pip else 0.0,
                        "intended_rr": t.rr,
                        "sl_source": t.sl_source,
                        "tp_construction_rule": (t.reasoning[-1] if t.reasoning else ""),
                        "sl_construction_rule": (t.reasoning[0] if t.reasoning else ""),
                        "reasoning": list(t.reasoning),
                        "structure_inputs": dict(ctx.get("structure", {}) or {}),
                    },
                    "market_entry_facts": {
                        "bid_at_entry": ctx.get("bid", 0.0),
                        "ask_at_entry": ctx.get("ask", 0.0),
                        "spread_at_entry": round(
                            float(ctx.get("ask", 0.0)) - float(ctx.get("bid", 0.0)), 8
                        ),
                        "entry_price": t.entry,
                        "entry_price_basis": basis,
                    },
                    "simulation_assumptions": assumptions,
                    "lifecycle_initial": lifecycle.to_dict(),
                }
            )
            ev.update(market_block("opportunity_market_time", bar_time_raw, off))
            ev.update(market_block("entry_market_time", bar_time_raw, off))
            self._write(ev)

            self._active[trade_id] = {
                "trade_id": trade_id,
                "canonical_opportunity_id": root,
                "definition": ev,
                "lifecycle": lifecycle,
                "timeout_bars": assumptions["timeout_bars"],
                "checkpoint_interval": assumptions["checkpoint_interval_bars"],
                "direction": direction,
                "entry_price": t.entry,
                "stop_loss": t.stop_loss,
                "take_profit": t.take_profit,
                "pip": pip,
            }

    # ─────────────────────────────────────────────────────────────────────
    # CLOSED-BAR EVALUATION (authoritative lifecycle transitions)
    # ─────────────────────────────────────────────────────────────────────

    def evaluate_bar(
        self,
        *,
        symbol: str,
        bar_time: int,
        bar_high: float,
        bar_low: float,
        bar_close: float,
        bar_index: int = 0,
    ) -> None:
        """
        Evaluate every ACTIVE simulation for `symbol` against one authoritative
        closed M5 bar. At-most-once per (shadow_trade_id, bar_time) via the
        durable watermark. Never fabricates missed bars (DATA_GAP instead).
        """
        for trade_id, sim in list(self._active.items()):
            if sim["definition"]["symbol"] != symbol:
                continue

            lc: LifecycleState = sim["lifecycle"]
            watermark = int(lc.last_evaluated_bar_time)
            if bar_time <= watermark:
                continue  # stale / duplicate delivery — exactly-once guard

            direction = sim["direction"]
            entry = sim["entry_price"]
            sl = sim["stop_loss"]
            tp = sim["take_profit"]

            # ─── DATA_GAP honesty: never fabricate skipped bars ───────────
            gap = bar_time - watermark
            if gap > M5_BAR_INTERVAL_S * 1.5:
                lc.data_gaps.append(
                    {
                        "from_market_time": watermark,
                        "to_market_time": bar_time,
                        "missing_bars_estimate": int(round(gap / M5_BAR_INTERVAL_S)) - 1,
                    }
                )

            # ─── Forward-only progression ──────────────────────────────────
            lc.bars_elapsed += 1
            if direction == "BUY":
                lc.max_favourable_price = max(lc.max_favourable_price, bar_high)
                lc.max_adverse_price = min(lc.max_adverse_price, bar_low)
            else:
                lc.max_favourable_price = min(lc.max_favourable_price, bar_low)
                lc.max_adverse_price = max(lc.max_adverse_price, bar_high)

            running_r = compute_r_multiple(
                direction=direction,
                entry_price=entry,
                exit_price=bar_close,
                stop_loss=sl,
            )
            lc.state_log.append({"bar": lc.bars_elapsed, "r": running_r, "close": bar_close})
            lc.last_evaluated_bar_time = int(bar_time)

            # ─── Exit evaluation — exact fill, SL_FIRST, then timeout ─────
            exit_price: float | None = None
            exit_reason = ""
            if direction == "BUY":
                if bar_low <= sl:
                    exit_price, exit_reason = sl, EXIT_STOP_LOSS
                elif bar_high >= tp:
                    exit_price, exit_reason = tp, EXIT_TAKE_PROFIT
            else:
                if bar_high >= sl:
                    exit_price, exit_reason = sl, EXIT_STOP_LOSS
                elif bar_low <= tp:
                    exit_price, exit_reason = tp, EXIT_TAKE_PROFIT

            if exit_price is None and lc.bars_elapsed >= sim["timeout_bars"]:
                exit_price, exit_reason = bar_close, EXIT_TIMEOUT

            if exit_price is not None:
                self._close(sim=sim, exit_price=exit_price, exit_reason=exit_reason,
                            exit_market_time=int(bar_time), exit_bar_index=bar_index)
                self._active.pop(trade_id, None)
            elif (
                lc.bars_elapsed % sim["checkpoint_interval"] == 0
                or len(lc.data_gaps) > sim.get("persisted_gap_count", 0)
            ):
                self._progress(sim=sim)
                sim["persisted_gap_count"] = len(lc.data_gaps)

    def _progress(self, *, sim: dict[str, Any]) -> None:
        """Periodic checkpoint (contract §19)."""
        ev = self._envelope(
            event_type="PROGRESS",
            symbol=sim["definition"]["symbol"],
            market_time_raw=sim["lifecycle"].last_evaluated_bar_time,
            broker_offset=get_broker_offset_seconds(),
            canonical_opportunity_id=sim["canonical_opportunity_id"],
            shadow_trade_id=sim["trade_id"],
        )
        ev.update({"lifecycle": sim["lifecycle"].to_dict()})
        self._write(ev)

    def _close(
        self,
        *,
        sim: dict[str, Any],
        exit_price: float,
        exit_reason: str,
        exit_market_time: int,
        exit_bar_index: int,
    ) -> None:
        """CLOSE event: complete outcome + full progression (contract §21)."""
        from core.clock import utc_ms, utc_ms_to_iso

        lc: LifecycleState = sim["lifecycle"]
        d = sim["definition"]["construction"]
        off = get_broker_offset_seconds()

        pnl_r = compute_r_multiple(
            direction=sim["direction"],
            entry_price=sim["entry_price"],
            exit_price=exit_price,
            stop_loss=sim["stop_loss"],
        )
        mfe_r = compute_mfe_r(
            direction=sim["direction"],
            entry_price=sim["entry_price"],
            max_favourable_price=lc.max_favourable_price,
            stop_loss=sim["stop_loss"],
        )
        mae_r = compute_mae_r(
            direction=sim["direction"],
            entry_price=sim["entry_price"],
            max_adverse_price=lc.max_adverse_price,
            stop_loss=sim["stop_loss"],
        )

        ms = utc_ms()
        ev = self._envelope(
            event_type="CLOSE",
            symbol=sim["definition"]["symbol"],
            market_time_raw=exit_market_time,
            broker_offset=off,
            canonical_opportunity_id=sim["canonical_opportunity_id"],
            shadow_trade_id=sim["trade_id"],
        )
        ev.update(market_block("exit_market_time", exit_market_time, off))
        ev.update(
            {
                "exit_price": exit_price,
                "exit_reason": exit_reason,
                "exit_bar_index": exit_bar_index,
                "bars_held": lc.bars_elapsed,
                "closed_at_utc_ms": ms,
                "closed_at_utc": utc_ms_to_iso(ms),
                "outcome": {
                    "pnl_r_multiple": round(pnl_r, 4),
                    "mfe_r": round(mfe_r, 4),
                    "mae_r": round(mae_r, 4),
                    "risk_distance": d.get("risk_distance"),
                    "intended_rr": d.get("intended_rr"),
                },
                "trade_state_progression": list(lc.state_log),
                "data_gaps": list(lc.data_gaps),
                "final_lifecycle": {
                    "max_favourable_price": lc.max_favourable_price,
                    "max_adverse_price": lc.max_adverse_price,
                    "last_evaluated_bar_time": lc.last_evaluated_bar_time,
                },
            }
        )
        self._write(ev)

    # ─────────────────────────────────────────────────────────────────────
    # RECOVERY (NEW domain only) + introspection
    # ─────────────────────────────────────────────────────────────────────

    def recover(self) -> None:
        """
        Rebuild ACTIVE simulations exclusively from the NEW Shadow stream:

            OPEN (immutable definition) + latest PROGRESS + no CLOSE ⇒ ACTIVE

        Never reads legacy shadow datasets, live positions, or broker tickets.
        """
        self._active.clear()
        closed: set[str] = set()
        for ev in load_events(self._writer.base_dir):
            et = ev.get("event_type")
            tid = str(ev.get("shadow_trade_id", "") or "")
            if not tid:
                if et == "PLAN":
                    root = str(ev.get("canonical_opportunity_id", "") or "")
                    if root:
                        self._planned_roots.add(root)
                continue
            if et == "OPEN":
                if tid in closed:
                    continue
                init = LifecycleState.from_dict(ev.get("lifecycle_initial", {}))
                assumptions = ev.get("simulation_assumptions", {})
                cons = ev.get("construction", {})
                self._active[tid] = {
                    "trade_id": tid,
                    "canonical_opportunity_id": ev.get("canonical_opportunity_id", ""),
                    "definition": ev,
                    "lifecycle": init,
                    "timeout_bars": int(assumptions.get("timeout_bars", 60)),
                    "checkpoint_interval": int(
                        assumptions.get(
                            "checkpoint_interval_bars", DEFAULT_CHECKPOINT_INTERVAL
                        )
                    ),
                    "direction": cons.get("direction", ""),
                    "entry_price": float(cons.get("entry_price", 0.0)),
                    "stop_loss": float(cons.get("stop_loss", 0.0)),
                    "take_profit": float(cons.get("take_profit", 0.0)),
                    "pip": 0.01 if "JPY" in str(ev.get("symbol", "")).upper() else 0.0001,
                }
            elif et == "PROGRESS":
                sim = self._active.get(tid)
                if sim is None:
                    continue
                lcd = ev.get("lifecycle", {})
                restored = LifecycleState.from_dict(lcd)
                for g in lcd.get("data_gaps", []):
                    if g not in restored.data_gaps:
                        restored.data_gaps.append(g)
                sim["lifecycle"] = restored
            elif et == "CLOSE":
                self._active.pop(tid, None)
                closed.add(tid)

    def snapshot(self, shadow_trade_id: str) -> dict[str, Any] | None:
        """Introspection helper (tests/diagnostics). Read-only view of live state."""
        sim = self._active.get(shadow_trade_id)
        if sim is None:
            return None
        return {
            "trade_id": sim["trade_id"],
            "canonical_opportunity_id": sim["canonical_opportunity_id"],
            "lifecycle": sim["lifecycle"].to_dict(),
            "timeout_bars": sim["timeout_bars"],
            "direction": sim["direction"],
            "entry_price": sim["entry_price"],
            "stop_loss": sim["stop_loss"],
            "take_profit": sim["take_profit"],
        }

    def active_ids(self) -> list[str]:
        return list(self._active.keys())


_runtime: ShadowRuntime | None = None


def get_shadow_runtime() -> ShadowRuntime:
    """Process-wide singleton."""
    global _runtime
    if _runtime is None:
        _runtime = ShadowRuntime()
    return _runtime
