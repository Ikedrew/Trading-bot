"""
Wave 3B — Canonical eligible-finding intake for the research cycle (CASE B).

ROOT CAUSE (locked):
    ``ResearchCycleRunner.run_cycle()`` built the investigation intake only from
    the triggers returned by *this* cycle's ``_detect_findings()`` call::

        triggers = self._detect_findings(engine, ...)
        eligible = [t for t in triggers if t.status == TriggerStatus.ELIGIBLE]

    Canonical findings persisted by earlier cycles hydrate into
    ``FindingTriggerEngine`` through ``__init__ → _load()`` (visible via
    ``engine.all_triggers()``) but were never part of the freshly detected list,
    so persisted canonical findings could never reach ``_investigate_eligible()``.

These tests prove the repaired intake:

    persisted/fresh canonical findings
        → unique by canonical ``trigger_id``
        → ELIGIBLE only
        → _investigate_eligible()
        → hypothesis → registration → experiment → investigation → conclusion
        → ONLY ConclusionType.VALIDATED → create_optimisation_candidate()
        → CandidateRecord → CandidateRegistry

Isolation:
    Every persistence surface used by the chain (finding trigger store, cycle
    state/lock/audit, hypothesis registry, experiment catalogue, candidate
    registry, research reports) is redirected to a temporary directory. No
    production artefact is read-modified or written.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, ".")

import research_engine.lifecycle.research_cycle_runner as runner_module
from research_engine.lifecycle.experiment_protocol import ExperimentResult
from research_engine.lifecycle.finding_trigger import (
    EligibilityConfig,
    ExecutionMode,
    FindingTrigger,
    FindingTriggerEngine,
    TriggerCategory,
    TriggerStatus,
)
from research_engine.lifecycle.hypothesis import (
    ConclusionType,
    Hypothesis,
    HypothesisCategory,
)

# ─── REAL collaborators, captured at import time (before any monkeypatching) ──
from research_engine.lifecycle.orchestrator import (
    ResearchOrchestrator as _RealOrchestrator,
)
from research_engine.lifecycle.research_cycle_runner import (
    ResearchCycleConfig,
    ResearchCycleRunner,
)
from research_engine.v10.candidates.candidate_registry import (
    CandidateRegistry as _RealCandidateRegistry,
)

# Canonical question finding identity, as produced by the canonical
# ``detect_from_finding()`` contract: finding_id == question_id, source ==
# f"research_question_{question_id}", evidence carries question_id.
CANONICAL_QUESTION_ID = "Q-WAVE3B-CANON-1"
CANONICAL_FINDING_ID = "Q-WAVE3B-CANON-1"
CANONICAL_PATTERN = "WAVE3B_CANON_PAT"


# ═══════════════════════════════════════════════════════════════════════════════
# FIXTURES / FAKES
# ═══════════════════════════════════════════════════════════════════════════════

class _FakeDurability:
    """Gap-8 durability routed to an in-memory fake (no checkpoint writes)."""

    def restore_if_needed(self):
        return SimpleNamespace(status="skipped", checkpoint_id="", error="")

    def checkpoint(self, **kwargs):
        return SimpleNamespace(
            status="durable", error="", to_dict=lambda: {"status": "durable"})


def _fake_snapshot(tmp_path: Path):
    """Gap-6 snapshot routed to a temp path (no production snapshot baseline)."""
    snapshot_file = tmp_path / "snapshots" / "wave3b_snapshot.json"
    snapshot_file.parent.mkdir(parents=True, exist_ok=True)
    return ({"snapshot_file": str(snapshot_file)},
            {"material_change": False},
            "baseline")


def _temp_registry(directory: Path):
    """The real CandidateRegistry bound to a temporary directory."""
    class _TempCandidateRegistry(_RealCandidateRegistry):
        def __init__(self, storage_dir: str | None = None):
            super().__init__(storage_dir=str(directory))

    return _TempCandidateRegistry


class _RecordingOrchestrator:
    """Records the governed investigation pipeline without running experiments.

    ``detect_and_register`` returns a real ``Hypothesis`` so provenance and
    lifecycle links are genuine; no production persistence is touched.
    """

    def __init__(self, conclusion: str = "REJECTED"):
        self._conclusion = conclusion
        self.detect_calls: list[dict] = []
        self.investigate_calls: list[tuple[str, str]] = []
        self.hypotheses: list[Hypothesis] = []

    def detect_and_register(self, **kwargs) -> Hypothesis:
        self.detect_calls.append(kwargs)
        hypothesis = Hypothesis(
            title=kwargs.get("title", ""),
            description=kwargs.get("description", ""),
            category=kwargs.get("category", HypothesisCategory.OTHER),
            claim=kwargs.get("claim", ""),
            null_hypothesis=kwargs.get("null_hypothesis", ""),
            source=kwargs.get("source", ""),
            source_finding_id=kwargs.get("source_finding_id", ""),
            multiple_testing_count=kwargs.get("multiple_testing_count", 1),
        )
        self.hypotheses.append(hypothesis)
        return hypothesis

    def investigate(self, hypothesis, experiment_type, experiment_definition):
        self.investigate_calls.append((hypothesis.hypothesis_id, experiment_type))
        return SimpleNamespace(
            hypothesis_id=hypothesis.hypothesis_id,
            experiment_id=f"EXP-{hypothesis.hypothesis_id[-8:]}",
            experiment_type=experiment_type,
            status="complete",
            conclusion=self._conclusion,
        )


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    """Isolated persistence for the whole lifecycle chain.

    Every file the runner / engine / orchestrator writes is redirected into
    ``tmp_path``: no production trigger state, candidate registry, hypothesis
    registry, dataset or snapshot is touched.
    """
    import research_engine.lifecycle.candidate_activation_gate as activation_gate
    import research_engine.lifecycle.candidate_auto_evaluator as auto_evaluator
    import research_engine.lifecycle.cycle_snapshot as cycle_snapshot
    import research_engine.lifecycle.experiment_catalogue as catalogue
    import research_engine.lifecycle.finding_trigger as finding_trigger
    import research_engine.lifecycle.investigation_contracts as contracts
    import research_engine.lifecycle.orchestrator as orchestrator
    import research_engine.lifecycle.registry as registry
    import research_engine.lifecycle.state_durability as durability
    import research_engine.v10.candidates.candidate_registry as candidate_registry

    # Cycle state / lock / audit
    monkeypatch.setattr(runner_module, "_STATE_DIR", tmp_path)
    monkeypatch.setattr(runner_module, "_STATE_FILE", tmp_path / "cycle_state.json")
    monkeypatch.setattr(runner_module, "_LOCK_FILE", tmp_path / "research_cycle.lock")

    # Canonical finding trigger store
    monkeypatch.setattr(finding_trigger, "_TRIGGER_DIR", tmp_path)
    monkeypatch.setattr(finding_trigger, "_TRIGGER_FILE",
                        tmp_path / "finding_triggers.json")

    # Hypothesis registry (InvestigationRegistry)
    monkeypatch.setattr(registry, "_REGISTRY_DIR", tmp_path)
    monkeypatch.setattr(registry, "_REGISTRY_FILE", tmp_path / "hypotheses.json")
    monkeypatch.setattr(registry, "_AUDIT_LOG", tmp_path / "lifecycle_audit.jsonl")

    # Experiment catalogue
    monkeypatch.setattr(catalogue, "_CATALOGUE_DIR", tmp_path)
    monkeypatch.setattr(catalogue, "_CATALOGUE_FILE", tmp_path / "catalogue.json")
    monkeypatch.setattr(catalogue, "_AUDIT_LOG", tmp_path / "catalogue_audit.jsonl")

    # Research reports (orchestrator)
    monkeypatch.setattr(orchestrator, "_REPORT_DIR", tmp_path / "reports")

    # Candidate registry
    candidates_dir = tmp_path / "candidates"
    monkeypatch.setattr(candidate_registry, "CandidateRegistry",
                        _temp_registry(candidates_dir))

    # Durability / snapshot / post-cycle observation hooks: no side effects
    monkeypatch.setattr(durability, "ResearchStateDurability",
                        lambda *args, **kwargs: _FakeDurability())
    monkeypatch.setattr(cycle_snapshot, "record_cycle",
                        lambda **kwargs: _fake_snapshot(tmp_path))
    monkeypatch.setattr(activation_gate, "activate_eligible_candidates",
                        lambda: SimpleNamespace(candidates_activated=0,
                                                to_dict=lambda: {}))
    monkeypatch.setattr(auto_evaluator, "auto_evaluate_candidates",
                        lambda: SimpleNamespace(candidates_evaluated=0,
                                                to_dict=lambda: {}))

    # The external knowledge-map rejection lookup is a production input, not
    # part of the intake contract under test: pin it off so screening is
    # deterministic and hermetic. All other screening rules stay canonical.
    monkeypatch.setattr(FindingTriggerEngine, "_already_rejected",
                        lambda self, trigger: False)

    return SimpleNamespace(tmp=tmp_path, candidates_dir=candidates_dir,
                           contracts=contracts,
                           orchestrator_module=orchestrator,
                           trigger_file=tmp_path / "finding_triggers.json")


def _install_cycle_collaborators(monkeypatch, isolated, *, detected, orchestrator):
    """Route the cycle's detection + investigation collaborators deterministically.

    Detection is pinned to ``detected`` (never the live universe / S3), the
    orchestrator is the recording fake, and investigation contracts are stubbed
    so the subject under test is the *intake → pipeline wiring*, not experiment
    execution (covered by the existing lifecycle suites).
    """
    contracts = isolated.contracts
    monkeypatch.setattr(contracts, "get_contract", lambda category: SimpleNamespace(
        supported=True,
        experiment_type="DIRECTION_INVERSION",
        unsupported_reason="",
    ))
    monkeypatch.setattr(contracts, "build_experiment_from_trigger",
                        lambda trigger, **kwargs: (SimpleNamespace(), ""))
    monkeypatch.setattr(isolated.orchestrator_module, "ResearchOrchestrator",
                        lambda: orchestrator)
    monkeypatch.setattr(runner_module.ResearchCycleRunner, "_scan_population",
                        lambda self: {"total_patterns": 0, "patterns": {},
                                      "fingerprint": "wave3b"})
    monkeypatch.setattr(runner_module.ResearchCycleRunner, "_detect_findings",
                        lambda self, engine, pattern_stats: list(detected))
    return orchestrator


def _engine() -> FindingTriggerEngine:
    return FindingTriggerEngine(
        mode=ExecutionMode.DETECT_AND_INVESTIGATE,
        config=EligibilityConfig(min_sample_size=30),
    )


def _canonical_question_trigger(*, trigger_id: str = "",
                                finding_id: str = CANONICAL_FINDING_ID,
                                question_id: str = CANONICAL_QUESTION_ID,
                                pattern: str = CANONICAL_PATTERN,
                                sample_size: int = 60) -> FindingTrigger:
    """A canonical question finding, shaped exactly like detect_from_finding()."""
    return FindingTrigger(
        trigger_id=trigger_id,
        finding_id=finding_id,
        source=f"research_question_{question_id}",
        category=TriggerCategory.POOR_PATTERN_PERFORMANCE,
        title="Canonical question finding: pattern shows catastrophic performance",
        observation="Mean R=-0.420, WR=8.0%, N=60",
        evidence={
            "mean_r": -0.42,
            "win_rate": 0.08,
            "n": sample_size,
            "question_id": question_id,
            "source_report_status": "COMPLETE",
            "source_report_recommendation": "NEGATIVE_EDGE",
        },
        confidence="MEDIUM",
        sample_size=sample_size,
        evidence_maturity="MEDIUM",
        trigger_reason="Canonical question result was negative with sufficient data",
        suggested_claim=("Inverting the canonical pattern direction produces "
                         "positive expected value"),
        suggested_null="Canonical pattern direction has no systematic effect on outcome",
        suggested_patterns=[pattern],
    )


def _persist_eligible(engine: FindingTriggerEngine, trigger: FindingTrigger):
    """Persist through the engine's canonical screen → store contract."""
    stored = engine._screen(trigger)
    assert stored is not None, (
        f"trigger unexpectedly not eligible: {trigger.dismissed_reason}")
    assert stored.status == TriggerStatus.ELIGIBLE
    return stored


