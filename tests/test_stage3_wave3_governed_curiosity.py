"""
Stage 3 / Wave 3 -- Governed Autonomous Curiosity Generators.

Focused tests only. Scope:
    - the three governed curiosity modes EXPANSION / INTERACTION / DESTRUCTIVE;
    - evidence-driven signals: identical signals give identical identities, a
      timestamp or note never moves identity, and free text alone is refused;
    - missing source provenance fails closed and is never fabricated;
    - the finding/evidence and candidate-reconsideration adapters preserve the
      provenance of the existing substrates;
    - Wave 1 admission is never bypassed (an unadmitted dimension cannot become
      an investigation-ready proposal);
    - Wave 2 is delegated to, never duplicated: PERMIT / WAITING_DATA / BLOCKED
      / REFUSE are recorded EXACTLY and only PERMIT may register;
    - Wave 0 registration, deduplication and reload determinism;
    - question != interaction != slice: ONE `GEN-*` per interaction, never one
      per slice;
    - recursion control: self-parenting and duplicate children are rejected;
    - no interaction enumeration, no search and no profit ranking exist;
    - no hypothesis, candidate, experiment or production path is created;
    - canonical-70 isolation.

These are TEST FIXTURES, not production research questions. No research,
experiment, hypothesis, finding, candidate or production behaviour is executed.
"""

from __future__ import annotations

import ast
import inspect
import re
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from research_engine.lifecycle import (
    curiosity_generator as generator_module,
    curiosity_proposal as proposal_module,
    curiosity_signal as signal_module,
)
from research_engine.lifecycle.curiosity_generator import (
    CuriosityOutcome,
    CuriosityProcessor,
    DestructiveEligibilityDecision,
    DestructiveEligibilityEvidenceFreeze,
    evaluate_destructive_eligibility,
    propose_destructive,
    propose_expansion,
    propose_interaction,
)
from research_engine.lifecycle.curiosity_proposal import (
    CURIOSITY_PROPOSAL_ID_PREFIX,
    CuriosityMode,
    CuriosityProposalError,
    CuriosityProposalValidationError,
    DestructiveTargetKind,
    GeneratedResearchProposal,
    is_curiosity_proposal_identity,
)
from research_engine.lifecycle.curiosity_signal import (
    CURIOSITY_SIGNAL_ID_PREFIX,
    CuriosityProvenanceError,
    CuriosityRecursionError,
    CuriositySignal,
    CuriositySignalType,
    CuriositySignalValidationError,
    is_curiosity_signal_identity,
    signal_from_candidate_reconsideration,
    signal_from_finding_trigger,
)
from research_engine.lifecycle.candidate_reconsideration import (
    CandidateReconsiderationDecision,
    ReconsiderationStatus,
)
from research_engine.lifecycle.dimension_registry import DimensionRegistry
from research_engine.lifecycle.eligibility_evidence_freeze import (
    CellSupport,
    CommonSupport,
    EligibilityEvidenceFreeze,
    common_support_from_lineage,
    fingerprint_for_records,
)
from research_engine.lifecycle.generated_research_identity import (
    GENERATED_RESEARCH_ID_PREFIX,
    GeneratedResearchKind,
    is_generated_research_id,
)
from research_engine.lifecycle.generated_research_isolation import (
    assert_canonical_70_intact,
    assert_generated_not_in_canonical_apis,
    assert_no_generated_canonical_collision,
    canonical_inventory,
)
from research_engine.lifecycle.generated_research_store import GeneratedResearchStore
from research_engine.lifecycle.finding_trigger import FindingTrigger, TriggerCategory
from research_engine.lifecycle.governed_dimension import (
    DimensionCoverage,
    GovernedDimension,
)
from research_engine.lifecycle.interaction_feasibility import (
    EligibilityReasonCode,
    FeasibilitySpecification,
)
from research_engine.lifecycle.progressive_depth_gate import (
    EligibilityState,
    ExpansionJustification,
    ExpansionJustificationType,
    evaluate_interaction_eligibility,
    parent_step_is_valid,
)
from research_engine.lifecycle.research_interaction import (
    InteractionSlice,
    ResearchInteraction,
    is_interaction_identity,
    is_slice_identity,
)
from research_engine.registry.research_question_models import (
    EvidenceAuthority,
    EvidenceProducer,
)

EVIDENCE_BOUNDARY = "2026-09-25T00:00:00Z"
T0 = "2026-09-25T09:00:00Z"



# ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ Fixtures ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬ÃƒÂ¢Ã¢â‚¬ÂÃ¢â€šÂ¬


def _dimension(key: str, field_path: str) -> GovernedDimension:
    return GovernedDimension.create(
        dimension_key=key,
        authority=EvidenceAuthority(
            dataset="decision_trace",
            schema_version="v1",
            producer=EvidenceProducer.DECISION_TRACE,
            field_path=field_path,
            semantic_meaning="declared test-time semantics",
        ),
    )


def _admitted_registry(*extra_unadmitted: str) -> DimensionRegistry:
    """A registry with four ADMITTED dimensions and any requested UNADMITTED ones."""
    registry = DimensionRegistry()
    for key, field_path in (
        ("regime", "market.regime"),
        ("horizon", "decision.trade_horizon"),
        ("strategy_family", "decision.strategy"),
        ("session", "market.session"),
    ):
        dimension = _dimension(key, field_path)
        registry.register(dimension)
        registry.admit(dimension, DimensionCoverage(1000, 900, True, True))
    for key in extra_unadmitted:
        # Registered but deliberately NOT admitted: authoritative coverage was
        # never established, so Wave 1 must keep it out of every interaction.
        registry.register(_dimension(key, f"market.{key}"))
    return registry


@pytest.fixture
def registry() -> DimensionRegistry:
    return _admitted_registry()


