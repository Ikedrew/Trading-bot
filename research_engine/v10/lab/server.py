"""Local Research Lab server (stdlib only, loopback only).

Every /api/state request re-reads persisted state from disk. The browser never
holds authoritative state, and refreshing never runs a research cycle. Only an
explicit POST /api/run invokes the existing continuous research loop.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.request import urlopen

REPO = Path(__file__).resolve().parents[3]
STATIC = Path(__file__).with_name("static")
STATE_ROOT = REPO / "data" / "research" / "continuous"
PRODUCTION_COVERAGE_DIR = STATE_ROOT / "production_coverage"
OBSERVATION_SPACE_DIR = STATE_ROOT / "observation_space"
NO_PRODUCTION_OBSERVATION_SPACE = "NO_PRODUCTION_OBSERVATION_SPACE"
OPT_REGISTRY = REPO / "data" / "research" / "optimisation" / "registry.json"
LEGACY_COCKPIT = REPO / "reports" / "research" / "cockpit.html"
LOCK_PATH = STATE_ROOT / "lab_run.lock"
LAST_RUN_PATH = STATE_ROOT / "lab_last_run.json"
PROGRESS_PATH = STATE_ROOT / "cycle_progress.json"
# Repair Block 3: canonical candidate lifecycle authorities (read-only).
CANDIDATE_REGISTRY_DIR = REPO / "data" / "research" / "candidates"
OPTIMISATION_REGISTRY_DIR = REPO / "data" / "research" / "optimisation"
GOVERNANCE_DIR = REPO / "data" / "research" / "governance"
HOST, PORT = "127.0.0.1", 8765
APP_ID = "research-lab-v1"
READ_ONLY = False
HISTORICAL_ARCHIVE = REPO / "reports/research_lab_archive"

_run_guard = threading.Lock()
_run: dict[str, Any] = {"status": "IDLE"}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _mtime_iso(path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except OSError:
        return None


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    code = ctypes.c_ulong()
    ok = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
    ctypes.windll.kernel32.CloseHandle(handle)
    return bool(ok) and code.value == 259


# ── Failure translation ──────────────────────────────────────────────────────
_STAGE_LABEL = {
    "FRONTIER": "Fetching and freezing the latest data",
    "QUESTIONS": "Re-evaluating the 70 canonical questions",
    "SCIENTIFIC_STATE": "Updating findings, hypotheses and candidates",
    "Q71_PLUS": "Generating Q71+ research questions",
    "Q71_EXECUTION": "Executing generated research questions",
    "VALIDATION_QUEUE": "Processing the validation queue",
    "PROJECTION": "Publishing the research projection",
}


def humanise_failure(stage: str | None, reason: str | None) -> dict[str, Any]:
    text = reason or ""
    low = text.lower()
    if ("token" in low and ("expired" in low or "sso" in low)) or "tokenretrievalerror" in low:
        title = "Your AWS login has expired"
        message = ("AWS authentication has expired. Governed S3 research evidence could not be "
                   "accessed, so no downstream scientific stages were run with stale or "
                   "unverified evidence. Sign in to AWS externally, then retry the cycle. The "
                   "last successful research state remains visible.")
        kind = "AWS_AUTH_EXPIRED"
    elif "nosuchbucket" in low or "accessdenied" in low or "aws failure" in low:
        title = "AWS data could not be reached"
        message = ("The data store did not answer. Check your connection and AWS access, then try "
                   "again. The last successful research state is still shown.")
        kind = "AWS_UNAVAILABLE"
    else:
        title = "The research cycle did not finish"
        message = ("A stage failed before the cycle could complete. The last successful research "
                   "state is still shown; expand the technical details for the exact error.")
        kind = "CYCLE_FAILED"
    return {"kind": kind, "title": title, "message": message, "failed_stage": stage,
            "failed_stage_label": _STAGE_LABEL.get(stage or "", stage), "technical": text}


# ── State assembly ───────────────────────────────────────────────────────────
def _registry_questions() -> list[dict[str, Any]]:
    from research_engine.v10.continuous.canonical_question_cycle import _load_registry
    rows = []
    for q in _load_registry():
        if q.scientific_owner_id:
            kind, note = "ALIAS_OR_SUPERSEDED", f"Alias of / superseded by {q.scientific_owner_id}"
        elif not q.runner_module:
            kind, note = "UNIMPLEMENTED", "No runner implemented for this question yet"
        else:
            kind, note = "NOT_YET_EVALUATED", "Awaiting the next successful cycle"
        rows.append({
            "question_id": q.id, "title": q.title, "description": q.description,
            "category": getattr(q.category, "value", str(q.category)),
            "priority": getattr(q.priority, "value", str(q.priority)),
            "depends_on": list(q.depends_on), "legacy_ids": list(q.legacy_ids),
            "runner": q.runner_module or None, "superseded_by": q.scientific_owner_id or None,
            "registry_status": kind, "registry_note": note,
            "status": kind, "status_source": "registry",
        })
    return rows


def _merge_questions(base: list[dict[str, Any]], overlay: dict[str, Any]) -> list[dict[str, Any]]:
    for row in base:
        live = overlay.get(row["question_id"])
        if not live:
            continue
        # Block 2 rows nest the governed result under "result"; flat rows are accepted too.
        result = live.get("result") if isinstance(live.get("result"), dict) else {}
        flat = {k: v for k, v in live.items() if k not in ("result", "definition")}
        flat.update(result)
        status = flat.get("status")
        if row["registry_status"] in ("ALIAS_OR_SUPERSEDED", "UNIMPLEMENTED") and status in (None, ""):
            status = row["registry_status"]
        row.update({k: v for k, v in flat.items() if k not in ("question_id", "title", "description")})
        row["runner_failed"] = bool(live.get("runner_failed"))
        row["status"] = status or row["registry_status"]
        row["status_source"] = "block2_projection"
    return base


def _annotate_evaluation_currency(rows: list[dict[str, Any]]) -> None:
    """Expose whether each projected result matches current evaluator authority."""
    try:
        from research_engine.registry.definition_validator import build_definitions_from_registry
        from research_engine.v10.continuous.canonical_question_cycle import (
            REGISTRY_VERSION, _load_registry,
        )
        from research_engine.v10.continuous.evaluation_identity import (
            evaluation_identities, is_result_current,
        )
        questions = _load_registry()
        identities = evaluation_identities(
            questions, build_definitions_from_registry(questions),
            registry_version=REGISTRY_VERSION)
    except Exception:
        for row in rows:
            row["evaluation_currency"] = "UNKNOWN"
        return
    for row in rows:
        current = identities.get(str(row.get("question_id")))
        if current is None or row.get("status_source") == "registry":
            row["evaluation_currency"] = "NOT_EVALUATED"
            continue
        is_current, reason = is_result_current(row.get("evaluation_identity"), current)
        row["evaluation_currency"] = "CURRENT" if is_current else "STALE"
        row["evaluation_stale_reason"] = reason
        row["current_evaluation_identity_digest"] = current.get(
            "evaluation_identity_digest")


def _bound_evidence_references(rows: list[dict[str, Any]], limit: int = 25) -> None:
    """Keep the operator API bounded while declaring omitted projection detail."""
    for row in rows:
        references = row.get("evidence_references")
        if not isinstance(references, list):
            continue
        row["evidence_reference_count"] = len(references)
        if len(references) > limit:
            row["evidence_references"] = references[:limit]
            row["evidence_references_truncated"] = True
            row["evidence_references_shown"] = limit
        else:
            row["evidence_references_truncated"] = False
            row["evidence_references_shown"] = len(references)

        truncation: dict[str, dict[str, int]] = {}

        def bounded(value: Any, path: str) -> Any:
            if isinstance(value, list):
                if len(value) > limit:
                    truncation[path] = {"total": len(value), "shown": limit}
                return [bounded(item, f"{path}[{index}]")
                        for index, item in enumerate(value[:limit])]
            if isinstance(value, dict):
                return {key: bounded(item, f"{path}.{key}" if path else key)
                        for key, item in value.items()}
            return value

        for field, value in list(row.items()):
            if field != "evidence_references":
                row[field] = bounded(value, field)
        if truncation:
            row["operator_view_truncation"] = truncation


def _cycles() -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    history = STATE_ROOT / "cycles" / "history"
    rows: list[dict[str, Any]] = []
    for path in history.glob("*.json") if history.exists() else []:
        try:
            row = _read_json(path)
        except (OSError, ValueError):
            continue
        # imported_at: when THIS Lab wrote the local copy (file mtime).
        # completed_at: the production cycle's real completion time, read from
        # the record itself.  recorded_at remains the ordering key but now
        # prefers the real completion time so the operator timeline reflects
        # research reality, not the local import moment.
        row["imported_at"] = _mtime_iso(path)
        row["completed_at"] = row.get("completed_at")
        row["recorded_at"] = row.get("completed_at") or row["imported_at"]
        row["failure"] = (humanise_failure(row.get("failure_stage"), row.get("failure_reason"))
                          if row.get("cycle_outcome") == "FAILED" else None)
        rows.append(row)
    rows.sort(key=lambda r: r.get("recorded_at") or "", reverse=True)
    completed_pointer = STATE_ROOT / "cycles" / "latest_completed.json"
    pointer = (completed_pointer if completed_pointer.exists()
               else STATE_ROOT / "cycles" / "latest_success.json")
    success = None
    if pointer.exists():
        try:
            value = _read_json(pointer)
            success = value if value.get("cycle_outcome") == "COMPLETED" else None
        except (OSError, ValueError):
            success = None
    return rows, success


def _fallback_from_optimisation() -> dict[str, Any]:
    """Block 3-4 persisted registry, used until a unified projection exists."""
    if not OPT_REGISTRY.exists():
        return {"hypotheses": [], "candidates": [], "findings": [], "plans": {}}
    reg = _read_json(OPT_REGISTRY)
    plans = reg.get("plans", {})
    hyps = []
    for hid, h in sorted(reg.get("hypotheses", {}).items()):
        row = dict(h)
        row["dependent_candidates"] = sorted(
            cid for cid, c in reg.get("candidates", {}).items() if c.get("hypothesis_id") == hid)
        hyps.append(row)
    cands = []
    for cid, c in sorted(reg.get("candidates", {}).items()):
        row = dict(c)
        hyp = reg.get("hypotheses", {}).get(c.get("hypothesis_id"), {})
        row["source_finding_ids"] = [hyp["source_finding"]] if hyp.get("source_finding") else []
        row["source_question_ids"] = [hyp["source_question"]] if hyp.get("source_question") else []
        row["live_approved"] = bool((c.get("shadow_binding") or {}).get("live_approved", False))
        row["validation_queue"], row["shadow_evidence"] = [], {}
        cands.append(row)
    findings = []
    for fid in sorted({h.get("source_finding") for h in hyps if h.get("source_finding")}):
        findings.append({
            "finding_id": fid, "status": "REFERENCED_BY_HYPOTHESIS",
            "summary": "Referenced as the source of hypotheses; the scientific-state store has no "
                       "persisted finding record yet.",
            "dependent_hypotheses": [h["hypothesis_id"] for h in hyps if h.get("source_finding") == fid],
            "dependent_candidates": [c["candidate_id"] for c in cands if fid in c["source_finding_ids"]],
        })
    return {"hypotheses": hyps, "candidates": cands, "findings": findings, "plans": plans}


def _candidate_lifecycle_state() -> dict[str, Any]:
    """Repair Block 3 — exact authoritative candidate lifecycle for the Lab.

    Reads ONLY the governed authorities (production authority ledger,
    application ledger, human-decision store, candidate lifecycle ledger) plus
    the compatibility registries, and projects them onto ONE canonical
    vocabulary.  Failure is reported, never invented.
    """
    try:
        from research_engine.control_plane.candidate_lifecycle_service import (
            build_candidate_lifecycle_projection,
        )

        projection = build_candidate_lifecycle_projection(
            registry_dir=str(CANDIDATE_REGISTRY_DIR),
            optimisation_registry_dir=str(OPTIMISATION_REGISTRY_DIR),
            application_path=GOVERNANCE_DIR / "application.jsonl",
            authority_path=GOVERNANCE_DIR / "production_authority.jsonl",
            decisions_dir=str(CANDIDATE_REGISTRY_DIR),
            validation_queue_path=STATE_ROOT / "validation_queue.json",
            lifecycle_ledger_path=GOVERNANCE_DIR / "candidate_lifecycle.jsonl",
        )
    except Exception as exc:
        return {"schema_version": "research_lab_candidate_lifecycle_v1",
                "available": False, "error": f"{type(exc).__name__}:{exc}",
                "candidates": [], "review_ready": [], "runtime_effective": [],
                "action_required": [], "disagreements": [], "state_counts": {}}
    projection["available"] = True
    return projection


def _status_events(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events = []
    for c in candidates:
        for step in c.get("status_history", []) or []:
            events.append({"time": step.get("timestamp"), "kind": "CANDIDATE_STATUS",
                           "subject": c["candidate_id"], "title": f"{c['candidate_id']} → {step.get('status')}",
                           "detail": step.get("evidence") or ""})
    return events


def _projection_events(projection: dict[str, Any] | None, questions: list[dict[str, Any]],
                       recorded_at: str | None) -> list[dict[str, Any]]:
    if not projection:
        return []
    events: list[dict[str, Any]] = []
    frontier = projection.get("data_frontier") or {}
    snapshot_id = frontier.get("snapshot_id")
    if snapshot_id:
        events.append({
            "time": recorded_at, "kind": "SNAPSHOT", "subject": snapshot_id,
            "title": f"Research snapshot recorded: {snapshot_id}",
            "detail": f"Frontier {frontier.get('frontier_start') or '—'} → "
                      f"{frontier.get('frontier_end') or '—'}; "
                      f"status {frontier.get('status') or 'unknown'}.",
        })
    evaluated = [q for q in questions if q.get("evaluated_at")]
    if evaluated:
        events.append({
            "time": max((q["evaluated_at"] for q in evaluated), default=recorded_at),
            "kind": "QUESTION_EVALUATION", "subject": f"{len(evaluated)} questions",
            "title": f"{len(evaluated)} canonical questions evaluated",
            "detail": f"Recorded evaluations for {len(evaluated)} of {len(questions)} canonical questions.",
        })

    labels = {
        "new_answers": "Question changed",
        "newly_sufficient_questions": "Question became answerable",
        "new_findings": "Finding created or strengthened",
        "weakened_findings": "Finding weakened",
        "new_hypotheses": "Hypothesis created",
        "invalidated_hypotheses": "Hypothesis invalidated",
        "proposed_candidates": "Candidate proposed",
        "validation_transitions": "Validation transition",
        "shadow_transitions": "Shadow transition",
        "new_q71_questions": "Q71+ question created",
        "retired_questions": "Q71+ question retired",
        "new_blockers": "Research blocker recorded",
    }
    id_fields = ("question_id", "finding_id", "hypothesis_id", "candidate_id",
                 "generated_question_id", "job_id", "id")
    what_changed = projection.get("what_changed") or {}
    for key, label in labels.items():
        for item in what_changed.get(key, []) or []:
            if isinstance(item, dict):
                subject = next((item[field] for field in id_fields if item.get(field)), None)
                status = item.get("status")
                if status:
                    subject = f"{subject or label} → {status}"
                detail = item.get("summary") or item.get("reason") or item.get("evidence") or ""
                event_time = item.get("timestamp") or item.get("created_at") or recorded_at
            else:
                subject, detail, event_time = str(item), "", recorded_at
            title = f"{label}: {subject}" if subject else label
            events.append({"time": event_time, "kind": key.upper(), "subject": subject or "",
                           "title": title, "detail": str(detail)})
    return events


def _local_scientific_records() -> tuple[list, list]:
    """Read existing registries verbatim; never create findings from references."""
    import hashlib
    hypotheses, candidates = [], []
    seen = {}
    def accept(row, path, identity, collection):
        if not isinstance(row, dict) or not row.get(identity) or not row.get("status"):
            raise ValueError("LOCAL_RECORD_IDENTITY_OR_STATUS_MISSING:" + str(path))
        key = (identity, row[identity])
        if key in seen:
            if seen[key] != row:
                raise ValueError("LOCAL_RECORD_IDENTITY_CONFLICT:" + row[identity])
            return
        seen[key] = dict(row)
        item = dict(row)
        item["display_provenance"] = {
            "path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "scope": "SEPARATE_LOCAL_PERSISTED_REGISTRY",
            "belongs_to_displayed_question_cycle": False,
            "live_currentness_verified": False,
        }
        collection.append(item)
    if OPT_REGISTRY.exists():
        reg = _read_json(OPT_REGISTRY)
        for identity, rows, collection in (
                ("hypothesis_id", reg["hypotheses"], hypotheses),
                ("candidate_id", reg["candidates"], candidates)):
            for key, row in rows.items():
                if row.get(identity) != key:
                    raise ValueError("LOCAL_REGISTRY_KEY_MISMATCH:" + key)
                accept(row, OPT_REGISTRY, identity, collection)
        for candidate in candidates:
            candidate["plan"] = next((p for p in reg.get("plans", {}).values()
                                      if p.get("candidate_id") == candidate["candidate_id"]), {})
    path = CANDIDATE_REGISTRY_DIR / "candidates.jsonl"
    if path.exists():
        with path.open(encoding="utf-8-sig") as stream:
            for line in stream:
                if line.strip():
                    accept(json.loads(line), path, "candidate_id", candidates)
    return hypotheses, candidates


def _completed_read_only_state() -> dict[str, Any]:
    """Adapt verified existing historical artifacts to the ORIGINAL Lab API.

    No unified projection is fabricated, persisted, or substituted. Local
    registry records retain separate provenance and are not part of that cycle.
    """
    from research_engine.v10.continuous.research_lab_archive import (
        load_archive, verified_file,
    )
    directory, catalog, summary, _ = load_archive(HISTORICAL_ARCHIVE)
    def fields(entity):
        row = {}
        omitted = {}
        for descriptor in entity["fields"]:
            name = descriptor["field"]
            name = name[7:] if name.startswith("result.") else name
            info = catalog["files"][descriptor["path"]]
            # Large original collections remain available through explicit
            # verified downloads. Never read megabytes merely to render a card.
            if name == "evidence_references" or info["bytes"] > 256 * 1024:
                omitted[name] = {"total": descriptor.get("count"),
                                 "bytes": info["bytes"], "download": "/api/artifact?path=" + descriptor["path"]}
                continue
            row[name] = _read_json(verified_file(directory, catalog, descriptor["path"]))
        row["operator_view_omitted_fields"] = omitted
        row["display_provenance"] = {
            "scope": "VERIFIED_COMPLETED_TRANSFER", "archive_id": summary["archive_id"],
            "source_cycle_id": summary["cycle_id"], "bridge_run_id": summary["bridge_run_id"],
            "completed_at": summary["completed_at"], "live_currentness_verified": False}
        return row
    questions = []
    for item in summary["questions"]:
        row = fields(item)
        definition = row.pop("definition", {}) or {}
        row.update({key: definition[key] for key in ("title", "description", "category", "priority", "depends_on", "legacy_ids") if key in definition})
        row.setdefault("title", item["title"])
        row["question_id"] = item["question_id"]
        row["status_source"] = "block2_projection"
        row["registry_status"] = "PERSISTED_RESULT"
        row["evaluation_currency"] = "NOT_CHECKED_OFFLINE"
        evidence = row["operator_view_omitted_fields"].get("evidence_references", {})
        row["evidence_reference_count"] = evidence.get("total", 0)
        row["evidence_references_shown"] = 0
        row["evidence_references_truncated"] = bool(evidence.get("total"))
        row["evidence_references"] = []
        questions.append(row)
    findings = [fields(x) for x in summary["entities"]["finding"]]
    hypotheses = [fields(x) for x in summary["entities"]["hypothesis"]]
    candidates = [fields(x) for x in summary["entities"]["candidate"]]
    local_hypotheses, local_candidates = _local_scientific_records()
    def append_distinct(target, additions, identity):
        existing = {r[identity]: r for r in target}
        for row in additions:
            if row[identity] in existing:
                original = existing[row[identity]]
                keys = set(row) - {"display_provenance", "plan"}
                if any(original.get(k) != row[k] for k in keys):
                    raise ValueError("TRANSFER_LOCAL_RECORD_CONFLICT:" + row[identity])
            else:
                target.append(row); existing[row[identity]] = row
    append_distinct(hypotheses, local_hypotheses, "hypothesis_id")
    append_distinct(candidates, local_candidates, "candidate_id")
    hyp_by_id = {h["hypothesis_id"]: h for h in hypotheses}
    finding_by_id = {f["finding_id"]: f for f in findings}
    for h in hypotheses:
        h["dependent_candidates"] = [c["candidate_id"] for c in candidates if c.get("hypothesis_id") == h["hypothesis_id"]]
    for f in findings:
        f["summary"] = f.get("proposition")
        f["dependent_hypotheses"] = [h["hypothesis_id"] for h in hypotheses if h.get("source_finding") == f["finding_id"]]
        f["dependent_candidates"] = [c["candidate_id"] for c in candidates if c.get("hypothesis_id") in f["dependent_hypotheses"]]
    for c in candidates:
        h = hyp_by_id.get(c.get("hypothesis_id"), {})
        fid = h.get("source_finding") or (c.get("change_definition") or {}).get("source_finding_id")
        c["source_finding_ids"] = [fid] if fid else []
        c["source_question_ids"] = [h["source_question"]] if h.get("source_question") else []
        c["lineage_gaps"] = []
        if not h:
            c["lineage_gaps"].append("Persisted hypothesis missing: " + str(c.get("hypothesis_id")))
        if fid and fid not in finding_by_id:
            c["lineage_gaps"].append("Persisted finding missing: " + fid)
        c["runtime_authority"] = "NOT_VERIFIED_OFFLINE"
        c["human_approval_required"] = True
        c["decision_boundary"] = "Record existence is not validation, profitability, test eligibility or live authorization. Review evidence and governed validation plan."
    _bound_evidence_references(questions)
    for row in questions:
        evidence = row["operator_view_omitted_fields"].get("evidence_references", {})
        row["evidence_reference_count"] = evidence.get("total", 0)
        row["evidence_references_shown"] = 0
        row["evidence_references_truncated"] = bool(evidence.get("total"))
    _bound_evidence_references(findings)
    counts = {}
    for q in questions:
        counts[q["status"]] = counts.get(q["status"], 0) + 1
    return {
        "generated_at": _now(), "source": "VERIFIED_COMPLETED_TRANSFER_WITH_SEPARATE_LOCAL_REGISTRIES",
        "read_only": True, "errors": [],
        "source_notice": "Latest locally imported completed cycle; separate local registry records retain their own dates and provenance. Live production freshness is NOT verified.",
        "freshness": {"projection_version": None, "projection_persisted_at": None,
                      "last_attempt_at": None, "last_attempt_outcome": None,
                      "last_success": {"completed_at": summary["completed_at"]},
                      "last_successful_cycle_id": summary["cycle_id"],
                      "historical_completed_at": summary["completed_at"], "research_lag": {}},
        "question_counts": counts, "questions": questions,
        "findings": findings, "hypotheses": hypotheses, "candidates": candidates,
        "candidate_lifecycle": {"status": "NOT_VERIFIED_OFFLINE", "candidates": []},
        "q71": {"generated": [], "queue": [], "available": False},
        "investigations": [{"id": q["question_id"], "type": "CANONICAL_QUESTION",
                            "status": q["status"], "state": q.get("work_state") or q["status"],
                            "sample_n": q.get("sample_n"), "minimum_sample": q.get("minimum_required_n"),
                            "note": q.get("failure_reason") or "; ".join(q.get("missing_evidence") or [])}
                           for q in questions if q["status"] not in ("COMPLETE", "NEGATIVE_RESULT", "ALIAS_OR_SUPERSEDED")],
        "work_queues": {"review_required": summary["review_required"],
                        "candidate_design_required": summary["candidate_design_required"],
                        "validation_queue_available": False},
        "data_frontier": {"snapshot_id": summary["snapshot_id"], "status": "HISTORICAL_OFFLINE_COPY",
                           "frontier_start": None, "frontier_end": None},
        "what_changed_cycle": None, "changes": [], "cycles": [], "last_run": None,
        "latest_failure": None, "run": {"status": "READ_ONLY", "read_only": True},
        "legacy_diagnostics": {"available": False},
        "invariants": {"live_approved_any": False, "live_authority_verified": False,
                       "canonical_question_count": len(questions)},
        "completed_transfer": {k: summary[k] for k in ("cycle_id", "snapshot_id", "bridge_run_id", "completed_at", "verification")},
    }

def _parse_iso(value: Any) -> "datetime | None":
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _elapsed_human(seconds: float) -> str:
    if seconds < 0:
        return "in the future (clock skew)"
    if seconds >= 86400:
        return f"{seconds / 86400:.1f}d"
    if seconds >= 3600:
        return f"{seconds / 3600:.1f}h"
    if seconds >= 60:
        return f"{seconds / 60:.0f}m"
    return f"{seconds:.0f}s"


def _freshness_block(*, projection, cycles, success, projection_imported_at,
                     research_lag, bridge_status) -> dict[str, Any]:
    """Compose freshness that NEVER conflates distinct times.

    Separately reports: the real production research completion time, the
    projection publication time (where independently evidenced), the local
    import/sync time, the last attempt + its real outcome, the last successful
    cycle, and the elapsed time since GENUINE research completion.
    """
    marker = {}
    try:
        marker_path = STATE_ROOT / "continuous_bridge_state.json"
        if marker_path.exists():
            marker = _read_json(marker_path) or {}
    except (OSError, ValueError):
        marker = {}

    latest = cycles[0] if cycles else None
    # Production's latest ATTEMPT (may be FAILED/in-progress) is surfaced by the
    # bridge as a read-only diagnostic — it is never imported.  Prefer it for
    # the "last attempt + outcome" display so the Lab reflects production
    # reality, not just the locally-imported successful cycles.
    prod_attempt = (bridge_status or {}).get("latest_attempt") or {}
    # Real completion of the latest SUCCESSFUL cycle (authoritative research
    # time) comes from the success pointer / bridge marker, never a file mtime.
    production_completed_at = (
        (success or {}).get("completed_at")
        or marker.get("production_completed_at"))
    completed_dt = _parse_iso(production_completed_at)
    seconds_since_completion = (
        (datetime.now(timezone.utc) - completed_dt).total_seconds()
        if completed_dt else None)

    return {
        "projection_version": (projection or {}).get("projection_version"),
        # Distinct, explicitly-labelled timestamps.
        "production_cycle_completed_at": production_completed_at,
        "projection_published_at": marker.get("projection_published_at"),
        "projection_imported_at": projection_imported_at,
        "synced_at": marker.get("imported_at") or projection_imported_at,
        "seconds_since_completion": seconds_since_completion,
        "time_since_completion": (
            _elapsed_human(seconds_since_completion)
            if seconds_since_completion is not None else "unknown"),
        # Last attempt: prefer production's real latest attempt (incl. a FAILED
        # one that was never imported); fall back to the locally-imported cycle.
        "last_attempt_cycle_id": (
            prod_attempt.get("continuous_cycle_id")
            or (latest or {}).get("continuous_cycle_id")),
        "last_attempt_completed_at": (latest or {}).get("completed_at"),
        "last_attempt_observed_at": (
            prod_attempt.get("observed_at") or (latest or {}).get("imported_at")),
        "last_attempt_outcome": (
            prod_attempt.get("cycle_outcome")
            or (latest or {}).get("cycle_outcome")),
        "last_attempt_failure_stage": prod_attempt.get("failure_stage"),
        "last_attempt_failure_reason": prod_attempt.get("failure_reason"),
        "last_attempt_imported": prod_attempt.get("imported", None),
        "production_latest_attempt": prod_attempt or None,
        "last_success": success,
        "last_successful_cycle_id": (success or {}).get("continuous_cycle_id"),
        # Deprecated alias retained for any older client; now points at the
        # real completion time, NOT the import mtime, so the UI cannot present
        # an import as a completion.  Prefer the explicit fields above.
        "projection_persisted_at": production_completed_at,
        "last_attempt_at": (latest or {}).get("completed_at"),
        "freshness_basis": (
            "production_cycle_completed_at is the authoritative research "
            "completion time; projection_imported_at/synced_at are LOCAL import "
            "times and are never presented as a completed cycle."),
        "research_lag": research_lag,
        # Provenance of the live production bridge that feeds this Lab the
        # newest verified completed cycle (import/no-op/fail-closed state).
        "continuous_bridge": bridge_status,
    }


def _additional_records(projection: dict[str, Any] | None) -> dict[str, Any]:
    """Inspectable historical + separate-local records that are NOT part of the
    authoritative production projection.

    The production projection remains the sole authority for its own scientific
    state.  This block exposes, SEPARATELY and clearly labelled, records that
    would otherwise disappear from view once a projection becomes authoritative:

      * historical archive findings / hypotheses / candidates (verified offline
        completed transfer), and
      * separate local persisted registry hypotheses / candidates.

    Records are NEVER merged into the projection's arrays and are de-duplicated
    against the projection's own IDs so nothing is double-counted or implied to
    be produced by the current projection.  Each record keeps its own source,
    identity, completion time, lineage and authority labels.
    """
    proj = projection or {}
    proj_finding_ids = {str(f.get("finding_id")) for f in proj.get("findings", [])}
    proj_hyp_ids = {str(h.get("hypothesis_id")) for h in proj.get("hypotheses", [])}
    proj_cand_ids = {str(c.get("candidate_id")) for c in proj.get("candidates", [])}

    hist = {"available": False, "findings": [], "hypotheses": [], "candidates": [],
            "source": None}
    try:
        from research_engine.v10.continuous.research_lab_archive import load_archive
        directory, catalog, summary, _ = load_archive(HISTORICAL_ARCHIVE)

        def label(entity_kind, row):
            return {
                "id": row.get("id"),
                "status": row.get("status"),
                "statement": row.get("statement"),
                "source": "VERIFIED_COMPLETED_HISTORICAL_ARCHIVE",
                "archive_id": summary["archive_id"],
                "source_cycle_id": summary["cycle_id"],
                "completed_at": summary["completed_at"],
                "authority": "NON_AUTHORITATIVE_HISTORICAL",
                "live_currentness_verified": False,
                "belongs_to_current_projection": False,
                "kind": entity_kind,
            }
        hist["source"] = {
            "archive_id": summary["archive_id"], "cycle_id": summary["cycle_id"],
            "completed_at": summary["completed_at"]}
        hist["findings"] = [label("finding", r)
                            for r in summary["entities"]["finding"]
                            if str(r.get("id")) not in proj_finding_ids]
        hist["hypotheses"] = [label("hypothesis", r)
                              for r in summary["entities"]["hypothesis"]
                              if str(r.get("id")) not in proj_hyp_ids]
        hist["candidates"] = [label("candidate", r)
                              for r in summary["entities"]["candidate"]
                              if str(r.get("id")) not in proj_cand_ids]
        hist["available"] = True
    except Exception as exc:
        hist["error"] = f"{type(exc).__name__}: {exc}"

    local = {"available": False, "hypotheses": [], "candidates": [], "error": None}
    try:
        local_hyps, local_cands = _local_scientific_records()
        for h in local_hyps:
            h["belongs_to_current_projection"] = (
                str(h.get("hypothesis_id")) in proj_hyp_ids)
            h["authority"] = "SEPARATE_LOCAL_REGISTRY_NON_AUTHORITATIVE"
        for c in local_cands:
            c["belongs_to_current_projection"] = (
                str(c.get("candidate_id")) in proj_cand_ids)
            c["authority"] = "SEPARATE_LOCAL_REGISTRY_NON_AUTHORITATIVE"
            c["runtime_authority"] = "NOT_VERIFIED_LOCAL"
            c["human_approval_required"] = True
        # Only expose those not already authoritative in the projection, so the
        # projection's own records are never duplicated.
        local["hypotheses"] = [h for h in local_hyps
                               if not h["belongs_to_current_projection"]]
        local["candidates"] = [c for c in local_cands
                               if not c["belongs_to_current_projection"]]
        local["available"] = bool(local["hypotheses"] or local["candidates"])
    except Exception as exc:
        local["error"] = f"{type(exc).__name__}: {exc}"

    return {
        "notice": "Authoritative scientific state is the production projection "
                  "above. The records here are historical/local, shown for "
                  "inspection only, labelled by source and authority, and are "
                  "NOT part of the current projection or merged into it.",
        "historical_archive": hist,
        "separate_local_registry": local,
        "counts": {
            "historical_findings": len(hist["findings"]),
            "historical_hypotheses": len(hist["hypotheses"]),
            "historical_candidates": len(hist["candidates"]),
            "local_hypotheses": len(local["hypotheses"]),
            "local_candidates": len(local["candidates"]),
        },
    }


def build_state() -> dict[str, Any]:
    errors: list[str] = []
    # Continuous bridge (read-only consumer): before reading our own STATE_ROOT,
    # import any NEWLY completed, verified production cycle from the continuous
    # coordinator's output.  Idempotent + incremental: a stat-only no-op when
    # nothing advanced, so this never reloads large artifacts per refresh.
    # Fail-safe: any error leaves prior authoritative Lab state untouched and is
    # reported, never surfaced as a fabricated cycle. READ_ONLY restricts
    # investigation execution, not consumption of verified completed results.
    bridge_status: dict[str, Any] = {"status": "SKIPPED"}
    try:
        from research_engine.v10.continuous.continuous_projection_bridge import (
            sync_latest_completed_cycle,
        )
        bridge_status = sync_latest_completed_cycle(STATE_ROOT).to_dict()
    except Exception as exc:
        # Fail closed on the import; the Lab keeps whatever it already had.
        bridge_status = {"status": "ERROR", "reason": f"{type(exc).__name__}: {exc}"}
        errors.append(f"Continuous bridge: {type(exc).__name__}: {exc}")
    projection = None
    try:
        from research_engine.v10.continuous.research_projection import ResearchProjectionStore
        projection = ResearchProjectionStore(STATE_ROOT / "projection").load_latest()
    except Exception as exc:
        errors.append(f"Projection unreadable: {exc}")
    if READ_ONLY and projection is not None:
        try:
            from research_engine.v10.continuous.continuous_projection_bridge import (
                _verify_projection,
            )
            marker = _read_json(STATE_ROOT / "continuous_bridge_state.json")
            if (marker.get("schema") != "research_lab_continuous_bridge_v1"
                    or marker.get("imported_projection_version") != projection.get("projection_version")
                    or marker.get("imported_cycle_id") != projection.get("continuous_cycle_id")):
                raise ValueError("LIVE_PROJECTION_NOT_VERIFIED_COMPLETED_CYCLE")
            _verify_projection(projection, expected_version=str(marker["imported_projection_version"]))
        except Exception as exc:
            errors.append(f"Projection not publishable: {type(exc).__name__}: {exc}")
            projection = None
    if READ_ONLY and projection is None:
        historical = _completed_read_only_state()
        historical["errors"].extend(errors)
        historical["publication_bridge"] = bridge_status
        return historical
    overlay: dict[str, Any] = {}
    if projection:
        overlay = {r["question_id"]: r for r in projection.get("canonical_questions", [])}
    try:
        questions = _merge_questions(_registry_questions(), overlay)
        if not READ_ONLY:
            _annotate_evaluation_currency(questions)
        _bound_evidence_references(questions)
    except Exception as exc:
        questions = []
        errors.append(f"Canonical registry unavailable: {exc}")
    source = "UNIFIED_PROJECTION" if projection else "PERSISTED_STORES_NO_PROJECTION"
    src = projection or {}
    # The unified projection is the sole authority for scientific artifacts.
    # Empty projected collections are authoritative emptiness, not permission
    # to substitute mutable diagnostic registries. With no projection, show
    # only canonical definitions and an explicit no-authority state.
    hypotheses = list(src.get("hypotheses", []))
    candidates = list(src.get("candidates", []))
    findings = list(src.get("findings", []))
    cycles, success = _cycles()
    q71_rows = src.get("generated_questions", [])
    work = src.get("investigations_and_work_queues") or {}
    investigations = []
    for c in candidates:
        plan = c.get("plan") or {}
        if c.get("status") not in ("REJECTED", "RETIRED"):
            investigations.append({
                "id": c["candidate_id"], "type": "CANDIDATE_VALIDATION", "status": c.get("status"),
                "state": ("SHADOW_COLLECTING" if c.get("status") == "SHADOW_VALIDATION_ACTIVE"
                          else "WAITING_FOR_DATA" if c.get("status") == "PROPOSED" else "IN_PROGRESS"),
                "minimum_sample": plan.get("minimum_sample"), "note": c.get("notes", ""),
                "linked": c.get("hypothesis_id")})
    for q in questions:
        if q["status"] in ("WAITING_FOR_DATA", "INSUFFICIENT_DATA", "CANNOT_KNOW_YET"):
            investigations.append({
                "id": q["question_id"], "type": "CANONICAL_QUESTION", "status": q["status"],
                "state": "WAITING_FOR_DATA", "minimum_sample": q.get("minimum_required_n"),
                "sample_n": q.get("sample_n"), "note": q.get("title"), "linked": None})
    # Generated questions are investigations in their own right, with their own
    # governed waiting/blocked reasons.
    for row in q71_rows:
        lifecycle = str(row.get("lifecycle_status") or "")
        if lifecycle not in (
                "WAITING_FOR_DATA", "MISSING_EVALUATOR", "IMPLEMENTATION_BLOCKED",
                "BLOCKED", "INSUFFICIENT_DATA", "RUNNING", "QUEUED"):
            continue
        investigations.append({
            "id": row.get("generated_question_id"), "type": "GENERATED_QUESTION",
            "status": lifecycle,
            "state": ("RUNNING" if lifecycle == "RUNNING"
                      else "MISSING_EVALUATOR" if lifecycle == "MISSING_EVALUATOR"
                      else "IN_PROGRESS" if lifecycle in ("QUEUED", "IMPLEMENTATION_BLOCKED")
                      else "WAITING_FOR_DATA"),
            "minimum_sample": None, "sample_n": None,
            "note": row.get("question") or row.get("why_generated"),
            "linked": None,
            "reason": row.get("waiting_reason") or row.get("missing_evaluator_reason")
            or row.get("reason_code"),
            "execution_freshness": row.get("execution_freshness"),
            "evaluator_available": row.get("evaluator_available"),
        })
    changes = list(_status_events(candidates))
    for cyc in cycles:
        changes.append({"time": cyc.get("recorded_at"), "kind": "CYCLE", "subject": cyc["continuous_cycle_id"],
                        "title": f"Research cycle {cyc['cycle_outcome']}",
                        "detail": (cyc["failure"]["title"] if cyc.get("failure") else "")})
    last_run = None
    if LAST_RUN_PATH.exists():
        try:
            last_run = _read_json(LAST_RUN_PATH)
        except (OSError, ValueError):
            pass
    proj_path = STATE_ROOT / "projection" / "latest.json"
    # Local file mtime == when THIS Lab wrote the projection copy (import/sync
    # time), NOT when the research cycle completed.  Labelled as such below.
    projection_imported_at = _mtime_iso(proj_path) if projection else None
    changes.extend(_projection_events(projection, questions, projection_imported_at))
    changes.sort(key=lambda e: e.get("time") or "", reverse=True)
    counts: dict[str, int] = {}
    for q in questions:
        counts[q["status"]] = counts.get(q["status"], 0) + 1
    # Repair Block 3: ONE canonical candidate lifecycle.  The Lab shows the
    # exact authoritative state, never a legacy status re-interpreted by the UI.
    lifecycle = _candidate_lifecycle_state()
    lifecycle_by_id = {row["candidate_id"]: row for row in lifecycle.get("candidates", [])}
    for c in candidates:
        view = lifecycle_by_id.get(c.get("candidate_id"))
        if view is None:
            continue
        c["canonical_lifecycle"] = {
            "canonical_state": view["canonical_state"],
            "source": view["source"],
            "runtime_effective": view["runtime_effective"],
            "required_action": view["required_action"],
            "review_notice": view["review_notice"],
            "disagreements": view["disagreements"],
            "authoritative_sources": view["authoritative_sources"],
            "compatibility_sources": view["compatibility_sources"],
            "superseded_by": view["superseded_by"],
            "evidence_frontier": view["evidence_frontier"],
            "baseline_config_hash": view["baseline_config_hash"],
        }
    for entry in investigations:
        view = lifecycle_by_id.get(entry.get("id"))
        if view is not None:
            entry["canonical_state"] = view["canonical_state"]
            entry["runtime_effective"] = view["runtime_effective"]
    return {
        "generated_at": _now(), "source": source, "errors": errors, "read_only": READ_ONLY,
        "freshness": _freshness_block(
            projection=projection, cycles=cycles, success=success,
            projection_imported_at=projection_imported_at,
            research_lag=src.get("research_lag") or {},
            bridge_status=bridge_status),
        "latest_failure": next((c["failure"] | {"cycle_id": c["continuous_cycle_id"],
                                                  "at": c.get("imported_at") or c.get("recorded_at"),
                                                  "completed_at": c.get("completed_at"),
                                                  "stage_statuses": c.get("stage_statuses")}
                                for c in cycles[:1] if c.get("failure")), None),
        "question_counts": counts, "questions": questions,
        "q71": {
            "generated": q71_rows,
            "queue": work.get("generated_question_queue") or [],
            "available": bool(projection),
            # Repair Block 2: the five Lab distinctions, read from the
            # projection's own derivation.  The Lab never infers scientific
            # meaning from an execution status.
            "lifecycle_counts": work.get("generated_question_lifecycle_counts") or {},
            "execution_counts": work.get("generated_question_execution_counts") or {},
            "freshness_counts": work.get("generated_question_freshness_counts") or {},
            "executable": [row.get("generated_question_id") for row in q71_rows
                           if row.get("question_executable")],
            "running": [row.get("generated_question_id") for row in q71_rows
                        if row.get("question_running")],
            "answered": [row.get("generated_question_id") for row in q71_rows
                         if row.get("question_answered")],
            "scientifically_actionable": [
                row.get("generated_question_id") for row in q71_rows
                if row.get("question_scientifically_actionable")],
            "missing_evaluator": [
                row.get("generated_question_id") for row in q71_rows
                if row.get("lifecycle_status") == "MISSING_EVALUATOR"],
            "waiting_for_data": [
                row.get("generated_question_id") for row in q71_rows
                if row.get("lifecycle_status") == "WAITING_FOR_DATA"],
            "superseded": [row.get("generated_question_id") for row in q71_rows
                           if row.get("lifecycle_status") == "SUPERSEDED"],
        },
        "findings": findings, "hypotheses": hypotheses, "candidates": candidates,
        # Historical + separate-local records, labelled, never merged into the
        # authoritative projection arrays above (survives projection changes).
        "additional_records": _additional_records(projection),
        "candidate_lifecycle": lifecycle,
        # Observation space / governed coverage.  Projected verbatim from the
        # unified projection; a missing production snapshot is reported as
        # NO_PRODUCTION_OBSERVATION_SPACE rather than as an empty success.
        "observation_coverage": dict(
            src.get("observation_coverage")
            or {"status": NO_PRODUCTION_OBSERVATION_SPACE,
                "reason": "no unified projection observation coverage surface",
                "observation_space_snapshot_id": None,
                "production_coverage_snapshot_id": None,
                "total_governed_observation_cells": 0,
                "cell_count": 0,
                "conserved": False}),
        "investigations": investigations, "work_queues": work,
        "data_frontier": src.get("data_frontier"),
        "what_changed_cycle": src.get("what_changed"),
        "changes": changes, "cycles": cycles, "last_run": last_run,
        "run": run_status(),
        "legacy_diagnostics": {"available": LEGACY_COCKPIT.exists(),
                               "generated_at": _mtime_iso(LEGACY_COCKPIT), "url": "/legacy/cockpit"},
        "invariants": {"live_approved_any": any(c.get("live_approved") for c in candidates),
                       "canonical_question_count": len(questions)},
    }


# ── Run control ──────────────────────────────────────────────────────────────
def run_status() -> dict[str, Any]:
    def with_progress(state: dict[str, Any]) -> dict[str, Any]:
        try:
            progress = _read_json(PROGRESS_PATH)
        except (OSError, ValueError, TypeError):
            return state
        if progress.get("status") != "RUNNING":
            return state
        stage = progress.get("current_stage") or state.get("stage") or "RUNNING"
        state.update({
            "stage": stage,
            "stage_label": _STAGE_LABEL.get(str(stage), str(stage)),
            "progress": progress,
        })
        return state

    state = dict(_run)
    if state.get("status") == "IDLE" and LOCK_PATH.exists():
        try:
            lock = _read_json(LOCK_PATH)
            if _pid_alive(int(lock.get("server_pid", 0))) and _pid_alive(int(lock.get("child_pid", 0))):
                return with_progress({
                    "status": "RUNNING", "started_at": lock.get("started_at"),
                    "stage": "RUNNING", "stage_label": "Research cycle in progress",
                    "note": "A cycle is running from another Research Lab window."})
        except (OSError, ValueError, TypeError):
            pass
    if state.get("status") == "RUNNING":
        state["elapsed_s"] = int(time.time() - state["started_epoch"])
        return with_progress(state)
    return state


def _acquire_lock() -> bool:
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            reservation = {"server_pid": os.getpid(), "child_pid": os.getpid(),
                           "started_at": _now(), "stage": "RUNNING"}
            with os.fdopen(fd, "w", encoding="utf-8") as lock_file:
                lock_file.write(json.dumps(reservation))
                lock_file.flush()
                os.fsync(lock_file.fileno())
            return True
        except FileExistsError:
            try:
                lock = _read_json(LOCK_PATH)
                if _pid_alive(int(lock.get("server_pid", 0))) and _pid_alive(int(lock.get("child_pid", 0))):
                    return False
            except (OSError, ValueError, TypeError):
                time.sleep(0.2)
                try:
                    lock = _read_json(LOCK_PATH)
                except (OSError, ValueError):
                    if LOCK_PATH.exists() and time.time() - LOCK_PATH.stat().st_mtime < 5:
                        return False
                else:
                    if _pid_alive(int(lock.get("server_pid", 0))) and _pid_alive(int(lock.get("child_pid", 0))):
                        return False
            LOCK_PATH.unlink(missing_ok=True)
    return False


def _persist_last_run(record: dict[str, Any]) -> None:
    LAST_RUN_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = LAST_RUN_PATH.with_name(f".{LAST_RUN_PATH.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps(record, indent=2), encoding="utf-8")
        os.replace(temporary, LAST_RUN_PATH)
    finally:
        temporary.unlink(missing_ok=True)


def _failed_record(started: str, reason: str, stage: str | None = None,
                   exit_code: int | None = None) -> dict[str, Any]:
    failure_stage = "FRONTIER" if "tokenretrievalerror" in reason.lower() or (
        "token" in reason.lower() and ("expired" in reason.lower() or "sso" in reason.lower())
    ) else stage
    return {
        "started_at": started,
        "finished_at": _now(),
        "exit_code": exit_code,
        "status": "FAILED",
        "outcome": "FAILED",
        "failure": humanise_failure(failure_stage, reason),
    }


def start_run() -> tuple[int, dict[str, Any]]:
    if READ_ONLY:
        return 403, {"error": "READ_ONLY", "message": "Research execution disabled in this Lab session."}
    with _run_guard:
        if _run.get("status") == "RUNNING" or not _acquire_lock():
            return 409, {"error": "ALREADY_RUNNING", "message": "A research cycle is already running.",
                         "run": run_status()}
        before = {p.name for p in (STATE_ROOT / "cycles" / "history").glob("*.json")} \
            if (STATE_ROOT / "cycles" / "history").exists() else set()
        started = _now()
        try:
            proc = subprocess.Popen(
                [sys.executable, "-m", "research_engine.v10.continuous.research_loop"], cwd=REPO,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
        except OSError as exc:
            record = _failed_record(started, f"{type(exc).__name__}: {exc}", "FRONTIER")
            try:
                _persist_last_run(record)
            except OSError as persist_exc:
                record["persistence_error"] = f"{type(persist_exc).__name__}: {persist_exc}"
                print(f"Research Lab could not persist failed run state: {persist_exc}", file=sys.stderr)
            _run.clear()
            _run.update({"status": "IDLE", "last": record})
            LOCK_PATH.unlink(missing_ok=True)
            return 500, {"error": "RUN_START_FAILED", "run": record}
        lock = {"server_pid": os.getpid(), "child_pid": proc.pid,
                "started_at": started, "stage": "RUNNING"}
        temporary = LOCK_PATH.with_name(f".{LOCK_PATH.name}.{os.getpid()}.tmp")
        try:
            temporary.write_text(json.dumps(lock), encoding="utf-8")
            os.replace(temporary, LOCK_PATH)
        finally:
            temporary.unlink(missing_ok=True)
        _run.clear()
        _run.update({"status": "RUNNING", "started_at": started, "started_epoch": time.time(),
                     "stage": "RUNNING", "stage_label": "Research cycle in progress"})
        threading.Thread(target=_wait_run, args=(proc, before, started), daemon=True).start()
        return 202, {"run": run_status()}


def _wait_run(proc: subprocess.Popen, before: set[str], started: str) -> None:
    try:
        out, err = proc.communicate()
        result = None
        history = STATE_ROOT / "cycles" / "history"
        fresh = [p for p in history.glob("*.json") if p.name not in before] if history.exists() else []
        if fresh:
            result = _read_json(max(fresh, key=lambda p: p.stat().st_mtime))
        if result and result.get("cycle_outcome") in ("COMPLETED", "NO_NEW_RESEARCH_EVIDENCE"):
            record: dict[str, Any] = {"started_at": started, "finished_at": _now(),
                                      "exit_code": proc.returncode, "status": "SUCCEEDED",
                                      "outcome": result["cycle_outcome"],
                                      "cycle_id": result["continuous_cycle_id"]}
        else:
            reason = (result or {}).get("failure_reason") or (err or out or "")[-4000:]
            stage = (result or {}).get("failure_stage")
            if not stage and ("tokenretrievalerror" in reason.lower() or (
                    "token" in reason.lower() and ("expired" in reason.lower() or "sso" in reason.lower()))):
                stage = "FRONTIER"
            record = _failed_record(started, reason or "The research process exited without a cycle result.",
                                    stage, proc.returncode)
            record["outcome"] = (result or {}).get("cycle_outcome", "FAILED")
            record["stage_statuses"] = (result or {}).get("stage_statuses")
        _persist_last_run(record)
        _run.clear()
        _run.update({"status": "IDLE", "last": record})
    except Exception as exc:
        record = _failed_record(started, f"{type(exc).__name__}: {exc}", "FRONTIER", proc.poll())
        try:
            _persist_last_run(record)
        except OSError as persist_exc:
            record["persistence_error"] = f"{type(persist_exc).__name__}: {persist_exc}"
            print(f"Research Lab could not persist failed run state: {persist_exc}", file=sys.stderr)
        _run.clear()
        _run.update({"status": "IDLE", "last": record})
        print(f"Research Lab run supervisor failed: {exc}", file=sys.stderr)
    finally:
        LOCK_PATH.unlink(missing_ok=True)


# ── HTTP ─────────────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    server_version = "ResearchLab/1"

    def log_message(self, *args: Any) -> None:
        pass

    def _host_ok(self) -> bool:
        return (self.headers.get("Host") or "").split(":")[0] in ("127.0.0.1", "localhost")

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, value: Any) -> None:
        self._send(code, json.dumps(value, default=str).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self) -> None:
        if not self._host_ok():
            return self._json(403, {"error": "BAD_HOST"})
        path = self.path.split("?", 1)[0]
        if path == "/api/health":
            return self._json(200, {"app": APP_ID, "pid": os.getpid(), "read_only": READ_ONLY, "repository": str(REPO), "state_root": str(STATE_ROOT)})
        if path == "/api/artifact" and READ_ONLY:
            try:
                from urllib.parse import parse_qs, urlsplit
                from research_engine.v10.continuous.research_lab_archive import load_archive, verified_file
                directory, catalog, _, _ = load_archive(HISTORICAL_ARCHIVE)
                relative = parse_qs(urlsplit(self.path).query).get("path", [""])[0]
                artifact = verified_file(directory, catalog, relative)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(artifact.stat().st_size))
                self.send_header("Content-Disposition", 'attachment; filename="' + artifact.name + '"')
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                with artifact.open("rb") as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        self.wfile.write(block)
                return
            except Exception as exc:
                return self._json(409, {"error": "ARTIFACT_FAIL_CLOSED:" + str(exc)})
        if path == "/api/state":
            try:
                return self._json(200, build_state())
            except Exception as exc:
                return self._json(500, {"error": f"{type(exc).__name__}: {exc}"})
        if path == "/api/run":
            return self._json(200, run_status())
        if path == "/legacy/cockpit" and LEGACY_COCKPIT.exists():
            return self._send(200, LEGACY_COCKPIT.read_bytes(), "text/html; charset=utf-8")
        if path.startswith("/api/"):
            return self._json(404, {"error": "NOT_FOUND"})
        # Every other path is a client-side route, so F5 on a detail page never 404s.
        self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")

    def do_POST(self) -> None:
        if not self._host_ok() or self.headers.get("X-Research-Lab") != "1":
            return self._json(403, {"error": "FORBIDDEN"})
        if self.path.split("?", 1)[0] == "/api/run":
            code, body = start_run()
            return self._json(code, body)
        self._json(404, {"error": "NOT_FOUND"})


def _existing_server() -> bool:
    try:
        with urlopen(f"http://{HOST}:{PORT}/api/health", timeout=1.5) as resp:
            health = json.loads(resp.read())
            return (health.get("app") == APP_ID and health.get("read_only") == READ_ONLY
                    and health.get("repository") == str(REPO)
                    and health.get("state_root") == str(STATE_ROOT))
    except Exception:
        return False


def main(argv: list[str] | None = None) -> int:
    global READ_ONLY, HISTORICAL_ARCHIVE, PORT, STATE_ROOT, PRODUCTION_COVERAGE_DIR, OBSERVATION_SPACE_DIR, LOCK_PATH, LAST_RUN_PATH, PROGRESS_PATH
    parser = argparse.ArgumentParser(description="Human Research Lab")
    parser.add_argument("--open", action="store_true", help="open the browser")
    parser.add_argument("--read-only", action="store_true", default=True, help="disable all research execution (default)")
    parser.add_argument("--archive", type=Path, default=HISTORICAL_ARCHIVE)
    parser.add_argument("--state-root", type=Path, default=STATE_ROOT,
                        help="Research Lab publication directory (default: production state root)")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    READ_ONLY = args.read_only; HISTORICAL_ARCHIVE = args.archive.resolve(); PORT = args.port
    STATE_ROOT = args.state_root.resolve()
    PRODUCTION_COVERAGE_DIR = STATE_ROOT / "production_coverage"
    OBSERVATION_SPACE_DIR = STATE_ROOT / "observation_space"
    LOCK_PATH = STATE_ROOT / "lab_run.lock"
    LAST_RUN_PATH = STATE_ROOT / "lab_last_run.json"
    PROGRESS_PATH = STATE_ROOT / "cycle_progress.json"
    if not 1024 <= PORT <= 65535: parser.error("port must be 1024..65535")
    url = f"http://{HOST}:{PORT}/"
    if _existing_server():
        print(f"Research Lab already running at {url}")
        if args.open:
            webbrowser.open(url)
        return 0
    os.chdir(REPO)
    sys.path.insert(0, str(REPO))
    try:
        httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError as exc:
        print(f"Cannot bind {HOST}:{PORT}: {exc}")
        return 1
    print(f"Research Lab running at {url}  (close this window to stop)")
    if args.open:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
