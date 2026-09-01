"""
Event Stream — Pure Observation Persistence Layer.

Raw market + environment observations persisted as an immutable, append-only,
time-ordered JSONL stream. This is the system's sensory record.

OBSERVATION FAMILIES (ONLY these reach disk):
    MARKET_DATA:         CANDLE (OHLCV per closed bar)
    FEATURE_STATE:       FEATURE_UPDATE (ATR, volatility, structure, spread)
    SESSION_MARKERS:     SESSION_TRANSITION, SESSION_STATE
    INFRASTRUCTURE:      LATENCY_OBSERVATION, FEED_HEALTH, DATA_GAP, RECONNECT
    SYSTEM_DIAGNOSTICS:  SYSTEM_HEALTH, PIPELINE_HEALTH, CLOCK_SYNC

NEVER PERSISTED (rejected at write time):
    trades, decisions, outcomes, strategy logic, PnL, broker fills,
    execution data, optimisation output, or any future non-observation type.

ENFORCEMENT:
    Strict allowlist — only declared observation types can reach disk.
    No blacklist. No toggle. No legacy bypass.

Guarantees:
    - Single write path (no direct file writes elsewhere)
    - Append-only (never overwrites)
    - Ordered by ts_utc_ms (monotonic clock from core.clock)
    - Validated before write (reject non-observations)
    - Thread-safe (single writer with lock)
    - Never raises to caller (swallows failures)

S3: s3://trading-bot-v10-data/core/events/symbol={SYMBOL}/date={YYYY-MM-DD}/
Local: events/{YYYY-MM-DD}.jsonl

Time Model:
    ts_utc_ms:   observation time (when bot saw it — system clock)
    payload.ts:  market time (when it happened — broker clock)
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time as _time
from pathlib import Path
from typing import Any

from core.clock import utc_ms, utc_ms_to_date

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# OBSERVATION EVENT TYPES (STRICT ALLOWLIST)
# ═══════════════════════════════════════════════════════════════════════════════
# ONLY these event types can be persisted to events/.
# Everything else is rejected at emit-time. No exceptions. No toggle.

_ALLOWED_OBSERVATION_TYPES = frozenset({
    # MARKET_DATA — raw market observations
    "CANDLE",

    # FEATURE_STATE — calculated market features
    "FEATURE_UPDATE",

    # INFRASTRUCTURE_STATE — connectivity observations
    "FEED_HEALTH",
    "DATA_GAP",
    "RECONNECT",

    # SYSTEM_DIAGNOSTICS — system health observations
    "SYSTEM_HEALTH",
    "CLOCK_SYNC",
})


# ─── LEGACY EVENT TYPE COMPATIBILITY ──────────────────────────────────────────
# These constants are maintained for backward compatibility with callers that
# still reference EventType.CANDLE etc. The emit() function accepts raw strings.

class EventType:
    """Legacy event type constants. Use raw strings with emit() instead."""
    CANDLE = "CANDLE"
    FEATURE_UPDATE = "FEATURE_UPDATE"
    SESSION_TRANSITION = "SESSION_TRANSITION"
    SESSION_STATE = "SESSION_STATE"
    LATENCY_OBSERVATION = "LATENCY_OBSERVATION"
    FEED_HEALTH = "FEED_HEALTH"
    DATA_GAP = "DATA_GAP"
    RECONNECT = "RECONNECT"
    SYSTEM_HEALTH = "SYSTEM_HEALTH"
    PIPELINE_HEALTH = "PIPELINE_HEALTH"
    CLOCK_SYNC = "CLOCK_SYNC"
    # Legacy types (callers still reference these — emit() will reject them)
    PATTERN_DETECTED = "PATTERN_DETECTED"
    BIAS_CHANGE = "BIAS_CHANGE"
    CONFLUENCE_SCORE = "CONFLUENCE_SCORE"
    DECISION = "DECISION"
    RISK_CHECK = "RISK_CHECK"
    EXECUTION = "EXECUTION"
    TRADE_MANAGEMENT = "TRADE_MANAGEMENT"
    OUTCOME = "OUTCOME"
    STRATEGY = "STRATEGY"
    ENTITY = "ENTITY"
    # (Legacy enum values removed — EventType is now a plain class above)


# ─── CONFIGURATION ───────────────────────────────────────────────────────────

_EVENT_DIR = Path("events")
_lock = threading.Lock()
_current_file: Path | None = None
_current_date: str | None = None
_file_handle: Any = None  # Kept open for performance (flush per write)


# ─── INTERNAL STATE ───────────────────────────────────────────────────────────

_enabled: bool = True
_total_emitted: int = 0
_total_errors: int = 0


def _is_stream_enabled() -> bool:
    """Check if event stream is enabled via config + module flag."""
    if not _enabled:
        return False
    try:
        from core import config
        return bool(getattr(config, "EVENT_STREAM_ENABLED", True))
    except ImportError:
        return True


def _get_event_dir() -> Path:
    """Get event directory from config or default."""
    try:
        from core import config
        return Path(getattr(config, "EVENT_STREAM_DIR", "events"))
    except ImportError:
        return _EVENT_DIR


def _get_file_handle(date_str: str) -> Any:
    """Get or rotate the file handle for today's stream."""
    global _current_file, _current_date, _file_handle

    if _current_date == date_str and _file_handle is not None:
        return _file_handle

    # Close old handle
    if _file_handle is not None:
        try:
            _file_handle.close()
        except Exception:
            pass

    # Open new
    event_dir = _get_event_dir()
    event_dir.mkdir(parents=True, exist_ok=True)
    filepath = event_dir / f"{date_str}.jsonl"

    _file_handle = open(filepath, "a", encoding="utf-8")
    _current_file = filepath
    _current_date = date_str

    return _file_handle


