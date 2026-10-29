"""
Causal Explanation — Answers: "Why did this happen?"

Reads from: decision_ledger, decision_trace, execution_results, trade_truth, trade_journal
Reconstructs the evidence chain for a decision or trade.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


_LOGS = Path("logs")


def explain_latest_decision(symbol: str) -> dict[str, Any]:
    """
    Explain the latest decision for a symbol.

    Returns:
        - symbol
        - decision (EXECUTE/NO_TRADE/RISK_BLOCK/PATTERN_REJECT)
        - reason
        - evidence chain (score, stage, components, guards)
        - timestamp
    """
    result: dict[str, Any] = {
        "symbol": symbol,
        "decision": None,
        "reason": None,
        "evidence": {},
        "timestamp": None,
        "source": "decision_ledger",
    }

    # Read latest decision_ledger record for this symbol
    ledger_record = _read_latest_ledger(symbol)
    if not ledger_record:
        result["decision"] = "NO_DATA"
        result["reason"] = f"No decision_ledger records found for {symbol}"
        return result

    result["decision"] = ledger_record.get("decision")
    result["reason"] = ledger_record.get("reason")
    result["timestamp"] = ledger_record.get("timestamp")
    result["evidence"]["regime"] = ledger_record.get("regime")
    result["evidence"]["signal_score"] = ledger_record.get("signal_score")
    result["evidence"]["signal_type"] = ledger_record.get("signal_type")
    result["evidence"]["risk_flag"] = ledger_record.get("risk_flag")
    result["evidence"]["causal_signature"] = ledger_record.get("causal_signature")
    result["evidence"]["execution_intent"] = ledger_record.get("execution_intent")

    # Enrich with decision_trace if available
    trace = _read_matching_trace(symbol, ledger_record)
    if trace:
        result["evidence"]["terminal_stage"] = trace.get("terminal_stage")
        result["evidence"]["terminal_reason"] = trace.get("terminal_reason")
        result["evidence"]["score_strategy"] = trace.get("score_strategy")
        result["evidence"]["weakest_component"] = trace.get("weakest_component")
        result["evidence"]["threshold_gap"] = trace.get("threshold_gap")
        result["evidence"]["closest_flip"] = trace.get("closest_flip_component")
        result["evidence"]["pattern_name"] = trace.get("pattern_name")
        result["evidence"]["selected_strategy"] = trace.get("selected_strategy")
        result["evidence"]["components"] = trace.get("components", {})
        result["source"] = "decision_ledger + decision_trace"

    return result


def explain_trade(trade_id: str) -> dict[str, Any]:
    """
    Explain why a specific trade won or lost.

    Returns:
        - trade_id
        - symbol, direction, horizon
        - entry/exit prices
        - r_multiple
        - exit_reason
        - duration
        - explanation (human-readable summary)
    """
    result: dict[str, Any] = {
        "trade_id": trade_id,
        "found": False,
        "explanation": "Trade not found in journal.",
    }

    # Search trade_journal
    journal_record = _find_trade_in_journal(trade_id)
    if not journal_record:
        return result

    result["found"] = True
    result["symbol"] = journal_record.get("symbol")
    result["direction"] = journal_record.get("direction")
    result["trade_horizon"] = journal_record.get("trade_horizon", "SCALP")
    result["entry_price"] = journal_record.get("entry_price")
    result["exit_price"] = journal_record.get("exit_price")
    result["net_pnl"] = journal_record.get("net_pnl")
    result["close_reason"] = journal_record.get("close_reason")
    result["duration_seconds"] = journal_record.get("duration_seconds")
    result["initial_sl"] = journal_record.get("initial_sl")
    result["initial_tp"] = journal_record.get("initial_tp")
    result["max_favourable_price"] = journal_record.get("max_favourable_price")

    # Compute R-multiple
    risk = abs(journal_record.get("entry_price", 0) - journal_record.get("initial_sl", 0))
    if risk > 0:
        if journal_record.get("direction") == "BUY":
            r = (journal_record.get("exit_price", 0) - journal_record.get("entry_price", 0)) / risk
        else:
            r = (journal_record.get("entry_price", 0) - journal_record.get("exit_price", 0)) / risk
        result["r_multiple"] = round(r, 2)
    else:
        result["r_multiple"] = 0.0

    # Generate explanation
    pnl = journal_record.get("net_pnl", 0)
    reason = journal_record.get("close_reason", "unknown")
    if pnl > 0:
        result["explanation"] = (
            f"Trade won. Exit: {reason}. R = {result['r_multiple']}. "
            f"Duration: {round(result['duration_seconds']/60, 1)} min."
        )
    else:
        result["explanation"] = (
            f"Trade lost. Exit: {reason}. R = {result['r_multiple']}. "
            f"Duration: {round(result['duration_seconds']/60, 1)} min. "
            f"Market-driven outcome — no execution error."
        )

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# INTERNAL DATA READERS
# ═══════════════════════════════════════════════════════════════════════════════

def _read_latest_ledger(symbol: str) -> dict[str, Any] | None:
    """Read the most recent decision_ledger record for a symbol."""
    sym_dir = _LOGS / "decision_ledger" / symbol
    if not sym_dir.exists():
        # Try all subdirectories (ledger may be in flat structure)
        sym_dir = _LOGS / "decision_ledger"
    if not sym_dir.exists():
        return None

    files = sorted(sym_dir.rglob("*.jsonl"))
    if not files:
        return None

    # Read last line of latest file
    try:
        lines = [l for l in open(files[-1], encoding="utf-8") if l.strip()]
        if not lines:
            return None
        return json.loads(lines[-1])
    except Exception:
        return None


def _read_matching_trace(symbol: str, ledger_record: dict) -> dict[str, Any] | None:
    """Find the decision_trace matching a ledger record (by entity_id or cycle_id)."""
    trace_dir = _LOGS / "decision_trace" / symbol
    if not trace_dir.exists():
        trace_dir = _LOGS / "decision_trace"
    if not trace_dir.exists():
        return None

    files = sorted(trace_dir.rglob("*.jsonl"))
    if not files:
        return None

    entity_id = ledger_record.get("entity_id", "")
    cycle_id = ledger_record.get("cycle_id")

    # Search latest file backwards for matching record
    try:
        lines = [l for l in open(files[-1], encoding="utf-8") if l.strip()]
        for line in reversed(lines):
            rec = json.loads(line)
            if entity_id and rec.get("entity_id") == entity_id:
                return rec
            if cycle_id and rec.get("cycle_id") == cycle_id and rec.get("symbol") == symbol:
                return rec
    except Exception:
        pass

    return None


def _find_trade_in_journal(trade_id: str) -> dict[str, Any] | None:
    """Search trade_journal for a specific trade_id."""
    journal_dir = _LOGS / "trade_journal"
    if not journal_dir.exists():
        return None

    # Search all files (most recent first)
    for f in sorted(journal_dir.glob("*.jsonl"), reverse=True):
        try:
            for line in open(f, encoding="utf-8"):
                if trade_id in line:
                    rec = json.loads(line)
                    if rec.get("trade_id") == trade_id:
                        return rec
        except Exception:
            continue

    return None
