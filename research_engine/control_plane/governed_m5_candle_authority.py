"""Governed M5 candle evidence authority (final build-gap closure).

The candidate-capable Q71 family ``GOVERNED_EXIT_POLICY_COUNTERFACTUAL`` needs
the ordered ``events_v1`` M5 OHLC candle stream, whose governed production
authority is::

    events_v1:CANDLE:mt5_data:M5

That stream is *real governed production data* (dataset ``events``, schema
``events_v1``, role CORE, owner ``observation_stream`` in
``core.production_data_contract.PRODUCTION_SCHEMA_REGISTRY``).  It is simply not
part of the closed common investigation snapshot dataset set
(``research_engine.v10.investigation_snapshot.BOUND_DATASETS``), and the Q71
worker is forbidden from reopening storage.  The candidate-capable path
therefore fails closed with ``MISSING_M5_CANDLE_AUTHORITY``.

This module closes that gap **without** touching the frozen snapshot contract:

    governed ``events`` S3 objects (canonical prefix, frontier window)
        -> strict per-record contract validation (this module)
        -> immutable, content-addressed governed candle authority artifact
        -> snapshot-pinned governed evidence membership (binding)
        -> frozen candle rows consumed by the counterfactual producer
        -> Q71 worker (frozen object only, never storage)

Authority rules enforced here:

* Nothing is invented.  Every row is a governed ``events`` record read from the
  canonical S3 layout through the governed research data source.  No bar is
  interpolated, back-filled, defaulted or synthesised, and a missing bar stays a
  missing bar with an explicit completeness indicator.
* Only the frontier's own governed instrument population is admitted: the
  symbols are the symbols of the frozen ``shadow_runtime`` lifecycle population
  the candle authority is bound to.  A candle for an instrument the frontier
  never traded cannot enter the authority.
* The artifact is *frozen*: write-once, content-addressed, digest-verified on
  read, and pinned to the exact InvestigationSnapshot identity it was produced
  from (id + fingerprint + investigation epoch + frontier window).
* Invalid, duplicated, non-monotonic or out-of-window records fail closed with a
  closed reason code.  A read failure is a read failure; it is never a silent
  empty universe.
* Historical frozen snapshots never gain candle authority: the authority is a
  separate artifact bound by an explicit snapshot-pinned membership, and old
  snapshots remain readable and valid exactly as they were frozen.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from core.production_data_contract import current_schema
from research_engine.control_plane.evidence_provenance import evidence_digest
from research_engine.data_access.s3_source import (
    ResearchDataSourceError,
    S3ResearchDataSource,
)


M5_CANDLE_AUTHORITY_SCHEMA = "governed_m5_candle_authority_v1"
M5_CANDLE_BINDING_SCHEMA = "governed_m5_candle_authority_binding_v1"
M5_CANDLE_AUTHORITY_ID_PREFIX = "MCA-"

#: Producer identity/version, recorded on every artifact so a consumer can prove
#: which code produced the candle authority it is about to analyse.
M5_CANDLE_PRODUCER_IDENTITY = (
    "research_engine.control_plane.governed_m5_candle_authority")
M5_CANDLE_PRODUCER_VERSION = "governed_m5_candle_authority_producer_v1"

#: The governed evidence authority this artifact carries.  This is the canonical
#: identity already used by ``exit_bar_path_v1``,
#: ``shadow_timestamp_normalization`` and ``exit_policy_governed``; it is not a
#: new invention.
M5_CANDLE_AUTHORITY_IDENTITY = "events_v1:CANDLE:mt5_data:M5"
M5_CANDLE_DATASET = "events"
M5_CANDLE_EVENT_TYPE = "CANDLE"
M5_CANDLE_SOURCE = "mt5_data"
M5_CANDLE_TIMEFRAME = "M5"
M5_BAR_INTERVAL_MS = 300_000
M5_BAR_INTERVAL_SECONDS = M5_BAR_INTERVAL_MS // 1000

ORDERING_RULE = "strictly ascending payload.ts within each symbol"
DUPLICATE_RULE = (
    "a repeated (symbol, payload.ts) pair is DUPLICATE_CANDLE and fails closed; "
    "the producer never de-duplicates, picks a winner or averages bars")

# ── Fail-closed reason codes (Step 3) ───────────────────────────────────────
INVALID_SCHEMA = "INVALID_SCHEMA"
WRONG_EVENT_TYPE = "WRONG_EVENT_TYPE"
WRONG_SOURCE = "WRONG_SOURCE"
WRONG_TIMEFRAME = "WRONG_TIMEFRAME"
WRONG_DATASET = "WRONG_DATASET"
NON_MONOTONIC_CANDLES = "NON_MONOTONIC_CANDLES"
DUPLICATE_CANDLE = "DUPLICATE_CANDLE"
MISSING_REQUIRED_FIELDS = "MISSING_REQUIRED_FIELDS"
STALE_FRONTIER = "STALE_FRONTIER"
DIGEST_MISMATCH = "DIGEST_MISMATCH"
SOURCE_AUTHORITY_MISMATCH = "SOURCE_AUTHORITY_MISMATCH"
OUT_OF_FRONTIER_WINDOW = "OUT_OF_FRONTIER_WINDOW"
SYMBOL_NOT_IN_FRONTIER_POPULATION = "SYMBOL_NOT_IN_FRONTIER_POPULATION"
UNKNOWN_GOVERNED_SYMBOL_POPULATION = "UNKNOWN_GOVERNED_SYMBOL_POPULATION"
NO_CANDLE_ROWS = "NO_CANDLE_ROWS"
CANDLE_AUTHORITY_UNAVAILABLE = "CANDLE_AUTHORITY_UNAVAILABLE"
INCOMPLETE_BARS = "INCOMPLETE_BARS"
SUPERSEDED_EVIDENCE = "SUPERSEDED_EVIDENCE"

REASON_CODES = frozenset({
    INVALID_SCHEMA, WRONG_EVENT_TYPE, WRONG_SOURCE, WRONG_TIMEFRAME,
    WRONG_DATASET, NON_MONOTONIC_CANDLES, DUPLICATE_CANDLE,
    MISSING_REQUIRED_FIELDS, STALE_FRONTIER, DIGEST_MISMATCH,
    SOURCE_AUTHORITY_MISMATCH, OUT_OF_FRONTIER_WINDOW,
    SYMBOL_NOT_IN_FRONTIER_POPULATION, UNKNOWN_GOVERNED_SYMBOL_POPULATION,
    NO_CANDLE_ROWS, CANDLE_AUTHORITY_UNAVAILABLE, INCOMPLETE_BARS,
    SUPERSEDED_EVIDENCE,
})

#: The exact record fields the governed M5 contract requires.
REQUIRED_CANDLE_FIELDS: tuple[str, ...] = (
    "schema_version", "type", "source", "timeframe", "symbol", "payload",
)
REQUIRED_PAYLOAD_FIELDS: tuple[str, ...] = ("ts", "o", "h", "l", "c")

#: Source-authority fields that must agree with the bound snapshot's own
#: authority.  A candle read from a different bucket/region/contract is a
#: different evidence authority and never belongs to this frontier.
SOURCE_AUTHORITY_FIELDS: tuple[str, ...] = (
    "data_contract", "canonical_authority", "source", "bucket", "region",
    "research_profile",
)

DEFAULT_M5_CANDLE_AUTHORITY_DIRECTORY = Path(
    "analysis/assurance/m5_candle_authority")
_LATEST_POINTER = "latest.json"


class CandleAuthorityError(RuntimeError):
    """The governed M5 candle authority is invalid; no analysis may proceed."""

    def __init__(self, reason_code: str, detail: str = "") -> None:
        super().__init__(
            reason_code if not detail else f"{reason_code}:{detail}")
        self.reason_code = reason_code
        self.detail = detail


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"),
                   ensure_ascii=False, allow_nan=False).encode("utf-8")
    ).hexdigest()


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise CandleAuthorityError(MISSING_REQUIRED_FIELDS, name)
    return value


def _integer(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CandleAuthorityError(MISSING_REQUIRED_FIELDS, name)
    return int(value)


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CandleAuthorityError(MISSING_REQUIRED_FIELDS, name)
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        raise CandleAuthorityError(MISSING_REQUIRED_FIELDS, name)
    return number


def _iso_utc(epoch_ms: int) -> str:
    return datetime.fromtimestamp(
        epoch_ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def authority_identity_for(value: str) -> str:
    text = str(value or "")
    if len(text) != 16 or any(ch not in "0123456789ABCDEF" for ch in text):
        raise CandleAuthorityError(INVALID_SCHEMA, "authority_id")
    return M5_CANDLE_AUTHORITY_ID_PREFIX + text


def is_authority_identity(value: Any) -> bool:
    text = str(value or "")
    return (text.startswith(M5_CANDLE_AUTHORITY_ID_PREFIX)
            and len(text) == len(M5_CANDLE_AUTHORITY_ID_PREFIX) + 16
            and all(ch in "0123456789ABCDEF"
                    for ch in text[len(M5_CANDLE_AUTHORITY_ID_PREFIX):]))


# ── Governed record contract ────────────────────────────────────────────────
def validate_candle_record(record: Any) -> dict[str, Any]:
    """Strictly validate one governed ``events_v1`` M5 CANDLE record.

    The record is returned unchanged (the governed producer's own row); every
    field the contract requires is proven present and in the governed value
    domain.  Nothing is defaulted, repaired or invented.
    """
    if not isinstance(record, Mapping):
        raise CandleAuthorityError(INVALID_SCHEMA, "record")
    for name in REQUIRED_CANDLE_FIELDS:
        if name not in record:
            raise CandleAuthorityError(MISSING_REQUIRED_FIELDS, name)
    if record.get("schema_version") != current_schema(M5_CANDLE_DATASET):
        raise CandleAuthorityError(
            INVALID_SCHEMA, str(record.get("schema_version")))
    if record.get("type") != M5_CANDLE_EVENT_TYPE:
        raise CandleAuthorityError(WRONG_EVENT_TYPE, str(record.get("type")))
    if record.get("source") != M5_CANDLE_SOURCE:
        raise CandleAuthorityError(WRONG_SOURCE, str(record.get("source")))
    if record.get("timeframe") != M5_CANDLE_TIMEFRAME:
        raise CandleAuthorityError(
            WRONG_TIMEFRAME, str(record.get("timeframe")))
    payload = record.get("payload")
    if not isinstance(payload, Mapping):
        raise CandleAuthorityError(MISSING_REQUIRED_FIELDS, "payload")
    for name in REQUIRED_PAYLOAD_FIELDS:
        if name not in payload:
            raise CandleAuthorityError(MISSING_REQUIRED_FIELDS, "payload." + name)
    return dict(record)


def canonical_bar(record: Mapping[str, Any], *, object_key: str = "") -> dict[str, Any]:
    """The canonical, human-readable bar the authority carries for one record."""
    payload = record["payload"]
    timestamp = _integer(payload.get("ts"), "payload.ts")
    if timestamp <= 0 or timestamp % M5_BAR_INTERVAL_MS:
        raise CandleAuthorityError(
            OUT_OF_FRONTIER_WINDOW, "payload.ts_not_m5_aligned:" + str(timestamp))
    return {
        "symbol": _text(record.get("symbol"), "symbol"),
        "timeframe": M5_CANDLE_TIMEFRAME,
        "ts_utc_ms": timestamp,
        "open_time_utc": _iso_utc(timestamp),
        "open": _finite(payload.get("o"), "payload.o"),
        "high": _finite(payload.get("h"), "payload.h"),
        "low": _finite(payload.get("l"), "payload.l"),
        "close": _finite(payload.get("c"), "payload.c"),
        "volume": (None if payload.get("v") is None
                   else _finite(payload.get("v"), "payload.v")),
        "source_object_key": str(object_key or ""),
    }


def candle_symbol_population(
    shadow_runtime_rows: Iterable[Mapping[str, Any]],
) -> tuple[str, ...]:
    """The governed instrument population of one frozen shadow lifecycle set.

    The candle authority may only carry bars for instruments this frontier's own
    ``shadow_runtime`` population actually traded.  A snapshot whose lifecycle
    population names no instrument cannot anchor a candle authority at all, and
    that fails closed instead of admitting the whole stream.
    """
    symbols: set[str] = set()
    for row in shadow_runtime_rows:
        if not isinstance(row, Mapping):
            continue
        symbol = str(row.get("symbol") or "").strip().upper()
        if symbol:
            symbols.add(symbol)
    if not symbols:
        raise CandleAuthorityError(
            UNKNOWN_GOVERNED_SYMBOL_POPULATION,
            "frozen shadow_runtime population names no instrument")
    return tuple(sorted(symbols))


def source_authority_material(snapshot_authority: Mapping[str, Any]) -> dict[str, Any]:
    """The evidence-authority fields a candle read must agree with."""
    return {name: snapshot_authority.get(name) for name in SOURCE_AUTHORITY_FIELDS}


# ── Frozen governed artifact ────────────────────────────────────────────────
@dataclass(frozen=True)
class GovernedM5CandleAuthority:
    """One immutable, content-addressed governed M5 candle authority."""

    schema: str
    authority_id: str
    produced_at: str
    producer_identity: str
    producer_version: str
    authority_identity: str
    dataset: str
    dataset_schema_version: str
    event_type: str
    source: str
    timeframe: str
    snapshot_id: str
    snapshot_fingerprint: str
    investigation_epoch: str
    frontier_start: str
    frontier_end: str
    source_authority: Mapping[str, Any]
    source_authority_digest: str
    symbols: tuple[str, ...]
    bar_count: int
    first_bar_utc_ms: int
    last_bar_utc_ms: int
    object_identities: tuple[tuple[str, str, int, int], ...]
    object_manifest_digest: str
    ordering_rule: str
    duplicate_rule: str
    completeness: Mapping[str, Any]
    bars: tuple[Mapping[str, Any], ...]
    candle_rows: tuple[Mapping[str, Any], ...]
    content_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema != M5_CANDLE_AUTHORITY_SCHEMA:
            raise CandleAuthorityError(INVALID_SCHEMA, str(self.schema))
        if not is_authority_identity(self.authority_id):
            raise CandleAuthorityError(INVALID_SCHEMA, "authority_id")
        if self.authority_identity != M5_CANDLE_AUTHORITY_IDENTITY:
            raise CandleAuthorityError(
                SOURCE_AUTHORITY_MISMATCH, str(self.authority_identity))
        if self.dataset != M5_CANDLE_DATASET:
            raise CandleAuthorityError(WRONG_DATASET, str(self.dataset))
        if self.dataset_schema_version != current_schema(M5_CANDLE_DATASET):
            raise CandleAuthorityError(
                INVALID_SCHEMA, str(self.dataset_schema_version))
        if self.event_type != M5_CANDLE_EVENT_TYPE:
            raise CandleAuthorityError(WRONG_EVENT_TYPE, str(self.event_type))
        if self.source != M5_CANDLE_SOURCE:
            raise CandleAuthorityError(WRONG_SOURCE, str(self.source))
        if self.timeframe != M5_CANDLE_TIMEFRAME:
            raise CandleAuthorityError(WRONG_TIMEFRAME, str(self.timeframe))
        if int(self.bar_count) != len(self.bars):
            raise CandleAuthorityError(INCOMPLETE_BARS, "bar_count")
        if int(self.bar_count) != len(self.candle_rows):
            raise CandleAuthorityError(INCOMPLETE_BARS, "candle_rows")
        if int(self.bar_count) > 0:
            if (int(self.first_bar_utc_ms) != int(self.bars[0]["ts_utc_ms"])
                    or int(self.last_bar_utc_ms) != int(self.bars[-1]["ts_utc_ms"])):
                raise CandleAuthorityError(INCOMPLETE_BARS, "first_last_bar")
        object.__setattr__(self, "content_digest", _digest(self.material()))

    def material(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "authority_id": self.authority_id,
            "produced_at": self.produced_at,
            "producer_identity": self.producer_identity,
            "producer_version": self.producer_version,
            "authority_identity": self.authority_identity,
            "dataset": self.dataset,
            "dataset_schema_version": self.dataset_schema_version,
            "event_type": self.event_type,
            "source": self.source,
            "timeframe": self.timeframe,
            "snapshot_id": self.snapshot_id,
            "snapshot_fingerprint": self.snapshot_fingerprint,
            "investigation_epoch": self.investigation_epoch,
            "frontier_start": self.frontier_start,
            "frontier_end": self.frontier_end,
            "source_authority": dict(self.source_authority),
            "source_authority_digest": self.source_authority_digest,
            "symbols": list(self.symbols),
            "bar_count": int(self.bar_count),
            "first_bar_utc_ms": int(self.first_bar_utc_ms),
            "last_bar_utc_ms": int(self.last_bar_utc_ms),
            "first_bar_utc": _iso_utc(int(self.first_bar_utc_ms)),
            "last_bar_utc": _iso_utc(int(self.last_bar_utc_ms)),
            "object_identities": [list(item) for item in self.object_identities],
            "object_manifest_digest": self.object_manifest_digest,
            "ordering_rule": self.ordering_rule,
            "duplicate_rule": self.duplicate_rule,
            "completeness": dict(self.completeness),
            "bars": [dict(item) for item in self.bars],
            "candle_rows": [dict(item) for item in self.candle_rows],
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.material(), "content_digest": self.content_digest}


@dataclass(frozen=True)
class GovernedM5CandleAuthorityBinding:
    """The governed membership pinning one candle authority to one frontier.

    The common investigation snapshot's bound-dataset set is closed, so the
    admission of candle authority is an explicit, snapshot-pinned governed
    membership rather than a silent change to the snapshot contract.  Historical
    snapshots therefore never gain candle authority.
    """

    schema: str
    authority_id: str
    content_digest: str
    authority_identity: str
    dataset: str
    dataset_schema_version: str
    snapshot_id: str
    snapshot_fingerprint: str
    investigation_epoch: str
    frontier_start: str
    frontier_end: str
    symbols: tuple[str, ...]
    bar_count: int
    first_bar_utc_ms: int
    last_bar_utc_ms: int
    source_authority_digest: str
    producer_identity: str
    producer_version: str
    bound_at: str

    def __post_init__(self) -> None:
        if self.schema != M5_CANDLE_BINDING_SCHEMA:
            raise CandleAuthorityError(INVALID_SCHEMA, str(self.schema))
        if not is_authority_identity(self.authority_id):
            raise CandleAuthorityError(INVALID_SCHEMA, "authority_id")
        if self.authority_identity != M5_CANDLE_AUTHORITY_IDENTITY:
            raise CandleAuthorityError(
                SOURCE_AUTHORITY_MISMATCH, str(self.authority_identity))
        if self.dataset != M5_CANDLE_DATASET:
            raise CandleAuthorityError(WRONG_DATASET, str(self.dataset))
        for name, value in (
            ("content_digest", self.content_digest),
            ("snapshot_fingerprint", self.snapshot_fingerprint),
            ("source_authority_digest", self.source_authority_digest),
            ("object_manifest_digest", None),
        ):
            if name == "object_manifest_digest":
                continue
            text = str(value or "")
            if len(text) != 64 or any(
                    ch not in "0123456789abcdef" for ch in text):
                raise CandleAuthorityError(DIGEST_MISMATCH, name)

    def material(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "authority_id": self.authority_id,
            "content_digest": self.content_digest,
            "authority_identity": self.authority_identity,
            "dataset": self.dataset,
            "dataset_schema_version": self.dataset_schema_version,
            "snapshot_id": self.snapshot_id,
            "snapshot_fingerprint": self.snapshot_fingerprint,
            "investigation_epoch": self.investigation_epoch,
            "frontier_start": self.frontier_start,
            "frontier_end": self.frontier_end,
            "symbols": list(self.symbols),
            "bar_count": int(self.bar_count),
            "first_bar_utc_ms": int(self.first_bar_utc_ms),
            "last_bar_utc_ms": int(self.last_bar_utc_ms),
            "source_authority_digest": self.source_authority_digest,
            "producer_identity": self.producer_identity,
            "producer_version": self.producer_version,
            "bound_at": self.bound_at,
        }

    def to_dict(self) -> dict[str, Any]:
        return self.material()

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "GovernedM5CandleAuthorityBinding":
        if not isinstance(value, Mapping):
            raise CandleAuthorityError(INVALID_SCHEMA, "binding")
        binding = cls(
            schema=str(value.get("schema") or ""),
            authority_id=str(value.get("authority_id") or ""),
            content_digest=str(value.get("content_digest") or ""),
            authority_identity=str(value.get("authority_identity") or ""),
            dataset=str(value.get("dataset") or ""),
            dataset_schema_version=str(
                value.get("dataset_schema_version") or ""),
            snapshot_id=str(value.get("snapshot_id") or ""),
            snapshot_fingerprint=str(value.get("snapshot_fingerprint") or ""),
            investigation_epoch=str(value.get("investigation_epoch") or ""),
            frontier_start=str(value.get("frontier_start") or ""),
            frontier_end=str(value.get("frontier_end") or ""),
            symbols=tuple(str(item) for item in (value.get("symbols") or ())),
            bar_count=int(value.get("bar_count") or 0),
            first_bar_utc_ms=int(value.get("first_bar_utc_ms") or 0),
            last_bar_utc_ms=int(value.get("last_bar_utc_ms") or 0),
            source_authority_digest=str(
                value.get("source_authority_digest") or ""),
            producer_identity=str(value.get("producer_identity") or ""),
            producer_version=str(value.get("producer_version") or ""),
            bound_at=str(value.get("bound_at") or ""),
        )
        return binding


# ── Deterministic governed producer ─────────────────────────────────────────
def _window_bounds(frontier_start: str, frontier_end: str) -> tuple[int, int]:
    try:
        start = datetime.strptime(str(frontier_start), "%Y-%m-%d").replace(
            tzinfo=timezone.utc)
        end = datetime.strptime(str(frontier_end), "%Y-%m-%d").replace(
            tzinfo=timezone.utc)
    except (TypeError, ValueError) as exc:
        raise CandleAuthorityError(
            OUT_OF_FRONTIER_WINDOW, "frontier window") from exc
    if start > end:
        raise CandleAuthorityError(
            OUT_OF_FRONTIER_WINDOW, "inverted frontier window")
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000) + 86_400_000 - 1
    return start_ms, end_ms


def _collapse(records: Sequence[Mapping[str, Any]]) -> tuple[
    list[dict[str, Any]], int,
]:
    """Collapse identical repeated bars; refuse conflicting duplicates.

    The governed ``events`` dataset legitimately contains an identical copy of a
    bar in more than one object (the canonical ``part-000`` file and the
    delivery outbox handoff).  An *identical* repeat is the same governed
    observation and is collapsed deterministically to its first occurrence.  A
    repeat that carries *different* OHLC for the same ``(symbol, payload.ts)``
    is a real conflict and fails closed.
    """
    by_key: dict[tuple[str, int], dict[str, Any]] = {}
    collapsed = 0
    for record in records:
        symbol = str(record["symbol"]).strip().upper()
        timestamp = int(record["payload"]["ts"])
        key = (symbol, timestamp)
        previous = by_key.get(key)
        if previous is None:
            by_key[key] = dict(record)
            continue
        if evidence_digest([previous]) == evidence_digest([record]):
            collapsed += 1
            continue
        raise CandleAuthorityError(
            DUPLICATE_CANDLE, f"{symbol}:{timestamp}")
    ordered = [by_key[key] for key in sorted(
        by_key, key=lambda item: (item[1], item[0]))]
    return ordered, collapsed


def _assert_monotonic_per_object(
    records: Sequence[Mapping[str, Any]],
) -> None:
    """Within one governed object a symbol's bars must not go backwards."""
    last_by_symbol: dict[str, int] = {}
    for record in records:
        symbol = str(record["symbol"]).strip().upper()
        timestamp = int(record["payload"]["ts"])
        previous = last_by_symbol.get(symbol)
        if previous is not None and timestamp < previous:
            raise CandleAuthorityError(
                NON_MONOTONIC_CANDLES, f"{symbol}:{timestamp}<{previous}")
        last_by_symbol[symbol] = timestamp


def _completeness(
    bars: Sequence[Mapping[str, Any]], *, symbols: Sequence[str],
    window_start_ms: int, window_end_ms: int, collapsed: int,
) -> dict[str, Any]:
    per_symbol: dict[str, list[int]] = {str(name): [] for name in symbols}
    for bar in bars:
        per_symbol.setdefault(str(bar["symbol"]), []).append(int(bar["ts_utc_ms"]))
    rows: list[dict[str, Any]] = []
    total_expected = 0
    total_supplied = 0
    for symbol in sorted(per_symbol):
        stamps = per_symbol[symbol]
        if not stamps:
            rows.append({
                "symbol": symbol,
                "first_bar_utc_ms": None,
                "last_bar_utc_ms": None,
                "distinct_bars": 0,
                "expected_bars": 0,
                "missing_bars": 0,
                "complete": False,
                "reason": NO_CANDLE_ROWS,
            })
            continue
        span = stamps[-1] - stamps[0]
        expected = (span // M5_BAR_INTERVAL_MS) + 1
        missing = max(0, expected - len(stamps))
        total_expected += expected
        total_supplied += len(stamps)
        rows.append({
            "symbol": symbol,
            "first_bar_utc_ms": stamps[0],
            "last_bar_utc_ms": stamps[-1],
            "distinct_bars": len(stamps),
            "expected_bars": expected,
            "missing_bars": missing,
            "complete": missing == 0,
        })
    return {
        "window_start_utc_ms": int(window_start_ms),
        "window_end_utc_ms": int(window_end_ms),
        "distinct_bars": total_supplied,
        "expected_bars": total_expected,
        "missing_bars": max(0, total_expected - total_supplied),
        "collapsed_identical_rows": int(collapsed),
        "complete": bool(rows) and all(row["complete"] for row in rows),
        "symbols": rows,
        "gap_policy": (
            "missing M5 slots are reported per symbol and never interpolated, "
            "back-filled or synthesised"),
    }


def _assemble(
    *, records: Sequence[Mapping[str, Any]], symbols: Sequence[str],
    snapshot_id: str, snapshot_fingerprint: str, investigation_epoch: str,
    frontier_start: str, frontier_end: str, source_authority: Mapping[str, Any],
    object_identities: Sequence[Sequence[Any]], produced_at: str,
    require_complete: bool,
) -> GovernedM5CandleAuthority:
    """Validate, order and freeze one governed M5 candle authority."""
    if not str(snapshot_id or "").strip():
        raise CandleAuthorityError(STALE_FRONTIER, "snapshot_id")
    for name, value in (
        ("snapshot_fingerprint", snapshot_fingerprint),
        ("investigation_epoch", investigation_epoch),
    ):
        if not str(value or "").strip():
            raise CandleAuthorityError(STALE_FRONTIER, name)
    window_start_ms, window_end_ms = _window_bounds(frontier_start, frontier_end)
    population = {str(name).strip().upper() for name in symbols}
    validated: list[dict[str, Any]] = []
    for raw in records:
        record = validate_candle_record(raw)
        symbol = str(record.get("symbol")).strip().upper()
        if symbol not in population:
            raise CandleAuthorityError(
                SYMBOL_NOT_IN_FRONTIER_POPULATION, symbol)
        timestamp = int(record["payload"]["ts"])
        if not (window_start_ms <= timestamp <= window_end_ms):
            raise CandleAuthorityError(
                OUT_OF_FRONTIER_WINDOW, f"{symbol}:{timestamp}")
        validated.append(record)
    _assert_monotonic_per_object(validated)
    ordered, collapsed = _collapse(validated)
    if not ordered:
        raise CandleAuthorityError(
            CANDLE_AUTHORITY_UNAVAILABLE,
            NO_CANDLE_ROWS + ": no governed " + M5_CANDLE_AUTHORITY_IDENTITY
            + " bars in the frontier window")
    bars = tuple(canonical_bar(row) for row in ordered)
    sorted_objects = sorted(object_identities, key=lambda item: str(item[0]))
    object_material = [
        {
            "identifier": str(item[0]),
            "content_sha256": str(item[1]),
            "row_count": int(item[2]),
            "byte_size": int(item[3]),
        }
        for item in sorted_objects
    ]
    completeness = _completeness(
        bars, symbols=sorted(population), window_start_ms=window_start_ms,
        window_end_ms=window_end_ms, collapsed=collapsed)
    if require_complete and not completeness["complete"]:
        raise CandleAuthorityError(
            INCOMPLETE_BARS, str(completeness["missing_bars"]))
    resolved_authority = dict(source_authority)
    authority_digest = evidence_digest([resolved_authority])
    object_manifest_digest = evidence_digest(object_material)
    material = {
        "schema": M5_CANDLE_AUTHORITY_SCHEMA,
        "authority_identity": M5_CANDLE_AUTHORITY_IDENTITY,
        "dataset": M5_CANDLE_DATASET,
        "dataset_schema_version": current_schema(M5_CANDLE_DATASET),
        "snapshot_id": str(snapshot_id),
        "snapshot_fingerprint": str(snapshot_fingerprint),
        "investigation_epoch": str(investigation_epoch),
        "frontier_start": str(frontier_start),
        "frontier_end": str(frontier_end),
        "source_authority_digest": authority_digest,
        "symbols": sorted(population),
        "object_manifest_digest": object_manifest_digest,
        "bars": [dict(item) for item in bars],
        "completeness": completeness,
    }
    return GovernedM5CandleAuthority(
        schema=M5_CANDLE_AUTHORITY_SCHEMA,
        authority_id=authority_identity_for(_digest(material)[:16].upper()),
        produced_at=str(produced_at),
        producer_identity=M5_CANDLE_PRODUCER_IDENTITY,
        producer_version=M5_CANDLE_PRODUCER_VERSION,
        authority_identity=M5_CANDLE_AUTHORITY_IDENTITY,
        dataset=M5_CANDLE_DATASET,
        dataset_schema_version=current_schema(M5_CANDLE_DATASET),
        event_type=M5_CANDLE_EVENT_TYPE,
        source=M5_CANDLE_SOURCE,
        timeframe=M5_CANDLE_TIMEFRAME,
        snapshot_id=str(snapshot_id),
        snapshot_fingerprint=str(snapshot_fingerprint),
        investigation_epoch=str(investigation_epoch),
        frontier_start=str(frontier_start),
        frontier_end=str(frontier_end),
        source_authority=resolved_authority,
        source_authority_digest=authority_digest,
        symbols=tuple(sorted(population)),
        bar_count=len(bars),
        first_bar_utc_ms=int(bars[0]["ts_utc_ms"]),
        last_bar_utc_ms=int(bars[-1]["ts_utc_ms"]),
        object_identities=tuple(
            (str(item[0]), str(item[1]), int(item[2]), int(item[3]))
            for item in sorted_objects),
        object_manifest_digest=object_manifest_digest,
        ordering_rule=ORDERING_RULE,
        duplicate_rule=DUPLICATE_RULE,
        completeness=completeness,
        bars=bars,
        candle_rows=tuple(dict(row) for row in ordered),
    )


def _current_source_authority(source: S3ResearchDataSource) -> dict[str, Any]:
    from core.production_data_contract import DATA_CONTRACT_VERSION
    return {
        "data_contract": DATA_CONTRACT_VERSION,
        "canonical_authority":
            "core.production_data_contract.PRODUCTION_SCHEMA_REGISTRY",
        "source": "research_engine.data_access.s3_source.S3ResearchDataSource",
        "bucket": source.bucket,
        "region": str(getattr(source, "_region", "")),
        "research_profile": source.research_profile,
    }


def freeze_governed_m5_candle_authority(
    *,
    candle_rows: Sequence[Mapping[str, Any]],
    shadow_runtime_rows: Sequence[Mapping[str, Any]],
    snapshot_id: str, snapshot_fingerprint: str, investigation_epoch: str,
    frontier_start: str, frontier_end: str,
    snapshot_authority: Mapping[str, Any],
    produced_at: str, require_complete: bool = False,
) -> GovernedM5CandleAuthority:
    """Freeze an authority from already-materialized governed candle rows.

    This is the path used when a governed ``events`` population has already been
    materialized (for example from an immutable governed events snapshot) and no
    new storage read is required.  The rows are still put through the identical
    governed contract; nothing is trusted because of where it came from.
    """
    symbols = candle_symbol_population(shadow_runtime_rows)
    return _assemble(
        records=[dict(row) for row in candle_rows if isinstance(row, Mapping)],
        symbols=symbols, snapshot_id=snapshot_id,
        snapshot_fingerprint=snapshot_fingerprint,
        investigation_epoch=investigation_epoch, frontier_start=frontier_start,
        frontier_end=frontier_end,
        source_authority=source_authority_material(snapshot_authority),
        object_identities=(), produced_at=produced_at,
        require_complete=require_complete)


def build_governed_m5_candle_authority(
    *,
    shadow_runtime_rows: Sequence[Mapping[str, Any]],
    snapshot_id: str, snapshot_fingerprint: str, investigation_epoch: str,
    frontier_start: str, frontier_end: str,
    snapshot_authority: Mapping[str, Any],
    produced_at: str, source: S3ResearchDataSource | None = None,
    require_complete: bool = False,
) -> GovernedM5CandleAuthority:
    """Read the governed ``events`` M5 stream and freeze its candle authority.

    The read is bounded three ways and never widened:

    * the canonical ``events`` dataset prefix of the governed contract,
    * the frontier's own date window,
    * the frontier's own governed instrument population (the symbols of the
      frozen ``shadow_runtime`` lifecycle population).
    """
    symbols = candle_symbol_population(shadow_runtime_rows)
    if source is None:
        from research_engine.data_access.s3_source import get_default_source
        resolved_source = get_default_source()
    else:
        resolved_source = source
    expected = source_authority_material(snapshot_authority)
    actual = _current_source_authority(resolved_source)
    if expected != actual:
        raise CandleAuthorityError(
            SOURCE_AUTHORITY_MISMATCH,
            "; ".join(
                f"{name}={expected.get(name)!r}!={actual.get(name)!r}"
                for name in SOURCE_AUTHORITY_FIELDS
                if expected.get(name) != actual.get(name)))

    discovered: dict[str, dict[str, Any]] = {}
    try:
        for symbol in symbols:
            for item in resolved_source.discover_dataset_objects(
                    M5_CANDLE_DATASET, symbol=symbol,
                    start_date=str(frontier_start), end_date=str(frontier_end)):
                key = str(item.get("identifier") or "")
                if key:
                    discovered.setdefault(key, dict(item))
        if not discovered:
            raise CandleAuthorityError(
                CANDLE_AUTHORITY_UNAVAILABLE,
                NO_CANDLE_ROWS + ": no governed " + M5_CANDLE_AUTHORITY_IDENTITY
                + " objects in the frontier window")
        records: list[dict[str, Any]] = []
        identities: list[tuple[str, str, int, int]] = []
        for key in sorted(discovered):
            read = resolved_source.read_objects_for_freeze(
                M5_CANDLE_DATASET, [discovered[key]],
                expected_schema_version=current_schema(M5_CANDLE_DATASET),
                start_date=str(frontier_start), end_date=str(frontier_end))
            _assert_monotonic_per_object(read)
            records.extend(dict(row) for row in read)
            metadata = resolved_source.verified_object_metadata(
                M5_CANDLE_DATASET, key) or {}
            identities.append((
                key, str(metadata.get("content_sha256") or ""),
                int(metadata.get("row_count") or 0),
                int(metadata.get("byte_size") or 0)))
    except CandleAuthorityError:
        raise
    except ResearchDataSourceError as exc:
        raise CandleAuthorityError(
            CANDLE_AUTHORITY_UNAVAILABLE,
            f"{type(exc).__name__}:{exc}") from exc

    return _assemble(
        records=records, symbols=symbols, snapshot_id=snapshot_id,
        snapshot_fingerprint=snapshot_fingerprint,
        investigation_epoch=investigation_epoch, frontier_start=frontier_start,
        frontier_end=frontier_end,
        source_authority=expected, object_identities=identities,
        produced_at=produced_at, require_complete=require_complete)


# ── Strict parse of a frozen artifact ───────────────────────────────────────
def validate_governed_m5_candle_authority(value: Any) -> GovernedM5CandleAuthority:
    """Strictly parse a frozen candle authority; malformed input fails closed."""
    if isinstance(value, GovernedM5CandleAuthority):
        return value
    if not isinstance(value, Mapping):
        raise CandleAuthorityError(INVALID_SCHEMA, "artifact")
    artifact = GovernedM5CandleAuthority(
        schema=str(value.get("schema") or ""),
        authority_id=str(value.get("authority_id") or ""),
        produced_at=str(value.get("produced_at") or ""),
        producer_identity=str(value.get("producer_identity") or ""),
        producer_version=str(value.get("producer_version") or ""),
        authority_identity=str(value.get("authority_identity") or ""),
        dataset=str(value.get("dataset") or ""),
        dataset_schema_version=str(value.get("dataset_schema_version") or ""),
        event_type=str(value.get("event_type") or ""),
        source=str(value.get("source") or ""),
        timeframe=str(value.get("timeframe") or ""),
        snapshot_id=str(value.get("snapshot_id") or ""),
        snapshot_fingerprint=str(value.get("snapshot_fingerprint") or ""),
        investigation_epoch=str(value.get("investigation_epoch") or ""),
        frontier_start=str(value.get("frontier_start") or ""),
        frontier_end=str(value.get("frontier_end") or ""),
        source_authority=dict(value.get("source_authority") or {}),
        source_authority_digest=str(
            value.get("source_authority_digest") or ""),
        symbols=tuple(str(item) for item in (value.get("symbols") or ())),
        bar_count=int(value.get("bar_count") or 0),
        first_bar_utc_ms=int(value.get("first_bar_utc_ms") or 0),
        last_bar_utc_ms=int(value.get("last_bar_utc_ms") or 0),
        object_identities=tuple(
            (str(item[0]), str(item[1]), int(item[2]), int(item[3]))
            for item in (value.get("object_identities") or ())
            if isinstance(item, Sequence) and not isinstance(item, (str, bytes))
            and len(tuple(item)) == 4),
        object_manifest_digest=str(value.get("object_manifest_digest") or ""),
        ordering_rule=str(value.get("ordering_rule") or ""),
        duplicate_rule=str(value.get("duplicate_rule") or ""),
        completeness=dict(value.get("completeness") or {}),
        bars=tuple(dict(item) for item in (value.get("bars") or ())
                   if isinstance(item, Mapping)),
        candle_rows=tuple(
            dict(item) for item in (value.get("candle_rows") or ())
            if isinstance(item, Mapping)),
    )
    supplied = str(value.get("content_digest") or "")
    if supplied and supplied != artifact.content_digest:
        raise CandleAuthorityError(DIGEST_MISMATCH, "content_digest")
    if artifact.producer_identity != M5_CANDLE_PRODUCER_IDENTITY:
        raise CandleAuthorityError(INVALID_SCHEMA, "producer_identity")
    return artifact


def bind_m5_candle_authority(
    authority: GovernedM5CandleAuthority, *, bound_at: str,
) -> GovernedM5CandleAuthorityBinding:
    """Admit one frozen candle authority to its own snapshot's boundary."""
    artifact = validate_governed_m5_candle_authority(authority)
    return GovernedM5CandleAuthorityBinding(
        schema=M5_CANDLE_BINDING_SCHEMA,
        authority_id=artifact.authority_id,
        content_digest=artifact.content_digest,
        authority_identity=artifact.authority_identity,
        dataset=artifact.dataset,
        dataset_schema_version=artifact.dataset_schema_version,
        snapshot_id=artifact.snapshot_id,
        snapshot_fingerprint=artifact.snapshot_fingerprint,
        investigation_epoch=artifact.investigation_epoch,
        frontier_start=artifact.frontier_start,
        frontier_end=artifact.frontier_end,
        symbols=tuple(artifact.symbols),
        bar_count=int(artifact.bar_count),
        first_bar_utc_ms=int(artifact.first_bar_utc_ms),
        last_bar_utc_ms=int(artifact.last_bar_utc_ms),
        source_authority_digest=artifact.source_authority_digest,
        producer_identity=artifact.producer_identity,
        producer_version=artifact.producer_version,
        bound_at=str(bound_at),
    )


def verify_m5_candle_authority_binding(
    binding: Any,
    *,
    snapshot_id: str,
    snapshot_fingerprint: str,
    investigation_epoch: str,
    authority: GovernedM5CandleAuthority | None = None,
) -> GovernedM5CandleAuthorityBinding:
    """Prove a candle membership belongs to *this* snapshot; else fail closed.

    Nothing is repaired or defaulted.  A membership from a different frontier, a
    different epoch, a superseded candle digest or a different artifact fails
    closed with a closed reason code, so a stale candle authority can never be
    paired with a newer shadow population.
    """
    if binding is None:
        raise CandleAuthorityError(CANDLE_AUTHORITY_UNAVAILABLE)
    if isinstance(binding, Mapping):
        binding = GovernedM5CandleAuthorityBinding.from_dict(binding)
    if not isinstance(binding, GovernedM5CandleAuthorityBinding):
        raise CandleAuthorityError(INVALID_SCHEMA, "binding_type")
    if binding.snapshot_id != str(snapshot_id):
        raise CandleAuthorityError(
            STALE_FRONTIER, binding.snapshot_id + "!=" + str(snapshot_id))
    if binding.snapshot_fingerprint != str(snapshot_fingerprint):
        raise CandleAuthorityError(SUPERSEDED_EVIDENCE, "snapshot_fingerprint")
    if binding.investigation_epoch != str(investigation_epoch):
        raise CandleAuthorityError(SUPERSEDED_EVIDENCE, "investigation_epoch")
    if authority is not None:
        artifact = validate_governed_m5_candle_authority(authority)
        if (artifact.authority_id != binding.authority_id
                or artifact.content_digest != binding.content_digest):
            raise CandleAuthorityError(SUPERSEDED_EVIDENCE, "artifact_identity")
    return binding


# ── Immutable governed evidence store ───────────────────────────────────────
def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _immutable_json(path: Path, payload: Mapping[str, Any]) -> None:
    """Write-once persistence: a repeated identical write is a no-op."""
    path = Path(path)
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CandleAuthorityError(
                INVALID_SCHEMA, "unreadable:" + str(path)) from exc
        if existing != json.loads(json.dumps(payload)):
            raise CandleAuthorityError(
                INVALID_SCHEMA, "identity_collision:" + str(path))
        return
    _atomic_json(path, payload)


class M5CandleAuthorityStore:
    """Immutable, content-addressed store of frozen M5 candle authorities."""

    def __init__(self, directory: Path | str | None = None) -> None:
        self.directory = Path(
            directory if directory is not None
            else DEFAULT_M5_CANDLE_AUTHORITY_DIRECTORY)

    def path_for(self, authority_id: str) -> Path:
        return self.directory / (str(authority_id) + ".json")

    def binding_path_for(self, authority_id: str) -> Path:
        return self.directory / (str(authority_id) + ".binding.json")

    def register(
        self, authority: GovernedM5CandleAuthority, *, bound_at: str = "",
    ) -> GovernedM5CandleAuthority:
        """Freeze one authority and its snapshot-pinned binding, write-once."""
        artifact = validate_governed_m5_candle_authority(authority)
        binding = bind_m5_candle_authority(
            artifact, bound_at=bound_at or artifact.produced_at)
        _immutable_json(self.path_for(artifact.authority_id), artifact.to_dict())
        _immutable_json(
            self.binding_path_for(artifact.authority_id), binding.to_dict())
        _atomic_json(self.directory / _LATEST_POINTER, {
            "authority_id": artifact.authority_id,
            "content_digest": artifact.content_digest,
            "snapshot_id": artifact.snapshot_id,
            "snapshot_fingerprint": artifact.snapshot_fingerprint,
            "investigation_epoch": artifact.investigation_epoch,
            "bar_count": int(artifact.bar_count),
            "produced_at": artifact.produced_at,
        })
        return artifact

    def authority_ids(self) -> tuple[str, ...]:
        if not self.directory.is_dir():
            return ()
        return tuple(sorted(
            path.stem for path in self.directory.glob(
                M5_CANDLE_AUTHORITY_ID_PREFIX + "*.json")
            if not path.name.endswith(".binding.json")))

    def load(self, authority_id: str) -> GovernedM5CandleAuthority | None:
        path = self.path_for(authority_id)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CandleAuthorityError(
                INVALID_SCHEMA, "unreadable:" + str(path)) from exc
        return validate_governed_m5_candle_authority(payload)

    def binding_for(
        self, authority_id: str,
    ) -> GovernedM5CandleAuthorityBinding | None:
        path = self.binding_path_for(authority_id)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CandleAuthorityError(
                INVALID_SCHEMA, "unreadable:" + str(path)) from exc
        return GovernedM5CandleAuthorityBinding.from_dict(payload)

    def latest_pointer(self) -> dict[str, Any] | None:
        path = self.directory / _LATEST_POINTER
        if not path.is_file():
            return None
        try:
            return dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return None

    def load_latest(self) -> GovernedM5CandleAuthority | None:
        pointer = self.latest_pointer()
        if not pointer:
            return None
        return self.load(str(pointer.get("authority_id") or ""))

    def for_snapshot(
        self, snapshot_id: str,
    ) -> tuple[GovernedM5CandleAuthority, GovernedM5CandleAuthorityBinding] | None:
        """The registered authority pinned to one snapshot, if any.

        Only a membership whose snapshot identity matches is returned, so an
        unchanged frontier reuses its deterministic authority and a different
        frontier can never inherit it.
        """
        for authority_id in self.authority_ids():
            binding = self.binding_for(authority_id)
            if binding is None or binding.snapshot_id != str(snapshot_id):
                continue
            artifact = self.load(authority_id)
            if artifact is None:
                continue
            return artifact, binding
        return None


__all__ = [
    "CANDLE_AUTHORITY_UNAVAILABLE",
    "CandleAuthorityError",
    "DEFAULT_M5_CANDLE_AUTHORITY_DIRECTORY",
    "DIGEST_MISMATCH",
    "DUPLICATE_CANDLE",
    "DUPLICATE_RULE",
    "GovernedM5CandleAuthority",
    "GovernedM5CandleAuthorityBinding",
    "INCOMPLETE_BARS",
    "INVALID_SCHEMA",
    "M5_BAR_INTERVAL_MS",
    "M5_BAR_INTERVAL_SECONDS",
    "M5_CANDLE_AUTHORITY_IDENTITY",
    "M5_CANDLE_AUTHORITY_ID_PREFIX",
    "M5_CANDLE_AUTHORITY_SCHEMA",
    "M5_CANDLE_BINDING_SCHEMA",
    "M5_CANDLE_DATASET",
    "M5_CANDLE_EVENT_TYPE",
    "M5_CANDLE_PRODUCER_IDENTITY",
    "M5_CANDLE_PRODUCER_VERSION",
    "M5_CANDLE_SOURCE",
    "M5_CANDLE_TIMEFRAME",
    "M5CandleAuthorityStore",
    "MISSING_REQUIRED_FIELDS",
    "NON_MONOTONIC_CANDLES",
    "NO_CANDLE_ROWS",
    "ORDERING_RULE",
    "OUT_OF_FRONTIER_WINDOW",
    "REASON_CODES",
    "REQUIRED_CANDLE_FIELDS",
    "REQUIRED_PAYLOAD_FIELDS",
    "SOURCE_AUTHORITY_FIELDS",
    "SOURCE_AUTHORITY_MISMATCH",
    "STALE_FRONTIER",
    "SUPERSEDED_EVIDENCE",
    "SYMBOL_NOT_IN_FRONTIER_POPULATION",
    "UNKNOWN_GOVERNED_SYMBOL_POPULATION",
    "WRONG_DATASET",
    "WRONG_EVENT_TYPE",
    "WRONG_SOURCE",
    "WRONG_TIMEFRAME",
    "authority_identity_for",
    "bind_m5_candle_authority",
    "build_governed_m5_candle_authority",
    "candle_symbol_population",
    "canonical_bar",
    "freeze_governed_m5_candle_authority",
    "is_authority_identity",
    "source_authority_material",
    "validate_candle_record",
    "validate_governed_m5_candle_authority",
    "verify_m5_candle_authority_binding",
]
