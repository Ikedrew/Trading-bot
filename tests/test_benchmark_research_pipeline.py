"""Narrow offline harness replay/collision regression; no fake runner reports."""
from copy import deepcopy

import pytest

from tools.benchmark_research_pipeline import bounded_registry
from tests.test_incremental_acquisition_replay import question_fixture
from research_engine.data_access.s3_source import S3ResearchDataSource
from research_engine.experiments.strategy_expectancy import run_e3
from research_engine.registry.baseline_manifest import BASELINE_QUESTION_IDS
from research_engine.v10.continuous import canonical_question_cycle as canonical
from research_engine.v10.continuous.question_cycle_state import (
    QuestionCycleStore, QuestionCycleStateError, immutable_json)


def test_bounded_registry_completed_replay_preserves_immutable_projection(tmp_path):
    fake, snapshot, manifests = question_fixture(tmp_path)
    registry = bounded_registry()
    assert {question.id for question in registry} == set(BASELINE_QUESTION_IDS)
    assert [question.id for question in registry if question.runner_module] == ["E3"]
    store = QuestionCycleStore(tmp_path / "questions")
    calls = []
    def execute(runner, question, population, context):
        calls.append(question.id)
        return canonical._execute_runner(runner, question, population, context)
    # question_fixture freezes with this bucket and the same source adapter.
    # The incremental-test helper belongs to a different source authority.
    kwargs = dict(source=S3ResearchDataSource(
                      bucket="question-cycle-test", client=fake,
                      verified_cache_directory=tmp_path / "bodies"),
                  manifest_directory=manifests, store=store,
                  registry=registry, runners={"E3": run_e3}, runner_executor=execute,
                  execution_revision="offline-real-e3-v1", force_inline_question_ids=("E3",))
    first = canonical.run_canonical_question_cycle(snapshot.snapshot_id, **kwargs)
    assert not first.failed_question_ids
    assert calls == ["E3"]
    immutable_path = store.projection_path(first.cycle_id)
    before = immutable_path.read_bytes()
    get_count = len(fake.get_calls)
    calls.clear()
    progress = []
    second = canonical.run_canonical_question_cycle(
        snapshot.snapshot_id, progress_callback=progress.append, **kwargs)
    assert second == first
    assert calls == []
    assert len(fake.get_calls) == get_count
    assert any(event.get("phase") == "COMPLETED_CYCLE_REPLAY" for event in progress)
    assert immutable_path.read_bytes() == before

    # A real content conflict must still be rejected, not treated as replay.
    conflict = deepcopy(store.load_projection(first.cycle_id))
    conflict["questions"]["E3"]["result"]["status"] = "BLOCKED"
    with pytest.raises(QuestionCycleStateError, match="IMMUTABLE_HISTORY_ID_COLLISION"):
        immutable_json(immutable_path, conflict)
    assert immutable_path.read_bytes() == before

    # An explicit changed execution revision cannot reuse the completed receipt.
    changed = dict(kwargs, execution_revision="offline-real-e3-v2")
    calls.clear()
    third = canonical.run_canonical_question_cycle(snapshot.snapshot_id, **changed)
    assert third.cycle_id != first.cycle_id
    assert calls == ["E3"]
    assert immutable_path.read_bytes() == before
