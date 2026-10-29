"""
Paper Outcome Engine — Post-trade outcome evaluation.

Records EXECUTED trades and tracks subsequent bars to determine
if SL or TP would have been hit at the intended levels.

RESTRICTION: This engine ONLY evaluates AFTER a trade has been executed.
It is NOT allowed to predict or simulate future trades.
It must NOT generate hypothetical performance before execution occurs.

Purpose:
    - Post-trade performance tracking ONLY
    - Outcome evaluation ONLY
    - NO predictive modelling

Usage:
    engine = PaperOutcomeEngine()
    engine.record_signal(...)  # ONLY after execution.place_market() succeeds
    engine.evaluate_pending(candles, closed_i)  # each cycle
    engine.report()  # periodic summary
"""

from __future__ import annotations

import json
import time as _time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PaperSignal:
    """A recorded EXECUTE signal awaiting outcome evaluation."""
    signal_id: str
    timestamp: float
    symbol: str
    source: str            # "old_system" or "new_engine"
    side: str              # "BUY" or "SELL"
    entry_price: float
    stop_loss: float
    take_profit: float
    pattern: str
    score: float
    bar_index_at_entry: int
    max_bars: int          # Timeout after N bars
    bars_elapsed: int = 0
    outcome: str = "PENDING"  # PENDING / WIN / LOSS / TIMEOUT
    exit_price: float | None = None
    exit_bar: int | None = None


