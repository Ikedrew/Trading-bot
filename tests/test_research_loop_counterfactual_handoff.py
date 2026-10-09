"""Wire 1: the production research loop must hand its governed counterfactual
evidence into the Q71 worker.

The autonomy audit found that the continuous loop produced a governed
counterfactual evidence artifact (plus its snapshot-pinned membership) but never
passed either into ``run_generated_question_worker``.  The candidate-capable
production Q71 family ``GOVERNED_EXIT_POLICY_COUNTERFACTUAL`` ->
``q71.counterfactual.governed_exit_policy`` therefore could not consume the
evidence the same cycle had already produced.

These regressions drive the *real* production loop
(``run_continuous_research_cycle``) with the *real* producers, the *real*
governed registry, the *real* worker and the *real* downstream bridge.  Nothing
is reconstructed off-path: the artifact the worker receives is exactly the one
the same cycle froze.

The analysable fixture is a real governed HD09 lifecycle population whose M5
candles sit inside the frontier window, so the candidate-capable evaluator runs
to a genuine COMPLETE / SCIENTIFICALLY_MEANINGFUL result.
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.production_data_contract import s3_base_prefix
from research_engine.control_plane.governed_counterfactual_evidence import (
    MISSING_COUNTERFACTUAL_EVIDENCE,
    STALE_FRONTIER,
    SUPERSEDED_EVIDENCE,
    GovernedCounterfactualBinding,
    verify_governed_counterfactual_binding,
)
from research_engine.data_access.s3_source import S3ResearchDataSource
from research_engine.lifecycle.research_agenda_store import ResearchAgendaStore
from research_engine.v10.continuous.generated_question_result import (
    GeneratedQuestionResultStore,
)
from research_engine.v10.continuous.q71_production_registry import (
    write_production_evaluator_registry,
)
from research_engine.v10.continuous.q71_worker import GeneratedExecutionPolicy
from research_engine.v10.investigation_snapshot import (
    freeze_investigation_snapshot, save_investigation_snapshot,
)
from research_engine.v10.optimisation.optimisation_registry import (
    OptimisationRegistry,
)

import research_engine.lifecycle.generated_research_store as generated_store_module
from tests.test_canonical_question_cycle import MemoryS3, _jsonl
from tests.test_governed_scientific_result import _objects
from tests.test_production_observation_coverage import _loop_fixtures
from tests.test_q71_candidate_capability import rows_for

EVALUATOR_KEY = "q71.counterfactual.governed_exit_policy"
GOVERNED_POLICY_ID = "REDUCED_TP_0_50R_V1"
FRONTIER_START = "2026-09-08"
FRONTIER_END = "2026-09-25"


def _events_objects(candles) -> dict[str, str]:
    """Governed ``events_v1`` M5 objects grouped by the date of each bar."""
    grouped: dict[str, list] = {}
    for row in candles:
        day = datetime.fromtimestamp(
            row["payload"]["ts"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        grouped.setdefault(day, []).append(row)
    return {
        f"{s3_base_prefix('events')}/schema_version=events_v1"
        f"/symbol=EURUSD/date={day}/part-000.jsonl": _jsonl(*rows)
        for day, rows in grouped.items()
    }


class Wire1Env:
    """One frozen frontier whose governed evidence makes the candidate real."""

    def __init__(self, tmp_path: Path) -> None:
        self.root = Path(tmp_path)
        self.rows = rows_for()
        objects = _objects(self.rows[0])
        objects.update(_events_objects(self.rows[1]))
        self.client = MemoryS3(objects)
        self.source = S3ResearchDataSource(
            bucket="question-cycle-test", client=self.client)
        self.snapshot = freeze_investigation_snapshot(
            start_date=FRONTIER_START, end_date=FRONTIER_END, source=self.source)
        self.manifests = self.root / "manifests"
        save_investigation_snapshot(
            self.snapshot, self.manifests / f"{self.snapshot.snapshot_id}.json")
        self.registry = write_production_evaluator_registry(
            self.root / "evaluator_registry.json")

    def kwargs(self, **overrides) -> dict:
        state, question, frontier, bridge = _loop_fixtures(
            self.root, snapshot_id=self.snapshot.snapshot_id)
        base = dict(
            state_root=self.root / "continuous",
            frontier_runner=lambda **_: frontier,
            question_runner=lambda value, **_: question,
            bridge_runner=lambda value, **_: bridge,
            question_kwargs={"state_directory": state},
            bridge_kwargs={
                "scientific_state_directory": self.root / "science",
                "optimisation_registry_directory": self.root / "registry"},
            q71_kwargs={
                "agenda_store": ResearchAgendaStore(self.root / "agenda.json")},
            q71_capacity=10,
            q71_evaluator_registry=self.registry,
            q71_execution_policy=GeneratedExecutionPolicy(max_questions_per_run=10),
            q71_manifest_directory=self.manifests,
            observation_space_manifest_directory=self.manifests,
            observation_space_directory=self.root / "observation_space",
            production_coverage_directory=self.root / "production_coverage",
            production_coverage_store_path=self.root / "research_coverage.json",
            q71_evidence_source=self.source,
            candle_authority_directory=self.root / "candle_authority",
            counterfactual_evidence_directory=self.root / "counterfactual_evidence",
            max_validation_jobs=0)
        base.update(overrides)
        return base

    def projection(self) -> dict:
        from research_engine.v10.continuous.research_projection import (
            ResearchProjectionStore,
        )

        return ResearchProjectionStore(
            self.root / "continuous" / "projection").load_latest()

    def coverage(self) -> dict:
        return self.projection()["observation_coverage"]

    def counterfactual_result(self):
        store = GeneratedQuestionResultStore(
            self.root / "continuous" / "q71_results")
        for result in store.results():
            if result.evaluator_key == EVALUATOR_KEY:
                return result
        return None

    def counterfactual_execution_state(self):
        """The worker's operational outcome row for the candidate-capable family.

        Admission refusals write no immutable result (that would be unsupported
        science), so they are observed through the execution store.
        """
        from research_engine.v10.continuous.generated_question_result import (
            GeneratedQuestionExecutionStore,
        )

        store = GeneratedQuestionExecutionStore(
            self.root / "continuous" / "q71_execution_state.json")
        for row in store.states().values():
            if row.get("evaluator_key") == EVALUATOR_KEY:
                return row
        return None

    def registry_view(self) -> OptimisationRegistry:
        registry = OptimisationRegistry(str(self.root / "registry"))
        registry.load()
        return registry


@pytest.fixture
def wire1(tmp_path, monkeypatch) -> Wire1Env:
    env = Wire1Env(tmp_path)
    # The loop's orchestration and its worker must share one governed generated
    # store; the loop does not parameterise the worker's store, so pin the
    # module default for the duration of the test.
    monkeypatch.setattr(generated_store_module, "DEFAULT_STORE_PATH",
                        tmp_path / "generated.json")
    return env


def _spy_worker(monkeypatch) -> dict:
    import research_engine.v10.continuous.research_loop as research_loop

    captured: dict = {"calls": 0}
    real = research_loop.run_generated_question_worker

    def spy(**kwargs):
        captured["calls"] += 1
        captured["kwargs"] = dict(kwargs)
        return real(**kwargs)

    monkeypatch.setattr(research_loop, "run_generated_question_worker", spy)
    return captured


def _run(env: Wire1Env, **overrides):
    from research_engine.v10.continuous.research_loop import (
        run_continuous_research_cycle,
    )

    return run_continuous_research_cycle(**env.kwargs(**overrides))


def _tamper(binding: GovernedCounterfactualBinding, **changes):
    return dataclasses.replace(binding, **changes)


#: Report fields that record wall-clock time, not science.
_VOLATILE_REPORT_KEYS = frozenset({
    "generated", "generated_at", "evaluated_at", "recorded_at",
    "produced_at", "timestamp", "as_of",
})


def _strip_volatile(value):
    """Drop wall-clock provenance so two runs compare on science alone."""
    if isinstance(value, dict):
        return {key: _strip_volatile(item) for key, item in value.items()
                if key not in _VOLATILE_REPORT_KEYS}
    if isinstance(value, list):
        return [_strip_volatile(item) for item in value]
    return value


# -- Step 6/8: the integrated production-loop handoff ------------------------

def test_loop_hands_its_own_cycle_artifact_and_binding_to_the_worker(
    wire1, monkeypatch,
):
    """The production loop passes the SAME cycle's frozen evidence to the worker."""
    captured = _spy_worker(monkeypatch)
    result = _run(wire1)

    assert result.cycle_outcome == "COMPLETED", (
        result.failure_stage, result.failure_reason)
    assert result.stage_statuses["OBSERVATION_SPACE"] == "COMPLETED"
    assert result.stage_statuses["Q71_EXECUTION"] in {
        "COMPLETED", "COMPLETED_WITH_REVIEW_REQUIRED"}
    # The loop invoked the worker exactly once, through the real code path.
    assert captured["calls"] == 1

    kwargs = captured["kwargs"]
    artifact = kwargs["governed_counterfactual_evidence"]
    binding = kwargs["governed_counterfactual_binding"]
    assert artifact is not None
    assert binding is not None

    # The handoff is byte-for-byte the artifact the SAME cycle froze upstream.
    cycle_state = wire1.coverage()["counterfactual_evidence"]
    assert cycle_state["present"] is True
    assert cycle_state["scientifically_analysable"] is True
    assert cycle_state["candle_authority_id"]
    assert artifact.dataset_id == cycle_state["dataset_id"]
    assert artifact.content_digest == cycle_state["content_digest"]

    # The membership is pinned to THIS frontier's own snapshot identity.
    assert binding.snapshot_id == wire1.snapshot.snapshot_id
    assert binding.content_digest == artifact.content_digest
    verify_governed_counterfactual_binding(
        binding, snapshot_id=wire1.snapshot.snapshot_id,
        snapshot_fingerprint=wire1.snapshot.snapshot_fingerprint,
        investigation_epoch=wire1.snapshot.evidence_epoch, evidence=artifact)

    # The candidate-capable evaluator consumed that evidence: only a verified,
    # analysable frozen artifact can produce a COMPLETE scientific result.
    row = wire1.counterfactual_result()
    assert row is not None
    assert row.execution_status == "COMPLETE"
    assert row.scientific_status == "SCIENTIFICALLY_MEANINGFUL"
    assert row.evidence_class == "GOVERNED_EXIT_POLICY_COUNTERFACTUAL"
    assert row.evidence_datasets == ("shadow_runtime",)
    assert row.governed_scientific_metrics["governed_policy_id"] == GOVERNED_POLICY_ID
    assert row.governed_scientific_metrics["scientifically_meaningful"] is True

    # Downstream: the shared bridge still yields finding -> hypothesis -> candidate.
    registry = wire1.registry_view()
    candidates = registry.list_candidates()
    assert candidates, "the candidate-capable result created no candidate"
    assert registry.list_hypotheses()
    candidate = candidates[0]
    assert candidate.status == "PROPOSED"
    assert candidate.policy_id == GOVERNED_POLICY_ID
    assert candidate.shadow_binding.get("live_approved", False) is False


