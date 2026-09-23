"""Governed risk-policy evidence foundation for HD10 (R1-R5).

This module constructs the shared, deterministic risk-event evidence that R1-R5
need.  It is evidence construction only: it implements no estimator, no
simulation, no threshold selection, no sizing optimisation, no readiness status,
and no report.  Question readiness stays owned by the canonical readiness
machinery.

Frozen authority: ``research_engine.registry.risk_policy_adjudication``
(``HD10_ADJUDICATED_CONTRACT``, version
``hd10_risk_policy_estimation_and_simulation_v1``).  This module reads that
authority; it never redefines it.

Canonical grain
    One canonical opportunity is one scientific observation.  Account fanout and
    horizon fanout are collapsed before any sample count is produced.  Blocked
    opportunities use the governed shadow lifecycle outcome as their only
    admissible counterfactual.

Chronology
    Ordering authority is the governed shadow-runtime lifecycle entry UTC with
    the canonical opportunity id as the tie-break (HD10 R3-R5 order rule).
    Nothing is ordered by file path, ingestion order, or an arbitrary id, and no
    volatile filesystem metadata is ever hashed.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import math
import re
from typing import Any, Iterable, Mapping

from research_engine.control_plane.evidence_provenance import (
    build_evidence_provenance,
    evidence_digest,
    select_current_evidence,
)
from research_engine.control_plane.evidence_resolver import (
    classify_authoritative_evidence_record,
)
from research_engine.control_plane.evidence_readiness import (
    EvidenceReadiness,
    EvidenceRequirement,
    evaluate_evidence_readiness,
)
from research_engine.data_quality.classifier import DataEpoch
from research_engine.registry.risk_policy_adjudication import (
    BASELINE_RISK_POLICY_ID,
    BASELINE_RISK_POLICY_V1,
    GUARD_TAXONOMY_V1,
    HD10_ADJUDICATION_VERSION,
    VALIDATION_CHRONOLOGY,
    baseline_risk_policy_identity_hash,
)

SCHEMA_VERSION = "risk_policy_evidence_v1"
CHRONOLOGY_SCHEMA_VERSION = "risk_policy_chronology_v1"

DECISION_SOURCE = "decision_trace"
OUTCOME_SOURCE = "shadow_trades"

# ─── RISK DISPOSITION ─────────────────────────────────────────────────────────

ALLOWED = "ALLOWED"
BLOCKED = "BLOCKED"
RISK_DISPOSITIONS = (ALLOWED, BLOCKED)

# ─── FROZEN HD10 GUARD TAXONOMY ───────────────────────────────────────────────

GUARD_IDS: tuple[str, ...] = tuple(guard["guard_id"] for guard in GUARD_TAXONOMY_V1)
if GUARD_IDS != ("SPREAD", "CORRELATION", "REGIME", "DAILY_LOSS"):
    raise RuntimeError("HD10 guard taxonomy changed: RW9.1 requires the frozen four guards")

GUARD_TAXONOMY_IDENTITY = (
    f"HD10_ADJUDICATED_CONTRACT.guard_taxonomy@{HD10_ADJUDICATION_VERSION}"
)
GUARD_TAXONOMY_DIGEST = evidence_digest(guard for guard in GUARD_TAXONOMY_V1)

# Explicit production guard identifiers → the frozen HD10 vocabulary.  Sources:
# ``risk/runtime_guard_chain.py`` (guard chain ``guard_name`` values),
# ``core/runtime/cycle_guards.py`` (cycle guard names) and the frozen guard ids
# themselves.  Any other name is UNMAPPED and is never guessed into a guard.
GUARD_NAME_VOCABULARY_V1: Mapping[str, str] = {
    "spread_guard": "SPREAD",
    "spread": "SPREAD",
    "correlation_guard": "CORRELATION",
    "correlation": "CORRELATION",
    "regime_guard": "REGIME",
    "regime": "REGIME",
    "daily_loss_guard": "DAILY_LOSS",
    "daily_loss": "DAILY_LOSS",
}

GUARD_AUTHORITY_NOT_APPLICABLE = "NOT_APPLICABLE"
GUARD_AUTHORITY_CANONICAL = "CANONICAL"
GUARD_AUTHORITY_UNMAPPED = "UNMAPPED"
GUARD_AUTHORITY_MISSING = "MISSING"
GUARD_AUTHORITY_STATES = (
    GUARD_AUTHORITY_NOT_APPLICABLE,
    GUARD_AUTHORITY_CANONICAL,
    GUARD_AUTHORITY_UNMAPPED,
    GUARD_AUTHORITY_MISSING,
)

_GUARD_REASON_PREFIX = "risk_guard:"
_GUARD_VERDICT_FIELDS = ("guard", "guard_name", "blocking_guard", "risk_flag")
_GUARD_MARKER_ROLES = ("runtime_guard_rejection",)
_GUARD_MARKER_EVENTS = ("RISK_REJECTION",)
_GUARD_MARKER_TYPES = ("RISK_GUARD",)

# ─── BASELINE POLICY BINDING ──────────────────────────────────────────────────

BASELINE_AUTHORITY_CURRENT = "CURRENT"
BASELINE_AUTHORITY_MISSING = "BASELINE_RISK_POLICY_AUTHORITY_MISSING"
BASELINE_AUTHORITY_DRIFT = "BASELINE_RISK_POLICY_IDENTITY_DRIFT"

# ─── OUTCOME AUTHORITY ────────────────────────────────────────────────────────

OUTCOME_AUTHORITY = "governed_shadow_runtime_v1_lifecycle_outcome_r_multiple"
PRIMARY_HORIZON_SCOPE = "PRIMARY_HORIZON_SIMULATION"
HORIZON_ALTERNATIVE_SCOPE = "HORIZON_ALTERNATIVE"
OUTCOME_SCOPES = (PRIMARY_HORIZON_SCOPE, HORIZON_ALTERNATIVE_SCOPE)

# ─── CHRONOLOGY AUTHORITY ─────────────────────────────────────────────────────

CHRONOLOGY_AUTHORITY = "governed_shadow_runtime_v1_lifecycle_entry_utc"
DECISION_CHRONOLOGY_AUTHORITY = "governed_decision_trace_timestamp_utc"
CHRONOLOGICAL_ORDER_KEY = ("entry_utc_epoch_s", "canonical_opportunity_id")

_CALIBRATION_FRACTION = 0.70
MINIMUM_VALIDATION_OPPORTUNITIES = int(
    VALIDATION_CHRONOLOGY["minimum_validation_opportunities"]
)
if "earliest 70%" not in VALIDATION_CHRONOLOGY["windows"]["calibration"]:
    raise RuntimeError("HD10 calibration window rule changed: RW9.1 chronology split is stale")

# ─── STABLE EXCLUSION CONTRACT ────────────────────────────────────────────────

EXCLUSION_REASONS: Mapping[str, str] = {
    "MISSING_CANONICAL_IDENTITY": (
        "the governed record declares no canonical_opportunity_id, so no canonical "
        "observation can be formed"
    ),
    "AMBIGUOUS_CANONICAL_IDENTITY": (
        "governed records for one canonical opportunity disagree about its canonical "
        "identity, so the observation cannot be joined deterministically"
    ),
    "DECISION_OUTCOME_LINEAGE_MISMATCH": (
        "the governed decision and shadow outcome share a canonical opportunity id "
        "but disagree on declared entity, decision, correlation, or symbol lineage"
    ),
    "MISSING_DECISION_AUTHORITY": (
        "no CURRENT governed decision-trace verdict exists for the canonical opportunity"
    ),
    "AMBIGUOUS_DECISION_AUTHORITY": (
        "the CURRENT governed decision verdicts for one canonical opportunity cannot be "
        "reduced to a single risk disposition"
    ),
    "RISK_LAYER_VERDICT_MISSING": (
        "the CURRENT decision trace never reached the risk layer, so it carries no risk "
        "disposition to link"
    ),
    "MISSING_DECISION_CHRONOLOGY": (
        "the governed decision verdict carries no usable decision timestamp"
    ),
    "MISSING_GOVERNED_OUTCOME": (
        "the allowed opportunity has no CURRENT governed shadow outcome R-multiple"
    ),
    "MISSING_BLOCKED_COUNTERFACTUAL_OUTCOME": (
        "the risk-blocked opportunity has no CURRENT governed shadow-counterfactual "
        "outcome R-multiple, which HD10 requires as the only admissible counterfactual"
    ),
    "CONFLICTING_GOVERNED_OUTCOME": (
        "multiple governed shadow outcomes for one canonical opportunity conflict and "
        "cannot be reduced to one canonical outcome"
    ),
    "ACCOUNT_FANOUT_PSEUDOREPLICATION": (
        "account-level executions of one canonical opportunity cannot be reduced to one "
        "observation without counting the same account twice"
    ),
    "AMBIGUOUS_OUTCOME_AUTHORITY": (
        "governed outcomes for one canonical opportunity mix account-scoped and unscoped "
        "authority, so the canonical outcome cannot be established"
    ),
    "INVALID_R_MULTIPLE": (
        "the governed shadow outcome R-multiple is missing, non-numeric, or not finite"
    ),
    "OUTCOME_SCOPE_EXCLUDED": (
        "the governed shadow outcome is outside the declared outcome scope"
    ),
    "MISSING_CHRONOLOGY": "the governed lifecycle carries no entry UTC chronology",
    "AMBIGUOUS_CHRONOLOGY": (
        "governed records for one canonical opportunity declare conflicting entry "
        "chronology, so ordering cannot be established"
    ),
    "NON_CURRENT_DECISION_AUTHORITY_PRESENT": (
        "a non-CURRENT decision-trace record exists for this canonical opportunity, so "
        "its CURRENT risk disposition cannot be proven complete"
    ),
    "NON_CURRENT_OUTCOME_AUTHORITY_PRESENT": (
        "a non-CURRENT shadow outcome record exists for this canonical opportunity, so "
        "its CURRENT outcome cannot be proven complete"
    ),
    "OUTCOME_MISSING_CANONICAL_IDENTITY": (
        "a governed shadow outcome declares no canonical_opportunity_id"
    ),
    "MISSING_OUTCOME_CLOSE_CHRONOLOGY": (
        "the governed lifecycle carries no outcome-close chronology, so the HD10 purge "
        "rule cannot be applied"
    ),
    BASELINE_AUTHORITY_MISSING: (
        "no exact RISK_POLICY_BASELINE_V1 authority (policy id, version, identity hash) "
        "was available; HD10 forbids assuming a default policy"
    ),
    BASELINE_AUTHORITY_DRIFT: (
        "the supplied RISK_POLICY_BASELINE_V1 identity does not match its own content "
        "digest, or a governed record declares a different baseline identity"
    ),
}

PURGED_OUTCOME_WINDOW_OVERLAP = "PURGED_OUTCOME_WINDOW_OVERLAP"


# ─── VALUE HELPERS ────────────────────────────────────────────────────────────


def _text(value: Any) -> str:
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, float) and not math.isfinite(value):
        return ""
    return str(value).strip()


def _nested(record: Mapping[str, Any], *path: str) -> Any:
    value: Any = record
    for name in path:
        if not isinstance(value, Mapping):
            return None
        value = value.get(name)
    return value


def _first(record: Mapping[str, Any], *paths: tuple[str, ...]) -> Any:
    for path in paths:
        value = _nested(record, *path)
        if value is not None and value != "":
            return value
    return None


def _epoch_seconds(value: Any) -> int | None:
    """Interpret a governed timestamp as integer UTC epoch seconds."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        parsed = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp())
    if isinstance(value, (int, float)):
        number = float(value)
        if not math.isfinite(number):
            return None
        # Millisecond/microsecond representations are normalised once,
        # deterministically; nothing else is repaired or inferred.
        while abs(number) >= 1e11:
            number /= 1000.0
        return int(number)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if re.fullmatch(r"-?\d+(\.\d+)?", text):
            return _epoch_seconds(float(text))
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp())
    return None


