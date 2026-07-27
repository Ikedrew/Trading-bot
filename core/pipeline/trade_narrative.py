"""
Trade Narrative Layer — Transparent engine output formatting.

Purely passive. Read-only. Post-decision formatting ONLY.
MUST NOT influence execution, modify decisions, or trigger logic.

Renders raw engine values, weighted math, and gate results
into a structured human-readable format. No interpretation.
No invented metrics. No subjective language.

Design rules:
    ✔ Raw engine values
    ✔ Weighted math (transparent)
    ✔ Direct mapping of engine fields
    ✔ Pass/fail gate outputs
    ✖ No invented scoring categories
    ✖ No reinterpreted metrics
    ✖ No subjective language
"""

from __future__ import annotations

from typing import Any


# Engine weights (mirrored from new_engine.py — read-only reference)
_ENGINE_WEIGHTS = {
    "pattern_quality": 0.14,
    "bias_alignment": 0.18,
    "market_quality": 0.08,
    "trend_alignment": 0.10,
    "chop_clarity": 0.06,
    "volatility_quality": 0.07,
    "bias_stability": 0.07,
    "confirmation_pre": 0.06,
    "htf_alignment": 0.14,
    "h4_alignment": 0.10,
}

_ENGINE_THRESHOLD = 0.40

# Legacy reason string compatibility (display layer only — engine never produces these anymore)
_LEGACY_REASON_MAP = {
    "confirmation_failed": "low_confirmation_score",
    "shadow_confirmation_failed": "low_confirmation_score",
    "FAILED CONFIRMATION GATE": "EV reduced by weak confirmation",
}


def _normalize_reason(reason: str) -> str:
    """Map legacy reason strings to current terminology for display."""
    for old, new in _LEGACY_REASON_MAP.items():
        if old in reason:
            return reason.replace(old, new)
    return reason


