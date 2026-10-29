"""Governed, read-only repair for the post-normalisation shadow timestamp defect.

The current MT5 feed converts broker/server bar epochs to UTC before creating a
``Candle``.  Shadow Runtime historically received that UTC value but subtracted
the measured broker offset again.  This module never mutates persisted records;
it produces an eligible normalized view only when lifecycle, clock, path, and
provenance evidence jointly prove that exact defect.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import math
from typing import Any, Iterable, Mapping

from research_engine.control_plane.evidence_provenance import evidence_digest


NORMALIZATION_CONTRACT_VERSION = "shadow_post_candle_utc_normalization_v1"
TIMESTAMP_SEMANTICS = "canonical_utc_bar_open_v1"
CANDLE_NORMALIZATION_VERSION = "mt5_broker_to_utc_once_v1"
PRODUCER_AUTHORITY = "git:7cdf2a6ba"

# Commit 7cdf2a6ba made Candle.time canonical UTC before closed-bar selection.
# Historical records before this authority boundary are never normalized here.
POST_NORMALIZATION_PRODUCER_EPOCH_UTC = 1_788_898_819

_MAX_WALL_LAG_SECONDS = 7 * 24 * 60 * 60
_M5_MILLISECONDS = 300_000


@dataclass(frozen=True)
class ShadowTimestampNormalization:
    eligible: bool
    reason: str
    lifecycle_identity: tuple[str, str, str] | None
    normalized_open: dict[str, Any] | None
    normalized_close: dict[str, Any] | None
    ordered_m5_path: tuple[dict[str, Any], ...]
    exclusions: tuple[dict[str, Any], ...]
    provenance: dict[str, Any]


def _integer(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(float(value)) or int(value) != value:
        return None
    return int(value)


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _identity(event: Mapping[str, Any]) -> tuple[str, str, str]:
    return (
        str(event.get("shadow_trade_id", "") or ""),
        str(event.get("canonical_opportunity_id", "") or ""),
        str(event.get("horizon", "") or ""),
    )


def _iso(epoch_s: int) -> str:
    return datetime.fromtimestamp(epoch_s, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _provenance(
    *,
    supplied_records: list[dict[str, Any]],
    selected_records: list[dict[str, Any]],
    identity: tuple[str, str, str] | None,
    eligible: bool,
    reason: str,
    offset: int | None,
    exclusions: list[dict[str, Any]],
    normalized_records: list[dict[str, Any]],
) -> dict[str, Any]:
    material = {
        "normalization_contract_version": NORMALIZATION_CONTRACT_VERSION,
        "timestamp_semantics": TIMESTAMP_SEMANTICS,
        "source_identity": "events_v1:CANDLE:mt5_data:M5",
        "producer_authority": PRODUCER_AUTHORITY,
        "producer_epoch_utc": POST_NORMALIZATION_PRODUCER_EPOCH_UTC,
        "lifecycle_identity": list(identity) if identity else None,
        "eligible": eligible,
        "reason": reason,
        "applied_offset_seconds": offset,
        "source_input_digest": evidence_digest(supplied_records),
        "selected_source_digest": evidence_digest(selected_records),
        "normalized_evidence_digest": evidence_digest(normalized_records),
        "input_records": len(supplied_records),
        "records_used": len(selected_records) if eligible else 0,
        "records_excluded": len(supplied_records) - len(selected_records) if eligible else len(supplied_records),
        "exclusions": sorted(
            (dict(item) for item in exclusions),
            key=lambda item: (str(item.get("reason", "")), str(item.get("identity", ""))),
        ),
    }
    material["digest_algorithm"] = "sha256"
    material["digest"] = evidence_digest([material])
    return material


def normalize_post_candle_utc_lifecycle(
    open_event: Mapping[str, Any],
    close_event: Mapping[str, Any],
    candle_events: Iterable[Mapping[str, Any]],
) -> ShadowTimestampNormalization:
    """Return an immutable normalized view or an explicit fail-closed exclusion.

    The correction is never a global offset rule.  The offset is taken from
    the paired source lifecycle and is applied only after the persisted legacy
    derived fields prove ``claimed_utc == canonical_bar_time - offset`` and an
    exact M5 path independently agrees with that canonical bar clock.
    """
    opened = deepcopy(dict(open_event)) if isinstance(open_event, Mapping) else {}
    closed = deepcopy(dict(close_event)) if isinstance(close_event, Mapping) else {}
    candles = [deepcopy(dict(row)) for row in candle_events if isinstance(row, Mapping)]
    supplied = [opened, closed, *candles]
    identity = _identity(opened) if opened else None

    def reject(reason: str, *, offset: int | None = None) -> ShadowTimestampNormalization:
        exclusions = [{"reason": reason, "identity": list(identity) if identity else None}]
        return ShadowTimestampNormalization(
            eligible=False,
            reason=reason,
            lifecycle_identity=identity,
            normalized_open=None,
            normalized_close=None,
            ordered_m5_path=(),
            exclusions=tuple(exclusions),
            provenance=_provenance(
                supplied_records=supplied,
                selected_records=[],
                identity=identity,
                eligible=False,
                reason=reason,
                offset=offset,
                exclusions=exclusions,
                normalized_records=[],
            ),
        )

    if opened.get("schema_version") != "shadow_runtime_v1" or opened.get("event_type") != "OPEN":
        return reject("INVALID_OPEN_AUTHORITY")
    if closed.get("schema_version") != "shadow_runtime_v1" or closed.get("event_type") != "CLOSE":
        return reject("INVALID_CLOSE_AUTHORITY")
    if not identity or not identity[0].startswith("nshadow_") or not identity[1] or not identity[2]:
        return reject("INVALID_LIFECYCLE_IDENTITY")
    if _identity(closed) != identity:
        return reject("LIFECYCLE_IDENTITY_MISMATCH")

    symbol = str(opened.get("symbol", "") or "")
    if not symbol or str(closed.get("symbol", "") or "") != symbol:
        return reject("CANONICAL_SYMBOL_MISMATCH")
    opportunity_parts = identity[1].split("*", 2)
    if len(opportunity_parts) != 3 or opportunity_parts[0] != symbol:
        return reject("OPPORTUNITY_IDENTITY_MISMATCH")

    open_offset = _integer(opened.get("broker_offset_seconds"))
    close_offset = _integer(closed.get("broker_offset_seconds"))
    if open_offset is None or close_offset is None:
        return reject("MISSING_BROKER_OFFSET")
    if open_offset != close_offset:
        return reject("INCONSISTENT_BROKER_OFFSET")
    offset = open_offset

    entry = _integer(opened.get("entry_market_time"))
    entry_claimed = _integer(opened.get("entry_market_time_utc_epoch_s"))
    open_event_time = _integer(opened.get("event_market_time"))
    open_event_claimed = _integer(opened.get("event_market_time_utc_epoch_s"))
    exit_time = _integer(closed.get("exit_market_time"))
    exit_claimed = _integer(closed.get("exit_market_time_utc_epoch_s"))
    close_event_time = _integer(closed.get("event_market_time"))
    close_event_claimed = _integer(closed.get("event_market_time_utc_epoch_s"))
    values = (
        entry, entry_claimed, open_event_time, open_event_claimed,
        exit_time, exit_claimed, close_event_time, close_event_claimed,
    )
    if any(value is None for value in values):
        return reject("MISSING_OR_INVALID_LIFECYCLE_TIMESTAMP", offset=offset)
    assert entry is not None and entry_claimed is not None
    assert open_event_time is not None and open_event_claimed is not None
    assert exit_time is not None and exit_claimed is not None
    assert close_event_time is not None and close_event_claimed is not None
    if entry < POST_NORMALIZATION_PRODUCER_EPOCH_UTC or exit_time < POST_NORMALIZATION_PRODUCER_EPOCH_UTC:
        return reject("PRE_NORMALIZATION_OR_AMBIGUOUS_EPOCH", offset=offset)
    if exit_time <= entry or entry % 300 or exit_time % 300:
        return reject("INVALID_LIFECYCLE_BOUNDARY", offset=offset)
    if open_event_time != entry or close_event_time != exit_time:
        return reject("EVENT_AND_LIFECYCLE_TIMESTAMP_MISMATCH", offset=offset)
    if (
        entry_claimed != entry - offset
        or open_event_claimed != open_event_time - offset
        or exit_claimed != exit_time - offset
        or close_event_claimed != close_event_time - offset
    ):
        return reject("INCONSISTENT_OFFSET_APPLICATION", offset=offset)
    try:
        opportunity_epoch = int(opportunity_parts[1])
    except ValueError:
        return reject("OPPORTUNITY_IDENTITY_MISMATCH", offset=offset)
    if opportunity_epoch != entry:
        return reject("OPPORTUNITY_TIMESTAMP_MISMATCH", offset=offset)

    for event in (opened, closed):
        wall_ms = _integer(event.get("recorded_at_utc_ms"))
        event_time = _integer(event.get("event_market_time"))
        if wall_ms is None or event_time is None:
            return reject("MISSING_WALL_CLOCK_PROVENANCE", offset=offset)
        lag = wall_ms / 1000.0 - event_time
        if lag < 0 or lag > _MAX_WALL_LAG_SECONDS:
            return reject("IMPLAUSIBLE_WALL_CLOCK_RELATION", offset=offset)

    by_timestamp: dict[int, tuple[dict[str, Any], tuple[float, float, float, float]]] = {}
    outside = 0
    for candle in candles:
        if (
            candle.get("type") != "CANDLE"
            or candle.get("source") != "mt5_data"
            or candle.get("schema_version") != "events_v1"
            or candle.get("timeframe") != "M5"
            or str(candle.get("symbol", "") or "") != symbol
        ):
            return reject("INVALID_CANDLE_AUTHORITY", offset=offset)
        payload = candle.get("payload")
        if not isinstance(payload, Mapping):
            return reject("INVALID_CANDLE_PAYLOAD", offset=offset)
        if payload.get("timestamp_semantics", TIMESTAMP_SEMANTICS) != TIMESTAMP_SEMANTICS:
            return reject("INCOMPATIBLE_CANDLE_TIMESTAMP_SEMANTICS", offset=offset)
        if payload.get("timestamp_normalization_version", CANDLE_NORMALIZATION_VERSION) != CANDLE_NORMALIZATION_VERSION:
            return reject("INCOMPATIBLE_CANDLE_TIMESTAMP_SEMANTICS", offset=offset)
        source_offset = payload.get("source_broker_offset_seconds")
        if source_offset is not None and _integer(source_offset) != offset:
            return reject("CANDLE_AND_SHADOW_OFFSET_MISMATCH", offset=offset)
        timestamp = _integer(payload.get("ts"))
        ohlc = tuple(_finite(payload.get(key)) for key in ("o", "h", "l", "c"))
        if timestamp is None or timestamp % _M5_MILLISECONDS or any(value is None for value in ohlc):
            return reject("INVALID_CANDLE_PAYLOAD", offset=offset)
        o, high, low, close_price = ohlc
        assert o is not None and high is not None and low is not None and close_price is not None
        if low > min(o, close_price) or high < max(o, close_price) or low > high:
            return reject("IMPOSSIBLE_CANDLE_GEOMETRY", offset=offset)
        analytical = (o, high, low, close_price)
        previous = by_timestamp.get(timestamp)
        if previous is not None and previous[1] != analytical:
            return reject("CONFLICTING_DUPLICATE_CANDLE", offset=offset)
        by_timestamp.setdefault(timestamp, (candle, analytical))

    entry_ms, exit_ms = entry * 1000, exit_time * 1000
    selected_pairs = [
        pair for timestamp, pair in sorted(by_timestamp.items())
        if entry_ms < timestamp <= exit_ms
    ]
    outside = len(by_timestamp) - len(selected_pairs)
    selected_candles = [pair[0] for pair in selected_pairs]
    selected_times = [_integer(row["payload"]["ts"]) for row in selected_candles]
    if not selected_times:
        return reject("MISSING_M5_PATH", offset=offset)
    if selected_times[-1] != exit_ms:
        return reject("MISSING_EXIT_M5_BAR", offset=offset)
    if any(b - a != _M5_MILLISECONDS for a, b in zip(selected_times, selected_times[1:])):
        return reject("NON_CONTIGUOUS_M5_PATH", offset=offset)

    bars_held = _integer(closed.get("bars_held"))
    progression = closed.get("trade_state_progression")
    if bars_held is None or bars_held <= 0 or not isinstance(progression, list):
        return reject("INVALID_PROGRESSION_AUTHORITY", offset=offset)
    if closed.get("data_gaps"):
        return reject("RUNTIME_REPORTED_DATA_GAP", offset=offset)
    if len(selected_candles) != bars_held or len(progression) != bars_held:
        return reject("PATH_CARDINALITY_MISMATCH", offset=offset)
    for step, candle in zip(progression, selected_candles):
        step_close = _finite(step.get("close")) if isinstance(step, Mapping) else None
        candle_close = _finite(candle["payload"].get("c"))
        if step_close is None or candle_close is None or not math.isclose(
            step_close, candle_close, rel_tol=0.0, abs_tol=1e-12,
        ):
            return reject("PATH_CLOSE_MISMATCH", offset=offset)

    normalized_open = deepcopy(opened)
    normalized_close = deepcopy(closed)
    for event, fields in (
        (normalized_open, ("event_market_time", "opportunity_market_time", "entry_market_time")),
        (normalized_close, ("event_market_time", "exit_market_time")),
    ):
        event["market_timestamp_semantics"] = TIMESTAMP_SEMANTICS
        event["market_timestamp_normalization_version"] = NORMALIZATION_CONTRACT_VERSION
        event["historical_timestamp_correction_seconds"] = offset
        for field in fields:
            source_time = _integer(event.get(field))
            if source_time is None:
                continue
            event[f"{field}_utc_epoch_s"] = source_time
            event[f"{field}_utc_iso8601"] = _iso(source_time)

    exclusions = []
    if outside:
        exclusions.append({"reason": "OUTSIDE_LIFECYCLE_WINDOW", "count": outside})
    selected_source = [opened, closed, *selected_candles]
    normalized_records = [normalized_open, normalized_close, *selected_candles]
    provenance = _provenance(
        supplied_records=supplied,
        selected_records=selected_source,
        identity=identity,
        eligible=True,
        reason="ELIGIBLE_POST_NORMALIZATION_DOUBLE_SUBTRACTION",
        offset=offset,
        exclusions=exclusions,
        normalized_records=normalized_records,
    )
    return ShadowTimestampNormalization(
        eligible=True,
        reason="ELIGIBLE_POST_NORMALIZATION_DOUBLE_SUBTRACTION",
        lifecycle_identity=identity,
        normalized_open=normalized_open,
        normalized_close=normalized_close,
        ordered_m5_path=tuple(selected_candles),
        exclusions=tuple(exclusions),
        provenance=provenance,
    )
