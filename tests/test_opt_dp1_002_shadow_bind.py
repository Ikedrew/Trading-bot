from __future__ import annotations

import ast
import copy
import json
from pathlib import Path

import pytest

from core.shadow.candidate_evaluation import CandidateEvaluation
from core.shadow.candidate_evaluation_persistence import CandidateEvaluationWriter
from core.shadow.candidate_models import TreatmentBar
from core.shadow.candidate_monitoring import CandidateMonitor
from core.shadow.candidate_persistence import CandidateEventWriter, load_candidate_events
from core.shadow.candidate_runtime import CandidateRegistration, CandidateRuntime
from core.shadow.integration import bind_candidate_from_shadow_open
from core.shadow.opt_dp1_002 import (
    ACTIVATION_FRONTIER_EPOCH_S,
    CANDIDATE_ID,
    FORWARD_PATH,
    OptDp1002TreatmentAdapter,
    POLICY,
    POLICY_ID,
    READINESS_CRITERIA,
    REGISTRY_PATH,
    TREATMENT_HASH,
    VALIDATION_PATH,
    register_opt_dp1_002,
    verify_binding_authority,
)
from research_engine.control_plane.exit_bar_path import ExitBar, ExitBarPathRecord
from research_engine.control_plane.exit_candidate_replay import _evaluate_outcome


def _event(arm="CANDIDATE", *, entry_time=None, shadow_id="shadow-1"):
    entry_time = entry_time or ACTIVATION_FRONTIER_EPOCH_S + 1
    return {
        "schema_version": "shadow_runtime_v1", "event_type": "OPEN",
        "event_id": f"open-{shadow_id}", "shadow_trade_id": shadow_id,
        "canonical_opportunity_id": "opportunity-1", "horizon": "SCALP",
        "symbol": "EURUSD", "entry_market_time_utc_epoch_s": entry_time,
        "construction": {"direction": "BUY", "entry_price": 100.0,
                         "stop_loss": 98.0, "take_profit": 104.0},
        "simulation_assumptions": {"timeout_bars": 9},
        "experiment_arm": {"experiment_id": "EXP-1", "experiment_arm": arm},
        "record_lineage": {"source": "test"},
    }


def _runtime(tmp_path):
    runtime = CandidateRuntime(CandidateEventWriter(str(tmp_path / "candidate")))
    assert register_opt_dp1_002(runtime)
    return runtime


def test_authoritative_candidate_policy_hash_and_lineage_match():
    authority = verify_binding_authority()
    assert authority["candidate"]["candidate_id"] == CANDIDATE_ID
    assert authority["candidate"]["status"] == "SHADOW_VALIDATION_ACTIVE"
    assert POLICY == {"policy_id": POLICY_ID, "policy_type": "TRAILING",
                      "activation_r": 0.25, "distance_r": 0.10}
    assert authority["binding"]["treatment_hash"] == TREATMENT_HASH
    assert authority["validation"]["comparison"]["validation_status"] == "VALIDATED"
    assert authority["forward"]["forward_validation_status"] == \
        "OPT_DP1_002_FORWARD_VALIDATED"
    assert [row["status"] for row in authority["candidate"]["status_history"]] == [
        "PROPOSED", "VALIDATED", "FORWARD_VALIDATED", "SHADOW_VALIDATION_ACTIVE"]
    assert authority["binding"]["live_approved"] is False


@pytest.mark.parametrize("target", ["candidate_id", "policy_id", "treatment_hash"])
def test_binding_identity_mismatch_fails_closed(tmp_path, monkeypatch, target):
    import core.shadow.opt_dp1_002 as binding
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    if target == "candidate_id":
        registry["candidates"][CANDIDATE_ID]["candidate_id"] = "wrong"
    elif target == "policy_id":
        registry["candidates"][CANDIDATE_ID]["changes"]["treatment"] = "wrong"
    else:
        registry["candidates"][CANDIDATE_ID]["shadow_binding"]["treatment_hash"] = "wrong"
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(registry), encoding="utf-8")
    monkeypatch.setattr(binding, "REGISTRY_PATH", path)
    with pytest.raises(RuntimeError, match="OPT_DP1_002_BINDING_AUTHORITY_INVALID"):
        binding.verify_binding_authority()


def test_not_forward_validated_cannot_register(tmp_path, monkeypatch):
    import core.shadow.opt_dp1_002 as binding
    forward = json.loads(FORWARD_PATH.read_text(encoding="utf-8"))
    forward["forward_validation_status"] = "INSUFFICIENT_DATA"
    path = tmp_path / "forward.json"
    path.write_text(json.dumps(forward), encoding="utf-8")
    monkeypatch.setattr(binding, "FORWARD_PATH", path)
    with pytest.raises(RuntimeError, match="FORWARD_VALIDATION_MISSING"):
        binding.register_opt_dp1_002(CandidateRuntime())