def _persist_with_status(engine: FindingTriggerEngine, trigger: FindingTrigger,
                         status: TriggerStatus) -> FindingTrigger:
    """Persist a canonical finding, then move it to a non-eligible lifecycle state.

    Canonical transition APIs are used where they exist; DISMISSED / BLOCKED
    have no marker API, so the canonical store is used directly.
    """
    _persist_eligible(engine, trigger)
    if status == TriggerStatus.REGISTERED:
        engine.mark_registered(trigger.trigger_id, "H-PREEXISTING")
    elif status == TriggerStatus.INVESTIGATING:
        engine.mark_investigating(trigger.trigger_id)
    elif status == TriggerStatus.COMPLETED:
        engine.mark_completed(trigger.trigger_id)
    else:
        trigger.status = status
        engine._store(trigger)
    stored = engine.get(trigger.trigger_id)
    assert stored.status == status
    return stored


def _cycle_config(mode: ExecutionMode = ExecutionMode.DETECT_AND_INVESTIGATE):
    return ResearchCycleConfig(mode=mode, min_cycle_interval_seconds=0.0,
                               max_investigations_per_cycle=5)


def _persisted_trigger_state(isolated, trigger_id: str) -> dict:
    """Read a trigger's persisted canonical record (status / provenance)."""
    data = json.loads(isolated.trigger_file.read_text(encoding="utf-8"))
    return data["triggers"][trigger_id]
