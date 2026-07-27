"""
Forensic Logger — Per-instrument gate-by-gate decision traceability.

Routes all decision logs to pair-specific Discord channels.
Every decision is fully reconstructable from logs alone.

Channel mapping: {SYMBOL} → pair-{symbol_lower}
    EURUSD_SB → pair-eurusd
    GBPUSD_SB → pair-gbpusd

Timestamp standard (every log includes):
    - timestamp_utc (system logic truth)
    - timestamp_local (human debugging)
    - timestamp_mt5 (market alignment)

Architecture:
    - Called AFTER each gate produces its output
    - Purely observational (try/except: pass everywhere)
    - Does NOT influence any decision logic
    - One instrument = one isolated stream of truth

Design: deterministic, passive, no side effects on execution.
"""

from __future__ import annotations

from typing import Any

from core.pipeline.timestamps import format_timestamp_line


# ─── CHANNEL ROUTING ──────────────────────────────────────────────────────────

def _pair_channel(symbol: str) -> str:
    """Convert symbol to Discord channel name: EURUSD_SB → pair-eurusd"""
    if not symbol:
        return "pair-unknown"
    base = symbol.lower().replace("_sb", "").replace("_", "")
    return f"pair-{base}"


def _send(symbol: str, message: str) -> None:
    """Send message to pair channel. Silent on failure."""
    try:
        from core.discord_notifier import send_discord
        channel = _pair_channel(symbol)
        if len(message) > 1950:
            message = message[:1947] + "..."
        send_discord(channel, message)
    except Exception:
        pass


# ─── GATE 1: SIGNAL FORMATION ─────────────────────────────────────────────────

def log_gate1(
    *,
    symbol: str,
    pattern_detected: bool,
    pattern_type: str,
    strategy_classification: str,
    confidence: float,
    decision: str,
    mt5_time: float | None = None,
) -> None:
    """Log Gate 1 signal formation result to pair channel."""
    try:
        _ts = format_timestamp_line(mt5_time)
        _icon = "✔" if decision == "VALID" else "⏳" if decision == "WAIT" else "✖"
        msg = (
            f"🟦 **GATE 1: SIGNAL FORMATION** | `{symbol}`\n"
            f"{_ts}\n"
            f"```\n"
            f"Pattern:    {_icon} {'DETECTED' if pattern_detected else 'NONE'}\n"
            f"Type:       {pattern_type}\n"
            f"Strategy:   {strategy_classification} ({confidence:.2f})\n"
            f"Decision:   {decision}\n"
            f"```"
        )
        _send(symbol, msg)
    except Exception:
        pass


# ─── GATE 2: MARKET VALIDATION + SCORING ──────────────────────────────────────

def log_gate2(
    *,
    symbol: str,
    score_neutral: float,
    score_strategy: float,
    ev: float | None,
    market_state: str,
    invalidators: list[str] | None = None,
    decision: str,
    mt5_time: float | None = None,
) -> None:
    """Log Gate 2 market validation result to pair channel."""
    try:
        _ts = format_timestamp_line(mt5_time)
        _icon = "✔" if decision == "SUPPORT" else "⚠" if decision == "WEAK" else "✖"
        _inv = ", ".join(invalidators) if invalidators else "none"
        _ev_str = f"{ev:+.6f}" if ev is not None else "N/A"
        msg = (
            f"🟨 **GATE 2: MARKET VALIDATION** | `{symbol}`\n"
            f"{_ts}\n"
            f"```\n"
            f"Score (neutral):  {score_neutral:.4f}\n"
            f"Score (strategy): {score_strategy:.4f}\n"
            f"EV:               {_ev_str}\n"
            f"Market State:     {market_state}\n"
            f"Invalidators:     {_inv}\n"
            f"Decision:         {_icon} {decision}\n"
            f"```"
        )
        _send(symbol, msg)
    except Exception:
        pass


# ─── GATE 3: EXECUTION STRUCTURE ──────────────────────────────────────────────

def log_gate3(
    *,
    symbol: str,
    rr: float,
    min_rr_required: float,
    top_k_rank: int | None = None,
    bias_state: str,
    execution_policy: str,
    decision: str,
    mt5_time: float | None = None,
) -> None:
    """Log Gate 3 execution structure result to pair channel."""
    try:
        _ts = format_timestamp_line(mt5_time)
        _icon = "✔" if decision == "APPROVE" else "⏳" if decision == "HOLD" else "✖"
        _rank = f"#{top_k_rank}" if top_k_rank else "N/A"
        msg = (
            f"🟧 **GATE 3: EXECUTION STRUCTURE** | `{symbol}`\n"
            f"{_ts}\n"
            f"```\n"
            f"RR:            {rr:.2f} (min: {min_rr_required:.2f})\n"
            f"Rank:          {_rank}\n"
            f"Bias:          {bias_state}\n"
            f"Policy:        {execution_policy}\n"
            f"Decision:      {_icon} {decision}\n"
            f"```"
        )
        _send(symbol, msg)
    except Exception:
        pass


