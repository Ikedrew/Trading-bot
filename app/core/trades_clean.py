"""
Trades Clean — Corrected trade analytics layer.

Reads raw trade_journal JSONL, applies PnL corrections for instruments
where the legacy 100K multiplier was incorrect (indices, commodities),
and writes a clean dataset suitable for analytics.

Storage: logs/trades_clean/{date}.jsonl

Rules:
    - trades_raw (trade_journal/) is NEVER modified
    - trades_clean is rebuilt from raw + broker_profit when available
    - For indices: uses broker_profit if present, otherwise flags as UNCORRECTABLE
    - For FX: legacy calculation is approximately correct, preserved as-is
    - final_pnl = broker_profit when available, else calculated (FX only)

Schema (trades_clean):
    symbol, trade_id, entry_price, exit_price, volume,
    calculated_pnl, broker_pnl, final_pnl, pnl_source,
    r_multiple_realised, duration_seconds, exit_reason,
    direction, pattern_name, correlation_id, trade_horizon
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.instrument_utils import get_instrument_class, InstrumentClass

logger = logging.getLogger(__name__)

_RAW_DIR = "logs/trade_journal"
_CLEAN_DIR = "logs/trades_clean"

# Instruments where the legacy 100K calculation is WRONG
_NEEDS_BROKER_PNL = frozenset({
    InstrumentClass.INDEX,
    InstrumentClass.COMMODITY,
    InstrumentClass.CRYPTO,
})


def rebuild_trades_clean(
    *,
    raw_dir: str | None = None,
    clean_dir: str | None = None,
    broker_profits: dict[str, float] | None = None,
) -> dict[str, Any]:
    """
    Rebuild the entire trades_clean dataset from raw journal files.

    Args:
        raw_dir: Override path to raw journal directory
        clean_dir: Override path to clean output directory
        broker_profits: Optional dict of {trade_id: broker_profit} for manual correction

    Returns:
        Summary dict with counts and corrections applied.
    """
    raw_path = Path(raw_dir or _RAW_DIR)
    clean_path = Path(clean_dir or _CLEAN_DIR)
    clean_path.mkdir(parents=True, exist_ok=True)

    if broker_profits is None:
        broker_profits = {}

    summary = {
        "total_trades": 0,
        "corrected_broker": 0,
        "corrected_manual": 0,
        "fx_preserved": 0,
        "uncorrectable": 0,
        "files_processed": 0,
    }

    for raw_file in sorted(raw_path.glob("*.jsonl")):
        date_str = raw_file.stem  # e.g., "2026-07-22"
        clean_file = clean_path / f"{date_str}.jsonl"

        clean_records: list[str] = []
        for line in raw_file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue

            clean_record = _clean_one_trade(raw, broker_profits)
            clean_records.append(json.dumps(clean_record, default=str))
            summary["total_trades"] += 1

            src = clean_record.get("pnl_source", "")
            if src == "BROKER":
                summary["corrected_broker"] += 1
            elif src == "MANUAL_BROKER":
                summary["corrected_manual"] += 1
            elif src == "CALCULATED_FX":
                summary["fx_preserved"] += 1
            elif src == "UNCORRECTABLE":
                summary["uncorrectable"] += 1

        # Write clean file
        clean_file.write_text("\n".join(clean_records) + "\n", encoding="utf-8")
        summary["files_processed"] += 1

    logger.info(
        "[TRADES_CLEAN] Rebuilt: total=%d broker=%d manual=%d fx=%d uncorrectable=%d files=%d",
        summary["total_trades"], summary["corrected_broker"], summary["corrected_manual"],
        summary["fx_preserved"], summary["uncorrectable"], summary["files_processed"],
    )
    return summary


def _clean_one_trade(raw: dict[str, Any], broker_profits: dict[str, float]) -> dict[str, Any]:
    """
    Produce a clean trade record from one raw journal entry.

    Logic:
        1. If broker_profit is embedded in the raw record → use it (BROKER)
        2. If manual broker_profit provided → use it (MANUAL_BROKER)
        3. If FX instrument → legacy calculation is acceptable (CALCULATED_FX)
        4. If index/commodity with no broker data → flag UNCORRECTABLE
    """
    symbol = raw.get("symbol", "")
    trade_id = raw.get("trade_id", "")
    entry_price = float(raw.get("entry_price", 0))
    exit_price = float(raw.get("exit_price", 0))
    volume = float(raw.get("final_volume", 0) or raw.get("initial_volume", 0))
    direction = raw.get("direction", "")
    calculated_pnl = float(raw.get("realised_pnl", 0))
    duration_seconds = float(raw.get("duration_seconds", 0))
    exit_reason = raw.get("close_reason", "")
    initial_sl = float(raw.get("initial_sl", 0))
    initial_tp = float(raw.get("initial_tp", 0))

    # Determine instrument class
    inst_class = get_instrument_class(symbol)
    needs_broker = inst_class in _NEEDS_BROKER_PNL

    # Source broker_profit
    broker_pnl: float | None = None

    # Check if raw record has embedded broker_profit (from fixed pipeline)
    if "broker_profit" in raw and raw["broker_profit"] is not None:
        broker_pnl = float(raw["broker_profit"])

    # Check manual override
    if trade_id in broker_profits:
        broker_pnl = broker_profits[trade_id]

    # Determine final_pnl and source
    if broker_pnl is not None:
        final_pnl = broker_pnl
        pnl_source = "MANUAL_BROKER" if trade_id in broker_profits else "BROKER"
    elif not needs_broker:
        # FX — legacy calculation is approximately correct
        final_pnl = calculated_pnl
        pnl_source = "CALCULATED_FX"
    else:
        # Index/commodity without broker data — CANNOT be trusted
        final_pnl = None
        pnl_source = "UNCORRECTABLE"

    # R-multiple calculation
    r_multiple = None
    if final_pnl is not None and initial_sl > 0 and entry_price > 0:
        risk_distance = abs(entry_price - initial_sl)
        if risk_distance > 0 and volume > 0:
            # For FX: risk_amount ≈ risk_distance * volume * 100000
            # For broker-sourced PnL: use profit / expected_loss_at_stop
            if pnl_source in ("BROKER", "MANUAL_BROKER"):
                # Approximate R using profit ratio to stop distance ratio
                price_move = abs(exit_price - entry_price)
                if risk_distance > 0:
                    r_multiple = round(price_move / risk_distance, 2)
                    # Apply direction sign
                    if direction == "BUY":
                        r_multiple = r_multiple if exit_price > entry_price else -r_multiple
                    else:
                        r_multiple = r_multiple if entry_price > exit_price else -r_multiple
            else:
                # FX: use calculated PnL
                expected_loss = risk_distance * volume * 100_000.0
                if expected_loss > 0:
                    r_multiple = round(final_pnl / expected_loss, 2)

    return {
        "record_role": "reconciliation_projection",
        "authority": "projection_of_trade_truth_and_broker_reconciliation",
        "may_override_live_truth": False,
        "symbol": symbol,
        "trade_id": trade_id,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "volume": volume,
        "direction": direction,
        "calculated_pnl": round(calculated_pnl, 4),
        "broker_pnl": round(broker_pnl, 4) if broker_pnl is not None else None,
        "final_pnl": round(final_pnl, 4) if final_pnl is not None else None,
        "pnl_source": pnl_source,
        "pnl_semantic": "reconciled_projection",
        "r_multiple_realised": r_multiple,
        "duration_seconds": duration_seconds,
        "exit_reason": exit_reason,
        "pattern_name": raw.get("pattern_name", ""),
        "correlation_id": raw.get("correlation_id", ""),
        "trade_horizon": raw.get("trade_horizon", "SCALP"),
        "initial_sl": initial_sl,
        "initial_tp": initial_tp,
        "position_ticket": raw.get("position_ticket"),
        "entry_time": raw.get("entry_time"),
        "exit_time": raw.get("exit_time"),
    }


def get_clean_trades(date_str: str | None = None, clean_dir: str | None = None) -> list[dict[str, Any]]:
    """Read clean trades for a date (or all dates if None)."""
    clean_path = Path(clean_dir or _CLEAN_DIR)
    results: list[dict[str, Any]] = []

    if date_str:
        files = [clean_path / f"{date_str}.jsonl"]
    else:
        files = sorted(clean_path.glob("*.jsonl"))

    for f in files:
        if not f.exists():
            continue
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    results.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return results