# -- Step 7: fail-closed proofs (the loop passes absence/bad identity, the
#    worker still refuses to admit anything it cannot verify) ----------------

def test_loop_passes_absence_explicitly_and_keeps_fail_closed(wire1, monkeypatch):
    """No producer -> the loop hands over explicit absence, never a fallback."""
    captured = _spy_worker(monkeypatch)
    result = _run(wire1, counterfactual_evidence_producer=None)

    assert result.cycle_outcome == "COMPLETED", (
        result.failure_stage, result.failure_reason)
    kwargs = captured["kwargs"]
    assert kwargs["governed_counterfactual_evidence"] is None
    assert kwargs["governed_counterfactual_binding"] is None
    state = wire1.coverage()["counterfactual_evidence"]
    assert state["present"] is False
    assert state["fail_closed_reason"] == "COUNTERFACTUAL_PRODUCER_DISABLED"
    # The worker still ran and still refused to invent evidence.
    row = wire1.counterfactual_result()
    assert row is not None
    assert row.execution_status != "COMPLETE"
    assert row.scientific_status == "NOT_SCIENTIFICALLY_RESOLVED"


def test_loop_refuses_a_membership_from_another_frontier(wire1, monkeypatch):
    """A binding minted for another snapshot is passed through and refused."""
    import research_engine.v10.continuous.research_loop as research_loop

    real_bind = research_loop.bind_counterfactual_evidence

    def foreign(artifact, *, bound_at):
        return _tamper(real_bind(artifact, bound_at=bound_at),
                       snapshot_id="ISNAP-OTHER-FRONTIER")

    monkeypatch.setattr(research_loop, "bind_counterfactual_evidence", foreign)
    captured = _spy_worker(monkeypatch)
    result = _run(wire1)

    assert result.cycle_outcome == "COMPLETED", (
        result.failure_stage, result.failure_reason)
    kwargs = captured["kwargs"]
    assert kwargs["governed_counterfactual_evidence"] is not None
    assert kwargs["governed_counterfactual_binding"].snapshot_id == \
        "ISNAP-OTHER-FRONTIER"
    row = wire1.counterfactual_execution_state()
    assert row is not None
    assert row["execution_status"] == "BLOCKED"
    assert row["reason_code"] == STALE_FRONTIER