def _fingerprint(seed: int = 1):
    return fingerprint_for_records(
        [{"market.regime": "TRENDING", "t": index} for index in range(500 + seed)],
        dataset_id="wave3_test_population",
        population="joint_eligible",
    )


def _spec(**overrides) -> FeasibilitySpecification:
    base = dict(spec_key="wave3_explicit", min_common_observations=60)
    base.update(overrides)
    return FeasibilitySpecification.create(**base)


def _support(
    interaction: ResearchInteraction,
    *,
    joint: int = 300,
    population_ref: str = "population:joint_v1",
    fingerprint=None,
    **kwargs,
) -> CommonSupport:
    return common_support_from_lineage(
        interaction.dimensions,
        # Deliberately LARGE marginal lineage, far from the joint count, so no
        # test can accidentally pass on marginal arithmetic.
        {"total_records": 10000,
         "key_coverage": {d.authority.field_path: {"non_empty": 10000}
                          for d in interaction.dimensions}},
        joint_population_ref=population_ref,
        joint_usable_observations=joint,
        dataset_fingerprint=fingerprint if fingerprint is not None else _fingerprint(),
        **kwargs,
    )


def _freeze(
    interaction: ResearchInteraction,
    *,
    spec: FeasibilitySpecification | None = None,
    cell_support: tuple[CellSupport, ...] = (),
    parent_justification_identity: str | None = None,
    t0: str = T0,
    boundary: str = EVIDENCE_BOUNDARY,
    **support_kwargs,
) -> EligibilityEvidenceFreeze:
    return EligibilityEvidenceFreeze.create(
        interaction,
        _support(interaction, **support_kwargs),
        spec if spec is not None else _spec(),
        evidence_boundary=boundary,
        cell_support=cell_support,
        parent_justification_identity=parent_justification_identity,
        t0=t0,
    )


def _justification(
    parent: ResearchInteraction,
    child: ResearchInteraction,
    registry: DimensionRegistry,
    *,
    added_key: str,
    unresolved: bool = True,
    note: str = "",
) -> ExpansionJustification:
    """A governed Wave 2 expansion justification for ONE parent -> child step."""
    added_identity = next(
        identity for identity in set(child.dimension_identities)
        - set(parent.dimension_identities)
    )
    assert registry.get(added_key) is not None
    assert registry.get(added_key).dimension_identity == added_identity
    return ExpansionJustification.create(
        parent_interaction_identity=parent.interaction_identity,
        parent_depth=parent.depth,
        child_interaction_identity=child.interaction_identity,
        added_dimension_key=added_key,
        added_dimension_identity=added_identity,
        justification_type=(
            ExpansionJustificationType.UNRESOLVED_BETWEEN_SLICE_HETEROGENEITY),
        evidence_reference="finding:wave3-test",
        unresolved_structure=unresolved,
        evidence_boundary=EVIDENCE_BOUNDARY,
        note=note,
    )


# --- Machine-produced source objects (test doubles over the EXISTING substrates)
#
# These are NOT the real FindingTriggerEngine / decide_candidate_reconsideration
# call sites: constructing those would write to the repository's lifecycle log
# directories. They are minimal stand-ins carrying exactly the attributes the
# adapters read, so the adapter's provenance-preservation contract can be tested
# without any production I/O.


class _StubTrigger:
    """The attributes the finding adapter reads off an existing FindingTrigger."""

    def __init__(self, trigger_id="TRG-0001", finding_id="finding-42", source="baseline",
                 category="EXIT_INEFFICIENCY", sample_size=240, confidence="HIGH",
                 baseline_epoch="BASE-v1", detected_at="2026-09-25T08:00:00Z"):
        self.trigger_id = trigger_id
        self.finding_id = finding_id
        self.source = source
        self.category = category
        self.sample_size = sample_size
        self.confidence = confidence
        self.baseline_epoch = baseline_epoch
        self.detected_at = detected_at
        self.evidence = {}


class _StubReconsideration:
    """The attributes the reconsideration adapter reads off a real decision."""

    def __init__(self, reconsideration_id="R63A-abc123", candidate_id="CAND-7",
                 status="ELIGIBLE_FOR_RECONSIDERATION", reason_codes=(),
                 human_rejected=False, evaluation="REJECTED"):
        self.reconsideration_id = reconsideration_id
        self.candidate_id = candidate_id
        self.status = status
        self.reason_codes = tuple(
            reason_codes or (("HUMAN_REJECTION_NOT_OVERRIDDEN",) if human_rejected else ()))
        self.candidate_treatment_id = "TRT-9"
        self.historical_baseline_id = "BASE-v0"
        self.historical_baseline_config_hash = "cfg-v0"
        self.target_baseline_id = "BASE-v1"
        self.target_baseline_config_hash = "cfg-v1"
        self.impact_id = "IMP-3"
        self.continuity_id = "CNT-3"
        self.historical_candidate_status = "REJECTED"
        self.historical_evaluation_outcome = evaluation
        self.fresh_evidence_required = False
        self.limitations = ()


def _expansion_signal(registry: DimensionRegistry, *, key="horizon",
                      boundary=EVIDENCE_BOUNDARY, note="", observed_at="",
                      trigger_id="TRG-0001") -> CuriositySignal:
    """A machine-produced finding signal naming ONE admitted dimension."""
    return CuriositySignal.create(
        signal_type=CuriositySignalType.FINDING_EVIDENCE,
        source_ref=f"finding:{trigger_id}",
        source_identity=trigger_id,
        source_provenance={"adapter": "finding_trigger", "trigger_id": trigger_id,
                           "finding_id": "finding-42", "source": "baseline",
                           "category": "EXIT_INEFFICIENCY", "sample_size": 240},
        target_kind="research_subject",
        target_ref="exit_policy_performance",
        reason_code="UNRESOLVED_EXIT_BEHAVIOUR",
        evidence_reference="finding:finding-42",
        evidence_boundary=boundary,
        dimension_ref=registry.get(key).dimension_identity,
        note=note,
        observed_at=observed_at,
    )


