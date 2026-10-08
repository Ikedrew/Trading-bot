"""Governed M5 candle evidence authority: contract and frontier membership.

These regressions prove the governed M5 candle authority closes the last BUILD
gap in the candidate-capable Q71 path without weakening any existing guarantee:

    governed ``events`` (events_v1) M5 CANDLE/mt5_data objects
        -> strict governed contract validation
        -> immutable, content-addressed candle authority
        -> snapshot-pinned governed membership
        -> frozen candle rows for the counterfactual producer
        -> Q71 worker (frozen object only; never storage)

Nothing here invents a bar, defaults a timeframe, retrofits a historical
snapshot or lets a consumer reach storage.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from core.production_data_contract import current_schema, s3_base_prefix
from research_engine.control_plane.governed_m5_candle_authority import (
    CANDLE_AUTHORITY_UNAVAILABLE,
    DIGEST_MISMATCH,
    DUPLICATE_CANDLE,
    INCOMPLETE_BARS,
    INVALID_SCHEMA,
    M5_CANDLE_AUTHORITY_IDENTITY,
    M5_CANDLE_PRODUCER_IDENTITY,
    M5CandleAuthorityStore,
    MISSING_REQUIRED_FIELDS,
    NON_MONOTONIC_CANDLES,
    OUT_OF_FRONTIER_WINDOW,
    SOURCE_AUTHORITY_MISMATCH,
    STALE_FRONTIER,
    SUPERSEDED_EVIDENCE,
    SYMBOL_NOT_IN_FRONTIER_POPULATION,
    UNKNOWN_GOVERNED_SYMBOL_POPULATION,
    WRONG_EVENT_TYPE,
    WRONG_SOURCE,
    WRONG_TIMEFRAME,
    CandleAuthorityError,
    _current_source_authority,
    bind_m5_candle_authority,
    build_governed_m5_candle_authority,
    candle_symbol_population,
    freeze_governed_m5_candle_authority,
    validate_candle_record,
    validate_governed_m5_candle_authority,
    verify_m5_candle_authority_binding,
)
from research_engine.control_plane.shadow_timestamp_normalization import (
    CANDLE_NORMALIZATION_VERSION,
)
from research_engine.data_access.s3_source import (
    ResearchDataSourceError,
    S3ResearchDataSource,
)
from research_engine.v10.investigation_snapshot import BOUND_DATASETS


PRODUCED_AT = "2026-10-08T00:00:00+00:00"
FRONTIER_START = "2026-09-25"
FRONTIER_END = "2026-09-25"
SNAPSHOT_ID = "ISNAP-M5CANDLE0000000000000001"
SNAPSHOT_FINGERPRINT = "a" * 64
INVESTIGATION_EPOCH = "INVESTIGATION-EPOCH-M5CANDLE0000000001"

# 2026-09-25T00:00:00Z
BAR0 = 1_790_294_400_000


class MemoryS3:
    """A minimal, deterministic S3 double (same shape as the snapshot tests)."""

    def __init__(self, objects: dict[str, str]):
        self.objects = dict(objects)
        self.list_prefixes: list[str] = []
        self.get_calls: list[str] = []

    def list_objects_v2(self, **kwargs):
        prefix = str(kwargs.get("Prefix") or "")
        self.list_prefixes.append(prefix)
        return {
            "IsTruncated": False,
            "Contents": [
                {
                    "Key": key,
                    "ETag": '"' + hashlib.md5(body.encode()).hexdigest() + '"',
                    "Size": len(body.encode()),
                    "LastModified": "2026-09-26T00:00:00Z",
                }
                for key, body in sorted(self.objects.items())
                if key.startswith(prefix)
            ],
        }

    def get_object(self, **kwargs):
        key = str(kwargs["Key"])
        self.get_calls.append(key)
        if key not in self.objects:
            raise KeyError(key)
        body = self.objects[key]

        class Body:
            def read(self):
                return body.encode("utf-8")

        return {
            "Body": Body(),
            "ETag": '"' + hashlib.md5(body.encode()).hexdigest() + '"',
            "ContentLength": len(body.encode()),
            "LastModified": "2026-09-26T00:00:00Z",
        }


def _candle(symbol, index, *, o=1.10, h=1.11, l=1.09, c=1.105, volume=12.0,
            **overrides):
    """One governed ``events_v1`` M5 CANDLE record in the real production shape."""
    ts = BAR0 + index * 300_000
    payload = {
        "ts": ts,
        "o": o, "h": h, "l": l, "c": c, "v": volume,
        "timestamp_normalization_version": CANDLE_NORMALIZATION_VERSION,
    }
    record = {
        "ts_utc_ms": ts,
        "type": "CANDLE",
        "symbol": symbol,
        "timeframe": "M5",
        "source": "mt5_data",
        "schema_version": current_schema("events"),
        "payload": payload,
    }
    for key, value in overrides.items():
        if key == "payload":
            record["payload"] = value
        else:
            record[key] = value
    return record


def _events_key(symbol, day="2026-09-25", part="part-000.jsonl"):
    return (
        f"{s3_base_prefix('events')}/schema_version={current_schema('events')}"
        f"/symbol={symbol}/date={day}/{part}"
    )


def _jsonl(rows):
    return "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)


def _source(objects):
    fake = MemoryS3(objects)
    return fake, S3ResearchDataSource(bucket="test-bucket", client=fake)


def _objects(*, eurusd_bars=4, gbpusd_bars=0, eurusd_overrides=None,
             extra=None):
    objects: dict[str, str] = {}
    if eurusd_bars:
        rows = [
            _candle("EURUSD", index, **(eurusd_overrides or {}))
            for index in range(eurusd_bars)
        ]
        objects[_events_key("EURUSD")] = _jsonl(rows)
    if gbpusd_bars:
        rows = [_candle("GBPUSD", index) for index in range(gbpusd_bars)]
        objects[_events_key("GBPUSD")] = _jsonl(rows)
    if extra:
        objects.update(extra)
    return objects


def shadow_rows(*symbols):
    """The frozen ``shadow_runtime`` lifecycle population of the frontier."""
    rows = []
    for symbol in symbols:
        rows.append({
            "event_type": "OPEN", "symbol": symbol,
            "shadow_trade_id": "S1", "canonical_opportunity_id": "O1",
            "horizon": "SCALP", "entry_market_time_utc_epoch_s": 1_790_294_700,
        })
        rows.append({
            "event_type": "CLOSE", "symbol": symbol,
            "shadow_trade_id": "S1", "canonical_opportunity_id": "O1",
            "horizon": "SCALP", "exit_market_time_utc_epoch_s": 1_790_295_000,
        })
    return rows


def snapshot_authority(source):
    """The authority fields a real InvestigationSnapshot carries."""
    return {**_current_source_authority(source), "schemas": {"events": {}}}


def build(objects, *, symbols=("EURUSD",), **kwargs):
    fake, source = _source(objects)
    authority = build_governed_m5_candle_authority(
        shadow_runtime_rows=shadow_rows(*symbols),
        snapshot_id=SNAPSHOT_ID, snapshot_fingerprint=SNAPSHOT_FINGERPRINT,
        investigation_epoch=INVESTIGATION_EPOCH,
        frontier_start=FRONTIER_START, frontier_end=FRONTIER_END,
        snapshot_authority=snapshot_authority(source),
        produced_at=PRODUCED_AT, source=source, **kwargs)
    return authority, source


def build_with_fake(objects, *, symbols=("EURUSD",), **kwargs):
    fake, source = _source(objects)
    authority = build_governed_m5_candle_authority(
        shadow_runtime_rows=shadow_rows(*symbols),
        snapshot_id=SNAPSHOT_ID, snapshot_fingerprint=SNAPSHOT_FINGERPRINT,
        investigation_epoch=INVESTIGATION_EPOCH,
        frontier_start=FRONTIER_START, frontier_end=FRONTIER_END,
        snapshot_authority=snapshot_authority(source),
        produced_at=PRODUCED_AT, source=source, **kwargs)
    return authority, source, fake


# ── 1-4. Schema / source / event type / timeframe ───────────────────────────
def test_governed_m5_candle_authority_contract_is_explicit():
    """1. The authority declares its whole governed contract."""
    authority, _ = build(_objects())
    assert authority.schema == "governed_m5_candle_authority_v1"
    assert authority.authority_identity == M5_CANDLE_AUTHORITY_IDENTITY
    assert authority.dataset == "events"
    assert authority.dataset_schema_version == "events_v1"
    assert authority.event_type == "CANDLE"
    assert authority.source == "mt5_data"
    assert authority.timeframe == "M5"
    assert authority.producer_identity == M5_CANDLE_PRODUCER_IDENTITY
    assert authority.bar_count == 4
    assert authority.first_bar_utc_ms == BAR0
    assert authority.last_bar_utc_ms == BAR0 + 3 * 300_000
    assert authority.symbols == ("EURUSD",)
    assert authority.ordering_rule and authority.duplicate_rule
    assert authority.completeness["distinct_bars"] == 4
    assert authority.completeness["missing_bars"] == 0
    assert authority.completeness["complete"] is True
    assert len(authority.content_digest) == 64
    # The canonical bar index exposes symbol, open time, OHLC and volume.
    first = authority.bars[0]
    assert set(first) >= {
        "symbol", "timeframe", "ts_utc_ms", "open_time_utc", "open", "high",
        "low", "close", "volume", "source_object_key"}
    assert first["open_time_utc"] == "2026-09-25T00:00:00Z"
    assert first["volume"] == 12.0


def test_candle_source_must_be_mt5_data():
    """2. A candle whose source is not ``mt5_data`` fails closed."""
    record = _candle("EURUSD", 0, source="simulated")
    with pytest.raises(CandleAuthorityError, match=WRONG_SOURCE):
        validate_candle_record(record)
    with pytest.raises(CandleAuthorityError, match=WRONG_SOURCE):
        build(_objects(eurusd_overrides={"source": "simulated"}))


def test_candle_event_type_must_be_candle():
    """3. A non-CANDLE event in the stream fails closed."""
    record = _candle("EURUSD", 0, type="FEATURE_UPDATE")
    with pytest.raises(CandleAuthorityError, match=WRONG_EVENT_TYPE):
        validate_candle_record(record)
    with pytest.raises(CandleAuthorityError, match=WRONG_EVENT_TYPE):
        build(_objects(eurusd_overrides={"type": "FEATURE_UPDATE"}))


def test_candle_timeframe_must_be_m5():
    """4. A candle whose timeframe is not ``M5`` fails closed."""
    record = _candle("EURUSD", 0, timeframe="M15")
    with pytest.raises(CandleAuthorityError, match=WRONG_TIMEFRAME):
        validate_candle_record(record)
    with pytest.raises(CandleAuthorityError, match=WRONG_TIMEFRAME):
        build(_objects(eurusd_overrides={"timeframe": "M15"}))


def test_candle_schema_version_must_be_the_governed_one():
    """1. A candle on any other schema version fails closed."""
    record = _candle("EURUSD", 0, schema_version="events_v2")
    with pytest.raises(CandleAuthorityError, match=INVALID_SCHEMA):
        validate_candle_record(record)


# ── 5/6/13. Deterministic identity and change sensitivity ───────────────────
def test_candle_authority_identity_is_deterministic():
    """5. Identical governed evidence reuses the identical authority identity."""
    first, _ = build(_objects())
    second, _ = build(_objects())
    assert first.authority_id == second.authority_id
    assert first.content_digest == second.content_digest
    assert first.to_dict() == second.to_dict()


def test_candle_digest_changes_when_candles_change():
    """6. Changed candle evidence changes the governed evidence identity."""
    base, _ = build(_objects())
    changed, _ = build(_objects(eurusd_overrides={"c": 1.115}))
    assert changed.content_digest != base.content_digest
    assert changed.authority_id != base.authority_id


def test_candle_authority_identity_covers_the_frontier_window():
    """13. The frontier window is part of the authority's own identity."""
    base, _ = build(_objects())
    _, source = _source(_objects())
    wider = build_governed_m5_candle_authority(
        shadow_runtime_rows=shadow_rows("EURUSD"),
        snapshot_id=SNAPSHOT_ID, snapshot_fingerprint=SNAPSHOT_FINGERPRINT,
        investigation_epoch=INVESTIGATION_EPOCH,
        frontier_start="2026-09-24", frontier_end="2026-09-26",
        snapshot_authority=snapshot_authority(source),
        produced_at=PRODUCED_AT, source=source)
    assert wider.frontier_start != base.frontier_start
    assert wider.content_digest != base.content_digest


