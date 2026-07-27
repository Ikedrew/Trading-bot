"""
Decision Replay Engine — Frame-by-frame reconstruction of trade decisions.

PRIMARY SOURCE: events/{YYYY-MM-DD}.jsonl (unified event ledger)

Reads a single time-ordered JSONL stream and reconstructs the full causal chain:
    CANDLE → ENTITY → STRATEGY → DECISION → EXECUTION → OUTCOME

All events share ts_utc_ms ordering. No cross-file joins required.

Usage:
    from tools.replay_engine.replay import DecisionReplayEngine

    engine = DecisionReplayEngine()
    result = engine.replay_by_timestamp("EURUSD", 1781950033)
    result = engine.replay_by_trade_id("t001")

    for frame in result.frames:
        print(frame)

Design: read-only, deterministic, offline analysis tool.
         Never modifies source files. Never interacts with MT5.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ─── OUTPUT STRUCTURES ────────────────────────────────────────────────────────

@dataclass
class ReplayFrame:
    """Single frame in the chronological replay timeline."""
    ts_utc_ms: int
    timestamp_utc: str
    frame_type: str  # CANDLE | ENTITY | STRATEGY | DECISION | EXECUTION | OUTCOME
    symbol: str
    payload: dict[str, Any]
    source: str = ""

    # Causal linkage fields (extracted from payload for easy access)
    entity_id: str = ""
    cycle_id: int = 0


@dataclass
class ReplayResult:
    """Complete replay output — chronological sequence of frames."""
    symbol: str
    query_type: str
    query_value: str
    time_window_start_ms: int
    time_window_end_ms: int
    total_frames: int
    frames: list[ReplayFrame] = field(default_factory=list)

    # Summary counts
    candle_frames: int = 0
    entity_frames: int = 0
    strategy_frames: int = 0
    decision_frames: int = 0
    execution_frames: int = 0
    outcome_frames: int = 0

    def print_summary(self) -> str:
        start = datetime.fromtimestamp(self.time_window_start_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
        end = datetime.fromtimestamp(self.time_window_end_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
        return (
            f"{'='*60}\n"
            f"REPLAY: {self.symbol} ({self.query_type}={self.query_value})\n"
            f"Window: {start} -> {end}\n"
            f"Frames: {self.total_frames} total\n"
            f"  CANDLE:    {self.candle_frames}\n"
            f"  ENTITY:    {self.entity_frames}\n"
            f"  STRATEGY:  {self.strategy_frames}\n"
            f"  DECISION:  {self.decision_frames}\n"
            f"  EXECUTION: {self.execution_frames}\n"
            f"  OUTCOME:   {self.outcome_frames}\n"
            f"{'='*60}\n"
        )


# ─── UNIFIED LEDGER READER ───────────────────────────────────────────────────

def _dates_in_range(start_ms: int, end_ms: int) -> list[str]:
    """Generate all date strings (YYYY-MM-DD) between start and end millis."""
    dates: list[str] = []
    day_ms = 86_400_000
    current = start_ms - (start_ms % day_ms)  # Start of day
    while current <= end_ms:
        dt = datetime.fromtimestamp(current / 1000, tz=timezone.utc)
        dates.append(dt.strftime("%Y-%m-%d"))
        current += day_ms
    return dates


def _read_events_for_window(
    start_ms: int,
    end_ms: int,
    *,
    symbol: str | None = None,
    event_type: str | None = None,
    event_dir: str = "events",
) -> list[dict[str, Any]]:
    """
    Read events from the unified ledger within a time window.

    Args:
        start_ms: Start of window (UTC millis, inclusive)
        end_ms: End of window (UTC millis, inclusive)
        symbol: Filter by symbol (None = all)
        event_type: Filter by type (None = all)
        event_dir: Path to events directory

    Returns:
        List of event dicts, sorted by ts_utc_ms.
    """
    events: list[dict[str, Any]] = []
    base_dir = Path(event_dir)

    for date_str in _dates_in_range(start_ms, end_ms):
        filepath = base_dir / f"{date_str}.jsonl"
        if not filepath.exists():
            continue

        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                ts = event.get("ts_utc_ms", 0)
                if ts < start_ms or ts > end_ms:
                    continue
                if symbol and event.get("symbol") != symbol:
                    continue
                if event_type and event.get("type") != event_type:
                    continue

                events.append(event)

    return sorted(events, key=lambda e: e.get("ts_utc_ms", 0))


# ─── MAIN ENGINE ──────────────────────────────────────────────────────────────

class DecisionReplayEngine:
    """
    Reconstructs trade decisions from the unified event ledger.

    Single read path: events/{YYYY-MM-DD}.jsonl
    No cross-file joins. No legacy dependencies.
    """

    def __init__(
        self,
        event_dir: str = "events",
        context_window_ms: int = 3_600_000,  # +/- 1 hour default
    ) -> None:
        self._event_dir = event_dir
        self._context_window_ms = context_window_ms

    def replay_by_timestamp(
        self,
        symbol: str,
        target_ms: int,
        window_ms: int | None = None,
    ) -> ReplayResult:
        """
        Replay all events around a specific UTC millisecond timestamp.

        Args:
            symbol: Trading symbol (e.g. "EURUSD")
            target_ms: UTC millisecond timestamp to investigate
            window_ms: Override context window (default: +/- 1 hour)
        """
        window = window_ms or self._context_window_ms
        start_ms = target_ms - window
        end_ms = target_ms + window

        return self._build_replay(symbol, "timestamp", str(target_ms), start_ms, end_ms)

    def replay_by_trade_id(
        self,
        trade_id: str,
        window_ms: int | None = None,
    ) -> ReplayResult:
        """
        Replay all events around a specific trade (finds OUTCOME event first).
        """
        symbol, ts_ms = self._find_trade_in_ledger(trade_id)
        if symbol is None or ts_ms is None:
            return ReplayResult(
                symbol="UNKNOWN", query_type="trade_id",
                query_value=trade_id, time_window_start_ms=0,
                time_window_end_ms=0, total_frames=0,
            )

        window = window_ms or self._context_window_ms
        return self._build_replay(symbol, "trade_id", trade_id, ts_ms - window, ts_ms + window)

    def replay_by_entity_id(
        self,
        entity_id: str,
        window_ms: int | None = None,
    ) -> ReplayResult:
        """
        Replay around a specific entity (e.g. "EURUSD_1782336300").
        """
        parts = entity_id.rsplit("_", 1)
        if len(parts) != 2:
            return ReplayResult(
                symbol="UNKNOWN", query_type="entity_id",
                query_value=entity_id, time_window_start_ms=0,
                time_window_end_ms=0, total_frames=0,
            )

        symbol = parts[0]
        try:
            target_seconds = float(parts[1])
            target_ms = int(target_seconds * 1000)
        except ValueError:
            return ReplayResult(
                symbol=symbol, query_type="entity_id",
                query_value=entity_id, time_window_start_ms=0,
                time_window_end_ms=0, total_frames=0,
            )

        window = window_ms or self._context_window_ms
        return self._build_replay(symbol, "entity_id", entity_id, target_ms - window, target_ms + window)

    def replay_by_cycle(
        self,
        symbol: str,
        cycle_id: int,
        date_str: str | None = None,
    ) -> ReplayResult:
        """
        Replay all events for a specific cycle (groups by cycle_id field).
        Scans the full day's ledger for matching cycle_id + symbol.
        """
        if date_str is None:
            from core.clock import utc_ms, utc_ms_to_date
            date_str = utc_ms_to_date(utc_ms())

        # Read full day, filter by symbol
        day_start = int(datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)
        day_end = day_start + 86_400_000

        all_events = _read_events_for_window(day_start, day_end, symbol=symbol, event_dir=self._event_dir)

        # Filter to matching cycle_id
        cycle_events = [
            e for e in all_events
            if e.get("payload", {}).get("cycle_id") == cycle_id
            or e.get("payload", {}).get("cycle_id") == cycle_id
        ]

        if not cycle_events:
            return ReplayResult(
                symbol=symbol, query_type="cycle_id",
                query_value=str(cycle_id), time_window_start_ms=day_start,
                time_window_end_ms=day_end, total_frames=0,
            )

        start_ms = min(e["ts_utc_ms"] for e in cycle_events)
        end_ms = max(e["ts_utc_ms"] for e in cycle_events)

        frames = [self._event_to_frame(e) for e in cycle_events]
        return self._assemble_result(symbol, "cycle_id", str(cycle_id), start_ms, end_ms, frames)

    # ─── INTERNAL ─────────────────────────────────────────────────────

    def _build_replay(
        self,
        symbol: str,
        query_type: str,
        query_value: str,
        start_ms: int,
        end_ms: int,
    ) -> ReplayResult:
        """Load events from unified ledger and assemble replay."""
        events = _read_events_for_window(
            start_ms, end_ms,
            symbol=symbol,
            event_dir=self._event_dir,
        )

        frames = [self._event_to_frame(e) for e in events]
        return self._assemble_result(symbol, query_type, query_value, start_ms, end_ms, frames)

    def _assemble_result(
        self,
        symbol: str,
        query_type: str,
        query_value: str,
        start_ms: int,
        end_ms: int,
        frames: list[ReplayFrame],
    ) -> ReplayResult:
        """Build ReplayResult with counts from frame list."""
        counts = {"CANDLE": 0, "ENTITY": 0, "STRATEGY": 0, "DECISION": 0, "EXECUTION": 0, "OUTCOME": 0}
        for f in frames:
            if f.frame_type in counts:
                counts[f.frame_type] += 1

        return ReplayResult(
            symbol=symbol,
            query_type=query_type,
            query_value=query_value,
            time_window_start_ms=start_ms,
            time_window_end_ms=end_ms,
            total_frames=len(frames),
            frames=frames,
            candle_frames=counts["CANDLE"],
            entity_frames=counts["ENTITY"],
            strategy_frames=counts["STRATEGY"],
            decision_frames=counts["DECISION"],
            execution_frames=counts["EXECUTION"],
            outcome_frames=counts["OUTCOME"],
        )

    def _event_to_frame(self, event: dict[str, Any]) -> ReplayFrame:
        """Convert a raw event dict into a ReplayFrame."""
        ts_ms = event.get("ts_utc_ms", 0)
        ts_iso = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{ts_ms % 1000:03d}Z"
        payload = event.get("payload", {})

        return ReplayFrame(
            ts_utc_ms=ts_ms,
            timestamp_utc=ts_iso,
            frame_type=event.get("type", "UNKNOWN"),
            symbol=event.get("symbol", ""),
            payload=payload,
            source=event.get("source", ""),
            entity_id=payload.get("entity_id", ""),
            cycle_id=payload.get("cycle_id", 0),
        )

    def _find_trade_in_ledger(self, trade_id: str) -> tuple[str | None, int | None]:
        """Search OUTCOME events in recent ledger files for a trade_id."""
        event_dir = Path(self._event_dir)
        if not event_dir.exists():
            return None, None

        # Search recent files (newest first)
        for filepath in sorted(event_dir.glob("*.jsonl"), reverse=True)[:7]:
            with open(filepath, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if event.get("type") != "OUTCOME":
                        continue
                    payload = event.get("payload", {})
                    if payload.get("trade_id") == trade_id:
                        return event.get("symbol"), event.get("ts_utc_ms")

        return None, None


# ─── CLI INTERFACE ────────────────────────────────────────────────────────────

def print_replay(result: ReplayResult) -> None:
    """Print a human-readable chronological replay to stdout."""
    print(result.print_summary())

    for i, frame in enumerate(result.frames):
        ts = frame.timestamp_utc
        prefix = f"[{i+1:04d}] {ts} | {frame.frame_type:10s}"
        p = frame.payload

        if frame.frame_type == "CANDLE":
            print(f"{prefix} | O={p.get('o',0):.5f} H={p.get('h',0):.5f} L={p.get('l',0):.5f} C={p.get('c',0):.5f} V={p.get('v',0)}")

        elif frame.frame_type == "ENTITY":
            print(f"{prefix} | {p.get('event_type','?'):7s} | {p.get('from_room','?')}->{p.get('to_room','?')} | score={p.get('data',{}).get('score',0):.3f} | {p.get('decision',{}).get('reason','')}")

        elif frame.frame_type == "STRATEGY":
            sel = p.get("selection", {})
            print(f"{prefix} | regime={p.get('regime',{}).get('current','?')} | selected={sel.get('selected_strategy','NONE')}({sel.get('selected_weight',0):.2f}) | pattern={p.get('mapping',{}).get('pattern','?')}")

        elif frame.frame_type == "DECISION":
            action = "TRADE" if p.get("should_trade") else "NO_TRADE"
            print(f"{prefix} | {action} | score={p.get('score',0):.3f} | side={p.get('side','-')} | reason={p.get('reason','')[:60]}")

        elif frame.frame_type == "EXECUTION":
            print(f"{prefix} | {p.get('status','?')} | fill={p.get('fill_price',0):.5f} | slippage={p.get('slippage',0):.6f} | latency={p.get('fill_latency_ms',0)}ms")

        elif frame.frame_type == "OUTCOME":
            print(f"{prefix} | trade={p.get('trade_id','?')} | RR={p.get('rr_realised',0):.2f} | pnl={p.get('pnl',0):.2f} | exit={p.get('exit_reason','?')} | dur={p.get('duration_ms',0)/1000:.0f}s")

        else:
            print(f"{prefix} | {json.dumps(p)[:80]}")

    print(f"\n{'='*60}")
    print(f"END OF REPLAY ({result.total_frames} frames)")


# ─── SCRIPT ENTRY POINT ───────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

    parser = argparse.ArgumentParser(description="Decision Replay Engine (Unified Ledger)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--trade-id", type=str, help="Replay by trade ID (searches OUTCOME events)")
    group.add_argument("--timestamp", type=int, help="Replay by UTC millisecond timestamp")
    group.add_argument("--entity-id", type=str, help="Replay by entity ID (e.g. EURUSD_1782336300)")
    group.add_argument("--cycle", type=int, help="Replay by cycle ID")

    parser.add_argument("--symbol", type=str, help="Symbol (required with --timestamp or --cycle)")
    parser.add_argument("--date", type=str, help="Date YYYY-MM-DD (for --cycle)")
    parser.add_argument("--window", type=int, default=3_600_000, help="Context window in milliseconds (default: 3600000)")
    parser.add_argument("--event-dir", type=str, default="events", help="Events directory (default: events)")

    args = parser.parse_args()

    engine = DecisionReplayEngine(
        event_dir=args.event_dir,
        context_window_ms=args.window,
    )

    if args.trade_id:
        result = engine.replay_by_trade_id(args.trade_id, window_ms=args.window)
    elif args.entity_id:
        result = engine.replay_by_entity_id(args.entity_id, window_ms=args.window)
    elif args.cycle is not None:
        if not args.symbol:
            parser.error("--symbol is required when using --cycle")
        result = engine.replay_by_cycle(args.symbol, args.cycle, date_str=args.date)
    else:
        if not args.symbol:
            parser.error("--symbol is required when using --timestamp")
        result = engine.replay_by_timestamp(args.symbol, args.timestamp, window_ms=args.window)

    print_replay(result)