def _destructive_signal(registry: DimensionRegistry, *, component="exit_rule:tpu",
                        kind=DestructiveTargetKind.EXIT_RULE,
                        boundary=EVIDENCE_BOUNDARY, reason=None,
                        target_ref="exit_rule:tpu") -> CuriositySignal:
    return CuriositySignal.create(
        signal_type=CuriositySignalType.CANDIDATE_RECONSIDERATION,
        source_ref="candidate:R63A-abc123",
        source_identity="R63A-abc123",
        source_provenance={"adapter": "candidate_reconsideration",
                           "reconsideration_id": "R63A-abc123",
                           "candidate_id": "CAND-7",
                           "status": "ELIGIBLE_FOR_RECONSIDERATION",
                           "human_decision_present": False},
        target_kind="system_component",
        target_ref=target_ref,
        reason_code=reason or "CANDIDATE_OUTCOME_REQUIRES_EXPLANATION",
        evidence_reference="candidate:CAND-7",
        evidence_boundary=boundary,
        component_ref=component,
        component_kind=kind.value,
        baseline_ref="BASE-v1",
    )



def _permitting_freeze(
    interaction: ResearchInteraction,
    *,
    spec: FeasibilitySpecification | None = None,
    cell_support: tuple[CellSupport, ...] = (),
    parent_justification_identity: str | None = None,
    boundary: str = EVIDENCE_BOUNDARY,
    **support_kwargs,
) -> EligibilityEvidenceFreeze:
    """A structurally COMPLETE freeze: the all-fixtures case is PERMIT-eligible.

    The committed Wave 2 gate requires an evidence boundary, a dataset
    fingerprint, an established joint population, a joinable evidence path and
    sufficient common support. Only the DECISION differs between states; the
    processor never manufactures any of these.
    """
    return _freeze(
        interaction,
        spec=spec,
        cell_support=cell_support,
        parent_justification_identity=parent_justification_identity,
        boundary=boundary,
        **support_kwargs,
    )


def _parent_permit(parent: ResearchInteraction, registry: DimensionRegistry):
    return evaluate_interaction_eligibility(
        parent, _permitting_freeze(parent), registry, t0=T0)


def _destructive_freeze(
    proposal: GeneratedResearchProposal,
    *,
    boundary: str | None = None,
    usable: int = 100,
    minimum: int = 60,
    justification: str | None = "COMPONENT_CHALLENGE_JUSTIFIED",
    fingerprint: str = "sha256:destructive-evidence-v1",
    blockers: tuple[str, ...] = (),
) -> DestructiveEligibilityEvidenceFreeze:
    return DestructiveEligibilityEvidenceFreeze(
        component_ref=proposal.component_ref,
        baseline_ref=proposal.baseline_ref,
        evidence_reference=proposal.evidence_reference,
        evidence_boundary=boundary or proposal.evidence_boundary,
        dataset_fingerprint=fingerprint,
        usable_observations=usable,
        minimum_observations=minimum,
        justification_code=justification,
        structural_blockers=blockers,
    )


# ═══ 1-2. Autonomous production from a machine signal ════════════════════════


def test_finding_signal_autonomously_produces_expansion_proposal(registry):
    """(1) A machine finding trigger -> CuriositySignal -> Expansion proposal."""
    signal = signal_from_finding_trigger(
        _StubTrigger(),
        target_kind="research_subject",
        target_ref="exit_policy_performance",
        evidence_boundary=EVIDENCE_BOUNDARY,
        dimension_ref=registry.get("horizon").dimension_identity,
    )
    assert signal.signal_type is CuriositySignalType.FINDING_EVIDENCE
    assert is_curiosity_signal_identity(signal.signal_identity)

    proposal = propose_expansion(signal, registry)
    assert proposal.mode is CuriosityMode.EXPANSION
    assert proposal.research_kind is GeneratedResearchKind.EXPANSION
    assert is_curiosity_proposal_identity(proposal.proposal_identity)
    # The dimension was chosen by the EVIDENCE, not by iterating the registry.
    assert proposal.proposed_interaction.dimension_keys == ("horizon",)
    assert proposal.added_dimension_identity == (
        registry.get("horizon").dimension_identity)
    # No human-authored question text anywhere in the proposal.
    assert proposal.target_ref == signal.target_ref
    assert proposal.reason_code == signal.reason_code


def test_candidate_reconsideration_signal_autonomously_produces_proposal():
    """(2) A candidate reconsideration outcome -> CuriositySignal -> proposal."""
    decision = _StubReconsideration()
    signal = signal_from_candidate_reconsideration(
        decision,
        target_kind="system_component",
        target_ref="treatment:COND-3",
        evidence_boundary=EVIDENCE_BOUNDARY,
    )
    assert signal.signal_type is CuriositySignalType.CANDIDATE_RECONSIDERATION
    # The adapter preserved the existing decision's OWN provenance.
    assert signal.source_ref == "candidate:R63A-abc123"
    assert signal.source_provenance["candidate_id"] == "CAND-7"
    assert signal.source_provenance["status"] == "ELIGIBLE_FOR_RECONSIDERATION"
    assert signal.reason_code == "CANDIDATE_OUTCOME_REQUIRES_EXPLANATION"

    destructive = propose_destructive(
        signal_from_candidate_reconsideration(
            decision,
            target_kind="system_component",
            target_ref="filter:volatility_filter",
            evidence_boundary=EVIDENCE_BOUNDARY,
            component_ref="filter:volatility_filter",
            component_kind="FILTER",
            baseline_ref="BASE-v1",
        ))
    assert destructive.mode is CuriosityMode.DESTRUCTIVE
    assert is_curiosity_proposal_identity(destructive.proposal_identity)
    # A candidate outcome supplies a SIGNAL only; it authorises no explanation.
    assert destructive.component_ref == "filter:volatility_filter"
    assert destructive.proposed_interaction is None