def test_candle_authority_identity_covers_its_snapshot():
    """13. The snapshot identity is part of the authority's own identity."""
    _, source = _source(_objects())
    other = build_governed_m5_candle_authority(
        shadow_runtime_rows=shadow_rows("EURUSD"),
        snapshot_id="ISNAP-OTHERFRONTIER000000000002",
        snapshot_fingerprint="b" * 64,
        investigation_epoch="INVESTIGATION-EPOCH-OTHER",
        frontier_start=FRONTIER_START, frontier_end=FRONTIER_END,
        snapshot_authority=snapshot_authority(source),
        produced_at=PRODUCED_AT, source=source)
    base, _ = build(_objects())
    assert other.content_digest != base.content_digest


def test_unchanged_rerun_produces_no_new_immutable_evidence(tmp_path):
    """33. An unchanged rerun deduplicates onto the same frozen artifact."""
    authority, _ = build(_objects())
    store = M5CandleAuthorityStore(tmp_path / "authority")
    store.register(authority, bound_at=PRODUCED_AT)
    store.register(authority, bound_at=PRODUCED_AT)
    assert store.authority_ids() == (authority.authority_id,)
    restored = store.load(authority.authority_id)
    assert restored is not None
    assert restored.content_digest == authority.content_digest


def test_changed_candles_produce_new_immutable_evidence(tmp_path):
    """34. Changed candle evidence produces a new immutable authority."""
    store = M5CandleAuthorityStore(tmp_path / "authority")
    base, _ = build(_objects())
    changed, _ = build(_objects(eurusd_overrides={"c": 1.115}))
    store.register(base, bound_at=PRODUCED_AT)
    store.register(changed, bound_at=PRODUCED_AT)
    assert set(store.authority_ids()) == {
        base.authority_id, changed.authority_id}
    # Both remain independently readable and digest-verified.
    assert store.load(base.authority_id).content_digest == base.content_digest
    assert (store.load(changed.authority_id).content_digest
            == changed.content_digest)