# ─── GATE 4: FINAL EXECUTION ──────────────────────────────────────────────────

def log_gate4(
    *,
    symbol: str,
    final_decision: str,
    reason: str,
    order_type: str | None = None,
    size: float = 0.0,
    dry_run: bool = True,
    mt5_time: float | None = None,
) -> None:
    """Log Gate 4 final execution decision to pair channel."""
    try:
        _ts = format_timestamp_line(mt5_time)
        _icon = "🟢" if final_decision == "EXECUTE" else "🔴"
        _order = order_type or "N/A"
        msg = (
            f"🟥 **GATE 4: EXECUTION DECISION** | `{symbol}`\n"
            f"{_ts}\n"
            f"```\n"
            f"Decision:  {_icon} {final_decision}\n"
            f"Reason:    {reason}\n"
            f"Order:     {_order}\n"
            f"Size:      {size:.4f}\n"
            f"Dry Run:   {dry_run}\n"
            f"```"
        )
        _send(symbol, msg)
    except Exception:
        pass


# ─── POST-TRADE ENTRY SNAPSHOT ────────────────────────────────────────────────

def log_trade_entry(
    *,
    symbol: str,
    trade_id: str,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    profile: str,
    regime_label: str,
    side: str,
) -> None:
    """Log trade entry snapshot to pair channel."""
    try:
        risk = abs(entry_price - stop_loss)
        reward = abs(take_profit - entry_price)
        rr = reward / risk if risk > 0 else 0.0
        msg = (
            f"📌 **TRADE ENTERED** | `{symbol}`\n"
            f"```\n"
            f"Trade ID:  {trade_id}\n"
            f"Side:      {side}\n"
            f"Entry:     {entry_price:.5f}\n"
            f"SL:        {stop_loss:.5f}\n"
            f"TP:        {take_profit:.5f}\n"
            f"RR:        {rr:.2f}\n"
            f"Profile:   {profile}\n"
            f"Regime:    {regime_label}\n"
            f"```"
        )
        _send(symbol, msg)
    except Exception:
        pass


# ─── TLSM LIFECYCLE UPDATE ────────────────────────────────────────────────────

def log_tlsm_update(
    *,
    symbol: str,
    trade_id: str,
    phase: str,
    r_multiple: float,
    peak_r: float,
    drawdown_r: float,
    bars_elapsed: int,
    exit_signal: str,
) -> None:
    """
    Log TLSM lifecycle update to pair channel.

    Only emits on phase transitions or exit signals to avoid spam.
    """
    try:
        # Only log meaningful updates (phase change or exit signal)
        if exit_signal == "NONE":
            return  # Silent — no phase transition, no exit
        msg = (
            f"📉 **TLSM UPDATE** | `{symbol}` | `{trade_id}`\n"
            f"```\n"
            f"Phase:     {phase}\n"
            f"R:         {r_multiple:+.2f}\n"
            f"Peak:      {peak_r:.2f}R\n"
            f"Drawdown:  {drawdown_r:.2f}R\n"
            f"Bars:      {bars_elapsed}\n"
            f"Exit:      {exit_signal}\n"
            f"```"
        )
        _send(symbol, msg)
    except Exception:
        pass


# ─── TLSM PHASE TRANSITION (ALWAYS LOGGED) ───────────────────────────────────

def log_tlsm_transition(
    *,
    symbol: str,
    trade_id: str,
    old_phase: str,
    new_phase: str,
    r_multiple: float,
    bars_elapsed: int,
) -> None:
    """Log TLSM phase transition to pair channel. Always emits."""
    try:
        msg = (
            f"🔄 **TLSM TRANSITION** | `{symbol}` | `{trade_id}`\n"
            f"```\n"
            f"Transition: {old_phase} → {new_phase}\n"
            f"R:          {r_multiple:+.2f}\n"
            f"Bars:       {bars_elapsed}\n"
            f"```"
        )
        _send(symbol, msg)
    except Exception:
        pass


