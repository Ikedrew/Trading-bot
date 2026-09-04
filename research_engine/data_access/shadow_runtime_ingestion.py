"""
Shadow Runtime Ingestion — canonical production shadow source for the Research Engine.

Data flow (one direction, no fallbacks):

    S3 shadow_runtime_v1  (canonical production shadow event stream, bucket
        trading-bot-v10-data, prefix supporting/shadow_runtime, dataset name
        "shadow_runtime" in the production data contract)
        → lifecycle reconstruction / normalisation  (THIS module)
        → existing internal research shadow shape   (shadow_trades_v1 domains:
          identity / decision_snapshot / simulated_outcome)
        → active shadow universes / consumers

Rules enforced here:
    - shadow_runtime_v1 is the AUTHORITATIVE production shadow source. The
      legacy ``shadow_trades`` dataset and local ``logs/shadow_trades`` are
      NEVER read for active shadow research.
    - Only COMPLETED lifecycles become shadow outcomes: a record is emitted
      only when the stream contains an OPEN (immutable construction) AND a
      CLOSE (final outcome) for the same canonical ``nshadow_*`` shadow_trade_id.
      OPEN/PROGRESS without CLOSE never becomes an outcome; CLOSE without OPEN
      is unpairable and is excluded with explicit accounting.
    - S3 read failures raise ResearchDataSourceError — never a silent local
      fallback. An empty S3 scope is logged explicitly as a collection gap,
      never silently reported as a successful empty universe.
    - Canonical runtime IDs (``nshadow_*``) are accepted verbatim; no ID is
      regenerated and timestamps never replace lineage joins.

Field mapping (canonical runtime → internal research shape), preserving:
    shadow_trade_id, plan_id, observation_id, canonical_opportunity_id,
    symbol, horizon, direction, entry/stop/target, close timestamp,
    pnl_r_multiple, mfe_r, mae_r.

This module is:
    - READ ONLY (never modifies source data)
    - RESEARCH-SIDE (no production imports beyond the shared data contract)
    - DETERMINISTIC (same S3 stream → same records, stable ordering)
"""

from __future__ import annotations

import logging
from typing import Any

from core.production_data_contract import current_schema

logger = logging.getLogger(__name__)

# Production-contract dataset holding the canonical shadow event stream.
_RUNTIME_DATASET = "shadow_runtime"
_RUNTIME_SCHEMA_VERSION = current_schema("shadow_runtime")  # shadow_runtime_v1

# Internal research shape consumers already expect (identity /
# decision_snapshot / simulated_outcome). Emitted records are tagged with
# this schema so existing schema gates keep working unchanged.
_RESEARCH_SCHEMA_VERSION = current_schema("shadow_trades")  # shadow_trades_v1

# Canonical runtime-minted shadow trade IDs.
_VALID_TRADE_ID_PREFIX = "nshadow_"

# shadow_runtime_v1 exit vocabulary → internal research shape vocabulary.
# The internal shape (and every existing consumer: universe populations,
# linker exit-reason maps, reports) uses "max_bars_timeout" for the
# horizon-timeout exit; the runtime stream emits "timeout".
_EXIT_REASON_MAP = {
    "stop_loss": "stop_loss",
    "take_profit": "take_profit",
    "timeout": "max_bars_timeout",
}