def _finite_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


# ─── GUARD TAXONOMY ───────────────────────────────────────────────────────────


def normalize_guard_name(value: Any) -> str | None:
    """Map one declared guard identifier onto the frozen HD10 taxonomy.

    ``None`` means the name is not an explicitly declared production guard
    identifier.  An unmapped guard is never guessed into one of the four.
    """
    text = _text(value).lower()
    if not text:
        return None
    return GUARD_NAME_VOCABULARY_V1.get(text)


def is_guard_rejection_record(record: Mapping[str, Any]) -> bool:
    """Return whether a decision-trace record is a runtime guard rejection."""
    role = _text(record.get("record_role")).lower()
    event = _text(record.get("event_type")).upper()
    rejection_type = _text(record.get("rejection_type")).upper()
    reason = _text(record.get("reason"))
    return (
        role in _GUARD_MARKER_ROLES
        or event in _GUARD_MARKER_EVENTS
        or rejection_type in _GUARD_MARKER_TYPES
        or reason.startswith(_GUARD_REASON_PREFIX)
    )


@dataclass(frozen=True)
class GuardHits:
    """Mapped and unmapped guard authority extracted from one governed record."""

    mapped: tuple[str, ...]
    unmapped: tuple[str, ...]
    terminal: str | None
    unmapped_terminal: str | None


def extract_guard_hits(record: Mapping[str, Any]) -> GuardHits:
    """Extract the declared guard authority of one governed decision record.

    Contributing (mapped) guard hits and the terminal guard are preserved
    separately; unmapped names are preserved verbatim so unsupported guard
    authority is explicit instead of silently attributed.
    """
    names: list[str] = []
    for field in _GUARD_VERDICT_FIELDS:
        value = _text(record.get(field))
        if value:
            names.append(value)
    for path in (
        ("risk_rejection_detail", "guard"),
        ("risk_rejection_detail", "guard_name"),
        ("risk_rejection", "guard"),
        ("risk_rejection", "guard_name"),
    ):
        value = _text(_nested(record, *path))
        if value:
            names.append(value)
    if is_guard_rejection_record(record):
        for path in (("metadata", "guard"), ("metadata", "guard_name")):
            value = _text(_nested(record, *path))
            if value:
                names.append(value)
        reason = _text(record.get("reason"))
        if reason.startswith(_GUARD_REASON_PREFIX):
            segments = reason.split(":")
            if len(segments) > 1 and segments[1].strip():
                names.append(segments[1].strip())

    mapped: set[str] = set()
    unmapped: set[str] = set()
    terminal: str | None = None
    unmapped_terminal: str | None = None
    for index, name in enumerate(names):
        guard_id = normalize_guard_name(name)
        if guard_id is None:
            unmapped.add(name.lower())
            if index == 0 and unmapped_terminal is None:
                unmapped_terminal = name.lower()
        else:
            mapped.add(guard_id)
            if index == 0 and terminal is None:
                # The blocking field names the guard that vetoed this record:
                # that is the terminal-guard authority where it exists.
                terminal = guard_id
    return GuardHits(
        mapped=tuple(sorted(mapped)),
        unmapped=tuple(sorted(unmapped)),
        terminal=terminal if terminal in mapped else None,
        unmapped_terminal=unmapped_terminal,
    )


# ─── BASELINE POLICY BINDING ──────────────────────────────────────────────────


