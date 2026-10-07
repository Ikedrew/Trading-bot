import json
import os
import threading
from http.server import ThreadingHTTPServer
from urllib.request import urlopen

from research_engine.v10.lab import server


class CompletedProcess:
    def __init__(self, stdout="", stderr="", returncode=1):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.pid = os.getpid()

    def communicate(self):
        return self.stdout, self.stderr

    def poll(self):
        return self.returncode


def isolate_run_state(tmp_path, monkeypatch):
    state_root = tmp_path / "continuous"
    monkeypatch.setattr(server, "STATE_ROOT", state_root)
    monkeypatch.setattr(server, "LOCK_PATH", state_root / "lab_run.lock")
    monkeypatch.setattr(server, "LAST_RUN_PATH", state_root / "lab_last_run.json")
    monkeypatch.setattr(server, "_run", {"status": "IDLE"})
    return state_root


def test_health_endpoint_reports_running_lab():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(f"http://127.0.0.1:{httpd.server_port}/api/health", timeout=2) as response:
            health = json.loads(response.read())
        assert health["app"] == server.APP_ID
        assert health["pid"] == os.getpid()
    finally:
        httpd.shutdown()
        thread.join(timeout=2)
        httpd.server_close()


def test_auth_failure_is_persisted_as_frontier_terminal_state(tmp_path, monkeypatch):
    state_root = isolate_run_state(tmp_path, monkeypatch)
    state_root.mkdir(parents=True)
    server.LOCK_PATH.write_text('{"stage":"RUNNING"}', encoding="utf-8")
    proc = CompletedProcess(stderr="TokenRetrievalError: AWS SSO token expired")

    server._wait_run(proc, set(), "2026-10-04T00:00:00Z")

    persisted = json.loads(server.LAST_RUN_PATH.read_text(encoding="utf-8"))
    assert persisted["status"] == "FAILED"
    assert persisted["failure"]["kind"] == "AWS_AUTH_EXPIRED"
    assert persisted["failure"]["failed_stage"] == "FRONTIER"
    assert "Governed S3 research evidence could not be accessed" in persisted["failure"]["message"]
    assert "no downstream scientific stages were run" in persisted["failure"]["message"]
    assert not server.LOCK_PATH.exists()
    assert server.run_status()["status"] == "IDLE"


def test_supervisor_exception_is_persisted_and_releases_lock(tmp_path, monkeypatch):
    state_root = isolate_run_state(tmp_path, monkeypatch)
    state_root.mkdir(parents=True)
    server.LOCK_PATH.write_text('{"stage":"RUNNING"}', encoding="utf-8")

    class BrokenProcess(CompletedProcess):
        def communicate(self):
            raise RuntimeError("output supervisor failed")

    server._wait_run(BrokenProcess(), set(), "2026-10-04T00:00:00Z")

    persisted = json.loads(server.LAST_RUN_PATH.read_text(encoding="utf-8"))
    assert persisted["status"] == "FAILED"
    assert "RuntimeError: output supervisor failed" in persisted["failure"]["technical"]
    assert not server.LOCK_PATH.exists()
    assert server.run_status()["status"] == "IDLE"


def test_successful_cycle_result_is_persisted(tmp_path, monkeypatch):
    state_root = isolate_run_state(tmp_path, monkeypatch)
    history = state_root / "cycles" / "history"
    history.mkdir(parents=True)
    (history / "cycle.json").write_text(json.dumps({
        "cycle_outcome": "COMPLETED",
        "continuous_cycle_id": "CRCYCLE-TEST",
    }), encoding="utf-8")
    server.LOCK_PATH.write_text('{"stage":"RUNNING"}', encoding="utf-8")

    server._wait_run(CompletedProcess(returncode=0), set(), "2026-10-04T00:00:00Z")

    persisted = json.loads(server.LAST_RUN_PATH.read_text(encoding="utf-8"))
    assert persisted["status"] == "SUCCEEDED"
    assert persisted["cycle_id"] == "CRCYCLE-TEST"
    assert not server.LOCK_PATH.exists()


def test_duplicate_start_does_not_launch_a_second_child(tmp_path, monkeypatch):
    state_root = isolate_run_state(tmp_path, monkeypatch)
    state_root.mkdir(parents=True)
    launches = []
    monkeypatch.setattr(server.subprocess, "Popen",
                        lambda *args, **kwargs: launches.append(CompletedProcess(returncode=0)) or launches[-1])
    monkeypatch.setattr(server.threading.Thread, "start", lambda self: None)

    status, _ = server.start_run()
    duplicate_status, duplicate_body = server.start_run()

    assert status == 202
    assert duplicate_status == 409
    assert duplicate_body["error"] == "ALREADY_RUNNING"
    assert len(launches) == 1
    assert json.loads(server.LOCK_PATH.read_text(encoding="utf-8"))["stage"] == "RUNNING"