def test_tampered_frozen_authority_fails_closed(tmp_path):
    """A digest that does not match the frozen content is refused."""
    authority, _ = build(_objects())
    store = M5CandleAuthorityStore(tmp_path / "authority")
    store.register(authority, bound_at=PRODUCED_AT)
    path = store.path_for(authority.authority_id)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["bars"][0]["close"] = 9.99
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(CandleAuthorityError, match=DIGEST_MISMATCH):
        store.load(authority.authority_id)


# ── 7-9. Duplicate, monotonicity, required fields ───────────────────────────
def test_conflicting_duplicate_candle_fails_closed():
    """7. A conflicting duplicate ``(symbol, ts)`` fails closed."""
    duplicate = _objects(eurusd_bars=0)
    rows = [_candle("EURUSD", 0), _candle("EURUSD", 1),
            _candle("EURUSD", 1, c=9.99), _candle("EURUSD", 2)]
    duplicate[_events_key("EURUSD")] = _jsonl(rows)
    with pytest.raises(CandleAuthorityError, match=DUPLICATE_CANDLE):
        build(duplicate)


def test_identical_duplicate_candle_is_collapsed_not_duplicated():
    """7. An identical repeat is the same governed observation, not two bars."""
    duplicate = _objects(eurusd_bars=0)
    rows = [_candle("EURUSD", 0), _candle("EURUSD", 1),
            _candle("EURUSD", 1), _candle("EURUSD", 2)]
    duplicate[_events_key("EURUSD")] = _jsonl(rows)
    authority, _ = build(duplicate)
    assert authority.bar_count == 3
    assert authority.completeness["collapsed_identical_rows"] == 1