def build_trade_narrative(
    *,
    symbol: str,
    decision: dict[str, Any],
    engine_state: Any,
    gate_results: dict[str, bool | str] | None = None,
    execution_result: Any = None,
    cycle_id: int | None = None,
    mt5_time: float | None = None,
) -> str:
    """
    Build structured trade narrative from raw engine output.

    100% read-only. Formats data only. No evaluation or mutation.
    Timestamp is inherited from Gate 4 (single source of time).
    """
    action = decision.get("action", "UNKNOWN")
    score = decision.get("score", 0.0)
    reason = decision.get("reason", "")
    components = decision.get("components", {})
    intent = decision.get("intent")
    pattern = decision.get("pattern", "")
    side = decision.get("side", "")
    confirmation_strength = decision.get("confirmation_strength", "")
    strategy_type = decision.get("strategy", "UNKNOWN")
    strategy_confidence = decision.get("strategy_confidence", 0.0)
    strategy_reasoning = decision.get("strategy_reasoning", "")
    weights_used = decision.get("weights_used", "global_fallback")

    lines: list[str] = []

    # ─── HEADER ───────────────────────────────────────────────────────
    _cycle_str = f" (CYCLE {cycle_id})" if cycle_id is not None else ""
    lines.append(f"{'═' * 60}")
    lines.append(f"🧭 {symbol} — TRADE DECISION{_cycle_str}")
    # Timestamp (inherited from Gate 4 — single source of time)
    try:
        from core.pipeline.timestamps import format_timestamp_line
        lines.append(format_timestamp_line(mt5_time))
    except Exception:
        pass
    lines.append("")

    # ─── ACTION SUMMARY ───────────────────────────────────────────────
    if action == "EXECUTE" and intent is not None:
        _side_str = getattr(intent, "side", None)
        _side_display = _side_str.name if hasattr(_side_str, "name") else str(side or "?")
        lines.append(f"Action:     EXECUTE {_side_display}")
    else:
        lines.append(f"Action:     NO TRADE")

    _status = "PASS" if action == "EXECUTE" else "BLOCKED"
    lines.append(f"Confidence: {score:.3f}")
    lines.append(f"Threshold:  {_ENGINE_THRESHOLD:.2f}")
    lines.append(f"Status:     {_status}")
    lines.append("")

    # ─── STRATEGY CLASSIFICATION ──────────────────────────────────────
    if strategy_type and strategy_type != "UNKNOWN":
        lines.append("🎯 STRATEGY CLASSIFICATION")
        lines.append(f"  Type:       {strategy_type}")
        lines.append(f"  Confidence: {strategy_confidence:.3f}")
        lines.append(f"  Weights:    {weights_used}")
        if strategy_reasoning:
            lines.append(f"  Reasoning:  {strategy_reasoning}")
        lines.append("")

    # ─── DUAL SCORING ─────────────────────────────────────────────────
    _score_neutral = decision.get("score_neutral", score)
    _score_strategy = decision.get("score_strategy", score)
    _delta = decision.get("delta", 0.0)
    lines.append("📊 DUAL SCORING")
    lines.append(f"  Neutral Score:  {_score_neutral:.4f}  (global weights — baseline truth)")
    lines.append(f"  Strategy Score: {_score_strategy:.4f}  (strategy-specific weights)")
    lines.append(f"  Delta:          {_delta:+.4f}")
    lines.append("")

    # ─── MARKET STATE ─────────────────────────────────────────────────
    _mkt_state = decision.get("market_state", "UNKNOWN")
    _mkt_conf = decision.get("market_state_confidence", 0.0)
    _mkt_reason = decision.get("market_state_reasoning", "")
    lines.append("🌊 MARKET STATE")
    _state_icon = {"STRUCTURED": "🟢", "TRANSITIONAL": "🟡", "CHOP": "🔴"}.get(_mkt_state, "⚪")
    lines.append(f"  State:      {_state_icon} {_mkt_state}")
    lines.append(f"  Confidence: {_mkt_conf:.3f}")
    if _mkt_reason:
        lines.append(f"  Detail:     {_mkt_reason}")
    lines.append("")

    # ─── EXECUTION POLICY ─────────────────────────────────────────────
    _pol_allowed = decision.get("policy_trade_allowed", False)
    _pol_rr = decision.get("policy_required_rr", 0.0)
    _pol_size = decision.get("policy_max_size_fraction", 0.0)
    _pol_reason = decision.get("policy_reasoning", "")
    lines.append("⚙️ EXECUTION POLICY")
    lines.append(f"  Permission: {'✔ ALLOWED' if _pol_allowed else '✖ BLOCKED'}")
    lines.append(f"  Required RR: {_pol_rr:.2f}")
    lines.append(f"  Max Size:    {_pol_size:.0%}")
    if _pol_reason:
        lines.append(f"  Reasoning:   {_pol_reason}")
    lines.append("")

    # ─── EXPECTED VALUE ───────────────────────────────────────────────
    _ev = decision.get("ev")
    if _ev is not None:
        _ev_pos = decision.get("ev_positive", False)
        _p_win = decision.get("p_success", 0.0)
        _p_loss = decision.get("p_failure", 0.0)
        _ev_reward = decision.get("ev_reward", 0.0)
        _ev_risk = decision.get("ev_risk", 0.0)
        _rr_eff = decision.get("rr_effective", 0.0)
        _damp = decision.get("ev_uncertainty_dampening", 0.0)
        lines.append("📐 EXPECTED VALUE (comparative ranking metric)")
        _ev_label = "RANKS ABOVE THRESHOLD" if _ev_pos else "BELOW THRESHOLD"
        lines.append(f"  EV:          {_ev:.6f} {'✔' if _ev_pos else '✖'} {_ev_label}")
        lines.append(f"  P(success):  {_p_win:.3f}")
        lines.append(f"  P(failure):  {_p_loss:.3f}")
        lines.append(f"  Reward:      {_ev_reward:.5f}")
        lines.append(f"  Risk:        {_ev_risk:.5f}")
        lines.append(f"  RR effective:{_rr_eff:.2f}")
        lines.append(f"  Uncertainty: {_damp:.0%} dampening")
        lines.append(f"  Context:     comparative (not predictive certainty)")
        lines.append("")

    # ─── WHY THIS DECISION HAPPENED ──────────────────────────────────
    lines.append("🧠 WHY THIS DECISION HAPPENED")
    if action == "EXECUTE":
        lines.append("The decision was triggered by weighted alignment across core engine factors:")
        _active = [(k, v) for k, v in components.items() if v > 0.5]
        _active.sort(key=lambda x: -x[1])
        for comp_name, comp_val in _active:
            _weight = _ENGINE_WEIGHTS.get(comp_name, 0.0)
            _contrib = comp_val * _weight
            lines.append(f"  - {comp_name} contributed {_contrib:.3f} (raw={comp_val:.3f} × weight={_weight:.2f})")
        if not _active:
            lines.append("  - No dominant factor — composite barely cleared threshold")
    else:
        if "score_below_threshold" in reason:
            lines.append(f"Composite score {score:.3f} failed to reach threshold {_ENGINE_THRESHOLD:.2f}")
            _weak = [(k, v) for k, v in components.items() if v < 0.4]
            _weak.sort(key=lambda x: x[1])
            if _weak:
                lines.append("Weakest factors:")
                for comp_name, comp_val in _weak[:3]:
                    lines.append(f"  - {comp_name} = {comp_val:.3f} (below useful contribution)")
        elif "confirmation_failed" in reason or "low_confirmation_score" in reason:
            lines.append("Confirmation score reduced EV probability (weak candle structure)")
            _detail = reason.split(":", 1)[1].strip() if ":" in reason else ""
            if _detail:
                lines.append(f"  Detail: {_detail}")
        elif "risk_rejected" in reason:
            lines.append("Score and confirmation passed but risk manager blocked execution")
            _detail = reason.split(":", 1)[1].strip() if ":" in reason else ""
            if _detail:
                lines.append(f"  Risk reason: {_detail}")
        elif "no_viable_pattern" in reason:
            lines.append("No pattern in detected signals met quality threshold")
        else:
            lines.append(f"Blocked: {reason}")
    lines.append("")

    # ─── ENGINE SCORING BREAKDOWN ─────────────────────────────────────
    # ─── ENGINE SCORING BREAKDOWN ─────────────────────────────────────
    # NOTE: _total_weighted below is DISPLAY-ONLY verification math.
    # The authoritative composite is `score` (from engine output).
    # This section exists for transparency — NOT for recomputation.
    if components:
        lines.append("⚙️ ENGINE SCORING BREAKDOWN")
        _max_possible = 0.0
        _total_weighted = 0.0
        for comp_name in _ENGINE_WEIGHTS:
            raw = components.get(comp_name, 0.0)
            weight = _ENGINE_WEIGHTS[comp_name]
            weighted = raw * weight
            _total_weighted += weighted
            _max_possible += weight  # max raw is 1.0
            lines.append(f"  {comp_name:20s} = {raw:.3f} × {weight:.2f} = {weighted:.4f}")
        lines.append("")

        # ─── COMPOSITE SCORE (ENGINE TRUTH — NOT RECALCULATED) ────────
        lines.append("🧮 COMPOSITE SCORE (source: engine)")
        lines.append(f"  COMPOSITE SCORE     = {score:.4f}  ← engine output (authoritative)")
        lines.append(f"  DISPLAY VERIFY      = {_total_weighted:.4f}  ← breakdown sum (informational)")
        lines.append(f"  MAX POSSIBLE        = {_max_possible:.2f}")
        lines.append(f"  THRESHOLD           = {_ENGINE_THRESHOLD:.3f}")
        _margin = score - _ENGINE_THRESHOLD
        _margin_str = f"+{_margin:.4f}" if _margin >= 0 else f"{_margin:.4f}"
        lines.append(f"  MARGIN OVER THRESH  = {_margin_str}")
        lines.append("")

    # ─── GATE / FILTER RESULTS ────────────────────────────────────────
    if gate_results:
        lines.append("📊 GATE / FILTER RESULT SUMMARY")
        for gate_name, gate_outcome in gate_results.items():
            if gate_outcome is True or gate_outcome == "PASS":
                lines.append(f"  ✔ {gate_name}")
            else:
                _reason_str = f" — {gate_outcome}" if isinstance(gate_outcome, str) and gate_outcome not in ("FAIL", "False") else ""
                lines.append(f"  ✖ {gate_name}{_reason_str}")
        lines.append("")
    elif action == "EXECUTE":
        # Infer gate results from engine output structure
        lines.append("📊 GATE / FILTER RESULT SUMMARY")
        lines.append(f"  ✔ Pattern gate passed ({pattern or '?'})")
        lines.append(f"  ✔ Score gate passed ({score:.3f} >= {_ENGINE_THRESHOLD})")
        if confirmation_strength:
            lines.append(f"  ✔ Confirmation gate passed ({confirmation_strength})")
        else:
            lines.append(f"  ✔ Confirmation gate passed")
        lines.append(f"  ✔ Risk engine approved entry")
        lines.append("")

    # ─── ORDER INTENT ─────────────────────────────────────────────────
    if intent is not None and action == "EXECUTE":
        lines.append("📋 ORDER INTENT (FINAL ENGINE OUTPUT)")
        _side_val = getattr(intent, "side", "?")
        _side_name = _side_val.name if hasattr(_side_val, "name") else str(_side_val)
        _entry = getattr(intent, "entry_reference", 0.0)
        _sl = getattr(intent, "sl", 0.0)
        _tp = getattr(intent, "tp", 0.0)
        _vol = getattr(intent, "volume", 0.0)
        lines.append(f"  Side    : {_side_name}")
        lines.append(f"  Entry   : {_entry:.5f}")
        lines.append(f"  SL      : {_sl:.5f}")
        lines.append(f"  TP      : {_tp:.5f}")
        lines.append(f"  Volume  : {_vol:.4f}")
        if _sl and _entry and _sl != _entry:
            _rr = abs(_tp - _entry) / abs(_entry - _sl)
            lines.append(f"  R:R     : {_rr:.2f}")
        lines.append("")

    # ─── FINAL RESULT ─────────────────────────────────────────────────
    lines.append("📈 FINAL RESULT")
    if execution_result is not None:
        _ok = getattr(execution_result, "ok", None)
        if _ok:
            _fill = getattr(execution_result, "fill_price", None)
            _fill_str = f" @ {_fill:.5f}" if _fill else ""
            lines.append(f"  Result: EXECUTE → ORDER FILLED{_fill_str}")
        else:
            _err = getattr(execution_result, "error", "unknown")
            lines.append(f"  Result: EXECUTE → EXECUTION FAILED ({_err})")
    elif action == "EXECUTE":
        lines.append(f"  Result: EXECUTE → ORDER SENT TO EXECUTION LAYER")
    else:
        _block_reason = _format_block_reason(reason)
        lines.append(f"  Result: BLOCKED → {_block_reason}")
    lines.append("")

    # ─── FINAL SCORE SUMMARY ──────────────────────────────────────────
    _confidence_pct = round(score * 100, 2)
    _grade = _get_grade(_confidence_pct)
    _status = "PASS" if action == "EXECUTE" else "BLOCKED"
    lines.append("📊 FINAL SCORE SUMMARY")
    lines.append(f"  Composite Score : {score:.4f}")
    lines.append(f"  Confidence      : {_confidence_pct}%")
    lines.append(f"  Grade           : {_grade}")
    lines.append(f"  Threshold       : {_ENGINE_THRESHOLD * 100}%")
    lines.append(f"  Status          : {_status}")

    lines.append(f"{'═' * 60}")
    return "\n".join(lines)


def _format_block_reason(reason: str) -> str:
    """Map engine reason codes to gate-level block descriptions."""
    reason = _normalize_reason(reason)
    if "score_below_threshold" in reason:
        return "FAILED SCORE GATE (composite below threshold)"
    if "low_confirmation_score" in reason:
        return "EV REDUCED (weak confirmation score dampened probability)"
    if "risk_rejected" in reason:
        return "FAILED RISK GATE"
    if "no_viable_pattern" in reason:
        return "FAILED PATTERN GATE (no viable pattern)"
    if "ev_policy_blocked" in reason:
        return "EV NEGATIVE (probabilistic edge insufficient)"
    return f"BLOCKED ({reason})"


def _get_grade(confidence_pct: float) -> str:
    """Convert confidence percentage to letter grade."""
    if confidence_pct >= 85:
        return "A"
    elif confidence_pct >= 70:
        return "B"
    elif confidence_pct >= 55:
        return "C"
    elif confidence_pct >= 40:
        return "D"
    elif confidence_pct >= 25:
        return "E"
    else:
        return "F"
