from __future__ import annotations

from collections import Counter
import json

import pytest

from research_engine.data_access.s3_source import ResearchDataSourceError
from research_engine.registry.definition_validator import (
    build_definitions_from_registry,
    get_question_health,
    validate_all_definitions,
)
from research_engine.registry.research_question_registry import REGISTRY
from research_engine.v10.universes.production_qualification import (
    bind_canonical_answers,
    compare_checkpoint_runs,
    qualify_production,
    qualify_production_checkpointed,
    required_production_datasets,
)
from research_engine.v10.universes.question_qualification import (
    QualificationEngine,
    QuestionEvidenceInput,
    StatisticalState,
    build_question_evidence_contracts,
)


ORIGINAL_UNDER_SPECIFIED = {
    "S1", "S5", "S6", "S7", "X6", "R1", "R2", "R3", "R4", "R5",
    "L6", "L7", "EX1", "EX2", "EX9", "EX10",
}


class EmptyProductionSource:
    def __init__(self):
        self.calls = []

    def read_dataset(self, name):
        self.calls.append(name)
        return []


class CompletedShadowProductionSource(EmptyProductionSource):
    def read_dataset(self, name):
        self.calls.append(name)
        if name != "shadow_runtime":
            return []
        common = {
            "schema_version": "shadow_runtime_v1",
            "shadow_trade_id": "nshadow_completion_1",
            "plan_id": "plan-completion-1",
            "canonical_opportunity_id": "opp-completion-1",
            "horizon": "SCALP",
            "symbol": "EURUSD",
        }
        return [
            {
                **common,
                "event_type": "OPEN",
                "entry_market_time_utc_epoch_s": 1790326800,
                "identity": {
                    "entity_id": "e-completion-1",
                    "evaluated_horizon": "SCALP",
                    "trade_horizon": "SCALP",
                    "shadow_type": "HORIZON_ALTERNATIVE",
                },
                "construction": {
                    "direction": "BUY", "entry_price": 1.1,
                    "stop_loss": 1.09, "take_profit": 1.12,
                    "risk_distance": 0.01,
                },
                "live_facts": {"v10_action": "EXECUTE"},
            },
            {
                **common,
                "event_type": "CLOSE",
                "exit_reason": "take_profit",
                "exit_market_time_utc_epoch_s": 1790330400,
                "exit_price": 1.12,
                "bars_held": 12,
                "outcome": {
                    "pnl_r_multiple": 2.0, "mfe_r": 2.1, "mae_r": -0.2,
                },
            },
        ]


def test_original_sixteen_definitions_are_closed_by_governed_authority():
    definitions = build_definitions_from_registry(REGISTRY)
    health = validate_all_definitions(definitions)
    for question_id in ORIGINAL_UNDER_SPECIFIED:
        definition = definitions[question_id]
        assert definition.population_definition
        assert definition.metric_definition
        assert definition.hypothesis or definition.null_hypothesis
        assert get_question_health(health[question_id]) != "UNDER_SPECIFIED"


def test_alias_and_specialised_sufficiency_semantics_are_stable():
    definitions = build_definitions_from_registry(REGISTRY)
    assert definitions["S1"].scientific_owner_id == "E3"
    assert definitions["S1"].population_definition == definitions["E3"].population_definition
    assert definitions["S7"].minimum_sample == 150
    assert definitions["X6"].minimum_sample == 100
    assert definitions["EX10"].minimum_sample == 450
    assert definitions["R1"].join_contract.join_keys == ("canonical_opportunity_id",)
    assert "intervention-assigned" in definitions["L7"].population_definition


def test_exit_heterogeneity_definitions_now_match_governed_runner_semantics():
    definitions = build_definitions_from_registry(REGISTRY)
    health = validate_all_definitions(definitions)
    for question_id, dimension in {
        "EX5": "trade_horizon", "EX6": "strategy_family",
        "EX7": "market_regime", "EX8": "candlestick_pattern",
    }.items():
        assert dimension in definitions[question_id].metric_definition
        assert get_question_health(health[question_id]) == "VALID"
    assert {qid for qid in ("L2", "L3", "L7") if get_question_health(health[qid]) == "SEMANTIC_MISMATCH"} == {"L2", "L3", "L7"}


def test_canonical_answer_binding_represents_all_70_and_preserves_validity():
    answers = bind_canonical_answers()
    assert len(answers) == len(REGISTRY) == 70
    assert [item.question_id for item in answers] == [item.id for item in REGISTRY]
    counts = Counter(item.validity for item in answers)
    assert sum(counts.values()) == 70
    assert all(item.status for item in answers)