def test_non_monotonic_candles_fail_closed():
    """8. Bars that go backwards inside one governed object fail closed."""
    out_of_order = _objects(eurusd_bars=0)
    rows = [_candle("EURUSD", index) for index in (0, 2, 1)]
    out_of_order[_events_key("EURUSD")] = _jsonl(rows)
    with pytest.raises(CandleAuthorityError, match=NON_MONOTONIC_CANDLES):
        build(out_of_order)


def test_missing_required_fields_fail_closed():
    """9. A record missing a required field fails closed."""
    for field in ("symbol", "timeframe", "source", "type", "schema_version",
                  "payload"):
        record = _candle("EURUSD", 0)
        record.pop(field)
        with pytest.raises(CandleAuthorityError, match=MISSING_REQUIRED_FIELDS):
            validate_candle_record(record)
    for field in ("ts", "o", "h", "l", "c"):
        record = _candle("EURUSD", 0)
        record["payload"].pop(field)
        with pytest.raises(CandleAuthorityError, match=MISSING_REQUIRED_FIELDS):
            validate_candle_record(record)


def test_non_m5_aligned_timestamp_fails_closed():
    """3. A bar whose timestamp is not on the M5 grid fails closed."""
    broken = _objects(eurusd_bars=0)
    record = _candle("EURUSD", 0)
    record["payload"]["ts"] = BAR0 + 1
    broken[_events_key("EURUSD")] = _jsonl([record])
    with pytest.raises(CandleAuthorityError, match=OUT_OF_FRONTIER_WINDOW):
        build(broken)


def test_out_of_window_candle_fails_closed():
    """14. A candle outside the frontier window fails closed."""
    out_of_window = _objects(eurusd_bars=0)
    record = _candle("EURUSD", 0)
    record["payload"]["ts"] = BAR0 - 86_400_000
    out_of_window[_events_key("EURUSD")] = _jsonl([record])
    with pytest.raises(CandleAuthorityError, match=OUT_OF_FRONTIER_WINDOW):
        build(out_of_window)


def test_require_complete_reports_missing_bars_as_fail_closed():
    """Missing bars are an explicit completeness/fail-closed state."""
    gapped = _objects(eurusd_bars=0)
    rows = [_candle("EURUSD", 0), _candle("EURUSD", 3)]
    gapped[_events_key("EURUSD")] = _jsonl(rows)
    authority, _ = build(gapped)
    assert authority.completeness["complete"] is False
    assert authority.completeness["missing_bars"] == 2
    with pytest.raises(CandleAuthorityError, match=INCOMPLETE_BARS):
        build(gapped, require_complete=True)


