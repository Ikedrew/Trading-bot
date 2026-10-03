from __future__ import annotations

import sys
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.shadow.candidate_evaluation import CandidateEvaluation
from core.shadow.candidate_evaluation_persistence import (
    CandidateEvaluationWriter,
)
from core.shadow.candidate_monitoring import CandidateMonitor
from core.shadow.candidate_persistence import CandidateEventWriter
from core.shadow.candidate_runtime import CandidateRegistration, CandidateRuntime
from core.shadow.integration import bind_candidate_from_shadow_open
from tests.helpers.fake_candidate_adapter import FakeBarCountAdapter


def _runtime(tmp_path, *, criteria=None, minimum_sample=None, adapter=None):
    candidate_dir = str(tmp_path / "candidate")
    runtime = CandidateRuntime(writer=CandidateEventWriter(candidate_dir))
    runtime.register(CandidateRegistration(
        candidate_id=FakeBarCountAdapter.CANDIDATE_ID,
        policy_id=FakeBarCountAdapter.POLICY_ID,
        treatment_hash=FakeBarCountAdapter.TREATMENT_HASH,
        adapter=adapter or FakeBarCountAdapter(),
        readiness_criteria=criteria,
        minimum_sample_requirement=minimum_sample,
    ))
    return runtime, candidate_dir


def _baseline_open(tid="shadow-1", canonical="canonical-1", horizon="SCALP"):
    return {
        "event_id": f"{tid}:OPEN",
        "event_type": "OPEN",
        "schema_version": "shadow_runtime_v1",
        "symbol": "EURUSD",
        "shadow_trade_id": tid,
        "canonical_opportunity_id": canonical,
        "horizon": horizon,
        "entry_market_time_utc_epoch_s": 1700000000,
        "construction": {
            "direction": "BUY",
            "entry_price": 1.1,
            "stop_loss": 1.099,
            "take_profit": 1.102,
        },
        "experiment_arm": {
            "experiment_id": "TEST_EXPERIMENT",
            "experiment_arm": "CANDIDATE",
            "assignment_id": "arm-test-1",
            "arm_assignment_method": "TEST_FIXTURE",
        },
        "record_lineage": {"dataset": "shadow_runtime"},
    }


def _baseline_close(open_event, r=0.5, time=1700000300):
    return {
        "event_id": f"{open_event['shadow_trade_id']}:CLOSE",
        "event_type": "CLOSE",
        "schema_version": "shadow_runtime_v1",
        "symbol": open_event["symbol"],
        "shadow_trade_id": open_event["shadow_trade_id"],
        "canonical_opportunity_id": open_event["canonical_opportunity_id"],
        "horizon": open_event["horizon"],
        "exit_market_time_utc_epoch_s": time,
        "exit_reason": "take_profit",
        "outcome": {"pnl_r_multiple": r},
        "record_lineage": {"dataset": "shadow_runtime"},
    }


def _candidate_events(open_event, *, r=1.0, runtime_id="candidate-runtime-1"):
    cid = FakeBarCountAdapter.CANDIDATE_ID
    pid = FakeBarCountAdapter.POLICY_ID
    lineage = {
        "experiment_arm_assignment": dict(open_event["experiment_arm"]),
        "baseline_open_event_id": open_event["event_id"],
        "baseline_record_lineage": dict(open_event["record_lineage"]),
    }
    state = {
        "candidate_id": cid,
        "policy_id": pid,
        "treatment_hash": FakeBarCountAdapter.TREATMENT_HASH,
        "shadow_trade_id": open_event["shadow_trade_id"],
        "canonical_opportunity_id": open_event["canonical_opportunity_id"],
        "trade_horizon": open_event["horizon"],
        "experiment_id": "TEST_EXPERIMENT",
        "experiment_arm": "CANDIDATE",
        "lineage": lineage,
    }
    common = {
        "candidate_runtime_id": runtime_id,
        "candidate_id": cid,
        "policy_id": pid,
        "treatment_hash": FakeBarCountAdapter.TREATMENT_HASH,
        "shadow_trade_id": open_event["shadow_trade_id"],
        "canonical_opportunity_id": open_event["canonical_opportunity_id"],
        "trade_horizon": open_event["horizon"],
        "symbol": "EURUSD",
        "experiment_id": "TEST_EXPERIMENT",
        "experiment_arm": "CANDIDATE",
        "lineage": lineage,
    }
    return (
        dict(common, event_id=f"{runtime_id}:OPEN", event_type="CANDIDATE_OPEN",
             schema_version="shadow_candidate_v1", state=state),
        dict(
            common,
            event_id=f"{runtime_id}:CLOSE",
            event_type="CANDIDATE_CLOSE",
            schema_version="shadow_candidate_v1",
            state=state,
            outcome={
                "exit_reason": "candidate_exit",
                "exit_time": 1700000400,
                "candidate_r": r,
            },
        ),
    )