# ─── CANONICAL FIELD NORMALISATION (Athena analytics contract) ────────────────
# Every event row MUST contain top-level canonical fields as non-empty strings.
# This removes reliance on nested JSON extraction in Athena queries.
# Resolved once at write-time inside emit(). Immutable after serialisation.
#
# Canonical fields:
#   pattern      — trading pattern name (e.g. "BULLISH_ENGULFING")
#   regime       — market regime classification (e.g. "TREND_UP", "RANGING")
#   bias         — directional bias state (e.g. "BUY", "SELL")
#   side         — signal/trade direction (e.g. "BUY", "SELL", "FLAT")
#   guard_result — risk gate outcome (e.g. "APPROVED", "REJECTED")

_FALLBACK_UNKNOWN = "UNKNOWN"
_FALLBACK_FLAT = "FLAT"


def _valid_str(value: Any) -> str | None:
    """Return stripped string if value is a non-empty string, else None."""
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _resolve_pattern(event: dict[str, Any]) -> str:
    """
    Resolve pattern from event structure.

    Priority:
        1. payload["pattern"]
        2. payload["data"]["pattern"]
        3. "UNKNOWN"

    Returns a guaranteed non-empty string. Never None, empty, or non-string.
    """
    payload = event.get("payload")
    if isinstance(payload, dict):
        candidate = _valid_str(payload.get("pattern"))
        if candidate:
            return candidate

        data = payload.get("data")
        if isinstance(data, dict):
            candidate = _valid_str(data.get("pattern"))
            if candidate:
                return candidate

    return _FALLBACK_UNKNOWN


def _resolve_regime(event: dict[str, Any]) -> str:
    """
    Resolve market regime from event structure.

    Priority:
        1. payload["regime"]
        2. payload["regime_state"]
        3. payload["market_state"]
        4. payload["data"]["regime"]
        5. "UNKNOWN"

    Returns a guaranteed non-empty string. Never None, empty, or non-string.
    """
    payload = event.get("payload")
    if isinstance(payload, dict):
        for key in ("regime", "regime_state", "market_state"):
            candidate = _valid_str(payload.get(key))
            if candidate:
                return candidate

        data = payload.get("data")
        if isinstance(data, dict):
            candidate = _valid_str(data.get("regime"))
            if candidate:
                return candidate

    return _FALLBACK_UNKNOWN


