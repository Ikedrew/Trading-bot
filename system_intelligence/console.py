"""
System Intelligence Observer — Natural Language Console.

Usage:
    python -m system_intelligence.console

Type any question directly. The Observer classifies intent, routes to
relevant domains, retrieves evidence, and returns a structured answer.

Examples:
    > Is the bot running?
    > Why didn't EURUSD trade today?
    > What is the win rate?
    > Which guard blocks the most?
    > What config is active?
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from system_intelligence import Observer
from system_intelligence.intent import classify_intent, Intent


def _safe_print(text: str) -> None:
    """Print with ASCII fallback for Windows terminals."""
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("ascii", errors="replace").decode("ascii"))


def _print_dict(d: dict, indent: int = 0) -> None:
    """Pretty-print a nested dict."""
    prefix = "  " * indent
    for k, v in d.items():
        if isinstance(v, dict):
            _safe_print(f"{prefix}{k}:")
            _print_dict(v, indent + 1)
        elif isinstance(v, list) and len(v) > 5:
            _safe_print(f"{prefix}{k}: [{len(v)} items]")
        else:
            _safe_print(f"{prefix}{k}: {v}")


def _handle_intent(obs: Observer, intent: Intent) -> bool:
    """Dispatch based on classified intent. Returns False to exit."""

    if intent.action == "state":
        result = obs.state()
        _safe_print("")
        _safe_print(f"  Status:    {result['status']}")
        _safe_print(f"  Mode:      {result['execution_mode']}")
        _safe_print(f"  Last seen: {result['last_heartbeat'] or 'never'}")
        _safe_print(f"  MT5:       {result['mt5_state']}")
        _safe_print(f"  Strategy:  {result['strategy']}")
        _safe_print(f"  Symbols:   {len(result['symbols'])} pairs")

    elif intent.action == "health":
        result = obs.health()
        _safe_print("")
        s = result["summary"]
        _safe_print(f"  {s['healthy']} healthy / {s['stale']} stale / {s['empty']} empty (of {s['total']})")
        _safe_print("")
        for ds in result["datasets"]:
            icon = "+" if ds["status"] == "HEALTHY" else ("~" if ds["status"] == "STALE" else "-")
            age = f"{ds['age_hours']}h" if ds["age_hours"] is not None else "n/a"
            _safe_print(f"  [{icon}] {ds['name']:30s} {ds['records']:>5} records  ({age})")

    elif intent.action == "config":
        result = obs.config()
        _safe_print("")
        _print_dict(result)

    elif intent.action == "explain":
        symbol = intent.symbol or "EURUSD"
        result = obs.explain(symbol)
        _safe_print("")
        _safe_print(f"  Symbol:   {result['symbol']}")
        _safe_print(f"  Decision: {result['decision']}")
        _safe_print(f"  Reason:   {result['reason']}")
        _safe_print(f"  Time:     {result.get('timestamp', '?')}")
        ev = result.get("evidence", {})
        if ev.get("terminal_stage"):
            _safe_print(f"  Stage:    {ev['terminal_stage']}")
        if ev.get("terminal_reason"):
            _safe_print(f"  Detail:   {ev['terminal_reason']}")
        if ev.get("score_strategy"):
            _safe_print(f"  Score:    {ev['score_strategy']}")
        if ev.get("weakest_component"):
            _safe_print(f"  Weakest:  {ev['weakest_component']}")
        if ev.get("pattern_name"):
            _safe_print(f"  Pattern:  {ev['pattern_name']}")
        if ev.get("regime"):
            _safe_print(f"  Regime:   {ev['regime']}")
        cs = ev.get("causal_signature")
        if cs:
            _safe_print(f"  Chain:    {str(cs).encode('ascii',errors='replace').decode('ascii')}")

    elif intent.action == "trade":
        trade_id = intent.trade_id
        if not trade_id:
            _safe_print("  Specify a trade_id (e.g., pos_12345)")
            return True
        result = obs.explain_by_trade(trade_id)
        _safe_print("")
        if not result.get("found"):
            _safe_print(f"  Trade '{trade_id}' not found in journal.")
        else:
            _safe_print(f"  {result['explanation']}")
            _safe_print(f"  Symbol:  {result['symbol']} {result['direction']}")
            _safe_print(f"  Horizon: {result.get('trade_horizon', '?')}")
            _safe_print(f"  Entry:   {result['entry_price']} -> Exit: {result['exit_price']}")
            _safe_print(f"  PnL:     {result['net_pnl']}")

    elif intent.action == "trades":
        result = obs.trades(days=30)
        _safe_print("")
        if result["total_trades"] == 0:
            _safe_print("  No trades in the last 30 days.")
        else:
            _safe_print(f"  Trades:   {result['total_trades']} ({result['wins']}W / {result['losses']}L)")
            _safe_print(f"  Win Rate: {result['win_rate']:.1%}")
            _safe_print(f"  Avg R:    {result['avg_r_multiple']}")
            _safe_print(f"  PnL:      {result['total_pnl']}")
            _safe_print(f"  Avg Hold: {result['avg_duration_minutes']:.0f} min")
            if result.get("by_pattern"):
                _safe_print("  Top patterns:")
                for p, d in sorted(result["by_pattern"].items(), key=lambda x: x[1]["pnl"], reverse=True)[:5]:
                    _safe_print(f"    {p}: {d['count']} trades, PnL={d['pnl']}")

    elif intent.action == "guards":
        result = obs.guards()
        _safe_print("")
        _safe_print(f"  Total blocks: {result['total_blocks']}")
        _safe_print(f"  Most blocking: {result['most_blocking_guard']}")
        if result.get("by_guard"):
            for guard, count in list(result["by_guard"].items())[:5]:
                _safe_print(f"    {guard}: {count}")

    elif intent.action == "domains":
        result = obs.domains_list()
        _safe_print("")
        _safe_print(f"  {len(result)} architecture domains:")
        for name, desc in result.items():
            _safe_print(f"    {name:22s} {desc}")

    elif intent.action == "route":
        result = obs.route(intent.question)
        _safe_print("")
        if not result["routed"]:
            _safe_print(f"  I don't know how to answer that directly.")
            _safe_print(f"  Try: is it running? / why didn't X trade? / what's the win rate? / which guard blocks?")
        else:
            for d in result["domains"]:
                _safe_print(f"  [{d['domain']}] {d['description']}")
                _safe_print(f"    Look in: {', '.join(d['evidence_sources'])}")
                _safe_print(f"    Answers: {'; '.join(d['answers'][:3])}")
                _safe_print("")

    elif intent.action == "help":
        _safe_print("")
        _safe_print("  Ask me anything about the trading system. Examples:")
        _safe_print("")
        _safe_print("    Is the bot running?")
        _safe_print("    Why didn't EURUSD trade?")
        _safe_print("    What is the win rate?")
        _safe_print("    Which guard blocks the most?")
        _safe_print("    What config is active?")
        _safe_print("    Are datasets healthy?")
        _safe_print("    What domains do you understand?")
        _safe_print("    What patterns make money?")
        _safe_print("")
        _safe_print("  Direct commands still work: state, health, config, trades, guards, domains")
        _safe_print("  Type 'exit' to quit.")

    else:
        _safe_print(f"  I'm not sure how to help with that. Type 'help' for examples.")

    return True


def main() -> None:
    """Run the natural language Observer console."""
    _safe_print("")
    _safe_print("SYSTEM INTELLIGENCE OBSERVER")
    _safe_print("----------------------------")
    _safe_print("Ask me anything about the trading system.")
    _safe_print("Type 'help' for examples or 'exit' to quit.")
    _safe_print("")

    obs = Observer()

    while True:
        try:
            raw = input("observer> ").strip()
        except (EOFError, KeyboardInterrupt):
            _safe_print("\nGoodbye.")
            break

        if not raw:
            continue

        if raw.lower() in ("exit", "quit", "q"):
            _safe_print("Goodbye.")
            break

        intent = classify_intent(raw)
        _handle_intent(obs, intent)
        _safe_print("")


if __name__ == "__main__":
    main()