# ─── TRADE EXIT ───────────────────────────────────────────────────────────────

def log_trade_exit(
    *,
    symbol: str,
    trade_id: str,
    exit_reason: str,
    r_multiple: float,
    peak_r: float,
    bars_held: int,
    profile: str,
) -> None:
    """Log trade exit to pair channel."""
    try:
        _icon = "✅" if r_multiple > 0 else "❌"
        msg = (
            f"{_icon} **TRADE CLOSED** | `{symbol}` | `{trade_id}`\n"
            f"```\n"
            f"Exit:      {exit_reason}\n"
            f"Result:    {r_multiple:+.2f}R\n"
            f"Peak:      {peak_r:.2f}R\n"
            f"Bars held: {bars_held}\n"
            f"Profile:   {profile}\n"
            f"```"
        )
        _send(symbol, msg)
    except Exception:
        pass


# ─── FULL CYCLE SUMMARY (CALLED FROM ENGINE OUTPUT) ───────────────────────────

def log_full_cycle(
    *,
    symbol: str,
    cycle_id: int,
    engine_result: dict[str, Any],
    mt5_time: float | None = None,
) -> None:
    """
    Emit a consolidated per-cycle forensic trace covering all gates.

    Called once per symbol per cycle after engine produces result.
    Determines gate outcomes from engine_result fields and logs each gate.
    """
    try:
        action = engine_result.get("action", "NO_TRADE")
        reason = engine_result.get("reason", "")
        score = engine_result.get("score", 0.0)
        pattern = engine_result.get("pattern", "")
        strategy = engine_result.get("strategy", "?")
        strategy_confidence = engine_result.get("strategy_confidence", 0.0)
        score_neutral = engine_result.get("score_neutral", 0.0)
        score_strategy = engine_result.get("score_strategy", 0.0)
        ev = engine_result.get("ev")
        market_state = engine_result.get("market_state", "?")
        rr = engine_result.get("rr_effective", 0.0)
        required_rr = engine_result.get("policy_required_rr", 0.0)
        policy_allowed = engine_result.get("policy_trade_allowed", False)
        bias_phase = engine_result.get("_bias_phase", "?")

        # Gate 1
        g1_decision = "VALID" if pattern else "INVALID"
        log_gate1(
            symbol=symbol,
            pattern_detected=bool(pattern),
            pattern_type=pattern or "NONE",
            strategy_classification=strategy or "?",
            confidence=strategy_confidence,
            decision=g1_decision,
            mt5_time=mt5_time,
        )

        # Gate 2
        invalidators = []
        if "score_below_threshold" in reason:
            invalidators.append("score_below_threshold")
        if "policy_blocked" in reason:
            if "CHOP" in reason:
                invalidators.append("market_state_chop")
            if "NEUTRAL_SCORE" in reason:
                invalidators.append("neutral_score_low")
            if "CONFIDENCE" in reason:
                invalidators.append("strategy_confidence_low")

        g2_decision = "SUPPORT" if score_neutral >= 0.30 and policy_allowed else \
                      "WEAK" if score_neutral >= 0.20 else "REJECT"
        log_gate2(
            symbol=symbol,
            score_neutral=score_neutral,
            score_strategy=score_strategy,
            ev=ev,
            market_state=market_state,
            invalidators=invalidators or None,
            decision=g2_decision,
            mt5_time=mt5_time,
        )

        # Gate 3
        g3_policy = "ALLOWED" if policy_allowed else "BLOCKED"
        g3_decision = "APPROVE" if policy_allowed and action == "EXECUTE" else \
                      "HOLD" if policy_allowed else "REJECT"
        log_gate3(
            symbol=symbol,
            rr=rr,
            min_rr_required=required_rr,
            bias_state=bias_phase,
            execution_policy=g3_policy,
            decision=g3_decision,
            mt5_time=mt5_time,
        )

        # Gate 4
        _size = 0.0
        _order = None
        _dry = True
        intent = engine_result.get("intent")
        if intent and action == "EXECUTE":
            _order = getattr(intent, "side", None)
            _order = _order.name if hasattr(_order, "name") else str(_order)
            _size = getattr(intent, "volume", 0.0)
            from core import config
            _dry = getattr(config, "DRY_RUN", True)

        log_gate4(
            symbol=symbol,
            final_decision=action,
            reason=reason or "passed all gates",
            order_type=_order,
            size=_size,
            dry_run=_dry,
            mt5_time=mt5_time,
        )

    except Exception:
        pass  # Forensic logging must never affect execution