# ── 10-15. Snapshot / frontier membership ──────────────────────────────────
def test_absent_candle_authority_fails_closed():
    """20. No governed candle objects means no authority at all."""
    with pytest.raises(CandleAuthorityError,
                       match=CANDLE_AUTHORITY_UNAVAILABLE):
        build({})


def test_frontier_population_without_instruments_fails_closed():
    """Only the frontier's own governed instrument population is admitted."""
    with pytest.raises(CandleAuthorityError,
                       match=UNKNOWN_GOVERNED_SYMBOL_POPULATION):
        candle_symbol_population([{"event_type": "OPEN"}])


def test_candle_for_an_instrument_outside_the_frontier_fails_closed():
    """15. A candle the frontier never traded cannot enter the authority."""
    _, source = _source(_objects(gbpusd_bars=4))
    with pytest.raises(CandleAuthorityError,
                       match=SYMBOL_NOT_IN_FRONTIER_POPULATION):
        freeze_governed_m5_candle_authority(
            candle_rows=[_candle("GBPUSD", 0)],
            shadow_runtime_rows=shadow_rows("EURUSD"),
            snapshot_id=SNAPSHOT_ID,
            snapshot_fingerprint=SNAPSHOT_FINGERPRINT,
            investigation_epoch=INVESTIGATION_EPOCH,
            frontier_start=FRONTIER_START, frontier_end=FRONTIER_END,
            snapshot_authority=snapshot_authority(source),
            produced_at=PRODUCED_AT)


def test_only_the_frontier_instruments_are_read():
    """No widening: the read is bounded to the frontier's own symbols."""
    objects = _objects(gbpusd_bars=4)
    authority, _, fake = build_with_fake(objects, symbols=("EURUSD",))
    assert authority.symbols == ("EURUSD",)
    assert fake.list_prefixes
    assert all("symbol=EURUSD" in prefix for prefix in fake.list_prefixes)
    assert all("GBPUSD" not in key for key in fake.get_calls)


def test_source_authority_mismatch_fails_closed():
    """A candle read from another evidence authority never belongs here."""
    _, source = _source(_objects())
    wrong = dict(snapshot_authority(source))
    wrong["bucket"] = "another-bucket"
    with pytest.raises(CandleAuthorityError, match=SOURCE_AUTHORITY_MISMATCH):
        build_governed_m5_candle_authority(
            shadow_runtime_rows=shadow_rows("EURUSD"),
            snapshot_id=SNAPSHOT_ID, snapshot_fingerprint=SNAPSHOT_FINGERPRINT,
            investigation_epoch=INVESTIGATION_EPOCH,
            frontier_start=FRONTIER_START, frontier_end=FRONTIER_END,
            snapshot_authority=wrong, produced_at=PRODUCED_AT, source=source)


def test_membership_is_pinned_to_the_exact_snapshot(tmp_path):
    """10/11/12. A membership admits only its own snapshot's authority."""
    authority, _ = build(_objects())
    store = M5CandleAuthorityStore(tmp_path / "authority")
    store.register(authority, bound_at=PRODUCED_AT)
    binding = store.binding_for(authority.authority_id)
    assert binding is not None
    assert binding.snapshot_id == SNAPSHOT_ID
    assert binding.content_digest == authority.content_digest
    assert binding.authority_identity == M5_CANDLE_AUTHORITY_IDENTITY
    assert binding.bar_count == authority.bar_count
    verify_m5_candle_authority_binding(
        binding, snapshot_id=SNAPSHOT_ID,
        snapshot_fingerprint=SNAPSHOT_FINGERPRINT,
        investigation_epoch=INVESTIGATION_EPOCH, authority=authority)
    # A different snapshot cannot borrow it.
    with pytest.raises(CandleAuthorityError, match=STALE_FRONTIER):
        verify_m5_candle_authority_binding(
            binding, snapshot_id="ISNAP-OTHERFRONTIER000000000002",
            snapshot_fingerprint=SNAPSHOT_FINGERPRINT,
            investigation_epoch=INVESTIGATION_EPOCH)


