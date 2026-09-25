"""
Stage ③ / Wave 0 — Generated Research Identity & Frozen-70 Isolation.

Focused tests only. Scope:
    - deterministic semantic identity and deduplication;
    - timestamp independence of scientific identity;
    - immutable-field / conflicting-identity fail-closed behaviour;
    - persistence and restart-safe reload;
    - malformed persisted content fails closed;
    - no import-time writes;
    - mechanical proof that the canonical 70 remain frozen and uncontaminated.

No research, experiment, hypothesis, candidate or production behaviour is
executed by this suite.
"""

from __future__ import annotations

import importlib
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from research_engine.lifecycle.generated_research_identity import (
    GENERATED_RESEARCH_ID_PREFIX,
    GENERATED_RESEARCH_SCHEMA_VERSION,
    GENERATED_RESEARCH_SEMANTIC_VERSION,
    GeneratedResearchError,
    GeneratedResearchIdentityConflict,
    GeneratedResearchKind,
    GeneratedResearchNamespaceViolation,
    GeneratedResearchProposal,
    GeneratedResearchRecord,
    GeneratedResearchValidationError,
    canonical_json,
    generated_research_id_for,
    is_generated_research_id,
    semantic_identity_for,
)
from research_engine.lifecycle.generated_research_isolation import (
    CANONICAL_DEFINITION_VERSION,
    CANONICAL_QUESTION_COUNT,
    assert_canonical_70_intact,
    assert_generated_research_isolated,
    assert_generated_research_id_isolated,
    assert_no_generated_canonical_collision,
    assert_generated_not_in_canonical_apis,
    canonical_identity_snapshot,
    canonical_inventory,
)
from research_engine.lifecycle.generated_research_store import (
    STORE_FORMAT,
    GeneratedResearchStore,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _proposal(**overrides) -> GeneratedResearchProposal:
    base = dict(
        research_kind=GeneratedResearchKind.EXPANSION,
        trigger_ref="finding:FT-1",
        target_kind="PATTERN",
        target_ref="TWEEZER_TOP",
        specification={"population": "closed_shadow", "metric": "r_multiple"},
    )
    base.update(overrides)
    return GeneratedResearchProposal(**base)


@pytest.fixture
def store(tmp_path: Path) -> GeneratedResearchStore:
    return GeneratedResearchStore(tmp_path / "generated_research.json")


# ─── Versions / namespace ───────────────────────────────────────────────────


def test_wave0_versions_start_at_one_and_are_never_above_one():
    assert GENERATED_RESEARCH_SCHEMA_VERSION == 1
    assert GENERATED_RESEARCH_SEMANTIC_VERSION == 1


def test_generated_id_is_inside_reserved_namespace_and_outside_canonical():
    record = GeneratedResearchRecord.create(_proposal(), created_at="2026-01-01T00:00:00+00:00")
    assert record.generated_research_id.startswith(GENERATED_RESEARCH_ID_PREFIX)
    assert is_generated_research_id(record.generated_research_id)
    assert record.generated_research_id not in canonical_inventory()
    assert_generated_research_id_isolated(record.generated_research_id)


# ─── Deterministic semantic identity ────────────────────────────────────────


def test_semantic_identity_is_deterministic_across_equivalent_proposals():
    left = semantic_identity_for(_proposal())
    right = semantic_identity_for(_proposal())
    assert left == right
    assert generated_research_id_for(left) == generated_research_id_for(right)
    assert GeneratedResearchRecord.create(_proposal()).generated_research_id == \
        GeneratedResearchRecord.create(_proposal()).generated_research_id


def test_semantic_identity_is_independent_of_registration_order(tmp_path: Path):
    proposals = {
        "a": _proposal(target_ref="TWEEZER_TOP"),
        "b": _proposal(target_ref="MARUBOZU"),
        "c": _proposal(target_ref="ENGULFING"),
    }
    expected = {key: semantic_identity_for(value) for key, value in proposals.items()}
    assert len(set(expected.values())) == 3

    for order in (("a", "b", "c"), ("c", "a", "b"), ("b", "c", "a")):
        store = GeneratedResearchStore(tmp_path / f"order_{''.join(order)}.json")
        for key in order:
            store.register(proposals[key])
        assert {r.semantic_identity for r in store.all()} == set(expected.values())
        for key in order:
            assert store.get(generated_research_id_for(expected[key])) is not None


def test_timestamp_does_not_change_semantic_identity():
    early = GeneratedResearchRecord.create(_proposal(), created_at="2026-01-01T00:00:00+00:00")
    late = GeneratedResearchRecord.create(_proposal(), created_at="2030-12-31T23:59:59+00:00")
    assert early.semantic_identity == late.semantic_identity
    assert early.generated_research_id == late.generated_research_id
    assert early.created_at != late.created_at
    # The timestamp is provenance: it is absent from the identity material.
    assert "created_at" not in early.semantic_material()


@pytest.mark.parametrize("overrides", [
    {"research_kind": GeneratedResearchKind.INTERACTION},
    {"trigger_ref": "finding:FT-2"},
    {"target_kind": "STRATEGY"},
    {"target_ref": "MARUBOZU"},
    {"dimension_ref": "EXIT_POLICY"},
    {"parent_refs": ("hypothesis:H-7",)},
    {"specification": {"population": "closed_shadow", "metric": "mae_r"}},
    {"specification": {"population": "open_shadow", "metric": "r_multiple"}},
])
def test_materially_different_semantics_produce_different_identity(overrides):
    base = semantic_identity_for(_proposal())
    assert semantic_identity_for(_proposal(**overrides)) != base


def test_identity_does_not_depend_on_key_insertion_order():
    left = semantic_identity_for(_proposal(specification={"a": 1, "b": {"c": 2, "d": 3}}))
    right = semantic_identity_for(_proposal(specification={"b": {"d": 3, "c": 2}, "a": 1}))
    assert left == right


def test_parent_refs_are_order_independent_and_canonicalised():
    left = semantic_identity_for(_proposal(parent_refs=("finding:FT-9", "hypothesis:H-1")))
    right = semantic_identity_for(_proposal(parent_refs=("hypothesis:H-1", "finding:FT-9")))
    assert left == right
    record = GeneratedResearchRecord.create(
        _proposal(parent_refs=("hypothesis:H-1", "finding:FT-9")))
    assert record.parent_refs == ("finding:FT-9", "hypothesis:H-1")


def test_dimension_ref_is_explicitly_absent_in_wave0_not_invented():
    record = GeneratedResearchRecord.create(_proposal())
    assert record.dimension_ref is None
    assert record.semantic_material()["dimension_ref"] is None


# ─── Registration / dedup ───────────────────────────────────────────────────


def test_register_creates_identity_outside_the_canonical_70(store: GeneratedResearchStore):
    record = store.register(_proposal())
    assert is_generated_research_id(record.generated_research_id)
    assert record.generated_research_id not in canonical_inventory()
    assert len(store) == 1
    assert canonical_inventory().count(record.generated_research_id) == 0


def test_equivalent_registration_deduplicates_to_one_identity(store: GeneratedResearchStore):
    first = store.register(_proposal())
    second = store.register(_proposal())
    third = store.register(_proposal())
    assert first is second is third
    assert len(store) == 1
    # Re-discovery is not re-creation: the original creation timestamp survives.
    assert store.register(_proposal()).created_at == first.created_at


def test_registration_is_explicit_and_writes_only_on_registration(tmp_path: Path):
    path = tmp_path / "generated_research.json"
    store = GeneratedResearchStore(path)
    assert not path.exists(), "constructing a store must not write"
    assert len(store) == 0
    store.register(_proposal())
    assert path.exists()


def test_distinct_semantics_produce_distinct_records(store: GeneratedResearchStore):
    records = [
        store.register(_proposal()),
        store.register(_proposal(target_ref="MARUBOZU")),
        store.register(_proposal(research_kind=GeneratedResearchKind.INTERACTION)),
    ]
    assert len({r.semantic_identity for r in records}) == 3
    assert len({r.generated_research_id for r in records}) == 3
    assert len(store) == 3


# ─── Retrieval ──────────────────────────────────────────────────────────────


def test_retrieval_by_generated_research_id_and_by_semantic_identity(
    store: GeneratedResearchStore,
):
    record = store.register(_proposal())
    assert store.get(record.generated_research_id) == record
    assert store.get_by_semantic_identity(record.semantic_identity) == record
    assert record.generated_research_id in store
    assert store.get("GEN-0000000000000000") is None
    assert store.get_by_semantic_identity("0" * 64) is None


def test_retrieval_rejects_non_string_lookups(store: GeneratedResearchStore):
    with pytest.raises(GeneratedResearchValidationError):
        store.get("")
    with pytest.raises(GeneratedResearchValidationError):
        store.get_by_semantic_identity("")


# ─── Immutability / fail-closed ─────────────────────────────────────────────


def test_record_is_frozen(store: GeneratedResearchStore):
    record = store.register(_proposal())
    with pytest.raises(Exception):
        record.target_ref = "MARUBOZU"          # type: ignore[misc]
    with pytest.raises(Exception):
        record.semantic_identity = "0" * 64      # type: ignore[misc]


def test_mutated_semantic_material_fails_closed():
    record = GeneratedResearchRecord.create(_proposal())
    tampered = replace(record, target_ref="MARUBOZU")
    with pytest.raises(GeneratedResearchIdentityConflict):
        tampered.validate()


def test_same_id_with_conflicting_immutable_semantics_fails_closed(
    store: GeneratedResearchStore,
):
    record = store.register(_proposal())
    forged = replace(
        record,
        semantic_identity=record.semantic_identity,
        specification_json=canonical_json({"population": "open_shadow"}),
    )
    with pytest.raises(GeneratedResearchIdentityConflict):
        store.register_record(forged)


def test_generated_id_that_does_not_match_its_identity_fails_closed():
    record = GeneratedResearchRecord.create(_proposal())
    forged = replace(record, generated_research_id="GEN-0000000000000000")
    with pytest.raises(GeneratedResearchIdentityConflict):
        forged.validate()


def test_unknown_research_kind_fails_closed():
    with pytest.raises(GeneratedResearchValidationError):
        GeneratedResearchRecord.create(_proposal(research_kind="TELEPATHY"))


@pytest.mark.parametrize("overrides", [
    {"trigger_ref": "not-a-reference"},
    {"trigger_ref": ""},
    {"target_kind": ""},
    {"parent_refs": ("nope",)},
    {"parent_refs": ("finding:FT-1", "finding:FT-1")},
    {"dimension_ref": "not a token"},
])
def test_invalid_proposal_material_fails_closed(overrides):
    with pytest.raises(GeneratedResearchValidationError):
        GeneratedResearchRecord.create(_proposal(**overrides))


def test_non_canonical_encoded_payload_fails_closed():
    record = GeneratedResearchRecord.create(_proposal())
    with pytest.raises(GeneratedResearchValidationError):
        replace(record, specification_json='{"b": 1, "a": 2}').validate()


def test_schema_version_above_one_fails_closed():
    record = GeneratedResearchRecord.create(_proposal())
    with pytest.raises(GeneratedResearchValidationError):
        replace(record, schema_version=2).validate()
    with pytest.raises(GeneratedResearchValidationError):
        replace(record, semantic_version=2).validate()


# ─── Persistence / restart safety ───────────────────────────────────────────


def test_persistence_reload_preserves_identity(tmp_path: Path):
    path = tmp_path / "generated_research.json"
    first = GeneratedResearchStore(path)
    records = [
        first.register(_proposal()),
        first.register(_proposal(target_ref="MARUBOZU")),
        first.register(_proposal(research_kind=GeneratedResearchKind.INTERACTION)),
    ]
    payload_before = path.read_text(encoding="utf-8")

    # A brand-new process-level construction: restart-safe reload.
    reloaded = GeneratedResearchStore(path)
    assert len(reloaded) == len(records)
    for record in records:
        restored = reloaded.get(record.generated_research_id)
        assert restored == record
        assert restored.semantic_identity == record.semantic_identity
        assert restored.created_at == record.created_at
        assert reloaded.get_by_semantic_identity(record.semantic_identity) == record

    # Deterministic serialisation: an unchanged store re-serialises byte-identically.
    reloaded.register(_proposal())
    assert path.read_text(encoding="utf-8") == payload_before


def test_persisted_document_is_canonical_and_versioned(tmp_path: Path):
    path = tmp_path / "generated_research.json"
    store = GeneratedResearchStore(path)
    record = store.register(_proposal())
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["format"] == STORE_FORMAT
    assert document["schema_version"] == 1
    assert document["id_prefix"] == GENERATED_RESEARCH_ID_PREFIX
    assert len(document["records"]) == 1
    assert GeneratedResearchRecord.from_dict(document["records"][0]) == record
    # No temp file is left behind by the atomic write.
    assert not (tmp_path / "generated_research.json.tmp").exists()


def test_store_does_not_modify_persisted_content_on_duplicate(tmp_path: Path):
    path = tmp_path / "generated_research.json"
    store = GeneratedResearchStore(path)
    store.register(_proposal())
    payload_before = path.read_bytes()
    stamp_before = path.stat().st_mtime_ns

    store.register(_proposal())
    store.register(_proposal())

    assert path.read_bytes() == payload_before
    assert path.stat().st_mtime_ns == stamp_before


def _write_document(path: Path, document: object) -> None:
    path.write_text(json.dumps(document), encoding="utf-8")


def _valid_document(tmp_path: Path) -> dict:
    store = GeneratedResearchStore(tmp_path / "seed.json")
    store.register(_proposal())
    return json.loads((tmp_path / "seed.json").read_text(encoding="utf-8"))


def test_unreadable_store_fails_closed(tmp_path: Path):
    path = tmp_path / "generated_research.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(GeneratedResearchError):
        GeneratedResearchStore(path)


def test_wrong_store_format_fails_closed(tmp_path: Path):
    document = _valid_document(tmp_path)
    document["format"] = "something_else"
    path = tmp_path / "generated_research.json"
    _write_document(path, document)
    with pytest.raises(GeneratedResearchError):
        GeneratedResearchStore(path)


def test_future_store_schema_version_fails_closed(tmp_path: Path):
    document = _valid_document(tmp_path)
    document["schema_version"] = 2
    path = tmp_path / "generated_research.json"
    _write_document(path, document)
    with pytest.raises(GeneratedResearchValidationError):
        GeneratedResearchStore(path)


@pytest.mark.parametrize("mutate", [
    pytest.param(lambda d: d["records"][0].pop("target_ref"), id="missing_field"),
    pytest.param(lambda d: d["records"][0].update({"unknown": 1}), id="unknown_field"),
    pytest.param(lambda d: d["records"][0].update({"semantic_identity": "0" * 64}),
                 id="identity_drift"),
    pytest.param(lambda d: d["records"][0].update({"research_kind": "TELEPATHY"}),
                 id="unknown_kind"),
    pytest.param(lambda d: d["records"][0].update({"specification_json": "{oops"}),
                 id="unparseable_spec"),
    pytest.param(lambda d: d["records"][0].update({"created_at": ""}), id="no_timestamp"),
])
def test_malformed_persisted_record_fails_closed(tmp_path: Path, mutate):
    document = _valid_document(tmp_path)
    mutate(document)
    path = tmp_path / "generated_research.json"
    _write_document(path, document)
    with pytest.raises(GeneratedResearchError):
        GeneratedResearchStore(path)


def test_duplicate_id_in_persisted_store_fails_closed(tmp_path: Path):
    document = _valid_document(tmp_path)
    document["records"].append(dict(document["records"][0]))
    path = tmp_path / "generated_research.json"
    _write_document(path, document)
    with pytest.raises(GeneratedResearchIdentityConflict):
        GeneratedResearchStore(path)


def test_persisted_record_with_id_disagreeing_with_its_identity_fails_closed(tmp_path: Path):
    """Defensive: a cloned row cannot re-bind one identity to two generated IDs."""
    document = _valid_document(tmp_path)
    clone = dict(document["records"][0])
    clone["generated_research_id"] = "GEN-0123456789ABCDEF"
    document["records"].append(clone)
    path = tmp_path / "generated_research.json"
    _write_document(path, document)
    with pytest.raises(GeneratedResearchIdentityConflict):
        GeneratedResearchStore(path)


# ─── No import-time writes ──────────────────────────────────────────────────


def test_importing_the_layer_writes_nothing(tmp_path: Path):
    """Import must be side-effect free: no records, no directories, no files."""
    script = (
        "import sys\n"
        f"sys.path.insert(0, {str(REPO_ROOT)!r})\n"
        "import research_engine.lifecycle.generated_research_identity\n"
        "import research_engine.lifecycle.generated_research_isolation\n"
        "import research_engine.lifecycle.generated_research_store\n"
        "print('imported')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stderr
    assert "imported" in result.stdout
    assert list(tmp_path.iterdir()) == [], "import must not create any file"


# ─── Frozen-70 isolation ────────────────────────────────────────────────────


def test_canonical_registry_is_exactly_seventy_and_unique():
    inventory = canonical_inventory()
    assert CANONICAL_QUESTION_COUNT == 70
    assert len(inventory) == 70
    assert len(set(inventory)) == 70


def test_canonical_ids_exactly_match_frozen_baseline_manifest():
    from research_engine.registry.baseline_manifest import BASELINE_QUESTION_IDS

    assert canonical_inventory() == tuple(BASELINE_QUESTION_IDS)
    assert len(BASELINE_QUESTION_IDS) == CANONICAL_QUESTION_COUNT


def test_canonical_definition_versions_all_remain_one():
    assert CANONICAL_DEFINITION_VERSION == 1
    assert set(canonical_identity_snapshot()["definition_versions"].values()) == {1}


def test_canonical_70_guard_passes_on_live_registry():
    assert_canonical_70_intact()


def test_registering_generated_research_does_not_alter_canonical_registry(
    tmp_path: Path,
):
    before = canonical_identity_snapshot()
    store = GeneratedResearchStore(tmp_path / "generated_research.json")
    for index, kind in enumerate(GeneratedResearchKind):
        store.register(_proposal(
            research_kind=kind,
            target_ref=f"PATTERN_{index}",
            trigger_ref=f"finding:FT-{index}",
        ))
    assert len(store) == len(GeneratedResearchKind)
    assert canonical_identity_snapshot() == before


def test_generated_ids_cannot_collide_with_any_canonical_id(
    tmp_path: Path,
):
    store = GeneratedResearchStore(tmp_path / "generated_research.json")
    records = [store.register(_proposal(target_ref=f"P{index}"))
               for index in range(5)]
    generated_ids = [record.generated_research_id for record in records]
    canonical = set(canonical_inventory())
    assert not (set(generated_ids) & canonical)
    assert_no_generated_canonical_collision(generated_ids)


@pytest.mark.parametrize("canonical_id", [
    "E1", "M4", "X6", "PORT-1", "OPP-1", "HORIZON-1", "Q71",
])
def test_canonical_style_id_cannot_pose_as_generated_research(canonical_id: str):
    with pytest.raises(GeneratedResearchNamespaceViolation):
        assert_generated_research_id_isolated(canonical_id)


def test_malformed_generated_id_is_rejected():
    for bad in ("", "gen-0123456789abcdef", "GEN-0123", "GEN-XYZ", "Q71"):
        with pytest.raises(GeneratedResearchNamespaceViolation):
            assert_generated_research_id_isolated(bad)


def test_generated_research_is_not_returned_by_canonical_apis(
    tmp_path: Path,
):
    from research_engine.registry.research_question_registry import get_question

    store = GeneratedResearchStore(tmp_path / "generated_research.json")
    generated_ids = [store.register(_proposal(target_ref=f"P{index}")).generated_research_id
                     for index in range(3)]

    assert_generated_not_in_canonical_apis(generated_ids)
    for generated_id in generated_ids:
        assert get_question(generated_id) is None
        assert get_question(generated_id.casefold()) is None


def test_full_isolation_guard_passes_for_live_generated_records(
    tmp_path: Path,
):
    store = GeneratedResearchStore(tmp_path / "generated_research.json")
    generated_ids = [store.register(_proposal(target_ref=f"P{index}")).generated_research_id
                     for index in range(4)]
    assert_generated_research_isolated(generated_ids)


def test_canonical_inventory_and_definition_authority_is_unchanged():
    """Existing canonical authority APIs remain intact and unaffected."""
    from research_engine.registry import (
        REGISTRY,
        build_definitions_from_registry,
        get_questions_by_category,
    )
    from research_engine.registry.inventory_guard import (
        canonical_questions,
        executable_canonical_questions,
    )
    from research_engine.registry.research_question_models import QuestionCategory

    assert len(REGISTRY) == 70
    assert len(canonical_questions()) == 70
    assert executable_canonical_questions()

    definitions = build_definitions_from_registry(REGISTRY)
    assert len(definitions) == 70
    assert {d.definition_version for d in definitions.values()} == {1}

    system_edge = get_questions_by_category(QuestionCategory.SYSTEM_EDGE)
    assert system_edge
    assert all(question.category is QuestionCategory.SYSTEM_EDGE for question in system_edge)


# ─── Zero production authority ──────────────────────────────────────────────


def test_generated_layer_has_no_production_or_execution_authority(tmp_path: Path):
    """
    Wave 0 executes nothing and reaches no production state.

    Registration produces a record and a JSON file. Nothing else: no runner, no
    experiment, no candidate, no baseline, no config, no runtime behaviour.
    """
    import inspect

    from research_engine.lifecycle import (
        generated_research_identity,
        generated_research_store,
    )

    store_module = inspect.getsource(generated_research_store)
    identity_module = inspect.getsource(generated_research_identity)

    forbidden = (
        "baseline_authority", "candidate_activation_gate", "governance_gate",
        "production_adapter", "research_cycle_runner", "orchestrator",
        "FindingTriggerEngine", "run_experiment", "apply_treatment",
    )
    for symbol in forbidden:
        assert symbol not in store_module, f"store must not reference {symbol}"
        assert symbol not in identity_module, f"identity must not reference {symbol}"

    # The persisted artefact is inert identity data: no runner, no report, no config.
    store = GeneratedResearchStore(tmp_path / "generated_research.json")
    record = store.register(_proposal())
    persisted = json.loads((tmp_path / "generated_research.json").read_text(encoding="utf-8"))
    assert set(persisted) == {"format", "schema_version", "id_prefix", "records"}
    assert set(record.to_dict()) == {
        "generated_research_id", "semantic_identity", "schema_version",
        "semantic_version", "created_at", "research_kind", "trigger_ref",
        "target_kind", "target_ref", "dimension_ref", "parent_refs",
        "specification_json", "provenance_json",
    }
    for forbidden_key in ("runner_module", "runner_function", "report_filename",
                          "priority", "status", "lifecycle_state", "agenda_score"):
        assert forbidden_key not in record.to_dict()
