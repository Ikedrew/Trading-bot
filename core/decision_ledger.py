"""
Decision Ledger — Append-only structured log of EVERY decision cycle.

Records one entry per symbol per cycle regardless of outcome:
    EXECUTE, NO_TRADE, RISK_BLOCK, SESSION_BLOCK, PATTERN_REJECT

This is NOT trade logging — it is decision-level observability.
Every cycle produces exactly one persistent record.

Storage:
    Local:  logs/decision_ledger/{SYMBOL}/{YYYY-MM-DD}.jsonl
    S3:     s3://trading-bot-data-mk1/decision_ledger/symbol={SYMBOL}/date={YYYY-MM-DD}/

Performance:
    - Buffered writes (flush every N records or on timer)
    - Never blocks execution (fire-and-forget with background flush)
    - try/except: pass on all paths

Usage:
    from core.decision_ledger import get_ledger, DecisionOutcome

    ledger = get_ledger()
    ledger.record(
        symbol="EURUSD",
        cycle_id=42,
        decision=DecisionOutcome.NO_TRADE,
        reason="score_below_threshold",
        ...
    )
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time as _time
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# DECISION OUTCOME ENUM
# ═══════════════════════════════════════════════════════════════════════════════

class DecisionOutcome(str, Enum):
    """Canonical decision outcomes — every cycle resolves to exactly one."""
    EXECUTE = "EXECUTE"
    NO_TRADE = "NO_TRADE"
    RISK_BLOCK = "RISK_BLOCK"
    SESSION_BLOCK = "SESSION_BLOCK"
    PATTERN_REJECT = "PATTERN_REJECT"
    KILL_SWITCH = "KILL_SWITCH"
    DAILY_LOSS_BLOCK = "DAILY_LOSS_BLOCK"
    STALE_DATA = "STALE_DATA"
    FEED_BLOCKED = "FEED_BLOCKED"

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

_S3_BUCKET = "v10-engine"
_S3_PREFIX = "decision_ledger"
_LOCAL_DIR = "logs/decision_ledger"
_SCHEMA_VERSION = "decision_ledger_v1"
_FLUSH_INTERVAL_SECONDS = 30.0
_FLUSH_BATCH_SIZE = 50


# ═══════════════════════════════════════════════════════════════════════════════
# CAUSAL SIGNATURE BUILDER
# ═══════════════════════════════════════════════════════════════════════════════

def build_causal_signature(decision: dict[str, Any]) -> str:
    """
    Build a composite causal signature from a fully populated _cycle_decision.

    Format: <active_factors>→<terminal_guard>
    Example: session+pattern+regime+score→correlation_guard

    Factors appear in pipeline evaluation order (left to right = early to late).
    Terminal guard is the single condition that stopped execution.

    Rules:
        - Left side: ALL domains that were evaluated before exit
        - Right side: exactly ONE terminal condition
        - Order: reflects sequential evaluation (session first, execution last)
        - Never empty on either side
    """
    factors: list[str] = []

    # Build factor chain in pipeline evaluation order
    # Each factor is included if it was evaluated (reached in the pipeline)
    session = decision.get("session_state", "")
    if session and session != "unknown":
        factors.append("session")

    pattern = decision.get("pattern_state", "")
    if pattern and pattern != "none":
        factors.append("pattern")

    regime = decision.get("regime", "")
    if regime and regime != "unknown":
        factors.append("regime")

    score = decision.get("signal_score", 0)
    if score and score > 0:
        factors.append("score")

    risk_flag = decision.get("risk_flag", "") or (
        decision.get("risk_state", {}).get("risk_flag", "") if isinstance(decision.get("risk_state"), dict) else ""
    )
    drawdown = decision.get("drawdown_pct", 0) or (
        decision.get("risk_state", {}).get("drawdown_pct", 0) if isinstance(decision.get("risk_state"), dict) else 0
    )
    daily_loss = decision.get("daily_loss_pct", 0) or (
        decision.get("risk_state", {}).get("daily_loss_pct", 0) if isinstance(decision.get("risk_state"), dict) else 0
    )
    if risk_flag or drawdown > 0 or daily_loss > 0:
        factors.append("risk")

    if decision.get("execution_intent"):
        factors.append("execution")

    # Ensure at least one factor
    if not factors:
        factors.append("pipeline")

    # Determine terminal guard from decision + original reason
    terminal = _resolve_terminal(decision)

    return "+".join(factors) + "\u2192" + terminal


def _resolve_terminal(decision: dict[str, Any]) -> str:
    """
    Resolve the terminal guard name from decision state.

    Maps (decision_type, risk_flag, reason) to a canonical terminal identifier.
    """
    dec = decision.get("decision")
    if dec is None:
        return "unknown"

    # Use enum value if it's an enum
    dec_val = dec.value if hasattr(dec, "value") else str(dec)

    # Direct mapping for non-RISK_BLOCK decisions
    terminal_map = {
        "KILL_SWITCH": "kill_switch",
        "DAILY_LOSS_BLOCK": "daily_loss_guard",
        "SESSION_BLOCK": "session_guard",
        "PATTERN_REJECT": "pattern_gate",
        "EXECUTE": "execute",
    }
    if dec_val in terminal_map:
        return terminal_map[dec_val]

    # RISK_BLOCK: use risk_flag as terminal
    if dec_val == "RISK_BLOCK":
        flag = decision.get("risk_flag", "") or (
            decision.get("risk_state", {}).get("risk_flag", "") if isinstance(decision.get("risk_state"), dict) else ""
        )
        if flag:
            return flag
        return "risk_guard"

    # NO_TRADE: extract terminal from reason
    if dec_val == "NO_TRADE":
        reason = decision.get("reason", "")
        if "strategy_processing_error" in reason:
            return "processing_error"
        if "execution_failed" in reason:
            return "execution_failed"
        if "score_below" in reason:
            return "scoring_engine"
        if "policy_blocked" in reason:
            return "policy_gate"
        if "ev_policy_blocked" in reason:
            return "ev_policy"
        if "swing_blocked" in reason:
            return "swing_filter"
        if "risk_rejected" in reason:
            return "risk_check"
        if "data_invalid" in reason:
            return "data_validation"
        if "no_viable_pattern" in reason:
            return "pattern_viability"
        # Old pipeline stage-based reasons
        last_stage = decision.get("last_stage", "")
        if last_stage:
            return last_stage
        return "engine_reject"

    return "unknown"


def build_ledger_entry(
    *,
    symbol: str,
    cycle_id: int,
    decision: DecisionOutcome | str,
    reason: str = "",
    # Signal state
    signal_score: float = 0.0,
    signal_type: str | None = None,
    pattern_state: str = "none",
    # Market context
    regime: str = "unknown",
    session_state: str = "unknown",
    # Risk state
    drawdown_pct: float = 0.0,
    daily_loss_pct: float = 0.0,
    exposure_level: float = 0.0,
    risk_flag: str = "",
    # Execution intent (only for EXECUTE)
    execution_intent: dict[str, Any] | None = None,
    # Reasoning (observational — explains the decision)
    reasoning: dict[str, Any] | None = None,
    # Uncertainty (observational — measures ambiguity)
    uncertainty: dict[str, Any] | None = None,
    # Score attribution (observational — decomposes score into factors)
    score_attribution: dict[str, Any] | None = None,
    # Dual EV comparison (observational — shadow research model comparison)
    dual_ev: dict[str, Any] | None = None,
    # Linkage
    context_snapshot_id: str = "",
    correlation_id: str = "",
    entity_id: str = "",
    observation_id: str = "",
    # Performance
    decision_latency_ms: int = 0,
    # Metadata
    engine_version: str = "V10",
    last_stage: str = "",
) -> dict[str, Any]:
    """
    Build a single decision ledger entry.

    Every field is explicitly typed. No inference, no defaults that hide absence.
    """
    now = datetime.now(timezone.utc)

    decision_str = decision.value if isinstance(decision, DecisionOutcome) else str(decision)

    entry = {
        "timestamp": now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "timestamp_unix": round(now.timestamp(), 3),
        "symbol": symbol,
        "cycle_id": cycle_id,
        "decision": decision_str,
        "reason": reason,
        "regime": regime,
        "session_state": session_state,
        "signal_score": round(signal_score, 4),
        "signal_type": signal_type,
        "pattern_state": pattern_state,
        "last_stage": last_stage,
        "risk_state": {
            "drawdown_pct": round(drawdown_pct, 4),
            "daily_loss_pct": round(daily_loss_pct, 4),
            "exposure_level": round(exposure_level, 4),
            "risk_flag": risk_flag,
        },
        "execution_intent": execution_intent,
        "reasoning": reasoning,
        "uncertainty": uncertainty,
        "score_attribution": score_attribution,
        "dual_ev": dual_ev,
        "context_snapshot_id": context_snapshot_id,
        "correlation_id": correlation_id,
        "entity_id": entity_id,
        "observation_id": observation_id,
        "decision_latency_ms": decision_latency_ms,
        "engine_version": engine_version,
        "schema_version": _SCHEMA_VERSION,
        "causal_signature": "",  # Populated below
    }

    # Build composite causal signature from fully populated entry
    entry["causal_signature"] = build_causal_signature(entry)

    return entry


# ═══════════════════════════════════════════════════════════════════════════════
# DECISION LEDGER WRITER (buffered, thread-safe, fire-and-forget)
# ═══════════════════════════════════════════════════════════════════════════════

class DecisionLedgerWriter:
    """
    Buffered append-only writer for decision ledger records.

    Thread-safe. Never blocks caller. Flushes periodically or on batch size.
    Local JSONL is primary truth. S3 mirror is secondary (fire-and-forget).
    """

    def __init__(
        self,
        *,
        local_dir: str = _LOCAL_DIR,
        flush_interval: float = _FLUSH_INTERVAL_SECONDS,
        flush_batch_size: int = _FLUSH_BATCH_SIZE,
    ) -> None:
        self._local_dir = Path(local_dir)
        self._flush_interval = flush_interval
        self._flush_batch_size = flush_batch_size
        self._buffer: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._last_flush: float = _time.time()
        self._total_written: int = 0
        self._total_errors: int = 0

    def record(self, **kwargs: Any) -> None:
        """
        Record a decision cycle entry. Fire-and-forget. Never raises.

        Accepts same kwargs as build_ledger_entry().
        """
        try:
            entry = build_ledger_entry(**kwargs)
            with self._lock:
                # Hard ceiling: drop oldest if buffer exceeds 5x batch size
                # This prevents unbounded memory growth on persistent flush failure
                if len(self._buffer) >= self._flush_batch_size * 5:
                    self._buffer = self._buffer[-(self._flush_batch_size * 4):]
                    self._total_errors += 1
                self._buffer.append(entry)
                if len(self._buffer) >= self._flush_batch_size:
                    self._flush_locked()
        except Exception:
            self._total_errors += 1

    def tick(self) -> None:
        """
        Called periodically (e.g. end of cycle) to flush on timer.
        Never raises.
        """
        try:
            now = _time.time()
            if now - self._last_flush >= self._flush_interval:
                with self._lock:
                    if self._buffer:
                        self._flush_locked()
        except Exception:
            pass

    def flush(self) -> None:
        """Force flush all buffered entries. Called on shutdown."""
        try:
            with self._lock:
                if self._buffer:
                    self._flush_locked()
        except Exception:
            pass

    def _flush_locked(self) -> None:
        """Flush buffer to disk + S3. Must be called with _lock held."""
        if not self._buffer:
            return

        # Group by (symbol, date) for partitioned writes
        partitions: dict[tuple[str, str], list[str]] = {}
        for entry in self._buffer:
            symbol = entry.get("symbol", "UNKNOWN")
            date_str = entry["timestamp"][:10]  # YYYY-MM-DD from ISO
            key = (symbol, date_str)
            line = json.dumps(entry, separators=(",", ":"), default=str) + "\n"
            partitions.setdefault(key, []).append(line)

        # Write each partition
        for (symbol, date_str), lines in partitions.items():
            self._write_local(symbol, date_str, lines)
            self._write_s3(symbol, date_str, lines)

        self._total_written += len(self._buffer)
        self._buffer.clear()
        self._last_flush = _time.time()

    def _write_local(self, symbol: str, date_str: str, lines: list[str]) -> None:
        """Append lines to local JSONL partition. Never raises."""
        try:
            local_path = self._local_dir / symbol / f"{date_str}.jsonl"
            local_path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(local_path), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
            try:
                os.write(fd, "".join(lines).encode("utf-8"))
                os.fsync(fd)
            finally:
                os.close(fd)
        except Exception as exc:
            self._total_errors += 1
            logger.debug("[DECISION_LEDGER] local_write_failed: %s", exc)

    def _write_s3(self, symbol: str, date_str: str, lines: list[str]) -> None:
        """Append lines to S3 partition. Fire-and-forget. Never raises."""
        try:
            from core import config as _cfg
            if not getattr(_cfg, "EVENT_STREAM_S3_MIRROR", False):
                return

            import boto3
            from botocore.config import Config as BotoConfig
            s3 = boto3.client(
                "s3",
                aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
                aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
                region_name=os.getenv("AWS_REGION", "eu-west-2"),
                config=BotoConfig(
                    connect_timeout=3,
                    read_timeout=5,
                    retries={"max_attempts": 0},
                ),
            )
            key = f"{_S3_PREFIX}/symbol={symbol}/date={date_str}/part-000.jsonl"
            body = "".join(lines)

            # Read-append-write (acceptable for decision ledger volume)
            try:
                existing = s3.get_object(Bucket=_S3_BUCKET, Key=key)
                body = existing["Body"].read().decode("utf-8") + body
            except Exception:
                pass  # New file

            s3.put_object(
                Bucket=_S3_BUCKET, Key=key,
                Body=body.encode("utf-8"),
                ContentType="application/x-ndjson",
            )
        except Exception:
            pass  # S3 failure must never affect runtime

    def stats(self) -> dict[str, Any]:
        """Return writer statistics."""
        return {
            "total_written": self._total_written,
            "total_errors": self._total_errors,
            "buffer_size": len(self._buffer),
            "last_flush": self._last_flush,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# SINGLETON ACCESS
# ═══════════════════════════════════════════════════════════════════════════════

_ledger: DecisionLedgerWriter | None = None


def get_ledger() -> DecisionLedgerWriter:
    """Get or create the singleton decision ledger writer."""
    global _ledger
    if _ledger is None:
        _ledger = DecisionLedgerWriter()
    return _ledger


# ═══════════════════════════════════════════════════════════════════════════════
# READER (for offline analysis / replay)
# ═══════════════════════════════════════════════════════════════════════════════

def load_ledger(
    *,
    symbol: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    decision: str | None = None,
    local_dir: str = _LOCAL_DIR,
) -> list[dict[str, Any]]:
    """
    Load decision ledger records from local JSONL.

    Read-only. Supports filtering by symbol, date range, and decision type.
    """
    records: list[dict[str, Any]] = []
    path = Path(local_dir)
    if not path.exists():
        return records

    for f in sorted(path.rglob("*.jsonl")):
        if symbol and symbol not in str(f):
            continue
        fname = f.stem  # YYYY-MM-DD
        if date_from and fname < date_from:
            continue
        if date_to and fname > date_to:
            continue

        try:
            for line in f.read_text(encoding="utf-8").strip().split("\n"):
                if not line.strip():
                    continue
                rec = json.loads(line)
                if decision and rec.get("decision") != decision:
                    continue
                records.append(rec)
        except (json.JSONDecodeError, OSError):
            continue

    return records