def test_stale_or_superseded_membership_fails_closed(tmp_path):
    """14/15. A stale candle/frontier pairing is refused, never repaired."""
    authority, _ = build(_objects())
    store = M5CandleAuthorityStore(tmp_path / "authority")
    store.register(authority, bound_at=PRODUCED_AT)
    binding = store.binding_for(authority.authority_id).to_dict()
    stale = dict(binding, snapshot_fingerprint="c" * 64)
    with pytest.raises(CandleAuthorityError, match=SUPERSEDED_EVIDENCE):
        verify_m5_candle_authority_binding(
            stale, snapshot_id=SNAPSHOT_ID,
            snapshot_fingerprint=SNAPSHOT_FINGERPRINT,
            investigation_epoch=INVESTIGATION_EPOCH)
    stale = dict(binding, investigation_epoch="INVESTIGATION-EPOCH-OLD")
    with pytest.raises(CandleAuthorityError, match=SUPERSEDED_EVIDENCE):
        verify_m5_candle_authority_binding(
            stale, snapshot_id=SNAPSHOT_ID,
            snapshot_fingerprint=SNAPSHOT_FINGERPRINT,
            investigation_epoch=INVESTIGATION_EPOCH)
    # A superseded artifact digest cannot be paired with the membership.
    other, _ = build(_objects(eurusd_overrides={"c": 1.115}))
    with pytest.raises(CandleAuthorityError, match=SUPERSEDED_EVIDENCE):
        verify_m5_candle_authority_binding(
            binding, snapshot_id=SNAPSHOT_ID,
            snapshot_fingerprint=SNAPSHOT_FINGERPRINT,
            investigation_epoch=INVESTIGATION_EPOCH, authority=other)


def test_store_lookup_is_snapshot_scoped(tmp_path):
    """An authority pinned to one frontier is never reused by another."""
    store = M5CandleAuthorityStore(tmp_path / "authority")
    base, _ = build(_objects())
    store.register(base, bound_at=PRODUCED_AT)
    assert store.for_snapshot(SNAPSHOT_ID)[0].authority_id == base.authority_id
    assert store.for_snapshot("ISNAP-OTHERFRONTIER000000000002") is None


def test_historical_snapshots_never_gain_candle_authority():
    """11/37/38. The common snapshot contract is untouched by this module."""
    from research_engine.v10 import investigation_snapshot as snapshots
    assert snapshots.MANIFEST_SCHEMA == 1
    assert "events" not in snapshots.BOUND_DATASETS
    assert "events_v1" not in snapshots.BOUND_DATASETS
    assert snapshots.BOUND_DATASETS == (
        snapshots.REQUIRED_DATASETS + snapshots.OPTIONAL_DATASETS)
    # Candle authority is a *separate* artifact with an explicit membership, so
    # a historical frozen manifest still validates exactly as it was frozen.
    authority, _ = build(_objects())
    assert authority.snapshot_id == SNAPSHOT_ID
    assert "events" not in snapshots.EXCLUDED_BY_DESIGN



def test_frozen_rows_are_directly_consumable_by_the_hd09_authority():
    """16. The frozen candle rows feed the governed exit-bar-path authority."""
    from research_engine.control_plane.governed_exit_evidence import (
        _M5_CANDLE_MISSING, build_governed_exit_evidence,
    )

    authority, _ = build(_objects())
    evidence = build_governed_exit_evidence(
        shadow_rows("EURUSD"), authority.candle_rows, with_dimensions=False)
    assert _M5_CANDLE_MISSING not in evidence.missing_evidence
    # Without the authority's frozen rows the same call reports the real gap.
    empty = build_governed_exit_evidence(shadow_rows("EURUSD"), ())
    assert _M5_CANDLE_MISSING in empty.missing_evidence


def test_authority_carries_object_level_lineage():
    """16. Producer lineage: the exact governed objects and their digests."""
    authority, _ = build(_objects())
    assert len(authority.object_identities) == 1
    key, digest, rows, size = authority.object_identities[0]
    assert key == _events_key("EURUSD")
    assert len(digest) == 64
    assert rows == 4
    assert size > 0
    assert len(authority.object_manifest_digest) == 64
    restored = validate_governed_m5_candle_authority(authority.to_dict())
    assert restored.source_authority_digest == (
        authority.source_authority_digest)


def test_authority_round_trips_through_its_frozen_form():
    """A frozen authority validates back to the identical identity."""
    authority, _ = build(_objects())
    restored = validate_governed_m5_candle_authority(authority.to_dict())
    assert restored.authority_id == authority.authority_id
    assert restored.content_digest == authority.content_digest
    assert restored.candle_rows == authority.candle_rows