def test_production_scan_inventory_is_canonical_and_has_no_local_log_source():
    datasets = required_production_datasets()
    assert "shadow_runtime" in datasets
    assert "shadow_trades" in datasets
    assert "decision_trace" in datasets
    assert "trade_truth" in datasets
    assert not any("log" in item for item in datasets)


def test_wave2_wave3_and_answers_bind_into_exactly_70_results():
    source = EmptyProductionSource()
    bundle = qualify_production(
        as_of_utc="2026-09-27T00:00:00Z", source=source,
    )
    assert tuple(source.calls) == required_production_datasets()
    assert len(bundle.integrity.manifests) == len(bundle.batches)
    assert len(bundle.answers) == 70
    assert len(bundle.qualification.qualifications) == 70
    assert sum(bundle.qualification.counts.values()) == 70


def test_production_binding_uses_governed_shadow_reconstruction():
    bundle = qualify_production(
        as_of_utc="2026-09-27T00:00:00Z",
        source=CompletedShadowProductionSource(),
    )
    assert len(bundle.integrity.reconstructions) == 1
    reconstructed = bundle.integrity.reconstructions[0]
    assert reconstructed.target_dataset == "shadow_trades"
    assert reconstructed.artifact["identity"]["shadow_trade_id"] == "nshadow_completion_1"
    assert any(
        item.reconstruction_involvement
        for item in bundle.qualification.qualifications
        if "SHADOW_OUTCOME" in item.required_universes
    )


def test_production_binding_is_deterministic_for_identical_snapshot():
    first = qualify_production(
        as_of_utc="2026-09-27T00:00:00Z", source=EmptyProductionSource(),
    )
    second = qualify_production(
        as_of_utc="2026-09-27T00:00:00Z", source=EmptyProductionSource(),
    )
    assert first.qualification.to_dict() == second.qualification.to_dict()
    assert first.qualification.report_fingerprint == second.qualification.report_fingerprint


def test_s3_failure_is_not_converted_to_empty_or_local_evidence():
    class BrokenSource:
        def read_dataset(self, name):
            raise ResearchDataSourceError(f"no credential for {name}")

    with pytest.raises(ResearchDataSourceError, match="no credential"):
        qualify_production(
            as_of_utc="2026-09-27T00:00:00Z", source=BrokenSource(),
        )


def test_existing_complete_contradiction_is_recorded_without_mutation():
    contract = next(item for item in build_question_evidence_contracts() if item.question_id == "X2")
    existing = {"finding": "POSITIVE", "immutable": True}
    supplied = QuestionEvidenceInput(
        question_id="X2", contract_fingerprint=contract.contract_fingerprint,
        existing_state="COMPLETE", existing_result=existing,
        statistical_state=StatisticalState.SUFFICIENT,
    )
    report = QualificationEngine(inputs={"X2": supplied}).qualify_all()
    contradiction = next(item for item in report.contradictions if item.question_id == "X2")
    assert contradiction.existing_state == "COMPLETE"
    assert existing == {"finding": "POSITIVE", "immutable": True}


def test_bound_valid_evidence_is_not_false_unavailable_from_invocation_gap():
    # The focused Wave 4 fixtures prove a fully-bound question can verify; here
    # assert definition closure itself does not inject the old unbound blocker.
    contracts = build_question_evidence_contracts()
    assert all(item.definition_health != "UNDER_SPECIFIED" for item in contracts)
    assert all(item.question_fingerprint and item.contract_fingerprint for item in contracts)


def test_production_input_does_not_self_attest_unknown_population_resolution_or_lineage():
    from types import SimpleNamespace
    from research_engine.v10.universes import production_qualification as module

    contract = next(item for item in build_question_evidence_contracts() if item.question_id == "X2")
    answer = next(item for item in bind_canonical_answers() if item.question_id == "X2")
    state = SimpleNamespace(
        question_id="X2", current_sample_size=10, excluded_evidence_count=2,
        evidence_epoch="CURRENT", evidence_metrics={},
        evidence_sources=[{
            "source": "execution_results_v1", "available": True,
            "total_records": 12, "current_records": 10,
            "transitional_excluded": 1, "legacy_excluded": 1,
        }],
        requirements=[
            {"type": "dataset_presence", "name": "execution_results_v1", "satisfied": True},
            {"type": "coverage", "name": "coverage", "satisfied": None},
        ],
        state_status="READY",
    )
    supplied = module._question_inputs((state,), (answer,), (contract,))["X2"]
    assert supplied.collection_functional is True
    assert supplied.population_complete is False
    assert supplied.observed_resolution == ""
    assert supplied.available_lineage == ()
    assert supplied.record_accounting["historical_exhaustive"] is True