def _append_baseline(base_dir, event):
    timestamp = int(
        event.get("exit_market_time_utc_epoch_s")
        or event.get("entry_market_time_utc_epoch_s")
        or 1700000000
    )
    day = datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d")
    path = Path(base_dir) / event.get("symbol", "EURUSD") / f"{day}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event, separators=(",", ":")) + "\n")


def _append_candidate(base_dir, event):
    CandidateEventWriter(base_dir).append(
        event=event,
        symbol=event.get("symbol", "EURUSD"),
        market_time_utc=int(event.get("outcome", {}).get("exit_time") or 1700000000),
    )


def _evaluator(tmp_path, runtime, candidate_dir):
    baseline_dir = str(tmp_path / "baseline")
    evaluation_dir = str(tmp_path / "evaluation")
    return (
        CandidateEvaluation(
            writer=CandidateEvaluationWriter(evaluation_dir),
            baseline_dir=baseline_dir,
            candidate_dir=candidate_dir,
            candidate_runtime=runtime,
        ),
        baseline_dir,
    )


def test_candidate_arm_auto_binding_is_idempotent_and_control_is_ignored(tmp_path, monkeypatch):
    import core.shadow.candidate_runtime as candidate_runtime_module

    runtime, _ = _runtime(tmp_path)
    monkeypatch.setattr(
        candidate_runtime_module, "get_candidate_runtime", lambda: runtime
    )
    candidate_open = _baseline_open()
    bind_candidate_from_shadow_open(candidate_open)
    bind_candidate_from_shadow_open(candidate_open)
    bind_candidate_from_shadow_open(
        _baseline_open(tid="shadow-1", canonical="canonical-1")
    )
    assert len(runtime.active_ids()) == 1
    snapshot = runtime.snapshot(runtime.active_ids()[0])
    assert snapshot["experiment_arm"] == "CANDIDATE"
    assert snapshot["lineage"]["experiment_arm_assignment"]["assignment_id"] == (
        "arm-test-1"
    )

    control = _baseline_open(tid="shadow-control", canonical="canonical-control")
    control["experiment_arm"]["experiment_arm"] = "CONTROL"
    bind_candidate_from_shadow_open(control)
    assert len(runtime.active_ids()) == 1


def test_zero_registration_and_binding_failure_are_isolated(tmp_path, monkeypatch):
    import core.shadow.candidate_runtime as candidate_runtime_module

    runtime = CandidateRuntime(
        writer=CandidateEventWriter(str(tmp_path / "none"))
    )
    monkeypatch.setattr(
        candidate_runtime_module, "get_candidate_runtime", lambda: runtime
    )
    bind_candidate_from_shadow_open(_baseline_open())
    assert runtime.active_ids() == []

    class FailingAdapter:
        def initialize(self, *, entry_geometry):
            raise RuntimeError("fixture failure")

    failing, _ = _runtime(tmp_path / "failed", adapter=FailingAdapter())
    monkeypatch.setattr(
        candidate_runtime_module, "get_candidate_runtime", lambda: failing
    )
    baseline_open = _baseline_open(tid="shadow-fail", canonical="canonical-fail")
    bind_candidate_from_shadow_open(baseline_open)
    assert baseline_open["event_type"] == "OPEN"
    assert failing.active_ids() == []


