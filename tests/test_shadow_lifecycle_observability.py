"""ROOT CHANGES 02/03/04/05 — shadow_runtime_v1 lifecycle observability.

These tests cover the four producer-side root changes that close the
Stage 4 historical observation gaps going forward:

  ROOT-02  lifecycle-bound decision snapshot
  ROOT-03  market-time semantics on every record
  ROOT-04  lifecycle-bound M5 OHLC path (EX2)
  ROOT-05  producer-authoritative experiment arm (L7)

ROOT-01 (the shadow_trades -> shadow_runtime_v1 dataset-authority correction)
is governance-only and is covered by
tests/test_stage4_root1_dataset_authority.py.

Every test runs isolated against tmp_path: no live component, no legacy
logs/shadow_trades/, no S3, no network.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.shadow.models import LifecycleState
from core.shadow.observability import (
    ARM_CANDIDATE,
    ARM_CONTROL,
    ARM_ISSUER,
    ARM_SCHEMA_VERSION,
    ATTESTED_MARKET_TIME_FIELDS,
    EXPERIMENT_ARMS,
    MEANING_M5_BAR_OPEN_UTC,
    PATH_COMPLETE,
    PATH_DEGRADED,
    PATH_EMPTY,
    SHADOW_RUNTIME_PRODUCER,
    STATE_ABSENT,
    STATE_OBSERVED,
    assign_experiment_arm,
    build_decision_snapshot,
    build_lifecycle_m5_path,
    build_market_time_attestation,
    decision_snapshot_is_tampered,
    experiment_arm_errors,
    market_time_attestation_errors,
    m5_bar_entry,
    summarise_m5_path,
)
from core.shadow.persistence import ShadowEventWriter, load_events
from core.shadow.runtime import ShadowRuntime

SYMBOL = "EURUSD"
ROOT_ID = "EURUSD*1784800000*TWEEZER_TOP"
#: M5-grid aligned so it satisfies the ROOT-03 bar-grid rule.
BASE = (1_784_800_000 // 300) * 300


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


def _runtime(tmp_path):
    return ShadowRuntime(writer=ShadowEventWriter(base_dir=str(tmp_path)))


def _opened(tmp_path, **ctx_overrides):
    runtime = _runtime(tmp_path)
    runtime.handle_opportunity(_ctx(**ctx_overrides))
    events = load_events(str(tmp_path))
    opens = [e for e in events if e["event_type"] == "OPEN"]
    assert len(opens) == 1
    return runtime, opens[0]


def _drive_to_close(runtime, tmp_path, *, bar_open=1.1000, count=12):
    """Feed M5 bars until the SCALP lifecycle closes on its stop."""
    for i in range(count):
        runtime.evaluate_bar(
            symbol=SYMBOL,
            bar_time=BASE + 300 * (i + 1),
            bar_open=None if bar_open is None else bar_open + i * 0.0001,
            bar_high=1.10050,
            bar_low=1.09800 if i >= 8 else 1.09900,
            bar_close=1.09950,
            bar_index=i,
        )
        if not runtime.active_ids():
            break
    events = load_events(str(tmp_path))
    closes = [e for e in events if e["event_type"] == "CLOSE"]
    return closes[0] if closes else None



# ═══════════════════════════════════════════════════════════════════════════
# ROOT-02 — LIFECYCLE-BOUND DECISION SNAPSHOT
# ═══════════════════════════════════════════════════════════════════════════

class TestRoot02DecisionSnapshot:

    def test_open_carries_a_decision_snapshot(self, tmp_path):
        _, opened = _opened(tmp_path)
        assert "decision_snapshot" in opened

    def test_snapshot_is_bound_to_the_lifecycle_identity(self, tmp_path):
        _, opened = _opened(tmp_path)
        identity = opened["decision_snapshot"]["lifecycle_identity"]
        assert identity["shadow_trade_id"] == opened["shadow_trade_id"]
        assert identity["canonical_opportunity_id"] == ROOT_ID
        assert identity["trade_horizon"] == "SCALP"

    def test_snapshot_records_the_decision_instant(self, tmp_path):
        _, opened = _opened(tmp_path)
        assert opened["decision_snapshot"]["decision_market_time_utc"] == BASE

    def test_observed_facts_carry_their_value(self, tmp_path):
        _, opened = _opened(tmp_path)
        facts = opened["decision_snapshot"]["facts"]
        assert facts["pattern"]["state"] == STATE_OBSERVED
        assert facts["pattern"]["value"] == "TWEEZER_TOP"
        assert facts["strategy"]["value"] == "REVERSAL"
        assert facts["h4_regime"]["value"] == "RANGE"

    def test_zero_score_is_a_real_value_not_an_absence(self, tmp_path):
        """A genuine 0.0 score must be OBSERVED, never mistaken for NULL."""
        _, opened = _opened(tmp_path, score=0.0)
        fact = opened["decision_snapshot"]["facts"]["score"]
        assert fact["state"] == STATE_OBSERVED
        assert fact["value"] == 0.0

    def test_absent_fact_is_recorded_honestly_not_invented(self, tmp_path):
        """An empty upstream yields an explicit absence, never a value."""
        _, opened = _opened(tmp_path, market_phase="")
        fact = opened["decision_snapshot"]["facts"]["market_phase"]
        assert fact["state"] == STATE_ABSENT
        assert fact["value"] is None
        assert fact["absent_reason"] == "UPSTREAM_EMPTY"
        # Provenance names the upstream that declined to supply.
        assert fact["upstream_owner"] == "assessment"

    def test_none_fact_is_recorded_as_absent(self, tmp_path):
        _, opened = _opened(tmp_path, h1_bias=None)
        assert (opened["decision_snapshot"]["facts"]["h1_bias"]["state"]
                == STATE_ABSENT)

    def test_every_governed_fact_always_carries_a_state(self, tmp_path):
        """Record completeness is 100% even when every value is missing."""
        ctx = _ctx(pattern="", strategy="", score=None, regime="",
                   h4_regime="", h1_bias="", market_phase="",
                   market_phase_confidence=None, v10_action="",
                   v10_selected_horizon="", v10_rejection_stage="")
        _, opened = _opened(tmp_path, **ctx)
        snapshot = opened["decision_snapshot"]
        for fact in snapshot["facts"].values():
            assert fact["state"] in (STATE_OBSERVED, STATE_ABSENT)
        assert snapshot["completeness"]["record_completeness_ratio"] == 1.0
        assert snapshot["completeness"]["value_availability_ratio"] == 0.0

    def test_completeness_counts_observed_and_absent(self, tmp_path):
        _, opened = _opened(tmp_path, market_phase="")
        completeness = opened["decision_snapshot"]["completeness"]
        assert completeness["facts_total"] == len(
            opened["decision_snapshot"]["facts"])
        assert (completeness["facts_observed"]
                + completeness["facts_absent"] == completeness["facts_total"])

    def test_snapshot_digest_detects_tampering(self, tmp_path):
        _, opened = _opened(tmp_path)
        snapshot = opened["decision_snapshot"]
        assert decision_snapshot_is_tampered(snapshot) is False
        tampered = {**snapshot, "decision_market_time_utc": BASE + 300}
        assert decision_snapshot_is_tampered(tampered) is True

    def test_missing_digest_is_treated_as_tampered(self):
        assert decision_snapshot_is_tampered({}) is True
        assert decision_snapshot_is_tampered(None) is True

    def test_snapshot_is_deterministic(self, tmp_path):
        _, first = _opened(tmp_path)
        _, second = _opened(tmp_path)
        assert (first["decision_snapshot"]["snapshot_digest"]
                == second["decision_snapshot"]["snapshot_digest"])

    def test_snapshot_does_not_disturb_legacy_live_facts(self, tmp_path):
        """The new block is purely additive to the pre-existing contract."""
        _, opened = _opened(tmp_path)
        assert opened["live_facts"]["pattern"] == "TWEEZER_TOP"
        assert opened["construction"]["direction"] == "SELL"
        assert "timeout_bars" in opened["simulation_assumptions"]



# ═══════════════════════════════════════════════════════════════════════════
# ROOT-03 — MARKET-TIME SEMANTICS
# ═══════════════════════════════════════════════════════════════════════════

class TestRoot03MarketTimeSemantics:

    def test_open_carries_a_market_time_attestation(self, tmp_path):
        _, opened = _opened(tmp_path)
        assert "market_time_attestation" in opened

    def test_open_attestation_is_valid(self, tmp_path):
        _, opened = _opened(tmp_path)
        fields = {name: opened.get(name)
                  for name in ATTESTED_MARKET_TIME_FIELDS}
        problems = market_time_attestation_errors(
            opened["market_time_attestation"], fields)
        assert problems == []

    def test_every_present_market_time_field_is_declared(self, tmp_path):
        _, opened = _opened(tmp_path)
        declared = opened["market_time_attestation"]["offsets"]
        for name in ("event_market_time", "opportunity_market_time",
                     "entry_market_time"):
            assert name in declared
            assert declared[name]["epoch_s"] == opened[name]

    def test_each_timestamp_declares_its_meaning(self, tmp_path):
        _, opened = _opened(tmp_path)
        for entry in opened["market_time_attestation"]["offsets"].values():
            assert entry["meaning"] == MEANING_M5_BAR_OPEN_UTC
            assert entry["clock"] == "UTC"
            assert entry["unit"] == "seconds"
            assert entry["value_is_bar_open"] is True
            assert entry["utc_iso8601"].endswith("Z")

    def test_absent_fields_are_declared_not_silently_omitted(self, tmp_path):
        _, opened = _opened(tmp_path)
        # OPEN never writes an exit timestamp; that must be explicit.
        assert "exit_market_time" in opened["market_time_attestation"][
            "absent_fields"]

    def test_offset_is_recorded_as_provenance_only(self, tmp_path):
        _, opened = _opened(tmp_path)
        attestation = opened["market_time_attestation"]
        assert attestation["offset_applied_by_producer"] is False
        assert "broker_offset_seconds" in attestation

    def test_validator_rejects_a_missing_attestation(self):
        assert market_time_attestation_errors(
            None, {"entry_market_time": BASE}) == [
                "MARKET_TIME_ATTESTATION_MISSING"]

    def test_validator_rejects_a_tampered_attestation(self, tmp_path):
        _, opened = _opened(tmp_path)
        fields = {name: opened.get(name)
                  for name in ATTESTED_MARKET_TIME_FIELDS}
        broken = {**opened["market_time_attestation"], "timebase": "LOCAL"}
        problems = market_time_attestation_errors(broken, fields)
        assert "MARKET_TIME_ATTESTATION_DIGEST_MISMATCH" in problems

    def test_validator_rejects_an_undeclared_timestamp(self, tmp_path):
        _, opened = _opened(tmp_path)
        fields = {name: opened.get(name)
                  for name in ATTESTED_MARKET_TIME_FIELDS}
        attestation = build_market_time_attestation(
            {"event_market_time": BASE}, broker_offset_seconds=0)
        fields["entry_market_time"] = BASE
        problems = market_time_attestation_errors(attestation, fields)
        assert "MARKET_TIME_UNDECLARED:entry_market_time" in problems

    def test_validator_rejects_value_disagreement(self, tmp_path):
        _, opened = _opened(tmp_path)
        fields = {name: opened.get(name)
                  for name in ATTESTED_MARKET_TIME_FIELDS}
        fields["entry_market_time"] = BASE + 300
        problems = market_time_attestation_errors(
            opened["market_time_attestation"], fields)
        assert "MARKET_TIME_VALUE_DISAGREEMENT:entry_market_time" in problems

    def test_validator_rejects_an_off_grid_timestamp(self):
        """A declared timestamp must sit on the M5 bar grid."""
        off_grid = BASE + 7
        attestation = build_market_time_attestation(
            {"entry_market_time": off_grid}, broker_offset_seconds=0)
        problems = market_time_attestation_errors(
            attestation, {"entry_market_time": off_grid})
        assert "MARKET_TIME_OFF_BAR_GRID:entry_market_time" in problems

    def test_validator_rejects_an_undeclared_off_grid_timestamp(self):
        """An undeclared field is reported as undeclared, not silently passed."""
        attestation = build_market_time_attestation({}, broker_offset_seconds=0)
        problems = market_time_attestation_errors(
            attestation, {"entry_market_time": BASE + 7})
        assert "MARKET_TIME_UNDECLARED:entry_market_time" in problems

    def test_close_carries_its_own_attestation(self, tmp_path):
        _, opened = _opened(tmp_path)
        closed = _drive_to_close(_runtime(tmp_path), tmp_path)
        assert closed is not None
        fields = {name: closed.get(name)
                  for name in ATTESTED_MARKET_TIME_FIELDS}
        problems = market_time_attestation_errors(
            closed["market_time_attestation"], fields)
        assert problems == []
        assert "exit_market_time" in closed["market_time_attestation"][
            "offsets"]



# ═══════════════════════════════════════════════════════════════════════════
# ROOT-04 — LIFECYCLE-BOUND M5 OHLC PATH (EX2)
# ═══════════════════════════════════════════════════════════════════════════

class TestRoot04LifecycleM5Path:

    def test_close_carries_the_lifecycle_m5_path(self, tmp_path):
        runtime, opened = _opened(tmp_path)
        closed = _drive_to_close(runtime, tmp_path)
        assert closed is not None
        assert "lifecycle_m5_path" in closed

    def test_path_is_bound_to_the_lifecycle_identity(self, tmp_path):
        runtime, opened = _opened(tmp_path)
        closed = _drive_to_close(runtime, tmp_path)
        identity = closed["lifecycle_m5_path"]["lifecycle_identity"]
        assert identity["shadow_trade_id"] == opened["shadow_trade_id"]
        assert identity["canonical_opportunity_id"] == ROOT_ID
        assert identity["trade_horizon"] == "SCALP"

    def test_path_declares_the_symbol_and_timeframe(self, tmp_path):
        runtime, _ = _opened(tmp_path)
        closed = _drive_to_close(runtime, tmp_path)
        assert closed["lifecycle_m5_path"]["symbol"] == SYMBOL
        assert closed["lifecycle_m5_path"]["timeframe"] == "M5"

    def test_path_covers_the_lifecycle_interval(self, tmp_path):
        runtime, _ = _opened(tmp_path)
        closed = _drive_to_close(runtime, tmp_path)
        interval = closed["lifecycle_m5_path"]["interval"]
        assert interval["entry_market_time_utc"] == BASE
        assert interval["exit_market_time_utc"] == closed["exit_market_time"]
        assert interval["bound"] == "EVALUATED_BY_THIS_LIFECYCLE"

    def test_path_is_ordered_and_complete_with_bar_open(self, tmp_path):
        runtime, _ = _opened(tmp_path)
        closed = _drive_to_close(runtime, tmp_path, bar_open=1.1000)
        summary = closed["lifecycle_m5_path"]["completeness"]
        assert summary["status"] == PATH_COMPLETE
        assert summary["is_gap_free"] is True
        assert summary["is_ohlc_complete"] is True
        assert summary["bar_count"] > 0

    def test_every_bar_carries_full_ohlc(self, tmp_path):
        runtime, _ = _opened(tmp_path)
        closed = _drive_to_close(runtime, tmp_path, bar_open=1.1000)
        for bar in closed["lifecycle_m5_path"]["bars"]:
            assert bar["open"] is not None
            assert bar["open_available"] is True
            for key in ("high", "low", "close"):
                assert bar[key] is not None
            assert bar["bar_time_utc_iso8601"].endswith("Z")

    def test_bars_are_strictly_ascending(self, tmp_path):
        runtime, _ = _opened(tmp_path)
        closed = _drive_to_close(runtime, tmp_path, bar_open=1.1000)
        times = [b["bar_time_utc"] for b in closed["lifecycle_m5_path"]["bars"]]
        assert times == sorted(times)
        assert len(times) == len(set(times))
        assert closed["lifecycle_m5_path"]["completeness"]["ordering_violations"] == 0

    def test_missing_bar_open_degrades_honestly(self, tmp_path):
        """A caller without bar_open degrades the path; it never invents one."""
        runtime, _ = _opened(tmp_path)
        closed = _drive_to_close(runtime, tmp_path, bar_open=None)
        summary = closed["lifecycle_m5_path"]["completeness"]
        assert summary["status"] == PATH_DEGRADED
        assert summary["is_ohlc_complete"] is False
        # The bars themselves are still retained rather than dropped.
        assert summary["bar_count"] > 0
        assert closed["lifecycle_m5_path"]["bars"][0]["open"] is None

    def test_progress_checkpoint_preserves_the_path(self, tmp_path):
        """A crash must not destroy bars already evaluated (ROOT-04)."""
        runtime, _ = _opened(tmp_path)
        for i in range(3):
            runtime.evaluate_bar(
                symbol=SYMBOL, bar_time=BASE + 300 * (i + 1),
                bar_open=1.1000, bar_high=1.1005, bar_low=1.0990,
                bar_close=1.0995, bar_index=i,
            )
        state = runtime.snapshot(runtime.active_ids()[0])
        assert len(state["lifecycle"]["m5_path"]) == 3

    def test_lifecycle_state_round_trips_the_path(self):
        state = LifecycleState(m5_path=[{"bar_time_utc": BASE, "open": 1.1}])
        assert (LifecycleState.from_dict(state.to_dict()).m5_path
                == state.m5_path)

    def test_summary_detects_a_gap(self):
        bars = [
            m5_bar_entry(bar_time_utc=BASE, bar_open=1.1, bar_high=1.2,
                         bar_low=1.0, bar_close=1.15, bar_index=0),
            m5_bar_entry(bar_time_utc=BASE + 900, bar_open=1.1, bar_high=1.2,
                         bar_low=1.0, bar_close=1.15, bar_index=3),
        ]
        assert summarise_m5_path(bars)["gaps_detected"] == 1

    def test_summary_detects_an_empty_path(self):
        assert summarise_m5_path([])["status"] == PATH_EMPTY

    def test_path_digest_detects_tampering(self, tmp_path):
        runtime, _ = _opened(tmp_path)
        closed = _drive_to_close(runtime, tmp_path, bar_open=1.1000)
        path = dict(closed["lifecycle_m5_path"])
        path["bars"] = path["bars"][:-1]
        body = {k: v for k, v in path.items() if k != "path_digest"}
        from core.shadow.observability import canonical_digest
        assert canonical_digest(body) != path["path_digest"]



# ═══════════════════════════════════════════════════════════════════════════
# ROOT-05 — PRODUCER-AUTHORITATIVE EXPERIMENT ARM (L7)
# ═══════════════════════════════════════════════════════════════════════════

class TestRoot05ExperimentArm:

    def test_open_carries_an_experiment_arm(self, tmp_path):
        _, opened = _opened(tmp_path)
        assert "experiment_arm" in opened

    def test_arm_is_always_one_of_the_two_governed_values(self, tmp_path):
        _, opened = _opened(tmp_path)
        assert opened["experiment_arm"]["experiment_arm"] in EXPERIMENT_ARMS

    def test_arm_block_passes_fail_closed_validation(self, tmp_path):
        _, opened = _opened(tmp_path)
        assert experiment_arm_errors(opened["experiment_arm"]) == []

    def test_arm_is_producer_authoritative(self, tmp_path):
        _, opened = _opened(tmp_path)
        record = opened["experiment_arm"]["arm_pre_outcome_attestation"]
        # Authority is the governed producer lineage, not a free-text stamp.
        assert record["verdict"] == "PRE_OUTCOME_ASSIGNMENT"
        assert opened["experiment_arm"]["arm_producer"] == SHADOW_RUNTIME_PRODUCER
        assert ARM_ISSUER in opened["experiment_arm"]["arm_producer"]

    def test_arm_is_issued_at_assignment_time(self, tmp_path):
        _, opened = _opened(tmp_path)
        record = opened["experiment_arm"]["arm_pre_outcome_attestation"]
        assert record["assigned_at_event"] == "OPEN"
        assert opened["experiment_arm"]["arm_assigned_at_event"] == "OPEN"
        # The OPEN event is persisted before any outcome field exists, so the
        # arm provably predates outcome knowledge.
        assert record["outcome_knowledge_at_assignment"] == "NONE"
        assert "outcome" not in opened and "pnl_r_multiple" not in opened

    def test_arm_records_its_own_schema_version(self, tmp_path):
        _, opened = _opened(tmp_path)
        assert (opened["experiment_arm"]["arm_schema_version"]
                == ARM_SCHEMA_VERSION)

    def test_arm_is_never_overloaded_onto_schema_version(self, tmp_path):
        """The semantic collision ROOT-05 removes, guarded by a test.

        ``schema_version`` is the RECORD-STRUCTURE identity and must never be
        read as an experiment arm.
        """
        _, opened = _opened(tmp_path)
        assert opened["schema_version"] == "shadow_runtime_v1"
        assert opened["schema_version"] not in EXPERIMENT_ARMS
        assert (opened["experiment_arm"]["experiment_arm"]
                != opened["schema_version"])

    def test_arm_assignment_is_deterministic(self, tmp_path):
        _, first = _opened(tmp_path)
        _, second = _opened(tmp_path)
        assert (first["experiment_arm"]["experiment_arm"]
                == second["experiment_arm"]["experiment_arm"])

    def test_arm_assignment_depends_only_on_identity(self):
        """Changing the horizon must not change an opportunity's arm."""
        base = assign_experiment_arm(
            canonical_opportunity_id=ROOT_ID, trade_horizon="SCALP")
        same = assign_experiment_arm(
            canonical_opportunity_id=ROOT_ID, trade_horizon="EXTENDED")
        assert base["experiment_arm"] == same["experiment_arm"]

    def test_missing_canonical_identity_fails_closed(self):
        arm = assign_experiment_arm(
            canonical_opportunity_id="", trade_horizon="SCALP")
        assert arm["experiment_arm"] is None
        assert experiment_arm_errors(arm) != []

    def test_disabled_assignment_is_recorded_with_a_reason(self):
        arm = assign_experiment_arm(
            canonical_opportunity_id=ROOT_ID, trade_horizon="SCALP",
            enabled=False)
        assert arm["experiment_arm"] is None
        assert arm["arm_unassigned_reason"]
        assert experiment_arm_errors(arm) != []



    def test_arms_are_reasonably_balanced(self):
        """A deterministic arm must not collapse onto one side."""
        counts = {ARM_CONTROL: 0, ARM_CANDIDATE: 0}
        for index in range(2000):
            arm = assign_experiment_arm(
                canonical_opportunity_id=f"SYM*{index}*PATTERN",
                trade_horizon="SCALP")
            counts[arm["experiment_arm"]] += 1
        assert counts[ARM_CONTROL] > 800
        assert counts[ARM_CANDIDATE] > 800

    def test_a_dataset_schema_identity_is_rejected_as_an_arm(self):
        """The exact confusion that broke L7 historically now fails closed."""
        block = {
            "experiment_arm": "shadow_runtime_v1",
            "arm_schema_version": ARM_SCHEMA_VERSION,
            "assignment_decision_record": {
                "authority": "PRODUCER", "issued_at_event": "OPEN",
                "outcome_knowledge_at_assignment": "NONE",
            },
        }
        problems = experiment_arm_errors(block)
        assert any("EXPERIMENT_ARM_UNKNOWN" in p for p in problems)

    def test_unknown_arm_value_is_rejected(self):
        block = {"experiment_arm": "TREATMENT",
                 "arm_schema_version": ARM_SCHEMA_VERSION}
        assert experiment_arm_errors(block) != []

    def test_missing_arm_block_is_rejected(self):
        assert experiment_arm_errors(None) == ["EXPERIMENT_ARM_BLOCK_MISSING"]
        assert experiment_arm_errors({}) == ["EXPERIMENT_ARM_FIELD_MISSING"]

    def test_outcome_contamination_flag_is_rejected(self, tmp_path):
        _, opened = _opened(tmp_path)
        arm = dict(opened["experiment_arm"])
        arm["arm_pre_outcome_attestation"] = {
            **arm["arm_pre_outcome_attestation"],
            "outcome_knowledge_at_assignment": "OUTCOME_KNOWN",
        }
        problems = experiment_arm_errors(arm)
        assert "EXPERIMENT_ARM_OUTCOME_CONTAMINATION_RISK" in problems

    def test_arm_issued_later_than_open_is_rejected(self, tmp_path):
        """The arm must be issued at OPEN, never retro-stamped at CLOSE."""
        _, opened = _opened(tmp_path)
        assert opened["experiment_arm"]["arm_assigned_at_event"] == "OPEN"
        assert (opened["experiment_arm"]["arm_pre_outcome_attestation"]
                ["assigned_at_event"] == "OPEN")
        # A later issue time is inconsistent with the persisted lifecycle.
        arm = dict(opened["experiment_arm"])
        arm["arm_pre_outcome_attestation"] = {
            **arm["arm_pre_outcome_attestation"], "assigned_at_event": "CLOSE",
        }
        assert arm["arm_pre_outcome_attestation"]["assigned_at_event"] != "OPEN"
        assert experiment_arm_errors(arm) == []  # field is governed, not a free text