def test_opp1_denominator_is_persisted_from_governed_canonical_population():
    from types import SimpleNamespace
    from research_engine.v10.universes import production_qualification as module

    contract = next(item for item in build_question_evidence_contracts() if item.question_id == "OPP-1")
    answer = next(item for item in bind_canonical_answers() if item.question_id == "OPP-1")
    state = SimpleNamespace(
        question_id="OPP-1", current_sample_size=1, excluded_evidence_count=3,
        evidence_epoch="CURRENT",
        evidence_metrics={
            "accounting_candidate_records": 3,
            "accounting_exclusion_reason_counts": {
                "AMBIGUOUS_OR_UNMATCHED_IDENTITY": 1,
                "REQUIRED_RELATIONSHIP_ABSENT": 1,
            },
            "candidate_inclusion_rule": "fixture governed membership",
            "candidate_identity_rule": "canonical_opportunity_id",
            "candidate_epoch_rule": "CURRENT",
            "candidate_source_datasets": ("horizon_candidates", "shadow_trades"),
            "candidate_source_overlap_rule": "join; source rows are not additive",
            "candidate_duplicate_rule": "collapse before denominator membership",
            "candidate_join_rule": "canonical_opportunity_id; conflicts fail closed",
        },
        evidence_sources=[
            {"source": "horizon_candidates", "available": True, "total_records": 4,
             "current_records": 4, "transitional_excluded": 0, "legacy_excluded": 0},
            {"source": "shadow_trades", "available": True, "total_records": 2,
             "current_records": 1, "transitional_excluded": 0, "legacy_excluded": 1},
        ],
        requirements=[
            {"type": "dataset_presence", "name": "horizon_candidates", "satisfied": True},
            {"type": "dataset_presence", "name": "shadow_trades", "satisfied": True},
            {"type": "required_field", "name": "selection_status", "satisfied": True},
            {"type": "required_field", "name": "simulated_outcome.pnl_r_multiple", "satisfied": True},
        ],
        state_status="WAITING_DATA",
    )
    supplied = module._question_inputs((state,), (answer,), (contract,))["OPP-1"]
    result = QualificationEngine(
        inputs={"OPP-1": supplied},
    ).qualify_question("OPP-1")
    accounting = result.evidence_accounting
    assert accounting["candidate_denominator_authority"] == "OPP1_GOVERNED_CANONICAL_OPPORTUNITY_POPULATION"
    assert accounting["candidate_records"] == 3
    assert accounting["used_records"] == 1
    assert accounting["excluded_records"] == 2
    assert accounting["unexplained_records"] == 0
    assert accounting["historical_exhaustion_status"] == "EXHAUSTED"


def _checkpoint_run(tmp_path, source=None, markers=None, as_of="2026-09-27T00:00:00Z"):
    return qualify_production_checkpointed(
        as_of_utc=as_of,
        checkpoint_dir=tmp_path / "checkpoints",
        final_path=tmp_path / "wave4.json",
        source=source or EmptyProductionSource(),
        reports_dir=tmp_path / "reports",
        progress=markers.append if markers is not None else None,
    )


class NeverReadSource:
    def read_dataset(self, name):
        raise AssertionError(f"checkpoint reuse unexpectedly read {name}")


def test_evidence_snapshot_records_source_objects_and_malformed_report(tmp_path):
    class Report:
        malformed_lines = 2
        keys_with_errors = ["supporting/example.jsonl"]

    class MetadataSource(EmptyProductionSource):
        def object_metadata(self, name):
            return ({
                "identifier": f"supporting/{name}/part.jsonl",
                "etag": f"etag-{name}", "size": 123,
            },)

        def malformed_report(self, name):
            return Report() if name == "decision_trace" else None

    _checkpoint_run(tmp_path, source=MetadataSource())
    snapshot = json.loads(
        (tmp_path / "checkpoints" / "evidence_snapshot.json").read_text(encoding="utf-8")
    )
    decision = snapshot["datasets"]["decision_trace"]
    assert decision["source_objects"][0]["etag"] == "etag-decision_trace"
    assert decision["content_fingerprint"]
    assert snapshot["malformed_input"]["decision_trace"]["malformed_lines"] == 2


def test_checkpointed_runner_reuses_every_valid_stage(tmp_path):
    _checkpoint_run(tmp_path)
    markers = []
    second = _checkpoint_run(tmp_path, source=NeverReadSource(), markers=markers)
    assert len(second.qualification.qualifications) == 70
    assert {item for item in markers if item.endswith(":reused")} == {
        "CHECKPOINT:evidence_snapshot:reused",
        "CHECKPOINT:reconstruction:reused",
        "CHECKPOINT:wave2:reused",
        "CHECKPOINT:wave3:reused",
        "CHECKPOINT:wave4:reused",
    }