# ═══ 3-4. Determinism: timestamps and notes are not identity ════════════════


def test_identical_signal_produces_identical_proposal_identity(registry):
    """(3) Same signal twice -> same signal AND proposal identity."""
    first = propose_expansion(_expansion_signal(registry), registry)
    second = propose_expansion(_expansion_signal(registry), registry)
    assert first.signal_identity == second.signal_identity
    assert first.proposal_identity == second.proposal_identity
    assert first.semantic_identity == second.semantic_identity


def test_timestamp_and_note_do_not_alter_scientific_identity(registry):
    """(4) A different note and observed_at leave the identities untouched."""
    plain = _expansion_signal(registry, note="", observed_at="")
    annotated = _expansion_signal(
        registry, note="a human wrote this prose", observed_at="2099-01-01T00:00:00Z")
    assert plain.note == "" and annotated.note
    assert plain.observed_at != annotated.observed_at

    assert plain.signal_identity == annotated.signal_identity
    assert plain.semantic_material() == annotated.semantic_material()

    left = propose_expansion(plain, registry, note="")
    right = propose_expansion(annotated, registry, note="a different note")
    assert left.proposal_identity == right.proposal_identity
    assert left.semantic_material() == right.semantic_material()
    assert left.target_ref == right.target_ref


# ═══ 5-6. Fail closed on missing / free-text-only authority ═════════════════


def test_missing_source_provenance_fails_closed(registry):
    """(5) A trigger with no identity cannot seed a signal; nothing is invented."""
    for field in ("trigger_id", "finding_id", "source"):
        trigger = _StubTrigger()
        setattr(trigger, field, "")
        with pytest.raises(CuriosityProvenanceError):
            signal_from_finding_trigger(
                trigger,
                target_kind="research_subject",
                target_ref="exit_policy_performance",
                evidence_boundary=EVIDENCE_BOUNDARY,
                dimension_ref=registry.get("horizon").dimension_identity,
            )
    # A reconsideration decision without its own identity also fails closed.
    decision = _StubReconsideration()
    decision.reconsideration_id = ""
    with pytest.raises(CuriosityProvenanceError):
        signal_from_candidate_reconsideration(
            decision, target_kind="system_component", target_ref="t:1",
            evidence_boundary=EVIDENCE_BOUNDARY)


def test_free_text_note_alone_cannot_create_a_proposal(registry):
    """(6) A note is not authority: it can neither make a signal nor a proposal."""
    with pytest.raises(CuriositySignalValidationError):
        CuriositySignal.create(
            signal_type=CuriositySignalType.FINDING_EVIDENCE,
            source_ref="", source_identity="", target_kind="research_subject",
            target_ref="exit_policy_performance",
            reason_code="because I feel like it",     # free text is not a reason
            evidence_reference="", evidence_boundary="",
            note="horizon seems interesting",
        )
    # A non-signal object can never be handed to a generator.
    with pytest.raises(CuriositySignalValidationError):
        propose_expansion("horizon seems interesting", registry)


# ═══ 7-8. Expansion needs a signal, and Wave 1 admission is never bypassed ══


def test_expansion_does_not_propose_an_unused_dimension_without_signal(registry):
    """(7) No signal naming a dimension -> no proposal, however 'unused' it is."""
    # The registry has four admitted dimensions; not one is proposed without an
    # authoritative signal that actually implicated it.
    assert len(registry.admitted()) == 4
    undimensioned = CuriositySignal.create(
        signal_type=CuriositySignalType.FINDING_EVIDENCE,
        source_ref="finding:TRG-9", source_identity="TRG-9",
        source_provenance={"adapter": "finding_trigger"},
        target_kind="research_subject", target_ref="exit_policy_performance",
        reason_code="UNRESOLVED_EXIT_BEHAVIOUR",
        evidence_reference="finding:finding-9",
        evidence_boundary=EVIDENCE_BOUNDARY,
    )
    assert undimensioned.dimension_ref is None
    with pytest.raises(CuriosityProvenanceError):
        propose_expansion(undimensioned, registry)


def test_unadmitted_dimension_cannot_become_investigation_ready(tmp_path):
    """(8) A registered-but-unadmitted dimension never becomes a proposal."""
    registry = _admitted_registry("liquidity")
    dimension = registry.get("liquidity")
    assert registry.is_registered(dimension) and not registry.is_admitted(dimension)

    signal = CuriositySignal.create(
        signal_type=CuriositySignalType.FINDING_EVIDENCE,
        source_ref="finding:TRG-77", source_identity="TRG-77",
        source_provenance={"adapter": "finding_trigger"},
        target_kind="research_subject", target_ref="exit_policy_performance",
        reason_code="UNRESOLVED_EXIT_BEHAVIOUR",
        evidence_reference="finding:finding-77",
        evidence_boundary=EVIDENCE_BOUNDARY,
        dimension_ref=dimension.dimension_identity,
    )
    with pytest.raises(CuriosityProposalValidationError) as excinfo:
        propose_expansion(signal, registry)
    assert "admitted" in str(excinfo.value)

    # Even a direct bypass attempt at the Wave 1 gate itself is refused.
    with pytest.raises(Exception):
        ResearchInteraction.admitted_from(registry, ["liquidity"])

    store = GeneratedResearchStore(tmp_path / "gen.json")
    assert len(store) == 0
    assert_canonical_70_intact()

    """(4) A different note and observed_at leave the identities untouched."""
    plain = _expansion_signal(registry, note="", observed_at="")
    annotated = _expansion_signal(
        registry, note="a human wrote this prose", observed_at="2099-01-01T00:00:00Z")
    assert plain.note == "" and annotated.note
    assert plain.observed_at != annotated.observed_at

    assert plain.signal_identity == annotated.signal_identity
    assert plain.semantic_material() == annotated.semantic_material()

    left = propose_expansion(plain, registry, note="")
    right = propose_expansion(annotated, registry, note="a different note")
    assert left.proposal_identity == right.proposal_identity
    assert left.semantic_material() == right.semantic_material()


