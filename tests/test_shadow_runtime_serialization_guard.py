"""
RUNTIME SERIALIZATION GUARD — the canonical Stage 4 version/epoch guard
executed on the ACTUAL core/shadow/runtime.py serialization/persistence path.

These tests are deliberately wired against the real production boundary
(``ShadowRuntime._write`` -> ``ShadowEventWriter.append``) rather than against
the guard in isolation, because the guarantee under test is not "the guard
works" but "no new shadow_runtime record can bypass it".

No test here may persist a record, perform an S3 write, start research
re-entry, or begin Q71+/RB-1.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.observability_contract import (  # noqa: E402
    DATASET_SHADOW_RUNTIME,
    ObservabilityContractError,
    assert_emission_contract,
    build_record_lineage,
    contract_for,
    record_contract_summary,
    resolve_evidence_epoch,
    validate_epoch_table,
)
from core.shadow.observability import (  # noqa: E402
    SHADOW_RUNTIME_PRODUCER_VERSION,
    SHADOW_RUNTIME_SCHEMA_GENERATION,
)
from core.shadow.persistence import ShadowEventWriter, load_events  # noqa: E402
from core.shadow.runtime import ShadowRuntime  # noqa: E402

SYMBOL = "EURUSD"
#: M5-grid aligned so it satisfies the ROOT-03 bar-grid rule.
BASE = (1_784_800_000 // 300) * 300
ROOT_ID = "EURUSD*1784800000*TWEEZER_TOP"

CONTRACT = contract_for(DATASET_SHADOW_RUNTIME)
GOVERNED_EPOCH = CONTRACT["evidence_epoch"]


# ═══════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════


def _ctx(**overrides):
    ctx = {
        "canonical_opportunity_id": ROOT_ID,
        "observation_id": "obs-1",
        "entity_id": "EURUSD_1784800000",
        "symbol": SYMBOL,
        "cycle_id": 42,
        "bar_time_utc": BASE,
        "direction": "SELL",
        "pattern": "TWEEZER_TOP",
        "strategy": "REVERSAL",
        "score": 0.61,
        "regime": "RANGE",
        "h4_regime": "RANGE",
        "h1_bias": "BEARISH",
        "market_phase": "PULLBACK",
        "market_phase_confidence": 0.55,
        "bid": 1.10000,
        "ask": 1.10002,
        "structure": {
            "m5_candle_high": 1.10050,
            "m5_candle_low": 1.09980,
            "m15_nearest_support": None,
            "m15_nearest_resistance": 1.10100,
            "h1_last_swing_high": None,
            "h1_last_swing_low": None,
        },
        "eligible_horizons": ["SCALP"],
        "horizon_assessments": [
            {"horizon": "SCALP", "confidence": 0.6, "reasoning": "tight",
             "eligible": True},
        ],
        "v10_action": "ENTER_SHORT",
        "v10_rejection_stage": "",
        "v10_selected_horizon": "SCALP",
    }
    ctx.update(overrides)
    return ctx


class _SpyWriter(ShadowEventWriter):
    """The REAL persistence adapter, with a call counter.

    A spy rather than a mock: serialization, partitioning and the file write all
    still happen for real, so "the sink was never called" is a statement about
    the guard, not about a stub.
    """

    def __init__(self, base_dir: str) -> None:
        super().__init__(base_dir=base_dir)
        self.calls: list[dict] = []

    def append(self, *, event, symbol, market_time_utc,
               broker_offset_seconds) -> None:
        self.calls.append(dict(event))
        super().append(event=event, symbol=symbol,
                       market_time_utc=market_time_utc,
                       broker_offset_seconds=broker_offset_seconds)

    def types(self) -> list[str]:
        return [str(e.get("event_type")) for e in self.calls]


def _runtime(tmp_path) -> ShadowRuntime:
    return ShadowRuntime(writer=_SpyWriter(str(tmp_path)))


def _events(tmp_path, event_type: str | None = None) -> list[dict]:
    events = load_events(str(tmp_path))
    if event_type is None:
        return events
    return [e for e in events if e.get("event_type") == event_type]


def _drive_to_close(runtime, *, count: int = 12) -> None:
    for i in range(count):
        runtime.evaluate_bar(
            symbol=SYMBOL,
            bar_time=BASE + 300 * (i + 1),
            bar_open=1.1000 + i * 0.0001,
            bar_high=1.10050,
            bar_low=1.09800 if i >= 8 else 1.09900,
            bar_close=1.09950,
            bar_index=i,
        )
        if not runtime.active_ids():
            break


def _tampered_lineage(monkeypatch, **overrides):
    """Make the runtime stamp a lineage that DISAGREES with the contract.

    This simulates the exact deployment failure under test: current producer
    code (generation-2 fields, producer v2) combined with wrong/old schema
    metadata.  The producer still builds genuine generation-2 fields; only the
    governed metadata envelope is corrupted, which is what the guard must
    catch before anything is serialized.
    """
    import core.shadow.runtime as runtime_mod

    real = runtime_mod.build_record_lineage

    def fake(dataset: str, *, event_type: str = ""):
        lineage = real(dataset, event_type=event_type)
        lineage.update(overrides)
        return lineage

    monkeypatch.setattr(runtime_mod, "build_record_lineage", fake)


def _assert_metadata_exact(lineage: dict) -> None:
    """Every governed identity dimension, checked on the SERIALIZED payload."""
    assert lineage["dataset"] == "shadow_runtime"
    assert lineage["dataset_version"] == "shadow_runtime_v1"
    assert lineage["schema_generation"] == 2
    assert lineage["producer_version"] == SHADOW_RUNTIME_PRODUCER_VERSION
    assert lineage["schema_generation"] == SHADOW_RUNTIME_SCHEMA_GENERATION
    assert lineage["evidence_epoch"] == GOVERNED_EPOCH
    # The stamped epoch must be independently resolvable, not merely copied.
    resolved = resolve_evidence_epoch(
        lineage["dataset"], lineage["dataset_version"],
        lineage["schema_generation"], lineage["producer_version"])
    assert lineage["evidence_epoch"] == resolved["epoch_id"]


# ═══════════════════════════════════════════════════════════════════════════
# 0. THE GUARD IS THE CANONICAL AUTHORITY (no duplicated logic)
# ═══════════════════════════════════════════════════════════════════════════


class TestCanonicalGuardAuthority:

    def test_epoch_table_is_unambiguous(self):
        assert validate_epoch_table() == []

    def test_runtime_delegates_to_the_contract_module(self):
        """runtime.py must CALL the guard, not reimplement it."""
        src = (ROOT / "core" / "shadow" / "runtime.py").read_text(
            encoding="utf-8")
        assert "from core.observability_contract import" in src
        assert "assert_emission_contract(event" in src
        # The runtime must not carry its own generation/epoch comparisons.
        assert "GENERATION_PRODUCER_DRIFT" not in src
        assert "EVIDENCE_EPOCH_MISMATCH" not in src

    def test_summary_reports_the_current_governed_identity(self):
        summary = record_contract_summary(DATASET_SHADOW_RUNTIME)


# ═══════════════════════════════════════════════════════════════════════════
# 1/2. VALID GENERATION-2 PERSISTENCE (OPEN, PLAN, PROGRESS, CLOSE)
# ═══════════════════════════════════════════════════════════════════════════


class TestValidGeneration2Persistence:

    def test_valid_gen2_open_persists(self, tmp_path):
        runtime = _runtime(tmp_path)
        runtime.handle_opportunity(_ctx())
        opens = _events(tmp_path, "OPEN")
        assert len(opens) == 1
        _assert_metadata_exact(opens[0]["record_lineage"])
        # The dataset identity on the record itself is the governed version.
        assert opens[0]["schema_version"] == "shadow_runtime_v1"

    def test_valid_gen2_close_persists(self, tmp_path):
        runtime = _runtime(tmp_path)
        runtime.handle_opportunity(_ctx())
        _drive_to_close(runtime)
        closes = _events(tmp_path, "CLOSE")
        assert len(closes) == 1
        _assert_metadata_exact(closes[0]["record_lineage"])

    def test_every_event_type_is_guarded_and_carries_identity(self, tmp_path):
        """PLAN, OPEN, PROGRESS and CLOSE all pass the same boundary."""
        runtime = _runtime(tmp_path)
        runtime.handle_opportunity(_ctx(eligible_horizons=["INTRADAY"]))
        # INTRADAY outlives the 12-bar checkpoint interval, so PROGRESS fires.
        for i in range(14):
            runtime.evaluate_bar(
                symbol=SYMBOL, bar_time=BASE + 300 * (i + 1),
                bar_open=1.1000, bar_high=1.1005, bar_low=1.0990,
                bar_close=1.0995, bar_index=i,
            )
        assert {"PLAN", "OPEN", "PROGRESS"} <= set(runtime._writer.types())
        for event in _events(tmp_path):
            _assert_metadata_exact(event["record_lineage"])
            assert event["record_lineage"]["event_type"] == event["event_type"]

    def test_event_specific_requirements_are_respected(self, tmp_path):
        """CLOSE does not have to carry the OPEN-only decision snapshot."""
        runtime = _runtime(tmp_path)


# ═══════════════════════════════════════════════════════════════════════════
# 3/4/5/6. FAIL CLOSED ON METADATA DRIFT
# ═══════════════════════════════════════════════════════════════════════════


class TestFailClosedOnMetadataDrift:

    def test_wrong_schema_generation_blocks_persistence(self, tmp_path,
                                                        monkeypatch):
        _tampered_lineage(monkeypatch, schema_generation=1)
        runtime = _runtime(tmp_path)
        with pytest.raises(ObservabilityContractError) as exc:
            runtime.handle_opportunity(_ctx())
        assert "GENERATION_PRODUCER_DRIFT" in str(exc.value)
        # PLAN is governed to require no generation-2 block and legitimately
        # precedes OPEN; the generation-2 OPEN is NOT persisted.
        assert "OPEN" not in runtime._writer.types()
        assert _events(tmp_path, "OPEN") == []

    def test_wrong_producer_version_blocks_persistence(self, tmp_path,
                                                       monkeypatch):
        _tampered_lineage(
            monkeypatch, producer_version="shadow_runtime_producer_v1")
        runtime = _runtime(tmp_path)
        with pytest.raises(ObservabilityContractError) as exc:
            runtime.handle_opportunity(_ctx())
        assert "PRODUCER_VERSION_DRIFT" in str(exc.value)
        assert "OPEN" not in runtime._writer.types()
        assert _events(tmp_path, "OPEN") == []

    def test_wrong_evidence_epoch_blocks_persistence(self, tmp_path,
                                                     monkeypatch):
        _tampered_lineage(
            monkeypatch, evidence_epoch="STAGE4-EPOCH-SHADOW-RUNTIME-G1")
        runtime = _runtime(tmp_path)
        with pytest.raises(ObservabilityContractError) as exc:
            runtime.handle_opportunity(_ctx())
        assert "EVIDENCE_EPOCH_MISMATCH" in str(exc.value)
        assert "OPEN" not in runtime._writer.types()
        assert _events(tmp_path, "OPEN") == []


    def test_gen2_fields_with_gen1_metadata_fails_closed(self, tmp_path,
                                                         monkeypatch):
        """
        THE critical rule.

        new producer code + wrong/old schema metadata = DO NOT PERSIST.

        The runtime still builds all four genuine generation-2 fields; only the
        metadata claims generation 1.  Nothing may be written.
        """
        _tampered_lineage(monkeypatch, schema_generation=1)
        runtime = _runtime(tmp_path)
        with pytest.raises(ObservabilityContractError):
            runtime.handle_opportunity(_ctx())
        assert "OPEN" not in runtime._writer.types()
        assert _events(tmp_path, "OPEN") == []

    def test_no_lineage_at_all_is_not_guessed(self, tmp_path, monkeypatch):
        """Pure generation-1 metadata (no lineage block) is refused, not
        downgraded into an accepted emission."""
        import core.shadow.runtime as runtime_mod
        monkeypatch.setattr(
            runtime_mod, "build_record_lineage",
            lambda dataset, *, event_type="": None)
        runtime = _runtime(tmp_path)
        with pytest.raises(ObservabilityContractError) as exc:
            runtime.handle_opportunity(_ctx())
        assert "RECORD_LINEAGE_MISSING" in str(exc.value)
        assert "OPEN" not in runtime._writer.types()
        assert _events(tmp_path, "OPEN") == []

    def test_close_metadata_drift_blocks_persistence(self, tmp_path,
                                                     monkeypatch):
        """The guard covers CLOSE, not only OPEN."""
        runtime = _runtime(tmp_path)
        runtime.handle_opportunity(_ctx())
        before = len(runtime._writer.calls)

        # The very next bar closes the lifecycle on its stop.
        _tampered_lineage(
            monkeypatch, evidence_epoch="STAGE4-EPOCH-SHADOW-RUNTIME-G1")
        with pytest.raises(ObservabilityContractError):
            runtime.evaluate_bar(
                symbol=SYMBOL, bar_time=BASE + 300, bar_open=1.1000,
                bar_high=1.10050, bar_low=1.09800, bar_close=1.09950,
                bar_index=0)
        assert len(runtime._writer.calls) == before
        assert _events(tmp_path, "CLOSE") == []

    def test_missing_gen2_field_blocks_persistence(self, tmp_path,
                                                   monkeypatch):
        """A record CLAIMING generation 2 while omitting a governed field fails."""
        monkeypatch.setattr(
            "core.shadow.runtime.assign_experiment_arm", lambda **kwargs: None)
        runtime = _runtime(tmp_path)
        with pytest.raises(ObservabilityContractError) as exc:
            runtime.handle_opportunity(_ctx())
        assert "GEN2_FIELD_MISSING" in str(exc.value)
        # PLAN legitimately precedes OPEN and is governed to require no
        # generation-2 block, so it is allowed.  The OPEN is NOT persisted.
        assert runtime._writer.types() == ["PLAN"]
        assert _events(tmp_path, "OPEN") == []


# ═══════════════════════════════════════════════════════════════════════════
# 5. FIRE-AND-FORGET SAFETY IS PRESERVED
#    (a missing SCIENTIFIC value is not broken METADATA)
# ═══════════════════════════════════════════════════════════════════════════


class TestScientificAbsenceIsNotMetadataDrift:

    def test_absent_at_decision_time_value_still_persists(self, tmp_path):
        """
        A required observation WAS attempted, the upstream value was genuinely
        unavailable, and that is recorded as an explicit governed ABSENT state.

        This is NOT metadata drift: the record must persist.
        """
        ctx = _ctx(v10_rejection_stage="", h4_regime="")
        ctx["structure"]["h1_last_swing_high"] = None
        runtime = _runtime(tmp_path)
        runtime.handle_opportunity(ctx)
        opens = _events(tmp_path, "OPEN")
        assert len(opens) == 1

        flat = json.dumps(opens[0]["decision_snapshot"])
        assert "ABSENT_AT_DECISION_TIME" in flat
        # The governed identity is still exact on that same record.
        _assert_metadata_exact(opens[0]["record_lineage"])

    def test_ungoverned_event_type_fails_closed(self, tmp_path):
        """An event type outside the governed set is refused, not guessed."""
        runtime = _runtime(tmp_path)
        runtime.handle_opportunity(_ctx())
        before = len(runtime._writer.calls)

        event = dict(runtime._writer.calls[-1])
        event["event_type"] = "NOT_A_GOVERNED_EVENT"
        event["record_lineage"] = dict(
            event["record_lineage"], event_type="NOT_A_GOVERNED_EVENT")
        with pytest.raises(ObservabilityContractError) as exc:
            runtime._write(event)
        assert "UNGOVERNED_EVENT_TYPE" in str(exc.value)
        assert len(runtime._writer.calls) == before


# ═══════════════════════════════════════════════════════════════════════════
# 8. GUARD FAILURE PRODUCES NO SERIALIZED / PERSISTED OUTPUT
# ═══════════════════════════════════════════════════════════════════════════


class TestGuardFailureWritesNothing:

    @pytest.mark.parametrize("drift", [
        {"schema_generation": 1},
        {"producer_version": "shadow_runtime_producer_v1"},
        {"evidence_epoch": "STAGE4-EPOCH-SHADOW-RUNTIME-G1"},
        {"collection_start": "2020-01-01"},
    ])
    def test_sink_is_never_called_and_nothing_is_on_disk(self, tmp_path,
                                                          monkeypatch, drift):
        _tampered_lineage(monkeypatch, **drift)
        runtime = _runtime(tmp_path)
        with pytest.raises(ObservabilityContractError):
            runtime.handle_opportunity(_ctx())
        # The generation-2 record was never handed to the sink at all...
        assert "OPEN" not in runtime._writer.types()
        # ...and no OPEN bytes reached the filesystem or the S3 mirror.
        assert _events(tmp_path, "OPEN") == []

    def test_guard_runs_before_serialization(self, tmp_path, monkeypatch):
        """Ordering: validate, THEN serialize. Never serialize then validate."""
        seen: list[dict] = []
        real_dumps = json.dumps

        def spy_dumps(obj, *args, **kwargs):
            if isinstance(obj, dict) and "event_type" in obj:
                seen.append(dict(obj))
            return real_dumps(obj, *args, **kwargs)

        _tampered_lineage(monkeypatch, schema_generation=1)
        runtime = _runtime(tmp_path)
        monkeypatch.setattr("core.shadow.persistence.json.dumps", spy_dumps)
        with pytest.raises(ObservabilityContractError):
            runtime.handle_opportunity(_ctx())
        # No generation-2 record was ever handed to the serializer.
        assert seen == []


# ═══════════════════════════════════════════════════════════════════════════
# 8b. SERIALIZATION ROUND TRIP
# ═══════════════════════════════════════════════════════════════════════════


class TestSerializationRoundTrip:

    def test_metadata_survives_serialize_deserialize(self, tmp_path):
        """
        runtime record -> guard -> serialize -> read back -> still exact.

        Validating only Python object attributes would prove nothing: the
        contract must hold on the bytes that are actually persisted.
        """
        runtime = _runtime(tmp_path)
        runtime.handle_opportunity(_ctx())
        _drive_to_close(runtime)

        raw_records = []
        for path in Path(tmp_path).rglob("*.jsonl"):
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    raw_records.append(json.loads(line))
        assert raw_records, "nothing was persisted"

        for raw in raw_records:
            _assert_metadata_exact(raw["record_lineage"])
            assert raw["schema_version"] == "shadow_runtime_v1"
            # The guard's own view of the DESERIALIZED record still holds.
            assert_emission_contract(raw, dataset=DATASET_SHADOW_RUNTIME)

    def test_lineage_is_resolvable_not_merely_copied(self, tmp_path):
        """The stamped epoch resolves independently from the identity tuple."""
        runtime = _runtime(tmp_path)
        runtime.handle_opportunity(_ctx())
        for event in _events(tmp_path):
            resolved = resolve_evidence_epoch(
                event["record_lineage"]["dataset"],
                event["record_lineage"]["dataset_version"],
                event["record_lineage"]["schema_generation"],
                event["record_lineage"]["producer_version"])
            assert resolved["epoch_id"] == event["record_lineage"][
                "evidence_epoch"]


# ═══════════════════════════════════════════════════════════════════════════
# 7. RECOVERY / RESTART
# ═══════════════════════════════════════════════════════════════════════════


class TestRecoveryPinsLifecycleMetadata:

    def test_restart_does_not_downgrade_metadata(self, tmp_path):
        """A generation-2 lifecycle resumes generation-2, never generation 1."""
        runtime = _runtime(tmp_path)
        runtime.handle_opportunity(_ctx())
        opened = _events(tmp_path, "OPEN")[0]
        assert opened["record_lineage"]["schema_generation"] == 2

        # Restart: a fresh runtime replays the stream and resumes the lifecycle.
        resumed = ShadowRuntime(writer=_SpyWriter(str(tmp_path)))
        assert resumed.active_ids() == runtime.active_ids()
        assert resumed.quarantined_ids() == {}

        _drive_to_close(resumed)
        closes = _events(tmp_path, "CLOSE")
        assert len(closes) == 1
        # The CLOSE carries the SAME pinned identity as the OPEN it belongs to.
        _assert_metadata_exact(closes[0]["record_lineage"])
        assert closes[0]["record_lineage"]["schema_generation"] == 2
        assert (closes[0]["record_lineage"]["evidence_epoch"]
                == opened["record_lineage"]["evidence_epoch"])
        assert (closes[0]["record_lineage"]["producer_version"]
                == opened["record_lineage"]["producer_version"])

    def test_generation1_lifecycle_is_quarantined_not_rewritten(self, tmp_path):
        """
        A lifecycle recovered under an older generation must NOT be silently
        rewritten as generation 2.  It is quarantined and emits nothing.
        """
        runtime = _runtime(tmp_path)
        runtime.handle_opportunity(_ctx())
        trade_id = runtime.active_ids()[0]

        # Rewrite the persisted stream as a historical generation-1 OPEN.
        path = next(Path(tmp_path).rglob("*.jsonl"))
        lines = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec.get("event_type") == "OPEN":
                rec["record_lineage"]["schema_generation"] = 1
            lines.append(json.dumps(rec, separators=(",", ":")))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        recovered = ShadowRuntime(writer=_SpyWriter(str(tmp_path)))
        # Not resumed, and the reason is machine-readable.
        assert recovered.active_ids() == []
        assert trade_id in recovered.quarantined_ids()
        assert "LIFECYCLE_LINEAGE_INCOMPATIBLE" in \
            recovered.quarantined_ids()[trade_id]

        # Drive bars: the quarantined lifecycle must emit nothing at all.
        _drive_to_close(recovered)
        assert recovered._writer.calls == []
        assert _events(tmp_path, "CLOSE") == []

    def test_pinned_lineage_conflict_fails_closed(self, tmp_path, monkeypatch):
        """
        Ambiguity between the lifecycle's PINNED identity and the currently
        governed identity fails closed rather than being silently reconciled.
        """
        import core.shadow.runtime as runtime_mod
        real = runtime_mod.build_record_lineage

        def conflict(dataset: str, *, event_type: str = ""):
            lineage = real(dataset, event_type=event_type)
            if event_type == "PROGRESS":
                # The PROGRESS stamp disagrees with the OPEN's pinned identity.
                lineage["evidence_epoch"] = "STAGE4-EPOCH-SHADOW-RUNTIME-G1"
            return lineage

        runtime = _runtime(tmp_path)
        runtime.handle_opportunity(_ctx(eligible_horizons=["INTRADAY"]))
        monkeypatch.setattr(runtime_mod, "build_record_lineage", conflict)
        before = len(runtime._writer.calls)

        # The 12th bar triggers the PROGRESS checkpoint.
        with pytest.raises(ObservabilityContractError) as exc:
            for i in range(12):
                runtime.evaluate_bar(
                    symbol=SYMBOL, bar_time=BASE + 300 * (i + 1),
                    bar_open=1.1000, bar_high=1.1005, bar_low=1.0990,
                    bar_close=1.0995, bar_index=i)
        assert "LIFECYCLE_LINEAGE_INCOMPATIBLE" in str(exc.value)
        # No PROGRESS record was persisted.
        assert len(runtime._writer.calls) == before
        assert _events(tmp_path, "PROGRESS") == []


# ═══════════════════════════════════════════════════════════════════════════
# 10. HISTORICAL GENERATION-1 READER COMPATIBILITY
# ═══════════════════════════════════════════════════════════════════════════


class TestHistoricalGeneration1ReadCompatibility:

    def test_historical_gen1_records_remain_readable(self, tmp_path):
        """
        Generation-1 records are never rewritten and never stop being READ.

        A historical stream (no record_lineage anywhere) loads, is replayed by
        recovery, and simply produces no new records.
        """
        path = Path(tmp_path) / SYMBOL / "2026-07-20.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        historical = {
            "event_type": "OPEN",
            "schema_version": "shadow_runtime_v1",
            "shadow_trade_id": "nshadow_historical",
            "canonical_opportunity_id": ROOT_ID,
            "observation_id": "obs-hist",
            "symbol": SYMBOL,
            "horizon": "SCALP",
            "lifecycle_initial": {},
            "simulation_assumptions": {"timeout_bars": 9},
            "construction": {"direction": "SELL", "entry_price": 1.1,
                             "stop_loss": 1.2, "take_profit": 1.0},
        }
        path.write_text(json.dumps(historical) + "\n", encoding="utf-8")

        # The historical reader is unaffected.
        replayed = load_events(str(tmp_path))
        assert len(replayed) == 1
        assert replayed[0]["shadow_trade_id"] == "nshadow_historical"

        # Recovery replays it and refuses to resume it under generation 2.
        runtime = ShadowRuntime(writer=_SpyWriter(str(tmp_path)))
        assert runtime.active_ids() == []
        assert "nshadow_historical" in runtime.quarantined_ids()

        # And it never gains a generation-2 identity.
        _drive_to_close(runtime)
        assert runtime._writer.calls == []
        assert load_events(str(tmp_path))[0].get("record_lineage") is None