# ═══════════════════════════════════════════════════════════════════════════════
# TEST 1 — persisted canonical ELIGIBLE trigger reaches investigation
# ═══════════════════════════════════════════════════════════════════════════════


class TestPersistedEligibleIntake:
    def test_persisted_canonical_eligible_trigger_reaches_investigation(
            self, isolated, monkeypatch):
        """A canonical ELIGIBLE trigger persisted before run_cycle() appears in
        the investigation intake — the CASE B defect it must catch."""
        _persist_eligible(_engine(),
                          _canonical_question_trigger(trigger_id="TRG-W3B-CANON-1"))

        orchestrator = _RecordingOrchestrator()
        _install_cycle_collaborators(monkeypatch, isolated, detected=[],
                                     orchestrator=orchestrator)

        result = ResearchCycleRunner(_cycle_config()).run_cycle()

        assert result.status == "complete"
        assert result.investigations_started == 1
        assert len(orchestrator.detect_calls) == 1
        assert len(orchestrator.investigate_calls) == 1

        # The hypothesis carries the canonical finding lineage.
        hypothesis = orchestrator.hypotheses[0]
        assert hypothesis.source_finding_id == CANONICAL_FINDING_ID
        assert hypothesis.source == "research_cycle:TRG-W3B-CANON-1"

        # The investigation pipeline canonically registers / completes it.
        persisted = _persisted_trigger_state(isolated, "TRG-W3B-CANON-1")
        assert persisted["status"] == TriggerStatus.COMPLETED.value
        assert persisted["hypothesis_id"] == hypothesis.hypothesis_id
        assert persisted["evidence"]["question_id"] == CANONICAL_QUESTION_ID

    def test_intake_merges_fresh_and_persisted_by_trigger_id(
            self, isolated, monkeypatch):
        """The intake helper itself merges both paths, unique by trigger_id."""
        persisted_engine = _engine()
        _persist_eligible(persisted_engine, _canonical_question_trigger(
            trigger_id="TRG-W3B-MERGE-1"))
        fresh = _canonical_question_trigger(trigger_id="TRG-W3B-MERGE-FRESH",
                                            finding_id="Q-WAVE3B-FRESH-1")
        fresh.status = TriggerStatus.ELIGIBLE  # detected list is post-screening
        # A freshly constructed engine hydrates the persisted triggers too.
        reloaded = _engine()
        runner = ResearchCycleRunner(_cycle_config())
        intake = runner._collect_eligible_intake(reloaded, [fresh])
        assert sorted(t.trigger_id for t in intake) == [
            "TRG-W3B-MERGE-1", "TRG-W3B-MERGE-FRESH"]


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 2 — fresh + persisted duplicate is investigated once
# ═══════════════════════════════════════════════════════════════════════════════


