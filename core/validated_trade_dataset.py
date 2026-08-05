"""
Validated Trade Dataset — Research-ready dataset with integrity checks.

Reconstructs the full trade lifecycle by joining:
    - trade_journal (execution/outcome)
    - decision_trace (decision context: pattern, regime, strategy, score)
    - trades_clean (corrected PnL)

Applies 7 validation checks per trade and classifies each as VALID/INVALID/PARTIAL.

Storage: logs/validated_trade_dataset/validated_trades.jsonl

Does NOT modify raw data. Marks invalid trades without deletion.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.instrument_utils import get_instrument_class, InstrumentClass

logger = logging.getLogger(__name__)

_JOURNAL_DIR = "logs/trade_journal"
_DECISION_TRACE_DIR = "logs/decision_trace"
_CLEAN_DIR = "logs/trades_clean"
_OUTPUT_DIR = "logs/validated_trade_dataset"
_SCHEMA_VERSION = "validated_trade_dataset_v1"


# ═══════════════════════════════════════════════════════════════
# VALIDATION CHECKS
# ═══════════════════════════════════════════════════════════════

def _check_lifecycle_completeness(trade: dict) -> list[str]:
    """Check 1: All critical lifecycle fields present."""
    errors = []
    required = [
        ("trade_id", "Missing trade_id"),
        ("symbol", "Missing symbol"),
        ("entry_price", "Missing entry_price"),
        ("exit_price", "Missing exit_price"),
        ("initial_sl", "Missing stop_loss"),
        ("initial_tp", "Missing take_profit"),
        ("direction", "Missing direction"),
        ("close_reason", "Missing close_reason"),
    ]
    for field, msg in required:
        val = trade.get(field)
        if val is None or val == "" or val == 0:
            errors.append(msg)
    return errors


def _check_correlation_linkage(trade: dict, decision: dict | None) -> list[str]:
    """Check 2: Correlation ID links to a decision trace."""
    errors = []
    cor_id = trade.get("correlation_id", "")
    if not cor_id:
        errors.append("No correlation_id — cannot link to decision")
    elif decision is None:
        errors.append(f"Correlation exists ({cor_id[:20]}) but no matching decision trace found")
    return errors


def _check_stop_placement(trade: dict) -> list[str]:
    """Check 3: BUY stop below entry, SELL stop above entry."""
    errors = []
    entry = trade.get("entry_price", 0)
    sl = trade.get("initial_sl", 0)
    direction = trade.get("direction", "")

    if entry <= 0 or sl <= 0:
        return errors  # Can't validate without prices

    if direction == "BUY" and sl >= entry:
        errors.append(f"BUY stop ({sl}) >= entry ({entry}) — stop on wrong side")
    elif direction == "SELL" and sl <= entry:
        errors.append(f"SELL stop ({sl}) <= entry ({entry}) — stop on wrong side")
    return errors


def _check_minimum_stop_distance(trade: dict) -> list[str]:
    """Check 4: Stop distance is not inside typical spread/noise."""
    errors = []
    entry = trade.get("entry_price", 0)
    sl = trade.get("initial_sl", 0)
    symbol = trade.get("symbol", "")

    if entry <= 0 or sl <= 0:
        return errors

    risk_dist = abs(entry - sl)
    pct_of_price = (risk_dist / entry) * 100 if entry > 0 else 0

    inst = get_instrument_class(symbol)
    # Minimum sensible stop: 0.01% for FX, 0.05% for indices
    if inst in (InstrumentClass.FX_MAJOR, InstrumentClass.FX_JPY):
        if pct_of_price < 0.01:
            errors.append(f"Stop distance {risk_dist:.5f} ({pct_of_price:.4f}%) likely inside spread")
    elif inst == InstrumentClass.INDEX:
        if pct_of_price < 0.02:
            errors.append(f"Stop distance {risk_dist:.1f} ({pct_of_price:.4f}%) unrealistically tight for index")

    return errors


def _check_rr_sanity(trade: dict) -> list[str]:
    """Check 5: R:R is within realistic bounds (0.5 to 20)."""
    errors = []
    entry = trade.get("entry_price", 0)
    sl = trade.get("initial_sl", 0)
    tp = trade.get("initial_tp", 0)

    if entry <= 0 or sl <= 0 or tp <= 0:
        return errors

    risk_dist = abs(entry - sl)
    reward_dist = abs(tp - entry)

    if risk_dist <= 0:
        errors.append("Zero risk distance — geometry invalid")
        return errors

    rr = reward_dist / risk_dist

    if rr > 20:
        errors.append(f"R:R={rr:.1f} exceeds realistic maximum (likely geometry error)")
    elif rr < 0.3:
        errors.append(f"R:R={rr:.2f} below minimum viable (risk > 3x reward)")

    return errors


def _check_pnl_instrument(trade: dict) -> list[str]:
    """Check 6: PnL is reasonable for the instrument type."""
    errors = []
    symbol = trade.get("symbol", "")
    pnl = trade.get("net_pnl", 0)
    volume = trade.get("final_volume", 0)
    inst = get_instrument_class(symbol)

    if inst in (InstrumentClass.INDEX, InstrumentClass.COMMODITY, InstrumentClass.CRYPTO):
        # Flag if PnL > $50K per trade (100K multiplier bug indicator)
        if abs(pnl) > 50000:
            errors.append(f"PnL={pnl:,.0f} — likely 100K multiplier bug (instrument: {inst.value})")

    return errors


def _check_broker_reconciliation(trade: dict, clean: dict | None) -> list[str]:
    """Check 7: Broker profit reconciliation (if available)."""
    errors = []
    if clean is None:
        return errors  # No clean data to compare

    broker_pnl = clean.get("broker_pnl")
    calc_pnl = clean.get("calculated_pnl")
    pnl_source = clean.get("pnl_source", "")

    if pnl_source == "UNCORRECTABLE":
        errors.append("PnL source UNCORRECTABLE — broker profit unavailable for non-FX instrument")
    elif broker_pnl is not None and calc_pnl is not None:
        if abs(broker_pnl) > 0 and abs(calc_pnl) > 0:
            ratio = abs(calc_pnl / broker_pnl) if broker_pnl != 0 else 0
            if ratio > 10 or ratio < 0.1:
                errors.append(f"PnL mismatch: calculated={calc_pnl:.2f} vs broker={broker_pnl:.2f} (ratio={ratio:.0f}x)")

    return errors


# ═══════════════════════════════════════════════════════════════
# DATASET BUILDER
# ═══════════════════════════════════════════════════════════════

def build_validated_dataset(
    *,
    journal_dir: str | None = None,
    decision_dir: str | None = None,
    clean_dir: str | None = None,
    output_dir: str | None = None,
) -> dict[str, Any]:
    """
    Build the validated trade dataset from all available sources.

    Returns summary statistics.
    """
    j_path = Path(journal_dir or _JOURNAL_DIR)
    d_path = Path(decision_dir or _DECISION_TRACE_DIR)
    c_path = Path(clean_dir or _CLEAN_DIR)
    o_path = Path(output_dir or _OUTPUT_DIR)
    o_path.mkdir(parents=True, exist_ok=True)

    # ─── Load trade journal ───────────────────────────────────
    trades = _load_journal(j_path)

    # ─── Load decision traces (indexed by symbol + cycle_id) ──
    decisions = _load_decisions(d_path)

    # ─── Load trades_clean (indexed by trade_id) ──────────────
    clean_trades = _load_clean(c_path)

    # ─── Validate each trade ──────────────────────────────────
    results = []
    stats = {"total": 0, "valid": 0, "invalid": 0, "partial": 0, "errors": {}}

    for trade in trades:
        stats["total"] += 1
        validated = _validate_one_trade(trade, decisions, clean_trades)
        results.append(validated)

        status = validated["validation_status"]
        if status == "VALID":
            stats["valid"] += 1
        elif status == "INVALID":
            stats["invalid"] += 1
        else:
            stats["partial"] += 1

        for err in validated["validation_errors"]:
            stats["errors"][err] = stats["errors"].get(err, 0) + 1

    # ─── Write output ─────────────────────────────────────────
    output_file = o_path / "validated_trades.jsonl"
    lines = [json.dumps(r, default=str) for r in results]
    output_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ─── Write summary ────────────────────────────────────────
    summary = {
        "schema_version": _SCHEMA_VERSION,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "total_trades": stats["total"],
        "valid": stats["valid"],
        "invalid": stats["invalid"],
        "partial": stats["partial"],
        "pass_rate": round(100 * stats["valid"] / max(stats["total"], 1), 1),
        "error_summary": dict(sorted(stats["errors"].items(), key=lambda x: -x[1])),
    }
    summary_file = o_path / "validation_summary.json"
    summary_file.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")

    logger.info(
        "[VALIDATED_DATASET] total=%d valid=%d invalid=%d partial=%d pass_rate=%.1f%%",
        stats["total"], stats["valid"], stats["invalid"], stats["partial"],
        summary["pass_rate"],
    )
    return summary


def _validate_one_trade(
    trade: dict,
    decisions: dict[str, dict],
    clean_trades: dict[str, dict],
) -> dict[str, Any]:
    """Validate a single trade and produce a complete validated record."""

    trade_id = trade.get("trade_id", "")
    symbol = trade.get("symbol", "")
    cor_id = trade.get("correlation_id", "")

    # ─── Find matching decision trace ─────────────────────────
    decision = _find_decision(trade, decisions)

    # ─── Find matching clean trade ────────────────────────────
    clean = clean_trades.get(trade_id)

    # ─── Run all validation checks ────────────────────────────
    all_errors = []
    all_errors.extend(_check_lifecycle_completeness(trade))
    all_errors.extend(_check_correlation_linkage(trade, decision))
    all_errors.extend(_check_stop_placement(trade))
    all_errors.extend(_check_minimum_stop_distance(trade))
    all_errors.extend(_check_rr_sanity(trade))
    all_errors.extend(_check_pnl_instrument(trade))
    all_errors.extend(_check_broker_reconciliation(trade, clean))

    # ─── Classify ─────────────────────────────────────────────
    critical_errors = [e for e in all_errors if any(k in e.lower() for k in
                       ["wrong side", "100k multiplier", "zero risk", "missing entry", "missing stop"])]

    if not all_errors:
        status = "VALID"
        quality_score = 100
    elif critical_errors:
        status = "INVALID"
        quality_score = max(0, 100 - len(all_errors) * 15)
    else:
        status = "PARTIAL"
        quality_score = max(0, 100 - len(all_errors) * 10)

    # ─── Compute geometry ─────────────────────────────────────
    entry = trade.get("entry_price", 0)
    sl = trade.get("initial_sl", 0)
    tp = trade.get("initial_tp", 0)
    risk_dist = abs(entry - sl) if entry > 0 and sl > 0 else 0
    reward_dist = abs(tp - entry) if entry > 0 and tp > 0 else 0
    rr_ratio = round(reward_dist / risk_dist, 2) if risk_dist > 0 else 0

    # ─── PnL resolution ───────────────────────────────────────
    final_pnl = None
    pnl_source = "CALCULATED"
    if clean and clean.get("final_pnl") is not None:
        final_pnl = clean["final_pnl"]
        pnl_source = clean.get("pnl_source", "CALCULATED")
    else:
        final_pnl = trade.get("net_pnl")

    # ─── Build validated record ───────────────────────────────
    return {
        # Identity
        "trade_id": trade_id,
        "position_ticket": trade.get("position_ticket"),
        "correlation_id": cor_id,
        "symbol": symbol,
        "entry_time": trade.get("entry_time"),
        "exit_time": trade.get("exit_time"),

        # Decision (from decision trace if linked)
        "pattern": decision.get("pattern_name", trade.get("pattern_name", "")) if decision else trade.get("pattern_name", ""),
        "regime": decision.get("regime", "") if decision else "",
        "regime_confidence": decision.get("regime_confidence", 0.0) if decision else 0.0,
        "strategy": decision.get("selected_strategy", "") if decision else "",
        "strategy_confidence": decision.get("strategy_confidence", 0.0) if decision else 0.0,
        "trade_horizon": trade.get("trade_horizon", ""),
        "direction": trade.get("direction", ""),
        "score": decision.get("score_strategy", 0.0) if decision else 0.0,
        "ev": decision.get("ev", 0.0) if decision else 0.0,

        # Risk
        "entry_price": entry,
        "exit_price": trade.get("exit_price", 0),
        "stop_loss": sl,
        "take_profit": tp,
        "risk_distance": round(risk_dist, 6),
        "reward_distance": round(reward_dist, 6),
        "rr_ratio": rr_ratio,

        # Execution
        "volume": trade.get("final_volume", 0),
        "broker_pnl": clean.get("broker_pnl") if clean else None,
        "calculated_pnl": trade.get("net_pnl"),
        "final_pnl": final_pnl,
        "pnl_source": pnl_source,
        "commission": trade.get("commission", 0),
        "swap": trade.get("swap", 0),
        "close_reason": trade.get("close_reason", ""),
        "duration_seconds": trade.get("duration_seconds", 0),

        # Instrument
        "instrument_class": get_instrument_class(symbol).value,

        # Validation
        "validation_status": status,
        "validation_errors": all_errors,
        "data_quality_score": quality_score,

        # Schema
        "schema_version": _SCHEMA_VERSION,
    }


# ═══════════════════════════════════════════════════════════════
# DATA LOADERS
# ═══════════════════════════════════════════════════════════════

def _load_journal(path: Path) -> list[dict]:
    trades = []
    for f in sorted(path.glob("*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    trades.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return trades


def _load_decisions(path: Path) -> dict[str, dict]:
    """Load decision traces indexed by symbol_cycle for join."""
    decisions = {}
    for f in path.rglob("*.jsonl"):
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                d = json.loads(line)
                # Only index EXECUTE decisions (these link to trades)
                if d.get("action") == "EXECUTE":
                    key = f"{d.get('symbol', '')}_{d.get('cycle_id', '')}"
                    decisions[key] = d
                    # Also index by entity_id for direct lookup
                    eid = d.get("entity_id", "")
                    if eid:
                        decisions[eid] = d
            except json.JSONDecodeError:
                pass
    return decisions


def _load_clean(path: Path) -> dict[str, dict]:
    """Load trades_clean indexed by trade_id."""
    clean = {}
    for f in sorted(path.glob("*.jsonl")):
        if not f.exists():
            continue
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    d = json.loads(line)
                    tid = d.get("trade_id", "")
                    if tid:
                        clean[tid] = d
                except json.JSONDecodeError:
                    pass
    return clean


def _find_decision(trade: dict, decisions: dict) -> dict | None:
    """Find the decision trace that produced this trade."""
    cor_id = trade.get("correlation_id", "")
    symbol = trade.get("symbol", "")

    if not cor_id:
        return None

    # Parse cycle_id from correlation_id: COR-YYYYMMDD-CYCLE-SYMBOL-HASH
    match = re.match(r"COR-\d{8}-(\d+)-", cor_id)
    if match:
        cycle_id = int(match.group(1))
        key = f"{symbol}_{cycle_id}"
        if key in decisions:
            return decisions[key]

    # Fallback: try entity_id from trade (if available)
    # entity_id format: SYMBOL_BARTIME
    entry_time = trade.get("entry_time", 0)
    if entry_time and symbol:
        entity_key = f"{symbol}_{int(entry_time)}"
        if entity_key in decisions:
            return decisions[entity_key]

    return None