def _resolve_market_phase(event: dict[str, Any]) -> str | None:
    """
    Resolve market lifecycle phase from event structure.

    Priority:
        1. payload["market_phase"]
        2. payload["data"]["market_phase"]
        3. None (phase not available — distinct from UNKNOWN)

    Returns the phase string or None. Unlike regime/bias, absence is meaningful
    (phase wasn't computed for this event) so we return None rather than a fallback.
    """
    payload = event.get("payload")
    if isinstance(payload, dict):
        candidate = _valid_str(payload.get("market_phase"))
        if candidate:
            return candidate

        data = payload.get("data")
        if isinstance(data, dict):
            candidate = _valid_str(data.get("market_phase"))
            if candidate:
                return candidate

    return None


def _resolve_trade_horizon(event: dict[str, Any]) -> str | None:
    """
    Resolve trade horizon from event structure.

    Priority:
        1. payload["trade_horizon"]
        2. payload["data"]["trade_horizon"]
        3. None (horizon not available)

    Returns the horizon string or None.
    """
    payload = event.get("payload")
    if isinstance(payload, dict):
        candidate = _valid_str(payload.get("trade_horizon"))
        if candidate:
            return candidate

        data = payload.get("data")
        if isinstance(data, dict):
            candidate = _valid_str(data.get("trade_horizon"))
            if candidate:
                return candidate

    return None


def _resolve_bias(event: dict[str, Any]) -> str:
    """
    Resolve directional bias from event structure.

    Priority:
        1. payload["bias"]
        2. payload["new_bias"] (BIAS_CHANGE events)
        3. payload["data"]["bias"]
        4. "UNKNOWN"

    Returns a guaranteed non-empty string. Never None, empty, or non-string.
    """
    payload = event.get("payload")
    if isinstance(payload, dict):
        for key in ("bias", "new_bias"):
            candidate = _valid_str(payload.get(key))
            if candidate:
                return candidate

        data = payload.get("data")
        if isinstance(data, dict):
            candidate = _valid_str(data.get("bias"))
            if candidate:
                return candidate

    return _FALLBACK_UNKNOWN


def _resolve_side(event: dict[str, Any]) -> str:
    """
    Resolve signal/trade direction from event structure.

    Priority:
        1. payload["side"]
        2. payload["signal"]["side"]
        3. "FLAT"

    Returns a guaranteed non-empty string. Never None, empty, or non-string.
    """
    payload = event.get("payload")
    if isinstance(payload, dict):
        candidate = _valid_str(payload.get("side"))
        if candidate:
            return candidate

        signal = payload.get("signal")
        if isinstance(signal, dict):
            candidate = _valid_str(signal.get("side"))
            if candidate:
                return candidate

    return _FALLBACK_FLAT


def _resolve_guard_result(event: dict[str, Any]) -> str:
    """
    Resolve risk gate outcome from event structure.

    Priority:
        1. payload["result"]
        2. payload["decision"]["result"]
        3. "UNKNOWN"

    Returns a guaranteed non-empty string. Never None, empty, or non-string.
    """
    payload = event.get("payload")
    if isinstance(payload, dict):
        candidate = _valid_str(payload.get("result"))
        if candidate:
            return candidate

        decision = payload.get("decision")
        if isinstance(decision, dict):
            candidate = _valid_str(decision.get("result"))
            if candidate:
                return candidate

    return _FALLBACK_UNKNOWN


