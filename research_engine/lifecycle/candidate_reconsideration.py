"""Wave 6.3A - candidate reconsideration / redevelopment eligibility (pure)."""
from __future__ import annotations
import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any
__all__ = ["ReconsiderationStatus", "HistoricalCandidateOutcome",
    "HistoricalHumanDecision", "CandidateReconsiderationDecision",
    "decide_candidate_reconsideration", "compute_reconsideration_id",
    "MATERIAL_BASELINE_CHANGE_AFTER_FAILED_EXPERIMENT",
    "MATERIAL_BASELINE_CHANGE_AFTER_INCONCLUSIVE_EXPERIMENT",
    "PARTIAL_BASELINE_CHANGE_REQUIRES_REVALIDATION",
    "BASELINE_CHANGE_UNAFFECTED", "HISTORICAL_CANDIDATE_NOT_TERMINAL",
    "HISTORICAL_CANDIDATE_ALREADY_SUCCESSFUL",
    "HUMAN_REJECTION_NOT_OVERRIDDEN",
    "CANDIDATE_CAUSED_TARGET_TRANSITION", "CONTINUITY_INDETERMINATE",
    "IMPACT_INDETERMINATE", "NO_HISTORICAL_EVIDENCE", "PROVENANCE_MISMATCH",
    "CONFLICTING_HISTORICAL_OUTCOME", "MALFORMED_HISTORICAL_OUTCOME",
    "REVALIDATION_REQUIRED_NO_FRESH_CONTEXT",
    "FRESH_EVIDENCE_REQUIRED_FOR_REVALIDATION"]
class ReconsiderationStatus(str, Enum):
    ELIGIBLE_FOR_RECONSIDERATION = "ELIGIBLE_FOR_RECONSIDERATION"
    NOT_ELIGIBLE = "NOT_ELIGIBLE"
    FRESH_EVIDENCE_REQUIRED = "FRESH_EVIDENCE_REQUIRED"
    INDETERMINATE = "INDETERMINATE"