def resolve_baseline_authority(
    authority: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Resolve the bound RISK_POLICY_BASELINE_V1 identity or fail closed.

    The digest always comes from HD10's canonical helper
    (``baseline_risk_policy_identity_hash``); this module duplicates no hashing
    logic, assumes no default policy, and never infers a policy identity from
    account behaviour.
    """
    if authority is None:
        return None, BASELINE_AUTHORITY_MISSING
    material = dict(authority)
    policy_id = _text(material.get("policy_id"))
    version = material.get("policy_version")
    declared = _text(material.get("identity_hash"))
    if not policy_id or isinstance(version, bool) or not isinstance(version, int) or not declared:
        return None, BASELINE_AUTHORITY_MISSING
    if (
        policy_id != BASELINE_RISK_POLICY_ID
        or version != BASELINE_RISK_POLICY_V1["policy_version"]
        or declared != BASELINE_RISK_POLICY_V1["identity_hash"]
    ):
        return None, BASELINE_AUTHORITY_DRIFT
    if baseline_risk_policy_identity_hash(material) != declared:
        return None, BASELINE_AUTHORITY_DRIFT
    if material != BASELINE_RISK_POLICY_V1:
        return None, BASELINE_AUTHORITY_DRIFT
    return material, None


def _declared_policy_identity(record: Mapping[str, Any]) -> tuple[str, str]:
    policy_id = _first(
        record,
        ("baseline_policy_id",),
        ("risk_policy_id",),
        ("risk_policy", "policy_id"),
    )
    identity = _first(
        record,
        ("baseline_policy_identity_hash",),
        ("risk_policy_identity_hash",),
        ("risk_policy", "identity_hash"),
    )
    return _text(policy_id), _text(identity)


# ─── GOVERNED DECISION VERDICT EXTRACTION ─────────────────────────────────────

_RISK_STAGE = "risk"


def _text_set(value: Any) -> frozenset[str]:
    if isinstance(value, str):
        return frozenset({value}) if value else frozenset()
    if isinstance(value, (list, tuple, set, frozenset)):
        return frozenset(_text(item) for item in value if _text(item))
    return frozenset()


def _decision_identity(record: Mapping[str, Any]) -> tuple[str, str]:
    canonical = _text(_first(
        record, ("canonical_opportunity_id",), ("identity", "canonical_opportunity_id"),
    ))
    symbol = _text(_first(
        record,
        ("canonical_symbol",),
        ("symbol",),
        ("identity", "canonical_symbol"),
        ("identity", "symbol"),
    ))
    return canonical, symbol


def _decision_verdict(record: Mapping[str, Any]) -> tuple[str | None, str]:
    """Derive the governed risk-layer verdict of one decision-trace record.

    Precedence (authority order, never a guess):

    1. a runtime guard-chain rejection record is a veto by that guard;
    2. a present engine risk-rejection detail is a veto by the engine risk layer;
    3. a passed ``risk`` pipeline stage means the risk layer did not veto;
    4. a reached-but-unpassed ``risk`` stage (or a ``risk`` terminal stage) is an
       engine risk-layer veto;
    5. otherwise the trace carries no risk-layer verdict at all.
    """
    if is_guard_rejection_record(record):
        return BLOCKED, "guard_chain_rejection"
    if record.get("risk_rejection_detail"):
        return BLOCKED, "engine_risk_rejection"
    reached = _text_set(record.get("stages_reached"))
    passed = _text_set(record.get("stages_passed"))
    if _RISK_STAGE in passed:
        return ALLOWED, "risk_stage_passed"
    if (
        (_RISK_STAGE in reached and _RISK_STAGE not in passed)
        or _text(record.get("terminal_stage")).lower() == _RISK_STAGE
    ):
        return BLOCKED, "engine_risk_stage"
    return None, ""


@dataclass(frozen=True)
class _DecisionView:
    canonical_opportunity_id: str
    canonical_symbol: str
    entity_id: str
    decision_id: str
    correlation_id: str
    cycle_id: str
    timestamp_utc: int | None
    disposition: str | None
    authority: str
    guards: GuardHits


def _decision_view(record: Mapping[str, Any]) -> _DecisionView:
    canonical, symbol = _decision_identity(record)
    disposition, authority = _decision_verdict(record)
    return _DecisionView(
        canonical_opportunity_id=canonical,
        canonical_symbol=symbol,
        entity_id=_text(_first(record, ("entity_id",), ("identity", "entity_id"))),
        decision_id=_text(_first(record, ("decision_id",), ("identity", "decision_id"))),
        correlation_id=_text(_first(
            record, ("correlation_id",), ("identity", "correlation_id"),
        )),
        cycle_id=_text(_first(record, ("cycle_id",), ("identity", "cycle_id"))),
        timestamp_utc=_epoch_seconds(_first(
            record,
            ("timestamp_utc",),
            ("ts_utc_ms",),
            ("timestamp_unix",),
            ("decision_timestamp_utc",),
            ("entry_market_time_utc_epoch_s",),
        )),
        disposition=disposition,
        authority=authority,
        guards=extract_guard_hits(record),
    )


# ─── GOVERNED SHADOW OUTCOME EXTRACTION ───────────────────────────────────────

_OUTCOME_R_PATHS = (
    ("simulated_outcome", "pnl_r_multiple"),
    ("pnl_r_multiple",),
    ("observed_pnl_r_multiple",),
    ("r_multiple",),
)


@dataclass(frozen=True)
class _OutcomeView:
    canonical_opportunity_id: str
    canonical_symbol: str
    entity_id: str
    outcome_identity: str
    trade_horizon: str
    account_id: str
    scope: str
    r_multiple: float | None
    entry_utc_epoch_s: int | None
    exit_utc_epoch_s: int | None
    decision_id: str
    correlation_id: str


def outcome_scope_of(record: Mapping[str, Any]) -> str:
    """Return the declared simulation scope of one governed shadow outcome."""
    return _text(_first(record, ("identity", "shadow_type"), ("shadow_type",)))


def _outcome_view(record: Mapping[str, Any]) -> _OutcomeView:
    canonical, symbol = _decision_identity(record)
    return _OutcomeView(
        canonical_opportunity_id=canonical,
        canonical_symbol=symbol,
        entity_id=_text(_first(record, ("identity", "entity_id"), ("entity_id",))),
        outcome_identity=_text(_first(
            record,
            ("identity", "shadow_trade_id"),
            ("identity", "trade_id"),
            ("shadow_trade_id",),
            ("trade_id",),
        )),
        trade_horizon=_text(_first(
            record,
            ("identity", "evaluated_horizon"),
            ("identity", "trade_horizon"),
            ("decision_snapshot", "trade_horizon"),
            ("trade_horizon",),
        )),
        account_id=_text(_first(
            record,
            ("identity", "account_id"),
            ("account_id",),
            ("execution", "account_id"),
        )),
        scope=outcome_scope_of(record),
        r_multiple=_finite_number(_first(record, *_OUTCOME_R_PATHS)),
        entry_utc_epoch_s=_epoch_seconds(_first(
            record,
            ("decision_snapshot", "timestamp_decision_utc"),
            ("entry_utc_epoch_s",),
            ("entry_market_time_utc_epoch_s",),
        )),
        exit_utc_epoch_s=_epoch_seconds(_first(
            record,
            ("simulated_outcome", "exit_timestamp"),
            ("exit_utc_epoch_s",),
            ("exit_market_time_utc_epoch_s",),
        )),
        decision_id=_text(_first(record, ("identity", "decision_id"), ("decision_id",))),
        correlation_id=_text(_first(
            record, ("identity", "correlation_id"), ("correlation_id",),
        )),
    )


# ─── CANONICAL RISK-EVENT RECORD ──────────────────────────────────────────────


@dataclass(frozen=True)
class RiskEventRecord:
    """One canonical opportunity's governed risk event and governed outcome."""

    schema_version: str
    hd10_adjudication_version: str
    canonical_opportunity_id: str
    canonical_symbol: str
    entity_id: str
    decision_ids: tuple[str, ...]
    correlation_ids: tuple[str, ...]
    cycle_ids: tuple[str, ...]
    trade_horizon: str
    decision_timestamps_utc: tuple[int, ...]
    decision_timestamp_utc: int
    entry_utc_epoch_s: int
    exit_utc_epoch_s: int | None
    risk_disposition: str
    risk_verdict_authority: str
    active_guards: tuple[str, ...]
    unmapped_guards: tuple[str, ...]
    terminal_guard: str | None
    unmapped_terminal_guard: str | None
    multi_guard: bool
    guard_exclusive: str | None
    guard_authority_state: str
    baseline_policy_id: str
    baseline_policy_version: int
    baseline_identity_hash: str
    outcome_identity: str
    outcome_authority: str
    outcome_scope: str
    outcome_r_multiple: float
    outcome_counterfactual: bool
    chronology_authority: str
    decision_chronology_authority: str
    source_identity: str
    account_ids: tuple[str, ...]
    account_execution_count: int
    record_digest: str

    @property
    def chronological_key(self) -> tuple[int, str]:
        """The HD10 R3-R5 ordering key: entry UTC then canonical opportunity id."""
        return (self.entry_utc_epoch_s, self.canonical_opportunity_id)

    @property
    def has_canonical_guard_attribution(self) -> bool:
        """Whether per-guard attribution is possible without guessing."""
        return self.guard_authority_state == GUARD_AUTHORITY_CANONICAL

    @property
    def is_blocked_with_counterfactual(self) -> bool:
        return self.risk_disposition == BLOCKED and self.outcome_counterfactual

    def digest_material(self) -> dict[str, Any]:
        """Deterministic material for this record's identity digest."""
        return {
            "schema_version": self.schema_version,
            "hd10_adjudication_version": self.hd10_adjudication_version,
            "canonical_opportunity_id": self.canonical_opportunity_id,
            "canonical_symbol": self.canonical_symbol,
            "entity_id": self.entity_id,
            "decision_ids": list(self.decision_ids),
            "correlation_ids": list(self.correlation_ids),
            "cycle_ids": list(self.cycle_ids),
            "trade_horizon": self.trade_horizon,
            "decision_timestamps_utc": list(self.decision_timestamps_utc),
            "decision_timestamp_utc": self.decision_timestamp_utc,
            "entry_utc_epoch_s": self.entry_utc_epoch_s,
            "exit_utc_epoch_s": self.exit_utc_epoch_s,
            "risk_disposition": self.risk_disposition,
            "risk_verdict_authority": self.risk_verdict_authority,
            "active_guards": list(self.active_guards),
            "unmapped_guards": list(self.unmapped_guards),
            "terminal_guard": self.terminal_guard,
            "unmapped_terminal_guard": self.unmapped_terminal_guard,
            "multi_guard": self.multi_guard,
            "guard_exclusive": self.guard_exclusive,
            "guard_authority_state": self.guard_authority_state,
            "baseline_policy_id": self.baseline_policy_id,
            "baseline_policy_version": self.baseline_policy_version,
            "baseline_identity_hash": self.baseline_identity_hash,
            "outcome_identity": self.outcome_identity,
            "outcome_authority": self.outcome_authority,
            "outcome_scope": self.outcome_scope,
            "outcome_r_multiple": self.outcome_r_multiple,
            "outcome_counterfactual": self.outcome_counterfactual,
            "chronology_authority": self.chronology_authority,
            "decision_chronology_authority": self.decision_chronology_authority,
            "source_identity": self.source_identity,
            "account_ids": list(self.account_ids),
            "account_execution_count": self.account_execution_count,
        }

    def analytical_record(self) -> dict[str, Any]:
        """Return the digest-bearing canonical analytical record."""
        value = self.digest_material()
        value["record_digest"] = self.record_digest
        return value


@dataclass(frozen=True)
class RiskEventExclusion:
    """One auditable exclusion: what was excluded and why."""

    canonical_opportunity_id: str | None
    reason: str
    detail: str
    source_identity: str

    def record(self) -> dict[str, Any]:
        return {
            "canonical_opportunity_id": self.canonical_opportunity_id,
            "reason": self.reason,
            "detail": self.detail,
            "source_identity": self.source_identity,
        }


# ─── EVIDENCE CONTAINER ───────────────────────────────────────────────────────

SOURCE_IDENTITY_DECISION = "decision_trace_v1:CURRENT:governed_risk_verdict"
SOURCE_IDENTITY_OUTCOME = (
    "shadow_trades_v1:CURRENT:governed_shadow_runtime_v1_lifecycle_outcome"
)


@dataclass(frozen=True)
class RiskPolicyEvidenceSummary:
    """Mechanical population facts for later R1-R5 runners.

    These are evidence facts only: no question status, no estimand, and no
    ready/waiting/complete decision is made here.
    """

    total_candidate_observations: int
    eligible_observations: int
    distinct_canonical_opportunities: int
    allowed_count: int
    blocked_count: int
    blocked_with_valid_counterfactual_count: int
    blocked_without_counterfactual_count: int
    guard_exposure_counts: dict[str, int]
    guard_exclusive_counts: dict[str, int]
    multi_guard_count: int
    unattributable_blocked_count: int
    unmapped_guard_observation_count: int
    guard_authority_missing_count: int
    account_fanout_observation_count: int
    collapsed_duplicate_record_count: int
    chronological_eligible_count: int
    earliest_entry_utc: int | None
    latest_entry_utc: int | None
    coverage_numerator: int
    coverage_denominator: int
    coverage_percentage: float
    exclusions_by_reason: dict[str, int]
    baseline_policy_id: str
    baseline_policy_version: int | None
    baseline_identity_hash: str


@dataclass(frozen=True)
class RiskPolicyEvidence:
    """Governed canonical risk-event population plus deterministic provenance."""

    schema_version: str
    hd10_adjudication_version: str
    baseline_authority_state: str
    records: tuple[RiskEventRecord, ...]
    exclusions: tuple[RiskEventExclusion, ...]
    summary: RiskPolicyEvidenceSummary
    provenance: dict[str, Any]
    evidence_provenance: dict[str, Any]
    baseline_authority: dict[str, Any]

    def readiness(self, requirement: EvidenceRequirement) -> EvidenceReadiness:
        """Delegate mechanical readiness to the canonical control-plane machinery."""
        return evaluate_evidence_readiness(
            total_count=self.summary.total_candidate_observations,
            valid_count=self.summary.eligible_observations,
            distinct_valid_count=self.summary.distinct_canonical_opportunities,
            requirement=requirement,
        )

    def chronological_records(self) -> tuple[RiskEventRecord, ...]:
        """Return the canonical population in HD10 entry-chronology order."""
        return tuple(sorted(self.records, key=lambda item: item.chronological_key))

    def allowed_records(self) -> tuple[RiskEventRecord, ...]:
        return tuple(item for item in self.records if item.risk_disposition == ALLOWED)

    def blocked_records(self) -> tuple[RiskEventRecord, ...]:
        return tuple(item for item in self.records if item.risk_disposition == BLOCKED)

    def blocked_with_counterfactual_records(self) -> tuple[RiskEventRecord, ...]:
        return tuple(item for item in self.records if item.is_blocked_with_counterfactual)

    def guard_exclusive_records(self, guard_id: str) -> tuple[RiskEventRecord, ...]:
        """Guard-exclusive blocked observations for later R2 strata.

        Only a single unambiguously mapped firing guard qualifies, so a
        multi-guard or unattributable observation can never enter a per-guard
        contrast.  No attribution is performed here.
        """
        if guard_id not in GUARD_IDS:
            raise ValueError(f"Unknown HD10 guard id: {guard_id!r}")
        return tuple(item for item in self.records if item.guard_exclusive == guard_id)

    def multi_guard_records(self) -> tuple[RiskEventRecord, ...]:
        """Multi-guard residuals, reported once and outside every per-guard slice."""
        return tuple(item for item in self.records if item.multi_guard)

    def unattributable_blocked_records(self) -> tuple[RiskEventRecord, ...]:
        """Blocked observations whose guard authority is unmapped or missing."""
        return tuple(
            item for item in self.records
            if item.risk_disposition == BLOCKED
            and item.guard_authority_state in (GUARD_AUTHORITY_UNMAPPED, GUARD_AUTHORITY_MISSING)
        )


# ─── CANONICAL REDUCTION ──────────────────────────────────────────────────────


def _unique_or_empty(values: Iterable[str]) -> str:
    finite = sorted({value for value in values if value})
    return finite[0] if len(finite) == 1 else ""


def _joined_identity(values: Iterable[str]) -> str:
    return "|".join(sorted({value for value in values if value}))


@dataclass(frozen=True)
class _ReducedOutcome:
    identity: str
    r_multiple: float
    entry_utc_epoch_s: int
    exit_utc_epoch_s: int | None
    trade_horizon: str
    account_ids: tuple[str, ...]
    account_execution_count: int


def _reduce_outcome_views(
    views: list[_OutcomeView],
) -> tuple[_ReducedOutcome | None, str | None, int]:
    """Reduce the governed outcomes of one canonical opportunity to one outcome.

    Account executions collapse under HD10's equal-weight-mean rule (weight
    ``w_o = 1 / k_o``), so account fanout can never increase the sample.
    Horizon or identity conflicts are never averaged silently; they fail closed.
    """
    unique_by_key: dict[tuple[Any, ...], _OutcomeView] = {}
    collapsed = 0
    for view in views:
        key = (
            view.outcome_identity, view.account_id, view.r_multiple,
            view.entry_utc_epoch_s, view.exit_utc_epoch_s, view.trade_horizon,
        )
        if key in unique_by_key:
            collapsed += 1
        else:
            unique_by_key[key] = view
    unique = sorted(
        unique_by_key.values(),
        key=lambda item: (
            item.outcome_identity, item.account_id,
            item.entry_utc_epoch_s if item.entry_utc_epoch_s is not None else -1,
            item.r_multiple,
        ),
    )
    if not unique:
        return None, None, collapsed

    scoped = [item for item in unique if item.account_id]
    unscoped = [item for item in unique if not item.account_id]
    if scoped and unscoped:
        return None, "AMBIGUOUS_OUTCOME_AUTHORITY", collapsed

    horizons = sorted({item.trade_horizon for item in unique if item.trade_horizon})
    if len(horizons) > 1:
        return None, "CONFLICTING_GOVERNED_OUTCOME", collapsed
    entries = [item.entry_utc_epoch_s for item in unique]
    if any(entry is None for entry in entries):
        return None, "MISSING_CHRONOLOGY", collapsed
    if len(set(entries)) > 1:
        return None, "AMBIGUOUS_CHRONOLOGY", collapsed
    exits = sorted({item.exit_utc_epoch_s for item in unique if item.exit_utc_epoch_s is not None})
    exit_value = exits[0] if len(exits) == 1 else None
    assert entries[0] is not None

    if not scoped:
        if len({item.r_multiple for item in unique}) > 1:
            return None, "CONFLICTING_GOVERNED_OUTCOME", collapsed
        return (
            _ReducedOutcome(
                identity=_joined_identity(item.outcome_identity for item in unique),
                r_multiple=unique[0].r_multiple,
                entry_utc_epoch_s=entries[0],
                exit_utc_epoch_s=exit_value,
                trade_horizon=horizons[0] if horizons else "",
                account_ids=(),
                account_execution_count=1,
            ),
            None,
            collapsed,
        )

    by_account: dict[str, list[_OutcomeView]] = defaultdict(list)
    for item in scoped:
        by_account[item.account_id].append(item)
    for account in sorted(by_account):
        items = by_account[account]
        if len({item.outcome_identity for item in items}) > 1 or len({item.r_multiple for item in items}) > 1:
            return None, "ACCOUNT_FANOUT_PSEUDOREPLICATION", collapsed
    per_account_r = [by_account[account][0].r_multiple for account in sorted(by_account)]
    return (
        _ReducedOutcome(
            identity=_joined_identity(item.outcome_identity for item in unique),
            r_multiple=sum(per_account_r) / len(per_account_r),
            entry_utc_epoch_s=entries[0],
            exit_utc_epoch_s=exit_value,
            trade_horizon=horizons[0] if horizons else "",
            account_ids=tuple(sorted(by_account)),
            account_execution_count=len(by_account),
        ),
        None,
        collapsed,
    )


@dataclass(frozen=True)
class _DecisionVerdict:
    disposition: str
    authority: str
    canonical_symbol: str
    entity_id: str
    decision_ids: tuple[str, ...]
    correlation_ids: tuple[str, ...]
    cycle_ids: tuple[str, ...]
    decision_timestamps_utc: tuple[int, ...]
    active_guards: tuple[str, ...]
    unmapped_guards: tuple[str, ...]
    terminal_guard: str | None
    unmapped_terminal_guard: str | None
    multi_guard: bool
    guard_exclusive: str | None
    guard_authority_state: str


def _canonical_symbol_authority(
    canonical_opportunity_id: str, declared: Iterable[str],
) -> tuple[str, str | None]:
    """Resolve the canonical symbol, failing closed on any conflict."""
    candidates = {value for value in declared if value}
    if "*" in canonical_opportunity_id:
        candidates.add(canonical_opportunity_id.split("*", 1)[0].upper())
    if len(candidates) > 1:
        return "", "AMBIGUOUS_CANONICAL_IDENTITY"
    return (next(iter(candidates)) if candidates else ""), None


def _aggregate_decisions(
    views: list[_DecisionView], canonical_opportunity_id: str,
) -> tuple[_DecisionVerdict | None, str | None]:
    """Reduce the governed decision verdicts of one canonical opportunity.

    A recorded guard rejection or engine risk veto is authoritative over a
    decision trace whose earlier risk stage passed, because the runtime guard
    chain has post-engine veto authority.
    """
    verdicts = [item for item in views if item.disposition is not None]
    if not verdicts:
        return None, "RISK_LAYER_VERDICT_MISSING"
    if any(item.timestamp_utc is None for item in verdicts):
        return None, "MISSING_DECISION_CHRONOLOGY"
    symbol, conflict = _canonical_symbol_authority(
        canonical_opportunity_id, (item.canonical_symbol for item in views),
    )
    if conflict:
        return None, conflict

    entity_ids = {item.entity_id for item in views if item.entity_id}
    if len(entity_ids) > 1:
        return None, "AMBIGUOUS_CANONICAL_IDENTITY"

    blocked = [item for item in verdicts if item.disposition == BLOCKED]
    identity = {
        "canonical_symbol": symbol,
        "entity_id": _unique_or_empty(item.entity_id for item in views),
        "decision_ids": tuple(sorted({item.decision_id for item in views if item.decision_id})),
        "correlation_ids": tuple(sorted({
            item.correlation_id for item in views if item.correlation_id
        })),
        "cycle_ids": tuple(sorted({item.cycle_id for item in views if item.cycle_id})),
        "decision_timestamps_utc": tuple(sorted({
            item.timestamp_utc for item in verdicts if item.timestamp_utc is not None
        })),
    }
    if not blocked:
        return (
            _DecisionVerdict(
                disposition=ALLOWED,
                authority="|".join(sorted({item.authority for item in verdicts})),
                active_guards=(),
                unmapped_guards=(),
                terminal_guard=None,
                unmapped_terminal_guard=None,
                multi_guard=False,
                guard_exclusive=None,
                guard_authority_state=GUARD_AUTHORITY_NOT_APPLICABLE,
                **identity,
            ),
            None,
        )

    mapped = tuple(sorted({guard for item in blocked for guard in item.guards.mapped}))
    unmapped = tuple(sorted({guard for item in blocked for guard in item.guards.unmapped}))
    terminals = sorted({item.guards.terminal for item in blocked if item.guards.terminal})
    unmapped_terminals = sorted({
        item.guards.unmapped_terminal for item in blocked if item.guards.unmapped_terminal
    })
    if unmapped:
        state = GUARD_AUTHORITY_UNMAPPED
    elif mapped:
        state = GUARD_AUTHORITY_CANONICAL
    else:
        state = GUARD_AUTHORITY_MISSING
    return (
        _DecisionVerdict(
            disposition=BLOCKED,
            authority="|".join(sorted({item.authority for item in blocked})),
            active_guards=mapped,
            unmapped_guards=unmapped,
            terminal_guard=terminals[0] if len(terminals) == 1 else None,
            unmapped_terminal_guard=unmapped_terminals[0] if len(unmapped_terminals) == 1 else None,
            multi_guard=len(mapped) > 1,
            guard_exclusive=(
                mapped[0]
                if state == GUARD_AUTHORITY_CANONICAL and len(mapped) == 1
                else None
            ),
            guard_authority_state=state,
            **identity,
        ),
        None,
    )


# ─── POPULATION CONSTRUCTION ──────────────────────────────────────────────────


def _decision_outcome_lineage_failure(
    verdict: _DecisionVerdict,
    outcomes: Iterable[_OutcomeView],
) -> str | None:
    """Fail closed when secondary lineage contradicts the canonical join root."""
    for outcome in outcomes:
        if (
            (outcome.canonical_symbol and verdict.canonical_symbol
             and outcome.canonical_symbol != verdict.canonical_symbol)
            or (outcome.entity_id and verdict.entity_id
                and outcome.entity_id != verdict.entity_id)
            or (outcome.decision_id and verdict.decision_ids
                and outcome.decision_id not in verdict.decision_ids)
            or (outcome.correlation_id and verdict.correlation_ids
                and outcome.correlation_id not in verdict.correlation_ids)
        ):
            return "DECISION_OUTCOME_LINEAGE_MISMATCH"
    return None


def _digestible_records(
    records: Iterable[Mapping[str, Any]],
) -> tuple[tuple[dict[str, Any], ...], int]:
    """Keep only records that can cross deterministic JSON provenance."""
    accepted: list[dict[str, Any]] = []
    rejected = 0
    for record in records:
        try:
            evidence_digest((record,))
        except (TypeError, ValueError):
            rejected += 1
        else:
            accepted.append(record)
    return tuple(accepted), rejected


def _non_current_canonical_ids(
    records: Iterable[Mapping[str, Any]], source: str,
) -> frozenset[str]:
    """Canonical opportunities that also carry non-CURRENT governed authority."""
    ids: set[str] = set()
    for record in records:
        epoch = classify_authoritative_evidence_record(dict(record), source)
        if epoch == DataEpoch.CURRENT:
            continue
        canonical, _ = _decision_identity(record)
        if canonical:
            ids.add(canonical)
    return frozenset(ids)


def _policy_identity_failure(
    records: Iterable[Mapping[str, Any]],
    baseline: Mapping[str, Any],
    *,
    require_record_policy_identity: bool,
) -> str | None:
    """Enforce the exact historical baseline identity on the record level.

    A record that declares a different policy identity, or a record that must
    prove its own RISK_POLICY_BASELINE_V1 identity and cannot, fails closed.
    Today's policy is never applied retroactively and account behaviour is never
    used to infer a policy identity.
    """
    declared_any = False
    for record in records:
        policy_id, identity_hash = _declared_policy_identity(record)
        declared_any = declared_any or bool(policy_id or identity_hash)
        if policy_id and policy_id != _text(baseline.get("policy_id")):
            return BASELINE_AUTHORITY_DRIFT
        if identity_hash and identity_hash != _text(baseline.get("identity_hash")):
            return BASELINE_AUTHORITY_DRIFT
    if require_record_policy_identity and not declared_any:
        return BASELINE_AUTHORITY_MISSING
    return None


def build_risk_policy_evidence(
    decision_records: Iterable[Mapping[str, Any]],
    outcome_records: Iterable[Mapping[str, Any]],
    *,
    baseline_authority: Mapping[str, Any] | None = None,
    outcome_scope: str = PRIMARY_HORIZON_SCOPE,
    require_record_policy_identity: bool = False,
) -> RiskPolicyEvidence:
    """Build the governed canonical risk-event population for HD10 R1-R5.

    Decision authority is the CURRENT governed decision trace; outcome authority
    is the CURRENT governed ``shadow_runtime_v1`` lifecycle outcome R-multiple.
    Both cross the canonical control-plane provenance boundary first and are
    joined only on explicit canonical identifiers: never by row order, by
    ingestion order, or by nearest timestamp.
    """
    if outcome_scope not in OUTCOME_SCOPES:
        raise ValueError(f"Unsupported HD10 outcome scope: {outcome_scope!r}")

    supplied_decisions = tuple(deepcopy(dict(item)) for item in decision_records)
    supplied_outcomes = tuple(deepcopy(dict(item)) for item in outcome_records)
    decision_inputs, malformed_decisions = _digestible_records(supplied_decisions)
    outcome_inputs, malformed_outcomes = _digestible_records(supplied_outcomes)

    decision_selection = select_current_evidence(DECISION_SOURCE, decision_inputs)
    outcome_selection = select_current_evidence(OUTCOME_SOURCE, outcome_inputs)
    evidence_provenance = build_evidence_provenance(decision_selection, outcome_selection)

    stale_decisions = _non_current_canonical_ids(decision_inputs, DECISION_SOURCE)
    stale_outcomes = _non_current_canonical_ids(outcome_inputs, OUTCOME_SOURCE)

    exclusions: list[RiskEventExclusion] = []
    decision_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    decision_views: list[_DecisionView] = []
    for record in decision_selection.records_for_analysis():
        view = _decision_view(record)
        if not view.canonical_opportunity_id:
            exclusions.append(RiskEventExclusion(
                canonical_opportunity_id=None,
                reason="MISSING_CANONICAL_IDENTITY",
                detail="CURRENT decision trace declares no canonical_opportunity_id",
                source_identity=SOURCE_IDENTITY_DECISION,
            ))
            continue
        decision_views.append(view)
        decision_groups[view.canonical_opportunity_id].append(record)

    outcome_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    outcome_views: list[_OutcomeView] = []
    for record in outcome_selection.records_for_analysis():
        view = _outcome_view(record)
        reason: str | None = None
        if not view.canonical_opportunity_id:
            reason = "OUTCOME_MISSING_CANONICAL_IDENTITY"
        elif view.r_multiple is None:
            reason = "INVALID_R_MULTIPLE"
        elif view.scope != outcome_scope:
            reason = "OUTCOME_SCOPE_EXCLUDED"
        if reason is not None:
            exclusions.append(RiskEventExclusion(
                canonical_opportunity_id=view.canonical_opportunity_id or None,
                reason=reason,
                detail=(
                    f"declared scope={view.scope or 'UNDECLARED'}; "
                    f"required outcome scope={outcome_scope}"
                ),
                source_identity=SOURCE_IDENTITY_OUTCOME,
            ))
            continue
        outcome_views.append(view)
        outcome_groups[view.canonical_opportunity_id].append(record)

    candidate_ids = sorted(
        {view.canonical_opportunity_id for view in decision_views}
        | {view.canonical_opportunity_id for view in outcome_views}
    )
    decisions_by_id: dict[str, list[_DecisionView]] = defaultdict(list)
    for view in decision_views:
        decisions_by_id[view.canonical_opportunity_id].append(view)
    outcomes_by_id: dict[str, list[_OutcomeView]] = defaultdict(list)
    for view in outcome_views:
        outcomes_by_id[view.canonical_opportunity_id].append(view)

    baseline, baseline_reason = resolve_baseline_authority(baseline_authority)
    records: list[RiskEventRecord] = []
    collapsed_duplicates = 0
    for canonical_id in candidate_ids:
        if baseline_reason is not None or baseline is None:
            exclusions.append(RiskEventExclusion(
                canonical_opportunity_id=canonical_id,
                reason=baseline_reason or BASELINE_AUTHORITY_MISSING,
                detail="bound RISK_POLICY_BASELINE_V1 authority rejected",
                source_identity=SOURCE_IDENTITY_DECISION,
            ))
            continue

        def _exclude(reason: str, detail: str = "") -> None:
            exclusions.append(RiskEventExclusion(
                canonical_opportunity_id=canonical_id,
                reason=reason,
                detail=detail,
                source_identity=SOURCE_IDENTITY_DECISION,
            ))

        if canonical_id in stale_decisions:
            _exclude(
                "NON_CURRENT_DECISION_AUTHORITY_PRESENT",
                "a non-CURRENT decision-trace record exists for this canonical opportunity",
            )
            continue
        if canonical_id in stale_outcomes:
            _exclude(
                "NON_CURRENT_OUTCOME_AUTHORITY_PRESENT",
                "a non-CURRENT shadow outcome record exists for this canonical opportunity",
            )
            continue

        verdict, verdict_reason = _aggregate_decisions(
            decisions_by_id.get(canonical_id, []), canonical_id,
        )
        if verdict_reason is not None or verdict is None:
            _exclude(verdict_reason or "MISSING_DECISION_AUTHORITY")
            continue

        policy_failure = _policy_identity_failure(
            decision_groups.get(canonical_id, []) + outcome_groups.get(canonical_id, []),
            baseline,
            require_record_policy_identity=require_record_policy_identity,
        )
        if policy_failure is not None:
            _exclude(policy_failure, "declared historical baseline identity rejected")
            continue

        lineage_failure = _decision_outcome_lineage_failure(
            verdict, outcomes_by_id.get(canonical_id, []),
        )
        if lineage_failure is not None:
            _exclude(lineage_failure)
            continue

        reduced, outcome_reason, collapsed = _reduce_outcome_views(
            outcomes_by_id.get(canonical_id, []),
        )
        collapsed_duplicates += collapsed
        if outcome_reason is not None:
            _exclude(outcome_reason)
            continue
        if reduced is None:
            _exclude(
                "MISSING_BLOCKED_COUNTERFACTUAL_OUTCOME"
                if verdict.disposition == BLOCKED
                else "MISSING_GOVERNED_OUTCOME"
            )
            continue

        record = RiskEventRecord(
            schema_version=SCHEMA_VERSION,
            hd10_adjudication_version=HD10_ADJUDICATION_VERSION,
            canonical_opportunity_id=canonical_id,
            canonical_symbol=verdict.canonical_symbol,
            entity_id=verdict.entity_id,
            decision_ids=verdict.decision_ids,
            correlation_ids=verdict.correlation_ids,
            cycle_ids=verdict.cycle_ids,
            trade_horizon=reduced.trade_horizon,
            decision_timestamps_utc=verdict.decision_timestamps_utc,
            decision_timestamp_utc=verdict.decision_timestamps_utc[0],
            entry_utc_epoch_s=reduced.entry_utc_epoch_s,
            exit_utc_epoch_s=reduced.exit_utc_epoch_s,
            risk_disposition=verdict.disposition,
            risk_verdict_authority=verdict.authority,
            active_guards=verdict.active_guards,
            unmapped_guards=verdict.unmapped_guards,
            terminal_guard=verdict.terminal_guard,
            unmapped_terminal_guard=verdict.unmapped_terminal_guard,
            multi_guard=verdict.multi_guard,
            guard_exclusive=verdict.guard_exclusive,
            guard_authority_state=verdict.guard_authority_state,
            baseline_policy_id=_text(baseline.get("policy_id")),
            baseline_policy_version=int(baseline.get("policy_version")),
            baseline_identity_hash=_text(baseline.get("identity_hash")),
            outcome_identity=reduced.identity,
            outcome_authority=OUTCOME_AUTHORITY,
            outcome_scope=outcome_scope,
            outcome_r_multiple=reduced.r_multiple,
            outcome_counterfactual=verdict.disposition == BLOCKED,
            chronology_authority=CHRONOLOGY_AUTHORITY,
            decision_chronology_authority=DECISION_CHRONOLOGY_AUTHORITY,
            source_identity=(
                f"{SOURCE_IDENTITY_DECISION}:{verdict.authority}"
                f"|{SOURCE_IDENTITY_OUTCOME}:{outcome_scope}"
            ),
            account_ids=reduced.account_ids,
            account_execution_count=reduced.account_execution_count,
            record_digest="",
        )
        records.append(replace(
            record, record_digest=evidence_digest((record.digest_material(),)),
        ))

    records.sort(key=lambda item: item.chronological_key)
    exclusions.sort(key=lambda item: (
        item.canonical_opportunity_id or "", item.reason, item.detail,
    ))
    return _finalise_evidence(
        records=records,
        exclusions=exclusions,
        candidate_ids=candidate_ids,
        collapsed_duplicates=collapsed_duplicates,
        baseline=baseline,
        baseline_reason=baseline_reason,
        outcome_scope=outcome_scope,
        evidence_provenance=evidence_provenance,
        supplied_decisions=supplied_decisions,
        supplied_outcomes=supplied_outcomes,
        malformed_decisions=malformed_decisions,
        malformed_outcomes=malformed_outcomes,
    )


def _epoch_projection(
    records: Iterable[Mapping[str, Any]], source: str,
) -> list[dict[str, Any]]:
    """Deterministic, metadata-free source-population projection."""
    projection: list[dict[str, Any]] = []
    for record in records:
        epoch = classify_authoritative_evidence_record(dict(record), source)
        canonical, symbol = _decision_identity(record)
        projection.append({
            "source": source,
            "canonical_opportunity_id": canonical,
            "canonical_symbol": symbol,
            "epoch": epoch.value if isinstance(epoch, DataEpoch) else "INCOMPATIBLE",
        })
    return projection


def _finalise_evidence(
    *,
    records: list[RiskEventRecord],
    exclusions: list[RiskEventExclusion],
    candidate_ids: list[str],
    collapsed_duplicates: int,
    baseline: Mapping[str, Any] | None,
    baseline_reason: str | None,
    outcome_scope: str,
    evidence_provenance: dict[str, Any],
    supplied_decisions: tuple[dict[str, Any], ...],
    supplied_outcomes: tuple[dict[str, Any], ...],
    malformed_decisions: int,
    malformed_outcomes: int,
) -> RiskPolicyEvidence:
    """Assemble the immutable evidence container, summary and provenance."""
    allowed = [item for item in records if item.risk_disposition == ALLOWED]
    blocked = [item for item in records if item.risk_disposition == BLOCKED]
    blocked_with_counterfactual = [item for item in blocked if item.is_blocked_with_counterfactual]
    guard_exposure = Counter(guard for item in records for guard in item.active_guards)
    guard_exclusive = Counter(
        item.guard_exclusive for item in records if item.guard_exclusive
    )
    reason_counts = Counter(item.reason for item in exclusions)
    summary = RiskPolicyEvidenceSummary(
        total_candidate_observations=len(candidate_ids),
        eligible_observations=len(records),
        distinct_canonical_opportunities=len({
            item.canonical_opportunity_id for item in records
        }),
        allowed_count=len(allowed),
        blocked_count=len(blocked),
        blocked_with_valid_counterfactual_count=len(blocked_with_counterfactual),
        blocked_without_counterfactual_count=len(blocked) - len(blocked_with_counterfactual),
        guard_exposure_counts={guard: guard_exposure.get(guard, 0) for guard in GUARD_IDS},
        guard_exclusive_counts={guard: guard_exclusive.get(guard, 0) for guard in GUARD_IDS},
        multi_guard_count=sum(1 for item in records if item.multi_guard),
        unattributable_blocked_count=sum(
            1 for item in blocked
            if item.guard_authority_state in (GUARD_AUTHORITY_UNMAPPED, GUARD_AUTHORITY_MISSING)
        ),
        unmapped_guard_observation_count=sum(1 for item in records if item.unmapped_guards),
        guard_authority_missing_count=sum(
            1 for item in blocked if item.guard_authority_state == GUARD_AUTHORITY_MISSING
        ),
        account_fanout_observation_count=sum(
            1 for item in records if item.account_execution_count > 1
        ),
        collapsed_duplicate_record_count=collapsed_duplicates,
        chronological_eligible_count=len(records),
        earliest_entry_utc=(min(item.entry_utc_epoch_s for item in records) if records else None),
        latest_entry_utc=(max(item.entry_utc_epoch_s for item in records) if records else None),
        coverage_numerator=len(records),
        coverage_denominator=len(candidate_ids),
        coverage_percentage=(
            100.0 * len(records) / len(candidate_ids) if candidate_ids else 0.0
        ),
        exclusions_by_reason=dict(sorted(reason_counts.items())),
        baseline_policy_id=_text(baseline.get("policy_id")) if baseline else "",
        baseline_policy_version=(int(baseline.get("policy_version")) if baseline else None),
        baseline_identity_hash=_text(baseline.get("identity_hash")) if baseline else "",
    )
    source_projection = (
        _epoch_projection(supplied_decisions, DECISION_SOURCE)
        + _epoch_projection(supplied_outcomes, OUTCOME_SOURCE)
    )
    provenance: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "hd10_adjudication_version": HD10_ADJUDICATION_VERSION,
        "baseline_policy_id": summary.baseline_policy_id,
        "baseline_policy_version": summary.baseline_policy_version,
        "baseline_identity_hash": summary.baseline_identity_hash,
        "baseline_authority_state": baseline_reason or BASELINE_AUTHORITY_CURRENT,
        "baseline_identity_rule": BASELINE_RISK_POLICY_V1["identity_hash_rule"],
        "guard_taxonomy_identity": GUARD_TAXONOMY_IDENTITY,
        "guard_taxonomy_digest": GUARD_TAXONOMY_DIGEST,
        "outcome_authority": OUTCOME_AUTHORITY,
        "outcome_scope": outcome_scope,
        "chronology_authority": CHRONOLOGY_AUTHORITY,
        "decision_chronology_authority": DECISION_CHRONOLOGY_AUTHORITY,
        "chronological_order_key": list(CHRONOLOGICAL_ORDER_KEY),
        "source_population": {
            "decision_records": len(supplied_decisions),
            "outcome_records": len(supplied_outcomes),
            "malformed_decision_records": malformed_decisions,
            "malformed_outcome_records": malformed_outcomes,
        },
        "source_population_digest": evidence_digest(source_projection),
        "candidate_observations": len(candidate_ids),
        "eligible_observations": len(records),
        "records_excluded": len(exclusions),
        "eligible_population_digest": evidence_digest(
            item.digest_material() for item in records
        ),
        "exclusion_digest": evidence_digest(item.record() for item in exclusions),
        "chronological_population_digest": evidence_digest(
            item.analytical_record() for item in records
        ),
        "exclusions_by_reason": summary.exclusions_by_reason,
        "guard_exposure_counts": summary.guard_exposure_counts,
        "evidence_provenance_state": evidence_provenance.get("state"),
        "evidence_provenance": evidence_provenance,
        "digest_algorithm": "sha256",
        "volatile_metadata_hashed": False,
    }
    provenance["digest"] = evidence_digest((provenance,))
    return RiskPolicyEvidence(
        schema_version=SCHEMA_VERSION,
        hd10_adjudication_version=HD10_ADJUDICATION_VERSION,
        baseline_authority_state=baseline_reason or BASELINE_AUTHORITY_CURRENT,
        records=tuple(records),
        exclusions=tuple(exclusions),
        summary=summary,
        provenance=provenance,
        evidence_provenance=evidence_provenance,
        baseline_authority=dict(baseline) if baseline else {},
    )