# ═══ 9-13. Wave 2 is delegated to; its verdict is recorded EXACTLY ═══════════


def test_valid_expansion_proposal_passes_through_wave2(registry, tmp_path):
    """(9) A valid 1D Expansion proposal -> Wave 2 PERMIT -> one GEN-*."""
    proposal = propose_expansion(_expansion_signal(registry), registry)
    interaction = proposal.proposed_interaction
    freeze = _permitting_freeze(interaction)

    # The gate was genuinely invoked: decide the SAME proposal independently.
    direct = evaluate_interaction_eligibility(interaction, freeze, registry)
    assert direct.state is EligibilityState.PERMIT

    processor = CuriosityProcessor(registry, GeneratedResearchStore(tmp_path / "g.json"))
    outcome = processor.process(proposal, freeze=freeze, t0=T0)
    assert outcome.eligibility_state is EligibilityState.PERMIT
    assert outcome.registered and outcome.is_investigation_ready
    # Curiosity recorded Wave 2's verdict verbatim; it did not re-derive it.
    assert outcome.decision.decision_identity == direct.decision_identity
    assert len(processor) == 1


def test_wave2_waiting_data_remains_waiting_data(registry, tmp_path):
    """(10) 23 of the required 60 usable observations -> WAITING_DATA, not PERMIT."""
    proposal = propose_expansion(_expansion_signal(registry), registry)
    interaction = proposal.proposed_interaction
    processor = CuriosityProcessor(registry, GeneratedResearchStore(tmp_path / "g.json"))

    outcome = processor.process(
        proposal, freeze=_permitting_freeze(interaction, joint=23), t0=T0)
    assert outcome.eligibility_state is EligibilityState.WAITING_DATA
    assert EligibilityReasonCode.COMMON_SUPPORT_INSUFFICIENT.value in outcome.reason_codes
    assert outcome.decision.waiting
    # NOT investigation-ready: a pending proposal is never pretended to be ready.
    assert not outcome.registered and outcome.record is None
    assert outcome.is_investigation_ready is False
    assert len(processor) == 0


def test_wave2_blocked_remains_blocked(tmp_path):
    """(11) Unproven joinability across datasets -> BLOCKED, retained verbatim."""
    # Two admitted dimensions from DIFFERENT authoritative datasets: the
    # interaction is representable by Wave 1, but the evidence path is
    # structurally unproven, which is exactly Wave 2's BLOCKED class.
    blocked_registry = DimensionRegistry()
    regime = GovernedDimension.create(
        dimension_key="regime",
        authority=EvidenceAuthority(
            dataset="decision_trace", schema_version="v1",
            producer=EvidenceProducer.DECISION_TRACE, field_path="market.regime",
            semantic_meaning="declared test-time semantics"),
    )
    trades = GovernedDimension.create(
        dimension_key="session",
        authority=EvidenceAuthority(
            dataset="shadow_trades", schema_version="v1",
            producer=EvidenceProducer.SHADOW_TRADES, field_path="trade.session",
            semantic_meaning="declared test-time semantics"),
    )
    for dimension in (regime, trades):
        blocked_registry.register(dimension)
        blocked_registry.admit(dimension, DimensionCoverage(1000, 900, True, True))

    signal = CuriositySignal.create(
        signal_type=CuriositySignalType.FINDING_EVIDENCE,
        source_ref="finding:TRG-51", source_identity="TRG-51",
        source_provenance={"adapter": "finding_trigger"},
        target_kind="research_subject", target_ref="exit_policy_performance",
        reason_code="CONDITIONAL_BEHAVIOUR_UNRESOLVED",
        evidence_reference="finding:finding-51",
        evidence_boundary=EVIDENCE_BOUNDARY,
        dimension_ref=trades.dimension_identity,
    )
    # A depth-2 interaction spanning BOTH datasets: representable by Wave 1, but
    # the evidence path is structurally unproven -> Wave 2's BLOCKED class. The
    # structural check runs BEFORE the depth/justification stage, so BLOCKED is
    # the reported state and the missing justification is not even reached.
    parent = ResearchInteraction.admitted_from(blocked_registry, ["regime"])
    parent_decision = _parent_permit(parent, blocked_registry)
    proposal = propose_interaction(
        signal, blocked_registry, parent_interaction=parent,
        parent_decision=parent_decision)
    processor = CuriosityProcessor(
        blocked_registry, GeneratedResearchStore(tmp_path / "g.json"))

    outcome = processor.process(
        proposal,
        freeze=_permitting_freeze(proposal.proposed_interaction),
        parent_interaction=parent,
        parent_decision=parent_decision,
        t0=T0)
    assert outcome.eligibility_state is EligibilityState.BLOCKED
    assert outcome.decision.blocked
    # The structural blocker is retained verbatim, and nothing worked around it.
    assert EligibilityReasonCode.JOINABILITY_UNPROVEN.value in outcome.reason_codes
    assert not outcome.registered