MATERIAL_BASELINE_CHANGE_AFTER_FAILED_EXPERIMENT = "MATERIAL_BASELINE_CHANGE_AFTER_FAILED_EXPERIMENT"
MATERIAL_BASELINE_CHANGE_AFTER_INCONCLUSIVE_EXPERIMENT = "MATERIAL_BASELINE_CHANGE_AFTER_INCONCLUSIVE_EXPERIMENT"
PARTIAL_BASELINE_CHANGE_REQUIRES_REVALIDATION = "PARTIAL_BASELINE_CHANGE_REQUIRES_REVALIDATION"
BASELINE_CHANGE_UNAFFECTED = "BASELINE_CHANGE_UNAFFECTED"
HISTORICAL_CANDIDATE_NOT_TERMINAL = "HISTORICAL_CANDIDATE_NOT_TERMINAL"
HISTORICAL_CANDIDATE_ALREADY_SUCCESSFUL = "HISTORICAL_CANDIDATE_ALREADY_SUCCESSFUL"
HUMAN_REJECTION_NOT_OVERRIDDEN = "HUMAN_REJECTION_NOT_OVERRIDDEN"
CANDIDATE_CAUSED_TARGET_TRANSITION = "CANDIDATE_CAUSED_TARGET_TRANSITION"
CONTINUITY_INDETERMINATE = "CONTINUITY_INDETERMINATE"
IMPACT_INDETERMINATE = "IMPACT_INDETERMINATE"
NO_HISTORICAL_EVIDENCE = "NO_HISTORICAL_EVIDENCE"
PROVENANCE_MISMATCH = "PROVENANCE_MISMATCH"
CONFLICTING_HISTORICAL_OUTCOME = "CONFLICTING_HISTORICAL_OUTCOME"
MALFORMED_HISTORICAL_OUTCOME = "MALFORMED_HISTORICAL_OUTCOME"
REVALIDATION_REQUIRED_NO_FRESH_CONTEXT = "REVALIDATION_REQUIRED_NO_FRESH_CONTEXT"
FRESH_EVIDENCE_REQUIRED_FOR_REVALIDATION = "FRESH_EVIDENCE_REQUIRED_FOR_REVALIDATION"
_REASON_ORDER = (MATERIAL_BASELINE_CHANGE_AFTER_FAILED_EXPERIMENT, MATERIAL_BASELINE_CHANGE_AFTER_INCONCLUSIVE_EXPERIMENT, PARTIAL_BASELINE_CHANGE_REQUIRES_REVALIDATION, BASELINE_CHANGE_UNAFFECTED, HISTORICAL_CANDIDATE_NOT_TERMINAL, HISTORICAL_CANDIDATE_ALREADY_SUCCESSFUL, HUMAN_REJECTION_NOT_OVERRIDDEN, CANDIDATE_CAUSED_TARGET_TRANSITION, CONTINUITY_INDETERMINATE, IMPACT_INDETERMINATE, NO_HISTORICAL_EVIDENCE, PROVENANCE_MISMATCH, CONFLICTING_HISTORICAL_OUTCOME, MALFORMED_HISTORICAL_OUTCOME, REVALIDATION_REQUIRED_NO_FRESH_CONTEXT, FRESH_EVIDENCE_REQUIRED_FOR_REVALIDATION)
_TERMINAL_STATUSES = frozenset({"ACCEPTED", "REJECTED", "ARCHIVED"})
_FAILED_EXPERIMENT_STATUSES = frozenset({"FAILED_VALIDATION", "REGRESSION_DETECTED"})
_ACTIVE_STATUSES = frozenset({"PROPOSED", "VALIDATING", "VALIDATED", "SHADOW_TESTING", "READY_FOR_REVIEW"})
_KNOWN_EVAL = frozenset({"VALIDATED", "REJECTED", "INCONCLUSIVE"})
_KNOWN_HUMAN = frozenset({"ACCEPT", "REJECT"})
@dataclass(frozen=True)
class CandidateReconsiderationDecision:
    reconsideration_id: str
    status: ReconsiderationStatus
    candidate_id: str
    historical_baseline_id: str
    historical_baseline_config_hash: str
    target_baseline_id: str
    target_baseline_config_hash: str
    impact_id: str
    continuity_id: str
    candidate_treatment_id: str
    historical_candidate_status: str
    historical_evaluation_outcome: str
    reason_codes: tuple[str, ...]
    fresh_evidence_required: bool
    limitations: tuple[str, ...]
    def to_dict(self):
        return {"reconsideration_id": self.reconsideration_id, "status": self.status.value, "candidate_id": self.candidate_id, "historical_baseline_id": self.historical_baseline_id, "historical_baseline_config_hash": self.historical_baseline_config_hash, "target_baseline_id": self.target_baseline_id, "target_baseline_config_hash": self.target_baseline_config_hash, "impact_id": self.impact_id, "continuity_id": self.continuity_id, "candidate_treatment_id": self.candidate_treatment_id, "historical_candidate_status": self.historical_candidate_status, "historical_evaluation_outcome": self.historical_evaluation_outcome, "reason_codes": list(self.reason_codes), "fresh_evidence_required": self.fresh_evidence_required, "limitations": list(self.limitations)}
_RECON_ID_FIELDS = ("candidate_id", "historical_baseline_id", "historical_baseline_config_hash", "target_baseline_id", "target_baseline_config_hash", "impact_id", "continuity_id", "candidate_treatment_id", "historical_candidate_status", "historical_evaluation_outcome", "historical_human_decision", "classification", "continuity_state", "status", "reason_codes", "fresh_evidence_required", "transition_candidate_id")
def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
def _digest(value):
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()
def _ordered(reasons):
    rank = {c: i for i, c in enumerate(_REASON_ORDER)}
    return tuple(sorted(set(r for r in reasons if r in rank), key=rank.__getitem__))
def _non_empty_str(v):
    return isinstance(v, str) and bool(v.strip())
def compute_reconsideration_id(**identity):
    missing = [f for f in _RECON_ID_FIELDS if f not in identity]
    if missing:
        raise ValueError("identity missing %s" % (missing,))
    extra = [k for k in identity if k not in _RECON_ID_FIELDS]
    if extra:
        raise ValueError("identity unexpected %s" % (extra,))
    codes = identity["reason_codes"]
    if not isinstance(codes, (list, tuple)) or not all(isinstance(c, str) and c.strip() for c in codes):
        raise ValueError("malformed reason_codes")
    return "R63A-" + _digest({f: identity[f] for f in _RECON_ID_FIELDS})
