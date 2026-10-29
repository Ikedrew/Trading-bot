"""Canonical assured epistemic finding authority (Stage 4, Q1-Q70)."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from research_engine.v10.universes.assurance_consumption_gate import (
    gate_hypothesis_source as _gate_hypothesis_source,
)

CERTIFICATION_PATH = Path(
    "analysis/assurance/final_70_question_certification_20260928.json")
MANIFEST_PATH = Path("analysis/assurance/historical_research_pass_20260928.json")
DATA_GAPS_PATH = Path(
    "analysis/assurance/stage4_dataset_schema_gap_register_20260928.json")
IMPLEMENTATION_GAPS_PATH = Path(
    "analysis/assurance/stage4_implementation_gap_register_20260928.json")

EXPECTED_CERTIFICATION_FINGERPRINT = (
    "b42b4bfcfa1eaf7df09cac9f88011c89362cf2ca907217e8466fff9fea3adc8b"
)

STORE_SCHEMA = 1
FINDING_VERSION = 1

SETTLED_STATES = frozenset({"COMPLETE", "NEGATIVE_RESULT"})
EVIDENCE_WORK_STATES = frozenset(
    {"INSUFFICIENT_DATA", "WAITING_DATA", "HISTORICALLY_UNANSWERABLE"})
REPAIR_STATES = frozenset({"IMPLEMENTATION_BLOCKED"})

TRUTH_CONSUMABLE_PERMITTED = ("OPTIMISATION", "INVESTIGATION")
EVIDENCE_WORK_PERMITTED = ("EVIDENCE_COLLECTION", "OBSERVABILITY")
REPAIR_PERMITTED = ("IMPLEMENTATION_REPAIR",)


class AssuredFindingsError(RuntimeError):
    """A downstream engine violated assured-finding consumption rules."""


@dataclass(frozen=True)
class AssuredFindingDecision:
    question_id: str
    assurance_status: str
    scientific_state: str
    admitted: bool
    scientific_truth_consumable: bool
    permitted_consumption: tuple[str, ...]
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "assurance_status": self.assurance_status,
            "scientific_state": self.scientific_state,
            "admitted": self.admitted,
            "scientific_truth_consumable": self.scientific_truth_consumable,
            "permitted_consumption": list(self.permitted_consumption),
            "reason": self.reason,
        }


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssuredFindingsError(f"{path} is not a JSON object")
    return value


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True, default=str).encode("utf-8")).hexdigest()


ALLOWED_STATES = (SETTLED_STATES | EVIDENCE_WORK_STATES
                  | REPAIR_STATES)


def load_authority_inputs() -> dict[str, Any]:
    certification = _read_json(CERTIFICATION_PATH)
    if (certification.get("certification_fingerprint")
            != EXPECTED_CERTIFICATION_FINGERPRINT):
        raise AssuredFindingsError("CERTIFICATION_FINGERPRINT_CHANGED")
    if certification.get("question_count") != 70:
        raise AssuredFindingsError("CERTIFICATION_NOT_EXACTLY_70")
    return {
        "certification": certification,
        "manifest": _read_json(MANIFEST_PATH),
        "data_gaps": _read_json(DATA_GAPS_PATH),
        "implementation_gaps": _read_json(IMPLEMENTATION_GAPS_PATH),
    }


def decide(qid: str, assurance: str, science: str) -> AssuredFindingDecision:
    verdict = str(assurance or "").upper()
    state = str(science or "").upper()
    if verdict != "VERIFIED":
        return AssuredFindingDecision(
            qid, verdict, state, False, False, (),
            "assurance not VERIFIED")
    if state in SETTLED_STATES:
        return AssuredFindingDecision(
            qid, verdict, state, True, True, TRUTH_CONSUMABLE_PERMITTED,
            "verified settled state")
    if state in EVIDENCE_WORK_STATES:
        return AssuredFindingDecision(
            qid, verdict, state, True, False, EVIDENCE_WORK_PERMITTED,
            "verified unresolved: evidence work only")
    if state in REPAIR_STATES:
        return AssuredFindingDecision(
            qid, verdict, state, True, False, REPAIR_PERMITTED,
            "verified block: repair only")
    return AssuredFindingDecision(
        qid, verdict, state, False, False, (),
        "not a governed downstream state")



def assert_truth_consumable(d: AssuredFindingDecision,
                            ) -> AssuredFindingDecision:
    if not d.admitted or not d.scientific_truth_consumable:
        raise AssuredFindingsError(f"{d.question_id}: {d.reason}")
    return d


def consume_scientific_truth(f: Mapping[str, Any]) -> Mapping[str, Any]:
    assert_truth_consumable(decide(str(f.get("question_id", "")),
                                   str(f.get("assurance_status", "")),
                                   str(f.get("scientific_state", ""))))
    return f


def gate_q71(qid: str, assurance: str, science: str,
             claim: bool = False) -> AssuredFindingDecision:
    state = str(science or "").upper()
    if state in REPAIR_STATES:
        dec = decide(qid, assurance, science)
        if claim:
            assert_truth_consumable(dec)
        return dec
    up = _gate_hypothesis_source(
        question_id=qid, assurance_state=assurance,
        scientific_state=science, claim_is_asserted=claim)
    return AssuredFindingDecision(
        up.question_id, up.assurance_state, up.scientific_state,
        up.admitted, up.scientific_truth_consumable,
        tuple(up.permitted_hypothesis_classes), up.reason)


def persist_all(base: str = "analysis/assurance") -> dict[str, str]:
    from pathlib import Path as _P
    import json as _j
    store = build_store()
    out = _P(base)
    out.mkdir(parents=True, exist_ok=True)
    stamp = "20260928"
    paths: dict[str, str] = {}
    store_p = out / f"assured_epistemic_findings_{stamp}.json"
    store_p.write_text(_j.dumps(store, indent=2, sort_keys=True) + "\n",
                       encoding="utf-8")
    paths["store"] = str(store_p)
    _write_reports(out, store)
    return paths


def build_store() -> dict[str, Any]:
    inp = load_authority_inputs()
    cert = inp["certification"]
    mrows = {str(r.get("question_id")): r
             for r in inp["manifest"].get("questions", ())}
    dgap = {str(q) for g in inp["data_gaps"].get("gaps", ())
            for q in g.get("affected_question_ids", ())}
    igap = {str(g.get("question_id"))
            for g in inp["implementation_gaps"].get("gaps", ())}
    out: list[dict[str, Any]] = []
    for c in cert["certifications"]:
        q = str(c.get("question_id", ""))
        s = str(c.get("scientific_state", ""))
        a = str(c.get("assurance_status", ""))
        if s == "HISTORICALLY_UNANSWERABLE" and q not in dgap:
            raise AssuredFindingsError(q + ": missing schema-gap link")
        if s == "IMPLEMENTATION_BLOCKED" and q not in igap:
            raise AssuredFindingsError(q + ": missing impl-gap link")
        mr = mrows.get(q, {})
        dec = decide(q, a, s)
        rp = str(mr.get("current_report_path", "") or "").replace(
            "\\", "/")
        out.append({
            "question_id": q,
            "canonical_question": c.get("canonical_question", ""),
            "assurance_status": a,
            "assurance_reason": c.get("assurance_reason", ""),
            "scientific_state": s,
            "interpretation": _interp(q, c, mr),
            "finding_version": FINDING_VERSION,
            "finding_status": "CURRENT",
            "supersedes": None,
            "report_status": c.get("report_status", ""),
            "report_authority_valid": c.get("report_authority_valid"),
            "current_report_path": rp,
            "result_fingerprint": c.get("result_fingerprint", ""),
            "evidence_fingerprint": c.get("evidence_fingerprint", ""),
            "evidence_epoch": c.get("evidence_epoch", ""),
            "candidate_count": c.get("candidate_count"),
            "usable_count": c.get("usable_count"),
            "analytical_count": c.get("analytical_count"),
            "exclusion_count": c.get("exclusion_count"),
            "runner_method": c.get("runner_method", ""),
            "historical_exhaustion": c.get("historical_exhaustion", ""),
            "next_action": c.get("next_action", "NONE"),
            "schema_gap_id": ("STAGE4-DATA-" + q
                              if s == "HISTORICALLY_UNANSWERABLE" else None),
            "implementation_gap_id": (q if s == "IMPLEMENTATION_BLOCKED"
                                      else None),
            "consumption": dec.to_dict(),
        })
    out.sort(key=lambda i: i["question_id"])
    counts: dict[str, int] = {}
    for i in out:
        counts[i["scientific_state"]] = counts.get(
            i["scientific_state"], 0) + 1
    mat: dict[str, Any] = {
        "schema": STORE_SCHEMA,
        "stage": "STAGE4_ASSURED_EPISTEMIC_FINDINGS",
        "finding_version": FINDING_VERSION,
        "certification_fingerprint": EXPECTED_CERTIFICATION_FINGERPRINT,
        "manifest_fingerprint": inp["manifest"].get("fingerprint", ""),
        "question_count": len(out),
        "scientific_state_counts": counts,
        "q71_started": False,
        "supersession": {
            "policy": "immutable history; revisions append",
            "superseded_count": 0,
        },
        "findings": out,
    }
    mat["store_fingerprint"] = _fingerprint(mat)
    return mat



# ---------------------------------------------------------------------------
# Versioned assured-finding publication (Stage 4 governed re-entry)
#
# The V1 store above is the immutable historical snapshot: FINDING_VERSION
# remains 1 for that artifact and it is never rewritten.  The helpers below
# are the pure, version-aware selection/validation rules reused by the
# scientific re-entry authority when it publishes finding V2+ for a single
# question.  Exactly one CURRENT finding must exist per question at all
# times, versions are contiguous from 1, and supersession is append-only.
# ---------------------------------------------------------------------------

FINDING_HISTORY_SCHEMA = 1
FINDING_STATUS_CURRENT = "CURRENT"
FINDING_STATUS_SUPERSEDED = "SUPERSEDED"


def finding_identity(question_id: str, version: int) -> str:
    """Canonical durable identity of one assured-finding version (``R1:v1``)."""
    return f"{str(question_id).strip().upper()}:v{int(version)}"


def select_current_finding(
        question_id: str,
        rows: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    """Return the single CURRENT finding for a question, or fail closed."""
    qid = str(question_id).strip().upper()
    if not rows:
        raise AssuredFindingsError(f"{qid}:ZERO_FINDING_HISTORY")
    current = [row for row in rows
               if row.get("finding_status") == FINDING_STATUS_CURRENT]
    if not current:
        raise AssuredFindingsError(f"{qid}:ZERO_CURRENT_FINDING")
    if len(current) > 1:
        raise AssuredFindingsError(f"{qid}:DUPLICATE_CURRENT_FINDING")
    row = current[0]
    if str(row.get("question_id") or "").strip().upper() != qid:
        raise AssuredFindingsError(f"{qid}:FINDING_IDENTITY_MISMATCH")
    if int(row.get("finding_version") or 0) != max(
            int(item.get("finding_version") or 0) for item in rows):
        raise AssuredFindingsError(f"{qid}:CURRENT_NOT_LATEST_VERSION")
    return row


def validate_finding_history(
        question_id: str,
        rows: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    """Fail-closed structural validation of one question's finding history.

    Enforces: non-empty history, contiguous versions starting at 1, exact
    identity per version, a linear append-only supersession chain with an
    existing predecessor, no cyclic supersession, exactly one CURRENT row
    (the latest version) and SUPERSEDED lifecycle metadata elsewhere.
    """
    qid = str(question_id).strip().upper()
    if not rows:
        raise AssuredFindingsError(f"{qid}:ZERO_FINDING_HISTORY")
    versions: list[int] = []
    for row in rows:
        try:
            versions.append(int(row.get("finding_version")))
        except (TypeError, ValueError):
            raise AssuredFindingsError(f"{qid}:FINDING_VERSION_INVALID")
    if versions != list(range(1, len(rows) + 1)):
        raise AssuredFindingsError(f"{qid}:FINDING_VERSION_REGRESSION")
    by_identity = {finding_identity(qid, version): version
                   for version in versions}
    if len(by_identity) != len(rows):
        raise AssuredFindingsError(f"{qid}:DUPLICATE_FINDING_IDENTITY")
    for index, row in enumerate(rows, start=1):
        version = versions[index - 1]
        if str(row.get("question_id") or "").strip().upper() != qid:
            raise AssuredFindingsError(f"{qid}:FINDING_IDENTITY_MISMATCH")
        if str(row.get("finding_id") or "") != finding_identity(qid, version):
            raise AssuredFindingsError(f"{qid}:FINDING_ID_MISMATCH")
        expected_supersedes = (None if index == 1
                               else finding_identity(qid, index - 1))
        if row.get("supersedes") != expected_supersedes:
            raise AssuredFindingsError(f"{qid}:FINDING_PREDECESSOR_MISSING")
        predecessor = row.get("supersedes")
        if predecessor is not None and predecessor not in by_identity:
            raise AssuredFindingsError(f"{qid}:FINDING_PREDECESSOR_MISSING")
        successor = row.get("superseded_by")
        if successor is not None and successor not in by_identity:
            raise AssuredFindingsError(f"{qid}:FINDING_SUCCESSOR_UNKNOWN")
        if successor is not None:
            if by_identity.get(successor, 0) <= version:
                raise AssuredFindingsError(f"{qid}:CYCLIC_SUPERSSESSION")
    # Exactly one CURRENT row and it must be the latest version.
    current = select_current_finding(qid, rows)
    if int(current.get("finding_version")) != versions[-1]:
        raise AssuredFindingsError(f"{qid}:CURRENT_NOT_LATEST_VERSION")
    for row in rows[:-1]:
        if row.get("finding_status") != FINDING_STATUS_SUPERSEDED:
            raise AssuredFindingsError(f"{qid}:SUPERSEDED_STATUS_MISSING")
        expected = finding_identity(qid, int(row.get("finding_version")) + 1)
        if row.get("superseded_by") != expected:
            raise AssuredFindingsError(f"{qid}:SUPERSESSION_CHAIN_BROKEN")
    if rows[-1].get("superseded_by") is not None:
        raise AssuredFindingsError(f"{qid}:LATEST_FINDING_SUPERSEDED")
    return current



def _interp(qid: str, cert: Mapping[str, Any],
            mrow: Mapping[str, Any]) -> str:
    state = str(cert.get("scientific_state", ""))
    canon = str(cert.get("canonical_question", ""))
    u = cert.get("usable_count")
    a = cert.get("analytical_count")
    c = cert.get("candidate_count")
    run = str(cert.get("runner_method", ""))
    rst = str(cert.get("report_status", ""))
    reason = str(mrow.get("reason", cert.get("report_validity_reason", "")))
    base = f"{qid} ({canon}): {reason}. usable={u} anal={a} cand={c}."
    if state == "COMPLETE":
        return ("Resolved positive " + base + f" {run} report {rst}. "
                + "Consumable as scientific truth.")
    if state == "NEGATIVE_RESULT":
        return ("Resolved negative/null " + base + f" {run} report {rst}. "
                + "Settled finding, not absence of evidence.")
    if state == "INSUFFICIENT_DATA":
        act = cert.get("next_action")
        miss = act.get("missing_observables", []) if isinstance(
            act, Mapping) else []
        return ("Unresolved " + base + f" missing {miss}. "
                + "Shortfall/revisit semantics; NOT market truth.")
    if state == "WAITING_DATA":
        act = cert.get("next_action")
        trig = ""
        if isinstance(act, Mapping):
            trig = (f" trigger {act.get('trigger_type')} "
                    f"{act.get('missing_observables')}; "
                    f"{act.get('condition')}.")
        return "Unresolved " + base + trig + " NOT market truth."
    if state == "HISTORICALLY_UNANSWERABLE":
        return ("Unresolved unrecoverable " + base
                + f" schema gap STAGE4-DATA-{qid}; V2/V3 additive only.")
    if state == "IMPLEMENTATION_BLOCKED":
        return ("Unresolved impl block " + base + f" {run} report {rst}; "
                + f"impl-gap {qid}; repair only.")
    return "Unresolved " + base


def _write_reports(out: Any, store: dict[str, Any]) -> None:
    import json as _j
    stamp = "20260928"
    finds = store["findings"]
    counts_txt = _j.dumps(store["scientific_state_counts"], sort_keys=True)
    summ = ["# Assured findings (70 current VERIFIED)", "",
            f"cert: `{store['certification_fingerprint']}`",
            f"store: `{store['store_fingerprint']}`",
            f"counts: `{counts_txt}`", "",
            "## Current findings"]
    for f in finds:
        summ += ["", f"### {f['question_id']} [{f['scientific_state']}]",
                 "", str(f["interpretation"])]
    (out / f"assured_epistemic_findings_summary_{stamp}.md").write_text(
        "\n".join(summ) + "\n", encoding="utf-8")
    head = ("qid", "state", "assurance", "truth", "permitted",
            "schema_gap", "impl_gap")
    rows = ["# 70-row current finding table", "",
            "| " + " | ".join(head) + " |",
            "|" + "|".join("---" for _ in head) + "|"]
    for f in finds:
        c = f["consumption"]
        rows.append("| " + " | ".join([
            str(f["question_id"]), str(f["scientific_state"]),
            str(f["assurance_status"]),
            "YES" if c["scientific_truth_consumable"] else "NO",
            ",".join(c["permitted_consumption"]),
            str(f["schema_gap_id"] or "-"),
            str(f["implementation_gap_id"] or "-")]) + " |")
    rows += ["", f"store: `{store['store_fingerprint']}`", ""]
    (out / f"assured_epistemic_findings_table_{stamp}.md").write_text(
        "\n".join(rows) + "\n", encoding="utf-8")
    amap = ["# Architecture / authority map", "",
            "Gate1 checkpoints -> historical pass (producer)",
            "-> final certification (certifier)",
            "-> assured_epistemic_findings_20260928.json (THIS authority)",
            "",
            "## Paths",
            "- producers: HistoricalResearchPass / FinalAssuranceCert",
            "- authority: control_plane/assured_epistemic_findings.py",
            "- consumers: consume_scientific_truth / gate_q71 / "
            "findings[].consumption",
            "- raw analysis/reports/* NOT authority (bypass rejected)",
            "",
            "## Versioning",
            "- v1 CURRENT x70, supersedes None, superseded 0",
            "- revisions append; history immutable",
            "",
            "## Upstream note",
            "- assurance_consumption_gate lacks IMPLEMENTATION_BLOCKED;",
            "  authority admits it repair-only (no gate file edit).",
            "",
            "## Q71+", "- q71_started False; gated via gate_q71().", ""]
    (out / f"assured_epistemic_findings_authority_map_{stamp}.md"
     ).write_text("\n".join(amap) + "\n", encoding="utf-8")
    cert_rows = {r["question_id"]: r
                 for r in _read_json(CERTIFICATION_PATH)["certifications"]}
    dgaps = _read_json(DATA_GAPS_PATH)["gaps"]
    igaps = _read_json(IMPLEMENTATION_GAPS_PATH)["gaps"]
    sg = ["# Schema-gap linkage (29 HISTORICALLY_UNANSWERABLE)", ""]
    for f in finds:
        if f["scientific_state"] != "HISTORICALLY_UNANSWERABLE":
            continue
        g = next(x for x in dgaps
                 if f["question_id"] in x["affected_question_ids"])
        sg += ["", f"## {f['question_id']} -> {g['gap_id']}", "",
               "```json",
               _j.dumps({"finding_state": f["scientific_state"],
                         "gap": g}, indent=2, sort_keys=True,
                        default=str),
               "```"]
    (out / f"assured_epistemic_findings_schema_gaps_{stamp}.md"
     ).write_text("\n".join(sg) + "\n", encoding="utf-8")
    ig = ["# Implementation-gap linkage (8 IMPLEMENTATION_BLOCKED)", ""]
    for f in finds:
        if f["scientific_state"] != "IMPLEMENTATION_BLOCKED":
            continue
        g = next(x for x in igaps if x["question_id"] == f["question_id"])
        ig += ["", f"## {f['question_id']}", "", "```json",
               _j.dumps({"finding_state": f["scientific_state"],
                         "gap": g}, indent=2, sort_keys=True,
                        default=str), "```"]
    (out / f"assured_epistemic_findings_impl_gaps_{stamp}.md"
     ).write_text("\n".join(ig) + "\n", encoding="utf-8")
    gl = ["# Downstream consumption-gate verification", "",
          f"store: `{store['store_fingerprint']}`", ""]
    ok = leak = 0
    for f in finds:
        d = decide(f["question_id"], f["assurance_status"],
                   f["scientific_state"])
        gl.append(f"- {f['question_id']}: admitted={d.admitted} "
                  f"truth={d.scientific_truth_consumable} "
                  f"perm={','.join(d.permitted_consumption)}")
        if d.scientific_truth_consumable:
            ok += 1
            consume_scientific_truth(f)
        else:
            try:
                consume_scientific_truth(f)
                leak += 1
            except AssuredFindingsError:
                pass
    gl += ["", f"truth={ok} (expect 14); leaks={leak} (expect 0)", ""]
    (out / f"assured_epistemic_findings_gate_check_{stamp}.md"
     ).write_text("\n".join(gl) + "\n", encoding="utf-8")