def test_candidate_runtime_ensures_recovery_after_registrations(tmp_path):
    first_runtime, candidate_dir = _runtime(tmp_path)
    first_runtime.bind_shadow_lifecycle(
        shadow_trade_id="shadow-restart",
        canonical_opportunity_id="canonical-restart",
        trade_horizon="SCALP",
        symbol="EURUSD",
        direction="BUY",
        entry_time=1700000000,
        entry_price=1.1,
        stop_loss=1.099,
        take_profit=1.102,
        candidate_id=FakeBarCountAdapter.CANDIDATE_ID,
        policy_id=FakeBarCountAdapter.POLICY_ID,
    )
    restarted_runtime, restarted_dir = _runtime(tmp_path)
    assert restarted_dir == candidate_dir
    recovery = restarted_runtime.ensure_recovered()
    assert recovery["active"] == 1
    assert len(restarted_runtime.active_ids()) == 1


def test_candidate_close_waits_for_baseline_and_duplicate_delivery_pairs_once(tmp_path):
    runtime, candidate_dir = _runtime(tmp_path)
    evaluation, baseline_dir = _evaluator(tmp_path, runtime, candidate_dir)
    open_event = _baseline_open()
    candidate_open, candidate_close = _candidate_events(open_event)
    _append_baseline(baseline_dir, open_event)
    _append_candidate(candidate_dir, candidate_open)
    _append_candidate(candidate_dir, candidate_close)

    assert evaluation.reconcile()["paired"] == 0
    close_event = _baseline_close(open_event, r=0.5)
    _append_baseline(baseline_dir, close_event)
    _append_baseline(baseline_dir, close_event)
    result = evaluation.reconcile()
    assert result["created"] == 1
    assert len(evaluation.records()) == 1
    record = evaluation.records()[0]
    assert record["outcome_classification"] == "IMPROVED"
    assert record["paired_delta_r"] == 0.5

    restarted = CandidateEvaluation(
        writer=CandidateEvaluationWriter(evaluation._writer.base_dir),
        baseline_dir=baseline_dir,
        candidate_dir=candidate_dir,
        candidate_runtime=runtime,
    )
    assert restarted.reconcile()["created"] == 0
    assert len(restarted.records()) == 1


def test_baseline_close_waits_for_candidate_and_all_classifications_are_emitted(tmp_path):
    runtime, candidate_dir = _runtime(tmp_path)
    evaluation, baseline_dir = _evaluator(tmp_path, runtime, candidate_dir)
    expected = {
        "improved": (0.5, 1.0, "IMPROVED"),
        "worsened": (0.5, 0.0, "WORSENED"),
        "unchanged": (0.5, 0.5, "UNCHANGED"),
    }
    for index, (name, (baseline_r, candidate_r, _)) in enumerate(expected.items()):
        open_event = _baseline_open(
            tid=f"shadow-{name}", canonical=f"canonical-{name}"
        )
        _append_baseline(baseline_dir, open_event)
        _append_baseline(
            baseline_dir,
            _baseline_close(open_event, r=baseline_r, time=1700000300 + index * 300),
        )
    assert evaluation.reconcile()["paired"] == 0

    for index, (name, (baseline_r, candidate_r, _)) in enumerate(expected.items()):
        open_event = _baseline_open(
            tid=f"shadow-{name}", canonical=f"canonical-{name}"
        )
        candidate_open, candidate_close = _candidate_events(
            open_event, r=candidate_r, runtime_id=f"runtime-{name}"
        )
        _append_candidate(candidate_dir, candidate_open)
        _append_candidate(candidate_dir, candidate_close)
    result = evaluation.reconcile()
    assert result["created"] == 3
    records = {
        row["canonical_opportunity_id"]: row["outcome_classification"]
        for row in evaluation.records()
    }
    assert records == {
        f"canonical-{name}": classification
        for name, (_, _, classification) in expected.items()
    }


