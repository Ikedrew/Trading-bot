"""Small offline regression checks for the optional cadence isolation seam."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from research_engine.v10.continuous import research_cadence as cadence
from research_engine.v10.continuous import research_loop as loop
from research_engine.v10.continuous import production_validation


@pytest.fixture
def capture_cycle(monkeypatch):
    calls = []
    monkeypatch.setattr(loop, "_production_evaluator_registry", lambda: None)
    monkeypatch.setattr(production_validation, "production_executors",
                        lambda *, state_root: (None, None))
    monkeypatch.setattr(loop, "run_continuous_research_cycle",
                        lambda **kwargs: (calls.append(kwargs)
                                          or SimpleNamespace(cycle_outcome="CYCLE_OK")))
    monkeypatch.setattr(cadence, "_pending_deep_count", lambda root: 0)
    return calls


@pytest.mark.parametrize("diagnostic", [False, True])
def test_absent_bundle_preserves_default_and_diagnostic_calls(
        tmp_path, capture_cycle, diagnostic):
    cache = tmp_path / "cache"
    report = cadence.run_cadence_tick(
        state_root=tmp_path / "state", force_fast_cycle=True,
        frontier_diagnostic=diagnostic,
        frontier_diagnostic_cache_directory=cache if diagnostic else None,
        offline_isolation=None)
    assert report.fast_cycle_outcome == "CYCLE_OK"
    assert len(capture_cycle) == 1
    options = capture_cycle[0]
    assert options["state_root"] == tmp_path / "state"
    assert set(options) == ({"state_root", "q71_evaluator_registry",
                             "validation_executor", "forward_executor"}
                            | ({"frontier_diagnostic",
                                "frontier_diagnostic_cache_directory"}
                               if diagnostic else set()))
    if diagnostic:
        assert options["frontier_diagnostic"] is True
        assert options["frontier_diagnostic_cache_directory"] == cache


def test_valid_bundle_is_forwarded_without_changing_owned_arguments(
        tmp_path, capture_cycle):
    source = object()
    bundle = {"frontier_kwargs": {"source": source},
              "question_kwargs": {"source": source}}
    report = cadence.run_cadence_tick(
        state_root=tmp_path / "state", force_fast_cycle=True,
        offline_isolation=bundle)
    assert report.fast_cycle_outcome == "CYCLE_OK"
    assert capture_cycle[0]["frontier_kwargs"] == bundle["frontier_kwargs"]
    assert capture_cycle[0]["question_kwargs"] == bundle["question_kwargs"]
    assert capture_cycle[0]["state_root"] == tmp_path / "state"


def test_invalid_bundle_rejects_owned_argument_before_cycle(
        tmp_path, capture_cycle):
    with pytest.raises(cadence.ResearchCadenceError,
                       match="OFFLINE_ISOLATION_OWNED_ARGUMENT"):
        cadence.run_cadence_tick(
            state_root=tmp_path / "state", force_fast_cycle=True,
            offline_isolation={"state_root": tmp_path / "other"})
    assert capture_cycle == []
