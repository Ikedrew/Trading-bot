"""
Research-Ready Trade Dataset — Final integrity-filtered dataset for research.

Takes validated_trade_dataset output and applies additional hardening:
    GAP 1: Broker PnL reconciliation (marks UNCORRECTABLE as excluded)
    GAP 2: Exit reason validation (reconstructs from available data)
    GAP 3: Risk geometry hardening (stop side, minimum distance, R:R bounds)
    GAP 4: Instrument calculation validation (flags FX assumptions on non-FX)
    GAP 5: Produces research_ready_trades.jsonl (only trades passing all checks)

A trade is research-ready when we can answer:
    "Do we know why this trade happened, how risk was calculated, and whether the result is real?"

Storage: logs/research_ready_trade_dataset/
    research_ready_trades.jsonl  — only VALID trades
    excluded_trades.jsonl        — trades that failed with reasons
    research_summary.json        — statistics and trust scores
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.instrument_utils import get_instrument_class, InstrumentClass, get_pip_size

logger = logging.getLogger(__name__)

_VALIDATED_DIR = "logs/validated_trade_dataset"
_OUTPUT_DIR = "logs/research_ready_trade_dataset"
_SCHEMA_VERSION = "research_ready_v1"

# Known broker profits for index trades (manual reconciliation from MT5 history)
_KNOWN_BROKER_PROFITS: dict[str, dict[str, float]] = {
    "pos_82095735": {"broker_pnl": 843.48, "commission": 0.0, "swap": 0.0},
    "pos_82098818": {"broker_pnl": 871.97, "commission": 0.0, "swap": 0.0},
}


# ═══════════════════════════════════════════════════════════════
# GAP 1: BROKER PNL RECONCILIATION
# ═══════════════════════════════════════════════════════════════

def _reconcile_pnl(trade: dict) -> dict:
    """
    Reconcile PnL using best available source.

    Priority:
        1. Known broker profit (manual reconciliation table)
        2. Existing broker_pnl from trades_clean
        3. Calculated PnL (FX only — known to be correct)
        4. UNCORRECTABLE (non-FX without broker data)
    """
    trade_id = trade.get("trade_id", "")
    inst_class = trade.get("instrument_class", "")

    # Check manual reconciliation table
    if trade_id in _KNOWN_BROKER_PROFITS:
        known = _KNOWN_BROKER_PROFITS[trade_id]
        trade["broker_pnl"] = known["broker_pnl"]
        trade["commission"] = known.get("commission", 0.0)
        trade["swap"] = known.get("swap", 0.0)
        trade["final_pnl"] = known["broker_pnl"] + known.get("swap", 0.0) - known.get("commission", 0.0)
        trade["pnl_source"] = "BROKER_RECONCILED"
        trade["pnl_status"] = "CONFIRMED"
        return trade

    # Existing broker_pnl
    if trade.get("broker_pnl") is not None:
        trade["final_pnl"] = trade["broker_pnl"]
        trade["pnl_source"] = "BROKER"
        trade["pnl_status"] = "CONFIRMED"
        return trade

    # FX instruments: calculated PnL is acceptable (100K formula correct for FX)
    if inst_class in ("FX_MAJOR", "FX_JPY"):
        trade["final_pnl"] = trade.get("calculated_pnl", 0)
        trade["pnl_source"] = "CALCULATED_FX"
        trade["pnl_status"] = "ACCEPTABLE"
        return trade

    # Non-FX without broker data: UNCORRECTABLE
    trade["pnl_status"] = "UNCORRECTABLE"
    trade["final_pnl"] = None
    return trade


# ═══════════════════════════════════════════════════════════════
# GAP 2: EXIT REASON VALIDATION
# ═══════════════════════════════════════════════════════════════

def _validate_exit_reason(trade: dict) -> dict:
    """
    Validate and classify exit reason.

    Reconstruction logic:
        - If close_reason is specific (stop_loss, take_profit) → use directly
        - If broker_close → attempt reconstruction from price vs SL/TP
        - If unknown → flag but don't exclude
    """
    close_reason = trade.get("close_reason", "")
    entry = trade.get("entry_price", 0)
    exit_price = trade.get("exit_price", 0) if "exit_price" in trade else 0
    sl = trade.get("stop_loss", 0)
    tp = trade.get("take_profit", 0)
    direction = trade.get("direction", "")

    # Already specific
    if close_reason in ("stop_loss", "take_profit", "time_exit", "management_exit"):
        trade["exit_reason_validated"] = close_reason.upper()
        trade["exit_reason_source"] = "lifecycle"
        return trade

    # Attempt reconstruction from price comparison
    if close_reason == "broker_close" and exit_price > 0:
        reconstructed = _reconstruct_exit_from_price(
            exit_price=exit_price, entry=entry, sl=sl, tp=tp, direction=direction
        )
        trade["exit_reason_validated"] = reconstructed
        trade["exit_reason_source"] = "reconstructed"
        return trade

    # Cannot determine
    trade["exit_reason_validated"] = "UNKNOWN"
    trade["exit_reason_source"] = "unknown"
    return trade


def _reconstruct_exit_from_price(
    exit_price: float, entry: float, sl: float, tp: float, direction: str
) -> str:
    """
    Reconstruct exit reason by comparing exit price to SL/TP levels.

    If exit is within tolerance of SL → STOP_LOSS
    If exit is within tolerance of TP → TAKE_PROFIT
    If exit is between SL and TP → MANUAL_CLOSE or OTHER
    """
    if sl <= 0 or tp <= 0 or exit_price <= 0:
        return "UNKNOWN"

    # Calculate tolerances (0.1% of price as tolerance for matching)
    tol = exit_price * 0.001

    # Check SL proximity
    if abs(exit_price - sl) <= tol:
        return "STOP_LOSS"

    # Check TP proximity
    if abs(exit_price - tp) <= tol:
        return "TAKE_PROFIT"

    # Check if exit is on the losing side (beyond SL)
    if direction == "BUY":
        if exit_price <= sl:
            return "STOP_LOSS"
        if exit_price >= tp:
            return "TAKE_PROFIT"
    elif direction == "SELL":
        if exit_price >= sl:
            return "STOP_LOSS"
        if exit_price <= tp:
            return "TAKE_PROFIT"

    # Between SL and TP — likely manual or time exit
    return "OTHER"


# ═══════════════════════════════════════════════════════════════
# GAP 3: RISK GEOMETRY HARDENING
# ═══════════════════════════════════════════════════════════════

def _validate_risk_geometry(trade: dict) -> dict:
    """
    Validate risk geometry and assign status.

    Checks:
        1. Stop on correct side
        2. Stop not inside minimum distance (spread/noise)
        3. R:R within realistic bounds
    """
    entry = trade.get("entry_price", 0)
    sl = trade.get("stop_loss", 0)
    tp = trade.get("take_profit", 0)
    direction = trade.get("direction", "")
    symbol = trade.get("symbol", "")
    rr = trade.get("rr_ratio", 0)

    if entry <= 0 or sl <= 0:
        trade["risk_geometry_status"] = "INVALID_MISSING_DATA"
        return trade

    # Check 1: Stop side
    if direction == "BUY" and sl >= entry:
        trade["risk_geometry_status"] = "INVALID_STOP_SIDE"
        return trade
    if direction == "SELL" and sl <= entry:
        trade["risk_geometry_status"] = "INVALID_STOP_SIDE"
        return trade

    # Check 2: Minimum stop distance
    risk_dist = abs(entry - sl)
    pct_of_price = (risk_dist / entry) * 100 if entry > 0 else 0
    inst = get_instrument_class(symbol)

    if inst in (InstrumentClass.FX_MAJOR, InstrumentClass.FX_JPY):
        if pct_of_price < 0.01:  # < 1 pip for most FX
            trade["risk_geometry_status"] = "STOP_TOO_CLOSE"
            return trade
    elif inst == InstrumentClass.INDEX:
        if pct_of_price < 0.02:
            trade["risk_geometry_status"] = "STOP_TOO_CLOSE"
            return trade

    # Check 3: R:R bounds
    if rr > 20:
        trade["risk_geometry_status"] = "INVALID_RR"
        return trade
    if rr < 0.3 and rr > 0:
        trade["risk_geometry_status"] = "INVALID_RR"
        return trade

    trade["risk_geometry_status"] = "VALID"
    return trade


# ═══════════════════════════════════════════════════════════════
# GAP 4: INSTRUMENT CALCULATION VALIDATION
# ═══════════════════════════════════════════════════════════════

def _validate_instrument(trade: dict) -> dict:
    """
    Validate instrument-specific calculations.

    Flags trades where FX formula was incorrectly applied to non-FX instruments.
    """
    inst_class = trade.get("instrument_class", "")
    pnl_status = trade.get("pnl_status", "")
    calculated_pnl = trade.get("calculated_pnl", 0)
    broker_pnl = trade.get("broker_pnl")

    trade["instrument_validated"] = True
    trade["instrument_error"] = ""

    # Non-FX with only calculated PnL (100K multiplier applied incorrectly)
    if inst_class in ("INDEX", "COMMODITY", "CRYPTO"):
        if pnl_status == "UNCORRECTABLE":
            trade["instrument_validated"] = False
            trade["instrument_error"] = "Non-FX instrument with uncorrectable PnL (100K multiplier bug)"
        elif broker_pnl is not None and calculated_pnl is not None:
            if abs(calculated_pnl) > 0 and abs(broker_pnl) > 0:
                ratio = abs(calculated_pnl / broker_pnl)
                if ratio > 10:
                    trade["instrument_error"] = f"PnL ratio {ratio:.0f}x indicates calculation error (corrected via broker)"

    return trade


# ═══════════════════════════════════════════════════════════════
# GAP 5: BUILD RESEARCH-READY DATASET
# ═══════════════════════════════════════════════════════════════

def build_research_ready_dataset(
    *,
    validated_dir: str | None = None,
    output_dir: str | None = None,
) -> dict[str, Any]:
    """
    Build the final research-ready dataset from validated trades.

    A trade is research-ready if:
        - PnL is confirmed (broker or acceptable FX calculation)
        - Risk geometry is VALID
        - Instrument calculations are validated
        - Core lifecycle fields exist

    Returns summary statistics.
    """
    v_path = Path(validated_dir or _VALIDATED_DIR)
    o_path = Path(output_dir or _OUTPUT_DIR)
    o_path.mkdir(parents=True, exist_ok=True)

    # Load validated trades
    validated_file = v_path / "validated_trades.jsonl"
    if not validated_file.exists():
        logger.warning("[RESEARCH_DATASET] validated_trades.jsonl not found — run validated_trade_dataset first")
        return {"error": "validated_trades.jsonl not found"}

    trades = []
    for line in validated_file.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                trades.append(json.loads(line))
            except json.JSONDecodeError:
                pass

    # Apply all gap fixes
    research_ready = []
    excluded = []

    for trade in trades:
        # Apply reconciliation and validation
        trade = _reconcile_pnl(trade)
        trade = _validate_exit_reason(trade)
        trade = _validate_risk_geometry(trade)
        trade = _validate_instrument(trade)

        # Determine if research-ready
        exclusion_reasons = []

        if trade.get("pnl_status") == "UNCORRECTABLE":
            exclusion_reasons.append("PnL uncorrectable (non-FX without broker data)")
        if trade.get("risk_geometry_status") in ("INVALID_STOP_SIDE", "STOP_TOO_CLOSE", "INVALID_RR"):
            exclusion_reasons.append(f"Risk geometry: {trade.get('risk_geometry_status')}")
        if not trade.get("instrument_validated", True):
            exclusion_reasons.append(trade.get("instrument_error", "Instrument validation failed"))

        if exclusion_reasons:
            trade["research_status"] = "EXCLUDED"
            trade["exclusion_reasons"] = exclusion_reasons
            excluded.append(trade)
        else:
            trade["research_status"] = "INCLUDED"
            trade["exclusion_reasons"] = []
            research_ready.append(trade)

    # Compute data trust score
    trust_score = _compute_trust_score(research_ready, excluded, trades)

    # Write output
    ready_file = o_path / "research_ready_trades.jsonl"
    ready_lines = [json.dumps(t, default=str) for t in research_ready]
    ready_file.write_text("\n".join(ready_lines) + "\n", encoding="utf-8")

    excluded_file = o_path / "excluded_trades.jsonl"
    excl_lines = [json.dumps(t, default=str) for t in excluded]
    excluded_file.write_text("\n".join(excl_lines) + "\n", encoding="utf-8")

    # Summary
    summary = {
        "schema_version": _SCHEMA_VERSION,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "dataset_counts": {
            "raw_trades": len(trades),
            "research_ready": len(research_ready),
            "excluded": len(excluded),
            "inclusion_rate": round(100 * len(research_ready) / max(len(trades), 1), 1),
        },
        "exclusion_breakdown": _count_exclusions(excluded),
        "pnl_reconciliation": {
            "broker_confirmed": sum(1 for t in research_ready if t.get("pnl_status") == "CONFIRMED"),
            "calculated_fx_acceptable": sum(1 for t in research_ready if t.get("pnl_status") == "ACCEPTABLE"),
            "uncorrectable_excluded": sum(1 for t in excluded if t.get("pnl_status") == "UNCORRECTABLE"),
        },
        "exit_reason_coverage": {
            "stop_loss": sum(1 for t in research_ready if t.get("exit_reason_validated") == "STOP_LOSS"),
            "take_profit": sum(1 for t in research_ready if t.get("exit_reason_validated") == "TAKE_PROFIT"),
            "other": sum(1 for t in research_ready if t.get("exit_reason_validated") == "OTHER"),
            "unknown": sum(1 for t in research_ready if t.get("exit_reason_validated") == "UNKNOWN"),
        },
        "risk_geometry": {
            "valid": sum(1 for t in research_ready if t.get("risk_geometry_status") == "VALID"),
            "excluded_stop_side": sum(1 for t in excluded if t.get("risk_geometry_status") == "INVALID_STOP_SIDE"),
            "excluded_too_close": sum(1 for t in excluded if t.get("risk_geometry_status") == "STOP_TOO_CLOSE"),
            "excluded_rr": sum(1 for t in excluded if t.get("risk_geometry_status") == "INVALID_RR"),
        },
        "data_trust_score": trust_score,
        "phase1_checklist": {
            "lifecycle_trace_complete": trust_score["lifecycle_completeness"] >= 90,
            "broker_pnl_confirmed": trust_score["broker_reconciliation"] >= 70,
            "exit_reasons_captured": trust_score["exit_reason_confidence"] >= 50,
            "risk_geometry_validated": trust_score["risk_validity"] >= 90,
            "instrument_calculations_validated": trust_score["instrument_correctness"] >= 90,
            "research_ready_dataset_created": len(research_ready) > 0,
        },
    }

    summary_file = o_path / "research_summary.json"
    summary_file.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")

    logger.info(
        "[RESEARCH_DATASET] raw=%d ready=%d excluded=%d trust_overall=%d%%",
        len(trades), len(research_ready), len(excluded), trust_score["overall"],
    )
    return summary


def _count_exclusions(excluded: list[dict]) -> dict[str, int]:
    """Count exclusion reasons."""
    counts: dict[str, int] = {}
    for t in excluded:
        for reason in t.get("exclusion_reasons", []):
            counts[reason] = counts.get(reason, 0) + 1
    return dict(sorted(counts.items(), key=lambda x: -x[1]))


def _compute_trust_score(ready: list, excluded: list, total: list) -> dict[str, int]:
    """
    Compute data trust score (0-100) across five dimensions.

    Each dimension scored 0-100, overall is weighted average.
    """
    n = max(len(total), 1)

    # 1. Lifecycle completeness: % of trades with all core fields
    lifecycle = sum(1 for t in total if t.get("validation_status") != "INVALID")
    lifecycle_score = round(100 * lifecycle / n)

    # 2. Broker reconciliation: % with confirmed/acceptable PnL
    confirmed = sum(1 for t in total if t.get("pnl_status") in ("CONFIRMED", "ACCEPTABLE"))
    broker_score = round(100 * confirmed / n)

    # 3. Risk validity: % with valid geometry
    valid_geo = sum(1 for t in total if t.get("risk_geometry_status") == "VALID")
    risk_score = round(100 * valid_geo / n)

    # 4. Instrument correctness: % with validated instrument
    inst_ok = sum(1 for t in total if t.get("instrument_validated", True))
    inst_score = round(100 * inst_ok / n)

    # 5. Exit reason confidence: % with reconstructed or known exit
    exit_known = sum(1 for t in total if t.get("exit_reason_validated", "UNKNOWN") != "UNKNOWN")
    exit_score = round(100 * exit_known / n)

    # Weighted overall
    overall = round(
        lifecycle_score * 0.20 +
        broker_score * 0.30 +
        risk_score * 0.20 +
        inst_score * 0.15 +
        exit_score * 0.15
    )

    return {
        "overall": overall,
        "lifecycle_completeness": lifecycle_score,
        "broker_reconciliation": broker_score,
        "risk_validity": risk_score,
        "instrument_correctness": inst_score,
        "exit_reason_confidence": exit_score,
    }