def test_loop_refuses_evidence_that_arrives_without_a_membership(
    wire1, monkeypatch,
):
    """An artifact with no binding fails closed instead of being admitted."""
    import research_engine.v10.continuous.research_loop as research_loop

    monkeypatch.setattr(research_loop, "bind_counterfactual_evidence",
                        lambda artifact, *, bound_at: None)
    captured = _spy_worker(monkeypatch)
    result = _run(wire1)

    assert result.cycle_outcome == "COMPLETED", (
        result.failure_stage, result.failure_reason)
    kwargs = captured["kwargs"]
    assert kwargs["governed_counterfactual_evidence"] is not None
    assert kwargs["governed_counterfactual_binding"] is None
    row = wire1.counterfactual_execution_state()
    assert row is not None
    assert row["execution_status"] == "BLOCKED"
    assert row["reason_code"] == MISSING_COUNTERFACTUAL_EVIDENCE


def test_loop_refuses_a_tampered_membership_digest(wire1, monkeypatch):
    """A membership whose digest disagrees with the artifact fails closed."""
    import research_engine.v10.continuous.research_loop as research_loop

    real_bind = research_loop.bind_counterfactual_evidence

    def tampered(artifact, *, bound_at):
        return _tamper(real_bind(artifact, bound_at=bound_at),
                       content_digest="0" * 64)

    monkeypatch.setattr(research_loop, "bind_counterfactual_evidence", tampered)
    captured = _spy_worker(monkeypatch)
    result = _run(wire1)

    assert result.cycle_outcome == "COMPLETED", (
        result.failure_stage, result.failure_reason)
    assert captured["kwargs"]["governed_counterfactual_binding"].content_digest \
        == "0" * 64
    row = wire1.counterfactual_execution_state()
    assert row is not None
    assert row["execution_status"] == "BLOCKED"
    assert row["reason_code"] == SUPERSEDED_EVIDENCE