# ─── S3 MIRROR (batched JSONL — Athena/Glue compatible) ──────────────────────
# Secondary persistence layer. NEVER blocks event emission.
# Local write = PRIMARY (truth). S3 = SECONDARY (analytics mirror).
# Exact same JSON — no schema transformation.
# Writes batched JSONL files per (symbol, date) partition.

_S3_ENABLED: bool = False
from core.config import NEW_RUNTIME_S3_BUCKET

_S3_BUCKET: str = NEW_RUNTIME_S3_BUCKET  # Canonical sink; shared with every runtime writer


def _s3_validate_bucket() -> None:
    """Guardrail: ensure only canonical bucket is used. Raises on misconfiguration."""
    bucket = os.getenv("AWS_S3_BUCKET", "trading-bot-v10-data")
    if bucket != _S3_BUCKET:
        raise ValueError(
            f"[EVENT_S3] Invalid S3 sink '{bucket}' — configured runtime sink is '{_S3_BUCKET}'."
        )


def _s3_is_enabled() -> bool:
    """Check if S3 mirror is enabled via config."""
    try:
        from core import config
        return bool(getattr(config, "EVENT_STREAM_S3_MIRROR", False))
    except ImportError:
        return False


def _s3_enqueue(line: str, event: dict[str, Any]) -> None:
    """
    Add event to S3 batch writer. Non-blocking.
    Uses batched JSONL format (Athena/Glue compatible).
    """
    global _S3_ENABLED

    if not _S3_ENABLED:
        _S3_ENABLED = _s3_is_enabled()
        if not _S3_ENABLED:
            return

    try:
        from core.storage.s3_batch_writer import get_batch_writer
        writer = get_batch_writer()
        writer.add_event(event)
    except Exception:
        pass  # S3 mirror must never block or affect pipeline


def s3_mirror_stats() -> dict[str, Any]:
    """Return S3 mirror statistics."""
    try:
        from core.storage.s3_batch_writer import get_batch_writer
        return get_batch_writer().stats()
    except Exception:
        return {
            "enabled": _S3_ENABLED,
            "error": "batch_writer_unavailable",
            "bucket": _S3_BUCKET,
        }


# ─── PUBLIC API ───────────────────────────────────────────────────────────────

def emit(
    event_type: str,
    symbol: str | None = None,
    payload: dict[str, Any] | None = None,
    *,
    source: str | None = None,
) -> bool:
    """
    Emit a single event into the unified stream.

    Args:
        event_type: One of the EventType enum values (10-layer model)
        symbol: Trading symbol (e.g. "EURUSD"). Optional for system events.
        payload: Event-specific data dict
        source: Source module name (optional, for debugging)

    Returns:
        True if event was written successfully, False otherwise.

    Never raises. Failures are logged and counted.
    """
    global _total_emitted, _total_errors

    if not _is_stream_enabled():
        return False

    try:
        # ─── ALLOWLIST ENFORCEMENT (pure observation layer) ──────────
        # ONLY declared observation types can reach disk.
        # Everything else is silently rejected. No exceptions. No toggle.
        event_type_str = event_type.value if hasattr(event_type, 'value') else str(event_type)

        if event_type_str not in _ALLOWED_OBSERVATION_TYPES:
            # Non-observation event — reject silently (not an error, just wrong layer)
            return False

        # ─── PAYLOAD VALIDATION ──────────────────────────────────────
        if payload is not None and not isinstance(payload, dict):
            logger.warning("[EVENT_BUS] rejected: payload must be dict, got %s", type(payload).__name__)
            _total_errors += 1
            return False

        # ─── BUILD EVENT ─────────────────────────────────────────────
        ts = utc_ms()
        event: dict[str, Any] = {
            "ts_utc_ms": ts,
            "type": event_type_str,
        }

        if symbol is not None:
            event["symbol"] = symbol

        if payload:
            event["payload"] = payload

        if source:
            event["source"] = source

        # ─── SCHEMA VERSION STAMP ────────────────────────────────
        # Tag event with current schema version (v2). This enables
        # read-time detection and migration of historical data.
        from core.schema_migrator import stamp_schema_version
        stamp_schema_version(event)

        # ─── FEATURE VERSION STAMP ───────────────────────────────
        # Tag event with current feature version. This records which
        # analytical logic (pattern engine, regime model, scoring)
        # produced the features on this event. Immutable after write.
        from core.feature_resolver import stamp_feature_version
        stamp_feature_version(event)

        # ─── SCHEMA CONTRACT VALIDATION ───────────────────────────
        # Enforce canonical schema before write. Violations are logged
        # and counted but NEVER block emission (system resilience).
        try:
            from core.event_schema_contract import validate_canonical_event
            validate_canonical_event(event)
        except (ValueError, ImportError) as schema_err:
            logger.warning("[EVENT_BUS] schema_warning: %s", schema_err)

        # ─── WRITE (LOCAL — PRIMARY, BLOCKING) ────────────────────
        date_str = utc_ms_to_date(ts)
        line = json.dumps(event, separators=(",", ":"), default=str) + "\n"

        with _lock:
            fh = _get_file_handle(date_str)
            fh.write(line)
            fh.flush()

        _total_emitted += 1

        # ─── S3 MIRROR (SECONDARY, NON-BLOCKING) ─────────────────────
        # Fire-and-forget: exact same JSON, no transformation.
        # S3 failure never affects return value or trading runtime.
        try:
            _s3_enqueue(line.rstrip("\n"), event)
        except Exception:
            pass  # S3 mirror failure must never affect emit result

        return True

    except Exception as exc:
        _total_errors += 1
        logger.warning("[EVENT_BUS] write_failed: %s", exc)
        return False