def test_wave2_refuse_remains_refuse(registry, tmp_path):
    """(12) A depth-2 proposal with no justification -> REFUSE, never downgraded."""
    parent = ResearchInteraction.admitted_from(registry, ["regime"])
    child = ResearchInteraction.admitted_from(registry, ["regime", "horizon"])
    parent_decision = _parent_permit(parent, registry)
    signal = _expansion_signal(registry, key="horizon")

    # No justification is attached: Wave 2, not curiosity, must refuse.
    proposal = propose_interaction(
        signal, registry, parent_interaction=parent,
        parent_decision=parent_decision)
    assert proposal.mode is CuriosityMode.INTERACTION
    assert proposal.expansion_justification is None

    processor = CuriosityProcessor(registry, GeneratedResearchStore(tmp_path / "g.json"))
    outcome = processor.process(
        proposal, freeze=_permitting_freeze(child), parent_interaction=parent,
        parent_decision=parent_decision, t0=T0)
    assert outcome.eligibility_state is EligibilityState.REFUSE
    assert outcome.decision.refused
    assert EligibilityReasonCode.EXPANSION_JUSTIFICATION_REQUIRED.value in outcome.reason_codes
    # The refusal is retained as provenance and nothing was registered.
    assert not outcome.registered and outcome.record is None
    assert len(processor) == 0


def test_only_wave2_permit_may_become_investigation_ready(registry, tmp_path):
    """(13) The four states partition: only PERMIT registers a `GEN-*` record."""
    store = GeneratedResearchStore(tmp_path / "g.json")
    processor = CuriosityProcessor(registry, store)
    proposal = propose_expansion(_expansion_signal(registry), registry)
    interaction = proposal.proposed_interaction

    # WAITING_DATA: valid path, insufficient usable support.
    waiting = processor.process(
        proposal, freeze=_permitting_freeze(interaction, joint=1), t0=T0)
    assert waiting.eligibility_state is EligibilityState.WAITING_DATA
    assert not waiting.registered and len(store) == 0

    # REFUSE: a depth-2 expansion carrying no justification.
    parent = ResearchInteraction.admitted_from(registry, ["regime"])
    child = ResearchInteraction.admitted_from(registry, ["regime", "horizon"])
    parent_decision = _parent_permit(parent, registry)
    refused = processor.process(
        propose_interaction(_expansion_signal(registry, key="horizon"), registry,
                            parent_interaction=parent,
                            parent_decision=parent_decision),
        freeze=_permitting_freeze(child), parent_interaction=parent,
        parent_decision=parent_decision, t0=T0)
    assert refused.eligibility_state is EligibilityState.REFUSE
    assert not refused.registered and len(store) == 0

    # PERMIT: the only state that registers.
    new_boundary = "2026-09-26T00:00:00Z"
    permitted_proposal = propose_expansion(
        _expansion_signal(registry, boundary=new_boundary), registry)
    permitted = processor.process(
        permitted_proposal,
        freeze=_permitting_freeze(
            permitted_proposal.proposed_interaction, boundary=new_boundary),
        t0=T0)
    assert permitted.eligibility_state is EligibilityState.PERMIT and permitted.registered
    assert len(store) == 1


# ═══ 14-16. Wave 0 registration, dedup and reload determinism ═══════════════


def test_valid_permit_proposal_registers_exactly_one_gen(registry, tmp_path):
    """(14) A PERMIT proposal registers EXACTLY ONE `GEN-*` record."""
    store = GeneratedResearchStore(tmp_path / "g.json")
    processor = CuriosityProcessor(registry, store)
    proposal = propose_expansion(_expansion_signal(registry), registry)

    outcome = processor.process(
        proposal, freeze=_permitting_freeze(proposal.proposed_interaction), t0=T0)
    assert outcome.eligibility_state is EligibilityState.PERMIT
    assert len(store) == 1
    record = outcome.record
    assert record.generated_research_id.startswith(GENERATED_RESEARCH_ID_PREFIX)
    assert is_generated_research_id(record.generated_research_id)
    # Everything Wave 3 must preserve is preserved in the registered record.
    assert record.research_kind is GeneratedResearchKind.EXPANSION
    assert record.trigger_ref == f"signal:{proposal.signal_identity}"
    assert record.target_ref == "exit_policy_performance"
    assert record.dimension_ref == proposal.interaction_identity
    spec = record.specification
    assert spec["curiosity_mode"] == "EXPANSION"
    assert spec["definition_version"] == 1
    assert spec["proposed_interaction_identity"] == proposal.interaction_identity
    assert spec["eligibility"]["state"] == "PERMIT"
    assert spec["eligibility"]["decision_identity"] == outcome.decision.decision_identity
    assert_canonical_70_intact()


def test_rerunning_identical_proposal_registers_zero_additional(registry, tmp_path):
    """(15) Re-running the SAME signal over the SAME evidence adds ZERO records."""
    path = tmp_path / "g.json"
    store = GeneratedResearchStore(path)
    processor = CuriosityProcessor(registry, store)
    signal = _expansion_signal(registry)
    proposal = propose_expansion(signal, registry)
    freeze = _permitting_freeze(proposal.proposed_interaction)

    first = processor.process(proposal, freeze=freeze, t0=T0)
    assert first.registered and not first.deduplicated
    assert len(store) == 1

    # Same evidence, same signal, different T0 and a different note: still one.
    repeat = processor.process(
        propose_expansion(signal, registry, note="a new note"), freeze=freeze,
        t0="2099-01-01T00:00:00Z")
    assert repeat.registered and repeat.deduplicated
    assert repeat.generated_research_id == first.generated_research_id
    assert len(store) == 1

    # A FRESH processor over the SAME store adds nothing either.
    again = CuriosityProcessor(registry, store).process(
        propose_expansion(signal, registry), freeze=freeze, t0=T0)
    assert again.deduplicated and len(store) == 1

    # And a brand-new signal (a different finding) IS a new generated question.
    other = processor.process(
        propose_expansion(_expansion_signal(registry, trigger_id="TRG-0002"), registry),
        freeze=freeze, t0=T0)
    assert other.registered and not other.deduplicated
    assert len(store) == 2
    assert other.generated_research_id != first.generated_research_id