# ─── CHRONOLOGICAL OUTCOME/PATH FOUNDATION (R3-R5) ────────────────────────────


@dataclass(frozen=True)
class ChronologicalRiskPopulation:
    """The governed population in HD10 entry-chronology order.

    Evidence construction only: no ruin simulation, no drawdown threshold, and
    no sizing model is chosen or implied here.
    """

    schema_version: str
    hd10_adjudication_version: str
    chronology_authority: str
    order_key: tuple[str, str]
    records: tuple[RiskEventRecord, ...]
    eligible_count: int
    distinct_canonical_opportunities: int
    baseline_policy_id: str
    baseline_identity_hash: str
    exclusions_by_reason: dict[str, int]
    digest: str
    provenance: dict[str, Any]

    def entry_chronology(self) -> tuple[int, ...]:
        return tuple(item.entry_utc_epoch_s for item in self.records)

    def opportunity_identities(self) -> tuple[str, ...]:
        return tuple(item.canonical_opportunity_id for item in self.records)


def build_chronological_risk_population(
    evidence: RiskPolicyEvidence,
) -> ChronologicalRiskPopulation:
    """Order the eligible canonical population by governed entry chronology.

    The order is HD10's declared R3-R5 rule: strictly increasing canonical entry
    UTC with the canonical opportunity id as the deterministic tie-break.  A
    repeated canonical opportunity would be pseudoreplication, so it fails
    closed instead of being ordered.
    """
    ordered = evidence.chronological_records()
    identities = [item.canonical_opportunity_id for item in ordered]
    if len(identities) != len(set(identities)):
        raise ValueError(
            "ACCOUNT_FANOUT_PSEUDOREPLICATION: one canonical opportunity may appear "
            "only once in the chronological population"
        )
    if any(item.entry_utc_epoch_s is None for item in ordered):
        raise ValueError("MISSING_CHRONOLOGY: every ordered record requires entry UTC")
    provenance: dict[str, Any] = {
        "schema_version": CHRONOLOGY_SCHEMA_VERSION,
        "hd10_adjudication_version": HD10_ADJUDICATION_VERSION,
        "chronology_authority": CHRONOLOGY_AUTHORITY,
        "decision_chronology_authority": DECISION_CHRONOLOGY_AUTHORITY,
        "order_key": list(CHRONOLOGICAL_ORDER_KEY),
        "source_evidence_digest": evidence.provenance["digest"],
        "source_evidence_schema_version": evidence.schema_version,
        "baseline_policy_id": evidence.summary.baseline_policy_id,
        "baseline_identity_hash": evidence.summary.baseline_identity_hash,
        "guard_taxonomy_digest": GUARD_TAXONOMY_DIGEST,
        "records": len(ordered),
        "distinct_canonical_opportunities": len(set(identities)),
        "exclusions_by_reason": dict(evidence.summary.exclusions_by_reason),
        "records_digest": evidence_digest(item.analytical_record() for item in ordered),
        "digest_algorithm": "sha256",
    }
    provenance["digest"] = evidence_digest((provenance,))
    return ChronologicalRiskPopulation(
        schema_version=CHRONOLOGY_SCHEMA_VERSION,
        hd10_adjudication_version=HD10_ADJUDICATION_VERSION,
        chronology_authority=CHRONOLOGY_AUTHORITY,
        order_key=CHRONOLOGICAL_ORDER_KEY,
        records=tuple(ordered),
        eligible_count=len(ordered),
        distinct_canonical_opportunities=len(set(identities)),
        baseline_policy_id=evidence.summary.baseline_policy_id,
        baseline_identity_hash=evidence.summary.baseline_identity_hash,
        exclusions_by_reason=dict(evidence.summary.exclusions_by_reason),
        digest=provenance["digest"],
        provenance=provenance,
    )