def load_shadow_runtime_events(
    *,
    symbol: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[dict[str, Any]]:
    """Load canonical shadow_runtime_v1 events from S3 via the shared layer.

    Errors surface as ResearchDataSourceError (no local fallback). An empty
    result means the requested scope genuinely has no objects in S3.
    """
    from research_engine.data_access.s3_source import get_default_source

    return get_default_source().read_dataset(
        _RUNTIME_DATASET,
        symbol=symbol,
        start_date=start_date,
        end_date=end_date,
    )


def reconstruct_completed_shadow_trades(
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Reconstruct completed shadow trades from the shadow_runtime_v1 event stream.

    A lifecycle is COMPLETED only when it has:
        - an OPEN event (immutable construction + identity + live facts), and
        - a CLOSE event (final outcome with pnl_r_multiple).

    PLAN and PROGRESS events participate in lifecycle accounting only; they
    never by themselves produce an outcome record. Incomplete lifecycles are
    counted and logged — they NEVER become completed shadow outcomes.
    """
    opens: dict[str, dict[str, Any]] = {}
    closes: dict[str, dict[str, Any]] = {}
    plans = 0
    progresses = 0
    bad_schema = 0
    close_without_open: set[str] = set()

    for ev in events:
        if not isinstance(ev, dict):
            bad_schema += 1
            continue
        if ev.get("schema_version") != _RUNTIME_SCHEMA_VERSION:
            bad_schema += 1
            continue
        event_type = ev.get("event_type")
        trade_id = str(ev.get("shadow_trade_id", "") or "")
        if event_type == "PLAN":
            plans += 1
        elif event_type == "PROGRESS":
            progresses += 1
        elif event_type == "OPEN":
            if not trade_id.startswith(_VALID_TRADE_ID_PREFIX):
                bad_schema += 1  # non-canonical ID — never reclassified
            elif trade_id not in opens:
                opens[trade_id] = ev  # first OPEN wins (append-only stream)
        elif event_type == "CLOSE":
            if not trade_id.startswith(_VALID_TRADE_ID_PREFIX):
                bad_schema += 1
            else:
                closes[trade_id] = ev  # last CLOSE wins
                if trade_id not in opens:
                    close_without_open.add(trade_id)

    records: list[dict[str, Any]] = []
    incomplete_no_close = 0
    incomplete_no_outcome = 0
    for trade_id, open_ev in opens.items():
        close_ev = closes.get(trade_id)
        if close_ev is None:
            incomplete_no_close += 1
            continue
        rec = _map_to_research_record(open_ev, close_ev)
        if rec is None:
            incomplete_no_outcome += 1
            continue
        records.append(rec)

    records.sort(key=lambda r: r["identity"]["shadow_trade_id"])

    logger.info(
        "[SHADOW_INGESTION] shadow_runtime_v1 stream: plans=%d opens=%d "
        "progress=%d closes=%d → completed=%d (incomplete: no_close=%d "
        "no_outcome=%d close_without_open=%d bad_schema=%d)",
        plans, len(opens), progresses, len(closes), len(records),
        incomplete_no_close, incomplete_no_outcome, len(close_without_open),
        bad_schema,
    )
    return records


def ingest_completed_shadow_trades(
    *,
    symbol: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[dict[str, Any]]:
    """
    Canonical production shadow population for active shadow research.

    Reads shadow_runtime_v1 from S3, reconstructs completed lifecycles, and
    returns them in the existing internal research shape. Never falls back to
    the legacy shadow_trades dataset or to local logs.
    """
    events = load_shadow_runtime_events(
        symbol=symbol, start_date=start_date, end_date=end_date,
    )
    if not events:
        # Explicit, visible collection-gap accounting — an empty canonical
        # source must never silently masquerade as a successful empty universe.
        logger.warning(
            "[SHADOW_INGESTION] canonical S3 dataset '%s' (%s) returned NO "
            "events for scope symbol=%s range=%s..%s — collection gap, not a "
            "successful empty universe",
            _RUNTIME_DATASET, _RUNTIME_SCHEMA_VERSION,
            symbol or "*", start_date or "-", end_date or "-",
        )
        return []
    return reconstruct_completed_shadow_trades(events)


# ─── canonical runtime event → internal research shape ──────────────────────


def _map_to_research_record(
    open_ev: dict[str, Any],
    close_ev: dict[str, Any],
) -> dict[str, Any] | None:
    """
    Map one completed runtime lifecycle (OPEN + CLOSE) into the existing
    internal research shadow shape (identity / decision_snapshot /
    simulated_outcome). Returns None when the outcome evidence is incomplete.
    """
    construction = open_ev.get("construction") or {}
    open_identity = open_ev.get("identity") or {}
    live_facts = open_ev.get("live_facts") or {}
    entry_facts = open_ev.get("market_entry_facts") or {}
    outcome = close_ev.get("outcome") or {}

    shadow_trade_id = str(open_ev.get("shadow_trade_id", "") or "")
    pnl_r = outcome.get("pnl_r_multiple")
    if pnl_r is None:
        # CLOSE without outcome evidence is NOT a completed outcome.
        return None

    exit_reason_raw = str(close_ev.get("exit_reason", "") or "")
    horizon = str(open_identity.get("evaluated_horizon", "") or "")

    return {
        "schema_version": _RESEARCH_SCHEMA_VERSION,
        "source": "shadow_runtime_ingestion",
        "source_schema_version": _RUNTIME_SCHEMA_VERSION,
        # ─── identity ────────────────────────────────────────────────
        "identity": {
            "trade_id": shadow_trade_id,
            "shadow_trade_id": shadow_trade_id,
            "plan_id": open_ev.get("plan_id", ""),
            "observation_id": open_ev.get("observation_id", ""),
            "canonical_opportunity_id": open_ev.get(
                "canonical_opportunity_id", ""
            ),
            "entity_id": open_identity.get("entity_id", ""),
            "cycle_id": open_identity.get("cycle_id"),
            "symbol": open_ev.get("symbol", ""),
            "strategy_id": live_facts.get("strategy", ""),
            "shadow_type": open_identity.get("shadow_type", ""),
            "evaluated_horizon": horizon,
            "trade_horizon": open_identity.get("trade_horizon", "") or horizon,
            # Inherited live observations (live_facts) — provenance, not
            # shadow decisions. Kept under identity as the internal shape does.
            "v10_action": live_facts.get("v10_action", ""),
            "v10_rejection_stage": live_facts.get("v10_rejection_stage", ""),
            "v10_selected_horizon": live_facts.get("v10_selected_horizon", ""),
            "horizon_selection_status": live_facts.get(
                "horizon_selection_status", ""
            ),
        },
        # ─── decision snapshot (frozen at shadow open) ───────────────
        "decision_snapshot": {
            "timestamp_decision_utc": open_ev.get(
                "entry_market_time_utc_epoch_s"
            ),
            "direction": construction.get("direction", ""),
            "entry_intent_price": construction.get("entry_price"),
            "stop_loss_intent": construction.get("stop_loss"),
            "take_profit_intent": construction.get("take_profit"),
            "pattern": live_facts.get("pattern", ""),
            "score": live_facts.get("score", 0.0),
            "regime": live_facts.get("regime", ""),
            "h4_regime": live_facts.get("h4_regime", ""),
            "h1_bias": live_facts.get("h1_bias", ""),
            "market_phase": live_facts.get("market_phase", ""),
            "market_phase_confidence": live_facts.get(
                "market_phase_confidence", 0.0
            ),
            "trade_horizon": open_identity.get("trade_horizon", "") or horizon,
            "bid_at_entry": entry_facts.get("bid_at_entry"),
            "ask_at_entry": entry_facts.get("ask_at_entry"),
            "spread_at_entry": entry_facts.get("spread_at_entry"),
            "risk_config_snapshot": {
                "risk_price_distance": construction.get("risk_distance"),
                "risk_pips": construction.get("risk_pips"),
                "reward_risk_ratio": construction.get("intended_rr"),
            },
        },
        # ─── simulated outcome (counterfactual) ──────────────────────
        "simulated_outcome": {
            "pnl_r_multiple": pnl_r,
            "mfe_r": outcome.get("mfe_r"),
            "mae_r": outcome.get("mae_r"),
            "exit_reason": _EXIT_REASON_MAP.get(exit_reason_raw, exit_reason_raw),
            "exit_price": close_ev.get("exit_price"),
            "exit_timestamp": close_ev.get("exit_market_time_utc_epoch_s"),
            "exit_timestamp_iso": close_ev.get("exit_market_time_utc_iso8601"),
            "bars_held": close_ev.get("bars_held"),
            "risk_distance": outcome.get("risk_distance"),
            "intended_rr": outcome.get("intended_rr"),
        },
    }