def test_duplicate_registration_is_one_and_conflict_fails(tmp_path):
    runtime = _runtime(tmp_path)
    assert register_opt_dp1_002(runtime)
    assert runtime.registered_keys() == [(CANDIDATE_ID, POLICY_ID)]
    conflict = CandidateRegistration(
        candidate_id=CANDIDATE_ID, policy_id=POLICY_ID,
        treatment_hash="wrong", adapter=OptDp1002TreatmentAdapter())
    with pytest.raises(ValueError, match="CANDIDATE_REGISTRATION_CONFLICT"):
        runtime.register(conflict)


def test_candidate_arm_only_frontier_dedup_and_account_fanout(tmp_path, monkeypatch):
    import core.shadow.candidate_runtime as runtime_module
    runtime = _runtime(tmp_path)
    monkeypatch.setattr(runtime_module, "_CANDIDATE_RUNTIME", runtime)
    bind_candidate_from_shadow_open(_event("CONTROL", shadow_id="control"))
    bind_candidate_from_shadow_open(_event(
        entry_time=ACTIVATION_FRONTIER_EPOCH_S - 1, shadow_id="historical"))
    assert runtime.active_ids() == []

    bind_candidate_from_shadow_open(_event())
    first = runtime.active_ids()
    assert len(first) == 1
    bind_candidate_from_shadow_open(_event())
    bind_candidate_from_shadow_open(_event(shadow_id="account-fanout"))
    assert runtime.active_ids() == first
    events = load_candidate_events(str(tmp_path / "candidate"))
    assert sum(row["event_type"] == "CANDIDATE_OPEN" for row in events) == 1


def test_restart_restores_state_and_does_not_reapply_old_bar(tmp_path):
    path = str(tmp_path / "candidate")
    first = CandidateRuntime(CandidateEventWriter(path))
    register_opt_dp1_002(first)
    event = _event()
    rid = first.bind_shadow_lifecycle(
        shadow_trade_id=event["shadow_trade_id"],
        canonical_opportunity_id=event["canonical_opportunity_id"],
        trade_horizon=event["horizon"], symbol=event["symbol"], direction="BUY",
        entry_time=event["entry_market_time_utc_epoch_s"], entry_price=100,
        stop_loss=98, take_profit=104, experiment_arm="CANDIDATE",
        treatment_context={"timeout_bars": 9}, candidate_id=CANDIDATE_ID,
        policy_id=POLICY_ID)
    first.evaluate_bar(symbol="EURUSD", bar_time=ACTIVATION_FRONTIER_EPOCH_S + 300,
                       bar_high=100.6, bar_low=99.9, bar_close=100.5)
    before = first.snapshot(rid)

    restarted = CandidateRuntime(CandidateEventWriter(path))
    register_opt_dp1_002(restarted)
    assert restarted.recover()["active"] == 1
    assert restarted.snapshot(rid)["treatment_state"] == before["treatment_state"]
    result = restarted.evaluate_bar(
        symbol="EURUSD", bar_time=ACTIVATION_FRONTIER_EPOCH_S + 300,
        bar_high=100.6, bar_low=99.9, bar_close=100.5)
    assert result[0]["status"] == "DUPLICATE_IGNORED"


def _offline_record(bars):
    return ExitBarPathRecord(
        schema_version="exit_bar_path_v1",
        lifecycle_identity=("shadow", "opportunity", "SCALP"),
        canonical_opportunity_id="opportunity", canonical_symbol="EURUSD",
        trade_horizon="SCALP", direction="BUY", entry_utc_epoch_s=1,
        exit_utc_epoch_s=999, entry_price=100.0, baseline_stop_loss=98.0,
        baseline_take_profit=104.0, baseline_timeout_bars=9,
        baseline_simulation_model_version="v1", observed_exit_reason="stop_loss",
        observed_exit_price=98.0, observed_bars_held=9,
        observed_pnl_r_multiple=-1.0, observed_mfe_r=0.0, timeframe="M5",
        ordered_m5_bars=tuple(bars), timestamp_semantics="closed",
        timestamp_version="v1", source_evidence_identity="source",
        lifecycle_source_digest="a" * 64, m5_source_digest="b" * 64,
        normalization_provenance_digest="c" * 64,
        analytical_digest="d" * 64,
    )