def _summarise_eval(decisions):
    if not decisions:
        raise ValueError("missing evaluation history")
    distinct = set(decisions)
    unknown = distinct - _KNOWN_EVAL
    if unknown:
        raise ValueError("unknown evaluation %r" % (sorted(unknown),))
    groups = [bool(distinct & {"REJECTED"}), bool(distinct & {"INCONCLUSIVE"}), bool(distinct & {"VALIDATED"})]
    if sum(groups) > 1:
        raise ValueError("conflicting evaluation %r" % (sorted(distinct),))
    if "REJECTED" in distinct:
        return "REJECTED"
    if "INCONCLUSIVE" in distinct:
        return "INCONCLUSIVE"
    return "VALIDATED"

@dataclass(frozen=True)
class HistoricalCandidateOutcome:
    candidate_status: str
    evaluation_decisions: tuple[str, ...] = ()
@dataclass(frozen=True)
class HistoricalHumanDecision:
    present: bool = False
    decision: str = ""
    outcome: str = ""
def decide_candidate_reconsideration(*, impact_record, continuity=None, historical_outcome, human_decision=None, transition_candidate_id=None, population=None):
    human = human_decision if human_decision is not None else HistoricalHumanDecision()
    g = lambda o, n, d="": getattr(o, n, d)
    impact_id = g(impact_record, "impact_id"); classification = g(impact_record, "classification")
    ic = g(impact_record, "candidate_id"); ih = g(impact_record, "candidate_baseline_id")
    ihh = g(impact_record, "candidate_baseline_config_hash"); ito = g(impact_record, "to_baseline_id")
    itoh = g(impact_record, "to_baseline_config_hash"); iapp = g(impact_record, "application_id"); itrt = g(impact_record, "candidate_treatment_id")
    cc = g(continuity, "candidate_id") if continuity is not None else ""
    cid = g(continuity, "continuity_id") if continuity is not None else ""
    ch = g(continuity, "historical_baseline_id") if continuity is not None else ""
    chh = g(continuity, "historical_baseline_config_hash") if continuity is not None else ""
    cto = g(continuity, "target_baseline_id") if continuity is not None else ""
    ctoh = g(continuity, "target_baseline_config_hash") if continuity is not None else ""
    cimp = g(continuity, "impact_id") if continuity is not None else ""
    capp = g(continuity, "application_id") if continuity is not None else ""
    ctrt = g(continuity, "candidate_treatment_id") if continuity is not None else ""
    raw = g(continuity, "state", "") if continuity is not None else ""
    cstate = raw.value if hasattr(raw, "value") else (raw if isinstance(raw, str) else "")
    cand = ic or cc; hist = ih or ch; histh = ihh or chh; tgt = ito or cto; tgth = itoh or ctoh
    def _mk(status, reasons, limits, outcome="UNKNOWN", fresh=False, cd="", hd="", hh="", td="", th="", ctd=""):
        ordered = _ordered(list(reasons))
        hdec = ("%s:%s" % (human.decision, human.outcome)) if human.present else "NONE"
        tc = transition_candidate_id if _non_empty_str(transition_candidate_id) else "?"
        rid = compute_reconsideration_id(candidate_id=cd or "?", historical_baseline_id=hd or "?", historical_baseline_config_hash=hh or "?", target_baseline_id=td or "?", target_baseline_config_hash=th or "?", impact_id=impact_id if _non_empty_str(impact_id) else "?", continuity_id=ctd or "", candidate_treatment_id=itrt if _non_empty_str(itrt) else "?", historical_candidate_status=historical_outcome.candidate_status if _non_empty_str(historical_outcome.candidate_status) else "?", historical_evaluation_outcome=outcome, historical_human_decision=hdec, classification=classification if _non_empty_str(classification) else "?", continuity_state=cstate if _non_empty_str(cstate) else "?", status=status.value, reason_codes=list(ordered), fresh_evidence_required=fresh, transition_candidate_id=tc)
        return CandidateReconsiderationDecision(reconsideration_id=rid, status=status, candidate_id=cd or "?", historical_baseline_id=hd or "?", historical_baseline_config_hash=hh or "?", target_baseline_id=td or "?", target_baseline_config_hash=th or "?", impact_id=impact_id if _non_empty_str(impact_id) else "?", continuity_id=ctd or "", candidate_treatment_id=itrt if _non_empty_str(itrt) else "?", historical_candidate_status=historical_outcome.candidate_status if _non_empty_str(historical_outcome.candidate_status) else "?", historical_evaluation_outcome=outcome, reason_codes=ordered, fresh_evidence_required=fresh, limitations=tuple(limits))
    probs = []
    for label, v in (("impact candidate", ic), ("impact_id", impact_id), ("hist baseline", ih), ("hist config", ihh), ("target baseline", ito), ("target config", itoh), ("treatment", itrt), ("classification", classification)):
        if not _non_empty_str(v):
            probs.append("impact lacks %s" % label)
    if continuity is None:
        probs.append("no continuity snapshot bound")
    else:
        for label, v in (("candidate", cc), ("continuity_id", cid), ("impact", cimp), ("hist baseline", ch), ("hist config", chh), ("target baseline", cto), ("target config", ctoh), ("treatment", ctrt), ("state", cstate)):
            if not _non_empty_str(v):
                probs.append("continuity lacks %s" % label)
        if _non_empty_str(cimp) and _non_empty_str(impact_id) and cimp != impact_id:
            probs.append("continuity impact_id mismatch")
        for label, a, b in (("candidate", cc, ic), ("hist baseline", ch, ih), ("hist config", chh, ihh), ("target baseline", cto, ito), ("target config", ctoh, itoh), ("application", capp, iapp), ("treatment", ctrt, itrt)):
            if _non_empty_str(a) and _non_empty_str(b) and a != b:
                probs.append("continuity/impact %s mismatch" % label)
    if classification not in ("UNAFFECTED", "PARTIALLY_AFFECTED", "MATERIALLY_AFFECTED", "INDETERMINATE"):
        probs.append("unknown classification %r" % (classification,))
    if cstate and cstate not in ("CONTINUITY_ALLOWED", "REVALIDATION_REQUIRED", "BLOCKED_INDETERMINATE", "NO_HISTORICAL_EVIDENCE"):
        probs.append("unknown continuity %r" % (cstate,))
    if population is not None:
        for label, a, b in (("candidate", g(population, "candidate_id"), cand), ("impact", g(population, "impact_id"), impact_id), ("continuity", g(population, "continuity_id"), cid), ("target baseline", g(population, "target_baseline_id"), tgt), ("target config", g(population, "target_baseline_config_hash"), tgth), ("treatment", g(population, "candidate_treatment_id"), itrt)):
            if _non_empty_str(a) and _non_empty_str(b) and a != b:
                probs.append("population %s mismatch" % label)
    sv = historical_outcome.candidate_status
    if not _non_empty_str(sv):
        return _mk(ReconsiderationStatus.INDETERMINATE, [MALFORMED_HISTORICAL_OUTCOME, PROVENANCE_MISMATCH], ["missing candidate status"])
    try:
        outcome = _summarise_eval(tuple(historical_outcome.evaluation_decisions))
    except ValueError as exc:
        msg = str(exc)
        if "conflicting" in msg:
            return _mk(ReconsiderationStatus.INDETERMINATE, [CONFLICTING_HISTORICAL_OUTCOME, PROVENANCE_MISMATCH], ["conflicting eval: %s" % msg])
        if "unknown" in msg:
            return _mk(ReconsiderationStatus.INDETERMINATE, [MALFORMED_HISTORICAL_OUTCOME, PROVENANCE_MISMATCH], ["unknown eval: %s" % msg])
        return _mk(ReconsiderationStatus.INDETERMINATE, [MALFORMED_HISTORICAL_OUTCOME, NO_HISTORICAL_EVIDENCE], ["no eval history"])
    if human.present and (human.decision not in _KNOWN_HUMAN or human.outcome != "COMPLETED"):
        return _mk(ReconsiderationStatus.INDETERMINATE, [HUMAN_REJECTION_NOT_OVERRIDDEN, PROVENANCE_MISMATCH], ["ambiguous human decision"], outcome, True, cand, hist, histh, tgt, tgth, cid)
    if probs:
        return _mk(ReconsiderationStatus.INDETERMINATE, [PROVENANCE_MISMATCH], probs, outcome, ("BLOCKED_INDETERMINATE" in (cstate, classification)), cand, hist, histh, tgt, tgth, cid)
    def B(s, r, l, f=False):
        return _mk(s, r, l, outcome, f, cand, hist, histh, tgt, tgth, cid)
    if not _non_empty_str(transition_candidate_id):
        return B(ReconsiderationStatus.INDETERMINATE, [PROVENANCE_MISMATCH], ["deploying candidate identity not supplied"], True)
    if transition_candidate_id == cand:
        return B(ReconsiderationStatus.NOT_ELIGIBLE, [CANDIDATE_CAUSED_TARGET_TRANSITION], ["candidate caused N->N+1"])
    if sv in _ACTIVE_STATUSES:
        return B(ReconsiderationStatus.NOT_ELIGIBLE, [HISTORICAL_CANDIDATE_NOT_TERMINAL], ["status %r not terminal" % sv])
    if human.present and human.decision == "REJECT":
        return B(ReconsiderationStatus.NOT_ELIGIBLE, [HUMAN_REJECTION_NOT_OVERRIDDEN], ["explicit REJECT governs"])
    if sv in ("ACCEPTED",) or outcome == "VALIDATED" or (human.present and human.decision == "ACCEPT"):
        return B(ReconsiderationStatus.NOT_ELIGIBLE, [HISTORICAL_CANDIDATE_ALREADY_SUCCESSFUL], ["already successful"])
    if classification == "INDETERMINATE":
        return B(ReconsiderationStatus.INDETERMINATE, [IMPACT_INDETERMINATE], ["impact INDETERMINATE"], True)
    if cstate == "BLOCKED_INDETERMINATE":
        return B(ReconsiderationStatus.INDETERMINATE, [CONTINUITY_INDETERMINATE], ["continuity BLOCKED"], True)
    if cstate == "NO_HISTORICAL_EVIDENCE":
        return B(ReconsiderationStatus.INDETERMINATE, [NO_HISTORICAL_EVIDENCE], ["no historical evidence"], True)
    failed = outcome == "REJECTED" or sv in _FAILED_EXPERIMENT_STATUSES
    inconc = outcome == "INCONCLUSIVE"
    term = failed or inconc
    if classification == "UNAFFECTED":
        return B(ReconsiderationStatus.NOT_ELIGIBLE, [BASELINE_CHANGE_UNAFFECTED], ["transition did not affect context"])
    if classification == "MATERIALLY_AFFECTED":
        if failed:
            return B(ReconsiderationStatus.ELIGIBLE_FOR_RECONSIDERATION, [MATERIAL_BASELINE_CHANGE_AFTER_FAILED_EXPERIMENT], ["terminal failed; context materially changed"], True)
        if inconc:
            return B(ReconsiderationStatus.ELIGIBLE_FOR_RECONSIDERATION, [MATERIAL_BASELINE_CHANGE_AFTER_INCONCLUSIVE_EXPERIMENT], ["terminal inconclusive; context materially changed"], True)
        return B(ReconsiderationStatus.NOT_ELIGIBLE, [HISTORICAL_CANDIDATE_ALREADY_SUCCESSFUL], ["material change alone"])
    if classification == "PARTIALLY_AFFECTED":
        if not term:
            return B(ReconsiderationStatus.NOT_ELIGIBLE, [HISTORICAL_CANDIDATE_ALREADY_SUCCESSFUL], ["partial change; not failed"])
        if cstate == "REVALIDATION_REQUIRED":
            return B(ReconsiderationStatus.FRESH_EVIDENCE_REQUIRED, [PARTIAL_BASELINE_CHANGE_REQUIRES_REVALIDATION, FRESH_EVIDENCE_REQUIRED_FOR_REVALIDATION], ["partial + revalidation; fresh N+1 first"], True)
        if cstate == "CONTINUITY_ALLOWED":
            return B(ReconsiderationStatus.NOT_ELIGIBLE, [BASELINE_CHANGE_UNAFFECTED], ["evidence continuous; partial alone"])
        return B(ReconsiderationStatus.INDETERMINATE, [REVALIDATION_REQUIRED_NO_FRESH_CONTEXT], ["partial w/o continuity"], True)
    return B(ReconsiderationStatus.INDETERMINATE, [IMPACT_INDETERMINATE], ["unrecognized classification"], True)

