"""Stage IV Wave 1 assurance-contract guarantees."""

from dataclasses import replace
import json

import pytest

from core.production_data_contract import current_schema
from research_engine.v10.universes import models
from research_engine.v10.universes.assurance import (
    EXPECTED_ACTIVE_AUTHORITY,
    DatasetRole,
    PresenceContext,
    PresenceStatus,
    UNIVERSE_ASSURANCE_CONTRACTS,
    UniverseContractError,
    UniverseDependency,
    classify_presence,
    describe_analytical_topology,
    expected_active_universes,
    expected_activity,
    validate_contracts,
)
from research_engine.v10.universes.models import Universe


def test_every_canonical_active_universe_has_exactly_one_complete_contract():
    validate_contracts()
    assert tuple(UNIVERSE_ASSURANCE_CONTRACTS) == models.ACTIVE_UNIVERSES
    assert set(UNIVERSE_ASSURANCE_CONTRACTS) == set(models.ACTIVE_UNIVERSES)
    assert Universe.SHADOW_REALITY not in UNIVERSE_ASSURANCE_CONTRACTS


def test_expected_active_topology_is_derived_from_canonical_models_authority():
    assert expected_active_universes() == models.ACTIVE_UNIVERSES
    assert all(expected_activity(u).value == "EXPECTED_ACTIVE" for u in models.ACTIVE_UNIVERSES)
    assert expected_activity(Universe.SHADOW_REALITY).value == "NOT_EXPECTED"

    topology = describe_analytical_topology()
    assert topology["expected_active_authority"] == EXPECTED_ACTIVE_AUTHORITY
    assert topology["expected_active_universes"] == [u.value for u in models.ACTIVE_UNIVERSES]


def test_expected_activity_does_not_depend_on_files_or_recent_records(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    before = expected_active_universes()
    (tmp_path / "shadow_trades.jsonl").write_text("{}\n", encoding="utf-8")
    assert expected_active_universes() == before == models.ACTIVE_UNIVERSES


def test_presence_classification_distinguishes_legitimate_and_unexpected_absence():
    legitimate = classify_presence(
        Universe.EXECUTION,
        PresenceContext(record_count=0, source_available=True,
                        qualifying_activity_expected=False),
    )
    unexpected = classify_presence(
        Universe.EXECUTION,
        PresenceContext(record_count=0, source_available=True,
                        qualifying_activity_expected=True),
    )
    assert legitimate is PresenceStatus.ABSENT_LEGITIMATE
    assert unexpected is PresenceStatus.ABSENT_UNEXPECTED


def test_shadow_runtime_is_primary_and_shadow_trades_is_not_interchangeable():
    contract = UNIVERSE_ASSURANCE_CONTRACTS[Universe.SHADOW_OUTCOME]
    roles = {binding.dataset: binding.role for binding in contract.datasets}
    assert roles == {
        "shadow_runtime": DatasetRole.PRIMARY_SOURCE,
        "shadow_trades": DatasetRole.LEGACY_COMPATIBILITY,
    }
    assert contract.internal_record_schema == current_schema("shadow_trades")
    assert contract.dependencies[0].reference == "shadow_runtime"
    assert contract.identity.primary_fields == ("shadow_trade_id",)
    assert any("does not preserve canonical_opportunity_id" in item
               for item in contract.limitations)


def test_contract_validation_rejects_duplicate_missing_and_extra_universes():
    contracts = tuple(UNIVERSE_ASSURANCE_CONTRACTS.values())
    with pytest.raises(UniverseContractError, match="duplicate"):
        validate_contracts(contracts + (contracts[0],))
    with pytest.raises(UniverseContractError, match="topology diverges"):
        validate_contracts(contracts[:-1])


def test_contract_validation_rejects_incomplete_invalid_references_and_ambiguity():
    contracts = tuple(UNIVERSE_ASSURANCE_CONTRACTS.values())
    broken = replace(contracts[0], authority_module="")
    with pytest.raises(UniverseContractError, match="missing authority_module"):
        validate_contracts((broken,) + contracts[1:])

    for incomplete in (
        replace(contracts[0], population_semantics=""),
        replace(contracts[0], resolution=""),
        replace(contracts[0], identity=replace(
            contracts[0].identity, primary_fields=())),
        replace(contracts[0], timestamps=replace(
            contracts[0].timestamps, event_fields=())),
    ):
        with pytest.raises(UniverseContractError):
            validate_contracts((incomplete,) + contracts[1:])

    invalid_dependency = replace(
        contracts[0],
        dependencies=(UniverseDependency(
            "not_a_canonical_dataset", "DATASET", "invalid test reference"),),
    )
    with pytest.raises(UniverseContractError, match="invalid dataset dependency"):
        validate_contracts((invalid_dependency,) + contracts[1:])

    invalid_counterpart = replace(
        contracts[0],
        counterparts=(replace(contracts[0].counterparts[0],
                              universe=Universe.SHADOW_REALITY),),
    )
    with pytest.raises(UniverseContractError, match="counterpart SHADOW_REALITY"):
        validate_contracts((invalid_counterpart,) + contracts[1:])

    with pytest.raises(UniverseContractError, match="exactly one expected-active authority"):
        validate_contracts(contracts, expectation_authorities=(
            EXPECTED_ACTIVE_AUTHORITY, "competing.authority",
        ))


def test_shadow_contract_validation_rejects_reversed_authority_roles():
    contracts = tuple(UNIVERSE_ASSURANCE_CONTRACTS.values())
    shadow_index = next(i for i, c in enumerate(contracts)
                        if c.universe is Universe.SHADOW_OUTCOME)
    shadow = contracts[shadow_index]
    reversed_bindings = tuple(
        replace(binding, role=(
            DatasetRole.LEGACY_COMPATIBILITY
            if binding.dataset == "shadow_runtime"
            else DatasetRole.PRIMARY_SOURCE
        ))
        for binding in shadow.datasets
    )
    mutated = list(contracts)
    mutated[shadow_index] = replace(shadow, datasets=reversed_bindings)
    with pytest.raises(UniverseContractError, match="shadow_runtime as its sole primary"):
        validate_contracts(mutated)


def test_self_knowledge_is_restart_deterministic_and_json_serializable():
    first = describe_analytical_topology()
    second = describe_analytical_topology()
    assert first == second
    assert json.loads(json.dumps(first, sort_keys=True)) == first


def test_contract_registry_is_immutable():
    with pytest.raises(TypeError):
        UNIVERSE_ASSURANCE_CONTRACTS[Universe.EXECUTION] = object()  # type: ignore[index]