# ─── OBSERVATION EMITTERS (typed shortcuts for each family) ───────────────────

# MARKET_DATA
def emit_candle(symbol: str, payload: dict[str, Any], *, source: str = "mt5_data") -> bool:
    """Emit a CANDLE observation (OHLCV per closed bar)."""
    return emit("CANDLE", symbol, payload, source=source)


# FEATURE_STATE
def emit_feature_update(symbol: str, payload: dict[str, Any], *, source: str = "feature_engine") -> bool:
    """Emit a FEATURE_UPDATE observation (ATR, volatility, structure, spread)."""
    return emit("FEATURE_UPDATE", symbol, payload, source=source)


# SESSION_MARKERS
def emit_session_transition(symbol: str, payload: dict[str, Any], *, source: str = "session_guard") -> bool:
    """Emit a SESSION_TRANSITION observation (session phase changed)."""
    return emit("SESSION_TRANSITION", symbol, payload, source=source)


def emit_session_state(symbol: str, payload: dict[str, Any], *, source: str = "session_guard") -> bool:
    """Emit a SESSION_STATE observation (current session context)."""
    return emit("SESSION_STATE", symbol, payload, source=source)


# INFRASTRUCTURE_STATE
def emit_latency_observation(symbol: str, payload: dict[str, Any], *, source: str = "mt5_data") -> bool:
    """Emit a LATENCY_OBSERVATION (API response time measurement)."""
    return emit("LATENCY_OBSERVATION", symbol, payload, source=source)


def emit_feed_health(symbol: str, payload: dict[str, Any], *, source: str = "stale_monitor") -> bool:
    """Emit a FEED_HEALTH observation (feed state transition)."""
    return emit("FEED_HEALTH", symbol, payload, source=source)


def emit_data_gap(symbol: str, payload: dict[str, Any], *, source: str = "stale_monitor") -> bool:
    """Emit a DATA_GAP observation (missing bars or tick gaps detected)."""
    return emit("DATA_GAP", symbol, payload, source=source)


def emit_reconnect(symbol: str, payload: dict[str, Any], *, source: str = "mt5_connection") -> bool:
    """Emit a RECONNECT observation (feed reconnection event)."""
    return emit("RECONNECT", symbol, payload, source=source)