def test_runtime_adapter_exactly_matches_authoritative_offline_replay():
    bars = (
        ExitBar(300_000, 100.0, 100.6, 99.8, 100.5),
        ExitBar(600_000, 100.5, 101.0, 100.3, 100.4),
    )
    offline, errors = _evaluate_outcome(_offline_record(bars), POLICY)
    assert not errors and offline is not None
    adapter = OptDp1002TreatmentAdapter()
    geometry = {"entry_price": 100.0, "stop_loss": 98.0,
                "take_profit": 104.0, "risk_distance": 2.0,
                "timeout_bars": 9}
    state = adapter.initialize(entry_geometry=geometry)
    runtime_result = None
    activation_progression = []
    stop_progression = []
    for bar in bars:
        runtime_result = adapter.on_bar(
            entry_geometry=geometry, risk_distance=2.0, direction="BUY",
            bar=TreatmentBar("EURUSD", bar.timestamp_utc_ms // 1000,
                             bar.open, bar.high, bar.low, bar.close),
            prior_state=state)
        state = runtime_result.treatment_state
        activation_progression.append(state["activation_reached"])
        stop_progression.append(state["prior_effective_stop"])
        if runtime_result.terminal:
            break
    assert activation_progression == [True, True]
    assert stop_progression == [98.0, pytest.approx(100.4)]
    assert runtime_result is not None and runtime_result.terminal
    assert runtime_result.exit_reason == offline.exit_reason
    assert runtime_result.exit_price == pytest.approx(offline.exit_price)
    assert bars[len(activation_progression) - 1].timestamp_utc_ms // 1000 == \
        offline.exit_utc_epoch_s
    assert (runtime_result.exit_price - 100.0) / 2.0 == pytest.approx(offline.candidate_r)


def test_before_activation_original_stop_and_adverse_move_never_loosen():
    adapter = OptDp1002TreatmentAdapter()
    geometry = {"entry_price": 100.0, "stop_loss": 98.0,
                "take_profit": 104.0, "risk_distance": 2.0, "timeout_bars": 9}
    state = adapter.initialize(entry_geometry=geometry)
    first = adapter.on_bar(
        entry_geometry=geometry, risk_distance=2, direction="BUY",
        bar=TreatmentBar("EURUSD", 1, 100, 100.49, 99.5, 100.2),
        prior_state=state)
    assert not first.treatment_state["activation_reached"]
    assert first.treatment_state["prior_effective_stop"] == 98.0
    activated = adapter.on_bar(
        entry_geometry=geometry, risk_distance=2, direction="BUY",
        bar=TreatmentBar("EURUSD", 2, 100.2, 101.0, 100.0, 100.8),
        prior_state=first.treatment_state)
    adverse = adapter.on_bar(
        entry_geometry=geometry, risk_distance=2, direction="BUY",
        bar=TreatmentBar("EURUSD", 3, 100.8, 100.9, 100.81, 100.85),
        prior_state=activated.treatment_state)
    assert adverse.treatment_state["prior_effective_stop"] == pytest.approx(100.8)


def test_initial_monitoring_is_active_insufficient_and_has_zero_prospective_pairs(tmp_path):
    runtime = _runtime(tmp_path)
    evaluation = CandidateEvaluation(
        writer=CandidateEvaluationWriter(str(tmp_path / "evaluation")),
        baseline_dir=str(tmp_path / "baseline"),
        candidate_dir=str(tmp_path / "candidate"), candidate_runtime=runtime)
    report = CandidateMonitor(
        evaluation=evaluation, candidate_runtime=runtime,
        candidate_dir=str(tmp_path / "candidate")).report()["reports"][0]
    assert report["candidate_id"] == CANDIDATE_ID
    assert report["policy_id"] == POLICY_ID
    assert report["treatment_hash"] == TREATMENT_HASH
    assert report["metrics"]["paired_n"] == 0
    assert report["readiness"]["state"] == "SHADOW_VALIDATION_INSUFFICIENT_DATA"
    assert READINESS_CRITERIA["minimum_paired_sample"] == 300


def test_binding_has_no_execution_or_broker_path():
    paths = [Path("core/shadow/opt_dp1_002.py"),
             Path("core/shadow/frozen_trailing_policy.py")]
    forbidden = {"order_send", "position_modify", "position_close", "mt5"}
    for path in paths:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports = {alias.name.lower() for node in ast.walk(tree)
                   if isinstance(node, (ast.Import, ast.ImportFrom))
                   for alias in node.names}
        calls = {node.func.attr.lower() for node in ast.walk(tree)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
        assert not any(any(token in item for token in forbidden) for item in imports)
        assert not (calls & forbidden)