@dataclass(frozen=True)
class ChronologicalRiskWindows:
    """HD10's frozen calibration/validation split, as an evidence fact.

    No statistic, calibration fit, threshold, or model selection is performed
    here; this is evidence construction that R3-R5 will later consume.
    """

    schema_version: str
    calibration_records: tuple[RiskEventRecord, ...]
    validation_records: tuple[RiskEventRecord, ...]
    purged_records: tuple[RiskEventRecord, ...]
    missing_close_chronology_records: tuple[RiskEventRecord, ...]
    calibration_fraction: float
    minimum_validation_opportunities: int
    first_validation_entry_utc: int | None
    provenance: dict[str, Any]


def split_chronological_windows(
    population: ChronologicalRiskPopulation,
) -> ChronologicalRiskWindows:
    """Apply HD10's frozen 70/30 chronological windows and purge rule.

    Calibration is the earliest 70% of eligible canonical opportunities in
    canonical entry-chronology order and validation is the most recent 30%.
    Calibration evidence whose outcome window closes at or after the first
    validation entry UTC is purged, and a calibration record without an
    outcome-close chronology cannot be proven purgeable, so it fails closed.
    """
    records = population.records
    total = len(records)
    calibration_target = int(math.floor(_CALIBRATION_FRACTION * total))
    calibration_candidates = list(records[:calibration_target])
    validation = list(records[calibration_target:])
    first_validation_entry = validation[0].entry_utc_epoch_s if validation else None

    calibration: list[RiskEventRecord] = []
    purged: list[RiskEventRecord] = []
    missing_close: list[RiskEventRecord] = []
    for item in calibration_candidates:
        if item.exit_utc_epoch_s is None:
            missing_close.append(item)
            continue
        if (
            first_validation_entry is not None
            and item.exit_utc_epoch_s >= first_validation_entry
        ):
            purged.append(item)
            continue
        calibration.append(item)

    provenance: dict[str, Any] = {
        "schema_version": CHRONOLOGY_SCHEMA_VERSION,
        "hd10_adjudication_version": HD10_ADJUDICATION_VERSION,
        "source_population_digest": population.digest,
        "chronology_authority": CHRONOLOGY_AUTHORITY,
        "order_key": list(CHRONOLOGICAL_ORDER_KEY),
        "calibration_fraction": _CALIBRATION_FRACTION,
        "minimum_validation_opportunities": MINIMUM_VALIDATION_OPPORTUNITIES,
        "eligible_records": total,
        "calibration_records": len(calibration),
        "validation_records": len(validation),
        "first_validation_entry_utc": first_validation_entry,
        "purged_records": len(purged),
        "purge_reason": PURGED_OUTCOME_WINDOW_OVERLAP,
        "missing_close_chronology_records": len(missing_close),
        "missing_close_chronology_reason": "MISSING_OUTCOME_CLOSE_CHRONOLOGY",
        "calibration_digest": evidence_digest(
            item.analytical_record() for item in calibration
        ),
        "validation_digest": evidence_digest(
            item.analytical_record() for item in validation
        ),
        "digest_algorithm": "sha256",
    }
    provenance["digest"] = evidence_digest((provenance,))
    return ChronologicalRiskWindows(
        schema_version=CHRONOLOGY_SCHEMA_VERSION,
        calibration_records=tuple(calibration),
        validation_records=tuple(validation),
        purged_records=tuple(purged),
        missing_close_chronology_records=tuple(missing_close),
        calibration_fraction=_CALIBRATION_FRACTION,
        minimum_validation_opportunities=MINIMUM_VALIDATION_OPPORTUNITIES,
        first_validation_entry_utc=first_validation_entry,
        provenance=provenance,
    )