# -- Step 5: the three non-candidate production families are unaffected ------

def test_non_candidate_evaluators_ignore_counterfactual_kwargs():
    """The counterfactual kwargs never alter the other three families."""
    from research_engine.experiments.q71_production_evaluators import (
        completed_shadow_lifecycle_expectancy,
        governed_exit_path_distribution,
        session_conditioned_execution_slippage,
    )

    shadow = rows_for()[0]
    sentinel = {"this": "is not governed evidence"}
    cases = (
        (completed_shadow_lifecycle_expectancy,
         {"generated_question_id": "GEN-NC-1",
          "datasets": {"shadow_runtime": shadow}}),
        (governed_exit_path_distribution,
         {"generated_question_id": "GEN-NC-2",
          "datasets": {"shadow_runtime": shadow}}),
        (session_conditioned_execution_slippage,
         {"generated_question_id": "GEN-NC-3",
          "datasets": {"execution_results": shadow,
                       "execution_context": shadow}}),
    )
    for runner, base in cases:
        without = runner(**base)
        with_extra = runner(
            **base,
            governed_counterfactual_evidence=sentinel,
            governed_counterfactual_binding=sentinel)
        # Identical science; only the wall-clock provenance differs.
        assert _strip_volatile(with_extra) == _strip_volatile(without)


# -- Step 7.8: the handoff adds no recomputation -----------------------------

def test_handoff_reuses_the_single_cycle_production(wire1, monkeypatch):
    """One cycle produces the artifact once and hands over exactly that one."""
    from research_engine.control_plane.governed_counterfactual_evidence import (
        build_governed_counterfactual_evidence as real_producer,
    )

    calls = {"n": 0}

    def counting(**kwargs):
        calls["n"] += 1
        return real_producer(**kwargs)

    captured = _spy_worker(monkeypatch)
    result = _run(wire1, counterfactual_evidence_producer=counting)

    assert result.cycle_outcome == "COMPLETED", (
        result.failure_stage, result.failure_reason)
    # Exactly one production, one worker call: the handoff recomputes nothing.
    assert calls["n"] == 1
    assert captured["calls"] == 1
    handed = captured["kwargs"]["governed_counterfactual_evidence"]
    assert handed.dataset_id == \
        wire1.coverage()["counterfactual_evidence"]["dataset_id"]
