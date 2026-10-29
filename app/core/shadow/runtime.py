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

import hashlib
import logging
from typing import Any, Mapping

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
    MARKET_TIMESTAMP_NORMALIZATION_VERSION,
    MARKET_TIMESTAMP_SEMANTICS,
    SCHEMA_VERSION,
    SIMULATION_MODEL_VERSION,
    LifecycleState,
    utc_market_block,
)
from core.shadow.persistence import (
    ShadowEventWriter,
    get_broker_offset_seconds,
    load_events,
)
from core.shadow.observability import (
    assign_experiment_arm,
    build_decision_snapshot,
    build_lifecycle_m5_path,
    build_market_time_attestation,
    m5_bar_entry,
)
from core.observability_contract import (
    DATASET_SHADOW_RUNTIME,
    ObservabilityContractError,
    assert_emission_contract,
    assert_producer_contract,
    build_record_lineage,
)
from core.trade_truth import compute_mae_r, compute_mfe_r, compute_r_multiple


def shadow_arm_enabled() -> bool:
    """Whether ROOT-05 arm assignment is active (config-gated, default ON).

    Assignment is on by default because an UNASSIGNED arm is a governance
    failure, not a neutral state: L7 fails closed without it.  Operators may
    disable it explicitly, in which case every arm is recorded as
    UNASSIGNED with a reason rather than silently omitted.
    """
    try:
        from core import config as _cfg

        return bool(getattr(_cfg, "SHADOW_EXPERIMENT_ARM_ENABLED", True))
    except Exception:
        return True

logger = logging.getLogger(__name__)

#: Recovery fail-closed reason for a persisted legacy OPEN that carries no
#: ``record_lineage`` at all.  This is the single high-volume legacy case (a
#: pre-governance stream can hold tens of thousands of identical OPENs), so it
#: is counted and reported once per recovery pass instead of logged per record.
#: See ``ShadowRuntime.recover``.
_RECOVERY_UNPINNED_REASON = (
    "RECOVERED_LIFECYCLE_UNPINNED:shadow_runtime:"
    "no record_lineage on the persisted OPEN")