class TestDuplicateIntake:
    def test_fresh_and_persisted_duplicate_investigated_once(
            self, isolated, monkeypatch):
        """The same canonical trigger_id surfacing through both the persisted
        store and this cycle's detections must produce exactly one investigation."""
        _persist_eligible(_engine(),
                          _canonical_question_trigger(trigger_id="TRG-W3B-DUP"))
        duplicate = _canonical_question_trigger(trigger_id="TRG-W3B-DUP")

        orchestrator = _RecordingOrchestrator()
        _install_cycle_collaborators(monkeypatch, isolated,
                                     detected=[duplicate],
                                     orchestrator=orchestrator)

        result = ResearchCycleRunner(_cycle_config()).run_cycle()

        assert result.status == "complete"
        assert len(orchestrator.detect_calls) == 1
        assert len(orchestrator.investigate_calls) == 1
        assert result.investigations_started == 1


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 3 — persisted non-eligible trigger excluded
# ═══════════════════════════════════════════════════════════════════════════════


class TestNonEligibleExclusion:
    @pytest.mark.parametrize("status", [
        TriggerStatus.REGISTERED,
        TriggerStatus.INVESTIGATING,
        TriggerStatus.COMPLETED,
        TriggerStatus.DISMISSED,
        TriggerStatus.BLOCKED,
    ])
    def test_persisted_non_eligible_trigger_excluded(
            self, isolated, monkeypatch, status):
        """Triggers in REGISTERED / INVESTIGATING / COMPLETED / DISMISSED /
        BLOCKED must never be passed to _investigate_eligible()."""
        tid = f"TRG-W3B-{status.value}"
        _persist_with_status(
            _engine(),
            _canonical_question_trigger(
                trigger_id=tid,
                finding_id=f"Q-WAVE3B-{status.value}",
                question_id=f"Q-WAVE3B-{status.value}",
                pattern=f"WAVE3B_{status.value}_PAT"),
            status)
        # ELIGIBLE control proving the harness itself works.
        _persist_eligible(_engine(), _canonical_question_trigger(
            trigger_id="TRG-W3B-CONTROL", finding_id="Q-WAVE3B-CONTROL",
            question_id="Q-WAVE3B-CONTROL", pattern="WAVE3B_CONTROL_PAT"))

        orchestrator = _RecordingOrchestrator()
        _install_cycle_collaborators(monkeypatch, isolated, detected=[],
                                     orchestrator=orchestrator)

        result = ResearchCycleRunner(_cycle_config()).run_cycle()

        assert result.status == "complete"
        assert result.investigations_started == 1
        assert [c["source_finding_id"] for c in orchestrator.detect_calls] == [
            "Q-WAVE3B-CONTROL"]
        assert all(tid not in c["source"] for c in orchestrator.detect_calls)

        # Lifecycle state of the non-eligible trigger is untouched.
        assert _persisted_trigger_state(isolated, tid)["status"] == status.value


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 4 — trigger registration is idempotent
# ═══════════════════════════════════════════════════════════════════════════════


