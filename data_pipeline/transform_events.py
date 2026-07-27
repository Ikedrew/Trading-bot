"""
Curated Event Layer — Transforms raw trading bot event logs into a clean,
schema-consistent dataset for AWS Glue + Athena querying.

Pipeline: RAW → CLEAN → QUERYABLE

Input:  Raw JSONL files from events/ (inconsistent schemas, nested payloads)
Output: Curated JSONL files in events/curated/ (flat, strict schema, Athena-ready)

Every output event conforms to CURATED_SCHEMA:
    - timestamp (ISO 8601 string)
    - symbol (string)
    - event_type (string)
    - pattern (string)
    - htf_bias (string: bullish / bearish / neutral)
    - liquidity_swept (boolean)
    - bos_confirmed (boolean)
    - atr_regime (string: expansion / contraction / neutral)
    - pnl (float)
    - trade_id (string)

Guarantees:
    - 1 input event = 1 output event (no aggregation)
    - Deterministic (same input always produces same output)
    - No nested JSON in output (flat structure only)
    - No dynamic keys (schema stable)
    - Missing fields defaulted safely (never None for required fields)
    - Glue crawler compatible (consistent column types)
    - Athena queryable without json_extract

Usage:
    from data_pipeline.transform_events import run_pipeline

    stats = run_pipeline()
    # or: stats = run_pipeline(raw_dir="events", curated_dir="events/curated")

    print(stats)
    # {"total_raw": 5000, "total_valid": 4800, "total_dropped": 200}
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# CURATED SCHEMA DEFINITION
# ═══════════════════════════════════════════════════════════════════════════════

CURATED_SCHEMA: dict[str, type] = {
    "timestamp": str,       # ISO 8601 UTC
    "symbol": str,          # Trading symbol
    "event_type": str,      # Event type (CANDLE, RISK_CHECK, OUTCOME, etc.)
    "pattern": str,         # Pattern name or "UNKNOWN"
    "htf_bias": str,        # bullish / bearish / neutral
    "liquidity_swept": bool,  # Whether liquidity was swept
    "bos_confirmed": bool,  # Whether break of structure confirmed
    "atr_regime": str,      # expansion / contraction / neutral
    "pnl": float,           # Profit/loss (0.0 for non-trade events)
    "trade_id": str,        # Trade identifier or ""
}

# Required fields — events missing these are dropped
_REQUIRED_RAW_FIELDS = ("ts_utc_ms", "symbol", "type")


# ═══════════════════════════════════════════════════════════════════════════════
# TRANSFORMATION FUNCTION
# ═══════════════════════════════════════════════════════════════════════════════

def transform_event(raw_event: dict[str, Any]) -> dict[str, Any] | None:
    """
    Transform a single raw event into curated schema.

    Args:
        raw_event: Raw event dict from JSONL source

    Returns:
        Curated event dict conforming to CURATED_SCHEMA, or None if invalid.
        None indicates the event should be dropped (missing critical fields).
    """
    # ─── Validation: reject events missing critical fields ────────────
    ts_utc_ms = raw_event.get("ts_utc_ms")
    if not isinstance(ts_utc_ms, (int, float)) or ts_utc_ms <= 0:
        return None

    symbol = raw_event.get("symbol")
    if not isinstance(symbol, str) or not symbol.strip():
        return None

    event_type = raw_event.get("type")
    if not isinstance(event_type, str) or not event_type.strip():
        return None

    # ─── Extract payload (source of most fields) ─────────────────────
    payload = raw_event.get("payload")
    if not isinstance(payload, dict):
        payload = {}

    # ─── Resolve timestamp (ISO 8601) ────────────────────────────────
    try:
        dt = datetime.fromtimestamp(ts_utc_ms / 1000.0, tz=timezone.utc)
        timestamp = dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    except (OSError, ValueError, OverflowError):
        return None

    # ─── Resolve pattern ─────────────────────────────────────────────
    pattern = _resolve_str_field(
        raw_event.get("pattern"),
        payload.get("pattern"),
        _nested_get(payload, "data", "pattern"),
        fallback="UNKNOWN",
    )

    # ─── Resolve htf_bias ────────────────────────────────────────────
    # Map from various source formats to canonical: bullish/bearish/neutral
    raw_bias = _resolve_str_field(
        raw_event.get("bias"),
        payload.get("bias"),
        payload.get("new_bias"),
        _nested_get(payload, "data", "bias"),
        fallback="",
    )
    htf_bias = _normalise_bias(raw_bias)

    # ─── Resolve liquidity_swept ─────────────────────────────────────
    liquidity_swept = _resolve_bool_field(
        payload.get("liquidity_swept"),
        payload.get("sweep_detected"),
        _nested_get(payload, "data", "liquidity_swept"),
    )

    # ─── Resolve bos_confirmed ───────────────────────────────────────
    bos_confirmed = _resolve_bool_field(
        payload.get("bos_confirmed"),
        payload.get("structure_break"),
        _nested_get(payload, "data", "bos_confirmed"),
    )

    # ─── Resolve atr_regime ──────────────────────────────────────────
    raw_regime = _resolve_str_field(
        raw_event.get("regime"),
        payload.get("regime"),
        payload.get("regime_state"),
        payload.get("market_state"),
        _nested_get(payload, "data", "regime"),
        fallback="",
    )
    atr_regime = _normalise_regime(raw_regime)

    # ─── Resolve pnl ─────────────────────────────────────────────────
    pnl = _resolve_float_field(
        payload.get("pnl"),
        raw_event.get("pnl"),
    )

    # ─── Resolve trade_id ────────────────────────────────────────────
    trade_id = _resolve_str_field(
        payload.get("trade_id"),
        raw_event.get("trade_id"),
        payload.get("decision_id"),
        fallback="",
    )

    # ─── Build curated event ─────────────────────────────────────────
    return {
        "timestamp": timestamp,
        "symbol": symbol.strip(),
        "event_type": event_type.strip(),
        "pattern": pattern,
        "htf_bias": htf_bias,
        "liquidity_swept": liquidity_swept,
        "bos_confirmed": bos_confirmed,
        "atr_regime": atr_regime,
        "pnl": pnl,
        "trade_id": trade_id,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# PIPELINE RUNNER
# ═══════════════════════════════════════════════════════════════════════════════

def run_pipeline(
    *,
    raw_dir: str = "events",
    curated_dir: str = "events/curated",
) -> dict[str, int]:
    """
    Run the full transformation pipeline.

    Reads all JSONL files from raw_dir, transforms each event,
    and writes curated output to curated_dir.

    Args:
        raw_dir: Path to directory containing raw JSONL files
        curated_dir: Path to output directory for curated JSONL

    Returns:
        Stats dict: {"total_raw", "total_valid", "total_dropped"}
    """
    raw_path = Path(raw_dir)
    curated_path = Path(curated_dir)
    curated_path.mkdir(parents=True, exist_ok=True)

    total_raw = 0
    total_valid = 0
    total_dropped = 0
    drop_reasons: dict[str, int] = {}

    # Process each raw JSONL file
    for raw_file in sorted(raw_path.glob("*.jsonl")):
        output_file = curated_path / f"curated_{raw_file.name}"
        file_valid = 0
        file_dropped = 0

        with (
            open(raw_file, "r", encoding="utf-8") as infile,
            open(output_file, "w", encoding="utf-8") as outfile,
        ):
            for line_num, line in enumerate(infile, 1):
                line = line.strip()
                if not line:
                    continue

                total_raw += 1

                # Parse raw JSON
                try:
                    raw_event = json.loads(line)
                except json.JSONDecodeError:
                    total_dropped += 1
                    file_dropped += 1
                    drop_reasons["json_parse_error"] = drop_reasons.get("json_parse_error", 0) + 1
                    continue

                if not isinstance(raw_event, dict):
                    total_dropped += 1
                    file_dropped += 1
                    drop_reasons["not_dict"] = drop_reasons.get("not_dict", 0) + 1
                    continue

                # Transform
                curated = transform_event(raw_event)

                if curated is None:
                    total_dropped += 1
                    file_dropped += 1
                    # Classify drop reason
                    reason = _classify_drop_reason(raw_event)
                    drop_reasons[reason] = drop_reasons.get(reason, 0) + 1
                    continue

                # Write curated event (flat JSONL — one per line)
                outfile.write(
                    json.dumps(curated, separators=(",", ":"), default=str) + "\n"
                )
                total_valid += 1
                file_valid += 1

        logger.info(
            "[CURATE] %s → %s (valid=%d dropped=%d)",
            raw_file.name, output_file.name, file_valid, file_dropped,
        )

    stats = {
        "total_raw": total_raw,
        "total_valid": total_valid,
        "total_dropped": total_dropped,
        "drop_reasons": drop_reasons,
    }

    logger.info(
        "[CURATE] COMPLETE — raw=%d valid=%d dropped=%d",
        total_raw, total_valid, total_dropped,
    )

    return stats


# ═══════════════════════════════════════════════════════════════════════════════
# FIELD RESOLUTION HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _resolve_str_field(*candidates: Any, fallback: str = "UNKNOWN") -> str:
    """Return first valid non-empty string from candidates, or fallback."""
    for value in candidates:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return fallback


def _resolve_bool_field(*candidates: Any) -> bool:
    """Return first truthy boolean from candidates, or False."""
    for value in candidates:
        if isinstance(value, bool):
            return value
        if value is True or value == 1:
            return True
    return False


def _resolve_float_field(*candidates: Any) -> float:
    """Return first valid numeric value from candidates, or 0.0."""
    for value in candidates:
        if isinstance(value, (int, float)) and value == value:  # NaN check
            return float(value)
    return 0.0


def _nested_get(d: dict[str, Any], outer: str, inner: str) -> Any:
    """Safely get a nested dict value."""
    nested = d.get(outer)
    if isinstance(nested, dict):
        return nested.get(inner)
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# NORMALISATION HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

_BULLISH_TERMS = frozenset({"buy", "bullish", "long", "up", "trend_up"})
_BEARISH_TERMS = frozenset({"sell", "bearish", "short", "down", "trend_down"})


def _normalise_bias(raw: str) -> str:
    """Normalise bias to: bullish / bearish / neutral."""
    if not raw:
        return "neutral"
    lower = raw.lower().strip()
    if lower in _BULLISH_TERMS:
        return "bullish"
    if lower in _BEARISH_TERMS:
        return "bearish"
    return "neutral"


_EXPANSION_TERMS = frozenset({"expansion", "trend_up", "trend_down", "trending", "volatile"})
_CONTRACTION_TERMS = frozenset({"contraction", "ranging", "choppy", "consolidation", "flat"})


def _normalise_regime(raw: str) -> str:
    """Normalise regime to: expansion / contraction / neutral."""
    if not raw:
        return "neutral"
    lower = raw.lower().strip()
    if lower in _EXPANSION_TERMS:
        return "expansion"
    if lower in _CONTRACTION_TERMS:
        return "contraction"
    # Check partial matches
    if "trend" in lower or "expan" in lower:
        return "expansion"
    if "rang" in lower or "chop" in lower or "contract" in lower:
        return "contraction"
    return "neutral"


def _classify_drop_reason(event: dict[str, Any]) -> str:
    """Classify why an event was dropped (for diagnostics)."""
    if not isinstance(event.get("ts_utc_ms"), (int, float)):
        return "missing_timestamp"
    if event.get("ts_utc_ms", 0) <= 0:
        return "invalid_timestamp"
    if not isinstance(event.get("symbol"), str) or not event.get("symbol", "").strip():
        return "missing_symbol"
    if not isinstance(event.get("type"), str) or not event.get("type", "").strip():
        return "missing_event_type"
    return "unknown"


# ═══════════════════════════════════════════════════════════════════════════════
# CLI ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    raw = sys.argv[1] if len(sys.argv) > 1 else "events"
    curated = sys.argv[2] if len(sys.argv) > 2 else "events/curated"

    print(f"[PIPELINE] RAW: {raw}")
    print(f"[PIPELINE] CURATED: {curated}")
    print()

    result = run_pipeline(raw_dir=raw, curated_dir=curated)

    print()
    print("═══════════════════════════════════════════════")
    print("PIPELINE COMPLETE")
    print("═══════════════════════════════════════════════")
    print(f"  Total raw events:    {result['total_raw']}")
    print(f"  Total valid written: {result['total_valid']}")
    print(f"  Total dropped:       {result['total_dropped']}")
    if result.get("drop_reasons"):
        print("  Drop reasons:")
        for reason, count in sorted(result["drop_reasons"].items(), key=lambda x: -x[1]):
            print(f"    {reason}: {count}")
