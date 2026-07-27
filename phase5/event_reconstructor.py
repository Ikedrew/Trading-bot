"""
Phase 5 — Event Reconstructor.

Parses raw JSONL shadow logs into validated TradeEvent objects.
Does exactly three things: Parse → Correlate → Validate.

NEVER imports trading runtime code.
NEVER modifies logs or state.
NEVER performs attribution, clustering, or analysis.

Input: JSONL log files containing Phase 3–4 shadow pipeline records.
Output: list[TradeEvent] + EventReconstructionReport.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ─── CANONICAL TRADE EVENT SCHEMA (FROZEN) ────────────────────────────────────

@dataclass(frozen=True)
class VoterSnapshot:
    """Per-voter scores at decision time."""
    bias: float = 0.0
    structure: float = 0.0
    session: float = 0.0
    spread: float = 0.0
    volatility: float = 0.0


@dataclass(frozen=True)
class WeightIntelligenceSnapshot:
    """Weight intelligence state at decision time."""
    current: dict[str, float] = field(default_factory=dict)
    recommended: dict[str, float] = field(default_factory=dict)
    deltas: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class TradeEvent:
    """
    Canonical atomic unit of truth for all Phase 5 analysis.
    One event = one trade decision cycle.
    Every Phase 5 module consumes list[TradeEvent] only.
    """
    trade_id: str
    timestamp: float
    symbol: str

    production_decision: str  # BUY | SELL | NO_TRADE
    shadow_decision: str      # BUY | SELL | NO_TRADE

    pnl: float
    outcome: str              # win | loss | breakeven

    ssi: float

    agreement_score: float
    conflict_types: list[str]
    conflict_severity: str    # none | low | medium | high

    system_state: str         # coherent | tensioned | unstable | degenerate

    voter_snapshot: VoterSnapshot
    dominant_voters: list[str]
    conflicting_voters: list[str]

    weight_intelligence: WeightIntelligenceSnapshot


# ─── RECONSTRUCTION REPORT ────────────────────────────────────────────────────

@dataclass(frozen=True)
class EventReconstructionReport:
    """Summary of reconstruction process."""
    total_records: int
    reconstructed_events: int
    invalid_events: int
    dropped_records: int


# ─── VALIDATION ───────────────────────────────────────────────────────────────

_VALID_DECISIONS = frozenset({"BUY", "SELL", "NO_TRADE"})
_VALID_OUTCOMES = frozenset({"win", "loss", "breakeven"})
_VALID_SEVERITIES = frozenset({"none", "low", "medium", "high"})
_VALID_STATES = frozenset({"coherent", "tensioned", "unstable", "degenerate"})


def _validate_event(data: dict[str, Any]) -> list[str]:
    """Validate a raw event dict. Returns list of error strings (empty = valid)."""
    errors: list[str] = []

    if not data.get("trade_id"):
        errors.append("missing trade_id")
    if not isinstance(data.get("timestamp"), (int, float)):
        errors.append("invalid timestamp")
    if not data.get("symbol"):
        errors.append("missing symbol")

    if data.get("production_decision") not in _VALID_DECISIONS:
        errors.append(f"invalid production_decision: {data.get('production_decision')}")
    if data.get("shadow_decision") not in _VALID_DECISIONS:
        errors.append(f"invalid shadow_decision: {data.get('shadow_decision')}")

    if data.get("outcome") not in _VALID_OUTCOMES:
        errors.append(f"invalid outcome: {data.get('outcome')}")

    ssi = data.get("ssi", -1)
    if not isinstance(ssi, (int, float)) or ssi < 0 or ssi > 1:
        errors.append(f"ssi out of range: {ssi}")

    agreement = data.get("agreement_score", -1)
    if not isinstance(agreement, (int, float)) or agreement < 0 or agreement > 1:
        errors.append(f"agreement_score out of range: {agreement}")

    if data.get("conflict_severity") not in _VALID_SEVERITIES:
        errors.append(f"invalid conflict_severity: {data.get('conflict_severity')}")

    if data.get("system_state") not in _VALID_STATES:
        errors.append(f"invalid system_state: {data.get('system_state')}")

    return errors


# ─── PARSING ──────────────────────────────────────────────────────────────────

def _parse_trade_event(data: dict[str, Any]) -> TradeEvent:
    """Convert validated dict into frozen TradeEvent."""
    voter_raw = data.get("voter_snapshot", {})
    voter = VoterSnapshot(
        bias=float(voter_raw.get("bias", 0.0)),
        structure=float(voter_raw.get("structure", 0.0)),
        session=float(voter_raw.get("session", 0.0)),
        spread=float(voter_raw.get("spread", 0.0)),
        volatility=float(voter_raw.get("volatility", 0.0)),
    )

    wi_raw = data.get("weight_intelligence", {})
    weight_intel = WeightIntelligenceSnapshot(
        current=wi_raw.get("current", {}),
        recommended=wi_raw.get("recommended", {}),
        deltas=wi_raw.get("deltas", {}),
    )

    return TradeEvent(
        trade_id=str(data["trade_id"]),
        timestamp=float(data["timestamp"]),
        symbol=str(data["symbol"]),
        production_decision=str(data["production_decision"]),
        shadow_decision=str(data["shadow_decision"]),
        pnl=float(data.get("pnl", 0.0)),
        outcome=str(data["outcome"]),
        ssi=float(data.get("ssi", 0.5)),
        agreement_score=float(data.get("agreement_score", 0.5)),
        conflict_types=list(data.get("conflict_types", [])),
        conflict_severity=str(data.get("conflict_severity", "none")),
        system_state=str(data.get("system_state", "coherent")),
        voter_snapshot=voter,
        dominant_voters=list(data.get("dominant_voters", [])),
        conflicting_voters=list(data.get("conflicting_voters", [])),
        weight_intelligence=weight_intel,
    )


# ─── MAIN API ─────────────────────────────────────────────────────────────────

def reconstruct_events(
    log_path: Path,
) -> tuple[list[TradeEvent], EventReconstructionReport]:
    """
    Reconstruct TradeEvents from JSONL log file.

    Each line in the file should be a JSON object matching the TradeEvent schema.
    Invalid records are counted and skipped (never stop processing).

    Args:
        log_path: Path to JSONL file containing trade event records.

    Returns:
        (list of valid TradeEvents, reconstruction report)
    """
    events: list[TradeEvent] = []
    total_records = 0
    invalid_count = 0
    dropped_count = 0

    if not log_path.exists():
        return [], EventReconstructionReport(
            total_records=0,
            reconstructed_events=0,
            invalid_events=0,
            dropped_records=0,
        )

    with open(log_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            total_records += 1

            # Parse JSON
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                dropped_count += 1
                continue

            if not isinstance(data, dict):
                dropped_count += 1
                continue

            # Validate
            errors = _validate_event(data)
            if errors:
                invalid_count += 1
                continue

            # Reconstruct
            try:
                event = _parse_trade_event(data)
                events.append(event)
            except (KeyError, TypeError, ValueError):
                invalid_count += 1

    report = EventReconstructionReport(
        total_records=total_records,
        reconstructed_events=len(events),
        invalid_events=invalid_count,
        dropped_records=dropped_count,
    )

    return events, report
