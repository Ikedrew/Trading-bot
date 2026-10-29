"""
Strategy Trace Logger v1.3.2 — Full decision replay logging.

Produces a complete explainable trace for every strategy activation cycle.
Every decision is replayable from logs alone.

Outputs:
    - Regime trace (current + history + flips)
    - Eligibility trace (per-strategy binary + reason)
    - Mapping trace (pattern → candidates with strength)
    - Gating trace (per-strategy validation breakdown)
    - Selection trace (active/excluded/selected + weight)
    - Final decision summary

Integration points:
    - Discord (pair channel)
    - Local JSONL (logs/strategy_trace.jsonl)
    - Console (compact summary)

Design: purely observational, no side effects on execution.
"""

from __future__ import annotations

import json
import time as _time
from collections import deque
from pathlib import Path
from typing import Any

from core.clock import utc_ms, utc_ms_to_iso
from strategy.schema_activation import ActivationResult
from strategy.mapping_activation import get_pattern_mappings, PatternMapping
from strategy.signals import Signal


# ─── LOCAL LOG ────────────────────────────────────────────────────────────────

_TRACE_LOG = Path("logs/strategy_trace.jsonl")
_TRACE_LOG.parent.mkdir(parents=True, exist_ok=True)

# ─── REGIME HISTORY TRACKER ───────────────────────────────────────────────────

_regime_history: deque[str] = deque(maxlen=10)
_regime_flip_count: int = 0


def _track_regime(regime: str) -> dict[str, Any]:
    """Track regime history and compute flip count."""
    global _regime_flip_count
    if _regime_history and _regime_history[-1] != regime:
        _regime_flip_count += 1
    _regime_history.append(regime)
    return {
        "current": regime,
        "history": list(_regime_history),
        "flip_count": _regime_flip_count,
    }


# ─── MAIN TRACE BUILDER ──────────────────────────────────────────────────────