class PaperOutcomeEngine:
    """
    Tracks hypothetical trade outcomes for both old and new systems.

    Call record_signal() when either system produces EXECUTE.
    Call evaluate_pending() every cycle with current candle data.
    Call report() periodically for accuracy summaries.
    """

    def __init__(self, max_bars: int = 60) -> None:
        """
        Args:
            max_bars: Maximum bars to track before TIMEOUT (default 60 = 5 hours on M5)
        """
        self._max_bars = max_bars
        self._pending: list[PaperSignal] = []
        self._completed: list[PaperSignal] = []
        self._signal_counter: int = 0

    def record_signal(
        self,
        *,
        symbol: str,
        source: str,
        side: str,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        pattern: str,
        score: float,
        bar_index: int,
    ) -> None:
        """Record an executed trade signal for post-trade outcome tracking.
        
        RESTRICTION: Only 'executed_trade' and 'old_system_shadow' sources are valid.
        This engine does NOT accept hypothetical/predictive signals.
        """
        # Allowed sources: executed trades + old system shadow (for comparison only)
        _VALID_SOURCES = ("executed_trade", "old_system_shadow", "new_engine", "old_system", "V10")
        if source not in _VALID_SOURCES:
            print(f"[PAPER] REJECTED invalid source '{source}' — only post-execution tracking allowed")
            return
        self._signal_counter += 1
        sig = PaperSignal(
            signal_id=f"{source}_{self._signal_counter}",
            timestamp=_time.time(),
            symbol=symbol,
            source=source,
            side=side,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            pattern=pattern,
            score=score,
            bar_index_at_entry=bar_index,
            max_bars=self._max_bars,
        )
        self._pending.append(sig)
        print(f"[PAPER] Recorded {source} signal: {symbol} {side} entry={entry_price:.5f} SL={stop_loss:.5f} TP={take_profit:.5f} pattern={pattern} score={score:.3f}")

    def evaluate_pending(self, symbol: str, high: float, low: float, close: float) -> list[PaperSignal]:
        """
        Check all pending signals against current bar's high/low.

        Call once per new bar per symbol.

        Args:
            symbol: Current symbol being evaluated
            high: Current bar's high price
            low: Current bar's low price
            close: Current bar's close price

        Returns:
            List of signals that resolved this bar.
        """
        resolved: list[PaperSignal] = []

        for sig in list(self._pending):
            if sig.symbol != symbol:
                continue
            if sig.outcome != "PENDING":
                continue

            sig.bars_elapsed += 1

            # Check SL/TP hit
            if sig.side == "BUY":
                if low <= sig.stop_loss:
                    sig.outcome = "LOSS"
                    sig.exit_price = sig.stop_loss
                elif high >= sig.take_profit:
                    sig.outcome = "WIN"
                    sig.exit_price = sig.take_profit
            else:  # SELL
                if high >= sig.stop_loss:
                    sig.outcome = "LOSS"
                    sig.exit_price = sig.stop_loss
                elif low <= sig.take_profit:
                    sig.outcome = "WIN"
                    sig.exit_price = sig.take_profit

            # Timeout
            if sig.outcome == "PENDING" and sig.bars_elapsed >= sig.max_bars:
                sig.outcome = "TIMEOUT"
                sig.exit_price = close

            # Resolved?
            if sig.outcome != "PENDING":
                sig.exit_bar = sig.bar_index_at_entry + sig.bars_elapsed
                resolved.append(sig)
                self._pending.remove(sig)
                self._completed.append(sig)
                print(f"[PAPER OUTCOME] {sig.source} | {sig.symbol} {sig.side} | {sig.outcome} | bars={sig.bars_elapsed} | pattern={sig.pattern} | score={sig.score:.3f}")

        return resolved

    def report(self) -> dict[str, Any]:
        """Generate accuracy report across all completed signals."""
        if not self._completed:
            return {"total": 0, "message": "No completed signals yet"}

        # Overall stats
        total = len(self._completed)
        wins = sum(1 for s in self._completed if s.outcome == "WIN")
        losses = sum(1 for s in self._completed if s.outcome == "LOSS")
        timeouts = sum(1 for s in self._completed if s.outcome == "TIMEOUT")

        # Per-source
        by_source: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "wins": 0, "losses": 0, "timeouts": 0})
        for s in self._completed:
            by_source[s.source]["total"] += 1
            if s.outcome == "WIN":
                by_source[s.source]["wins"] += 1
            elif s.outcome == "LOSS":
                by_source[s.source]["losses"] += 1
            else:
                by_source[s.source]["timeouts"] += 1

        # Per-pattern
        by_pattern: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "wins": 0, "losses": 0})
        for s in self._completed:
            by_pattern[s.pattern]["total"] += 1
            if s.outcome == "WIN":
                by_pattern[s.pattern]["wins"] += 1
            elif s.outcome == "LOSS":
                by_pattern[s.pattern]["losses"] += 1

        # Per-score band
        by_score_band: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "wins": 0, "losses": 0})
        for s in self._completed:
            if s.score >= 0.7:
                band = "HIGH (0.7+)"
            elif s.score >= 0.5:
                band = "MID (0.5-0.7)"
            else:
                band = "LOW (<0.5)"
            by_score_band[band]["total"] += 1
            if s.outcome == "WIN":
                by_score_band[band]["wins"] += 1
            elif s.outcome == "LOSS":
                by_score_band[band]["losses"] += 1

        report = {
            "total": total,
            "wins": wins,
            "losses": losses,
            "timeouts": timeouts,
            "win_rate": round(wins / max(1, wins + losses) * 100, 1),
            "pending": len(self._pending),
            "by_source": dict(by_source),
            "by_pattern": dict(by_pattern),
            "by_score_band": dict(by_score_band),
        }

        return report

    def print_report(self) -> None:
        """Print formatted accuracy report."""
        r = self.report()
        if r["total"] == 0:
            print("[PAPER REPORT] No completed signals yet")
            return

        print(f"\n{'='*60}")
        print(f"[PAPER OUTCOME REPORT]")
        print(f"  Total: {r['total']} | Wins: {r['wins']} | Losses: {r['losses']} | Timeouts: {r['timeouts']}")
        print(f"  Win Rate: {r['win_rate']}% | Pending: {r['pending']}")
        print(f"\n  BY SOURCE:")
        for src, stats in r["by_source"].items():
            wr = round(stats["wins"] / max(1, stats["wins"] + stats["losses"]) * 100, 1)
            print(f"    {src:15s}: {stats['total']} signals, {wr}% win rate")
        print(f"\n  BY PATTERN:")
        for pat, stats in r["by_pattern"].items():
            wr = round(stats["wins"] / max(1, stats["wins"] + stats["losses"]) * 100, 1)
            print(f"    {pat:25s}: {stats['total']} signals, {wr}% win rate")
        print(f"\n  BY SCORE BAND:")
        for band, stats in r["by_score_band"].items():
            wr = round(stats["wins"] / max(1, stats["wins"] + stats["losses"]) * 100, 1)
            print(f"    {band:15s}: {stats['total']} signals, {wr}% win rate")
        print(f"{'='*60}\n")


# Module-level singleton
_paper_engine: PaperOutcomeEngine | None = None


def get_paper_engine() -> PaperOutcomeEngine:
    """Get or create singleton paper outcome engine."""
    global _paper_engine
    if _paper_engine is None:
        _paper_engine = PaperOutcomeEngine(max_bars=60)
    return _paper_engine