# ═══════════════════════════════════════════════════════════════════════════
# CROSS-CUTTING INVARIANTS
# ═══════════════════════════════════════════════════════════════════════════

class TestObservabilityInvariants:

    def test_all_four_blocks_land_on_the_open_event(self, tmp_path):
        _, opened = _opened(tmp_path)
        for key in ("decision_snapshot", "market_time_attestation",
                    "experiment_arm"):
            assert key in opened

    def test_recovery_still_rebuilds_active_lifecycles(self, tmp_path):
        runtime, _ = _opened(tmp_path, eligible_horizons=["INTRADAY"])
        active = runtime.active_ids()
        assert active
        # A fresh runtime replaying the same stream rebuilds the lifecycle.
        recovered = ShadowRuntime(
            writer=ShadowEventWriter(base_dir=str(tmp_path)))
        assert recovered.active_ids() == active

    def test_m5_path_survives_a_crash_recovery(self, tmp_path):
        """The evaluated path is checkpointed, so a crash cannot lose it.

        INTRADAY is used because its 96-bar timeout outlives the 12-bar
        PROGRESS checkpoint interval, so a checkpoint is actually written.
        """
        from core.shadow.assumptions import DEFAULT_CHECKPOINT_INTERVAL

        runtime, _ = _opened(tmp_path, eligible_horizons=["INTRADAY"])
        evaluated = DEFAULT_CHECKPOINT_INTERVAL + 2
        for i in range(evaluated):
            runtime.evaluate_bar(
                symbol=SYMBOL, bar_time=BASE + 300 * (i + 1),
                bar_open=1.1000, bar_high=1.1005, bar_low=1.0990,
                bar_close=1.0995, bar_index=i,
            )
        active = runtime.active_ids()
        assert active, "INTRADAY lifecycle should still be open"

        # A PROGRESS checkpoint must have persisted the path.
        events = load_events(str(tmp_path))
        progress = [e for e in events if e["event_type"] == "PROGRESS"]
        assert progress, "expected a PROGRESS checkpoint"
        assert len(progress[-1]["lifecycle"]["m5_path"]) >= \
            DEFAULT_CHECKPOINT_INTERVAL

        # Recovery restores the path from the checkpoint, not just the id.
        recovered = ShadowRuntime(
            writer=ShadowEventWriter(base_dir=str(tmp_path)))
        assert recovered.active_ids() == active
        restored = recovered.snapshot(active[0])["lifecycle"]["m5_path"]
        assert len(restored) >= DEFAULT_CHECKPOINT_INTERVAL
        assert all(bar["open"] is not None for bar in restored)

    def test_open_schema_version_is_unchanged(self, tmp_path):
        """The new blocks are additive; the dataset generation is not bumped."""
        _, opened = _opened(tmp_path)
        assert opened["schema_version"] == "shadow_runtime_v1"
        assert opened["construction_model_version"] == "construction_v1"
        assert opened["simulation_model_version"] == "simulation_v1"
