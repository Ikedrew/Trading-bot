"""Isolated filesystem regression tests for the production projection store."""

import hashlib
import json
import os
from pathlib import Path

import pytest

from research_engine.control_plane.stage4_dataset_snapshot import canonical_json
from research_engine.v10.continuous.research_projection import (
    PROJECTION_SCHEMA, ResearchProjectionError, ResearchProjectionStore,
)


def projection(cycle_id):
    material = {
        "continuous_cycle_id": cycle_id,
        "canonical_question_cycle_id": "QCYCLE-TEST",
        "canonical_questions": [],
    }
    version = "RPROJ-" + hashlib.sha256(
        canonical_json(material).encode("utf-8")
    ).hexdigest()[:32].upper()
    return {"projection_schema": PROJECTION_SCHEMA,
            "projection_version": version, **material}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_atomic_history_pointer_replacement_and_readback(tmp_path):
    store = ResearchProjectionStore(tmp_path / "projection")
    first, second = projection("CCYCLE-ONE"), projection("CCYCLE-TWO")
    first_history = store.save(first)
    first_bytes = first_history.read_bytes()
    assert store.latest_path.read_bytes() == first_bytes
    assert json.loads(first_bytes) == first

    second_history = store.save(second)
    assert second_history != first_history
    assert first_history.read_bytes() == first_bytes
    assert store.latest_path.read_bytes() == second_history.read_bytes()
    assert digest(store.latest_path) == digest(second_history)
    assert ResearchProjectionStore(store.directory).load_latest() == second

    latest_before = store.latest_path.read_bytes()
    store.save(second)
    assert store.latest_path.read_bytes() == latest_before
    assert second_history.read_bytes() == latest_before


def test_failed_pointer_replace_preserves_cause_and_retries_from_history(
        tmp_path, monkeypatch):
    store = ResearchProjectionStore(tmp_path / "projection")
    first, second = projection("CCYCLE-ONE"), projection("CCYCLE-TWO")
    store.save(first)
    previous_pointer = store.latest_path.read_bytes()
    real_replace = os.replace

    def deny_pointer(source, destination):
        if Path(destination) == store.latest_path:
            raise PermissionError(13, "injected pointer denial", str(destination))
        return real_replace(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", deny_pointer)
        with pytest.raises(ResearchProjectionError) as caught:
            store.save(second)
    error = caught.value
    history = store.history_directory / (second["projection_version"] + ".json")
    assert isinstance(error.__cause__, PermissionError)
    assert "injected pointer denial" in str(error)
    assert str(store.latest_path) in str(error)
    assert "PROJECTION_PERSISTENCE_FAILED" in str(error)
    assert history.exists()
    original_history = history.read_bytes()
    assert store.latest_path.read_bytes() == previous_pointer
    assert not store.latest_path.with_name("latest.json.tmp").exists()

    # The immutable artifact is reused after the interrupted pointer write.
    assert ResearchProjectionStore(store.directory).save(second) == history
    assert history.read_bytes() == original_history
    assert store.latest_path.read_bytes() == original_history


def test_transient_windows_sharing_violation_retries_atomically(tmp_path, monkeypatch):
    store = ResearchProjectionStore(tmp_path / "projection")
    first, second = projection("CCYCLE-ONE"), projection("CCYCLE-TWO")
    store.save(first)
    real_replace = os.replace
    attempts = 0

    def transient_replace(source, destination):
        nonlocal attempts
        if Path(destination) == store.latest_path:
            attempts += 1
            if attempts < 3:
                error = PermissionError(13, "injected sharing violation", str(destination))
                error.winerror = 32
                raise error
        return real_replace(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", transient_replace)
        store.save(second)
    assert attempts == 3
    history = store.history_directory / (second["projection_version"] + ".json")
    assert store.latest_path.read_bytes() == history.read_bytes()


def test_persistent_replace_failure_reports_underlying_os_error(tmp_path, monkeypatch):
    store = ResearchProjectionStore(tmp_path / "projection")
    value = projection("CCYCLE-ONE")
    real_replace = os.replace

    def disk_error(source, destination):
        if Path(destination) == store.latest_path:
            raise OSError(28, "injected disk full", str(destination))
        return real_replace(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", disk_error)
        with pytest.raises(ResearchProjectionError) as caught:
            store.save(value)
    assert isinstance(caught.value.__cause__, OSError)
    assert "injected disk full" in str(caught.value)
    assert str(store.latest_path) in str(caught.value)
    assert not store.latest_path.exists()
    assert not store.latest_path.with_name("latest.json.tmp").exists()


def test_persistent_windows_sharing_violation_has_bounded_retries(tmp_path, monkeypatch):
    store = ResearchProjectionStore(tmp_path / "projection")
    store.save(projection("CCYCLE-ONE"))
    previous_pointer = store.latest_path.read_bytes()
    attempts = 0
    real_replace = os.replace

    def always_locked(source, destination):
        nonlocal attempts
        if Path(destination) == store.latest_path:
            attempts += 1
            error = PermissionError(13, "injected persistent lock", str(destination))
            error.winerror = 32
            raise error
        return real_replace(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", always_locked)
        with pytest.raises(ResearchProjectionError) as caught:
            store.save(projection("CCYCLE-TWO"))
    assert attempts == 8
    assert isinstance(caught.value.__cause__, PermissionError)
    assert "injected persistent lock" in str(caught.value)
    assert store.latest_path.read_bytes() == previous_pointer
    assert not store.latest_path.with_name("latest.json.tmp").exists()