def test_generated_identity_is_deterministic_across_store_reload(registry, tmp_path):
    """(16) Reloading the store yields the identical `GEN-*` identity."""
    path = tmp_path / "g.json"
    store = GeneratedResearchStore(path)
    processor = CuriosityProcessor(registry, store)
    proposal = propose_expansion(_expansion_signal(registry), registry)
    outcome = processor.process(
        proposal, freeze=_permitting_freeze(proposal.proposed_interaction), t0=T0)
    original_id = outcome.generated_research_id
    original_identity = outcome.record.semantic_identity

    reloaded = GeneratedResearchStore(path)
    assert len(reloaded) == 1
    restored = reloaded.get(original_id)
    assert restored is not None
    assert restored.semantic_identity == original_identity
    assert restored.created_at == outcome.record.created_at

    # Re-processing the identical signal against the RELOADED store still
    # resolves to the same identity and registers nothing new.
    again = CuriosityProcessor(registry, reloaded).process(
        propose_expansion(_expansion_signal(registry), registry),
        freeze=_permitting_freeze(proposal.proposed_interaction), t0=T0)
    assert again.generated_research_id == original_id
    assert len(reloaded) == 1
    # A rebuilt proposal from scratch is byte-identical in identity material.
    assert propose_expansion(
        _expansion_signal(registry), registry).proposal_identity == (
            proposal.proposal_identity)


# --- 17+. Interrupted recovery coverage --------------------------------------


def test_huge_sample_without_interaction_justification_remains_refuse(registry, tmp_path):
    parent = ResearchInteraction.admitted_from(registry, ["regime"])
    parent_decision = _parent_permit(parent, registry)
    proposal = propose_interaction(
        _expansion_signal(registry, key="horizon"), registry,
        parent_interaction=parent, parent_decision=parent_decision)
    outcome = CuriosityProcessor(
        registry, GeneratedResearchStore(tmp_path / "g.json")).process(
            proposal,
            freeze=_permitting_freeze(
                proposal.proposed_interaction, joint=10_000_000),
            parent_interaction=parent, parent_decision=parent_decision)
    assert outcome.eligibility_state is EligibilityState.REFUSE
    assert not outcome.registered


def test_destructive_requires_authoritative_permit(registry, tmp_path):
    proposal = propose_destructive(_destructive_signal(registry))
    processor = CuriosityProcessor(registry, GeneratedResearchStore(tmp_path / "g.json"))
    with pytest.raises(CuriosityProposalValidationError):
        processor.process(proposal)
    permitted = processor.process(
        proposal, destructive_freeze=_destructive_freeze(proposal))
    assert isinstance(permitted.decision, DestructiveEligibilityDecision)
    assert permitted.eligibility_state is EligibilityState.PERMIT
    assert permitted.registered and len(processor) == 1


@pytest.mark.parametrize(
    "freeze_kwargs, expected",
    [
        ({"usable": 2}, EligibilityState.WAITING_DATA),
        ({"blockers": ("LINEAGE_UNPROVEN",)}, EligibilityState.BLOCKED),
        ({"justification": None}, EligibilityState.REFUSE),
    ],
)
def test_destructive_nonpermit_never_registers(
        registry, tmp_path, freeze_kwargs, expected):
    proposal = propose_destructive(_destructive_signal(registry))
    processor = CuriosityProcessor(registry, GeneratedResearchStore(tmp_path / "g.json"))
    outcome = processor.process(
        proposal, destructive_freeze=_destructive_freeze(proposal, **freeze_kwargs))
    assert outcome.eligibility_state is expected
    assert not outcome.registered and outcome.record is None and len(processor) == 0


def test_destructive_proposal_and_gate_do_not_mutate_target(registry, tmp_path):
    target = {"enabled": True, "weight": 1.0, "config": {"threshold": 7}}
    before = repr(target)
    proposal = propose_destructive(_destructive_signal(registry))
    CuriosityProcessor(registry, GeneratedResearchStore(tmp_path / "g.json")).process(
        proposal, destructive_freeze=_destructive_freeze(proposal))
    assert repr(target) == before


def test_many_slices_of_one_interaction_still_make_one_question(registry, tmp_path):
    proposal = propose_expansion(_expansion_signal(registry), registry)
    interaction = proposal.proposed_interaction
    slices = [InteractionSlice.create(interaction, {"horizon": value})
              for value in (5, 15, 60)]
    assert len({cell.slice_identity for cell in slices}) == 3
    assert {cell.interaction_identity for cell in slices} == {proposal.interaction_identity}
    processor = CuriosityProcessor(registry, GeneratedResearchStore(tmp_path / "g.json"))
    freeze = _permitting_freeze(interaction)
    outcomes = [processor.process(proposal, freeze=freeze) for _ in slices]
    assert len({outcome.generated_research_id for outcome in outcomes}) == 1
    assert len(processor) == 1


def test_direct_self_parent_and_duplicate_parent_question_are_rejected(registry):
    parent = propose_expansion(_expansion_signal(registry), registry)
    with pytest.raises(CuriosityRecursionError):
        replace(parent, parent_proposal_identity=parent.proposal_identity)
    with pytest.raises(CuriosityRecursionError):
        propose_expansion(
            _expansion_signal(registry), registry, parent_proposal=parent)


def test_changed_boundary_changes_identity_and_mismatch_fails_closed(registry, tmp_path):
    boundary_b = "2026-09-26T00:00:00Z"
    first = propose_expansion(_expansion_signal(registry), registry)
    second = propose_expansion(
        _expansion_signal(registry, boundary=boundary_b), registry)
    assert first.signal_identity != second.signal_identity
    assert first.proposal_identity != second.proposal_identity
    processor = CuriosityProcessor(registry, GeneratedResearchStore(tmp_path / "g.json"))
    with pytest.raises(CuriosityProposalValidationError):
        processor.process(first, freeze=_permitting_freeze(
            first.proposed_interaction, boundary=boundary_b))