def test_freeze_from_materialized_governed_rows_matches_the_read_path():
    """A previously materialized governed population freezes deterministically.

    The object-level lineage is absent on this path (nothing was read from
    storage), so the artifact digest differs from the read path; the governed
    bar population and the evidence authority are identical.
    """
    authority, source = build(_objects())
    frozen = freeze_governed_m5_candle_authority(
        candle_rows=authority.candle_rows,
        shadow_runtime_rows=shadow_rows("EURUSD"),
        snapshot_id=SNAPSHOT_ID, snapshot_fingerprint=SNAPSHOT_FINGERPRINT,
        investigation_epoch=INVESTIGATION_EPOCH,
        frontier_start=FRONTIER_START, frontier_end=FRONTIER_END,
        snapshot_authority=snapshot_authority(source),
        produced_at=PRODUCED_AT)
    assert frozen.bars == authority.bars
    assert frozen.candle_rows == authority.candle_rows
    assert frozen.symbols == authority.symbols
    assert frozen.bar_count == authority.bar_count
    assert frozen.source_authority_digest == authority.source_authority_digest
    assert frozen.object_identities == ()
    assert frozen.authority_identity == authority.authority_identity
    replay = freeze_governed_m5_candle_authority(
        candle_rows=authority.candle_rows,
        shadow_runtime_rows=shadow_rows("EURUSD"),
        snapshot_id=SNAPSHOT_ID, snapshot_fingerprint=SNAPSHOT_FINGERPRINT,
        investigation_epoch=INVESTIGATION_EPOCH,
        frontier_start=FRONTIER_START, frontier_end=FRONTIER_END,
        snapshot_authority=snapshot_authority(source),
        produced_at=PRODUCED_AT)
    assert replay.content_digest == frozen.content_digest


def test_no_alternate_or_fallback_candle_source_exists():
    """17. There is no second candle source, fixture fallback or default."""
    import research_engine.control_plane.governed_m5_candle_authority as module

    source_text = Path(module.__file__).read_text(encoding="utf-8")
    producer_text = source_text.split(
        "# ── Deterministic governed producer", 1)[1].split(
        "# ── Strict parse of a frozen artifact", 1)[0]
    for forbidden in ("read_artifact", "test_fixture", "SYNTHETIC", "fallback",
                      "_exit_path_audit", "glob(", "iterdir", "listdir",
                      "os.environ", "Path(", "open("):
        assert forbidden not in producer_text
    # The only dataset the producer may read is the governed events dataset.
    assert module.M5_CANDLE_DATASET == "events"
    assert module.M5_CANDLE_AUTHORITY_IDENTITY == "events_v1:CANDLE:mt5_data:M5"
    assert producer_text.count("discover_dataset_objects") == 1
    assert producer_text.count("read_objects_for_freeze") == 1
    assert "M5_CANDLE_DATASET" in producer_text.split(
        "discover_dataset_objects", 1)[1].split(")", 1)[0]


def test_source_read_failure_fails_closed_as_unavailable():
    """20. A blocked read is a blocked read, never a silent empty universe."""
    class Blocked(MemoryS3):
        def list_objects_v2(self, **kwargs):
            raise RuntimeError("token expired")

    source = S3ResearchDataSource(bucket="test-bucket", client=Blocked({}))
    with pytest.raises(ResearchDataSourceError):
        source.discover_dataset_objects(
            "events", start_date=FRONTIER_START, end_date=FRONTIER_END)



# ── 16/20/21/25/26. Counterfactual producer + production evaluator ──────────
def _governed_lifecycles(count: int = 2):
    """Real governed HD09 lifecycles with their authoritative M5 candle paths."""
    from tests.test_governed_scientific_result import _hd09_lifecycle

    sources = [_hd09_lifecycle(index) for index in range(count)]
    shadow_rows = []
    candle_rows = []
    for source in sources:
        shadow_rows.append(dict(source.open_event))
        shadow_rows.append(dict(source.close_event))
        candle_rows.extend(dict(row) for row in source.candle_events)
    return shadow_rows, candle_rows


def _lifecycle_window(candle_rows) -> tuple[str, str]:
    from datetime import datetime, timezone

    stamps = [int(row["payload"]["ts"]) for row in candle_rows]
    first = datetime.fromtimestamp(min(stamps) / 1000.0, tz=timezone.utc)
    last = datetime.fromtimestamp(max(stamps) / 1000.0, tz=timezone.utc)
    return first.strftime("%Y-%m-%d"), last.strftime("%Y-%m-%d")