def build_strategy_trace(
    *,
    symbol: str,
    cycle_id: int,
    activation: ActivationResult,
    pattern: Signal,
    entity_id: str = "",
    engine_action: str = "NO_TRADE",
    engine_reason: str = "",
    ev_value: float | None = None,
    policy_status: str = "",
) -> dict[str, Any]:
    """
    Build complete strategy activation trace for one cycle.

    Every strategy must appear in the trace — even if rejected.
    Every stage must be represented — even if nothing passed.

    Args:
        symbol: Trading pair
        cycle_id: Current cycle number
        activation: Output from run_strategy_activation()
        pattern: Detected pattern Signal
        engine_action: Final engine decision (EXECUTE/NO_TRADE)
        engine_reason: Engine rejection reason (if any)
        ev_value: Expected value (if computed)
        policy_status: Execution policy result

    Returns:
        Full trace dict for logging/replay.
    """
    # ─── 1. REGIME TRACE ──────────────────────────────────────────────
    regime_trace = _track_regime(activation.regime)
    regime_trace["confidence"] = activation.regime_confidence

    # ─── 2. ELIGIBILITY TRACE ─────────────────────────────────────────
    eligibility_trace = []
    _eligible_set = set(activation.eligible_strategies)
    _rejected_at_elig = {r.strategy: r.reason for r in activation.rejected_strategies if r.stage == "ELIGIBILITY"}

    for strat in ("CONTINUATION", "REVERSAL", "FALSE_BREAK"):
        if strat in _eligible_set:
            eligibility_trace.append({"strategy": strat, "allowed": True, "reason": "eligible_in_regime"})
        else:
            eligibility_trace.append({"strategy": strat, "allowed": False, "reason": _rejected_at_elig.get(strat, "not_eligible")})

    # ─── 3. MAPPING TRACE ─────────────────────────────────────────────
    mappings = get_pattern_mappings(pattern)
    mapping_trace = {
        "pattern": pattern.pattern,
        "candidates": [
            {"strategy": m.strategy, "strength": m.strength, "context_dependency": m.context_dependency}
            for m in mappings
        ],
    }

    # ─── 4. GATING TRACE ──────────────────────────────────────────────
    gating_trace = []
    _rejected_at_gating = {r.strategy: r.reason for r in activation.rejected_strategies if r.stage == "GATING"}
    _gated_set = set(activation.gated_strategies)

    for strat in ("CONTINUATION", "REVERSAL", "FALSE_BREAK"):
        if strat in _gated_set:
            gating_trace.append({"strategy": strat, "passed": True, "fail_reason": None})
        elif strat in _rejected_at_gating:
            gating_trace.append({"strategy": strat, "passed": False, "fail_reason": _rejected_at_gating[strat]})
        else:
            # Not reached gating (filtered earlier)
            gating_trace.append({"strategy": strat, "passed": False, "fail_reason": "filtered_before_gating"})

    # ─── 5. SELECTION TRACE ───────────────────────────────────────────
    _rejected_at_selection = {r.strategy: r.reason for r in activation.rejected_strategies if r.stage == "SELECTION"}
    active_in_selection = [c.strategy for c in activation.strategy_candidates if c.allowed]
    excluded_from_selection = [c.strategy for c in activation.strategy_candidates if not c.allowed]

    selection_trace = {
        "active_candidates": active_in_selection,
        "excluded_candidates": excluded_from_selection,
        "selected_strategy": activation.selected_strategy,
        "selected_weight": activation.selected_weight,
        "reason": "highest_valid_weight" if activation.selected_strategy else "no_valid_candidates",
    }

    # ─── 6. FINAL DECISION TRACE ──────────────────────────────────────
    final_trace = {
        "action": engine_action,
        "strategy": activation.selected_strategy,
        "confidence": activation.selected_weight,
        "reason": engine_reason or "passed_all_gates",
        "ev_alignment": ev_value,
        "policy_status": policy_status,
    }

    # ─── 7. STRATEGY PRESSURE + OVERRIDE SOURCE (v1.3.3) ─────────────
    # Raw pressure: pure pattern intent (before eligibility)
    # Final pressure: post-eligibility + gating + modulation
    raw_pressure = dict(activation.raw_pressure) if hasattr(activation, 'raw_pressure') else {"REVERSAL": 0.0, "FALSE_BREAK": 0.0, "CONTINUATION": 0.0}
    final_pressure_map = dict(activation.final_pressure) if hasattr(activation, 'final_pressure') else {}

    pressure_rank: dict[str, float] = {}
    override_source: dict[str, str] = {}

    _elig_blocked = {r.strategy for r in activation.rejected_strategies if r.stage == "ELIGIBILITY"}
    _map_blocked = {r.strategy for r in activation.rejected_strategies if r.stage == "MAPPING"}
    _gate_blocked = {r.strategy for r in activation.rejected_strategies if r.stage == "GATING"}
    _sel_blocked = {r.strategy for r in activation.rejected_strategies if r.stage == "SELECTION"}

    for candidate in activation.strategy_candidates:
        pressure_rank[candidate.strategy] = candidate.activation_weight

        # Determine override source (last layer that eliminated this strategy)
        if candidate.strategy == activation.selected_strategy:
            override_source[candidate.strategy] = "SELECTED"
        elif candidate.strategy in _elig_blocked:
            override_source[candidate.strategy] = "ELIGIBILITY_BLOCK"
        elif candidate.strategy in _map_blocked:
            override_source[candidate.strategy] = "MAPPING_WEAK"
        elif candidate.strategy in _gate_blocked:
            override_source[candidate.strategy] = "GATING_FAIL"
        elif candidate.strategy in _sel_blocked:
            override_source[candidate.strategy] = "SELECTION_LOSS"
        elif not candidate.allowed:
            override_source[candidate.strategy] = "SELECTION_LOSS"
        else:
            override_source[candidate.strategy] = "SELECTION_LOSS"

    # Ensure all 3 strategies have entries
    for s in ("CONTINUATION", "REVERSAL", "FALSE_BREAK"):
        if s not in pressure_rank:
            pressure_rank[s] = 0.0
        if s not in override_source:
            if s in _elig_blocked:
                override_source[s] = "ELIGIBILITY_BLOCK"
            else:
                override_source[s] = "MAPPING_WEAK"

    # Stability metrics
    sorted_pressures = sorted(pressure_rank.values(), reverse=True)
    stability_gap = round(sorted_pressures[0] - sorted_pressures[1], 4) if len(sorted_pressures) >= 2 else 0.0
    if stability_gap >= 0.20:
        stability_label = "HIGH"
    elif stability_gap >= 0.08:
        stability_label = "MEDIUM"
    else:
        stability_label = "LOW"

    # ─── FULL TRACE OBJECT ────────────────────────────────────────────
    _ts_ms = utc_ms()
    trace = {
        "symbol": symbol,
        "cycle_id": cycle_id,
        "entity_id": entity_id,
        "ts_utc_ms": _ts_ms,
        "timestamp": utc_ms_to_iso(_ts_ms),
        "regime": regime_trace,
        "eligibility": {"results": eligibility_trace},
        "mapping": mapping_trace,
        "gating": {"results": gating_trace},
        "selection": selection_trace,
        "pressure_rank": pressure_rank,
        "raw_pressure": raw_pressure,
        "final_pressure": final_pressure_map,
        "override_source": override_source,
        "stability_gap": stability_gap,
        "stability_label": stability_label,
        "final_decision": final_trace,
        "rejected_strategies": [
            {"strategy": r.strategy, "stage": r.stage, "reason": r.reason}
            for r in activation.rejected_strategies
        ],
    }

    return trace