def test_interaction_requires_and_preserves_exact_parent_permit(registry, tmp_path):
    parent = ResearchInteraction.admitted_from(registry, ["regime"])
    signal = _expansion_signal(registry, key="horizon")
    with pytest.raises(CuriosityProposalValidationError):
        propose_interaction(signal, registry, parent_interaction=parent)

    parent_decision = _parent_permit(parent, registry)
    child = ResearchInteraction.admitted_from(registry, ["regime", "horizon"])
    justification = _justification(
        parent, child, registry, added_key="horizon")
    proposal = propose_interaction(
        signal, registry, parent_interaction=parent,
        parent_decision=parent_decision, justification=justification)
    assert proposal.parent_decision_identity == parent_decision.decision_identity

    wrong_parent = ResearchInteraction.admitted_from(registry, ["session"])
    wrong_decision = _parent_permit(wrong_parent, registry)
    processor = CuriosityProcessor(registry, GeneratedResearchStore(tmp_path / "g.json"))
    with pytest.raises(CuriosityProposalValidationError):
        processor.process(
            proposal, freeze=_permitting_freeze(child), parent_interaction=parent,
            parent_decision=wrong_decision)
    outcome = processor.process(
        proposal, freeze=_permitting_freeze(child), parent_interaction=parent,
        parent_decision=parent_decision)
    assert outcome.eligibility_state is EligibilityState.PERMIT
    assert outcome.decision.parent_justification_identity == justification.justification_identity


def test_identical_refuse_replay_is_stable(registry, tmp_path):
    parent = ResearchInteraction.admitted_from(registry, ["regime"])
    parent_decision = _parent_permit(parent, registry)
    proposal = propose_interaction(
        _expansion_signal(registry, key="horizon"), registry,
        parent_interaction=parent, parent_decision=parent_decision)
    freeze = _permitting_freeze(proposal.proposed_interaction)
    processor = CuriosityProcessor(registry, GeneratedResearchStore(tmp_path / "g.json"))
    first = processor.process(
        proposal, freeze=freeze, parent_interaction=parent,
        parent_decision=parent_decision)
    replay = processor.process(
        proposal, freeze=freeze, parent_interaction=parent,
        parent_decision=parent_decision)
    assert first.eligibility_state is replay.eligibility_state is EligibilityState.REFUSE
    assert first.decision.decision_identity == replay.decision.decision_identity
    assert len(processor) == 0


def test_real_finding_seam_preserves_governed_provenance(registry):
    trigger = FindingTrigger(
        trigger_id="TRG-real1", finding_id="finding-real1", source="evidence_layer",
        category=TriggerCategory.EXIT_INEFFICIENCY, sample_size=240,
        evidence={
            "question_id": "E1",
            "experiment_id": "EXP-real1",
            "source_datasets": ["decision_trace"],
            "dataset_fingerprint": "sha256:finding-real1",
            "evidence_as_of": EVIDENCE_BOUNDARY,
        })
    signal = trigger.to_curiosity_signal(
        target_kind="research_subject", target_ref="exit_policy_performance",
        dimension_ref=registry.get("horizon").dimension_identity)
    proposal = propose_expansion(signal, registry)
    assert signal.source_provenance["evidence"] == trigger.evidence
    assert signal.source_provenance["dataset_fingerprint"] == "sha256:finding-real1"
    assert "research:E1" in signal.parent_research_refs
    assert proposal.mode is CuriosityMode.EXPANSION
    assert trigger.hypothesis_id == ""


def _real_reconsideration(*, human_rejected: bool = False):
    return CandidateReconsiderationDecision(
        reconsideration_id="R63A-real1",
        status=ReconsiderationStatus.NOT_ELIGIBLE if human_rejected else (
            ReconsiderationStatus.ELIGIBLE_FOR_RECONSIDERATION),
        candidate_id="CAND-real1",
        historical_baseline_id="BASE-v0",
        historical_baseline_config_hash="cfg-v0",
        target_baseline_id="BASE-v1",
        target_baseline_config_hash="cfg-v1",
        impact_id="IMP-real1",
        continuity_id="CNT-real1",
        candidate_treatment_id="TRT-real1",
        historical_candidate_status="REJECTED",
        historical_evaluation_outcome="REJECTED",
        reason_codes=(("HUMAN_REJECTION_NOT_OVERRIDDEN",) if human_rejected else ()),
        fresh_evidence_required=False,
        limitations=(),
    )


def test_real_candidate_reconsideration_seam_preserves_provenance_and_human_rejection():
    decision = _real_reconsideration(human_rejected=True)
    signal = decision.to_curiosity_signal(
        target_kind="system_component", target_ref="filter:volatility_filter",
        evidence_boundary=EVIDENCE_BOUNDARY,
        component_ref="filter:volatility_filter", component_kind="FILTER",
        baseline_ref="BASE-v1")
    proposal = propose_destructive(signal)
    assert signal.reason_code == "CANDIDATE_REJECTED_BY_HUMAN"
    assert signal.source_provenance["human_decision_present"] is True
    assert signal.source_provenance["historical_baseline_config_hash"] == "cfg-v0"
    assert proposal.mode is CuriosityMode.DESTRUCTIVE
    assert decision.status is ReconsiderationStatus.NOT_ELIGIBLE


def test_wave3_has_no_search_profit_execution_or_production_authority():
    modules = (signal_module, proposal_module, generator_module)
    forbidden_calls = {
        "product", "combinations", "permutations", "run_experiment",
        "create_hypothesis", "create_candidate", "activate_candidate",
        "promote_candidate", "execute_research", "apply_treatment",
    }
    forbidden_import_fragments = (
        "broker", "risk", "sizing", "production", "research_runner",
        "experiment_runner", "candidate_evolution", "governance_gate",
    )
    for module in modules:
        tree = ast.parse(inspect.getsource(module))
        calls = {
            node.func.id for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert forbidden_calls.isdisjoint(calls)
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
        assert not any(fragment in imported for imported in imports
                       for fragment in forbidden_import_fragments)
    assert_canonical_70_intact()
