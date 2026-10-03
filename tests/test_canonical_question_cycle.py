from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest

from core.production_data_contract import current_schema, s3_base_prefix
from research_engine.data_access.s3_source import S3ResearchDataSource, get_default_source
from research_engine.registry.research_question_registry import REGISTRY
from research_engine.v10.continuous.canonical_question_cycle import (
    AFFECTED,
    ALIAS_OR_SUPERSEDED,
    REQUIRES_RECHECK,
    UNAFFECTED,
    CanonicalQuestionCycleError,
    question_result_delta,
    run_canonical_question_cycle,
)
from research_engine.v10.continuous.question_cycle_state import (
    CanonicalQuestionResult,
    QuestionCycleStore,
)
from research_engine.v10.investigation_snapshot import (
    BOUND_DATASETS,
    freeze_investigation_snapshot,
    save_investigation_snapshot,
)


def _key(dataset: str, day: str = "2026-09-25", part: str = "part-000.jsonl") -> str:
    return (
        f"{s3_base_prefix(dataset)}/schema_version={current_schema(dataset)}"
        f"/symbol=EURUSD/date={day}/{part}"
    )


def _jsonl(*rows: dict) -> str:
    return "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)


def _shadow_events() -> tuple[dict, dict]:
    common = {
        "schema_version": current_schema("shadow_runtime"),
        "canonical_opportunity_id": "nopp_1",
        "observation_id": "nobs_1",
        "shadow_trade_id": "nshadow_0123456789abcdef",
        "symbol": "EURUSD",
        "horizon": "SCALP",
        "plan_id": "nplan_1",
    }
    opened = {
        **common,
        "event_type": "OPEN",
        "identity": {
            "entity_id": "EURUSD_1", "cycle_id": 1,
            "trade_horizon": "SCALP", "evaluated_horizon": "SCALP",
            "shadow_type": "HORIZON_ALTERNATIVE",
        },
        "live_facts": {
            "pattern": "HAMMER", "strategy": "MEAN_REVERSION", "score": 0.8,
            "regime": "TRENDING", "h4_regime": "TRENDING",
            "market_phase": "IMPULSE", "h1_bias": "BULLISH",
        },
        "construction": {
            "direction": "BUY", "entry_price": 1.10, "stop_loss": 1.09,
            "take_profit": 1.12, "risk_distance": 0.01, "intended_rr": 2.0,
        },
        "entry_market_time_utc_epoch_s": 1777700000,
        "entry_market_time_utc_iso8601": "2026-04-01T00:00:00Z",
    }
    closed = {
        **common,
        "event_type": "CLOSE",
        "exit_market_time_utc_epoch_s": 1777701500,
        "exit_market_time_utc_iso8601": "2026-04-01T00:25:00Z",
        "exit_price": 1.12,
        "exit_reason": "take_profit",
        "bars_held": 5,
        "outcome": {
            "pnl_r_multiple": 2.0, "mfe_r": 2.1, "mae_r": -0.2,
            "risk_distance": 0.01, "intended_rr": 2.0,
        },
    }
    return opened, closed


def _row(dataset: str, marker: str = "base") -> dict:
    return {
        "schema_version": current_schema(dataset),
        "data_epoch": "CURRENT",
        "marker": marker,
        "entity_id": "EURUSD_1",
        "canonical_opportunity_id": "nopp_1",
        "correlation_id": "corr_1",
        "trade_id": "trade_1",
        "cycle_id": 1,
        "timestamp_utc": "2026-09-25T12:00:00Z",
        "pattern": "HAMMER",
        "strategy": "REVERSAL",
        "score": 0.8,
        "components": {"trend": 0.8},
        "r_multiple": 2.0,
        "pnl_r_multiple": 2.0,
        "exit_reason": "take_profit",
        "market_phase": "IMPULSE",
        "h4_regime": "TRENDING",
        "trade_horizon": "SCALP",
        "result_ok": True,
        "protection_status": "VERIFIED",
    }


def _objects() -> dict[str, str]:
    result = {}
    for dataset in BOUND_DATASETS:
        rows = _shadow_events() if dataset == "shadow_runtime" else (_row(dataset),)
        result[_key(dataset)] = _jsonl(*rows)
    return result