class TestRegistrationIdempotency:
    def test_registration_is_idempotent_across_cycles(self, isolated, monkeypatch):
        """After the first investigation moves a trigger out of ELIGIBLE, the
        next cycle must not create a second hypothesis for it."""
        _persist_eligible(_engine(),
                          _canonical_question_trigger(trigger_id="TRG-W3B-IDEM"))

        orchestrator1 = _RecordingOrchestrator()
        _install_cycle_collaborators(monkeypatch, isolated, detected=[],
                                     orchestrator=orchestrator1)
        result1 = ResearchCycleRunner(_cycle_config()).run_cycle()
        assert result1.status == "complete"
        assert len(orchestrator1.detect_calls) == 1

        # The lifecycle moved it out of ELIGIBLE — and that persisted.
        reloaded = _engine()
        assert reloaded.get("TRG-W3B-IDEM").status == TriggerStatus.COMPLETED

        # Cycle 2: the same persisted finding yields no second hypothesis.
        orchestrator2 = _RecordingOrchestrator()
        _install_cycle_collaborators(monkeypatch, isolated, detected=[],
                                     orchestrator=orchestrator2)
        result2 = ResearchCycleRunner(_cycle_config()).run_cycle()
        assert result2.status == "complete"
        assert orchestrator2.detect_calls == []
        assert result2.investigations_started == 0

        # The canonical REGISTERED marker only fires from ELIGIBLE, so a
        # stray re-registration can never resurrect a completed trigger.
        prior_hyp = reloaded.get("TRG-W3B-IDEM").hypothesis_id
        reloaded.mark_registered("TRG-W3B-IDEM", "H-SECOND")
        assert reloaded.get("TRG-W3B-IDEM").status == TriggerStatus.COMPLETED
        assert reloaded.get("TRG-W3B-IDEM").hypothesis_id == prior_hyp

        # Screen-level contract: re-detecting the same finding cannot mint a
        # second ELIGIBLE trigger (the engine dismisses the duplicate).
        rescreened = reloaded._screen(_canonical_question_trigger())
        assert rescreened is None
        assert reloaded.by_status(TriggerStatus.ELIGIBLE) == []
        assert reloaded.get("TRG-W3B-IDEM").status == TriggerStatus.COMPLETED


