"""Wave 6.1B — persisted candidate impact history (SMOKE).

Recovery scope: ONE minimal passing smoke test proving that an exact persisted
VERIFIED N -> N+1 transition can be reconstructed from Wave 5 truth via
research_engine.lifecycle.candidate_impact_history.reconstruct_verified_transition.

Fixture reuse: this deliberately reuses the SINGLE proven Wave 5 fixture
architecture from tests/test_wave5_application_service.py — the ``env`` fixture
and its ``PersistentFake`` adapter using the {"kind": "wave5_fake_policy"}
effective state. No new fixture architecture is invented here, and no adapter
approaches are mixed.

The full A-O suite is NOT built in this recovery step.
"""

from __future__ import annotations

import json

# Reuse the proven Wave 5 fixture + adapter EXACTLY (no re-invention).
# Importing ``env`` registers the pytest fixture in this module's namespace.
from tests.test_wave5_application_service import PersistentFake, env  # noqa: F401

from research_engine.lifecycle.candidate_impact_history import (
    VerifiedBaselineTransition,
    reconstruct_verified_transition,
)


def _verified_env(env):
    """Drive the proven Wave 5 flow to a persisted VERIFIED transition.

    Returns (application_record, deployment_operation). This is legitimate
    persisted truth: approval -> governed execute() -> VERIFIED ledger row +
    COMPLETED fsynced operation file.
    """
    application = env.approve()
    operation = env.service().execute(env.app_id)
    assert operation["phase"] == "COMPLETED"
    assert [r.state for r in env.ledger.list_all()] == [
        "APPROVED_NOT_DEPLOYED", "DEPLOYED", "VERIFIED"
    ]
    return application, operation


def test_reconstructs_exact_verified_transition(env):
    application, operation = _verified_env(env)

    transition = reconstruct_verified_transition(
        env.app_id,
        application_path=env.root / "applications.jsonl",
        operations_dir=env.root / "operations",
    )

    assert isinstance(transition, VerifiedBaselineTransition)

    # from-baseline is the exact persisted starting baseline (OLD / old-config),
    # taken from the VERIFIED ledger row and cross-checked against the
    # operation's old_snapshot.
    assert transition.from_baseline_id == "OLD"
    assert transition.from_baseline_config_hash == "old-config"
    assert transition.from_baseline_id == operation["old_snapshot"]["snapshot_id"]
    assert transition.from_baseline_config_hash == operation["old_snapshot"]["config_hash"]

    # to-baseline is the exact persisted verified next baseline from the
    # operation snapshot — NOT the active-baseline pointer.
    assert transition.to_baseline_id == operation["snapshot"]["snapshot_id"]
    assert transition.to_baseline_config_hash == operation["snapshot"]["config_hash"]
    assert transition.to_baseline_id != transition.from_baseline_id

    # application identity is exact.
    assert transition.application_id == env.app_id
    assert transition.operation_id == operation["operation_id"]

    # deployed treatment identity + frozen persisted spec are exact truth.
    assert transition.deployed_treatment_id == "historical-treatment"
    assert transition.deployed_treatment_spec == application.treatment_spec
    spec = json.loads(transition.deployed_treatment_spec)
    assert spec["treatment_id"] == "historical-treatment"
    assert spec["scope"]["symbols"] == ["EURUSD"]

    # The active-baseline pointer is NOT used as transition proof: mutating the
    # active pointer to an unrelated baseline must not change the reconstructed
    # transition (it is derived from persisted ledger + operation only).
    from research_engine.v10.baselines import baseline_authority as authority
    from research_engine.v10.baselines.models import BaselineSnapshot

    env.reg.save(BaselineSnapshot(snapshot_id="UNRELATED", config_hash="unrelated"))
    authority.set_active("UNRELATED", actor="test", reason="pointer must not be proof")

    reconstructed_again = reconstruct_verified_transition(
        env.app_id,
        application_path=env.root / "applications.jsonl",
        operations_dir=env.root / "operations",
    )
    assert reconstructed_again.to_dict() == transition.to_dict()
    assert reconstructed_again.to_baseline_id != "UNRELATED"


# ─── Wave 6.1B Step 2: historical candidate identity + N-era eligibility ──────
#
# These reuse the SAME proven Wave 5 ``env`` fixture. The fixture already
# persists legitimate historical truth: candidate C1 bound to baseline OLD with
# a frozen evaluation (C1.jsonl) carrying baseline_id=OLD, config_hash=old-config,
# treatment_id=historical-treatment and a frozen canonical treatment_spec.

from pathlib import Path

import pytest

from research_engine.lifecycle.candidate_impact_history import (
    CandidateBaselineMismatchError,
    CandidateHistoricalIdentity,
    CandidateHistoricalProvenanceError,
    _ensure_from_baseline_eligibility,
    reconstruct_verified_transition as _reconstruct,
    resolve_candidate_historical_identity,
)
from research_engine.v10.candidates.candidate_registry import CandidateRegistry


def _registry_dir(env) -> str:
    return env.dirs["registry_dir"]


def _evaluations_dir(env) -> Path:
    # The Wave 5 fixture monkeypatches the evaluation bridge dir to <root>/evaluations
    # and writes the frozen C1.jsonl there.
    return env.root / "evaluations"


def _resolve_c1(env) -> CandidateHistoricalIdentity:
    return resolve_candidate_historical_identity(
        "C1", registry_dir=_registry_dir(env), evaluations_dir=_evaluations_dir(env),
    )