class MemoryS3:
    def __init__(self, objects: dict[str, str]):
        self.objects = dict(objects)
        self.get_calls: list[str] = []

    def list_objects_v2(self, **kwargs):
        prefix = str(kwargs.get("Prefix") or "")
        return {
            "IsTruncated": False,
            "Contents": [
                {
                    "Key": key,
                    "ETag": '"' + hashlib.md5(body.encode()).hexdigest() + '"',
                    "Size": len(body.encode()),
                    "LastModified": "2026-09-25T13:00:00Z",
                }
                for key, body in sorted(self.objects.items()) if key.startswith(prefix)
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
                return body.encode()

        return {
            "Body": Body(),
            "ETag": '"' + hashlib.md5(body.encode()).hexdigest() + '"',
            "ContentLength": len(body.encode()),
            "LastModified": "2026-09-25T13:00:00Z",
            "VersionId": "v1",
        }


def _freeze(tmp_path: Path, fake: MemoryS3):
    source = S3ResearchDataSource(bucket="question-cycle-test", client=fake)
    snapshot = freeze_investigation_snapshot(
        start_date="2026-09-25", end_date="2026-09-25", source=source)
    directory = tmp_path / "manifests"
    save_investigation_snapshot(snapshot, directory / f"{snapshot.snapshot_id}.json")
    return snapshot, directory


def _runners(overrides: dict[str, dict] | None = None, broken: str | None = None):
    configured = overrides or {}
    output = {}
    for question in REGISTRY:
        if not question.runner_module:
            continue

        def runner(*args, _qid=question.id, **kwargs):
            if _qid == broken:
                raise RuntimeError("deterministic runner failure")
            report = {
                "status": "COMPLETE",
                "conclusion": "answer:" + _qid,
                "sample_n": 1,
                "primary_metrics": {"expectancy": 0.25},
                "confidence": "LOW",
            }
            report.update(configured.get(_qid, {}))
            return report

        output[question.id] = runner
    return output


class RecordingExecutor:
    def __init__(self):
        self.snapshot_ids: list[str] = []

    def __call__(self, runner, question, population, context):
        self.snapshot_ids.append(context.snapshot_id)
        return runner(population=population, context=context)


def _run(tmp_path, fake, snapshot, manifests, **kwargs):
    handoff = {
        "snapshot_id": snapshot.snapshot_id,
        "fingerprint": snapshot.snapshot_fingerprint,
        "investigation_epoch": snapshot.evidence_epoch,
        "predecessor_snapshot_id": kwargs.pop("predecessor_snapshot_id", None),
        "changed_datasets": kwargs.pop("changed_datasets", list(BOUND_DATASETS)),
    }
    return run_canonical_question_cycle(
        handoff,
        source=S3ResearchDataSource(bucket="question-cycle-test", client=fake),
        manifest_directory=manifests,
        state_directory=tmp_path / "cycles",
        runners=kwargs.pop("runners", _runners()),
        runner_executor=kwargs.pop("runner_executor", RecordingExecutor()),
        **kwargs,
    )


def _projection(tmp_path: Path) -> dict:
    return json.loads(
        (tmp_path / "cycles" / "current_projection.json").read_text(encoding="utf-8"))


def test_first_snapshot_binds_all_evaluated_questions_and_accounts_for_all_70(tmp_path):
    fake = MemoryS3(_objects())
    snapshot, manifests = _freeze(tmp_path, fake)
    executor = RecordingExecutor()
    cycle = _run(tmp_path, fake, snapshot, manifests, runner_executor=executor)
    projection = _projection(tmp_path)

    assert cycle.total_questions == 70
    assert len(projection["questions"]) == 70
    assert set(projection["questions"]) == {question.id for question in REGISTRY}
    assert len(executor.snapshot_ids) > 0
    assert set(executor.snapshot_ids) == {snapshot.snapshot_id}
    assert cycle.evaluated_count == 68
    assert cycle.retained_count == 0
    assert all(cycle.planning[q.id] == AFFECTED
               for q in REGISTRY if q.id not in {"S1", "L6"})


def test_s1_is_owner_alias_and_l6_is_explicitly_unimplemented(tmp_path):
    fake = MemoryS3(_objects())
    snapshot, manifests = _freeze(tmp_path, fake)
    cycle = _run(tmp_path, fake, snapshot, manifests)
    questions = _projection(tmp_path)["questions"]

    s1 = questions["S1"]
    assert cycle.planning["S1"] == ALIAS_OR_SUPERSEDED
    assert s1["result"]["status"] == ALIAS_OR_SUPERSEDED
    assert s1["result"]["substantive_answer"]["scientific_owner_id"] == "E3"
    assert s1["definition"]["registered_runner"] is None

    l6 = questions["L6"]
    assert l6["result"]["status"] == "UNIMPLEMENTED"
    assert l6["result"]["failure_reason"] == "NO_REGISTERED_RUNNER:L6"
    assert l6["result"]["snapshot_id"] == snapshot.snapshot_id


def test_normalisation_preserves_scientific_outputs_and_lineage(tmp_path):
    fake = MemoryS3(_objects())
    snapshot, manifests = _freeze(tmp_path, fake)
    report = {
        "status": "NEGATIVE_RESULT",
        "conclusion": "No durable edge",
        "sample_n": 42,
        "primary_metrics": {
            "expectancy": -0.12,
            "profit_factor": 0.82,
            "win_rate": 0.41,
            "max_drawdown": 4.5,
            "confidence_interval": [-0.3, 0.02],
        },
        "confidence": {"level": 0.95},
        "limitations": ["small regime slice"],
        "missing_evidence": ["longer history"],
    }
    _run(tmp_path, fake, snapshot, manifests, runners=_runners({"E1": report}))
    result = _projection(tmp_path)["questions"]["E1"]["result"]

    assert result["status"] == "NEGATIVE_RESULT"
    assert result["substantive_answer"] == "No durable edge"
    assert result["sample_n"] == 42
    assert result["key_metrics"]["expectancy"] == -0.12
    assert result["key_metrics"]["profit_factor"] == 0.82
    assert result["key_metrics"]["win_rate"] == 0.41
    assert result["key_metrics"]["max_drawdown"] == 4.5
    assert result["key_metrics"]["confidence_interval"] == [-0.3, 0.02]
    assert result["confidence"] == {"level": 0.95}
    assert "small regime slice" in result["limitations"]
    assert "longer history" in result["missing_evidence"]
    assert result["evidence_references"]
    assert {item["dataset"] for item in result["evidence_references"]} == {"shadow_runtime"}


def test_missing_question_dataset_is_waiting_without_current_source_fallback(tmp_path):
    fake = MemoryS3(_objects())
    snapshot, manifests = _freeze(tmp_path, fake)
    cycle = _run(tmp_path, fake, snapshot, manifests)
    port = _projection(tmp_path)["questions"]["PORT-1"]["result"]

    assert port["status"] == "WAITING_FOR_DATA"
    assert "portfolio_rankings" in port["missing_evidence"]
    assert cycle.total_questions == 70
    assert all("portfolio_rankings" not in key for key in fake.get_calls)


def test_added_current_s3_object_after_freeze_cannot_change_cycle_result(tmp_path):
    fake = MemoryS3(_objects())
    snapshot, manifests = _freeze(tmp_path, fake)
    fake.objects[_key("trade_truth", part="late-current.jsonl")] = _jsonl(
        _row("trade_truth", "late"))
    executor = RecordingExecutor()
    first = _run(tmp_path, fake, snapshot, manifests, runner_executor=executor)
    first_projection = _projection(tmp_path)
    second = _run(tmp_path, fake, snapshot, manifests, runner_executor=executor)

    assert second.to_dict() == first.to_dict()
    assert _projection(tmp_path) == first_projection
    assert _key("trade_truth", part="late-current.jsonl") not in fake.get_calls


def test_default_runner_environment_is_exact_bound_and_blocks_escape(tmp_path):
    fake = MemoryS3(_objects())
    snapshot, manifests = _freeze(tmp_path, fake)

    def bound_runner(shadow_trades=None):
        rows = get_default_source().read_dataset("trade_truth")
        return {"status": "COMPLETE", "sample_n": len(rows), "conclusion": "bound"}

    runners = _runners()
    runners["E1"] = bound_runner
    run_canonical_question_cycle(
        {"snapshot_id": snapshot.snapshot_id, "fingerprint": snapshot.snapshot_fingerprint,
         "investigation_epoch": snapshot.evidence_epoch,
         "changed_datasets": list(BOUND_DATASETS)},
        source=S3ResearchDataSource(bucket="question-cycle-test", client=fake),
        manifest_directory=manifests,
        state_directory=tmp_path / "bound",
        runners=runners,
    )
    assert json.loads((tmp_path / "bound" / "current_projection.json").read_text())[
        "questions"]["E1"]["result"]["substantive_answer"] == "bound"

    def escaping_runner(shadow_trades=None):
        get_default_source().read_dataset("portfolio_rankings")
        return {"status": "COMPLETE"}

    runners["E1"] = escaping_runner
    run_canonical_question_cycle(
        {"snapshot_id": snapshot.snapshot_id, "fingerprint": snapshot.snapshot_fingerprint,
         "investigation_epoch": snapshot.evidence_epoch,
         "changed_datasets": list(BOUND_DATASETS)},
        source=S3ResearchDataSource(bucket="question-cycle-test", client=fake),
        manifest_directory=manifests,
        state_directory=tmp_path / "escape",
        runners=runners,
    )
    escaped = json.loads((tmp_path / "escape" / "current_projection.json").read_text())
    assert escaped["questions"]["E1"]["result"]["status"] == "INVALID"
    assert "DATASET_NOT_BOUND:portfolio_rankings" in escaped["questions"]["E1"]["result"]["failure_reason"]


def test_affected_planner_retains_unrelated_and_rechecks_ambiguous(tmp_path):
    fake = MemoryS3(_objects())
    first_snapshot, manifests = _freeze(tmp_path, fake)
    first = _run(tmp_path, fake, first_snapshot, manifests)
    first_projection = _projection(tmp_path)

    fake.objects[_key("decision_trace")] = _jsonl(_row("decision_trace", "changed"))
    second_snapshot, _ = _freeze(tmp_path, fake)
    second = _run(
        tmp_path, fake, second_snapshot, manifests,
        predecessor_snapshot_id=first.snapshot_id,
        changed_datasets=["decision_trace"],
    )
    second_projection = _projection(tmp_path)

    assert second.planning["E1"] == UNAFFECTED
    assert second_projection["questions"]["E1"]["retained_previous"] is True
    assert second_projection["questions"]["E1"]["last_evaluated_snapshot_id"] == first.snapshot_id
    assert second.planning["D1"] == AFFECTED
    assert second.planning["PORT-1"] == REQUIRES_RECHECK
    assert second_projection["questions"]["D1"]["last_evaluated_snapshot_id"] == second_snapshot.snapshot_id
    assert first_projection["questions"]["E1"]["result"] == second_projection["questions"]["E1"]["result"]


def test_previous_insufficient_question_reenters_when_required_dataset_changes(tmp_path):
    fake = MemoryS3(_objects())
    first_snapshot, manifests = _freeze(tmp_path, fake)
    first = _run(
        tmp_path, fake, first_snapshot, manifests,
        runners=_runners({
            "E2": {"status": "INSUFFICIENT_DATA", "missing_evidence": ["sample_size"]}
        }),
    )
    assert _projection(tmp_path)["questions"]["E2"]["result"]["status"] == "INSUFFICIENT_DATA"

    extra_open, extra_close = deepcopy(_shadow_events())
    extra_open["shadow_trade_id"] = extra_close["shadow_trade_id"] = "nshadow_ffffffffffffffff"
    extra_open["canonical_opportunity_id"] = extra_close["canonical_opportunity_id"] = "nopp_2"
    fake.objects[_key("shadow_runtime")] += _jsonl(extra_open, extra_close)
    second_snapshot, _ = _freeze(tmp_path, fake)
    second = _run(
        tmp_path, fake, second_snapshot, manifests,
        predecessor_snapshot_id=first.snapshot_id,
        changed_datasets=["shadow_runtime"],
    )
    assert second.planning["E2"] == AFFECTED
    assert _projection(tmp_path)["questions"]["E2"]["last_evaluated_snapshot_id"] == second_snapshot.snapshot_id


def test_history_is_immutable_projection_advances_and_retained_history_is_not_duplicated(tmp_path):
    fake = MemoryS3(_objects())
    first_snapshot, manifests = _freeze(tmp_path, fake)
    first = _run(tmp_path, fake, first_snapshot, manifests)
    e1_history = tmp_path / "cycles" / "question_history" / "E1"
    first_files = list(e1_history.glob("*.json"))
    assert len(first_files) == 1

    fake.objects[_key("decision_trace")] = _jsonl(_row("decision_trace", "v2"))
    second_snapshot, _ = _freeze(tmp_path, fake)
    second = _run(
        tmp_path, fake, second_snapshot, manifests,
        predecessor_snapshot_id=first.snapshot_id,
        changed_datasets=["decision_trace"],
    )
    projection = _projection(tmp_path)
    assert projection["cycle_id"] == second.cycle_id
    assert projection["questions"]["E1"]["last_evaluated_snapshot_id"] == first.snapshot_id
    assert len(list(e1_history.glob("*.json"))) == 1
    d1_history = tmp_path / "cycles" / "question_history" / "D1"
    d1_files = list(d1_history.glob("*.json"))
    assert len(d1_files) == 2
    d1_by_snapshot = {
        json.loads(path.read_text())["snapshot_id"]: json.loads(path.read_text())
        for path in d1_files
    }
    assert d1_by_snapshot[second_snapshot.snapshot_id]["previous_snapshot_id"] == first.snapshot_id
    assert d1_by_snapshot[second_snapshot.snapshot_id]["previous_result_id"] == \
        d1_by_snapshot[first.snapshot_id]["result_id"]
    assert len(list((tmp_path / "cycles" / "cycles").glob("*.json"))) == 2


def _result(**overrides):
    values = {
        "question_id": "E1", "question_version": "v1", "snapshot_id": "ISNAP-X",
        "snapshot_fingerprint": "a" * 64, "investigation_epoch": "EPOCH-X",
        "evaluated_at": "2026-10-03T00:00:00+00:00", "status": "INSUFFICIENT_DATA",
        "substantive_answer": None, "sample_n": 10, "minimum_required_n": 30,
        "sample_deficit": 20, "key_metrics": {"expectancy": 0.1},
        "evidence_datasets": ("shadow_trades",), "missing_evidence": ("sample_size",),
        "implementation_status": "IMPLEMENTED_WAITING_EVIDENCE",
    }
    values.update(overrides)
    return CanonicalQuestionResult(**values)


def test_delta_detects_every_governed_change_dimension_and_no_change():
    previous = _result()
    current = _result(
        snapshot_id="ISNAP-Y", status="COMPLETE", substantive_answer="answer",
        sample_n=30, sample_deficit=0, key_metrics={"expectancy": 0.3},
        missing_evidence=(), implementation_status="IMPLEMENTED")
    delta = question_result_delta(previous, current)
    assert delta == {
        "first_result": False,
        "status_changed": True,
        "substantive_answer_changed": True,
        "sample_n_changed": True,
        "sample_deficit_changed": True,
        "key_metrics_changed": True,
        "evidence_availability_changed": True,
        "implementation_state_changed": True,
        "unchanged": False,
    }
    unchanged = question_result_delta(previous, _result(snapshot_id="ISNAP-Z"))
    assert unchanged["unchanged"] is True


def test_runner_failure_is_isolated_loud_and_all_70_remain(tmp_path):
    fake = MemoryS3(_objects())
    snapshot, manifests = _freeze(tmp_path, fake)
    cycle = _run(
        tmp_path, fake, snapshot, manifests,
        runners=_runners(broken="E1"), runner_executor=RecordingExecutor())
    projection = _projection(tmp_path)

    assert cycle.cycle_status == "COMPLETED_WITH_QUESTION_FAILURES"
    assert "E1" in cycle.failed_question_ids
    assert projection["questions"]["E1"]["result"]["status"] == "INVALID"
    assert "deterministic runner failure" in projection["questions"]["E1"]["result"]["failure_reason"]
    assert len(projection["questions"]) == 70
    assert projection["questions"]["E3"]["result"]["status"] in {
        "COMPLETE", "INSUFFICIENT_DATA"}


def test_registry_and_snapshot_system_failures_fail_the_whole_cycle(tmp_path):
    fake = MemoryS3(_objects())
    snapshot, manifests = _freeze(tmp_path, fake)
    common = dict(
        source=S3ResearchDataSource(bucket="question-cycle-test", client=fake),
        manifest_directory=manifests,
        state_directory=tmp_path / "cycles",
        runners=_runners(),
        runner_executor=RecordingExecutor(),
    )
    with pytest.raises(CanonicalQuestionCycleError, match="QUESTION_COUNT_MISMATCH"):
        run_canonical_question_cycle(snapshot.snapshot_id, registry=REGISTRY[:-1], **common)
    with pytest.raises(CanonicalQuestionCycleError, match="DUPLICATE_CANONICAL"):
        run_canonical_question_cycle(
            snapshot.snapshot_id, registry=(*REGISTRY[:-1], REGISTRY[0]), **common)
    with pytest.raises(CanonicalQuestionCycleError, match="QUESTION_COUNT_MISMATCH"):
        run_canonical_question_cycle(
            snapshot.snapshot_id,
            registry=(*REGISTRY, replace(REGISTRY[0], id="EXTRA-QUESTION")),
            **common,
        )

    class ExplodingRegistry:
        def __iter__(self):
            raise RuntimeError("registry unavailable")

    with pytest.raises(CanonicalQuestionCycleError, match="REGISTRY_LOAD_FAILED"):
        run_canonical_question_cycle(
            snapshot.snapshot_id, registry=ExplodingRegistry(), **common)
    with pytest.raises(CanonicalQuestionCycleError, match="SNAPSHOT_RESOLUTION_FAILED"):
        run_canonical_question_cycle("ISNAP-DOES-NOT-EXIST", **common)


def test_persistence_failure_prevents_success(tmp_path):
    fake = MemoryS3(_objects())
    snapshot, manifests = _freeze(tmp_path, fake)

    class FailingStore(QuestionCycleStore):
        def save_projection(self, projection):
            raise OSError("projection disk failure")

    with pytest.raises(CanonicalQuestionCycleError, match="QUESTION_CYCLE_PERSISTENCE_FAILED"):
        run_canonical_question_cycle(
            snapshot.snapshot_id,
            source=S3ResearchDataSource(bucket="question-cycle-test", client=fake),
            manifest_directory=manifests,
            runners=_runners(),
            runner_executor=RecordingExecutor(),
            store=FailingStore(tmp_path / "failure"),
        )
    assert not (tmp_path / "failure" / "current_projection.json").exists()
    failed_cycle_dir = tmp_path / "failure" / "cycles"
    assert not failed_cycle_dir.exists() or not list(failed_cycle_dir.glob("*.json"))


def test_real_canonical_runner_discovery_path_remains_all_70_and_failure_isolated(tmp_path):
    fake = MemoryS3(_objects())
    snapshot, manifests = _freeze(tmp_path, fake)
    cycle = run_canonical_question_cycle(
        {"snapshot_id": snapshot.snapshot_id,
         "fingerprint": snapshot.snapshot_fingerprint,
         "investigation_epoch": snapshot.evidence_epoch,
         "changed_datasets": list(BOUND_DATASETS)},
        source=S3ResearchDataSource(bucket="question-cycle-test", client=fake),
        manifest_directory=manifests,
        state_directory=tmp_path / "real-runners",
    )
    projection = json.loads(
        (tmp_path / "real-runners" / "current_projection.json").read_text())
    assert cycle.total_questions == 70
    assert len(projection["questions"]) == 70
    assert projection["questions"]["S1"]["result"]["status"] == ALIAS_OR_SUPERSEDED
    assert projection["questions"]["L6"]["result"]["status"] == "UNIMPLEMENTED"