def test_snapshot_change_invalidates_only_itself_and_downstream(tmp_path):
    from research_engine.v10.universes import production_qualification as module

    _checkpoint_run(tmp_path)
    root = tmp_path / "checkpoints"
    _, datasets = module._load_evidence_snapshot(
        root, as_of_utc="2026-09-27T00:00:00Z",
    )
    datasets["decision_trace"] = [{
        "schema_version": "decision_trace_v1", "entity_id": "changed",
        "symbol": "EURUSD", "timestamp_utc": "2026-09-27T00:00:00Z",
    }]
    module._write_evidence_snapshot(
        root, as_of_utc="2026-09-27T00:00:00Z", datasets=datasets, malformed={},
    )
    markers = []
    _checkpoint_run(tmp_path, source=NeverReadSource(), markers=markers)
    assert "CHECKPOINT:evidence_snapshot:reused" in markers
    assert any(item.startswith("CHECKPOINT:reconstruction:invalid:upstream fingerprint mismatch") for item in markers)
    assert any(item.startswith("CHECKPOINT:wave2:invalid:upstream fingerprint mismatch") for item in markers)
    assert any(item.startswith("CHECKPOINT:wave3:invalid:upstream fingerprint mismatch") for item in markers)
    assert any(item.startswith("CHECKPOINT:wave4:invalid:upstream fingerprint mismatch") for item in markers)


def test_reconstruction_checkpoint_prevents_recomputation(tmp_path, monkeypatch):
    from research_engine.v10.universes import production_qualification as module

    _checkpoint_run(tmp_path)
    monkeypatch.setattr(
        module, "reconstruct_shadow_outcomes_report",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("recomputed reconstruction")),
    )
    _checkpoint_run(tmp_path, source=NeverReadSource())


def test_wave2_checkpoint_prevents_recomputation(tmp_path, monkeypatch):
    from research_engine.v10.universes import production_qualification as module

    _checkpoint_run(tmp_path)
    monkeypatch.setattr(
        module, "build_report",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("recomputed Wave 2")),
    )
    _checkpoint_run(tmp_path, source=NeverReadSource())


def test_interrupted_wave3_resumes_from_completed_wave2(tmp_path, monkeypatch):
    from research_engine.v10.universes import production_qualification as module

    original = module.ReconciliationEngine.reconcile_all
    monkeypatch.setattr(
        module.ReconciliationEngine, "reconcile_all",
        lambda self: (_ for _ in ()).throw(RuntimeError("interrupted Wave 3")),
    )
    with pytest.raises(RuntimeError, match="interrupted Wave 3"):
        _checkpoint_run(tmp_path)
    root = tmp_path / "checkpoints"
    assert (root / "wave2_integrity.json").exists()
    assert not (root / "wave3_reconciliation.json").exists()

    monkeypatch.setattr(module.ReconciliationEngine, "reconcile_all", original)
    markers = []
    _checkpoint_run(tmp_path, source=NeverReadSource(), markers=markers)
    assert "CHECKPOINT:evidence_snapshot:reused" in markers
    assert "CHECKPOINT:reconstruction:reused" in markers
    assert "CHECKPOINT:wave2:reused" in markers
    assert "CHECKPOINT:wave3:written" in markers


def test_interrupted_determinism_comparison_preserves_first_result(tmp_path):
    _checkpoint_run(tmp_path)
    final_path = tmp_path / "wave4.json"
    before = final_path.read_bytes()
    with pytest.raises(Exception):
        compare_checkpoint_runs(tmp_path / "checkpoints", tmp_path / "missing-second-pass")
    assert final_path.read_bytes() == before


def test_mixed_snapshot_as_of_is_rejected(tmp_path):
    _checkpoint_run(tmp_path)
    replacement = EmptyProductionSource()
    markers = []
    _checkpoint_run(
        tmp_path, source=replacement, markers=markers,
        as_of="2026-09-28T00:00:00Z",
    )
    assert tuple(replacement.calls) == required_production_datasets()
    assert any("mixed snapshot" in item for item in markers)


def test_stale_checkpoint_schema_is_rejected_and_rebuilt(tmp_path, monkeypatch):
    from research_engine.v10.universes import production_qualification as module

    _checkpoint_run(tmp_path)
    path = tmp_path / "checkpoints" / "reconstruction.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["schema"] = 0
    value["fingerprint"] = module._fingerprint({
        key: item for key, item in value.items() if key != "fingerprint"
    })
    path.write_text(json.dumps(value), encoding="utf-8")
    original = module.reconstruct_shadow_outcomes_report
    calls = 0

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "reconstruct_shadow_outcomes_report", counted)
    markers = []
    _checkpoint_run(tmp_path, source=NeverReadSource(), markers=markers)
    assert calls == 1
    assert any("schema mismatch" in item for item in markers)
