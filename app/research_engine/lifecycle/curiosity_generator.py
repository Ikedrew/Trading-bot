"""
Governed Curiosity Generators v1 -- three deterministic EXPANSION / INTERACTION
/ DESTRUCTIVE proposal generators, and the Wave 2 -> Wave 0 seam.

Stage 3 / Wave 3. This is the front end Stage 3 was missing: the ability to look
at governed evidence and autonomously PROPOSE a new research question outside the
frozen canonical 70.

THE SEAM
========
    EVIDENCE SIGNAL            curiosity_signal.CuriositySignal
        v
    CURIOSITY GENERATOR        this module -- propose ONLY
        v
    GENERATED RESEARCH PROPOSAL
        v
    WAVE 1 governed dimensions / interaction
        v
    GOVERNED ELIGIBILITY       Wave 2 interaction gate, or the narrow destructive
                               evidence gate when no interaction is scientific
        v
    WAVE 0 GENERATED REGISTRATION
        v
    eligible for later investigation

CURIOSITY MAY PROPOSE. CURIOSITY MAY NOT APPROVE ITSELF.
=======================================================
The generators in this module NEVER decide that a proposal is eligible. They
build exactly one proposal from exactly one signal, and `CuriosityProcessor`
then hands interaction proposals to the COMMITTED Wave 2 gate and component
challenges to the narrow destructive evidence gate. The exact four-state verdict
is recorded: PERMIT, WAITING_DATA, BLOCKED and REFUSE are never collapsed,
reinterpreted, downgraded or upgraded. Only a PERMIT may register.

The curiosity layer NEVER:

    - bypasses `DimensionRegistry` admission (a dimension must be an ADMITTED
      governed dimension, and `ResearchInteraction.admitted_from` is the only way
      an interaction is built);
    - bypasses evidence feasibility (Wave 2 owns it, this module does not
      duplicate a single line of it);
    - bypasses progressive-depth rules (an INTERACTION proposal is the parent
      plus EXACTLY ONE dimension, and depth skipping is mechanically impossible);
    - bypasses generated-research registration (registration goes through the
      committed Wave 0 `GeneratedResearchStore`);
    - executes research, creates findings as truth, promotes hypotheses or
      candidates, or alters production.

NO ENUMERATION, NO SEARCH, NO PROFIT MINING (HARD INVARIANTS)
=============================================================
There is no `for dimension in registry`, no `itertools.product`, no
"all_interactions()", no ranking of candidate dimensions, no "try another
dimension after a refusal", and no selection by historical profit, expectancy,
R-multiple or best slice. The generators are pure functions of ONE supplied
signal and the governed Wave 1/Wave 2 authorities. Wave 4 governs search
provenance and multiplicity.

RECURSION CONTROL
=================
`GeneratedResearchProposal` rejects direct self-parenting and a child proposal
semantically identical to its parent. Ancestry is preserved in
`parent_research_refs` / `parent_proposal_identity` so a later wave can govern
revisits and detect cycles; Wave 3 deliberately does not build a graph engine.

NO PRODUCTION AUTHORITY
-----------------------
This module imports no runner, no orchestrator, no `GovernanceGate`, no risk, no
broker, no production configuration. It performs no I/O of its own; the only
write it can ever cause is the explicit, caller-initiated Wave 0 store
registration, and a proposal is not a finding, a hypothesis, a candidate or a
production change.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Mapping

from research_engine.lifecycle.curiosity_proposal import (
    CuriosityMode,
    CuriosityProposalError,
    CuriosityProposalValidationError,
    DestructiveTargetKind,
    GeneratedResearchProposal,
)
from research_engine.lifecycle.curiosity_signal import (
    CuriosityProvenanceError,
    CuriositySignal,
    CuriositySignalValidationError,
)
from research_engine.lifecycle.dimension_registry import DimensionRegistry
from research_engine.lifecycle.generated_research_identity import (
    GeneratedResearchProposal as Wave0Proposal,
    GeneratedResearchRecord,
    canonical_json,
)
from research_engine.lifecycle.generated_research_store import GeneratedResearchStore
from research_engine.lifecycle.governed_dimension import GovernedDimension
from research_engine.lifecycle.progressive_depth_gate import (
    EligibilityDecision,
    EligibilityState,
    ExpansionJustification,
    evaluate_interaction_eligibility,
    parent_step_is_valid,
)
from research_engine.lifecycle.research_interaction import ResearchInteraction


@dataclass(frozen=True)
class DestructiveEligibilityEvidenceFreeze:
    """Immutable evidence used to govern one destructive proposal.

    This is intentionally a Wave 3 adapter rather than an artificial dimension
    interaction.  It uses Wave 2's exact four-state vocabulary while leaving the
    committed interaction gate unchanged.
    """

    component_ref: str
    baseline_ref: str
    evidence_reference: str
    evidence_boundary: str
    dataset_fingerprint: str
    usable_observations: int
    minimum_observations: int
    justification_code: str | None = None
    structural_blockers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for label in ("component_ref", "baseline_ref", "evidence_reference",
                      "evidence_boundary"):
            value = getattr(self, label)
            if not isinstance(value, str) or not value.strip():
                raise CuriosityProposalValidationError(
                    f"destructive eligibility {label} must be non-empty")
        if not isinstance(self.dataset_fingerprint, str):
            raise CuriosityProposalValidationError(
                "destructive eligibility dataset_fingerprint must be a string")
        for label in ("usable_observations", "minimum_observations"):
            value = getattr(self, label)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise CuriosityProposalValidationError(
                    f"destructive eligibility {label} must be a non-negative integer")
        if self.minimum_observations < 1:
            raise CuriosityProposalValidationError(
                "destructive eligibility minimum_observations must be positive")
        blockers = tuple(sorted(set(self.structural_blockers)))
        if not all(isinstance(code, str) and re.fullmatch(
                r"[A-Z][A-Z0-9_]*", code) for code in blockers):
            raise CuriosityProposalValidationError(
                "destructive eligibility structural blockers must be non-empty codes")
        object.__setattr__(self, "structural_blockers", blockers)
        if self.justification_code is not None:
            if (not isinstance(self.justification_code, str)
                    or not re.fullmatch(
                        r"[A-Z][A-Z0-9_]*", self.justification_code)):
                raise CuriosityProposalValidationError(
                    "destructive eligibility justification_code must be non-empty")

    def semantic_material(self) -> dict[str, Any]:
        return {
            "kind": "destructive_eligibility_evidence_freeze",
            "component_ref": self.component_ref,
            "baseline_ref": self.baseline_ref,
            "evidence_reference": self.evidence_reference,
            "evidence_boundary": self.evidence_boundary,
            "dataset_fingerprint": self.dataset_fingerprint,
            "usable_observations": self.usable_observations,
            "minimum_observations": self.minimum_observations,
            "justification_code": self.justification_code,
            "structural_blockers": list(self.structural_blockers),
        }

    @property
    def semantic_identity(self) -> str:
        return hashlib.sha256(canonical_json(
            self.semantic_material()).encode("utf-8")).hexdigest()

    @property
    def evidence_identity(self) -> str:
        return f"DEV-{self.semantic_identity[:16].upper()}"


@dataclass(frozen=True)
class DestructiveEligibilityDecision:
    """Authoritative four-state result for one destructive proposal/freeze."""

    proposal_identity: str
    component_ref: str
    state: EligibilityState
    evidence_identity: str
    evidence_boundary: str
    reason_codes: tuple[str, ...] = ()

    @property
    def semantic_identity(self) -> str:
        material = {
            "kind": "destructive_eligibility_decision",
            "proposal_identity": self.proposal_identity,
            "component_ref": self.component_ref,
            "state": self.state.value,
            "evidence_identity": self.evidence_identity,
            "evidence_boundary": self.evidence_boundary,
            "reason_codes": list(self.reason_codes),
        }
        return hashlib.sha256(canonical_json(material).encode("utf-8")).hexdigest()

    @property
    def decision_identity(self) -> str:
        return f"DCD-{self.semantic_identity[:16].upper()}"

    @property
    def permitted(self) -> bool:
        return self.state is EligibilityState.PERMIT


def evaluate_destructive_eligibility(
    proposal: GeneratedResearchProposal,
    freeze: DestructiveEligibilityEvidenceFreeze,
) -> DestructiveEligibilityDecision:
    """Evaluate exactly one component challenge without inventing an interaction."""
    if proposal.mode is not CuriosityMode.DESTRUCTIVE:
        raise CuriosityProposalValidationError(
            "destructive eligibility may only evaluate a DESTRUCTIVE proposal")
    if not isinstance(freeze, DestructiveEligibilityEvidenceFreeze):
        raise CuriosityProposalValidationError(
            "DESTRUCTIVE registration requires a DestructiveEligibilityEvidenceFreeze")
    if proposal.evidence_boundary != freeze.evidence_boundary:
        raise CuriosityProposalValidationError(
            "proposal evidence_boundary does not match destructive evidence freeze")
    if proposal.evidence_reference != freeze.evidence_reference:
        raise CuriosityProposalValidationError(
            "proposal evidence_reference does not match destructive evidence freeze")
    if proposal.component_ref != freeze.component_ref or proposal.baseline_ref != freeze.baseline_ref:
        raise CuriosityProposalValidationError(
            "destructive evidence freeze does not describe the proposed component/baseline")

    if freeze.structural_blockers or not freeze.dataset_fingerprint.strip():
        state = EligibilityState.BLOCKED
        reasons = freeze.structural_blockers or ("EVIDENCE_FREEZE_UNAVAILABLE",)
    elif freeze.usable_observations < freeze.minimum_observations:
        state = EligibilityState.WAITING_DATA
        reasons = ("COMMON_SUPPORT_INSUFFICIENT",)
    elif freeze.justification_code is None:
        state = EligibilityState.REFUSE
        reasons = ("DESTRUCTIVE_JUSTIFICATION_REQUIRED",)
    else:
        state = EligibilityState.PERMIT
        reasons = ()
    return DestructiveEligibilityDecision(
        proposal_identity=proposal.proposal_identity,
        component_ref=proposal.component_ref,
        state=state,
        evidence_identity=freeze.evidence_identity,
        evidence_boundary=freeze.evidence_boundary,
        reason_codes=tuple(reasons),
    )



# -- Shared, governed helpers -------------------------------------------------


def _require_signal(signal: Any) -> CuriositySignal:
    if not isinstance(signal, CuriositySignal):
        raise CuriositySignalValidationError(
            f"expected a CuriositySignal, got {type(signal).__name__}; free text and "
            f"ad-hoc objects may not seed a research proposal")
    return signal


def _require_registry(registry: Any) -> DimensionRegistry:
    if not isinstance(registry, DimensionRegistry):
        raise CuriosityProposalValidationError(
            f"expected a DimensionRegistry, got {type(registry).__name__}")
    return registry


def _proposal_ancestry(
    signal: CuriositySignal,
    parent_proposal: GeneratedResearchProposal | None,
) -> tuple[tuple[str, ...], str | None]:
    refs = list(signal.parent_research_refs)
    parent_identity = None
    if parent_proposal is not None:
        if not isinstance(parent_proposal, GeneratedResearchProposal):
            raise CuriosityProposalValidationError(
                "parent_proposal must be a GeneratedResearchProposal")
        parent_identity = parent_proposal.proposal_identity
        refs.append(f"proposal:{parent_identity}")
    return tuple(sorted(set(refs))), parent_identity


def _reject_duplicate_parent_question(
    proposal: GeneratedResearchProposal,
    parent_proposal: GeneratedResearchProposal | None,
) -> GeneratedResearchProposal:
    if parent_proposal is not None and (
            proposal.scientific_question_material()
            == parent_proposal.scientific_question_material()):
        from research_engine.lifecycle.curiosity_signal import CuriosityRecursionError
        raise CuriosityRecursionError(
            "a child proposal may not be scientifically identical to its parent proposal")
    return proposal


def _admitted_dimension(
    signal: CuriositySignal,
    registry: DimensionRegistry,
) -> GovernedDimension:
    """
    Resolve the signal's implicated dimension through Wave 1 ADMISSION.

    Fail closed, and fail closed LOUDLY: an unknown dimension, a registered but
    UNADMITTED dimension, or a signal with no dimension at all cannot become a
    proposal. Curiosity never infers relevance merely because a dimension
    exists, and it never substitutes a different dimension for the one the
    evidence actually named.
    """
    if signal.dimension_ref is None:
        raise CuriosityProvenanceError(
            "no evidence signal naming a governed dimension was supplied: without an "
            "authoritative signal identifying WHY a dimension is relevant, no expansion "
            "or interaction proposal may be created")
    # The signal names the dimension by its governed DISPLAY identity (`DIM-...`),
    # which is what a `CuriositySignal` validates. The registry indexes by
    # governed KEY and by the 64-hex semantic identity, so the display identity
    # is resolved by matching it. This is a RESOLUTION of one explicitly named
    # identity, never an enumeration: exactly one dimension can match, and no
    # dimension is ever chosen from what the loop happens to pass.
    dimension = next(
        (candidate for candidate in registry.all()
         if candidate.dimension_identity == signal.dimension_ref),
        None,
    )
    if dimension is None:
        raise CuriosityProposalValidationError(
            f"the dimension named by the evidence signal {signal.dimension_ref!r} is not "
            f"a registered governed dimension; Wave 1 admission is never bypassed")
    if not registry.is_admitted(dimension):
        raise CuriosityProposalValidationError(
            f"the dimension named by the evidence signal {dimension.dimension_key!r} is "
            f"registered but NOT admitted for generated research; an unadmitted "
            f"dimension may never become an investigation-ready proposal")
    return dimension


# -- Mode 1: EXPANSION --------------------------------------------------------


def propose_expansion(
    signal: Any,
    registry: Any,
    *,
    parent_proposal: GeneratedResearchProposal | None = None,
    note: str = "",
) -> GeneratedResearchProposal:
    """
    EXPANSION: "What relevant governed dimension am I not currently
    investigating?"

    EXACT SEMANTICS
    ---------------
    The generator builds the depth-1 interaction of the ONE dimension the
    evidence signal actually named, resolved through Wave 1 admission. It does
    NOT iterate the registry and emit a question for every dimension that happens
    not to be in use: relevance is supplied by the evidence signal and by nothing
    else. An unadmitted or unknown dimension raises; it never becomes an
    eligible proposal.

    A depth-1 interaction needs no parent and no expansion justification, so an
    EXPANSION proposal is evaluated by Wave 2 on its own merits: authoritative
    evidence, a valid joint population and the governed support requirements.
    """
    signal = _require_signal(signal)
    registry = _require_registry(registry)
    dimension = _admitted_dimension(signal, registry)

    # `admitted_from` is the Wave 1 admission gate. It is the ONLY constructor
    # used here, so an interaction can never contain an unadmitted dimension.
    proposed = ResearchInteraction.admitted_from(registry, [dimension.dimension_key])

    parent_refs, parent_proposal_identity = _proposal_ancestry(signal, parent_proposal)
    proposal = GeneratedResearchProposal.create(
        mode=CuriosityMode.EXPANSION,
        signal_identity=signal.signal_identity,
        target_kind=signal.target_kind,
        target_ref=signal.target_ref,
        reason_code=signal.reason_code,
        evidence_reference=signal.evidence_reference,
        evidence_boundary=signal.evidence_boundary,
        proposed_interaction=proposed,
        added_dimension_identity=dimension.dimension_identity,
        parent_proposal_identity=parent_proposal_identity,
        parent_research_refs=parent_refs,
        note=note,
    )
    return _reject_duplicate_parent_question(proposal, parent_proposal)


# -- Mode 2: INTERACTION ------------------------------------------------------


def propose_interaction(
    signal: Any,
    registry: Any,
    *,
    parent_interaction: ResearchInteraction,
    parent_decision: EligibilityDecision | None = None,
    justification: ExpansionJustification | None = None,
    parent_proposal: GeneratedResearchProposal | None = None,
    note: str = "",
) -> GeneratedResearchProposal:
    """
    INTERACTION: "What governed combination of dimensions contains unresolved
    behaviour that may justify deeper investigation?"

    EXACT SEMANTICS
    ---------------
    The generator constructs ONE explicit proposed child interaction: the parent
    interaction plus EXACTLY ONE dimension -- the one the evidence signal named.

    IT MUST USE WAVE 2. The generator does not decide that a 3D interaction is
    permitted. It builds the child, optionally attaches a governed
    `ExpansionJustification`, and lets the committed Wave 2 gate return
    PERMIT / WAITING_DATA / BLOCKED / REFUSE. That verdict is recorded verbatim
    by `CuriosityProcessor`:

        REFUSE        the refusal and its provenance are retained; no active
                      generated research question is registered;
        WAITING_DATA  the proposal is retained as pending evidence; it is not
                      pretended to be investigation-ready;
        BLOCKED       the structural blocker is retained; nothing works around it;
        PERMIT        the proposal MAY proceed to Wave 0 registration -- which
                      still does not execute any investigation.

    There is NO interaction enumeration and NO "try another dimension after a
    refusal" fallback. A refusal ends this proposal. Wave 2 also mechanically
    makes depth skipping impossible: `parent_step_is_valid` requires the child to
    be the parent plus exactly one dimension, and a dimension already present in
    the parent cannot be added again.
    """
    signal = _require_signal(signal)
    registry = _require_registry(registry)
    if not isinstance(parent_interaction, ResearchInteraction):
        raise CuriosityProposalValidationError(
            f"parent_interaction must be a ResearchInteraction, got "
            f"{type(parent_interaction).__name__}; an INTERACTION proposal is a "
            f"governed parent -> child expansion and cannot be proposed without one")
    dimension = _admitted_dimension(signal, registry)

    # Progressive depth is enforced MECHANICALLY here, not merely requested of
    # Wave 2: the child is the parent plus the one named dimension, so its depth
    # is exactly parent.depth + 1. A depth-skipping child is unrepresentable.
    child_keys = tuple(parent_interaction.dimension_keys) + (dimension.dimension_key,)
    proposed = ResearchInteraction.admitted_from(registry, child_keys)
    if not parent_step_is_valid(parent_interaction, proposed):
        raise CuriosityProposalError(
            f"the proposed child interaction {proposed.interaction_identity!r} is not a "
            f"valid single-dimension step from parent "
            f"{parent_interaction.interaction_identity!r}; depth may never be skipped")

    if justification is not None and not isinstance(justification, ExpansionJustification):
        raise CuriosityProposalValidationError(
            f"justification must be a governed ExpansionJustification, got "
            f"{type(justification).__name__}; a Wave 2 justification is never "
            f"substituted with free text")
    if not isinstance(parent_decision, EligibilityDecision):
        raise CuriosityProposalValidationError(
            "a depth>1 INTERACTION proposal requires the exact parent "
            f"EligibilityDecision, got {type(parent_decision).__name__}")
    if parent_decision.interaction_identity != parent_interaction.interaction_identity:
        raise CuriosityProposalValidationError(
            "parent_decision does not correspond to the exact parent interaction")
    if not parent_decision.permitted:
        raise CuriosityProposalValidationError(
            "parent_decision must be PERMIT before a child interaction may be proposed")

    parent_refs, parent_proposal_identity = _proposal_ancestry(signal, parent_proposal)
    proposal = GeneratedResearchProposal.create(
        mode=CuriosityMode.INTERACTION,
        signal_identity=signal.signal_identity,
        target_kind=signal.target_kind,
        target_ref=signal.target_ref,
        reason_code=signal.reason_code,
        evidence_reference=signal.evidence_reference,
        evidence_boundary=signal.evidence_boundary,
        proposed_interaction=proposed,
        added_dimension_identity=dimension.dimension_identity,
        parent_interaction_identity=parent_interaction.interaction_identity,
        parent_proposal_identity=parent_proposal_identity,
        parent_decision_identity=parent_decision.decision_identity,
        expansion_justification=justification,
        parent_research_refs=parent_refs,
        note=note,
    )
    return _reject_duplicate_parent_question(proposal, parent_proposal)


# -- Mode 3: DESTRUCTIVE ------------------------------------------------------


def propose_destructive(
    signal: Any,
    *,
    parent_proposal: GeneratedResearchProposal | None = None,
    note: str = "",
) -> GeneratedResearchProposal:
    """
    DESTRUCTIVE: "What existing component, assumption, feature, rule or treatment
    may deserve challenge, weakening, disabling, removal or simplification?"

    EXACT SEMANTICS
    ---------------
    The minimal governed destructive semantics: this generator only expresses

        "Investigate whether component X still deserves to remain."

    A destructive proposal identifies the exact component, its generic category,
    the current baseline/reference it is measured against, the evidence signal
    motivating the challenge, the evidence boundary, and a machine-readable
    reason. The `DestructiveTargetKind` vocabulary is GENERIC -- PATTERN,
    GUARD, FEATURE, FILTER, SCORING_COMPONENT, EXIT_RULE, STRATEGY_COMPONENT,
    ASSUMPTION, RULE -- and is deliberately not hardcoded to candlestick
    patterns.

    WHAT IT DOES NOT DO
    -------------------
    It does not alter, weaken, disable or remove the component. It does not run
    the counterfactual. It does not change production. It does not decide which
    Wave 5 treatment (ADD / MODIFY / CONDITION / DOWNWEIGHT / DISABLE / REMOVE)
    would apply -- that taxonomy is explicitly deferred. It creates a governed
    research PROPOSAL challenging the component, and nothing else.

    A destructive proposal references no governed dimension interaction, so Wave
    2's interaction-depth semantics do not apply. Before registration it must
    pass the narrow destructive evidence gate, which returns the same four-state
    vocabulary and never treats the mode itself as permission.
    """
    signal = _require_signal(signal)
    if signal.component_ref is None:
        raise CuriosityProvenanceError(
            "no evidence signal naming an exact component to challenge was supplied; "
            "destructive curiosity may not target an unnamed or implied component")
    if signal.component_kind is None or signal.baseline_ref is None:
        raise CuriosityProvenanceError(
            "a destructive signal must name both the component's governed category and "
            "the current baseline/reference it is measured against")
    try:
        component_kind = DestructiveTargetKind(signal.component_kind)
    except ValueError as exc:
        raise CuriosityProposalValidationError(
            f"destructive component kind {signal.component_kind!r} is not a governed "
            f"category") from exc

    parent_refs, parent_proposal_identity = _proposal_ancestry(signal, parent_proposal)
    proposal = GeneratedResearchProposal.create(
        mode=CuriosityMode.DESTRUCTIVE,
        signal_identity=signal.signal_identity,
        target_kind=signal.target_kind,
        target_ref=signal.target_ref,
        reason_code=signal.reason_code,
        evidence_reference=signal.evidence_reference,
        evidence_boundary=signal.evidence_boundary,
        component_ref=signal.component_ref,
        component_kind=component_kind,
        baseline_ref=signal.baseline_ref,
        parent_proposal_identity=parent_proposal_identity,
        parent_research_refs=parent_refs,
        note=note,
    )
    return _reject_duplicate_parent_question(proposal, parent_proposal)


# -- The Wave 2 -> Wave 0 seam ------------------------------------------------


@dataclass(frozen=True)
class CuriosityOutcome:
    """
    The complete, immutable result of processing ONE proposal.

    `eligibility_state` is the authoritative verdict recorded verbatim: the
    COMMITTED Wave 2 verdict for an interaction, or the narrow destructive
    evidence verdict for a component challenge. Curiosity never reinterprets,
    re-derives or "improves" it.

    `is_investigation_ready` is deliberately NOT the same thing as "executed":
    it means the generated research record exists and may be investigated by a
    LATER, separately governed stage. Nothing in Wave 3 executes it.
    """

    proposal: GeneratedResearchProposal
    eligibility_state: EligibilityState
    decision: EligibilityDecision | DestructiveEligibilityDecision
    registered: bool
    record: GeneratedResearchRecord | None = None
    deduplicated: bool = False

    @property
    def is_investigation_ready(self) -> bool:
        """Whether a governed generated research record now exists."""
        return self.registered and self.record is not None

    @property
    def generated_research_id(self) -> str | None:
        return None if self.record is None else self.record.generated_research_id

    @property
    def reason_codes(self) -> tuple[str, ...]:
        return self.decision.reason_codes


class CuriosityProcessor:
    """
    The single governed seam from a proposal to Wave 0 generated research.

    This is deliberately a SMALL, explicit object rather than a plugin framework
    or an autonomous agent. It holds a `DimensionRegistry` (Wave 1 authority) and
    a `GeneratedResearchStore` (Wave 0 authority), and it does exactly three
    things per proposal:

        1. run the COMMITTED Wave 2 gate for an interaction, or the narrow
           destructive evidence gate for a component challenge;
        2. register through the COMMITTED Wave 0 store ONLY on PERMIT;
        3. retain the refusal / waiting / blocked provenance in the outcome.

    It executes no research, creates no hypothesis, finding or candidate, and
    reaches no production authority. It performs no I/O of its own beyond the
    explicit Wave 0 store registration the caller asked for.
    """

    def __init__(
        self,
        registry: DimensionRegistry,
        store: GeneratedResearchStore,
    ) -> None:
        self._registry = _require_registry(registry)
        if not isinstance(store, GeneratedResearchStore):
            raise CuriosityProposalValidationError(
                f"expected a Wave 0 GeneratedResearchStore, got "
                f"{type(store).__name__}; a second question store may never be created")
        self._store = store
        self._decisions_by_boundary: dict[
            tuple[str, str], EligibilityDecision | DestructiveEligibilityDecision
        ] = {}

    # -- Introspection -----------------------------------------------------

    @property
    def registry(self) -> DimensionRegistry:
        return self._registry

    @property
    def store(self) -> GeneratedResearchStore:
        return self._store

    def __len__(self) -> int:
        return len(self._store)

    # -- The seam ----------------------------------------------------------

    def process(
        self,
        proposal: GeneratedResearchProposal,
        *,
        freeze: Any = None,
        destructive_freeze: DestructiveEligibilityEvidenceFreeze | None = None,
        parent_interaction: ResearchInteraction | None = None,
        parent_decision: EligibilityDecision | None = None,
        t0: str = "",
    ) -> CuriosityOutcome:
        """
        Evaluate ONE proposal through Wave 2 and, only on PERMIT, register it
        through Wave 0.

        `freeze` is the immutable T0 `EligibilityEvidenceFreeze` the caller
        froze for THIS proposal's interaction. It is mandatory whenever the
        proposal proposes a governed interaction: without frozen evidence there
        is nothing for Wave 2 to decide against, and fabricating one is not
        permitted.

        `t0` is provenance only. It is not part of any identity, so re-running
        the same proposal at a different time reaches the same verdict and the
        same `GEN-*` identity.
        """
        if not isinstance(proposal, GeneratedResearchProposal):
            raise CuriosityProposalValidationError(
                f"expected a GeneratedResearchProposal, got {type(proposal).__name__}")

        decision: EligibilityDecision | DestructiveEligibilityDecision
        if proposal.requires_wave2:
            if destructive_freeze is not None:
                raise CuriosityProposalValidationError(
                    "an interaction proposal must not use destructive eligibility evidence")
            decision = self._evaluate_wave2(
                proposal,
                freeze=freeze,
                parent_interaction=parent_interaction,
                parent_decision=parent_decision,
                t0=t0,
            )
        else:
            if freeze is not None or parent_interaction is not None or parent_decision is not None:
                raise CuriosityProposalValidationError(
                    "a DESTRUCTIVE proposal must use its governed destructive eligibility "
                    "freeze, not an interaction freeze or parent")
            if destructive_freeze is None:
                raise CuriosityProposalValidationError(
                    "DESTRUCTIVE registration requires authoritative governed eligibility; "
                    "mode alone is never permission")
            decision = evaluate_destructive_eligibility(proposal, destructive_freeze)

        decision = self._bind_replay(proposal, decision)
        # The verdict is recorded EXACTLY. Curiosity never collapses, downgrades,
        # upgrades or reinterprets it. Only PERMIT reaches registration.
        if decision.state is not EligibilityState.PERMIT:
            return CuriosityOutcome(
                proposal=proposal,
                eligibility_state=decision.state,
                decision=decision,
                registered=False,
                record=None,
            )

        return self._register(proposal, decision)

    def _bind_replay(
        self,
        proposal: GeneratedResearchProposal,
        decision: EligibilityDecision | DestructiveEligibilityDecision,
    ) -> EligibilityDecision | DestructiveEligibilityDecision:
        """Prevent a proposal from changing verdict inside one evidence boundary."""
        boundary = proposal.evidence_boundary
        key = (proposal.proposal_identity, boundary)
        existing = self._decisions_by_boundary.get(key)
        if existing is not None and existing.semantic_identity != decision.semantic_identity:
            raise CuriosityProposalValidationError(
                "the proposal already has a different immutable eligibility decision at "
                f"evidence boundary {boundary!r}; materially changed evidence requires a "
                "new signal/proposal boundary")
        self._decisions_by_boundary[key] = decision
        return decision

    # -- Internals ---------------------------------------------------------

    def _evaluate_wave2(
        self,
        proposal: GeneratedResearchProposal,
        *,
        freeze: Any,
        parent_interaction: ResearchInteraction | None,
        parent_decision: EligibilityDecision | None,
        t0: str,
    ) -> EligibilityDecision:
        from research_engine.lifecycle.eligibility_evidence_freeze import (
            EligibilityEvidenceFreeze,
        )

        if not isinstance(freeze, EligibilityEvidenceFreeze):
            raise CuriosityProposalValidationError(
                f"a proposal proposing a governed interaction requires an immutable T0 "
                f"EligibilityEvidenceFreeze, got {type(freeze).__name__}; Wave 2 "
                f"eligibility is never decided without frozen evidence")
        if proposal.evidence_boundary != freeze.evidence_boundary:
            raise CuriosityProposalValidationError(
                "proposal evidence_boundary does not match the supplied Wave 2 evidence "
                "freeze; evidence may not be silently substituted")

        interaction = proposal.proposed_interaction
        parent = parent_interaction
        if proposal.parent_interaction_identity is not None:
            if parent is None:
                raise CuriosityProposalValidationError(
                    f"proposal declares parent interaction "
                    f"{proposal.parent_interaction_identity!r} but no parent interaction "
                    f"was supplied; an expansion is never decided without its parent")
            if parent.interaction_identity != proposal.parent_interaction_identity:
                raise CuriosityProposalValidationError(
                    f"supplied parent interaction {parent.interaction_identity!r} does "
                    f"not match the parent the proposal was built from "
                    f"({proposal.parent_interaction_identity!r})")
            if not isinstance(parent_decision, EligibilityDecision):
                raise CuriosityProposalValidationError(
                    "a child interaction requires its exact parent EligibilityDecision")
            if parent_decision.interaction_identity != parent.interaction_identity:
                raise CuriosityProposalValidationError(
                    "parent decision does not correspond to the supplied parent interaction")
            if parent_decision.decision_identity != proposal.parent_decision_identity:
                raise CuriosityProposalValidationError(
                    "parent decision does not match the exact decision preserved by the proposal")
            if not parent_decision.permitted:
                raise CuriosityProposalValidationError(
                    "a non-PERMIT parent decision cannot authorise child progression")
        elif parent is not None:
            raise CuriosityProposalValidationError(
                "a depth-1 proposal must not be given a parent interaction; Wave 2 "
                "decides that a 1D interaction has no lower-dimensional parent")

        # Straight delegation. No Wave 2 rule is duplicated, re-implemented or
        # second-guessed here.
        return evaluate_interaction_eligibility(
            interaction,
            freeze,
            self._registry,
            parent=parent,
            parent_decision=parent_decision,
            justification=proposal.expansion_justification,
            t0=t0,
        )

    def _register(
        self,
        proposal: GeneratedResearchProposal,
        decision: EligibilityDecision | DestructiveEligibilityDecision,
    ) -> CuriosityOutcome:
        """Register through the COMMITTED Wave 0 store. Deduplicates by identity."""
        if decision.state is not EligibilityState.PERMIT:
            raise CuriosityProposalValidationError(
                "only an authoritative PERMIT decision may register generated research")
        interaction = proposal.proposed_interaction
        wave0 = Wave0Proposal(
            research_kind=proposal.research_kind,
            trigger_ref=_trigger_ref_for(proposal),
            target_kind=proposal.target_kind,
            target_ref=proposal.target_ref,
            dimension_ref=(
                interaction.interaction_identity if interaction is not None
                else proposal.added_dimension_identity),
            parent_refs=tuple(proposal.parent_research_refs),
            specification=_wave0_specification(proposal, decision),
        )
        provenance = _wave0_provenance(proposal, decision)
        prospective = GeneratedResearchRecord.create(wave0, provenance=provenance)
        if f"generated_research:{prospective.generated_research_id}" in wave0.parent_refs:
            from research_engine.lifecycle.curiosity_signal import CuriosityRecursionError
            raise CuriosityRecursionError(
                "generated research may not directly reference itself as a parent")
        before = len(self._store)
        record = self._store.register(
            wave0, provenance=provenance)
        return CuriosityOutcome(
            proposal=proposal,
            eligibility_state=decision.state,
            decision=decision,
            registered=True,
            record=record,
            deduplicated=len(self._store) == before,
        )


# -- Wave 0 identity material -------------------------------------------------


def _trigger_ref_for(proposal: GeneratedResearchProposal) -> str:
    """
    The governed `trigger_ref` the Wave 0 record carries.

    It addresses the CURIOUSITY SIGNAL that caused the proposal -- not the
    proposal itself -- so two equivalent proposals from equivalent evidence
    always resolve to the same `trigger_ref`, and a later wave can trace any
    generated research question back to the machine event that caused it.
    """
    return f"signal:{proposal.signal_identity}"


def _wave0_specification(
    proposal: GeneratedResearchProposal,
    decision: EligibilityDecision | DestructiveEligibilityDecision,
) -> dict[str, Any]:
    """
    The scientific specification carried by the Wave 0 record.

    It preserves everything Wave 3 is required to preserve: curiosity mode,
    trigger/source reference, target, interaction reference, parent provenance,
    definition version and the exact Wave 2 outcome. It contains no timestamp, no
    counter and no insertion order, so two runs over the same inputs produce
    byte-identical specification material and therefore the same `GEN-*` identity.
    """
    return {
        "kind": "curiosity_generated_research",
        "definition_version": proposal.definition_version,
        "curiosity_mode": proposal.mode.value,
        "signal_identity": proposal.signal_identity,
        "reason_code": proposal.reason_code,
        "evidence_reference": proposal.evidence_reference,
        "evidence_boundary": proposal.evidence_boundary,
        "proposal_identity": proposal.proposal_identity,
        "proposal_semantic_identity": proposal.semantic_identity,
        "proposed_interaction_identity": proposal.interaction_identity,
        "added_dimension_identity": proposal.added_dimension_identity,
        "parent_interaction_identity": proposal.parent_interaction_identity,
        "parent_proposal_identity": proposal.parent_proposal_identity,
        "parent_decision_identity": proposal.parent_decision_identity,
        "expansion_justification_identity": (
            proposal.expansion_justification.justification_identity
            if proposal.expansion_justification is not None else None),
        "component": (
            None if proposal.component_ref is None else {
                "ref": proposal.component_ref,
                "kind": (
                    proposal.component_kind.value
                    if isinstance(proposal.component_kind, DestructiveTargetKind)
                    else proposal.component_kind),
                "baseline_ref": proposal.baseline_ref,
            }),
        "eligibility": {
            "governed_by_wave2": proposal.requires_wave2,
            "state": decision.state.value,
            "decision_identity": decision.decision_identity,
            "evidence_identity": decision.evidence_identity,
            "evidence_boundary": proposal.evidence_boundary,
            "specification_identity": getattr(decision, "specification_identity", None),
            "parent_justification_identity": (
                getattr(decision, "parent_justification_identity", None)),
            "reason_codes": list(decision.reason_codes),
        },
    }


def _wave0_provenance(
    proposal: GeneratedResearchProposal,
    decision: EligibilityDecision | DestructiveEligibilityDecision,
) -> Mapping[str, Any]:
    """
    Non-semantic provenance for the Wave 0 record's provenance payload.

    Only human-readable material lives here: the optional note, and the
    deterministic identity of the proposal that produced the record. This payload
    is NOT part of the `GEN-*` semantic identity, so changing a note can never
    mint a second generated research record.
    """
    return {
        "note": proposal.note,
        "proposal_identity": proposal.proposal_identity,
        "proposal_created_at": proposal.created_at,
        "eligibility_state": decision.state.value,
    }


__all__ = [
    "CuriosityOutcome",
    "CuriosityProcessor",
    "DestructiveEligibilityDecision",
    "DestructiveEligibilityEvidenceFreeze",
    "evaluate_destructive_eligibility",
    "propose_destructive",
    "propose_expansion",
    "propose_interaction",
]

