"""Offline Windows-safe progress publication and bounded failure handling."""
import json
import os
import threading

import pytest

import research_engine.v10.continuous.cycle_state as cycle_state
from research_engine.v10.continuous.cycle_state import ContinuousCycleProgressStore


def _sharing_denial():
    error = PermissionError(13, "offline access denied")
    error.winerror = 5
    return error


def test_progress_replace_recovers_from_temporary_windows_denial(tmp_path, monkeypatch):
    path = tmp_path / "cycle_progress.json"
    store = ContinuousCycleProgressStore(path)
    store.start(cycle_attempt_id="offline")
    original_replace = os.replace
    attempts = []
    monkeypatch.setattr(cycle_state.time, "sleep", lambda delay: None)

    def replace(source, destination):
        attempts.append((source, destination))
        if len(attempts) < 3:
            raise _sharing_denial()
        original_replace(source, destination)

    monkeypatch.setattr(cycle_state.os, "replace", replace)
    store.enter_stage("QUESTIONS", details={"phase": "QUESTION_RUNNING"})
    assert len(attempts) == 3
    assert json.loads(path.read_text(encoding="utf-8"))["current_stage"] == "QUESTIONS"
    assert not list(tmp_path.glob("cycle_progress.json.*.tmp"))


def test_progress_replace_exhaustion_raises_and_keeps_prior_file(tmp_path, monkeypatch):
    path = tmp_path / "cycle_progress.json"
    store = ContinuousCycleProgressStore(path)
    store.start(cycle_attempt_id="offline")
    previous = path.read_bytes()
    attempts = []
    monkeypatch.setattr(cycle_state.time, "sleep", lambda delay: None)

    def denied(source, destination):
        attempts.append((source, destination))
        raise _sharing_denial()

    monkeypatch.setattr(cycle_state.os, "replace", denied)
    with pytest.raises(PermissionError):
        store.enter_stage("QUESTIONS")
    assert len(attempts) == 8
    assert path.read_bytes() == previous
    assert not list(tmp_path.glob("cycle_progress.json.*.tmp"))


@pytest.mark.skipif(os.name != "nt", reason="Windows destination-sharing semantics")
def test_progress_replace_waits_for_concurrent_reader(tmp_path):
    path = tmp_path / "cycle_progress.json"
    store = ContinuousCycleProgressStore(path)
    store.start(cycle_attempt_id="offline")
    reader_open = threading.Event()
    release_reader = threading.Event()

    def reader():
        with path.open("r", encoding="utf-8"):
            reader_open.set()
            release_reader.wait(0.12)

    # Keep the destination open in another thread while the writer first tries
    # to replace it. Windows reports WinError 5 until that handle closes.
    thread = threading.Thread(target=reader)
    thread.start()
    assert reader_open.wait(2)
    try:
        store.enter_stage("QUESTIONS")
    finally:
        release_reader.set()
        thread.join(timeout=2)
    assert not thread.is_alive()
    assert json.loads(path.read_text(encoding="utf-8"))["current_stage"] == "QUESTIONS"
    assert not list(tmp_path.glob("cycle_progress.json.*.tmp"))
