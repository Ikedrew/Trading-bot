"""
Cycle Snapshot + Weekly Change Report — "What changed since the previous
completed research cycle?"

Owned by the canonical weekly research cycle (ResearchCycleRunner, invoked by
scripts/run_research_cycle.py). At the end of every SUCCESSFUL research cycle:

    canonical S3 evidence read (existing loaders, read-only)
        ↓
    questions re-evaluated (run_all, Gap-4 authoritative status contract)
        ↓
    snapshot of evidence / questions / findings / hypotheses / candidates
        ↓
    semantic diff against the LAST SUCCESSFUL snapshot
        ↓
    machine-readable change report + human-readable weekly report persisted
        ↓
    latest-success pointer advanced (ONLY on success)

Failure semantics: a failed cycle never produces a snapshot, never advances
the latest-success pointer, and never becomes the comparison baseline. The
next successful cycle compares against the last SUCCESSFUL cycle.

Persistence: logs/research_lifecycle/cycles/ (local research state, same
store as the investigation registry / cycle state / audit log). This does NOT
survive VM loss — durable research-state storage remains Gap 8.

This module is research-only: it NEVER modifies trading configuration and
never promotes candidates (READY_FOR_REVIEW is a notification to a human).

Scheduling owner: the production research VM (EC2/Windows) invokes the
canonical one-shot entry point via Windows Task Scheduler (exact command in
scripts/run_research_cycle.py). Locally the research profile comes from
RESEARCH_AWS_PROFILE; on EC2 no profile is forced (the standard boto3 chain
resolves the IAM instance role). No access keys, no PEM, no hardcoded profile
in research code.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_CYCLES_DIR = Path("logs/research_lifecycle/cycles")
_LATEST_SUCCESS_FILE = _CYCLES_DIR / "latest_success.json"

# Question fields compared for material change (Gap-4 contract fields).
_QUESTION_FIELDS = ("status", "recommendation", "confidence")


# ═══════════════════════════════════════════════════════════════════════════════
# SNAPSHOT BUILDER
# ═══════════════════════════════════════════════════════════════════════════════


def build_cycle_snapshot(*, cycle_id: str, fingerprint: str) -> dict[str, Any]:
    """
    Snapshot the meaningful research state at the end of a successful cycle.

    Reads canonical S3 evidence through the sanctioned data-access layer and
    current research lifecycle stores. Contains NO secrets and NO raw
    production datasets — counts, statuses and identities only.
    """
    from research_engine.experiments.research_runner import run_all
    from research_engine.lifecycle.registry import InvestigationRegistry
    from research_engine.v10.candidates.candidate_registry import CandidateRegistry

    # ─── evidence counts (canonical S3, read-only) ────────────────────────
    from research_engine.data_access.loaders import load_decision_trace, load_trade_truth
    from research_engine.data_access.shadow_runtime_ingestion import (
        ingest_completed_shadow_trades,
    )

    shadows = ingest_completed_shadow_trades()
    truths = load_trade_truth()
    traces = load_decision_trace()

    evidence = {
        "shadow_outcomes": len(shadows),
        "trade_truth": len(truths),
        "decision_trace": len(traces),
        "dataset_fingerprint": fingerprint,
    }

    # ─── question results (Gap-4 authoritative status contract) ───────────
    questions = {}
    for qid, info in run_all().items():
        questions[qid] = {
            "status": info.get("status", "UNKNOWN_STATUS"),
            "recommendation": info.get("recommendation", ""),
            "sample": info.get("sample", 0),
            "status_source": info.get("status_source", ""),
        }

    # ─── hypotheses ────────────────────────────────────────────────────────
    hypotheses = {}
    for h in InvestigationRegistry().all():
        hypotheses[h.hypothesis_id] = {
            "status": h.status.value,
            "conclusion": h.conclusion_type.value if h.conclusion_type else "",
            "source_finding_id": h.source_finding_id,
            "category": h.category.value,
        }

    # ─── candidates (+ Gap-1 prospective pair counts for shadow testing) ──
    candidates = {}
    registry = CandidateRegistry()
    for c in registry.list_all():
        entry = {
            "status": c.status,
            "hypothesis_id": c.hypothesis_id,
            "created_from_question": c.created_from_question,
            "validation_count": len(c.validation_history),
            "latest_verdict": c.validation_history[-1].decision if c.validation_history else "",
        }
        if c.status == "SHADOW_TESTING":
            # Gap-1 pairing contract: matched prospective candidate-shadow ↔
            # incumbent trade_truth pairs. Errors propagate (loud S3 failure).
            from research_engine.lifecycle.candidate_pairing import count_prospective_pairs
            entry["prospective_pairs"] = count_prospective_pairs(c)
        candidates[c.candidate_id] = entry

    # ─── findings / triggers (existing FindingTriggerEngine store) ────────
    findings = _load_trigger_state()

    now = datetime.now(timezone.utc).isoformat()
    return {
        "schema": "research_cycle_snapshot_v1",
        "cycle_id": cycle_id,
        "completed_at": now,
        "evidence": evidence,
        "questions": questions,
        "findings": findings,
        "hypotheses": hypotheses,
        "candidates": candidates,
    }


def _load_trigger_state() -> dict[str, dict[str, Any]]:
    """Load the persisted finding-trigger store (empty when none exist yet)."""
    from research_engine.lifecycle.finding_trigger import _TRIGGER_FILE

    if not _TRIGGER_FILE.exists():
        return {}
    try:
        data = json.loads(_TRIGGER_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    triggers = data.get("triggers", data) if isinstance(data, dict) else {}
    out: dict[str, dict[str, Any]] = {}
    if isinstance(triggers, dict):
        items = triggers.items()
    elif isinstance(triggers, list):
        items = ((t.get("trigger_id", ""), t) for t in triggers)
    else:
        items = ()
    for tid, t in items:
        if not isinstance(t, dict) or not tid:
            continue
        out[tid] = {
            "status": t.get("status", ""),
            "category": str(t.get("category", "")),
            "sample_size": t.get("sample_size", t.get("evidence_n", 0)) or 0,
        }
    return out


# ═══════════════════════════════════════════════════════════════════════════════
# PERSISTENCE (success-only baseline pointer)
# ═══════════════════════════════════════════════════════════════════════════════


def persist_cycle_snapshot(snapshot: dict[str, Any]) -> Path:
    """Persist one successful-cycle snapshot and advance the latest-success
    pointer. Called ONLY for successfully completed cycles."""
    _CYCLES_DIR.mkdir(parents=True, exist_ok=True)
    path = _CYCLES_DIR / f"{snapshot['cycle_id']}_snapshot.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(snapshot, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)

    pointer = {
        "cycle_id": snapshot["cycle_id"],
        "snapshot_file": path.name,
        "completed_at": snapshot["completed_at"],
        "fingerprint": snapshot["evidence"].get("dataset_fingerprint", ""),
    }
    tmp2 = _LATEST_SUCCESS_FILE.with_suffix(".tmp")
    tmp2.write_text(json.dumps(pointer, indent=2), encoding="utf-8")
    tmp2.replace(_LATEST_SUCCESS_FILE)
    return path


def load_latest_successful_snapshot() -> dict[str, Any] | None:
    """Load the last SUCCESSFUL cycle snapshot (comparison baseline), if any."""
    if not _LATEST_SUCCESS_FILE.exists():
        return None
    try:
        pointer = json.loads(_LATEST_SUCCESS_FILE.read_text(encoding="utf-8"))
        path = _CYCLES_DIR / pointer.get("snapshot_file", "")
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, KeyError):
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# SEMANTIC CHANGE REPORT
# ═══════════════════════════════════════════════════════════════════════════════


def diff_snapshots(previous: dict[str, Any] | None,
                   current: dict[str, Any]) -> dict[str, Any]:
    """
    Deterministic semantic diff between two cycle snapshots.

    Compares research-meaningful fields only; ignores timestamps, ordering
    and volatile formatting. The same two snapshots always produce the same
    diff.
    """
    report: dict[str, Any] = {
        "schema": "research_change_report_v1",
        "current_cycle_id": current.get("cycle_id", ""),
        "previous_cycle_id": (previous or {}).get("cycle_id", ""),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "is_baseline": previous is None,
        "evidence_growth": [],
        "question_changes": [],
        "findings_new": [],
        "findings_reconfirmed": [],
        "findings_no_longer_supported": [],
        "hypothesis_transitions": [],
        "candidate_transitions": [],
        "candidate_evidence_growth": [],
        "governance_changes": [],
    }

    if previous is None:
        # First successful cycle: baseline only. Never pretend everything
        # is "new" — nothing existed to compare against.
        report["material_change"] = False
        return report

    prev_evidence = (previous or {}).get("evidence", {})
    curr_evidence = current.get("evidence", {})
    for dataset in sorted(set(curr_evidence) | set(prev_evidence)):
        if dataset == "dataset_fingerprint":
            continue
        before, after = prev_evidence.get(dataset, 0), curr_evidence.get(dataset, 0)
        if after != before:
            report["evidence_growth"].append({
                "dataset": dataset, "previous": before, "current": after,
                "delta": after - before,
            })

    # ─── questions (Gap-4 contract fields) ─────────────────────────────────
    prev_q = (previous or {}).get("questions", {})
    curr_q = current.get("questions", {})
    for qid in sorted(set(curr_q) | set(prev_q)):
        before, after = prev_q.get(qid), curr_q.get(qid)
        if before is None:
            if after is not None:
                report["question_changes"].append({
                    "question_id": qid, "kind": "new_question_result",
                    "status": after.get("status", ""), "sample": after.get("sample", 0),
                })
            continue
        if after is None:
            continue  # question removed from registry — not a research change
        changes = []
        for field in _QUESTION_FIELDS:
            b, a = before.get(field, ""), after.get(field, "")
            if b != a:
                changes.append({"field": field, "previous": b, "current": a})
        b_sample, a_sample = before.get("sample", 0), after.get("sample", 0)
        if isinstance(a_sample, int) and isinstance(b_sample, int) and a_sample > b_sample:
            changes.append({"field": "sample", "previous": b_sample, "current": a_sample})
        if changes:
            report["question_changes"].append({
                "question_id": qid, "kind": "question_changed", "changes": changes,
            })

    # ─── findings ───────────────────────────────────────────────────────────
    prev_f = (previous or {}).get("findings", {})
    curr_f = current.get("findings", {})
    for fid in sorted(set(curr_f) - set(prev_f)):
        report["findings_new"].append({"finding_id": fid, **curr_f[fid]})
    for fid in sorted(set(curr_f) & set(prev_f)):
        b, a = prev_f[fid], curr_f[fid]
        if a.get("sample_size", 0) > b.get("sample_size", 0):
            report["findings_reconfirmed"].append({
                "finding_id": fid,
                "sample_previous": b.get("sample_size", 0),
                "sample_current": a.get("sample_size", 0),
            })
        elif a.get("status") != b.get("status"):
            report["findings_reconfirmed"].append({
                "finding_id": fid, "status_previous": b.get("status"),
                "status_current": a.get("status"),
            })
    for fid in sorted(set(prev_f) - set(curr_f)):
        # Never silently erased — explicitly reported as no longer supported.
        report["findings_no_longer_supported"].append({
            "finding_id": fid, **prev_f[fid],
        })

    # ─── hypotheses ─────────────────────────────────────────────────────────
    prev_h = (previous or {}).get("hypotheses", {})
    curr_h = current.get("hypotheses", {})
    for hid in sorted(set(curr_h) | set(prev_h)):
        b, a = prev_h.get(hid), curr_h.get(hid)
        if b is None and a is not None:
            report["hypothesis_transitions"].append({
                "hypothesis_id": hid, "transition": "NEW",
                "status": a.get("status", ""),
                "source_finding_id": a.get("source_finding_id", ""),
            })
        elif b is not None and a is not None and b.get("status") != a.get("status"):
            report["hypothesis_transitions"].append({
                "hypothesis_id": hid,
                "transition": f"{b.get('status', '')} -> {a.get('status', '')}",
                "conclusion": a.get("conclusion", ""),
            })

    # ─── candidates ─────────────────────────────────────────────────────────
    prev_c = (previous or {}).get("candidates", {})
    curr_c = current.get("candidates", {})
    for cid in sorted(set(curr_c) | set(prev_c)):
        b, a = prev_c.get(cid), curr_c.get(cid)
        if b is None and a is not None:
            report["candidate_transitions"].append({
                "candidate_id": cid, "transition": f"NEW -> {a.get('status', '')}",
            })
            continue
        if b is None or a is None:
            continue
        if b.get("status") != a.get("status"):
            report["candidate_transitions"].append({
                "candidate_id": cid,
                "transition": f"{b.get('status', '')} -> {a.get('status', '')}",
            })
            if a.get("status") == "READY_FOR_REVIEW":
                report["governance_changes"].append({
                    "candidate_id": cid,
                    "note": "candidate became READY_FOR_REVIEW - HUMAN DECISION REQUIRED",
                })
        b_pairs, a_pairs = b.get("prospective_pairs"), a.get("prospective_pairs")
        if isinstance(a_pairs, int) and isinstance(b_pairs, int) and a_pairs != b_pairs:
            entry = {"candidate_id": cid, "pairs_previous": b_pairs, "pairs_current": a_pairs}
            if a.get("latest_verdict") != b.get("latest_verdict"):
                entry["verdict"] = f"{b.get('latest_verdict', '')} -> {a.get('latest_verdict', '')}"
            report["candidate_evidence_growth"].append(entry)

    report["material_change"] = bool(
        report["evidence_growth"] or report["question_changes"]
        or report["findings_new"] or report["findings_reconfirmed"]
        or report["findings_no_longer_supported"]
        or report["hypothesis_transitions"] or report["candidate_transitions"]
        or report["candidate_evidence_growth"] or report["governance_changes"]
    )
    return report


def format_change_report(report: dict[str, Any]) -> str:
    """Concise human-readable weekly change report (ASCII-safe rendering)."""
    lines: list[str] = []
    add = lines.append
    add("RESEARCH WEEKLY CHANGE REPORT")
    add("=" * 60)
    add(f"Cycle:    {report.get('current_cycle_id', '')}")
    add(f"Previous: {report.get('previous_cycle_id', '') or '(none - baseline)'}")
    add("")

    if report.get("is_baseline"):
        add("BASELINE RESEARCH SNAPSHOT CREATED")
        add("No previous successful research cycle exists for comparison.")
        add("The next successful cycle will produce the first change report.")
        add("")
    elif not report.get("material_change"):
        add("NO MATERIAL RESEARCH CHANGE")
        add("")
    else:
        if report["evidence_growth"]:
            add("EVIDENCE GROWTH")
            for e in report["evidence_growth"]:
                add(f"  {e['dataset']}: {e['previous']} -> {e['current']} ({e['delta']:+d})")
            add("")
        if report["question_changes"]:
            add("QUESTION CHANGES")
            for q in report["question_changes"]:
                if q["kind"] == "new_question_result":
                    add(f"  {q['question_id']}: new result status={q['status']} n={q['sample']}")
                else:
                    for c in q["changes"]:
                        add(f"  {q['question_id']}: {c['field']} {c['previous']} -> {c['current']}")
            add("")
        if report["findings_new"]:
            add("NEW FINDINGS")
            for f in report["findings_new"]:
                add(f"  {f['finding_id']}: status={f.get('status', '')} n={f.get('sample_size', 0)}")
            add("")
        if report["findings_reconfirmed"]:
            add("RECONFIRMED FINDINGS")
            for f in report["findings_reconfirmed"]:
                if "sample_current" in f:
                    add(f"  {f['finding_id']}: evidence n={f['sample_previous']} -> {f['sample_current']}")
                else:
                    add(f"  {f['finding_id']}: status {f.get('status_previous')} -> {f.get('status_current')}")
            add("")
        if report["findings_no_longer_supported"]:
            add("FINDINGS NO LONGER SUPPORTED (history preserved)")
            for f in report["findings_no_longer_supported"]:
                add(f"  {f['finding_id']}: status={f.get('status', '')}")
            add("")
        if report["hypothesis_transitions"]:
            add("HYPOTHESIS TRANSITIONS")
            for h in report["hypothesis_transitions"]:
                add(f"  {h['hypothesis_id']}: {h['transition']}")
            add("")
        if report["candidate_transitions"]:
            add("CANDIDATE TRANSITIONS")
            for c in report["candidate_transitions"]:
                add(f"  {c['candidate_id']}: {c['transition']}")
            add("")
        if report["candidate_evidence_growth"]:
            add("CANDIDATE EVIDENCE GROWTH")
            for c in report["candidate_evidence_growth"]:
                extra = f" | verdict {c['verdict']}" if "verdict" in c else ""
                add(f"  {c['candidate_id']}: pairs {c['pairs_previous']} -> {c['pairs_current']}{extra}")
            add("")
        if report["governance_changes"]:
            add("CANDIDATES READY FOR HUMAN REVIEW")
            for g in report["governance_changes"]:
                add(f"  {g['candidate_id']}: {g['note']}")
            add("")

    add("SUMMARY")
    if report.get("is_baseline"):
        add("  Baseline created; no comparison performed.")
    elif not report.get("material_change"):
        add("  NO MATERIAL RESEARCH CHANGE since the previous successful cycle.")
    else:
        add(f"  evidence series changed: {len(report['evidence_growth'])}")
        add(f"  questions changed:       {len(report['question_changes'])}")
        add(f"  hypothesis transitions:  {len(report['hypothesis_transitions'])}")
        add(f"  candidate transitions:   {len(report['candidate_transitions'])}")
        add("  All changes remain research-only; human approval is required for "
            "any production consideration.")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# CYCLE RECORDING (called by ResearchCycleRunner on success)
# ═══════════════════════════════════════════════════════════════════════════════


def record_cycle(*, cycle_id: str, fingerprint: str) -> tuple[dict[str, Any], dict[str, Any], str]:
    """
    Build the snapshot, diff against the last SUCCESSFUL cycle, persist both
    the snapshot and the change report, and advance the latest-success pointer.

    Returns (snapshot, change_report, change_kind) where change_kind is one of
    "baseline" | "no_material_change" | "material_change".
    """
    snapshot = build_cycle_snapshot(cycle_id=cycle_id, fingerprint=fingerprint)
    previous = load_latest_successful_snapshot()
    report = diff_snapshots(previous, snapshot)

    snapshot_path = persist_cycle_snapshot(snapshot)
    snapshot["snapshot_file"] = str(snapshot_path)

    change_kind = "baseline" if report["is_baseline"] else (
        "material_change" if report["material_change"] else "no_material_change"
    )
    report["snapshot_file"] = str(snapshot_path)
    report_path = _CYCLES_DIR / f"{cycle_id}_change_report.json"
    tmp = report_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    tmp.replace(report_path)
    (report_path.parent / f"{cycle_id}_change_report.txt").write_text(
        format_change_report(report), encoding="utf-8")

    logger.info(
        "[CYCLE_SNAPSHOT] cycle=%s kind=%s snapshot=%s report=%s",
        cycle_id, change_kind, snapshot_path.name, report_path.name,
    )
    return snapshot, report, change_kind






