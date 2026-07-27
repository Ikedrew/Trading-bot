"""
Intent Classifier — Determines what the user is asking and routes to the right action.

Maps natural language questions to Observer methods + domain routing.
No NLP library required — keyword/pattern matching against known intents.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass
class Intent:
    """Classified user intent."""
    action: str          # "state", "config", "health", "explain", "trade", "guards", "route", "domains", "help"
    symbol: str = ""     # Extracted symbol if relevant
    trade_id: str = ""   # Extracted trade_id if relevant
    question: str = ""   # Original question for routing
    confidence: float = 0.0


# Symbol pattern: 3-letter + 3-letter currency pair (e.g., EURUSD, GBPUSD)
_SYMBOL_RE = re.compile(r"\b([A-Z]{6})\b")
# Trade ID pattern: pos_XXXXX
_TRADE_ID_RE = re.compile(r"\b(pos_\w+)\b")


# Intent patterns: (keywords/phrases, action, confidence)
_INTENT_PATTERNS: list[tuple[list[str], str, float]] = [
    # State / runtime
    (["is the bot running", "is it running", "bot status", "system status", "is it alive", "is it up"], "state", 0.9),
    (["running", "alive", "status", "uptime", "pid"], "state", 0.6),

    # Health
    (["dataset health", "data health", "are datasets", "persistence health", "stale data", "data fresh", "data quality"], "health", 0.9),
    (["healthy", "fresh", "stale"], "health", 0.5),

    # Config
    (["what config", "configuration", "what is enabled", "what limits", "permitted horizons", "feature flags"], "config", 0.9),
    (["enabled", "disabled", "setting", "parameter", "flag", "toggle"], "config", 0.5),

    # Guards / risk blocks
    (["which guard", "what blocks", "guard statistics", "most blocking", "risk block"], "guards", 0.9),
    (["blocked", "veto", "cooldown", "correlation guard", "spread guard"], "guards", 0.6),

    # Trades / performance
    (["how much money", "trade performance", "win rate", "pnl", "how many trades", "best pattern", "worst pattern"], "trades", 0.9),
    (["profit", "loss", "winning", "losing", "average r"], "trades", 0.5),
    (["hold time", "duration", "how long"], "trades", 0.6),

    # Explain decision (symbol-specific)
    (["why didn't", "why did not", "why no trade", "why was .* rejected", "why wasn't"], "explain", 0.9),
    (["why did .* trade", "why was .* executed", "why did .* execute"], "explain", 0.9),
    (["explain", "decision", "rejected", "approved"], "explain", 0.6),

    # Explain trade outcome
    (["why did .* lose", "why did .* win", "trade outcome", "why .* lost"], "trade", 0.9),

    # Domains
    (["what domains", "list domains", "architecture domains", "what does the observer know"], "domains", 0.9),

    # Help
    (["help", "commands", "what can you do", "how do i"], "help", 0.9),
]


def classify_intent(text: str) -> Intent:
    """
    Classify user input into an actionable intent.

    Strategy:
        1. Check for exact command matches (backward compat)
        2. Match against intent patterns
        3. Extract symbol/trade_id if present
        4. Fall back to domain routing for unrecognised questions
    """
    stripped = text.strip()
    lower = stripped.lower()

    # Exact command matches (preserve backward compat)
    if lower == "state":
        return Intent(action="state", confidence=1.0)
    if lower == "health":
        return Intent(action="health", confidence=1.0)
    if lower == "config":
        return Intent(action="config", confidence=1.0)
    if lower == "trades":
        return Intent(action="trades", confidence=1.0)
    if lower == "guards":
        return Intent(action="guards", confidence=1.0)
    if lower == "domains":
        return Intent(action="domains", confidence=1.0)
    if lower in ("help", "?"):
        return Intent(action="help", confidence=1.0)
    if lower.startswith("explain "):
        sym = stripped[8:].strip().upper()
        return Intent(action="explain", symbol=sym, confidence=1.0)

    # Extract symbol if present
    sym_match = _SYMBOL_RE.search(stripped.upper())
    symbol = sym_match.group(1) if sym_match else ""

    # Extract trade_id if present
    trade_match = _TRADE_ID_RE.search(stripped)
    trade_id = trade_match.group(1) if trade_match else ""

    # Pattern matching
    best_action = "route"
    best_confidence = 0.0

    for patterns, action, confidence in _INTENT_PATTERNS:
        for pattern in patterns:
            if ".*" in pattern:
                if re.search(pattern, lower):
                    if confidence > best_confidence:
                        best_action = action
                        best_confidence = confidence
            elif pattern in lower:
                if confidence > best_confidence:
                    best_action = action
                    best_confidence = confidence

    # If we matched explain/trade but have a symbol, use it
    if best_action == "explain" and symbol:
        return Intent(action="explain", symbol=symbol, question=stripped, confidence=best_confidence)
    if best_action == "trade" and trade_id:
        return Intent(action="trade", trade_id=trade_id, question=stripped, confidence=best_confidence)
    if best_action == "explain" and not symbol:
        # Try to extract symbol from question
        # e.g., "why didn't eurusd trade" → EURUSD
        for word in stripped.upper().split():
            if len(word) == 6 and word.isalpha():
                return Intent(action="explain", symbol=word, question=stripped, confidence=best_confidence)
        # No symbol found — route to domain instead
        return Intent(action="route", question=stripped, confidence=0.5)

    if best_action != "route":
        return Intent(action=best_action, symbol=symbol, trade_id=trade_id, question=stripped, confidence=best_confidence)

    # Fallback: route to domain
    return Intent(action="route", question=stripped, symbol=symbol, confidence=0.3)