def test_canonical_mismatch_fails_closed(tmp_path):
    runtime, candidate_dir = _runtime(tmp_path)
    evaluation, baseline_dir = _evaluator(tmp_path, runtime, candidate_dir)
    open_event = _baseline_open()
    candidate_open, candidate_close = _candidate_events(open_event)
    wrong_close = _baseline_close(open_event)
    wrong_close["canonical_opportunity_id"] = "other-canonical"
    for event in (open_event, wrong_close):
        _append_baseline(baseline_dir, event)
    for event in (candidate_open, candidate_close):
        _append_candidate(candidate_dir, event)
    result = evaluation.reconcile()
    assert result["paired"] == 0
    assert result["integrity_violations"] == 1
    assert result["errors"][0]["reason"] == (
        "BASELINE_CANDIDATE_CANONICAL_OR_HORIZON_MISMATCH"
    )


def test_hash_mismatch_and_ambiguous_baseline_close_fail_closed(tmp_path):
    runtime, candidate_dir = _runtime(tmp_path)
    evaluation, baseline_dir = _evaluator(tmp_path, runtime, candidate_dir)
    open_event = _baseline_open()
    candidate_open, candidate_close = _candidate_events(open_event)
    _append_baseline(baseline_dir, open_event)
    _append_baseline(baseline_dir, _baseline_close(open_event))
    duplicate = _baseline_close(open_event, r=0.75)
    duplicate["event_id"] = "shadow-1:CLOSE:ambiguous"
    _append_baseline(baseline_dir, duplicate)
    candidate_open["treatment_hash"] = "different-treatment"
    candidate_close["treatment_hash"] = "different-treatment"
    _append_candidate(candidate_dir, candidate_open)
    _append_candidate(candidate_dir, candidate_close)
    result = evaluation.reconcile()
    assert result["paired"] == 0
    assert result["errors"][0]["reason"] == (
        "TREATMENT_HASH_MISMATCH_OR_UNREGISTERED"
    )

    runtime2, candidate_dir2 = _runtime(tmp_path / "ambiguous")
    evaluation2, baseline_dir2 = _evaluator(
        tmp_path / "ambiguous", runtime2, candidate_dir2
    )
    open_event2 = _baseline_open()
    candidate_open2, candidate_close2 = _candidate_events(open_event2)
    _append_baseline(baseline_dir2, open_event2)
    _append_baseline(baseline_dir2, _baseline_close(open_event2))
    duplicate = _baseline_close(open_event2, r=0.75)
    duplicate["event_id"] = "shadow-1:CLOSE:ambiguous"
    _append_baseline(baseline_dir2, duplicate)
    _append_candidate(candidate_dir2, candidate_open2)
    _append_candidate(candidate_dir2, candidate_close2)
    result = evaluation2.reconcile()
    assert any(
        error["reason"] == "AMBIGUOUS_BASELINE_TERMINAL_OUTCOME"
        for error in result["errors"]
    )


def test_monitor_exact_metrics_and_readiness_rebuild(tmp_path):
    criteria = {
        "minimum_paired_sample": 4,
        "minimum_candidate_sample": 4,
        "candidate_pf_at_least_baseline": True,
        "max_drawdown_multiple": 1.0,
        "minimum_mean_paired_delta_r": 0.0,
        "require_complete_pairing": True,
        "require_zero_integrity_violations": True,
    }
    runtime, candidate_dir = _runtime(
        tmp_path, criteria=criteria, minimum_sample=4
    )
    evaluation, baseline_dir = _evaluator(tmp_path, runtime, candidate_dir)
    evaluation_writer = evaluation._writer
    fixture = [
        (1.0, 1.5),
        (-1.0, -0.5),
        (0.5, 0.25),
        (-0.5, -1.5),
    ]
    for index, (baseline_r, candidate_r) in enumerate(fixture):
        canonical_id = f"canonical-{index}"
        open_event = _baseline_open(
            tid=f"shadow-{index}", canonical=canonical_id
        )
        candidate_open, candidate_close = _candidate_events(
            open_event,
            r=candidate_r,
            runtime_id=f"candidate-runtime-{index}",
        )
        _append_baseline(baseline_dir, open_event)
        _append_baseline(
            baseline_dir,
            _baseline_close(
                open_event, r=baseline_r, time=1700000300 + index * 300
            ),
        )
        _append_candidate(candidate_dir, candidate_open)
        _append_candidate(candidate_dir, candidate_close)
    evaluation.reconcile()
    monitor = CandidateMonitor(
        evaluation=evaluation,
        candidate_runtime=runtime,
        candidate_dir=candidate_dir,
    )
    report = monitor.report()["reports"][0]
    metrics = report["metrics"]
    assert metrics["paired_n"] == 4
    assert (metrics["improved_n"], metrics["worsened_n"],
            metrics["unchanged_n"]) == (2, 2, 0)
    assert metrics["baseline_expectancy_r"] == 0.0
    assert metrics["candidate_expectancy_r"] == -0.0625
    assert metrics["mean_paired_delta_r"] == -0.0625
    assert metrics["median_paired_delta_r"] == 0.125
    assert metrics["baseline_profit_factor"] == 1.0
    assert metrics["candidate_profit_factor"] == 0.875
    assert metrics["baseline_max_drawdown_r"] == 1.0
    assert metrics["candidate_max_drawdown_r"] == 1.75
    assert report["readiness"]["state"] == "SHADOW_VALIDATION_ACTIVE"
    assert "CANDIDATE_PF_AT_LEAST_BASELINE" in (
        report["readiness"]["unsatisfied_criteria"]
    )

    restarted_evaluation = CandidateEvaluation(
        writer=CandidateEvaluationWriter(evaluation_writer.base_dir),
        baseline_dir=baseline_dir,
        candidate_dir=candidate_dir,
        candidate_runtime=runtime,
    )
    restarted = CandidateMonitor(
        evaluation=restarted_evaluation,
        candidate_runtime=runtime,
        candidate_dir=candidate_dir,
    ).report()["reports"][0]
    assert restarted["metrics"] == metrics
    assert restarted["readiness"] == report["readiness"]


