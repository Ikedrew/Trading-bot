"""
D3: Position State Reconciliation on Startup.

Discovers open broker positions (filtered by BOT_MAGIC) and registers them
into TradeStateManager so they resume active management immediately.

Called once during cold start, after MT5 initialization.
Idempotent: safe to call multiple times (no duplicate registration).
"""

from __future__ import annotations

import logging
import time
from typing import Any, TYPE_CHECKING

import MetaTrader5 as mt5

from core.mt5_timeout import mt5_call
from core.mt5_timestamp import normalize_mt5_timestamp
from core.trade_management.position import Position, PositionStatus
from strategy.signals import Side

if TYPE_CHECKING:
    from core.trade_management import TradeStateManager

logger = logging.getLogger(__name__)


def recover_positions_on_startup(
    *,
    trade_manager: "TradeStateManager | None",
    symbol: str,
    magic: int,
) -> int:
    """
    Discover open broker positions and register into TradeStateManager.

    Called once per symbol during startup (after MT5 init, before scanner loop).
    Idempotent: positions already tracked are skipped.

    Args:
        trade_manager: TradeStateManager instance (None = no-op)
        symbol: Resolved symbol name
        magic: BOT_MAGIC filter

    Returns:
        Number of positions recovered.
    """
    if trade_manager is None:
        return 0

    try:
        broker_positions = mt5_call(mt5.positions_get, symbol=symbol)
    except Exception as exc:
        logger.warning(
            "[STARTUP_POSITION_RECOVERY] symbol=%s error=positions_get_failed detail=%s",
            symbol, exc,
        )
        return 0

    if broker_positions is None or len(broker_positions) == 0:
        logger.info("[STARTUP_POSITION_RECOVERY] symbol=%s count=0 (none found)", symbol)
        return 0

    # Filter to our magic number
    our_positions = [p for p in broker_positions if int(p.magic) == magic]

    if not our_positions:
        logger.info("[STARTUP_POSITION_RECOVERY] symbol=%s count=0 (none with magic=%d)", symbol, magic)
        return 0

    # Get existing tracked tickets for duplicate protection
    existing_tickets: set[int] = set()
    for pos in trade_manager.positions_open():
        if pos.mt5_ticket is not None and pos.mt5_ticket > 0:
            existing_tickets.add(pos.mt5_ticket)

    recovered = 0
    for bp in our_positions:
        ticket = int(bp.ticket)

        # Duplicate protection: skip if already tracked
        if ticket in existing_tickets:
            continue

        # Determine side
        if int(bp.type) == mt5.ORDER_TYPE_BUY:
            side = Side.BUY
        else:
            side = Side.SELL

        # Normalize broker timestamp to UTC
        open_time_utc = normalize_mt5_timestamp(float(bp.time))

        # ─── IDENTITY RESTORATION: search execution_results for original identity ─
        _restored_identity = _restore_identity_from_logs(
            symbol=str(bp.symbol),
            ticket=ticket,
            entry_price=float(bp.price_open),
        )
        _pattern_tag = _restored_identity.get("pattern", "RECOVERED")
        _trade_identity = None
        if _restored_identity.get("correlation_id"):
            try:
                from core.trade_identity import TradeIdentity
                _trade_identity = TradeIdentity(
                    correlation_id=_restored_identity["correlation_id"],
                    decision_id=_restored_identity.get("decision_id", ""),
                    canonical_opportunity_id=_restored_identity.get(
                        "canonical_opportunity_id", ""
                    ),
                    observation_id=_restored_identity.get("observation_id", ""),
                    cycle_id=int(_restored_identity.get("cycle_id", 0)),
                    strategy=_restored_identity.get("strategy", ""),
                    pattern=_restored_identity.get("pattern", ""),
                    decision_ts_utc=float(_restored_identity.get("decision_ts_utc", 0.0)),
                )
            except Exception:
                _trade_identity = None
        # ─── END IDENTITY RESTORATION ──────────────────────────────────

        # ─── DURABLE EXCURSION RESTORE ─────────────────────────────────
        # Restore the HISTORICAL max_favourable/adverse price for THIS exact
        # broker ticket (persisted while the position was open) and combine with
        # the current broker price — the current observation may extend a
        # historical extreme but MUST NOT erase it. Falls back to the legacy
        # current-price seed when no durable state exists (legacy positions),
        # never fabricating history. Provenance recorded for research.
        _price_current = float(bp.price_current)
        _mfe_seed = _price_current
        _mae_seed = _price_current
        _excursion_provenance = "recovery_seeded"
        try:
            from core.trade_management.excursion_state import load_excursion, restore_extremes
            _saved = load_excursion(ticket)
            if _saved is not None:
                _r_mfe, _r_mae = restore_extremes(
                    side_name=side.name,
                    saved_mfe=_saved.get("max_favourable_price"),
                    saved_mae=_saved.get("max_adverse_price"),
                    current_price=_price_current,
                )
                if _r_mfe is not None:
                    _mfe_seed = _r_mfe
                if _r_mae is not None:
                    _mae_seed = _r_mae
                _excursion_provenance = "full_lifecycle"
        except Exception:
            pass  # Excursion restore must NEVER block recovery
        # ─── END DURABLE EXCURSION RESTORE ─────────────────────────────

        # Reconstruct Position object
        pos = Position(
            position_id=f"pos_{ticket}",
            symbol=str(bp.symbol),
            side=side,
            magic=int(bp.magic),
            entry_price=float(bp.price_open),
            initial_sl=float(bp.sl),
            initial_tp=float(bp.tp),
            stop_loss=float(bp.sl),
            take_profit=float(bp.tp),
            volume=float(bp.volume),
            open_time=open_time_utc,
            status=PositionStatus.OPEN,
            mt5_ticket=ticket,
            deal_id=0,  # Not available from positions_get
            order_id=0,
            pattern_tag=_pattern_tag,
            # Durable-restored extremes (full_lifecycle) when a checkpoint exists;
            # otherwise the legacy current-price seed (recovery_seeded). The
            # current broker price only ever EXTENDS a restored extreme.
            max_favourable_price=_mfe_seed,
            max_adverse_price=_mae_seed,
            excursion_provenance=_excursion_provenance,
            trade_identity=_trade_identity,
        )

        # Register into TradeStateManager
        trade_manager._by_id[pos.position_id] = pos
        existing_tickets.add(ticket)
        recovered += 1

        _id_status = f"correlation_id={_restored_identity.get('correlation_id', '')}" if _restored_identity.get('correlation_id') else "identity=NOT_FOUND"
        logger.info(
            "[STARTUP_POSITION_RECOVERY] symbol=%s ticket=%d volume=%.2f "
            "side=%s entry=%.5f sl=%.5f tp=%.5f pattern=%s %s",
            pos.symbol, ticket, pos.volume,
            pos.side.value, pos.entry_price, pos.stop_loss, pos.take_profit,
            _pattern_tag, _id_status,
        )

    # ─── POST-RECOVERY PROTECTION VERIFICATION ──────────────────────
    # Verify all recovered positions have SL/TP protection on broker.
    # If SL/TP are missing (0.0), log CRITICAL and attempt correction.
    for bp in our_positions:
        ticket = int(bp.ticket)
        broker_sl = float(bp.sl)
        broker_tp = float(bp.tp)
        if broker_sl == 0.0 or broker_tp == 0.0:
            logger.critical(
                "[STARTUP_PROTECTION_MISSING] symbol=%s ticket=%d sl=%.5f tp=%.5f — "
                "recovered position has NO broker-side protection",
                symbol, ticket, broker_sl, broker_tp,
            )
            # Attempt to find intended SL/TP from execution logs and correct
            try:
                from core.protection_verification import verify_protection
                # Use entry_price to estimate emergency SL if no record found
                _identity = _restore_identity_from_logs(
                    symbol=str(bp.symbol), ticket=ticket, entry_price=float(bp.price_open),
                )
                _req_sl = broker_sl  # If we can't find original, use what broker has
                _req_tp = broker_tp
                if _identity.get("sl"):
                    _req_sl = float(_identity["sl"])
                if _identity.get("tp"):
                    _req_tp = float(_identity["tp"])

                if _req_sl != 0.0 or _req_tp != 0.0:
                    verify_protection(
                        symbol=str(bp.symbol),
                        position_ticket=ticket,
                        requested_sl=_req_sl,
                        requested_tp=_req_tp,
                        correlation_id=_identity.get("correlation_id", f"RECOVERY-{ticket}"),
                    )
            except Exception as _prot_exc:
                logger.error(
                    "[STARTUP_PROTECTION_ERROR] ticket=%d error=%s", ticket, _prot_exc,
                )
    # ─── END POST-RECOVERY PROTECTION VERIFICATION ────────────────────

    logger.info(
        "[STARTUP_RECOVERY_COMPLETE] symbol=%s recovered=%d broker_total=%d",
        symbol, recovered, len(our_positions),
    )

    # ─── RESEARCH EVENT: structured recovery persistence ──────────────
    try:
        from core.research_events import persist_recovery_event
        _pos_details = []
        for bp in our_positions:
            _id = _restore_identity_from_logs(symbol=str(bp.symbol), ticket=int(bp.ticket), entry_price=float(bp.price_open))
            _pos_details.append({
                "ticket": int(bp.ticket),
                "symbol": str(bp.symbol),
                "volume": float(bp.volume),
                "entry_price": float(bp.price_open),
                "sl": float(bp.sl),
                "tp": float(bp.tp),
                "identity_found": bool(_id.get("correlation_id")),
            })
        _identity_ok = sum(1 for p in _pos_details if p["identity_found"])
        _identity_fail = len(_pos_details) - _identity_ok
        _protection_missing = sum(1 for bp in our_positions if float(bp.sl) == 0 or float(bp.tp) == 0)
        persist_recovery_event(
            symbol=symbol,
            recovered_count=recovered,
            broker_total=len(our_positions),
            positions=_pos_details,
            identity_restored=_identity_ok,
            identity_failed=_identity_fail,
            protection_missing=_protection_missing,
        )
    except Exception:
        pass  # Research event must NEVER block startup
    # ─── END RESEARCH EVENT ───────────────────────────────────────────

    return recovered