def test_counterfactual_producer_consumes_frozen_governed_candles():
    """16/21. The frozen candle authority clears MISSING_M5_CANDLE_AUTHORITY."""
    from research_engine.control_plane.governed_counterfactual_evidence import (
        build_governed_counterfactual_evidence,
    )

    shadow_rows, candle_rows = _governed_lifecycles(2)
    start, end = _lifecycle_window(candle_rows)
    authority = freeze_governed_m5_candle_authority(
        candle_rows=candle_rows, shadow_runtime_rows=shadow_rows,
        snapshot_id=SNAPSHOT_ID, snapshot_fingerprint=SNAPSHOT_FINGERPRINT,
        investigation_epoch=INVESTIGATION_EPOCH,
        frontier_start=start, frontier_end=end,
        snapshot_authority={
            "data_contract": "production_v1",
            "canonical_authority":
                "core.production_data_contract.PRODUCTION_SCHEMA_REGISTRY",
            "source":
                "research_engine.data_access.s3_source.S3ResearchDataSource",
            "bucket": "test-bucket", "region": "eu-west-2",
            "research_profile": None,
        },
        produced_at=PRODUCED_AT)
    binding = bind_m5_candle_authority(authority, bound_at=PRODUCED_AT)
    artifact = build_governed_counterfactual_evidence(
        shadow_runtime_rows=shadow_rows,
        candle_authority=authority, candle_authority_binding=binding,
        snapshot_id=SNAPSHOT_ID, snapshot_fingerprint=SNAPSHOT_FINGERPRINT,
        investigation_epoch=INVESTIGATION_EPOCH,
        frontier_start=start, frontier_end=end, produced_at=PRODUCED_AT,
        source_dataset_identities={"events": authority.content_digest})
    assert artifact.candle_authority_id == authority.authority_id
    assert artifact.candle_authority_digest == authority.content_digest
    assert artifact.source_candle_digest == authority.content_digest
    assert "MISSING_M5_CANDLE_AUTHORITY" not in artifact.reason_codes
    assert artifact.scientifically_analysable is True
    assert artifact.rows
    # The production evaluator analyses the frozen artifact without storage.
    from research_engine.control_plane.governed_counterfactual_evidence import (
        bind_counterfactual_evidence,
    )
    from research_engine.experiments.q71_production_evaluators import (
        governed_exit_policy_counterfactual,
    )
    artifact_binding = bind_counterfactual_evidence(
        artifact, bound_at=PRODUCED_AT)
    report = governed_exit_policy_counterfactual(
        generated_question_id="GEN-CANDLE-AUTHORITY",
        datasets={"shadow_runtime": shadow_rows},
        governed_counterfactual_evidence=artifact,
        governed_counterfactual_binding=artifact_binding)
    assert report["status"] in {"COMPLETE", "INSUFFICIENT_DATA"}
    assert report["provenance"].get("fail_closed_reason") != (
        "MISSING_M5_CANDLE_AUTHORITY")
    provenance = report["provenance"]["governed_counterfactual_evidence"]
    assert provenance["m5_authority"] == M5_CANDLE_AUTHORITY_IDENTITY
    assert provenance["dataset_id"] == artifact.dataset_id
    assert provenance["content_digest"] == artifact.content_digest
    assert provenance["admissible_rows"] == len(artifact.rows)


def test_counterfactual_producer_refuses_a_stale_candle_authority():
    """14/15. An authority from another window is refused, never repaired."""
    from research_engine.control_plane.governed_counterfactual_evidence import (
        CounterfactualEvidenceError, build_governed_counterfactual_evidence,
    )

    shadow_rows, candle_rows = _governed_lifecycles(1)
    start, end = _lifecycle_window(candle_rows)
    authority = freeze_governed_m5_candle_authority(
        candle_rows=candle_rows, shadow_runtime_rows=shadow_rows,
        snapshot_id=SNAPSHOT_ID, snapshot_fingerprint=SNAPSHOT_FINGERPRINT,
        investigation_epoch=INVESTIGATION_EPOCH,
        frontier_start=start, frontier_end=end,
        snapshot_authority={}, produced_at=PRODUCED_AT)
    binding = bind_m5_candle_authority(authority, bound_at=PRODUCED_AT)
    with pytest.raises(CounterfactualEvidenceError, match="STALE_FRONTIER"):
        build_governed_counterfactual_evidence(
            shadow_runtime_rows=shadow_rows,
            candle_authority=authority, candle_authority_binding=binding,
            snapshot_id=SNAPSHOT_ID, snapshot_fingerprint=SNAPSHOT_FINGERPRINT,
            investigation_epoch=INVESTIGATION_EPOCH,
            frontier_start="2026-01-01", frontier_end="2026-01-02",
            produced_at=PRODUCED_AT)
    # An authority without its governed membership is never admitted.
    with pytest.raises(CounterfactualEvidenceError,
                       match="MISSING_M5_CANDLE_AUTHORITY"):
        build_governed_counterfactual_evidence(
            shadow_runtime_rows=shadow_rows,
            candle_authority=authority,
            snapshot_id=SNAPSHOT_ID, snapshot_fingerprint=SNAPSHOT_FINGERPRINT,
            investigation_epoch=INVESTIGATION_EPOCH,
            frontier_start=start, frontier_end=end, produced_at=PRODUCED_AT)