def test_canonical_opportunity_is_one_monitoring_observation_across_horizons(
    tmp_path,
):
    runtime, candidate_dir = _runtime(tmp_path)
    evaluation, baseline_dir = _evaluator(tmp_path, runtime, candidate_dir)
    for index, horizon in enumerate(("SCALP", "INTRADAY")):
        open_event = _baseline_open(
            tid=f"shadow-horizon-{index}",
            canonical="shared-canonical",
            horizon=horizon,
        )
        candidate_open, candidate_close = _candidate_events(
            open_event, r=1.0 + index, runtime_id=f"runtime-horizon-{index}"
        )
        _append_baseline(baseline_dir, open_event)
        _append_baseline(
            baseline_dir,
            _baseline_close(open_event, r=0.5 + index, time=1700000300 + index),
        )
        _append_candidate(candidate_dir, candidate_open)
        _append_candidate(candidate_dir, candidate_close)
    evaluation.reconcile()
    metrics = CandidateMonitor(
        evaluation=evaluation,
        candidate_runtime=runtime,
        candidate_dir=candidate_dir,
    ).report()["reports"][0]["metrics"]
    assert len(evaluation.records()) == 2
    assert metrics["paired_n"] == 1
    assert metrics["observed_candidate_lifecycles"] == 1
    assert metrics["completed_candidate_outcomes"] == 1


def test_readiness_never_self_promotes_without_criteria_and_supports_review(tmp_path):
    runtime, candidate_dir = _runtime(tmp_path, criteria={})
    evaluation, _ = _evaluator(tmp_path, runtime, candidate_dir)
    report = CandidateMonitor(
        evaluation=evaluation,
        candidate_runtime=runtime,
        candidate_dir=candidate_dir,
    ).report()["reports"][0]
    assert report["readiness"]["state"] == "SHADOW_VALIDATION_ACTIVE"

    reg = runtime.registration(
        FakeBarCountAdapter.CANDIDATE_ID, FakeBarCountAdapter.POLICY_ID
    )
    reg.readiness_criteria = {"minimum_paired_sample": 2}
    insufficient = CandidateMonitor(
        evaluation=evaluation,
        candidate_runtime=runtime,
        candidate_dir=candidate_dir,
    ).report()["reports"][0]
    assert insufficient["readiness"]["state"] == (
        "SHADOW_VALIDATION_INSUFFICIENT_DATA"
    )

    reg.readiness_criteria = {"minimum_paired_sample": 1}
    open_event = _baseline_open()
    candidate_open, candidate_close = _candidate_events(open_event)
    _append_baseline(str(tmp_path / "baseline"), open_event)
    _append_baseline(
        str(tmp_path / "baseline"),
        _baseline_close(open_event, r=0.5),
    )
    _append_candidate(candidate_dir, candidate_open)
    _append_candidate(candidate_dir, candidate_close)
    validated = CandidateMonitor(
        evaluation=evaluation,
        candidate_runtime=runtime,
        candidate_dir=candidate_dir,
    ).report()["reports"][0]
    assert validated["readiness"]["state"] == "SHADOW_VALIDATED"
    review = CandidateMonitor(
        evaluation=evaluation,
        candidate_runtime=runtime,
        candidate_dir=candidate_dir,
    ).report(promotion_review_prerequisites_satisfied=True)["reports"][0]
    assert review["readiness"]["state"] == "READY_FOR_PROMOTION_REVIEW"