# ═══════════════════════════════════════════════════════════════════════════════
# MODE DEFAULT — unchanged (out of scope by design)
# ═══════════════════════════════════════════════════════════════════════════════


class TestModeDefaultUnchanged:
    def test_default_mode_remains_detect_only_and_investigates_nothing(
            self, isolated, monkeypatch):
        """The global default must stay DETECT_ONLY: persisted ELIGIBLE
        findings are only swept into the intake under DETECT_AND_INVESTIGATE."""
        assert ResearchCycleConfig().mode == ExecutionMode.DETECT_ONLY
        assert ResearchCycleRunner(_cycle_config(mode=ExecutionMode.DETECT_ONLY)
                                   )._config.mode == ExecutionMode.DETECT_ONLY

        _persist_eligible(_engine(),
                          _canonical_question_trigger(trigger_id="TRG-W3B-GUARD"))
        orchestrator = _RecordingOrchestrator()
        _install_cycle_collaborators(monkeypatch, isolated, detected=[],
                                     orchestrator=orchestrator)

        result = ResearchCycleRunner(
            _cycle_config(mode=ExecutionMode.DETECT_ONLY)).run_cycle()

        assert result.status == "complete"
        assert result.investigations_started == 0
        assert orchestrator.detect_calls == []
        assert _persisted_trigger_state(
            isolated, "TRG-W3B-GUARD")["status"] == TriggerStatus.ELIGIBLE.value
# ═══════════════════════════════════════════════════════════════════════════════
# TEST 5/6/7/8 — scientific gate, validated candidate, idempotency, provenance
# (real ResearchOrchestrator.create_optimisation_candidate + real registries,
# all persistence isolated by the fixture)
# ═══════════════════════════════════════════════════════════════════════════════


def _concluded_hypothesis(*, conclusion: ConclusionType,
                          hypothesis_id: str = "H-wave3b01",
                          finding_id: str = CANONICAL_FINDING_ID) -> Hypothesis:
    """A hypothesis shaped exactly like the governed pipeline produces it."""
    hypothesis = Hypothesis(
        hypothesis_id=hypothesis_id,
        title="Canonical question finding: pattern shows catastrophic performance",
        description="Mean R=-0.420, WR=8.0%, N=60",
        category=HypothesisCategory.OTHER,
        claim="Inverting the canonical pattern direction produces positive expected value",
        null_hypothesis="Canonical pattern direction has no systematic effect on outcome",
        source="research_cycle:TRG-W3B-CANON-1",
        source_finding_id=finding_id,
    )
    hypothesis.conclusion_type = conclusion
    hypothesis.conclusion_reason = f"test {conclusion.value.lower()} verdict"
    hypothesis.conclusion_confidence = "MEDIUM"
    return hypothesis


