"""
Research Protocol Scope v1 -- mechanical scope-drift enforcement.

Stage 3 / Wave 6. `research_protocol` says what an investigation MAY mean. This
module is the guardrail that will sit around a future autonomous research
executor and make the answer "no" mechanically, before any work happens.

THE PROBLEM THIS SOLVES
=======================
A pre-registration that cannot be checked is a suggestion. Once an investigation
is running, the pressures toward drift are real and gradual:

    "while we were aggregating the stop-distance dimension we also noticed the
     entry-timing dimension behaved oddly, so we looked at that too"

Each individual step is small, and by the end the investigation is no longer the
one that was registered. There is then no honest way to report it: it was neither
the pre-registered investigation nor a declared new one.

TWO ASSERTIONS, BOTH FAIL-CLOSED
================================
`assert_operation_permitted(protocol, operation, ...)` answers the narrow
question: is this kind of research activity, against this evidence and this
scope, inside the contract?

`assert_scope_consistent(protocol, intent)` answers the full question: is this
whole intended unit of work inside the contract? It checks the operation, the
subject, every governed component, the evidence boundary, the evidence
population, the comparison and the confirmation requirement -- and it fails on
the FIRST violation, never on a "mostly in scope" verdict.

Both raise `ScopeDriftError`. There is no permissive mode, no warning mode and no
return-a-bool mode, because a caller that can ignore the answer will eventually
ignore it.

DISCOVERY IS NOT SCOPE EXPANSION
================================
`escalate_out_of_scope_discovery` is the structural answer to "we found something
else interesting". It does not widen anything: the protocol is returned
unchanged and the discovery is returned as a governed, deterministically
identified `OutOfScopeDiscovery` carrying the governed reason it is out of scope
and the escalation route it must take. Realising that route -- a Wave 3 curiosity
signal, a proposal, Wave 2 feasibility, a Wave 5 opportunity, a future protocol --
is deliberately NOT implemented here. Wave 6 supplies the policy and the
provenance; it does not build the next wave's loop.

This module is DECLARATIVE. It never performs research, never calls an executor
and never imports a runner, orchestrator, candidate or treatment path.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from research_engine.lifecycle.generated_research_identity import canonical_json
from research_engine.lifecycle.research_opportunity import ResearchOpportunity
from research_engine.lifecycle.research_protocol import (
    GLOBALLY_PROHIBITED_OPERATIONS,
    RESEARCH_PROTOCOL_SCHEMA_VERSION,
    ComparisonKind,
    EscalationRoute,
    ProhibitedOperation,
    ProtocolFreeze,
    ProtocolStatus,
    ResearchOperation,
    ResearchProtocol,
    ResearchProtocolValidationError,
    ScopeComponentKind,
    is_research_protocol_identity,
)
from research_engine.lifecycle.search_provenance import ConfirmationPolicy

#: The namespace of a recorded out-of-scope discovery. Deliberately NOT a new
#: governed research identity: a discovery is not a question, a finding or a
#: candidate. It is a note that says "this became new governed research work, and
#: here is why it could not stay here".
OUT_OF_SCOPE_DISCOVERY_ID_PREFIX = "OOD-"
OUT_OF_SCOPE_DISCOVERY_ID_DIGEST_CHARS = 16


class ScopeDriftError(ResearchProtocolValidationError):
    """
    Intended research falls outside its registered protocol. Always fatal.

    A distinct type, not a generic validation error, because this is the failure
    the whole module exists to make impossible to ignore: a caller catching
    `ResearchProtocolValidationError` for ordinary input problems must not be able
    to swallow a scope violation along with them.
    """


def _digest(material: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(material).encode("utf-8")).hexdigest()


# ═══ The declared intent of a piece of research work ════════════════════════


@dataclass(frozen=True)
class ResearchIntent:
    """
    A DECLARATION of one intended unit of research work, to be checked against a
    protocol BEFORE it happens.

    This is a description, not a request to do anything: constructing one runs
    nothing, and no field of it is a callable, a path or a handle to a system. It
    exists so that a future executor must state its intent in governed terms and
    be checked, rather than simply proceeding and being reviewed afterwards.

    Every field is optional EXCEPT `operation`, because a piece of work that does
    not say what kind of analysis it is cannot be checked against a protocol at
    all. Supplying nothing and asking "is this allowed?" would make the guardrail
    trivially bypassable, so the narrow assertion is about an operation, and the
    full assertion is about a complete intent.

    The optional fields default to None, meaning "this piece of work says nothing
    about that dimension", which is DIFFERENT from a declared
    `ComparisonKind.NONE`. A declared `NONE` is an assertion that the work makes
    no comparison and is checked against the protocol; an unspecified comparison
    is silence and is not checked. Conflating the two would make an intent that
    merely performs a slice look like a comparison change, which is exactly the
    kind of false positive that trains callers to ignore the guardrail.
    """

    operation: ResearchOperation
    subject_ref: str = ""
    dimension_identities: tuple[str, ...] = ()
    interaction_identities: tuple[str, ...] = ()
    slice_identities: tuple[str, ...] = ()
    evidence_boundary: str = ""
    discovery_population_identity: str = ""
    comparison_kind: ComparisonKind | None = None
    confirmation_policy: ConfirmationPolicy | None = None
    requested_prohibited_authority: ProhibitedOperation | None = None
    note: str = ""                         # provenance only; NOT checked

    def __post_init__(self) -> None:
        object.__setattr__(self, "operation", self._coerce_operation(
            self.operation))
        for name in ("dimension_identities", "interaction_identities",
                     "slice_identities"):
            value = getattr(self, name)
            if value is None:
                value = ()
            if isinstance(value, (str, bytes)) or not isinstance(
                    value, (list, tuple)):
                raise ResearchProtocolValidationError(
                    f"intent {name} must be a sequence of governed identities")
            identities = tuple(sorted({str(item) for item in value}))
            object.__setattr__(self, name, identities)
        if self.comparison_kind is not None and isinstance(
                self.comparison_kind, str):
            try:
                object.__setattr__(self, "comparison_kind", ComparisonKind(
                    self.comparison_kind))
            except ValueError as exc:
                raise ResearchProtocolValidationError(
                    f"intent comparison_kind {self.comparison_kind!r} is not a "
                    f"governed ComparisonKind") from exc
        if (self.requested_prohibited_authority is not None
                and not isinstance(
                    self.requested_prohibited_authority, ProhibitedOperation)):
            try:
                object.__setattr__(self, "requested_prohibited_authority",
                                   ProhibitedOperation(
                                       self.requested_prohibited_authority))
            except ValueError as exc:
                raise ResearchProtocolValidationError(
                    f"intent requested_prohibited_authority "
                    f"{self.requested_prohibited_authority!r} is not a governed "
                    f"ProhibitedOperation") from exc

    @staticmethod
    def _coerce_operation(value: Any) -> ResearchOperation:
        if isinstance(value, ResearchOperation):
            return value
        try:
            return ResearchOperation(value)
        except (ValueError, TypeError) as exc:
            raise ResearchProtocolValidationError(
                f"intent operation {value!r} is not a governed ResearchOperation "
                f"member; the vocabulary is closed: "
                f"{[item.value for item in ResearchOperation]}") from exc

    def components_by_kind(self) -> Mapping[ScopeComponentKind, tuple[str, ...]]:
        return {
            ScopeComponentKind.DIMENSION: self.dimension_identities,
            ScopeComponentKind.INTERACTION: self.interaction_identities,
            ScopeComponentKind.SLICE: self.slice_identities,
        }



# ═══ The two assertions ═════════════════════════════════════════════════════


def assert_operation_permitted(
    protocol: ResearchProtocol,
    operation: ResearchOperation | str,
    *,
    evidence_boundary: str = "",
    discovery_population_identity: str = "",
    comparison_kind: ComparisonKind | str | None = None,
    requested_prohibited_authority: ProhibitedOperation | str | None = None,
) -> ResearchOperation:
    """
    Fail closed unless ONE research activity is inside this protocol's contract.

    Returns the coerced, governed operation on success purely so a caller can
    record exactly what was authorised; the return value is never a permission
    token and confers no authority of its own.

    Checks, in order, each of which is independently fatal:

      1. the operation is a member of the CLOSED governed vocabulary;
      2. no globally prohibited authority was requested alongside it;
      3. the protocol's status permits investigation at all;
      4. the operation is in the protocol's permitted set;
      5. the operation is NOT in the protocol's prohibited set;
      6. a supplied evidence boundary matches the protocol's exactly;
      7. a supplied discovery population matches the protocol's exactly;
      8. a supplied comparison kind matches the protocol's exactly.

    Every comparison is EXACT. A boundary one second later, a population that is a
    superset, a comparison that is "close enough" and a confirmation route that
    has been quietly downgraded are all drift, and all of them fail here.
    """
    if not isinstance(protocol, ResearchProtocol):
        raise ResearchProtocolValidationError(
            f"expected ResearchProtocol, got {type(protocol).__name__}")
    resolved = ResearchIntent._coerce_operation(operation)
    _refuse_prohibited_authority(protocol, requested_prohibited_authority)
    _refuse_inactive_protocol(protocol)
    _require_permitted(protocol, resolved)
    _require_exact_boundary(protocol, evidence_boundary)
    _require_exact_population(protocol, discovery_population_identity)
    _require_exact_comparison(protocol, comparison_kind)
    return resolved



def _refuse_prohibited_authority(
    protocol: ResearchProtocol,
    requested: ProhibitedOperation | str | None,
) -> None:
    """
    Refuse a request for forbidden authority AS SUCH, before anything else.

    This is checked first and separately on purpose. A request that bundles "run
    this analysis and promote the resulting candidate" is not an analysis that
    happens to be slightly out of scope: it is a request for authority Stage 3
    does not have, and the refusal must say so rather than reporting a tidy
    "operation not permitted". The forbidden item is never stripped from the
    request and the remainder allowed to proceed.
    """
    if requested is None:
        return
    authority = requested
    if not isinstance(authority, ProhibitedOperation):
        try:
            authority = ProhibitedOperation(authority)
        except (ValueError, TypeError) as exc:
            raise ScopeDriftError(
                f"requested authority {requested!r} is not a governed "
                f"ProhibitedOperation; free text is not authority") from exc
    if authority in GLOBALLY_PROHIBITED_OPERATIONS:
        raise ScopeDriftError(
            f"{authority.value} is globally prohibited in Stage 3; a research protocol "
            f"may never authorise it and the request is refused rather than silently "
            f"discarded")


def _refuse_inactive_protocol(protocol: ResearchProtocol) -> None:
    """A cancelled or superseded protocol investigates nothing further."""
    if protocol.status is ProtocolStatus.CANCELLED:
        raise ScopeDriftError(
            f"protocol {protocol.protocol_identity} is CANCELLED; it authorises no "
            f"research activity")
    if protocol.status is ProtocolStatus.SUPERSEDED:
        raise ScopeDriftError(
            f"protocol {protocol.protocol_identity} has been SUPERSEDED; research must "
            f"continue under the superseding protocol, not the one it replaced")


def _require_permitted(
    protocol: ResearchProtocol, operation: ResearchOperation) -> None:
    """The operation must be permitted and must not be explicitly prohibited."""
    permitted = {item.value for item in protocol.permitted_operations}
    prohibited = {item.value for item in protocol.prohibited_operations}
    if operation.value in prohibited:
        raise ScopeDriftError(
            f"operation {operation.value} is explicitly PROHIBITED by protocol "
            f"{protocol.protocol_identity}")
    if operation.value not in permitted:
        raise ScopeDriftError(
            f"operation {operation.value} is not in the permitted set of protocol "
            f"{protocol.protocol_identity}; the permitted operations are "
            f"{sorted(permitted)}. Declaring an operation the protocol does not "
            f"contain is scope drift, not a request to widen the protocol")


def _registered_population(protocol: ResearchProtocol) -> str:
    """The one discovery population this protocol is bound to, if any."""
    if protocol.evidence.discovery_population_identity:
        return protocol.evidence.discovery_population_identity
    if protocol.evidence.discovery_population is not None:
        return protocol.evidence.discovery_population.population_identity
    return ""


def _require_exact_boundary(protocol: ResearchProtocol, boundary: str) -> None:
    """Evidence may be neither widened nor re-dated mid-investigation."""
    if not boundary:
        return
    if boundary != protocol.evidence.evidence_boundary:
        raise ScopeDriftError(
            f"evidence boundary {boundary!r} does not match the frozen boundary "
            f"{protocol.evidence.evidence_boundary!r} of protocol "
            f"{protocol.protocol_identity}")


def _require_exact_population(
    protocol: ResearchProtocol, population_identity: str) -> None:
    """A protocol may never be pointed at a different population."""
    if not population_identity:
        return
    expected = _registered_population(protocol)
    if population_identity != expected:
        raise ScopeDriftError(
            f"discovery population {population_identity!r} is not the population "
            f"registered by protocol {protocol.protocol_identity} ({expected!r}); a "
            f"protocol may never be pointed at a different population")


def _require_exact_comparison(
    protocol: ResearchProtocol, comparison_kind: Any) -> None:
    """A comparison may not be changed once results exist."""
    if comparison_kind is None:
        return
    resolved = comparison_kind
    if not isinstance(resolved, ComparisonKind):
        try:
            resolved = ComparisonKind(resolved)
        except ValueError as exc:
            raise ScopeDriftError(
                f"comparison kind {comparison_kind!r} is not a governed ComparisonKind"
            ) from exc
    if resolved is not protocol.comparison.kind:
        raise ScopeDriftError(
            f"comparison kind {resolved.value} does not match the pre-registered "
            f"comparison {protocol.comparison.kind.value} of protocol "
            f"{protocol.protocol_identity}; a comparison may not be changed once "
            f"results exist")



def assert_scope_consistent(
    protocol: ResearchProtocol,
    intent: ResearchIntent,
    *,
    opportunity: ResearchOpportunity | None = None,
    freeze: ProtocolFreeze | None = None,
) -> ResearchIntent:
    """
    Fail closed unless a WHOLE intended unit of research work is inside the
    contract.

    This is the guardrail a future autonomous research executor would sit behind.
    It checks, in order:

      1. the operation, boundary, population and comparison (via
         `assert_operation_permitted`);
      2. any globally prohibited authority bundled into the intent;
      3. the subject, when the intent names one;
      4. EVERY governed component the intent touches, by kind, against BOTH the
         in-scope set and the explicitly excluded set;
      5. the confirmation policy, when the intent supplies one;
      6. the protocol's authorisation, when the opportunity is supplied;
      7. the T0 freeze, when one is supplied -- a protocol that has drifted from
         its own frozen contract cannot be executed against that freeze.

    There is no partial acceptance and no "mostly in scope" verdict: the first
    violation raises and nothing is returned. A caller that wants to know which
    rule it broke catches `ScopeDriftError` and reads the message; a caller that
    does not care still cannot proceed, because there is no other outcome.
    """
    if not isinstance(protocol, ResearchProtocol):
        raise ResearchProtocolValidationError(
            f"expected ResearchProtocol, got {type(protocol).__name__}")
    if not isinstance(intent, ResearchIntent):
        raise ResearchProtocolValidationError(
            f"expected ResearchIntent, got {type(intent).__name__}")

    assert_operation_permitted(
        protocol,
        intent.operation,
        evidence_boundary=intent.evidence_boundary,
        discovery_population_identity=intent.discovery_population_identity,
        comparison_kind=intent.comparison_kind,
        requested_prohibited_authority=intent.requested_prohibited_authority,
    )

    _require_same_subject(protocol, intent)
    _require_declared_components(protocol, intent)
    _require_same_confirmation(protocol, intent)

    if opportunity is not None:
        protocol.assert_binds_to(opportunity)
    if freeze is not None:
        if not isinstance(freeze, ProtocolFreeze):
            raise ResearchProtocolValidationError(
                f"expected ProtocolFreeze, got {type(freeze).__name__}")
        if freeze.protocol_identity != protocol.protocol_identity:
            raise ScopeDriftError(
                f"freeze {freeze.freeze_identity} binds protocol "
                f"{freeze.protocol_identity}, not {protocol.protocol_identity}")
        freeze.assert_belongs_to(protocol)
    return intent


def _require_same_subject(
    protocol: ResearchProtocol, intent: ResearchIntent) -> None:
    """A protocol may never be re-pointed at a different subject."""
    if not intent.subject_ref:
        return
    if intent.subject_ref != protocol.scope.subject_ref:
        raise ScopeDriftError(
            f"intent subject {intent.subject_ref!r} is not the registered subject "
            f"{protocol.scope.subject_ref!r} of protocol {protocol.protocol_identity}")


def _require_declared_components(
    protocol: ResearchProtocol, intent: ResearchIntent) -> None:
    """
    EVERY touched component must be declared in scope, and none may be excluded.

    The explicit `excluded` set is checked too, and is not redundant: it is what
    makes "this protocol deliberately does not examine entry timing" a mechanical
    refusal rather than an inference. An identity that is explicitly excluded is
    refused even if some other declaration would otherwise have admitted it.
    """
    in_scope = {(item.kind, item.identity) for item in protocol.scope.in_scope}
    excluded = {(item.kind, item.identity) for item in protocol.scope.excluded}
    for kind, identities in intent.components_by_kind().items():
        allowed = {identity for (item_kind, identity) in in_scope
                   if item_kind is kind}
        denied = {identity for (item_kind, identity) in excluded
                  if item_kind is kind}
        for identity in identities:
            if identity in denied:
                raise ScopeDriftError(
                    f"{kind.value} {identity} is explicitly EXCLUDED by protocol "
                    f"{protocol.protocol_identity}; examining it would widen the "
                    f"registered investigation")
            if identity not in allowed:
                raise ScopeDriftError(
                    f"{kind.value} {identity} is not declared in scope by protocol "
                    f"{protocol.protocol_identity}; the in-scope {kind.value} set is "
                    f"{sorted(allowed)}. Introducing an undeclared component is scope "
                    f"drift, not a discovery")



def _require_same_confirmation(
    protocol: ResearchProtocol, intent: ResearchIntent) -> None:
    """
    The confirmation route may not be changed, and may not be quietly weakened.

    Both directions matter: substituting a weaker policy is the obvious failure,
    but substituting a DIFFERENT policy -- even a stricter one -- also invalidates
    the pre-registration, because the investigation that was registered is not the
    investigation that would be run.
    """
    if intent.confirmation_policy is None:
        return
    registered = protocol.evidence.confirmation_policy
    if registered is None:
        raise ScopeDriftError(
            f"intent supplies a confirmation policy, but protocol "
            f"{protocol.protocol_identity} registered none")
    if (intent.confirmation_policy.semantic_material()
            != registered.semantic_material()):
        raise ScopeDriftError(
            f"confirmation policy {intent.confirmation_policy.semantic_material()} "
            f"contradicts the registered policy {registered.semantic_material()} of "
            f"protocol {protocol.protocol_identity}; the confirmation route is frozen "
            f"at pre-registration")


# ═══ Out-of-scope discovery ══════════════════════════════════════════════════


class OutOfScopeReason(str, Enum):
    """
    Why a discovery could not simply be absorbed into the running investigation.

    A closed vocabulary, so an escalation is always attributable to a governed
    cause. The free-text version of this -- "it was just so interesting" -- is
    precisely what this module refuses to record.
    """

    UNDECLARED_DIMENSION = "UNDECLARED_DIMENSION"
    UNDECLARED_INTERACTION = "UNDECLARED_INTERACTION"
    UNDECLARED_SLICE = "UNDECLARED_SLICE"
    EXPLICITLY_EXCLUDED_COMPONENT = "EXPLICITLY_EXCLUDED_COMPONENT"
    DIFFERENT_SUBJECT = "DIFFERENT_SUBJECT"
    OUTSIDE_EVIDENCE_BOUNDARY = "OUTSIDE_EVIDENCE_BOUNDARY"
    DIFFERENT_POPULATION = "DIFFERENT_POPULATION"
    REQUIRES_DIFFERENT_COMPARISON = "REQUIRES_DIFFERENT_COMPARISON"
    REQUIRES_PROHIBITED_AUTHORITY = "REQUIRES_PROHIBITED_AUTHORITY"


_REASON_FOR_KIND = {
    ScopeComponentKind.DIMENSION: OutOfScopeReason.UNDECLARED_DIMENSION,
    ScopeComponentKind.INTERACTION: OutOfScopeReason.UNDECLARED_INTERACTION,
    ScopeComponentKind.SLICE: OutOfScopeReason.UNDECLARED_SLICE,
}



@dataclass(frozen=True)
class OutOfScopeDiscovery:
    """
    A finding that is genuinely interesting and genuinely NOT permitted here.

    This record is the structural heart of "discovery does not expand the
    protocol". It states, permanently and mechanically:

        - WHICH protocol was running when the discovery was made;
        - WHAT was discovered, as a governed identity or reference;
        - WHY it is out of scope, as a closed governed reason code;
        - WHERE it must go next, as the protocol's escalation route.

    It is deliberately NOT a research question, a finding, a hypothesis, a
    candidate or a treatment. It grants no authority to investigate anything; it
    only records that new governed research work is now owed. Turning it into
    actual work requires the existing Wave 3 -> Wave 2 -> Wave 5 -> Wave 6
    lifecycle, which is deliberately not implemented here.

    `protocol_identity` and `protocol_semantic_identity` are recorded together on
    purpose: a reader can prove both WHICH contract was in force and EXACTLY what
    it said, long after the running investigation has moved on.
    """

    protocol_identity: str
    protocol_semantic_identity: str
    discovered_kind: ScopeComponentKind | None
    discovered_identity: str
    reason: OutOfScopeReason
    escalation_route: EscalationRoute
    note: str = ""                         # provenance only; NOT identity
    schema_version: int = RESEARCH_PROTOCOL_SCHEMA_VERSION
    semantic_identity: str = ""
    discovery_identity: str = ""

    def __post_init__(self) -> None:
        if not is_research_protocol_identity(self.protocol_identity):
            raise ResearchProtocolValidationError(
                f"an out-of-scope discovery must name the `RPL-*` protocol it was "
                f"found under, got {self.protocol_identity!r}")
        if not isinstance(self.reason, OutOfScopeReason):
            raise ResearchProtocolValidationError(
                f"out-of-scope reason must be a governed OutOfScopeReason, got "
                f"{self.reason!r}")
        if not isinstance(self.escalation_route, EscalationRoute):
            raise ResearchProtocolValidationError(
                f"escalation route must be a governed EscalationRoute, got "
                f"{self.escalation_route!r}")
        if self.discovered_kind is not None and not isinstance(
                self.discovered_kind, ScopeComponentKind):
            raise ResearchProtocolValidationError(
                f"discovered_kind must be a governed ScopeComponentKind, got "
                f"{self.discovered_kind!r}")
        if not isinstance(self.discovered_identity, str) or (
                not self.discovered_identity.strip()):
            raise ResearchProtocolValidationError(
                "an out-of-scope discovery must name WHAT was discovered")
        expected = _digest(self.semantic_material())
        expected_id = f"{OUT_OF_SCOPE_DISCOVERY_ID_PREFIX}{expected[:16].upper()}"
        if self.semantic_identity:
            if self.semantic_identity != expected:
                raise ResearchProtocolValidationError(
                    "presented out-of-scope discovery semantic identity does not "
                    "match the discovery material")
        else:
            object.__setattr__(self, "semantic_identity", expected)
        if self.discovery_identity:
            if self.discovery_identity != expected_id:
                raise ResearchProtocolValidationError(
                    f"presented out-of-scope discovery identity "
                    f"{self.discovery_identity!r} does not match the discovery material")
        else:
            object.__setattr__(self, "discovery_identity", expected_id)

    def semantic_material(self) -> dict[str, Any]:
        return {
            "kind": "out_of_scope_discovery",
            "schema_version": self.schema_version,
            "protocol_identity": self.protocol_identity,
            "protocol_semantic_identity": self.protocol_semantic_identity,
            "discovered_kind": (
                None if self.discovered_kind is None
                else self.discovered_kind.value),
            "discovered_identity": self.discovered_identity,
            "reason": self.reason.value,
            "escalation_route": self.escalation_route.value,
        }



def classify_out_of_scope(
    protocol: ResearchProtocol, intent: ResearchIntent) -> OutOfScopeReason:
    """
    Determine WHY an intent is outside the protocol, as a governed reason code.

    Raises `ScopeDriftError` when the intent is IN scope -- there is no
    "out-of-scope reason" for work that is actually permitted, and inventing one
    would let a caller relabel legitimate work as an escalation. Returns the
    single most specific reason otherwise, checked in the same order as
    `assert_scope_consistent` so the two can never disagree.
    """
    if not isinstance(protocol, ResearchProtocol):
        raise ResearchProtocolValidationError(
            f"expected ResearchProtocol, got {type(protocol).__name__}")
    if not isinstance(intent, ResearchIntent):
        raise ResearchProtocolValidationError(
            f"expected ResearchIntent, got {type(intent).__name__}")

    if intent.requested_prohibited_authority is not None:
        return OutOfScopeReason.REQUIRES_PROHIBITED_AUTHORITY
    if intent.subject_ref and intent.subject_ref != protocol.scope.subject_ref:
        return OutOfScopeReason.DIFFERENT_SUBJECT
    if intent.evidence_boundary and (
            intent.evidence_boundary != protocol.evidence.evidence_boundary):
        return OutOfScopeReason.OUTSIDE_EVIDENCE_BOUNDARY
    if intent.discovery_population_identity and (
            intent.discovery_population_identity
            != _registered_population(protocol)):
        return OutOfScopeReason.DIFFERENT_POPULATION
    if (intent.comparison_kind is not None
            and intent.comparison_kind is not protocol.comparison.kind):
        return OutOfScopeReason.REQUIRES_DIFFERENT_COMPARISON
    if intent.confirmation_policy is not None:
        registered = protocol.evidence.confirmation_policy
        if (registered is None or intent.confirmation_policy.semantic_material()
                != registered.semantic_material()):
            return OutOfScopeReason.DIFFERENT_POPULATION

    in_scope = {(item.kind, item.identity) for item in protocol.scope.in_scope}
    excluded = {(item.kind, item.identity) for item in protocol.scope.excluded}
    for kind, identities in intent.components_by_kind().items():
        for identity in identities:
            if (kind, identity) in excluded:
                return OutOfScopeReason.EXPLICITLY_EXCLUDED_COMPONENT
            if (kind, identity) not in in_scope:
                return _REASON_FOR_KIND[kind]
    raise ScopeDriftError(
        f"intent is IN SCOPE for protocol {protocol.protocol_identity}; it is not an "
        f"out-of-scope discovery, so it may be performed under the protocol directly "
        f"rather than escalated")


def escalate_out_of_scope_discovery(
    protocol: ResearchProtocol,
    intent: ResearchIntent,
    *,
    note: str = "",
) -> OutOfScopeDiscovery:
    """
    Record an out-of-scope discovery WITHOUT touching the protocol.

    The returned record is the ONLY thing that changes. The protocol is not
    widened, not re-frozen, not re-versioned and not annotated in place: this
    function takes a frozen dataclass and returns a new, separate record, so
    there is no code path here that could mutate the running investigation even
    by accident.

    The escalation route recorded is the protocol's OWN `ScopeChangePolicy`, so a
    protocol that declared a different route would escalate differently -- and
    since `ScopeChangePolicy` is identity material, changing the route is itself a
    new protocol identity rather than a runtime switch.
    """
    reason = classify_out_of_scope(protocol, intent)
    discovered_kind: ScopeComponentKind | None = None
    discovered_identity = intent.subject_ref
    for kind, identities in intent.components_by_kind().items():
        if identities:
            discovered_kind = kind
            discovered_identity = identities[0]
            break
    if not discovered_identity and intent.discovery_population_identity:
        discovered_identity = intent.discovery_population_identity
    return OutOfScopeDiscovery(
        protocol_identity=protocol.protocol_identity,
        protocol_semantic_identity=protocol.semantic_identity,
        discovered_kind=discovered_kind,
        discovered_identity=discovered_identity or intent.operation.value,
        reason=reason,
        escalation_route=protocol.scope_change_policy.escalation_route,
        note=note,
    )



__all__ = [
    "OUT_OF_SCOPE_DISCOVERY_ID_DIGEST_CHARS",
    "OUT_OF_SCOPE_DISCOVERY_ID_PREFIX",
    "OutOfScopeDiscovery",
    "OutOfScopeReason",
    "ResearchIntent",
    "ScopeDriftError",
    "assert_operation_permitted",
    "assert_scope_consistent",
    "classify_out_of_scope",
    "escalate_out_of_scope_discovery",
]

