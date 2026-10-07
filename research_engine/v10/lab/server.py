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
OPT_REGISTRY = REPO / "data" / "research" / "optimisation" / "registry.json"
LEGACY_COCKPIT = REPO / "reports" / "research" / "cockpit.html"
LOCK_PATH = STATE_ROOT / "lab_run.lock"
LAST_RUN_PATH = STATE_ROOT / "lab_last_run.json"
PROGRESS_PATH = STATE_ROOT / "cycle_progress.json"
HOST, PORT = "127.0.0.1", 8765
APP_ID = "research-lab-v1"

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


def _cycles() -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    history = STATE_ROOT / "cycles" / "history"
    rows: list[dict[str, Any]] = []
    for path in history.glob("*.json") if history.exists() else []:
        try:
            row = _read_json(path)
        except (OSError, ValueError):
            continue
        row["recorded_at"] = _mtime_iso(path)
        row["failure"] = (humanise_failure(row.get("failure_stage"), row.get("failure_reason"))
                          if row.get("cycle_outcome") == "FAILED" else None)
        rows.append(row)
    rows.sort(key=lambda r: r.get("recorded_at") or "", reverse=True)
    pointer = STATE_ROOT / "cycles" / "latest_success.json"
    success = None
    if pointer.exists():
        try:
            success = _read_json(pointer)
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


def build_state() -> dict[str, Any]:
    errors: list[str] = []
    projection = None
    try:
        from research_engine.v10.continuous.research_projection import ResearchProjectionStore
        projection = ResearchProjectionStore(STATE_ROOT / "projection").load_latest()
    except Exception as exc:
        errors.append(f"Projection unreadable: {exc}")
    overlay: dict[str, Any] = {}
    if projection:
        overlay = {r["question_id"]: r for r in projection.get("canonical_questions", [])}
    else:
        try:
            from research_engine.v10.continuous.question_cycle_state import QuestionCycleStore
            current = QuestionCycleStore().load_current()
            overlay = (current or {}).get("questions", {})
        except Exception as exc:
            errors.append(f"Question projection unreadable: {exc}")
    try:
        questions = _merge_questions(_registry_questions(), overlay)
        _annotate_evaluation_currency(questions)
    except Exception as exc:
        questions = []
        errors.append(f"Canonical registry unavailable: {exc}")
    fb = _fallback_from_optimisation()
    source = "UNIFIED_PROJECTION" if projection else "PERSISTED_STORES_NO_PROJECTION"
    src = projection or {}
    hypotheses = src.get("hypotheses") or fb["hypotheses"]
    candidates = src.get("candidates") or fb["candidates"]
    findings = src.get("findings") or fb["findings"]
    for c in candidates:
        c["plan"] = fb["plans"].get(c["candidate_id"])
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
    projection_saved_at = _mtime_iso(proj_path) if projection else None
    changes.extend(_projection_events(projection, questions, projection_saved_at))
    changes.sort(key=lambda e: e.get("time") or "", reverse=True)
    counts: dict[str, int] = {}
    for q in questions:
        counts[q["status"]] = counts.get(q["status"], 0) + 1
    return {
        "generated_at": _now(), "source": source, "errors": errors,
        "freshness": {
            "projection_version": (projection or {}).get("projection_version"),
            "projection_persisted_at": projection_saved_at,
            "last_attempt_at": cycles[0]["recorded_at"] if cycles else None,
            "last_attempt_outcome": cycles[0]["cycle_outcome"] if cycles else None,
            "last_success": success,
            "last_successful_cycle_id": (success or {}).get("continuous_cycle_id"),
        },
        "latest_failure": next((c["failure"] | {"cycle_id": c["continuous_cycle_id"],
                                                  "at": c["recorded_at"],
                                                  "stage_statuses": c.get("stage_statuses")}
                                for c in cycles[:1] if c.get("failure")), None),
        "question_counts": counts, "questions": questions,
        "q71": {"generated": q71_rows, "queue": work.get("generated_question_queue") or [],
                "available": bool(projection)},
        "findings": findings, "hypotheses": hypotheses, "candidates": candidates,
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
            return self._json(200, {"app": APP_ID, "pid": os.getpid()})
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
            return json.loads(resp.read()).get("app") == APP_ID
    except Exception:
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Human Research Lab")
    parser.add_argument("--open", action="store_true", help="open the browser")
    args = parser.parse_args(argv)
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