def _rewrite_single_jsonl(path: Path, **changes) -> None:
    """Rewrite the single-record JSONL row at *path* with *changes* applied."""
    records = [json.loads(line) for line in
               path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(records) == 1, f"expected exactly one row in {path}"
    records[0].update(changes)
    path.write_text(json.dumps(records[0]) + "\n", encoding="utf-8")


def _transition(env):
    return _reconstruct(
        env.app_id,
        application_path=env.root / "applications.jsonl",
        operations_dir=env.root / "operations",
    )


# 1. Historical candidate provenance resolves exactly from persisted authorities.
def test_historical_candidate_provenance_resolves_exactly(env):
    env.approve()
    identity = _resolve_c1(env)
    assert isinstance(identity, CandidateHistoricalIdentity)
    assert identity.candidate_id == "C1"
    assert identity.baseline_id == "OLD"
    assert identity.baseline_config_hash == "old-config"
    assert identity.treatment_id == "historical-treatment"
    # Frozen treatment_spec is the exact persisted canonical spec.
    spec = json.loads(identity.treatment_spec)
    assert spec["treatment_id"] == "historical-treatment"
    assert spec["scope"]["symbols"] == ["EURUSD"]


# 2. Mutable CandidateRecord.change_definition is NOT the historical authority.
def test_mutable_change_definition_is_not_authority(env):
    env.approve()
    before = _resolve_c1(env)

    # Deliberately mutate the persisted mutable change_definition AFTER the
    # frozen evaluation already exists — including a conflicting treatment_id
    # and a different scope. Historical resolution must ignore it entirely.
    registry = CandidateRegistry(_registry_dir(env))
    record = registry.get("C1")
    record.change_definition = {
        "type": "geometry_modification",
        "stop_multiplier": 9,
        "treatment_id": "MUTATED-NOT-AUTHORITY",
        "scope": {"symbols": ["GBPUSD"], "patterns": ["FAKE"]},
        "baseline_config_hash": "mutated-hash",
    }
    registry._persist()

    after = resolve_candidate_historical_identity(
        "C1", registry_dir=_registry_dir(env), evaluations_dir=_evaluations_dir(env),
    )
    # Unchanged: historical identity comes from the frozen evaluation, never
    # from the mutated change_definition.
    assert after == before
    assert after.treatment_id == "historical-treatment"
    assert after.baseline_config_hash == "old-config"
    assert json.loads(after.treatment_spec)["scope"]["symbols"] == ["EURUSD"]


# 3. Correct eligibility: Candidate @ N + verified transition N -> N+1 is eligible.
def test_candidate_at_N_is_eligible_for_transition_from_N(env):
    application, operation = _verified_env(env)
    transition = _transition(env)
    candidate = _resolve_c1(env)

    assert candidate.baseline_id == transition.from_baseline_id == "OLD"
    assert candidate.baseline_config_hash == transition.from_baseline_config_hash == "old-config"
    # Eligibility passes (returns None, raises nothing).
    assert _ensure_from_baseline_eligibility(candidate, transition) is None


# 4. Wrong baseline: candidate bound to a different baseline fails closed.
def test_wrong_baseline_fails_closed(env):
    _verified_env(env)
    transition = _transition(env)
    # Rebind the candidate's persisted registry baseline AND its frozen
    # evaluation baseline to a DIFFERENT baseline (so provenance resolves but
    # the from-baseline eligibility must fail).
    _rewrite_single_jsonl(
        Path(_registry_dir(env)) / "candidates.jsonl", baseline_id="OTHER")
    _rewrite_single_jsonl(
        _evaluations_dir(env) / "C1.jsonl", baseline_id="OTHER", config_hash="old-config")

    candidate = _resolve_c1(env)
    assert candidate.baseline_id == "OTHER"
    with pytest.raises(CandidateBaselineMismatchError):
        _ensure_from_baseline_eligibility(candidate, transition)


# 5. Config mismatch: same baseline_id == N but historical config hash != N's.
def test_config_hash_mismatch_fails_closed(env):
    _verified_env(env)
    transition = _transition(env)
    # baseline_id stays OLD (matches registry binding) but the historical
    # config hash drifts away from the transition from-baseline config hash.
    _rewrite_single_jsonl(
        _evaluations_dir(env) / "C1.jsonl", baseline_id="OLD", config_hash="drifted-config")

    candidate = _resolve_c1(env)
    assert candidate.baseline_id == transition.from_baseline_id == "OLD"
    assert candidate.baseline_config_hash == "drifted-config"
    assert candidate.baseline_config_hash != transition.from_baseline_config_hash
    with pytest.raises(CandidateBaselineMismatchError):
        _ensure_from_baseline_eligibility(candidate, transition)


# 6. Ambiguous / missing frozen provenance fails closed (no change_definition fallback).
def test_missing_frozen_provenance_fails_closed(env):
    env.approve()
    # Strip the frozen treatment provenance from the persisted evaluation row.
    path = _evaluations_dir(env) / "C1.jsonl"
    records = [json.loads(line) for line in
               path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(records) == 1
    records[0].pop("treatment_id", None)
    records[0].pop("treatment_spec", None)
    path.write_text(json.dumps(records[0]) + "\n", encoding="utf-8")

    with pytest.raises(CandidateHistoricalProvenanceError):
        _resolve_c1(env)


def test_ambiguous_frozen_provenance_fails_closed(env):
    env.approve()
    # Append a SECOND evaluation row with a conflicting frozen identity tuple.
    path = _evaluations_dir(env) / "C1.jsonl"
    first = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    conflicting = dict(first, treatment_id="a-different-treatment")
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(conflicting) + "\n")

    with pytest.raises(CandidateHistoricalProvenanceError):
        _resolve_c1(env)


# 7. Historical identity remains historical: advancing the active baseline must
#    NOT rebind Candidate @ N into Candidate @ N+1.
def test_historical_identity_is_not_rebound_by_active_advance(env):
    application, operation = _verified_env(env)
    before = _resolve_c1(env)

    from research_engine.v10.baselines import baseline_authority as authority
    from research_engine.v10.baselines.models import BaselineSnapshot

    # The active baseline advances (as it does after a real verified deploy) to
    # the NEW baseline N+1. This must not alter the candidate's historical N-era
    # identity.
    next_baseline_id = operation["snapshot"]["snapshot_id"]
    authority.set_active(next_baseline_id, actor="test", reason="baseline advanced")

    after = _resolve_c1(env)
    assert after == before
    assert after.baseline_id == "OLD"
    assert after.baseline_config_hash == "old-config"
    # Candidate @ N is NOT reinterpreted as Candidate @ N+1.
    assert after.baseline_id != next_baseline_id


# ─── Wave 6.1B Step 3: impact identity + durable append-only history store ────
#
# Identity/record/store behaviour ONLY. assess_candidate_impact end-to-end is
# NOT exercised here. No evidence/lifecycle/baseline/production concerns.

from research_engine.lifecycle.candidate_impact_history import (
    CandidateBaselineImpactRecord,
    CandidateImpactHistoryStore,
    ImpactConflictError,
    compute_impact_id,
)


# One exact historical (candidate @ N, verified N -> N+1) identity tuple. These
# are the full set of fields compute_impact_id binds; nothing else may affect id.
_IDENTITY = dict(
    candidate_id="C1",
    candidate_baseline_id="OLD",
    candidate_baseline_config_hash="old-config",
    from_baseline_id="OLD",
    from_baseline_config_hash="old-config",
    to_baseline_id="NEW",
    to_baseline_config_hash="new-config",
    application_id="APP-C1-REC-E1",
    candidate_treatment_id="historical-treatment",
    deployed_treatment_id="historical-treatment",
)


def _make_record(impact_id=None, *, classification="NO_OVERLAP",
                 reason_codes=("disjoint_symbols",),
                 candidate_scope=None, deployed_scope=None, **identity_overrides):
    identity = dict(_IDENTITY, **identity_overrides)
    iid = impact_id if impact_id is not None else compute_impact_id(**identity)
    if candidate_scope is None:
        candidate_scope = {"symbols": ["EURUSD"], "patterns": None}
    if deployed_scope is None:
        deployed_scope = {"symbols": ["GBPUSD"], "patterns": None}
    return CandidateBaselineImpactRecord(
        impact_id=iid,
        candidate_id=identity["candidate_id"],
        candidate_baseline_id=identity["candidate_baseline_id"],
        candidate_baseline_config_hash=identity["candidate_baseline_config_hash"],
        from_baseline_id=identity["from_baseline_id"],
        from_baseline_config_hash=identity["from_baseline_config_hash"],
        to_baseline_id=identity["to_baseline_id"],
        to_baseline_config_hash=identity["to_baseline_config_hash"],
        application_id=identity["application_id"],
        candidate_treatment_id=identity["candidate_treatment_id"],
        deployed_treatment_id=identity["deployed_treatment_id"],
        classification=classification,
        reason_codes=tuple(reason_codes),
        candidate_scope=candidate_scope,
        deployed_scope=deployed_scope,
    )


# TEST 1 — deterministic impact identity (no timestamp/randomness/active state).
def test_compute_impact_id_is_deterministic():
    a = compute_impact_id(**_IDENTITY)
    b = compute_impact_id(**dict(_IDENTITY))  # fresh dict, same values
    assert a == b
    assert isinstance(a, str) and a.startswith("CBI-")

    # Field ORDER of kwargs must not affect the identity (canonical JSON).
    reordered = {k: _IDENTITY[k] for k in reversed(list(_IDENTITY))}
    assert compute_impact_id(**reordered) == a

    # No wall-clock / randomness / active-baseline input exists — recompute is stable.
    from research_engine.v10.baselines import baseline_authority as authority
    from research_engine.v10.baselines.models import BaselineSnapshot
    # (No env fixture here; identity is a pure function of the passed tuple.)
    assert compute_impact_id(**_IDENTITY) == a


# TEST 2 — distinct candidates -> distinct impact IDs (same transition).
def test_distinct_candidates_distinct_ids():
    base = compute_impact_id(**_IDENTITY)
    other = compute_impact_id(**dict(_IDENTITY, candidate_id="C2"))
    other_tid = compute_impact_id(**dict(_IDENTITY, candidate_treatment_id="other-treatment"))
    assert other != base
    assert other_tid != base


# TEST 3 — distinct transitions -> distinct impact IDs (same candidate identity).
def test_distinct_transitions_distinct_ids():
    base = compute_impact_id(**_IDENTITY)
    diff_to = compute_impact_id(**dict(_IDENTITY, to_baseline_id="NEWER", to_baseline_config_hash="newer-config"))
    diff_app = compute_impact_id(**dict(_IDENTITY, application_id="APP-OTHER"))
    diff_deployed = compute_impact_id(**dict(_IDENTITY, deployed_treatment_id="other-deployed"))
    assert diff_to != base
    assert diff_app != base
    assert diff_deployed != base


def test_compute_impact_id_validates_fields():
    # Missing a required field fails closed.
    incomplete = dict(_IDENTITY)
    incomplete.pop("application_id")
    with pytest.raises(ValueError):
        compute_impact_id(**incomplete)
    # Extra field fails closed.
    with pytest.raises(ValueError):
        compute_impact_id(**dict(_IDENTITY, unexpected="x"))
    # Empty required value fails closed.
    with pytest.raises(ValueError):
        compute_impact_id(**dict(_IDENTITY, to_baseline_id=""))


# TEST 4 — record round-trip preserves all fields and meaning.
def test_record_round_trip_preserves_everything():
    record = _make_record(
        classification="SCOPE_OVERLAP",
        reason_codes=("symbol_overlap", "pattern_indeterminate"),
        candidate_scope={"symbols": ["EURUSD", "GBPUSD"], "patterns": ["TBC"]},
        deployed_scope={"symbols": ["GBPUSD"], "patterns": None},
    )
    restored = CandidateBaselineImpactRecord.from_dict(record.to_dict())
    assert restored == record
    d = record.to_dict()
    for field in (
        "impact_id", "candidate_id", "candidate_baseline_id",
        "candidate_baseline_config_hash", "from_baseline_id",
        "from_baseline_config_hash", "to_baseline_id", "to_baseline_config_hash",
        "application_id", "candidate_treatment_id", "deployed_treatment_id",
        "classification",
    ):
        assert getattr(restored, field) == d[field]
    assert restored.reason_codes == ("symbol_overlap", "pattern_indeterminate")
    assert restored.candidate_scope == {"symbols": ["EURUSD", "GBPUSD"], "patterns": ["TBC"]}
    assert restored.deployed_scope == {"symbols": ["GBPUSD"], "patterns": None}
    # canonical_json is a stable, order-independent serialization.
    assert CandidateBaselineImpactRecord.from_dict(
        json.loads(record.canonical_json())) == record


# TEST 5 — append + reload from a NEW store instance (restart survival).
def test_append_then_reload_from_new_store(tmp_path):
    store_dir = tmp_path / "impact"
    record = _make_record()
    store = CandidateImpactHistoryStore(store_dir)
    assert store.append(record) is True
    assert store.get(record.impact_id) == record

    # Brand-new instance from the same path reloads the exact historical record.
    reloaded = CandidateImpactHistoryStore(store_dir)
    got = reloaded.get(record.impact_id)
    assert got == record
    assert got.to_dict() == record.to_dict()
    assert [r.to_dict() for r in reloaded.list_all()] == [record.to_dict()]


# TEST 6 — idempotent duplicate append (one logical historical truth).
def test_idempotent_duplicate_append(tmp_path):
    store_dir = tmp_path / "impact"
    record = _make_record()
    store = CandidateImpactHistoryStore(store_dir)
    assert store.append(record) is True
    assert store.append(record) is False           # duplicate -> not newly appended
    # An equal-but-separate instance is also idempotent.
    assert store.append(_make_record()) is False
    assert len(store.list_all()) == 1

    reloaded = CandidateImpactHistoryStore(store_dir)
    assert len(reloaded.list_all()) == 1
    assert reloaded.get(record.impact_id) == record


# TEST 7 — conflicting content under same impact_id fails closed; original intact.
def test_conflict_fails_closed_and_preserves_original(tmp_path):
    store_dir = tmp_path / "impact"
    original = _make_record(classification="NO_OVERLAP", reason_codes=("disjoint_symbols",))
    store = CandidateImpactHistoryStore(store_dir)
    assert store.append(original) is True

    # Same deterministic impact_id, DIFFERENT persisted content.
    conflicting = _make_record(
        impact_id=original.impact_id,
        classification="SCOPE_OVERLAP",
        reason_codes=("symbol_overlap",),
    )
    assert conflicting.impact_id == original.impact_id
    with pytest.raises(ImpactConflictError):
        store.append(conflicting)

    # Original historical truth is unchanged in memory AND on disk.
    assert store.get(original.impact_id) == original
    assert len(store.list_all()) == 1
    reloaded = CandidateImpactHistoryStore(store_dir)
    assert reloaded.get(original.impact_id) == original
    assert reloaded.get(original.impact_id).classification == "NO_OVERLAP"


# TEST 8 — append-only history: earlier records are never overwritten.
def test_append_only_history_preserves_earlier_records(tmp_path):
    store_dir = tmp_path / "impact"
    store = CandidateImpactHistoryStore(store_dir)
    r1 = _make_record(candidate_id="C1")
    r2 = _make_record(candidate_id="C2")
    r3 = _make_record(to_baseline_id="NEWER", to_baseline_config_hash="newer-config")
    assert store.append(r1) is True
    first_after_r1 = store.get(r1.impact_id).to_dict()
    assert store.append(r2) is True
    assert store.append(r3) is True

    # r1 is byte/meaning stable after later appends.
    assert store.get(r1.impact_id).to_dict() == first_after_r1
    ids = {r.impact_id for r in store.list_all()}
    assert ids == {r1.impact_id, r2.impact_id, r3.impact_id}
    assert len({r1.impact_id, r2.impact_id, r3.impact_id}) == 3

    reloaded = CandidateImpactHistoryStore(store_dir)
    assert {r.impact_id for r in reloaded.list_all()} == ids
    assert reloaded.get(r1.impact_id).to_dict() == first_after_r1


# TEST 9 — active baseline state does not affect identity or persisted record.
def test_active_state_does_not_affect_identity_or_record(env, tmp_path):
    # Derive an identity/record BEFORE any active-baseline change.
    impact_id_before = compute_impact_id(**_IDENTITY)
    store_dir = tmp_path / "impact"
    store = CandidateImpactHistoryStore(store_dir)
    record = _make_record()
    assert store.append(record) is True

    # Advance the active baseline to an unrelated baseline.
    from research_engine.v10.baselines import baseline_authority as authority
    from research_engine.v10.baselines.models import BaselineSnapshot
    env.reg.save(BaselineSnapshot(snapshot_id="UNRELATED", config_hash="unrelated"))
    authority.set_active("UNRELATED", actor="test", reason="must not affect impact identity")

    # Identity recompute is unchanged; persisted record is unchanged.
    assert compute_impact_id(**_IDENTITY) == impact_id_before == record.impact_id
    reloaded = CandidateImpactHistoryStore(store_dir)
    assert reloaded.get(record.impact_id) == record


# ─── Wave 6.1B Step 4: end-to-end assess_candidate_impact composition ─────────
#
# Proves assess_candidate_impact composes the already-proven 6.1B pieces and
# delegates ALL classification science to the Wave 6.1A classifier. Uses the
# proven Wave 5 ``env`` fixture. No evidence/lifecycle/baseline/production
# mutation is permitted beyond the single impact-history record.

import research_engine.lifecycle.candidate_impact_history as history
from research_engine.lifecycle.candidate_impact_history import (
    assess_candidate_impact,
)
from research_engine.lifecycle.candidate_impact_classifier import (
    CandidateImpactResult,
    ImpactClassification,
    NormalizedScope,
    classify_candidate_impact,
)


def _assess(env, impact_dir, candidate_id="C1"):
    return assess_candidate_impact(
        candidate_id,
        env.app_id,
        registry_dir=env.dirs["registry_dir"],
        evaluations_dir=env.root / "evaluations",
        application_path=env.root / "applications.jsonl",
        operations_dir=env.root / "operations",
        impact_dir=impact_dir,
    )


def _persisted_state(env):
    """Snapshot every persisted Wave 5 authority as raw bytes for a strict
    before/after side-effect comparison (impact history is excluded)."""
    from research_engine.v10.baselines import baseline_authority as authority

    state: dict[str, object] = {}
    files = {
        "candidates": Path(env.dirs["registry_dir"]) / "candidates.jsonl",
        "evaluation": env.root / "evaluations" / "C1.jsonl",
        "decisions": Path(env.dirs["decisions_dir"]) / "decisions.jsonl",
        "recommendations": Path(env.dirs["recommendations_dir"]) / "recommendations.jsonl",
        "applications": env.root / "applications.jsonl",
        "pointer": env.adapter_path.parent.parent / "baselines" / "active_baseline.json",
        "adapter": env.adapter_path,
    }
    for name, path in files.items():
        state[name] = path.read_bytes() if path.is_file() else None
    ops_dir = env.root / "operations"
    state["operations"] = (
        {p.name: p.read_bytes() for p in sorted(ops_dir.glob("*.json"))}
        if ops_dir.is_dir() else {})
    baselines_dir = env.root / "baselines"
    state["baseline_snapshots"] = (
        {p.name: p.read_bytes() for p in sorted(baselines_dir.glob("*.json"))}
        if baselines_dir.is_dir() else {})
    state["active_baseline_id"] = authority.get_active().active_baseline_id
    return state


# TEST 1 — end-to-end success composes all proven pieces.
def test_assess_candidate_impact_end_to_end_success(env, tmp_path):
    application, operation = _verified_env(env)
    impact_dir = tmp_path / "impact"

    record = _assess(env, impact_dir)

    # (1) exact verified transition binding
    assert record.from_baseline_id == "OLD"
    assert record.from_baseline_config_hash == "old-config"
    assert record.to_baseline_id == operation["snapshot"]["snapshot_id"]
    assert record.to_baseline_config_hash == operation["snapshot"]["config_hash"]
    assert record.application_id == env.app_id
    # (2) exact historical Candidate @ N binding
    assert record.candidate_id == "C1"
    assert record.candidate_baseline_id == "OLD"
    assert record.candidate_baseline_config_hash == "old-config"
    assert record.candidate_treatment_id == "historical-treatment"
    assert record.deployed_treatment_id == "historical-treatment"
    # (4) classification is delegated to 6.1A — same treatment vs itself here.
    expected = classify_candidate_impact(
        "historical-treatment", application.treatment_spec,
        "historical-treatment", application.treatment_spec)
    assert record.classification == expected.classification.value
    assert record.reason_codes == expected.reason_codes
    # (6) deterministic impact_id
    assert record.impact_id == compute_impact_id(
        candidate_id="C1", candidate_baseline_id="OLD",
        candidate_baseline_config_hash="old-config",
        from_baseline_id="OLD", from_baseline_config_hash="old-config",
        to_baseline_id=record.to_baseline_id,
        to_baseline_config_hash=record.to_baseline_config_hash,
        application_id=env.app_id, candidate_treatment_id="historical-treatment",
        deployed_treatment_id="historical-treatment")
    # (7 + 8) persisted in the store and returned exactly.
    store = CandidateImpactHistoryStore(impact_dir)
    assert store.get(record.impact_id) == record
    assert [r.to_dict() for r in store.list_all()] == [record.to_dict()]


# TEST 2 — Wave 6.1A is the SOLE classifier authority.
def test_wave61a_is_sole_classifier_authority(env, tmp_path, monkeypatch):
    application, _ = _verified_env(env)
    impact_dir = tmp_path / "impact"

    calls = []
    sentinel = CandidateImpactResult(
        classification=ImpactClassification.PARTIALLY_AFFECTED,
        reason_codes=("PARTIAL_SCOPE_OVERLAP", "SAME_TREATMENT_TYPE"),
        candidate_treatment_id="historical-treatment",
        candidate_treatment_type="direction_inversion",
        deployed_treatment_id="historical-treatment",
        deployed_treatment_type="direction_inversion",
        candidate_scope=NormalizedScope(symbols=("EURUSD",), patterns=None),
        deployed_scope=NormalizedScope(symbols=("GBPUSD",), patterns=None),
    )

    def spy(cand_id, cand_spec, dep_id, dep_spec):
        calls.append((cand_id, cand_spec, dep_id, dep_spec))
        return sentinel

    # Patch at the boundary actually used by candidate_impact_history.py.
    monkeypatch.setattr(history, "classify_candidate_impact", spy)

    record = _assess(env, impact_dir)

    # Invoked with the FROZEN candidate + deployed provenance.
    assert len(calls) == 1
    cand_id, cand_spec, dep_id, dep_spec = calls[0]
    assert cand_id == "historical-treatment"
    assert dep_id == "historical-treatment"
    assert cand_spec == application.treatment_spec
    assert dep_spec == application.treatment_spec
    # Classification / reason_codes / scopes come FROM the 6.1A result.
    assert record.classification == "PARTIALLY_AFFECTED"
    assert record.reason_codes == ("PARTIAL_SCOPE_OVERLAP", "SAME_TREATMENT_TYPE")
    assert record.candidate_scope == {"symbols": ["EURUSD"], "patterns": None}
    assert record.deployed_scope == {"symbols": ["GBPUSD"], "patterns": None}


# TEST 3 — mutable change_definition is irrelevant end-to-end.
def test_change_definition_irrelevant_end_to_end(env, tmp_path):
    application, _ = _verified_env(env)
    impact_dir = tmp_path / "impact"
    baseline_record = _assess(env, impact_dir)

    # Mutate the mutable change_definition AFTER frozen provenance exists.
    registry = CandidateRegistry(env.dirs["registry_dir"])
    rec = registry.get("C1")
    rec.change_definition = {
        "type": "geometry_modification", "stop_multiplier": 9,
        "treatment_id": "MUTATED", "scope": {"symbols": ["GBPUSD"]},
    }
    registry._persist()

    impact_dir2 = tmp_path / "impact2"
    after = _assess(env, impact_dir2)
    # Same historical truth -> identical impact identity and content.
    assert after.impact_id == baseline_record.impact_id
    assert after.to_dict() == baseline_record.to_dict()
    assert after.candidate_treatment_id == "historical-treatment"
    assert after.candidate_scope == {"symbols": ["EURUSD"], "patterns": None}


# TEST 4 — idempotent reassessment.
def test_idempotent_reassessment(env, tmp_path):
    _verified_env(env)
    impact_dir = tmp_path / "impact"
    first = _assess(env, impact_dir)
    second = _assess(env, impact_dir)
    assert first.impact_id == second.impact_id
    assert first.to_dict() == second.to_dict()
    store = CandidateImpactHistoryStore(impact_dir)
    assert len(store.list_all()) == 1
    assert store.get(first.impact_id) == first


# TEST 5 — wrong baseline fails BEFORE persistence.
def test_wrong_baseline_fails_before_persistence(env, tmp_path):
    _verified_env(env)
    impact_dir = tmp_path / "impact"
    # Rebind candidate historical baseline to a DIFFERENT baseline (registry +
    # frozen evaluation), so eligibility fails against the OLD->NEW transition.
    _rewrite_single_jsonl(
        Path(env.dirs["registry_dir"]) / "candidates.jsonl", baseline_id="OTHER")
    _rewrite_single_jsonl(
        env.root / "evaluations" / "C1.jsonl", baseline_id="OTHER", config_hash="old-config")

    with pytest.raises(CandidateBaselineMismatchError):
        _assess(env, impact_dir)

    # No impact record persisted.
    assert not (Path(impact_dir) / "candidate_impact_history.jsonl").is_file()
    assert CandidateImpactHistoryStore(impact_dir).list_all() == []


# TEST 6 — INDETERMINATE from 6.1A is persisted faithfully.
def test_indeterminate_is_persisted_faithfully(env, tmp_path, monkeypatch):
    _verified_env(env)
    impact_dir = tmp_path / "impact"

    indeterminate = CandidateImpactResult(
        classification=ImpactClassification.INDETERMINATE,
        reason_codes=("MALFORMED_PROVENANCE",),
        candidate_treatment_id="historical-treatment",
        candidate_treatment_type="direction_inversion",
        deployed_treatment_id="historical-treatment",
        deployed_treatment_type="direction_inversion",
        candidate_scope=None,
        deployed_scope=None,
    )
    monkeypatch.setattr(history, "classify_candidate_impact",
                        lambda *a: indeterminate)

    record = _assess(env, impact_dir)
    # Persisted faithfully; not reinterpreted/promoted/reopened.
    assert record.classification == "INDETERMINATE"
    assert record.reason_codes == ("MALFORMED_PROVENANCE",)
    assert record.candidate_scope is None
    assert record.deployed_scope is None
    reloaded = CandidateImpactHistoryStore(impact_dir).get(record.impact_id)
    assert reloaded == record
    assert reloaded.classification == "INDETERMINATE"


# TEST 7 — two candidates, same transition -> two coexisting historical records.
def test_multiple_candidates_same_transition(env, tmp_path):
    _verified_env(env)
    impact_dir = tmp_path / "impact"

    # Create a SECOND legitimate historical candidate C2 bound to the same
    # from-baseline (OLD/old-config) with its own frozen evaluation provenance.
    from research_engine.lifecycle.treatment_provenance import canonical_spec
    from research_engine.v10.candidates.models import CandidateRecord
    registry = CandidateRegistry(env.dirs["registry_dir"])
    registry.create(CandidateRecord(
        candidate_id="C2", baseline_id="OLD", created_from_question="E2",
        status="READY_FOR_REVIEW",
        change_definition={"baseline_config_hash": "old-config"},
    ))
    c2_spec = canonical_spec({
        "change_type": "geometry_modification", "declared": {"stop_multiplier": 2.0},
        "scope": {"symbols": ["GBPUSD"], "patterns": None},
        "treatment_id": "c2-treatment",
    })
    c2_eval = {
        "candidate_id": "C2", "evaluation_id": "E2", "treatment_id": "c2-treatment",
        "baseline_id": "OLD", "config_hash": "old-config", "decision": "VALIDATED",
        "confidence": "HIGH", "eligible_pairs": 60, "survives_outlier_removal": True,
        "treatment_spec": c2_spec,
    }
    (env.root / "evaluations" / "C2.jsonl").write_text(
        json.dumps(c2_eval) + "\n", encoding="utf-8")

    r1 = _assess(env, impact_dir, candidate_id="C1")
    r2 = _assess(env, impact_dir, candidate_id="C2")
    assert r1.impact_id != r2.impact_id
    store = CandidateImpactHistoryStore(impact_dir)
    ids = {r.impact_id for r in store.list_all()}
    assert ids == {r1.impact_id, r2.impact_id}
    # Neither overwrites the other.
    assert store.get(r1.impact_id) == r1
    assert store.get(r2.impact_id) == r2


# TEST 8 — zero scientific/lifecycle side effects beyond the impact record.
def test_zero_scientific_or_lifecycle_side_effects(env, tmp_path):
    _verified_env(env)
    impact_dir = tmp_path / "impact"

    status_before = CandidateRegistry(env.dirs["registry_dir"]).get("C1").status
    before = _persisted_state(env)
    record = _assess(env, impact_dir)
    after = _persisted_state(env)

    # Every persisted Wave 5 authority is byte-identical afterward (this is the
    # decisive zero-side-effect proof: candidates.jsonl is in the snapshot).
    assert after == before
    # Candidate status/record UNCHANGED by the assessment (whatever the fixture
    # left it as — approval sets ACCEPTED; assessment must not alter it).
    candidate = CandidateRegistry(env.dirs["registry_dir"]).get("C1")
    assert candidate.status == status_before
    assert candidate.baseline_id == "OLD"
    # Active baseline unchanged by assessment.
    from research_engine.v10.baselines import baseline_authority as authority
    assert authority.get_active().active_baseline_id == before["active_baseline_id"]
    # The ONLY new scientific persistence is the impact record.
    store = CandidateImpactHistoryStore(impact_dir)
    assert [r.impact_id for r in store.list_all()] == [record.impact_id]


# ─── Wave 6.1B Final Sign-off: adversarial VERIFIED-transition fail-closed ────
#
# Each case corrupts exactly ONE persisted authority AFTER a legitimate VERIFIED
# deployment, then proves reconstruct_verified_transition fails closed with
# TransitionNotVerifiedError and that NO impact history and NO Wave 5 mutation
# results from the attempted (failed) reconstruction.

from research_engine.control_plane.application_service import digest as _op_digest
from research_engine.lifecycle.candidate_impact_history import (
    TransitionNotVerifiedError,
)


def _app_path(env) -> Path:
    return env.root / "applications.jsonl"


def _ops_dir(env) -> Path:
    return env.root / "operations"


def _op_path(env) -> Path:
    return _ops_dir(env) / (_op_digest(env.app_id) + ".json")


def _read_ledger_rows(env) -> list[dict]:
    return [json.loads(line) for line in
            _app_path(env).read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_ledger_rows(env, rows: list[dict]) -> None:
    _app_path(env).write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _read_op(env) -> dict:
    return json.loads(_op_path(env).read_text(encoding="utf-8"))


def _write_op(env, op: dict) -> None:
    _op_path(env).write_text(json.dumps(op), encoding="utf-8")


def _reconstruct_env(env):
    return _reconstruct(
        env.app_id,
        application_path=_app_path(env),
        operations_dir=_ops_dir(env),
    )


def _assert_fails_closed_no_side_effects(env, tmp_path):
    """Attempt reconstruction (and a full assess) and prove both fail closed
    with TransitionNotVerifiedError, leaving zero impact history."""
    impact_dir = tmp_path / "impact_corrupt"
    with pytest.raises(TransitionNotVerifiedError):
        _reconstruct_env(env)
    with pytest.raises(TransitionNotVerifiedError):
        assess_candidate_impact(
            "C1", env.app_id,
            registry_dir=env.dirs["registry_dir"],
            evaluations_dir=env.root / "evaluations",
            application_path=_app_path(env), operations_dir=_ops_dir(env),
            impact_dir=impact_dir,
        )
    assert not (Path(impact_dir) / "candidate_impact_history.jsonl").is_file()
    assert CandidateImpactHistoryStore(impact_dir).list_all() == []


# 1. no application history
def test_transition_fails_no_application_history(env, tmp_path):
    _verified_env(env)
    _app_path(env).write_text("", encoding="utf-8")
    _assert_fails_closed_no_side_effects(env, tmp_path)


# 2a. no VERIFIED row
def test_transition_fails_no_verified_row(env, tmp_path):
    _verified_env(env)
    rows = [r for r in _read_ledger_rows(env) if r["state"] != "VERIFIED"]
    _write_ledger_rows(env, rows)
    _assert_fails_closed_no_side_effects(env, tmp_path)


# 2b. duplicate VERIFIED row
def test_transition_fails_duplicate_verified_row(env, tmp_path):
    _verified_env(env)
    rows = _read_ledger_rows(env)
    verified = next(r for r in rows if r["state"] == "VERIFIED")
    rows.append(dict(verified))  # exact duplicate VERIFIED row
    _write_ledger_rows(env, rows)
    _assert_fails_closed_no_side_effects(env, tmp_path)


# 3. VERIFIED row missing a required identity/provenance field
@pytest.mark.parametrize("field", [
    "candidate_id", "recommendation_id", "evaluation_id", "treatment_id",
    "baseline_id", "baseline_config_hash",
])
def test_transition_fails_verified_missing_identity(env, tmp_path, field):
    _verified_env(env)
    rows = _read_ledger_rows(env)
    for r in rows:
        if r["state"] == "VERIFIED":
            r[field] = ""
    _write_ledger_rows(env, rows)
    _assert_fails_closed_no_side_effects(env, tmp_path)


# 4a. application-history rows disagree on immutable identity
def test_transition_fails_identity_conflict_across_rows(env, tmp_path):
    _verified_env(env)
    rows = _read_ledger_rows(env)
    for r in rows:
        if r["state"] == "DEPLOYED":
            r["treatment_id"] = "tampered-treatment"
    _write_ledger_rows(env, rows)
    _assert_fails_closed_no_side_effects(env, tmp_path)


# 4b. application-history rows disagree on frozen treatment provenance
def test_transition_fails_treatment_spec_conflict_across_rows(env, tmp_path):
    _verified_env(env)
    rows = _read_ledger_rows(env)
    for r in rows:
        if r["state"] == "APPROVED_NOT_DEPLOYED":
            r["treatment_spec"] = (r.get("treatment_spec") or "") + " "
    _write_ledger_rows(env, rows)
    _assert_fails_closed_no_side_effects(env, tmp_path)


# 5. missing APPROVED_NOT_DEPLOYED row
def test_transition_fails_missing_approved_row(env, tmp_path):
    _verified_env(env)
    rows = [r for r in _read_ledger_rows(env) if r["state"] != "APPROVED_NOT_DEPLOYED"]
    _write_ledger_rows(env, rows)
    _assert_fails_closed_no_side_effects(env, tmp_path)


# 6. deployed frozen treatment_spec invalid / not canonically validatable
def test_transition_fails_invalid_deployed_treatment_spec(env, tmp_path):
    _verified_env(env)
    rows = _read_ledger_rows(env)
    for r in rows:
        r["treatment_spec"] = "{not valid json"  # same on all rows (no row conflict)
    _write_ledger_rows(env, rows)
    _assert_fails_closed_no_side_effects(env, tmp_path)


# 7. persisted operation missing
def test_transition_fails_operation_missing(env, tmp_path):
    _verified_env(env)
    _op_path(env).unlink()
    _assert_fails_closed_no_side_effects(env, tmp_path)


# 8. operation phase not COMPLETED
def test_transition_fails_operation_not_completed(env, tmp_path):
    _verified_env(env)
    op = _read_op(env)
    op["phase"] = "APPLYING"
    _write_op(env, op)
    _assert_fails_closed_no_side_effects(env, tmp_path)


# 9. authorization drift: operation["application"] != persisted approved row
def test_transition_fails_authorization_drift(env, tmp_path):
    _verified_env(env)
    op = _read_op(env)
    op["application"]["treatment_id"] = "drifted-authorization"
    _write_op(env, op)
    _assert_fails_closed_no_side_effects(env, tmp_path)


# 10. operation_id != VERIFIED deployment_reference (foreign reference)
def test_transition_fails_foreign_deployment_reference(env, tmp_path):
    _verified_env(env)
    op = _read_op(env)
    op["operation_id"] = "FOREIGN-OPERATION"
    _write_op(env, op)
    _assert_fails_closed_no_side_effects(env, tmp_path)


# 11. verification evidence/digest mismatch vs VERIFIED ledger row
def test_transition_fails_verification_digest_mismatch(env, tmp_path):
    _verified_env(env)
    op = _read_op(env)
    op["verification"]["actual"] = {"tampered": True}
    _write_op(env, op)
    _assert_fails_closed_no_side_effects(env, tmp_path)


# 12. operation lacks old_snapshot
def test_transition_fails_missing_old_snapshot(env, tmp_path):
    _verified_env(env)
    op = _read_op(env)
    op.pop("old_snapshot", None)
    _write_op(env, op)
    _assert_fails_closed_no_side_effects(env, tmp_path)


# 13. operation lacks resulting snapshot
def test_transition_fails_missing_snapshot(env, tmp_path):
    _verified_env(env)
    op = _read_op(env)
    op.pop("snapshot", None)
    _write_op(env, op)
    _assert_fails_closed_no_side_effects(env, tmp_path)


# 14. old_snapshot identity/config != ledger from-baseline
@pytest.mark.parametrize("field", ["snapshot_id", "config_hash"])
def test_transition_fails_old_snapshot_mismatch(env, tmp_path, field):
    _verified_env(env)
    op = _read_op(env)
    op["old_snapshot"][field] = "mismatched-old"
    _write_op(env, op)
    _assert_fails_closed_no_side_effects(env, tmp_path)


# 15. resulting snapshot lacks snapshot_id or config_hash
@pytest.mark.parametrize("field", ["snapshot_id", "config_hash"])
def test_transition_fails_snapshot_missing_field(env, tmp_path, field):
    _verified_env(env)
    op = _read_op(env)
    op["snapshot"][field] = ""
    _write_op(env, op)
    _assert_fails_closed_no_side_effects(env, tmp_path)


# 16. resulting baseline identical to from-baseline (no genuine N -> N+1)
def test_transition_fails_no_genuine_transition(env, tmp_path):
    _verified_env(env)
    op = _read_op(env)
    # Force the to-baseline snapshot_id to equal the from-baseline id.
    op["snapshot"]["snapshot_id"] = op["old_snapshot"]["snapshot_id"]
    _write_op(env, op)
    _assert_fails_closed_no_side_effects(env, tmp_path)


# ─── Part 3: historical experiment reconstruction after restart ───────────────

def test_historical_experiment_reconstruction_after_restart(env, tmp_path):
    """Prove "What happened to Candidate X when production moved N -> N+1?" is
    answerable purely from persisted canonical stores after a simulated restart.

    No second experiment-history database: the answer is the persisted
    CandidateBaselineImpactRecord joined to the persisted frozen provenance and
    the persisted VERIFIED operation snapshots.
    """
    application, operation = _verified_env(env)
    impact_dir = tmp_path / "impact"
    written = _assess(env, impact_dir)

    # Simulate restart: advance the active pointer and rebuild every reader from
    # persisted paths ONLY (no in-memory carryover).
    from research_engine.v10.baselines import baseline_authority as authority
    from research_engine.v10.baselines.models import BaselineSnapshot
    env.reg.save(BaselineSnapshot(snapshot_id="LATER", config_hash="later"))
    authority.set_active("LATER", actor="test", reason="restart / pointer advanced")

    reloaded_record = CandidateImpactHistoryStore(impact_dir).get(written.impact_id)
    assert reloaded_record == written

    # The full historical answer is derivable from persisted truth.
    from research_engine.control_plane.application_ledger import (
        ApplicationLedger, ApplicationState,
    )
    verified_row = next(
        r for r in ApplicationLedger(_app_path(env)).list_all()
        if r.application_id == env.app_id and r.state == ApplicationState.VERIFIED)

    answer = {
        "candidate_id": reloaded_record.candidate_id,
        "candidate_baseline_id": reloaded_record.candidate_baseline_id,
        "candidate_baseline_config_hash": reloaded_record.candidate_baseline_config_hash,
        "frozen_candidate_treatment_id": reloaded_record.candidate_treatment_id,
        "frozen_deployed_treatment_id": reloaded_record.deployed_treatment_id,
        "application_id": reloaded_record.application_id,
        "from_baseline_id": reloaded_record.from_baseline_id,
        "from_baseline_config_hash": reloaded_record.from_baseline_config_hash,
        "to_baseline_id": reloaded_record.to_baseline_id,
        "to_baseline_config_hash": reloaded_record.to_baseline_config_hash,
        "classification": reloaded_record.classification,
        "reason_codes": reloaded_record.reason_codes,
        "candidate_scope": reloaded_record.candidate_scope,
        "deployed_scope": reloaded_record.deployed_scope,
    }

    assert answer["candidate_id"] == "C1"
    assert answer["candidate_baseline_id"] == "OLD"
    assert answer["candidate_baseline_config_hash"] == "old-config"
    assert answer["frozen_candidate_treatment_id"] == "historical-treatment"
    assert answer["frozen_deployed_treatment_id"] == verified_row.treatment_id
    assert answer["application_id"] == env.app_id
    assert answer["from_baseline_id"] == "OLD"
    assert answer["from_baseline_config_hash"] == "old-config"
    assert answer["to_baseline_id"] == operation["snapshot"]["snapshot_id"]
    assert answer["to_baseline_config_hash"] == operation["snapshot"]["config_hash"]
    # Classification is 6.1A truth, not recomputed by this test.
    assert answer["classification"] in {
        "UNAFFECTED", "PARTIALLY_AFFECTED", "MATERIALLY_AFFECTED", "INDETERMINATE"}
    # Frozen candidate + deployed specs are still resolvable from persisted stores.
    hist = resolve_candidate_historical_identity(
        "C1", registry_dir=env.dirs["registry_dir"], evaluations_dir=env.root / "evaluations")
    assert json.loads(hist.treatment_spec)["treatment_id"] == "historical-treatment"
    assert json.loads(verified_row.treatment_spec)["treatment_id"] == verified_row.treatment_id
    # The reconstructed answer did NOT depend on the (advanced) active pointer.
    assert authority.get_active().active_baseline_id == "LATER"
    assert answer["to_baseline_id"] != "LATER"