# ─── IDENTITY RESTORATION HELPER ──────────────────────────────────────────────

def _restore_identity_from_logs(
    *,
    symbol: str,
    ticket: int,
    entry_price: float,
) -> dict:
    """
    Search execution_results for a matching trade to restore identity.

    Searches by order_ticket (most reliable match).
    Returns dict with correlation_id, pattern, decision_id, cycle_id, strategy, decision_ts_utc.
    Returns empty dict if not found.

    Never raises. Never blocks startup.
    """
    try:
        import json
        from pathlib import Path
        from datetime import datetime, timezone

        results_dir = Path("logs/execution_results") / symbol
        if not results_dir.exists():
            return {}

        # Search all JSONL files for this ticket (most recent first)
        for jsonl_file in sorted(results_dir.glob("*.jsonl"), reverse=True):
            with open(jsonl_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                        # Match by order_ticket AND result_ok=True
                        if record.get("order_ticket") == ticket and record.get("result_ok") is True:
                            return {
                                "correlation_id": record.get("correlation_id", ""),
                                "canonical_opportunity_id": record.get(
                                    "canonical_opportunity_id", ""
                                ),
                                "observation_id": record.get("observation_id", ""),
                                "pattern": record.get("pattern", ""),
                                "decision_id": record.get("decision_id", ""),
                                "cycle_id": record.get("cycle_id", 0),
                                "strategy": "",  # Not stored in execution_results
                                "decision_ts_utc": record.get("decision_ts_utc_ms", 0) / 1000.0 if record.get("decision_ts_utc_ms") else 0.0,
                            }
                    except (json.JSONDecodeError, KeyError):
                        continue
        return {}
    except Exception as exc:
        logger.debug("[IDENTITY_RESTORE] search failed: %s", exc)
        return {}