# ─── EMIT FUNCTIONS ───────────────────────────────────────────────────────────

def emit_strategy_trace(trace: dict[str, Any]) -> None:
    """Persist trace to unified event stream + legacy JSONL + console summary."""
    # Unified event bus (primary)
    try:
        from core.event_stream import emit_strategy
        emit_strategy(trace.get("symbol", ""), trace, source="strategy_trace")
    except Exception:
        pass

    # Legacy JSONL (backward compat — will be removed after full migration)
    try:
        with open(_TRACE_LOG, "a") as f:
            f.write(json.dumps(trace, default=str) + "\n")
    except Exception:
        pass

    # Console compact summary
    try:
        _print_compact(trace)
    except Exception:
        pass


def emit_strategy_trace_discord(trace: dict[str, Any]) -> None:
    """Send strategy trace summary to pair Discord channel."""
    try:
        from core.discord_notifier import send_discord

        symbol = trace.get("symbol", "?")
        base = symbol.lower().replace("_sb", "").replace("_", "")
        channel = f"pair-{base}"

        msg = _format_discord(trace)
        if len(msg) > 1950:
            msg = msg[:1947] + "..."
        send_discord(channel, msg)
    except Exception:
        pass


# ─── FORMATTING ───────────────────────────────────────────────────────────────

def _print_compact(trace: dict[str, Any]) -> None:
    """Print compact strategy trace to console."""
    sym = trace.get("symbol", "?")
    regime = trace.get("regime", {})
    selection = trace.get("selection", {})
    final = trace.get("final_decision", {})
    pressure = trace.get("final_pressure", trace.get("pressure_rank", {}))
    raw_p = trace.get("raw_pressure", {})
    stability = trace.get("stability_label", "?")

    eligible = [e["strategy"][:4] for e in trace.get("eligibility", {}).get("results", []) if e.get("allowed")]
    rejected_count = len(trace.get("rejected_strategies", []))

    # Pressure compact
    _raw_parts = " ".join(f"{k[:4]}={v:.2f}" for k, v in sorted(raw_p.items(), key=lambda x: -x[1]))
    _fin_parts = " ".join(f"{k[:4]}={v:.2f}" for k, v in sorted(pressure.items(), key=lambda x: -x[1]))

    print(
        f"[STRAT TRACE] {sym} | "
        f"regime={regime.get('current', '?')}({regime.get('confidence', 0):.2f}) | "
        f"eligible={','.join(eligible)} | "
        f"selected={selection.get('selected_strategy', 'NONE')}({selection.get('selected_weight', 0):.2f}) | "
        f"raw=[{_raw_parts}] final=[{_fin_parts}] | "
        f"stability={stability} | "
        f"action={final.get('action', '?')} | "
        f"rejected={rejected_count}"
    )


def _format_discord(trace: dict[str, Any]) -> str:
    """Format trace for Discord with pressure + override."""
    sym = trace.get("symbol", "?")
    regime = trace.get("regime", {})
    selection = trace.get("selection", {})
    final = trace.get("final_decision", {})
    mapping = trace.get("mapping", {})
    pressure = trace.get("final_pressure", trace.get("pressure_rank", {}))
    raw_p = trace.get("raw_pressure", {})
    override = trace.get("override_source", {})
    stability = trace.get("stability_label", "?")
    stability_gap = trace.get("stability_gap", 0.0)
    rejected = trace.get("rejected_strategies", [])

    eligible_list = [e["strategy"] for e in trace.get("eligibility", {}).get("results", []) if e.get("allowed")]

    # Pressure lines
    _raw_parts = " | ".join(f"{k}={v:.2f}" for k, v in sorted(raw_p.items(), key=lambda x: -x[1]))
    _fin_parts = " | ".join(f"{k}={v:.2f}" for k, v in sorted(pressure.items(), key=lambda x: -x[1]))
    # Override line
    _o_parts = " ".join(f"{k}={v}" for k, v in override.items())

    lines = [
        f"🧠 **STRATEGY TRACE** | `{sym}` | Cycle {trace.get('cycle_id', '?')}",
        f"```",
        f"Regime:       {regime.get('current', '?')} ({regime.get('confidence', 0):.2f}) flips={regime.get('flip_count', 0)}",
        f"Eligible:     {', '.join(eligible_list) or 'NONE'}",
        f"Pattern:      {mapping.get('pattern', '?')}",
        f"Selected:     {selection.get('selected_strategy', 'NONE')} (w={selection.get('selected_weight', 0):.2f})",
        f"Raw Pressure: {_raw_parts}",
        f"Final Press:  {_fin_parts}",
        f"Overrides:    {_o_parts}",
        f"Stability:    {stability} (gap={stability_gap:.3f})",
        f"Action:       {final.get('action', '?')}",
        f"```",
    ]

    return "\n".join(lines)