def test_canonical_question_and_candidate_fallback_state():
    state = server.build_state()
    questions = {q["question_id"]: q for q in state["questions"]}

    assert len(questions) == 70
    assert questions["S1"]["status"] == "ALIAS_OR_SUPERSEDED"
    assert questions["S1"]["superseded_by"] == "E3"
    assert questions["L6"]["status"] == "INSUFFICIENT_DATA"

    candidate = next(c for c in state["candidates"] if c["candidate_id"] == "OPT-DP1-002")
    assert candidate["status"] == "SHADOW_VALIDATION_ACTIVE"
    assert candidate["source_finding_ids"] == ["F-DP1-006"]
    assert candidate["hypothesis_id"] == "HYP-DP1-002"
    assert candidate["policy_id"] == "TRAIL_ACT_0_25R_DIST_0_10R_V1"
    assert candidate["live_approved"] is False


def test_merge_uses_nested_block2_result_not_registry_fallback():
    base = server._registry_questions()
    overlay = {
        "E1": {"question_id": "E1", "runner_failed": False, "result": {
            "status": "COMPLETE", "sample_n": 21638, "substantive_answer": "NEGATIVE_EDGE",
            "limitations": ["x"], "missing_evidence": [], "key_metrics": {"n": 1}}},
        "M1": {"question_id": "M1", "runner_failed": True, "result": {
            "status": "INVALID", "failure_reason": "InvestigationSnapshotError:X"}},
        "S1": {"question_id": "S1", "runner_failed": False, "result": {
            "status": "ALIAS_OR_SUPERSEDED"}},
        "L6": {"question_id": "L6", "runner_failed": False, "result": {
            "status": "UNIMPLEMENTED"}},
    }
    rows = {q["question_id"]: q for q in server._merge_questions(base, overlay)}

    assert len(rows) == 70
    assert rows["E1"]["status"] == "COMPLETE"
    assert rows["E1"]["sample_n"] == 21638
    assert rows["E1"]["substantive_answer"] == "NEGATIVE_EDGE"
    assert rows["E1"]["status_source"] == "block2_projection"
    assert rows["M1"]["status"] == "INVALID" and rows["M1"]["runner_failed"] is True
    assert rows["S1"]["status"] == "ALIAS_OR_SUPERSEDED"
    assert rows["L6"]["status"] == "UNIMPLEMENTED"
    assert rows["D1"]["status"] == "NOT_YET_EVALUATED"  # absent from overlay: registry fallback


def test_evaluation_currency_is_deliberate_and_exposes_digest():
    from research_engine.registry.definition_validator import build_definitions_from_registry
    from research_engine.v10.continuous.canonical_question_cycle import (
        REGISTRY_VERSION, _load_registry,
    )
    from research_engine.v10.continuous.evaluation_identity import evaluation_identities

    questions = _load_registry()
    identities = evaluation_identities(
        questions, build_definitions_from_registry(questions),
        registry_version=REGISTRY_VERSION)
    current = identities["L3"]
    rows = [{"question_id": "L3", "status_source": "block2_projection",
             "evaluation_identity": None, "evaluation_identity_digest": None}]
    server._annotate_evaluation_currency(rows)
    assert rows[0]["evaluation_currency"] == "STALE"
    assert rows[0]["current_evaluation_identity_digest"] == current[
        "evaluation_identity_digest"]

    rows[0]["evaluation_identity"] = current
    rows[0]["evaluation_identity_digest"] = current["evaluation_identity_digest"]
    server._annotate_evaluation_currency(rows)
    assert rows[0]["evaluation_currency"] == "CURRENT"


def test_projection_timeline_includes_snapshot_and_material_changes():
    events = server._projection_events({
        "data_frontier": {
            "snapshot_id": "SNAP-1",
            "frontier_start": "2026-10-01",
            "frontier_end": "2026-10-02",
            "status": "FROZEN",
        },
        "what_changed": {
            "new_hypotheses": ["HYP-1"],
            "validation_transitions": [{"candidate_id": "OPT-1", "status": "COMPLETED"}],
        },
    }, [{"question_id": "Q1", "evaluated_at": "2026-10-04T00:00:00Z"}],
        "2026-10-04T00:01:00Z")

    titles = [event["title"] for event in events]
    assert "Research snapshot recorded: SNAP-1" in titles
    assert "1 canonical questions evaluated" in titles
    assert "Hypothesis created: HYP-1" in titles
    assert "Validation transition: OPT-1 → COMPLETED" in titles
    assert server._projection_events(None, [], None) == []