def _shadow_trade_id(canonical_opportunity_id: str, horizon: str) -> str:
    """Deterministic, globally-unique shadow lifecycle identity.

    One identity per (canonical_opportunity_id, horizon). Stable across bot
    restarts and event replays because it is derived solely from the canonical
    opportunity and horizon — never from a process-local counter, ``cycle_id``,
    or Python's non-stable ``hash()``. A stable cryptographic digest keeps the
    value collision-free across opportunities that would otherwise share the
    same ``cycle_id``/``symbol``/``horizon`` tuple.
    """
    digest = hashlib.sha256(
        f"{canonical_opportunity_id}::{horizon}".encode("utf-8")
    ).hexdigest()[:16]
    return f"nshadow_{digest}"


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
        # Stage 4: the emitting code must agree with the governed contract
        # BEFORE any record is produced.  Fails closed at construction, so a
        # producer edited without a governed contract change can never reach
        # the serialization boundary at all.
        self._contract = assert_producer_contract(DATASET_SHADOW_RUNTIME)
        self._writer = writer or ShadowEventWriter()
        self._active: dict[str, dict[str, Any]] = {}  # trade_id → sim state
        self._planned_roots: set[str] = set()
        # Stage 4: recovered lifecycles whose persisted metadata identity could
        # not be proven to be the current governed identity.  They are never
        # resumed and never emit; the reason is retained for reporting.
        self._quarantined: dict[str, str] = {}
        self.recover()

    # ─────────────────────────────────────────────────────────────────────
    # ENVELOPE
    # ─────────────────────────────────────────────────────────────────────

    def _envelope(
        self,
        *,
        event_id: str,
        event_type: str,
        symbol: str,
        market_time_utc: int,
        broker_offset: int,
        canonical_opportunity_id: str = "",
        observation_id: str = "",
        shadow_trade_id: str = "",
        horizon: str = "",
        pinned: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        ev: dict[str, Any] = {
            "event_id": event_id,
            "event_type": event_type,
            "schema_version": SCHEMA_VERSION,
            "construction_model_version": CONSTRUCTION_MODEL_VERSION,
            "simulation_model_version": SIMULATION_MODEL_VERSION,
            "canonical_opportunity_id": canonical_opportunity_id,
            "observation_id": observation_id,
            "shadow_trade_id": shadow_trade_id,
            "symbol": symbol,
            "horizon": horizon,
            "broker_offset_seconds": int(broker_offset),
            "market_timestamp_semantics": MARKET_TIMESTAMP_SEMANTICS,
            "market_timestamp_normalization_version": MARKET_TIMESTAMP_NORMALIZATION_VERSION,
        }
        ev.update(utc_market_block("event_market_time", market_time_utc))
        ev.update(_wall_stamp())
        # Stage 4: the governed lineage envelope is attached HERE, from the
        # canonical contract authority (never from producer-local constants),
        # so the stamp and the enforcement in _write() can never be derived
        # from two different truths.
        ev["record_lineage"] = self._lifecycle_lineage(event_type, pinned)
        return ev

    @staticmethod
    def _lifecycle_lineage(
            event_type: str,
            pinned: Mapping[str, Any] | None) -> dict[str, Any]:
        """The governed lineage for one event, pinned to its lifecycle.

        Metadata is resolved per record from the governed contract.  When a
        lifecycle carries a pinned envelope (its own already-persisted OPEN),
        that envelope is reused verbatim, so a generation-2 lifecycle can never
        resume emitting generation-1 metadata and a generation-1 lifecycle is
        never silently rewritten as generation 2.  A pinned identity that no
        longer equals the governed identity fails closed.
        """
        governed = build_record_lineage(
            DATASET_SHADOW_RUNTIME, event_type=event_type)
        if not pinned:
            return governed
        for key in ("dataset", "dataset_version", "producer_version",
                    "evidence_epoch", "collection_start"):
            if str(pinned.get(key, "")) != str(governed.get(key, "")):
                raise ObservabilityContractError(
                    f"LIFECYCLE_LINEAGE_INCOMPATIBLE:{key}:"
                    f"pinned={pinned.get(key)!r}"
                    f"!=governed={governed.get(key)!r}")
        if int(pinned.get("schema_generation", 0) or 0) != \
                int(governed["schema_generation"]):
            raise ObservabilityContractError(
                "LIFECYCLE_LINEAGE_INCOMPATIBLE:schema_generation:"
                f"pinned={pinned.get('schema_generation')!r}"
                f"!=governed={governed['schema_generation']!r}")
        # Reuse the persisted envelope, retagged with this event type.
        return dict(pinned, event_type=str(event_type or ""))

    def _write(self, event: dict[str, Any]) -> bool:
        """
        THE serialization/persistence boundary for every ``shadow_runtime``
        record (PLAN, OPEN, PROGRESS and CLOSE all pass through here).

        The canonical Stage 4 guard runs on the FULL outgoing record BEFORE it
        is serialized or persisted, so "new producer code + wrong/old schema
        metadata" is not representable.  On failure this raises and NOTHING is
        written: the guard is never downgraded to a warning, generation-2
        fields are never stripped, and the metadata is never rewritten.
        """
        _ledger = None
        _obligation = None
        try:
            from core.lifecycle_evidence_obligations import (
                create_dataset_obligation, obligation_ledger,
            )
            _ledger = obligation_ledger()
            _identity = {
                "event_id": str(event.get("event_id") or ""),
                "shadow_trade_id": str(event.get("shadow_trade_id") or ""),
                "canonical_opportunity_id": str(event.get("canonical_opportunity_id") or ""),
                "event_type": str(event.get("event_type") or ""),
                "symbol": str(event.get("symbol") or "UNKNOWN"),
            }
            _obligation = create_dataset_obligation(
                _ledger,
                event_id=f"shadow-runtime:{_identity['event_id'] or 'MISSING'}",
                lifecycle_stage="SHADOW_RUNTIME_EVENT",
                dataset="shadow_runtime", identity=_identity,
                timestamp=str(event.get("event_market_time_utc_iso8601") or ""),
                producer="core.shadow.runtime.ShadowRuntime._write",
                trigger=f"SHADOW_{_identity['event_type']}_EMITTED",
            )
        except Exception as _obligation_exc:
            _ledger = None
            _obligation = None
            logger.error("[LIFECYCLE_OBLIGATION] shadow event creation failed: %s",
                         _obligation_exc)

        try:
            assert_emission_contract(event, dataset=DATASET_SHADOW_RUNTIME)
            persisted = self._writer.append(
                event=event,
                symbol=event.get("symbol", "UNKNOWN"),
                market_time_utc=int(event.get("event_market_time_utc_epoch_s", 0)),
                broker_offset_seconds=int(event.get("broker_offset_seconds", 0)),
            )
            if _ledger is not None and _obligation is not None:
                from core.lifecycle_evidence_obligations import record_producer_outcome
                record_producer_outcome(
                    _ledger, _obligation, succeeded=persisted is not False,
                    observed_record_id=str(event.get("event_id") or "") or None,
                    failure_reason="SHADOW_RUNTIME_LOCAL_WRITE_FAILED",
                    provenance={"local_path_authority": "LOCAL_ONLY"},
                )
            return persisted is not False
        except Exception as exc:
            if _ledger is not None and _obligation is not None:
                try:
                    from core.lifecycle_evidence_obligations import record_producer_outcome
                    record_producer_outcome(
                        _ledger, _obligation, succeeded=False,
                        failure_reason=f"SHADOW_RUNTIME_PRODUCER_EXCEPTION:{type(exc).__name__}",
                    )
                except Exception:
                    pass
            raise

    # ─────────────────────────────────────────────────────────────────────
    # PLAN + OPEN (branch point entry)
    # ─────────────────────────────────────────────────────────────────────

    def handle_opportunity(self, ctx: dict[str, Any]) -> None:
        """
        Plan + open simulations for one canonical opportunity-cycle.

        ctx keys (assembled by core.shadow.integration):
            canonical_opportunity_id, entity_id, symbol, cycle_id,
            bar_time_utc, direction, pattern, strategy, score,
            regime, h4_regime, h1_bias, market_phase, market_phase_confidence,
            bid, ask, structure {m5_candle_high/low, m15_nearest_support/
            resistance, h1_last_swing_high/low}, eligible_horizons [str],
            horizon_assessments [{horizon, confidence, reasoning}],
            v10_action, v10_rejection_stage, v10_selected_horizon
        """
        root = str(ctx.get("canonical_opportunity_id", "") or "")
        if not root:
            return  # rule: no simulation without a canonical root
        observation_id = str(ctx.get("observation_id", "") or "")
        if root in self._planned_roots:
            return  # one PLAN per opportunity-cycle

        symbol = str(ctx.get("symbol", ""))
        bar_time_utc = int(ctx.get("bar_time_utc", 0))
        off = get_broker_offset_seconds()
        direction = str(ctx.get("direction", "") or "").upper()
        has_direction = direction in ("BUY", "SELL")
        if not symbol or bar_time_utc <= 0:
            return

        eligible = set(ctx.get("eligible_horizons", []) or [])
        assessments = {
            str(a.get("horizon", "")).upper(): a
            for a in (ctx.get("horizon_assessments", []) or [])
        }
        structure = ctx.get("structure", {}) or {}
        plan_id = f"nplan_{ctx.get('cycle_id', 0)}_{symbol}_{bar_time_utc}"

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

            if not has_direction:
                entries.append(
                    {
                        "horizon": hz,
                        "state": "ELIGIBLE_BUT_UNCONSTRUCTIBLE",
                        "missing_structure": ["direction"],
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
            event_id=f"PLAN:{plan_id}",
            event_type="PLAN",
            symbol=symbol,
            market_time_utc=bar_time_utc,
            broker_offset=off,
            canonical_opportunity_id=root,
            observation_id=observation_id,
        )
        plan_ev.update(
            {
                "plan_id": plan_id,
                "cycle_id": ctx.get("cycle_id", 0),
                "entity_id": ctx.get("entity_id", ""),
                "direction": direction,
                "entry_price_basis": (
                    "ASK" if direction == "BUY"
                    else "BID" if direction == "SELL"
                    else ""
                ),
                "horizons": entries,
                "constructed_count": len(constructed),
            }
        )
        self._write(plan_ev)
        self._planned_roots.add(root)
        self._open_constructed(
            ctx=ctx,
            symbol=symbol,
            bar_time_utc=bar_time_utc,
            off=off,
            direction=direction,
            plan_id=plan_id,
            constructed=constructed,
            observation_id=observation_id,
        )

    def _open_constructed(
        self,
        *,
        ctx: dict[str, Any],
        symbol: str,
        bar_time_utc: int,
        off: int,
        direction: str,
        plan_id: str,
        constructed: list[dict[str, Any]],
        observation_id: str | None = None,
    ) -> None:
        """Write one immutable OPEN per constructed horizon and activate it."""
        # Compatibility callers may invoke this internal branch helper with the
        # original opportunity context only.  Recover the already-established
        # observation identity from that context; never mint one here.
        if observation_id is None:
            observation_id = str(ctx.get("observation_id", "") or "")
        pip = 0.01 if "JPY" in symbol.upper() else 0.0001
        selected_hz = str(ctx.get("v10_selected_horizon", "") or "").upper()
        root = str(ctx.get("canonical_opportunity_id", "") or "")
        for item in constructed:
            hz = item["horizon"]
            t = item["trade"]
            trade_id = _shadow_trade_id(root, hz)
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
                last_evaluated_bar_time=bar_time_utc,  # entry bar itself is never evaluated
            )

            ev = self._envelope(
                event_id=f"{trade_id}:OPEN",
                event_type="OPEN",
                symbol=symbol,
                market_time_utc=bar_time_utc,
                broker_offset=off,
                canonical_opportunity_id=root,
                observation_id=observation_id,
                shadow_trade_id=trade_id,
                horizon=hz,
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
            ev.update(utc_market_block("opportunity_market_time", bar_time_utc))
            ev.update(utc_market_block("entry_market_time", bar_time_utc))

            # ─── ROOT CHANGES 02/03/05 — lifecycle-bound evidence ────────
            # Recorded ONCE, here at OPEN, and then frozen: the decision
            # instant is the only moment at which these facts are known
            # without look-ahead.  Each block is additive; the legacy
            # live_facts/construction blocks above are left untouched so
            # existing consumers keep byte-identical inputs.
            ev["decision_snapshot"] = build_decision_snapshot(
                ctx,
                shadow_trade_id=trade_id,
                canonical_opportunity_id=root,
                trade_horizon=hz,
                decision_market_time_utc=bar_time_utc,
            )
            ev["market_time_attestation"] = build_market_time_attestation(
                {
                    "event_market_time": ev.get("event_market_time"),
                    "opportunity_market_time": ev.get(
                        "opportunity_market_time"),
                    "entry_market_time": ev.get("entry_market_time"),
                },
                broker_offset_seconds=off,
            )
            ev["experiment_arm"] = assign_experiment_arm(
                canonical_opportunity_id=root,
                trade_horizon=hz,
                # The arm is bound to the exact lifecycle and stamped with the
                # decision instant, so L7 can prove the assignment preceded any
                # outcome knowledge rather than merely asserting it.
                shadow_trade_id=trade_id,
                decision_market_time_utc=bar_time_utc,
                enabled=shadow_arm_enabled(),
            )
            persisted = self._write(ev)
            if persisted:
                try:
                    from core.shadow.integration import bind_candidate_from_shadow_open
                    bind_candidate_from_shadow_open(ev)
                except Exception:
                    logger.exception("[SHADOW_CANDIDATE_OPEN_HOOK_ISOLATED]")

            self._active[trade_id] = {
                "trade_id": trade_id,
                "canonical_opportunity_id": root,
                "observation_id": observation_id,
                "definition": ev,
                "lifecycle": lifecycle,
                "timeout_bars": assumptions["timeout_bars"],
                "checkpoint_interval": assumptions["checkpoint_interval_bars"],
                "direction": direction,
                "entry_price": t.entry,
                "stop_loss": t.stop_loss,
                "take_profit": t.take_profit,
                "pip": pip,
                "horizon": hz,
                # The lifecycle's governed metadata identity, pinned at OPEN
                # and reused verbatim by every later event of this lifecycle.
                "record_lineage": ev["record_lineage"],
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
        bar_open: float | None = None,
    ) -> None:
        """
        Evaluate every ACTIVE simulation for `symbol` against one authoritative
        closed M5 bar. At-most-once per (shadow_trade_id, bar_time) via the
        durable watermark. Never fabricates missed bars (DATA_GAP instead).

        ``bar_open`` is optional so existing callers keep working, but the
        ROOT-04 lifecycle path is only OHLC-complete when the feed supplies
        it.  A missing open is recorded as an explicit degradation, never
        silently filled.
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

            # ─── ROOT-04: bind this M5 bar to THIS lifecycle ──────────────
            # The bar the runtime actually evaluated is recorded with its
            # full OHLC, so the EX2 exit path is producer-authored rather
            # than reconstructed later from a (symbol, ts) range join.
            lc.m5_path.append(
                m5_bar_entry(
                    bar_time_utc=int(bar_time),
                    bar_open=bar_open,
                    bar_high=bar_high,
                    bar_low=bar_low,
                    bar_close=bar_close,
                    bar_index=int(bar_index),
                )
            )

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
            event_id=(f"{sim['trade_id']}:PROGRESS:{sim['lifecycle'].bars_elapsed}:"
                      f"{sim['lifecycle'].last_evaluated_bar_time}"),
            event_type="PROGRESS",
            symbol=sim["definition"]["symbol"],
            market_time_utc=sim["lifecycle"].last_evaluated_bar_time,
            broker_offset=get_broker_offset_seconds(),
            canonical_opportunity_id=sim["canonical_opportunity_id"],
            observation_id=sim.get("observation_id", ""),
            shadow_trade_id=sim["trade_id"],
            horizon=sim["horizon"],
            pinned=sim.get("record_lineage"),
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
            event_id=f"{sim['trade_id']}:CLOSE",
            event_type="CLOSE",
            symbol=sim["definition"]["symbol"],
            market_time_utc=exit_market_time,
            broker_offset=off,
            canonical_opportunity_id=sim["canonical_opportunity_id"],
            observation_id=sim.get("observation_id", ""),
            shadow_trade_id=sim["trade_id"],
            horizon=sim["horizon"],
            pinned=sim.get("record_lineage"),
        )
        ev.update(utc_market_block("exit_market_time", exit_market_time))
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
                # ─── ROOT-04: the lifecycle-bound ordered M5 OHLC path ────
                "lifecycle_m5_path": build_lifecycle_m5_path(
                    lc.m5_path,
                    shadow_trade_id=sim["trade_id"],
                    canonical_opportunity_id=sim["canonical_opportunity_id"],
                    trade_horizon=sim["horizon"],
                    entry_market_time_utc=int(
                        sim["definition"].get("entry_market_time_utc_epoch_s", 0)
                    ),
                    exit_market_time_utc=int(exit_market_time),
                    symbol=str(sim["definition"].get("symbol", "")),
                ),
                "market_time_attestation": build_market_time_attestation(
                    {
                        "event_market_time": ev.get("event_market_time"),
                        "exit_market_time": exit_market_time,
                    },
                    broker_offset_seconds=off,
                ),
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
        self._quarantined.clear()
        closed: set[str] = set()
        legacy_unpinned_count = 0
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
                identity = ev.get("identity", {})
                # Stage 4 recovery: the lifecycle's governed metadata identity
                # is PINNED from its own persisted OPEN, never re-derived from
                # the current contract.  A lifecycle whose persisted identity
                # cannot be proven to BE the current governed identity is
                # QUARANTINED: it is not resumed and nothing is ever emitted
                # for it.  That is the fail-closed outcome -- an older
                # generation is never silently rewritten as generation 2, and a
                # generation-2 lifecycle never resumes generation-1 metadata.
                # Historical generation-1 records stay READABLE (recovery still
                # replays the whole stream); they simply cannot produce new
                # records, which is exactly the "new code + old metadata" ban.
                pinned = ev.get("record_lineage")
                if not isinstance(pinned, dict):
                    # Legacy generation-1 OPEN with no pinned lineage.  The
                    # fail-closed outcome is unchanged -- still quarantined,
                    # still never resumed -- but this is the one repeated case
                    # that can number in the tens of thousands, so the
                    # per-record error is suppressed and the volume is reported
                    # once, after this recovery pass completes.
                    self._quarantined[tid] = _RECOVERY_UNPINNED_REASON
                    legacy_unpinned_count += 1
                    continue
                try:
                    self._lifecycle_lineage("OPEN", pinned)
                except ObservabilityContractError as exc:
                    self._quarantined[tid] = str(exc)
                    logger.error(
                        "[SHADOW_RUNTIME_RECOVERY_QUARANTINE] %s", exc)
                    continue
                self._active[tid] = {
                    "trade_id": tid,
                    "canonical_opportunity_id": ev.get("canonical_opportunity_id", ""),
                    "observation_id": ev.get("observation_id", ""),
                    "definition": ev,
                    "record_lineage": pinned,
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
                    # OPEN owns the horizon.  The nested identity fallback is
                    # explicit persisted evidence for pre-repair OPEN records,
                    # not a timing/symbol heuristic.
                    "horizon": str(
                        ev.get("horizon")
                        or identity.get("evaluated_horizon")
                        or identity.get("trade_horizon")
                        or ""
                    ),
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
        if legacy_unpinned_count:
            logger.warning(
                "[SHADOW_RUNTIME_RECOVERY_QUARANTINE_SUMMARY] "
                "legacy_unpinned_open_count=%d",
                legacy_unpinned_count)

    def snapshot(self, shadow_trade_id: str) -> dict[str, Any] | None:
        """Introspection helper (tests/diagnostics). Read-only view of live state."""
        sim = self._active.get(shadow_trade_id)
        if sim is None:
            return None
        return {
            "trade_id": sim["trade_id"],
            "canonical_opportunity_id": sim["canonical_opportunity_id"],
            "observation_id": sim.get("observation_id", ""),
            "lifecycle": sim["lifecycle"].to_dict(),
            "timeout_bars": sim["timeout_bars"],
            "direction": sim["direction"],
            "entry_price": sim["entry_price"],
            "stop_loss": sim["stop_loss"],
            "take_profit": sim["take_profit"],
            "horizon": sim["horizon"],
        }

    def active_ids(self) -> list[str]:
        return list(self._active.keys())

    def quarantined_ids(self) -> dict[str, str]:
        """Recovered lifecycles refused resumption, with their fail-closed
        reason.  Reporting/introspection only; these emit nothing."""
        return dict(self._quarantined)


_runtime: ShadowRuntime | None = None


def get_shadow_runtime() -> ShadowRuntime:
    """Process-wide singleton."""
    global _runtime
    if _runtime is None:
        _runtime = ShadowRuntime()
    return _runtime