def _experiment_result(*, hypothesis_id: str = "H-wave3b01",
                       experiment_id: str = "EXP-wave3b01") -> ExperimentResult:
    return ExperimentResult(
        experiment_id=experiment_id,
        hypothesis_id=hypothesis_id,
        status="complete",
        n=120,
        mean_r=0.25,
        median_r=0.20,
        total_r=30.0,
        win_rate=0.55,
        std_dev=1.1,
        ci_lower=0.05,
        ci_upper=0.45,
        oos_n=48,
        oos_mean_r=0.18,
        survives_top20_removal=True,
        evidence_maturity="MEDIUM",
    )


def _candidates_in(isolated) -> list:
    return _RealCandidateRegistry(
        storage_dir=str(isolated.candidates_dir)).list_all()


class TestScientificGate:
    @pytest.mark.parametrize("conclusion", [
        ConclusionType.REJECTED,
        ConclusionType.INCONCLUSIVE,
        ConclusionType.SUPERSEDED,
    ])
    def test_non_validated_hypothesis_creates_zero_candidates(
            self, isolated, conclusion):
        orchestrator = _RealOrchestrator()
        created = orchestrator.create_optimisation_candidate(
            _concluded_hypothesis(conclusion=conclusion),
            _experiment_result())
        assert created is None
        assert _candidates_in(isolated) == []

    def test_unconcluded_hypothesis_creates_zero_candidates(self, isolated):
        hypothesis = _concluded_hypothesis(conclusion=ConclusionType.REJECTED)
        hypothesis.conclusion_type = None
        assert _RealOrchestrator().create_optimisation_candidate(
            hypothesis, _experiment_result()) is None
        assert _candidates_in(isolated) == []


# __WAVE3B_APPEND__

class TestValidatedCandidate:
    def test_validated_investigation_creates_exactly_one_candidate(self, isolated):
        orchestrator = _RealOrchestrator()
        hypothesis = _concluded_hypothesis(conclusion=ConclusionType.VALIDATED)
        result = _experiment_result()

        created = orchestrator.create_optimisation_candidate(hypothesis, result)

        assert created is not None
        assert created["candidate_id"] == "OPT-wave3b01"  # deterministic
        assert created["hypothesis_id"] == hypothesis.hypothesis_id
        assert created["status"] == "PROPOSED"  # governance: never auto-promoted

        stored = _candidates_in(isolated)
        assert len(stored) == 1
        assert stored[0].candidate_id == created["candidate_id"]
        assert stored[0].hypothesis_id == hypothesis.hypothesis_id


class TestCandidateIdempotency:
    def test_repeated_processing_creates_no_duplicate_candidate(self, isolated):
        orchestrator = _RealOrchestrator()
        hypothesis = _concluded_hypothesis(conclusion=ConclusionType.VALIDATED)
        result = _experiment_result()

        first = orchestrator.create_optimisation_candidate(hypothesis, result)
        second = orchestrator.create_optimisation_candidate(hypothesis, result)

        assert first is not None and second is not None
        assert first["candidate_id"] == second["candidate_id"] == "OPT-wave3b01"
        stored = _candidates_in(isolated)
        assert len(stored) == 1
        assert stored[0].candidate_id == "OPT-wave3b01"


class TestProvenance:
    def test_finding_lineage_preserved_end_to_end(self, isolated):
        orchestrator = _RealOrchestrator()
        hypothesis = _concluded_hypothesis(conclusion=ConclusionType.VALIDATED)
        result = _experiment_result()

        created = orchestrator.create_optimisation_candidate(hypothesis, result)

        assert created is not None
        assert hypothesis.source_finding_id == CANONICAL_FINDING_ID
        assert created["change_definition"]["source_finding_id"] == CANONICAL_FINDING_ID
        assert created["hypothesis_id"] == hypothesis.hypothesis_id
        assert created["change_definition"]["experiment_id"] == result.experiment_id

        stored = _candidates_in(isolated)[0]
        assert stored.change_definition["source_finding_id"] == CANONICAL_FINDING_ID

    def test_created_from_question_not_yet_connected(self, isolated):
        orchestrator = _RealOrchestrator()
        created = orchestrator.create_optimisation_candidate(
            _concluded_hypothesis(conclusion=ConclusionType.VALIDATED),
            _experiment_result())
        assert created is not None
        assert created["created_from_question"] == ""
        assert _candidates_in(isolated)[0].created_from_question == ""