def test_confidence_interval_readiness_is_unavailable_and_pf_zero_loss_is_explicit(
    tmp_path,
):
    runtime, candidate_dir = _runtime(
        tmp_path, criteria={"minimum_paired_sample": 1, "confidence_interval": True}
    )
    evaluation, baseline_dir = _evaluator(tmp_path, runtime, candidate_dir)
    open_event = _baseline_open()
    candidate_open, candidate_close = _candidate_events(open_event, r=2.0)
    _append_baseline(baseline_dir, open_event)
    _append_baseline(baseline_dir, _baseline_close(open_event, r=1.0))
    _append_candidate(candidate_dir, candidate_open)
    _append_candidate(candidate_dir, candidate_close)
    report = CandidateMonitor(
        evaluation=evaluation,
        candidate_runtime=runtime,
        candidate_dir=candidate_dir,
    ).report()["reports"][0]
    assert report["metrics"]["baseline_profit_factor"] is None
    assert report["metrics"]["baseline_profit_factor_status"] == "NO_GROSS_LOSSES"
    assert report["readiness"]["state"] == "SHADOW_VALIDATION_ACTIVE"
    assert "CONFIDENCE_INTERVAL_UNAVAILABLE" in (
        report["readiness"]["unsatisfied_criteria"]
    )


def test_evaluation_persistence_failure_is_not_valid_evidence(tmp_path):
    runtime, candidate_dir = _runtime(
        tmp_path, criteria={"minimum_paired_sample": 1}
    )
    _, baseline_dir = _evaluator(tmp_path, runtime, candidate_dir)
    open_event = _baseline_open()
    candidate_open, candidate_close = _candidate_events(open_event)
    _append_baseline(baseline_dir, open_event)
    _append_baseline(baseline_dir, _baseline_close(open_event))
    _append_candidate(candidate_dir, candidate_open)
    _append_candidate(candidate_dir, candidate_close)

    class FailingWriter(CandidateEvaluationWriter):
        def append(self, *, event, symbol, market_time_utc):
            return False

    evaluation = CandidateEvaluation(
        writer=FailingWriter(str(tmp_path / "failed-evaluation")),
        baseline_dir=baseline_dir,
        candidate_dir=candidate_dir,
        candidate_runtime=runtime,
    )
    result = evaluation.reconcile()
    assert result["paired"] == 0
    assert result["errors"][0]["reason"] == "EVALUATION_PERSISTENCE_FAILED"
    assert evaluation.records() == []
    report = CandidateMonitor(
        evaluation=evaluation,
        candidate_runtime=runtime,
        candidate_dir=candidate_dir,
    ).report()["reports"][0]
    assert report["readiness"]["state"] == "SHADOW_VALIDATION_INVALID"
    assert Path(baseline_dir).exists()
    assert Path(candidate_dir).exists()


def test_evaluation_modules_are_execution_isolated():
    modules = (
        "core/shadow/candidate_evaluation.py",
        "core/shadow/candidate_evaluation_persistence.py",
        "core/shadow/candidate_monitoring.py",
    )
    forbidden = (
        "order_send",
        "position modify",
        "position close",
        "mt5_execution",
        "execution_orchestrator",
    )
    for relative in modules:
        source = (ROOT / relative).read_text(encoding="utf-8").lower()
        assert all(token not in source for token in forbidden)