# SYSTEM_DIAGNOSTICS
def emit_system_health(payload: dict[str, Any], *, source: str = "runtime") -> bool:
    """Emit a SYSTEM_HEALTH diagnostic observation."""
    return emit("SYSTEM_HEALTH", None, payload, source=source)


def emit_pipeline_health(payload: dict[str, Any], *, source: str = "runtime") -> bool:
    """Emit a PIPELINE_HEALTH diagnostic observation."""
    return emit("PIPELINE_HEALTH", None, payload, source=source)


def emit_clock_sync(payload: dict[str, Any], *, source: str = "clock") -> bool:
    """Emit a CLOCK_SYNC diagnostic observation."""
    return emit("CLOCK_SYNC", None, payload, source=source)


# ─── LEGACY EMITTERS (for backward compatibility — these will be rejected) ───
# These are kept so existing callers don't crash on import. The emit() function
# silently rejects non-observation types via the allowlist.

def emit_decision(symbol: str, payload: dict[str, Any], *, source: str = "decision_audit") -> bool:
    """Legacy: DECISION events are no longer persisted to events/. Returns False."""
    return emit("DECISION", symbol, payload, source=source)


def emit_strategy(symbol: str, payload: dict[str, Any], *, source: str = "strategy_trace") -> bool:
    """Legacy: STRATEGY events are no longer persisted to events/. Returns False."""
    return emit("STRATEGY", symbol, payload, source=source)


def emit_entity(symbol: str, payload: dict[str, Any], *, source: str = "entity_tracker") -> bool:
    """Legacy: ENTITY events are no longer persisted to events/. Returns False."""
    return emit("ENTITY", symbol, payload, source=source)


def emit_execution(symbol: str, payload: dict[str, Any], *, source: str = "execution") -> bool:
    """Legacy: EXECUTION events are no longer persisted to events/. Returns False."""
    return emit("EXECUTION", symbol, payload, source=source)


def emit_outcome(symbol: str, payload: dict[str, Any], *, source: str = "trade_journal") -> bool:
    """Legacy: OUTCOME events are no longer persisted to events/. Returns False."""
    return emit("OUTCOME", symbol, payload, source=source)


def emit_pattern_detected(symbol: str, payload: dict[str, Any], *, source: str = "strategy_detection") -> bool:
    """Legacy: PATTERN_DETECTED events are no longer persisted to events/. Returns False."""
    return emit("PATTERN_DETECTED", symbol, payload, source=source)


def emit_bias_change(symbol: str, payload: dict[str, Any], *, source: str = "structure_analysis") -> bool:
    """Legacy: BIAS_CHANGE events are no longer persisted to events/. Returns False."""
    return emit("BIAS_CHANGE", symbol, payload, source=source)


def emit_confluence_score(symbol: str, payload: dict[str, Any], *, source: str = "scoring_engine") -> bool:
    """Legacy: CONFLUENCE_SCORE events are no longer persisted to events/. Returns False."""
    return emit("CONFLUENCE_SCORE", symbol, payload, source=source)


def emit_risk_check(symbol: str, payload: dict[str, Any], *, source: str = "risk_manager") -> bool:
    """Legacy: RISK_CHECK events are no longer persisted to events/. Returns False."""
    payload.setdefault("pattern", None)
    payload.setdefault("strategy", None)
    payload.setdefault("entity_id", None)
    payload.setdefault("cycle_id", None)
    payload.setdefault("regime", None)
    payload.setdefault("guard", None)
    payload.setdefault("result", None)
    payload.setdefault("reason", None)
    return emit("RISK_CHECK", symbol, payload, source=source)


def emit_trade_management(symbol: str, payload: dict[str, Any], *, source: str = "trade_management") -> bool:
    """Legacy: TRADE_MANAGEMENT events are no longer persisted to events/. Returns False."""
    return emit("TRADE_MANAGEMENT", symbol, payload, source=source)


# ─── CONTROL API ──────────────────────────────────────────────────────────────

def disable() -> None:
    """Disable event bus (useful for tests)."""
    global _enabled
    _enabled = False


def enable() -> None:
    """Re-enable event bus."""
    global _enabled
    _enabled = True


def is_observation_type(event_type: str) -> bool:
    """Check if an event type is a valid observation (allowed in events/)."""
    return event_type in _ALLOWED_OBSERVATION_TYPES


# Legacy compatibility — these are no-ops now (allowlist is permanent)
def enable_firewall() -> None:
    """No-op. Allowlist enforcement is permanent and cannot be disabled."""
    pass


def disable_firewall() -> None:
    """No-op. Allowlist enforcement is permanent and cannot be disabled."""
    logger.warning("[EVENT_STREAM] disable_firewall() called but allowlist is permanent — ignored")


def is_firewall_enabled() -> bool:
    """Always True. Allowlist enforcement is permanent."""
    return True


def flush() -> None:
    """Force flush any buffered writes."""
    global _file_handle
    with _lock:
        if _file_handle is not None:
            try:
                _file_handle.flush()
            except Exception:
                pass


def close() -> None:
    """Close the file handle (call on shutdown)."""
    global _file_handle, _current_date, _current_file
    with _lock:
        if _file_handle is not None:
            try:
                _file_handle.close()
            except Exception:
                pass
            _file_handle = None
            _current_date = None
            _current_file = None


def stats() -> dict[str, Any]:
    """Return event bus statistics."""
    return {
        "enabled": _enabled,
        "total_emitted": _total_emitted,
        "total_errors": _total_errors,
        "current_file": str(_current_file) if _current_file else None,
    }


# ─── STREAM READER (for replay engine) ───────────────────────────────────────

def read_stream(
    date_str: str | None = None,
    *,
    event_dir: str | None = None,
    symbol: str | None = None,
    event_type: str | None = None,
    start_ms: int | None = None,
    end_ms: int | None = None,
) -> list[dict[str, Any]]:
    """
    Read events from the unified stream with optional filtering.

    Args:
        date_str: Date to read (YYYY-MM-DD). None = today.
        event_dir: Override event directory path.
        symbol: Filter by symbol (None = all).
        event_type: Filter by type (None = all).
        start_ms: Filter events >= this timestamp.
        end_ms: Filter events <= this timestamp.

    Returns:
        List of event dicts, sorted by ts_utc_ms.
    """
    if date_str is None:
        date_str = utc_ms_to_date(utc_ms())

    base_dir = Path(event_dir) if event_dir else _get_event_dir()
    filepath = base_dir / f"{date_str}.jsonl"

    if not filepath.exists():
        return []

    events: list[dict[str, Any]] = []
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            # ─── SCHEMA MIGRATION (v1 → v2 at read-time) ─────────
            # Historical events may lack canonical fields. Migrate
            # them to v2 shape so downstream logic always sees a
            # consistent schema. Original S3/local data is unchanged.
            try:
                from core.schema_migrator import migrate_event
                event = migrate_event(event)
            except ImportError:
                pass

            # ─── FEATURE VERSION NORMALISATION ────────────────────
            # Ensure feature_version is present on all events.
            # Legacy events (pre-feature-versioning) get version=1.
            try:
                from core.feature_resolver import ensure_feature_version
                ensure_feature_version(event)
            except ImportError:
                pass

            # Apply filters
            if symbol and event.get("symbol") != symbol:
                continue
            if event_type and event.get("type") != event_type:
                continue
            ts = event.get("ts_utc_ms", 0)
            if start_ms and ts < start_ms:
                continue
            if end_ms and ts > end_ms:
                continue

            events.append(event)

    return sorted(events, key=lambda e: e.get("ts_utc_ms", 0))
