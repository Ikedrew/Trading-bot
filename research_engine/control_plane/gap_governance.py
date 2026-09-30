"""Stage 4 gap governance: durable governed work-item linkage (Q1-Q70)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from research_engine.control_plane.assured_epistemic_findings import (
    EXPECTED_CERTIFICATION_FINGERPRINT,
)


class GapGovernanceError(RuntimeError):
    """Gap-governance invariant violated (fail closed)."""


CERTIFICATION_PATH = Path(
    "analysis/assurance/final_70_question_certification_20260928.json")
DATA_GAPS_PATH = Path(
    "analysis/assurance/stage4_dataset_schema_gap_register_20260928.json")
IMPL_GAPS_PATH = Path(
    "analysis/assurance/stage4_implementation_gap_register_20260928.json")
FINDINGS_PATH = Path(
    "analysis/assurance/assured_epistemic_findings_20260928.json")
CANONICAL_STATE_PATH = Path(
    "research_engine/control_plane/gap_governance_state.json")
#: ROOT CHANGE 1 -- persisted shadow_runtime_v1 observation-source authority.
#: Additive overlay; the frozen work-item history above is never rewritten.
DATASET_AUTHORITY_PATH = Path(
    "research_engine/control_plane/stage4_dataset_authority_state.json")
#: Canonical observation source after ROOT CHANGE 1.  ``shadow_trades`` is a
#: declared but zero-object dataset and is NOT selectable as current evidence.
SHADOW_RUNTIME_DATASET = "shadow_runtime"
STAMP = "20260929"
STORE_SCHEMA = 1
GAP_TYPE_DATA = "DATA_SCHEMA_GAP"
GAP_TYPE_IMPL = "IMPLEMENTATION_GAP"
ALLOWED_GAP_TYPES = frozenset({GAP_TYPE_DATA, GAP_TYPE_IMPL})
STATUS_OPEN = "OPEN"
STATUS_READY = "READY"
STATUS_IN_PROGRESS = "IN_PROGRESS"
STATUS_BLOCKED = "BLOCKED"
STATUS_IMPLEMENTED = "IMPLEMENTED"
STATUS_VALIDATION_REQUIRED = "VALIDATION_REQUIRED"
STATUS_VALIDATED = "VALIDATED"
STATUS_RESOLVED = "RESOLVED"
STATUS_SUPERSEDED = "SUPERSEDED"
ALLOWED_STATUSES = frozenset({
    STATUS_OPEN, STATUS_READY, STATUS_IN_PROGRESS, STATUS_BLOCKED,
    STATUS_IMPLEMENTED, STATUS_VALIDATION_REQUIRED, STATUS_VALIDATED,
    STATUS_RESOLVED, STATUS_SUPERSEDED})
VERSION_CONSEQUENCES = frozenset({
    "NO_SCHEMA_CHANGE", "V2_REQUIRED", "FUTURE_VERSION_REQUIRED",
    "PRODUCER_ONLY_CHANGE", "CONTRACT_ONLY_CHANGE"})
OWNER_TYPES = frozenset({
    "SCHEMA_PRODUCER", "RESEARCH_RUNNER", "CONTROL_PLANE",
    "EXECUTION_SUBSYSTEM", "RESEARCH_ENGINE"})
EXISTING_FEATURE_MAP = (
    {"feature": "lifecycle/research_agenda.py",
     "purpose": "attention ORDER of executable opportunities",
     "verdict": "REUSED_FOR_PRIORITY_INPUTS",
     "reason": "blocked work retained but never queueable"},
    {"feature": "lifecycle/research_queue.py",
     "purpose": "bounded NEXT-work recommendation (READY only)",
     "verdict": "REJECTED_AS_AUTHORITY",
     "reason": "gaps are definitionally non-executable"},
    {"feature": "lifecycle/research_priority.py",
     "purpose": "lexicographic ordering, no profit criterion",
     "verdict": "REUSED_AS_PATTERN",
     "reason": "priority from blocked-count/fanout/immediacy"},
    {"feature": "v10/universes/future_data_contract.py",
     "purpose": "verified persistence paths, additive-only evolution",
     "verdict": "REUSED",
     "reason": "producer identity plus V1 immutability"},
    {"feature": "registry/master_repair_ledger.py",
     "purpose": "frozen Wave-A blocker planning consolidation",
     "verdict": "REUSED_FOR_PHRASE",
     "reason": "planning snapshot without lifecycle semantics"},
    {"feature": "lifecycle/revisit_governance.py",
     "purpose": "closed revisit triggers; revisit is not execute",
     "verdict": "EXTENDED",
     "reason": "re-entry: resolution, rerun, finding v2"},
    {"feature": "control_plane/report_ownership.py",
     "purpose": "adjudicated single-owner fail-closed map",
     "verdict": "REUSED_AS_PATTERN",
     "reason": "ownership fail-closed plus L3/D1 precedent"},
    {"feature": "lifecycle/research_agenda_store.py",
     "purpose": "atomic local JSON, dedup, immutable history",
     "verdict": "REUSED_AS_PATTERN",
     "reason": "canonical state persistence discipline"},
)
PRODUCER_OWNER_MODULE = {
    "shadow_trades":
    "research_engine/v10/universes/shadow_reality_universe.py",
    "decision_trace": "core/decision_trace.py",
    "market_context": "core/market_context/persistence.py",
    "horizon_candidates":
    "research_engine/experiments/selection_research.py",
    "strategy_candidates":
    "research_engine/experiments/strategy_expectancy.py",
    "portfolio_rankings":
    "research_engine/experiments/portfolio_ranking.py"}
IMPL_OWNER_MODULE = {
    "R1": "research_engine/experiments/r1_risk_layer_effectiveness.py",
    "R2": "research_engine/experiments/r2_guard_attribution.py",
    "L3": "research_engine/control_plane/report_ownership.py",
    "L6": "research_engine/experiments/learning_trust.py",
    "L7": "research_engine/experiments/learning_comparison.py",
    "G2": "research_engine/experiments/lineage_coverage.py",
    "G3": "research_engine/control_plane/assured_epistemic_findings.py",
    "EX2": "research_engine/experiments/exit_policy_governed.py"}
IMPL_OWNER_TYPE = {
    "R1": "RESEARCH_RUNNER", "R2": "RESEARCH_RUNNER",
    "L3": "CONTROL_PLANE", "L6": "RESEARCH_RUNNER",
    "L7": "RESEARCH_RUNNER", "G2": "RESEARCH_RUNNER",
    "G3": "CONTROL_PLANE", "EX2": "RESEARCH_RUNNER"}
IMPL_DOMAIN = {
    "R1": "research-engine/HD10-population",
    "R2": "research-engine/HD10-population",
    "L3": "control-plane/report-ownership",
    "L6": "research-engine/runner",
    "L7": "research-engine/label-contract",
    "G2": "research-engine/HD14-denominator",
    "G3": "control-plane/scientific-dependency",
    "EX2": "research-engine/HD09-population"}
IMPL_EVIDENCE = (
    "code_repair_verified", "targeted_tests_passed",
    "governed_population_aligned", "scientific_rerun_completed",
    "assurance_revalidated", "new_finding_version")
DATA_EVIDENCE = (
    "schema_change_deployed", "producer_emitting",
    "sample_condition_satisfied", "scientific_rerun_completed",
    "assurance_revalidated", "new_finding_version")
def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _fp(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True, default=str).encode("utf-8")).hexdigest()


def load_inputs() -> dict[str, Any]:
    cert = _read_json(CERTIFICATION_PATH)
    fp = EXPECTED_CERTIFICATION_FINGERPRINT
    if cert.get("certification_fingerprint") != fp:
        raise GapGovernanceError("CERTIFICATION_FINGERPRINT_CHANGED")
    if cert.get("question_count") != 70:
        raise GapGovernanceError("CERTIFICATION_NOT_EXACTLY_70")
    store = _read_json(FINDINGS_PATH)
    if store.get("certification_fingerprint") != fp:
        raise GapGovernanceError("FINDINGS_FINGERPRINT_CHANGED")
    return {"certification": cert,
            "data_gaps": _read_json(DATA_GAPS_PATH),
            "impl_gaps": _read_json(IMPL_GAPS_PATH),
            "findings": store}


def _clean_producers(raw: Any) -> list[str]:
    out: list[str] = []
    for item in (raw or []):
        name = str(item).split(":", 1)[-1].strip()
        if name and name not in out:
            out.append(name)
    return out


def data_owner(producer: str) -> dict[str, Any]:
    if producer not in PRODUCER_OWNER_MODULE:
        raise GapGovernanceError("UNKNOWN_OWNER:" + producer)
    return {"owner_type": "SCHEMA_PRODUCER",
            "owner_identifier": "producer:" + producer,
            "owning_module": PRODUCER_OWNER_MODULE[producer],
            "resolution_domain": "dataset-producer/" + producer,
            "owner_discovery_required": False}


def impl_owner(qid: str) -> dict[str, Any]:
    if qid not in IMPL_OWNER_MODULE:
        raise GapGovernanceError("UNKNOWN_OWNER:" + qid)
    return {"owner_type": IMPL_OWNER_TYPE[qid],
            "owner_identifier": "runner:" + qid,
            "owning_module": IMPL_OWNER_MODULE[qid],
            "resolution_domain": IMPL_DOMAIN[qid],
            "owner_discovery_required": False}


def version_consequence(obs: list[str]) -> str:
    joined = " ".join(obs).lower()
    has = joined.__contains__
    if (has("canonical_opportunity_id") or has("entry_time")
            or has("opportunity_id") or has("overall_score")):
        return "CONTRACT_ONLY_CHANGE"
    if has("market_timestamp") or has("candle:"):
        return "PRODUCER_ONLY_CHANGE"
    if (has("p_success") or has("v10_entry") or has("rank_position")
            or has("selection_status") or has("horizon")
            or has("confidence")):
        return "V2_REQUIRED"
    return "FUTURE_VERSION_REQUIRED"


def priority_for(nq: int, fanout: int, immediate: bool,
                 compounds: bool, certified: str = "") -> tuple[str, str]:
    base = str(certified or "").strip().upper() or None
    if immediate:
        level = "P0"
        why = "repair unlocks existing evidence"
    elif compounds or nq >= 2:
        level = "P0"
        why = "collection delay compounds"
    elif fanout >= 2:
        level = "P0"
        why = "downstream blocked"
    else:
        level = "P1"
        why = "future accumulation required"
    if base in ("P0", "P1", "P2", "P3"):
        level = base
    return (level, "%s; certified=%s; fanout=%d" % (why, level, fanout))


def _impl_detail(qid: str, gap: Mapping[str, Any]) -> dict[str, Any]:
    runnable = bool(gap.get("runnable_immediately_after_repair"))
    return {
        "question_contract": str(gap.get("contract", "")),
        "expected_governed_method": "governed runner on frozen Gate1 "
                                    "population for " + qid,
        "expected_governed_population": gap.get("governed"),
        "current_behaviour": "runner %s n=%s" % (
            gap.get("runner"), gap.get("runner_n")),
        "current_runner": str(gap.get("runner", "")),
        "exact_mismatch": str(gap.get("mismatch", "")),
        "required_code_repair": str(gap.get("repair", "")),
        "dependent_modules": [IMPL_OWNER_MODULE[qid]],
        "dependent_questions": ["G3"] if qid == "L6" else [],
        "dependency_chain": ["GWI-L6-IMPL"] if qid == "G3" else [],
        "evidence_already_exists": qid != "L6",
        "rerun_immediately_after_repair": runnable,
        "revalidation": "population+tests+rerun+assurance+finding-v2",
        "required_tests": ["test_" + qid.lower() + "_governed_population"],
        "resolution_criteria":
        "repair+tests+population+rerun+assurance+v2",
        "dependency_label": str(gap.get("dependency", ""))}
def _data_item(gap: Mapping[str, Any], row: Mapping[str, Any]) -> dict:
    qids = [str(q) for q in gap.get("affected_question_ids", ())]
    if len(qids) != 1:
        raise GapGovernanceError("DATA_GAP_MUST_MAP_ONE_QUESTION")
    qid = qids[0]
    if row["scientific_state"] != "HISTORICALLY_UNANSWERABLE":
        raise GapGovernanceError("DATA_GAP_STATE_MISMATCH:" + qid)
    obs = [str(o) for o in gap.get("missing_observable", ())]
    datasets = [str(d) for d in gap.get("current_datasets", ())]
    producers = _clean_producers(gap.get("required_future_producer"))
    owners = [data_owner(p) for p in producers]
    prio, rationale = priority_for(
        1, 0, False, True, str(gap.get("priority", "")))
    return {
        "gap_work_item_id": "GWI-" + qid + "-DATA",
        "gap_id": str(gap.get("gap_id")),
        "gap_type": GAP_TYPE_DATA,
        "affected_question_ids": qids,
        "affected_finding_ids": [qid + ":v1"],
        "version": 1, "created_at": "2026-09-29T00:00:00Z",
        "status": STATUS_OPEN, "substate": "COLLECTION_PENDING",
        "supersedes": None, "superseded_by": None,
        "missing_observable": obs,
        "missing_field_event_relationship": list(obs),
        "identity_requirement": "canonical_opportunity_id/entity lineage",
        "timestamp_requirement": "causal event time plus CURRENT epoch",
        "state_requirement": "CURRENT",
        "current_datasets": datasets,
        "current_schema_versions": dict(
            gap.get("current_schema_versions", {})),
        "why_impossible": str(gap.get("reason_unrecoverable")),
        "recovery_reference": "Gate1 EXHAUSTED plus Wave4 lossy",
        "future_producers": producers,
        "proposed_contract": str(gap.get("proposed_evidence_contract")),
        "proposed_schema_change": dict(
            gap.get("proposed_schema_change", {})),
        "schema_version_target": "v2",
        "backward_compatibility": "additive-only; V1 rows immutable",
        "migration_requirement": str(gap.get("compatibility_considerations")),
        "old_data_remains_valid": True,
        "earliest_epoch": str(gap.get("earliest_future_answerable_epoch")),
        "minimum_sample": "governed evidence contract thresholds",
        "reeval_trigger": "GOVERNED_EVIDENCE_CONTRACT_REEVALUATION",
        "priority": prio, "priority_rationale": rationale,
        "depends_on": [], "blocks": [], "dependency_type": None,
        "blocker_reason": "missing observable; future collection only",
        "resolution_criteria": "schema+producer+sample+rerun+assurance+v2",
        "required_evidence": list(DATA_EVIDENCE),
        "resolution_evidence": [],
        "owner": owners[0], "co_owners": owners[1:],
        "question_definition": str(row.get("canonical_question")),
        "evidence_contract_id": "frozen Gate1 2026-09-27",
        "evidence_contract_version": "v1",
        "certification_fingerprint": EXPECTED_CERTIFICATION_FINGERPRINT,
        "assured_finding_version": 1,
        "version_consequence": version_consequence(obs),
        "reentry_condition": "future frozen epoch satisfies contract",
        "reentry_action": "rerun question under historical pass",
        "target_question_ids": qids,
        "required_assurance_gate": "FinalAssuranceCertification",
        "finding_version_expected": 2,
        "consumable_as_scientific_truth": False,
        "permitted_consumption": [
            "engineering_planning", "schema_planning",
            "research_agenda", "priority_logic", "revisit_scheduling"]}


def _impl_item(gap: Mapping[str, Any], row: Mapping[str, Any]) -> dict:
    qid = str(gap.get("question_id"))
    if row["scientific_state"] != "IMPLEMENTATION_BLOCKED":
        raise GapGovernanceError("IMPL_GAP_STATE_MISMATCH:" + qid)
    det = _impl_detail(qid, gap)
    fanout = 1 if qid == "L6" else 0
    prio, rationale = priority_for(
        1, fanout, det["rerun_immediately_after_repair"], False)
    return {
        "gap_work_item_id": "GWI-" + qid + "-IMPL",
        "gap_id": "STAGE4-IMPL-" + qid,
        "gap_type": GAP_TYPE_IMPL,
        "affected_question_ids": [qid],
        "affected_finding_ids": [qid + ":v1"],
        "version": 1, "created_at": "2026-09-29T00:00:00Z",
        "status": STATUS_BLOCKED if qid == "G3" else STATUS_OPEN,
        "substate": "REPAIR_PENDING",
        "supersedes": None, "superseded_by": None,
        "detail": det,
        "priority": prio, "priority_rationale": rationale,
        "depends_on": (["GWI-L6-IMPL"] if qid == "G3" else []),
        "blocks": (["GWI-G3-IMPL"] if qid == "L6" else []),
        "dependency_type": ("SCIENTIFIC_DEPENDENCY"
                            if qid == "G3" else None),
        "blocker_reason": det["exact_mismatch"],
        "resolution_criteria": det["resolution_criteria"],
        "required_evidence": list(IMPL_EVIDENCE),
        "resolution_evidence": [],
        "owner": impl_owner(qid), "co_owners": [],
        "question_definition": str(row.get("canonical_question")),
        "evidence_contract_id": "frozen Gate1 2026-09-27",
        "evidence_contract_version": "v1",
        "certification_fingerprint": EXPECTED_CERTIFICATION_FINGERPRINT,
        "assured_finding_version": 1,
        "version_consequence": "NO_SCHEMA_CHANGE",
        "reentry_condition": (
            "repair verified plus population aligned"
            if det["rerun_immediately_after_repair"]
            else "L6 certified CURRENT then G3 repair"),
        "reentry_action": "rerun question under historical pass",
        "target_question_ids": [qid],
        "required_assurance_gate": "FinalAssuranceCertification",
        "finding_version_expected": 2,
        "consumable_as_scientific_truth": False,
        "permitted_consumption": ["IMPLEMENTATION_REPAIR"]}
def build_store() -> dict[str, Any]:
    inp = load_inputs()
    rows = {str(r.get("question_id")): r
            for r in inp["certification"]["certifications"]}
    data_gaps = list(inp["data_gaps"].get("gaps", ()))
    impl_gaps = list(inp["impl_gaps"].get("gaps", ()))
    if len(data_gaps) != 29:
        raise GapGovernanceError("DATA_GAP_COUNT_CHANGED")
    if len(impl_gaps) != 8:
        raise GapGovernanceError("IMPL_GAP_COUNT_CHANGED")
    items: list[dict[str, Any]] = []
    rels: list[dict[str, Any]] = []
    for gap in data_gaps:
        qid = str(gap.get("affected_question_ids", ["?"])[0])
        item = _data_item(gap, rows[qid])
        items.append(item)
        rels.append({"question_id": qid,
                     "scientific_state": rows[qid]["scientific_state"],
                     "finding_id": qid + ":v1",
                     "gap_work_item_id": item["gap_work_item_id"],
                     "gap_type": GAP_TYPE_DATA})
    for gap in impl_gaps:
        qid = str(gap.get("question_id"))
        item = _impl_item(gap, rows[qid])
        items.append(item)
        rels.append({"question_id": qid,
                     "scientific_state": rows[qid]["scientific_state"],
                     "finding_id": qid + ":v1",
                     "gap_work_item_id": item["gap_work_item_id"],
                     "gap_type": GAP_TYPE_IMPL})
    edges = [{"from": "GWI-L6-IMPL", "to": "GWI-G3-IMPL",
              "dependency_type": "SCIENTIFIC_DEPENDENCY",
              "dependency_status": "UNRESOLVED"}]
    material = {
        "schema": STORE_SCHEMA, "stamp": STAMP,
        "certification_fingerprint": EXPECTED_CERTIFICATION_FINGERPRINT,
        "existing_feature_map": list(EXISTING_FEATURE_MAP),
        "q71_started": False, "live_or_s3_reads": False,
        "new_scientific_research_run": False,
        "certified_data_gap_relationships": 29,
        "certified_implementation_gap_relationships": 8,
        "certified_gap_relationships": 37,
        "unique_work_items": len(items),
        "work_items": items, "relationships": rels,
        "dependency_edges": edges}
    material["store_fingerprint"] = _fp(
        {k: v for k, v in material.items() if k != "store_fingerprint"})
    validate_store(material)
    return material


def validate_store(store: Mapping[str, Any]) -> None:
    items = list(store.get("work_items", ()))
    rels = list(store.get("relationships", ()))
    edges = list(store.get("dependency_edges", ()))
    if store.get("certified_data_gap_relationships") != 29:
        raise GapGovernanceError("DATA_RELATIONSHIP_COUNT_CHANGED")
    if store.get("certified_implementation_gap_relationships") != 8:
        raise GapGovernanceError("IMPL_RELATIONSHIP_COUNT_CHANGED")
    if store.get("certified_gap_relationships") != 37:
        raise GapGovernanceError("GAP_RELATIONSHIP_COUNT_CHANGED")
    if len(rels) != 37:
        raise GapGovernanceError("RELATIONSHIPS_NOT_37")
    if len(items) != 37:
        raise GapGovernanceError("WORK_ITEMS_NOT_37")
    by_id = {str(i.get("gap_work_item_id")): i for i in items}
    if len(by_id) != len(items):
        raise GapGovernanceError("DUPLICATE_WORK_ITEM_ID")
    for item in items:
        gtype = str(item.get("gap_type"))
        if gtype not in ALLOWED_GAP_TYPES:
            raise GapGovernanceError("UNKNOWN_GAP_TYPE:" + gtype)
        if str(item.get("status")) not in ALLOWED_STATUSES:
            raise GapGovernanceError("UNKNOWN_STATUS")
        if str(item.get("version_consequence")) not in VERSION_CONSEQUENCES:
            raise GapGovernanceError("UNKNOWN_VERSION_CONSEQUENCE")
        if not item.get("affected_question_ids"):
            raise GapGovernanceError("MISSING_QUESTION_LINK")
        if not item.get("affected_finding_ids"):
            raise GapGovernanceError("MISSING_FINDING_LINK")
        owner = item.get("owner", {})
        if not isinstance(owner, Mapping):
            raise GapGovernanceError("MISSING_OWNER")
        if not owner.get("owner_identifier"):
            raise GapGovernanceError("MISSING_OWNER")
        if str(owner.get("owner_type")) not in OWNER_TYPES:
            raise GapGovernanceError("UNKNOWN_OWNER_TYPE")
        if owner.get("owner_discovery_required"):
            raise GapGovernanceError("OWNER_DISCOVERY_OPEN")
        if not item.get("resolution_criteria"):
            raise GapGovernanceError("MISSING_RESOLUTION_CRITERIA")
        if not item.get("reentry_condition"):
            raise GapGovernanceError("MISSING_REENTRY")
        if not item.get("reentry_action"):
            raise GapGovernanceError("MISSING_REENTRY")
        if item.get("consumable_as_scientific_truth"):
            raise GapGovernanceError("GAP_LEAKS_AS_TRUTH")
        if str(item.get("status")) == STATUS_RESOLVED:
            have = set(item.get("resolution_evidence", ()))
            need = set(item.get("required_evidence", ()))
            if not need or not need.issubset(have):
                raise GapGovernanceError("RESOLVED_WITHOUT_EVIDENCE")
        if gtype == GAP_TYPE_DATA:
            if item.get("old_data_remains_valid", True) is not True:
                raise GapGovernanceError("HISTORICAL_DATA_MUTATED")
    pairs = {(str(r.get("question_id")),
              str(r.get("gap_work_item_id"))) for r in rels}
    if len(pairs) != 37:
        raise GapGovernanceError("DUPLICATE_RELATIONSHIP")
    for rel in rels:
        if str(rel.get("gap_work_item_id")) not in by_id:
            raise GapGovernanceError("ORPHAN_RELATIONSHIP")
    for edge in edges:
        if str(edge.get("from")) not in by_id:
            raise GapGovernanceError("ORPHAN_DEPENDENCY_FROM")
        if str(edge.get("to")) not in by_id:
            raise GapGovernanceError("ORPHAN_DEPENDENCY_TO")
    _assert_acyclic(by_id, edges)
    _assert_no_equivalent_duplicates(items)
    transitions = list(store.get("observation_gap_transitions", ()))
    seen_transition_questions: set[str] = set()
    required_transition_fields = {
        "question_id", "gap_work_item_id", "missing_observable",
        "required_producer", "required_dataset_domain", "required_fields",
        "semantic_definition", "required_grain", "required_canonical_identity",
        "required_capture_timestamp_event", "required_lineage", "allowed_values_type",
        "minimum_completeness_rule", "evidence_contract_consumer",
        "historical_backfill_possible", "future_collection_only",
        "expected_reentry_trigger_class", "adjudication_evidence_fingerprint",
        "source_implementation_work_item_id", "source_reentry_id", "status",
    }
    for transition in transitions:
        if not isinstance(transition, Mapping):
            raise GapGovernanceError("OBSERVATION_GAP_TRANSITION_INVALID")
        missing = sorted(required_transition_fields - set(transition))
        if missing:
            raise GapGovernanceError("OBSERVATION_GAP_TRANSITION_INCOMPLETE:" + ",".join(missing))
        qid = str(transition["question_id"])
        if qid in seen_transition_questions:
            raise GapGovernanceError("DUPLICATE_OBSERVATION_GAP_TRANSITION:" + qid)
        seen_transition_questions.add(qid)
        source = get_work_item(store, str(transition["source_implementation_work_item_id"]))
        if source.get("status") != STATUS_RESOLVED:
            raise GapGovernanceError("OBSERVATION_GAP_BEFORE_IMPLEMENTATION_EXHAUSTED:" + qid)
        if str(transition.get("status")) != STATUS_OPEN:
            raise GapGovernanceError("NEW_OBSERVATION_GAP_NOT_OPEN:" + qid)
        if transition.get("historical_backfill_possible") is not False:
            raise GapGovernanceError("HISTORICAL_ABSENCE_NOT_PROVEN:" + qid)
        if transition.get("future_collection_only") is not True:
            raise GapGovernanceError("FUTURE_COLLECTION_RULE_MISSING:" + qid)
        # Old persisted transitions predate the canonical field and remain
        # readable without rewriting history. New records must carry it. This
        # check follows the historical invariants so their diagnostic ordering
        # remains stable for malformed legacy inputs.
        from research_engine.control_plane.stage4_identity import (
            Stage4IdentityError, requirement_id_for_question,
            validate_requirement_id,
        )
        canonical_rid = transition.get("observation_requirement_id")
        try:
            if canonical_rid is None:
                # Compatibility is limited to the two already-persisted rows.
                if transition in store.get("observation_gap_transitions", ()):
                    requirement_id_for_question(qid)
                else:
                    raise Stage4IdentityError(
                        "MISSING_OBSERVATION_REQUIREMENT_ID")
            else:
                expected_rid = requirement_id_for_question(qid)
                if validate_requirement_id(str(canonical_rid)) != expected_rid:
                    raise GapGovernanceError(
                        "OBSERVATION_REQUIREMENT_QUESTION_MISMATCH:" + qid)
        except Stage4IdentityError as exc:
            raise GapGovernanceError(str(exc)) from exc
def _assert_acyclic(by_id: dict, edges: list) -> None:
    graph: dict[str, list[str]] = {wid: [] for wid in by_id}
    for edge in edges:
        graph[str(edge.get("from")).strip()].append(
            str(edge.get("to")).strip())
    visiting: set[str] = set()
    done: set[str] = set()

    def visit(node: str, path: list[str]) -> None:
        if node in done:
            return
        if node in visiting:
            joined = "->".join(path + [node])
            raise GapGovernanceError("DEPENDENCY_CYCLE:" + joined)
        visiting.add(node)
        for nxt in graph.get(node, ()):
            visit(nxt, path + [node])
        visiting.discard(node)
        done.add(node)

    for node in graph:
        visit(node, [])


def _dedup_key(item: Mapping[str, Any]) -> tuple:
    if str(item.get("gap_type")) == GAP_TYPE_DATA:
        obs = item.get("missing_observable", ())
        ds = item.get("current_datasets", ())
        ok: tuple = ()
        dk: tuple = ()
        if isinstance(obs, list):
            ok = tuple(sorted(str(o).lower() for o in obs))
        if isinstance(ds, list):
            dk = tuple(sorted(ds))
        return (GAP_TYPE_DATA, ok, dk, str(item.get("gap_id", "")))
    det = item.get("detail", {})
    if isinstance(det, Mapping):
        return (GAP_TYPE_IMPL,
                str(det.get("exact_mismatch", "")).lower(),
                str(det.get("question_contract", "")).lower(),
                str(item.get("gap_id", "")))
    return (GAP_TYPE_IMPL, "", "", "")


def _reuse_key(obs: list[str], ds: list[str]) -> tuple:
    return (GAP_TYPE_DATA, tuple(sorted(str(o).lower() for o in obs)),
            tuple(sorted(ds)))


def _work_reuse_key(item: Mapping[str, Any]) -> tuple | None:
    if str(item.get("gap_type")) != GAP_TYPE_DATA:
        return None
    obs = item.get("missing_observable", ())
    ds = item.get("current_datasets", ())
    if not isinstance(obs, list) or not isinstance(ds, list):
        return None
    return _reuse_key(obs, ds)


def _assert_no_equivalent_duplicates(items: list) -> None:
    seen: dict[tuple, str] = {}
    for item in items:
        key = _dedup_key(item)
        wid = str(item.get("gap_work_item_id"))
        if key in seen and seen[key] != wid:
            raise GapGovernanceError(
                "DUPLICATE_EQUIVALENT_GAP:" + seen[key] + "~" + wid)
        seen[key] = wid


def reference_existing_gap(store: Mapping[str, Any],
                            missing_observable: list[str],
                            datasets: list[str]) -> str | None:
    key = _reuse_key(missing_observable, datasets)
    for item in store.get("work_items", ()):
        if _work_reuse_key(item) == key:
            return str(item.get("gap_work_item_id"))
    return None


def shared_underlying_groups(
        store: Mapping[str, Any]) -> list[list[str]]:
    from collections import defaultdict
    groups: dict[tuple, list[str]] = defaultdict(list)
    for item in store.get("work_items", ()):
        key = _work_reuse_key(item)
        if key is None:
            continue
        groups[key].append(str(item.get("gap_work_item_id")))
    return [sorted(v) for v in groups.values() if len(v) > 1]


def equivalent_pairs(store: Mapping[str, Any]) -> list[list[str]]:
    return shared_underlying_groups(store)


def create_q71_gap(missing: list[str], datasets: list[str]) -> dict:
    raise GapGovernanceError(
        "Q71_NOT_STARTED:reuse existing gap via reference_existing_gap")


def consume_gap_for_science(item: Mapping[str, Any]) -> Mapping[str, Any]:
    raise GapGovernanceError(
        str(item.get("gap_work_item_id")) + ": gap state is not science")
def apply_resolution_evidence(store: dict[str, Any], wid: str,
                              evidence: list[str]) -> dict[str, Any]:
    import copy
    nxt = copy.deepcopy(store)
    tgt = next((i for i in nxt["work_items"]
                if i.get("gap_work_item_id") == wid), None)
    if tgt is None:
        raise GapGovernanceError("UNKNOWN_WORK_ITEM:" + wid)
    have = set(tgt.get("resolution_evidence", ())) | set(evidence)
    tgt["resolution_evidence"] = sorted(have)
    need = set(tgt.get("required_evidence", ()))
    if need.issubset(have):
        tgt["status"] = STATUS_VALIDATION_REQUIRED
        tgt["substate"] = "RESEARCH_RERUN_REQUIRED"
    validate_store(nxt)
    nxt["store_fingerprint"] = _fp(
        {k: v for k, v in nxt.items() if k != "store_fingerprint"})
    return nxt


def supersede_with_new_finding(store: dict[str, Any], wid: str,
                               new_version: int = 2) -> dict[str, Any]:
    import copy
    nxt = copy.deepcopy(store)
    tgt = next((i for i in nxt["work_items"]
                if i.get("gap_work_item_id") == wid), None)
    if tgt is None:
        raise GapGovernanceError("UNKNOWN_WORK_ITEM:" + wid)
    have = set(tgt.get("resolution_evidence", ()))
    need = set(tgt.get("required_evidence", ()))
    if not need.issubset(have):
        raise GapGovernanceError("RESOLVED_WITHOUT_EVIDENCE")
    if tgt.get("status") not in (STATUS_VALIDATION_REQUIRED,
                                 STATUS_VALIDATED):
        raise GapGovernanceError("RESOLUTION_WITHOUT_VALIDATION")
    tgt["status"] = STATUS_RESOLVED
    tgt["assured_finding_version"] = new_version
    tgt["finding_version_expected"] = new_version + 1
    validate_store(nxt)
    nxt["store_fingerprint"] = _fp(
        {k: v for k, v in nxt.items() if k != "store_fingerprint"})
    return nxt


# ---------------------------------------------------------------------------
# Governed scientific re-entry linkage (Stage 4)
#
# A gap never resolves merely because a newer finding exists.  Re-entry
# readiness and resolution both require explicit evidence tying the governed
# work item, the re-entry event, the scoped scientific result, the versioned
# certification and the versioned assured finding together.
# ---------------------------------------------------------------------------

PRE_RERUN_EVIDENCE: dict[str, frozenset[str]] = {
    GAP_TYPE_IMPL: frozenset({
        "code_repair_verified", "targeted_tests_passed",
        "governed_population_aligned",
    }),
    GAP_TYPE_DATA: frozenset({
        "schema_change_deployed", "producer_emitting",
        "sample_condition_satisfied",
    }),
}
REENTRY_LINKAGE_KEYS = (
    "reentry_id",
    "work_item_id",
    "scientific_result_fingerprint",
    "certification_id",
    "finding_id",
    "assurance_status",
)
REENTRY_READY_STATUSES = (STATUS_VALIDATION_REQUIRED, STATUS_VALIDATED)
UNGOVERNED_TRIGGER_REASONS = (STATUS_OPEN, STATUS_READY, STATUS_IN_PROGRESS,
                              STATUS_BLOCKED, STATUS_IMPLEMENTED)


def reentry_ready_statuses() -> tuple[str, ...]:
    """Lifecycle states in which a scoped rerun may be authorized."""
    return REENTRY_READY_STATUSES


def get_work_item(store: Mapping[str, Any], wid: str) -> Mapping[str, Any]:
    item = next((i for i in store.get("work_items", ())
                 if str(i.get("gap_work_item_id")) == str(wid)), None)
    if item is None:
        raise GapGovernanceError("UNKNOWN_WORK_ITEM:" + str(wid))
    return item


def work_item_for_question(
        store: Mapping[str, Any], question_id: str) -> Mapping[str, Any] | None:
    qid = str(question_id).strip().upper()
    for item in store.get("work_items", ()):
        affected = [str(value) for value in item.get("affected_question_ids", ())]
        if qid in affected:
            return item
    return None


def gap_dependency_edges(
        store: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Question-level scientific dependency edges derived from gap governance.

    ``GWI-L6-IMPL -> GWI-G3-IMPL`` becomes ``L6 -> G3``: G3 may not re-enter
    until L6 holds the required CURRENT certified state.
    """
    edges: list[dict[str, Any]] = []
    for edge in store.get("dependency_edges", ()):
        source = get_work_item(store, str(edge.get("from")))
        target = get_work_item(store, str(edge.get("to")))
        for prerequisite in source.get("affected_question_ids", ()):
            for dependent in target.get("affected_question_ids", ()):
                edges.append({
                    "edge_id": f"{prerequisite}->{dependent}",
                    "prerequisite_question_id": str(prerequisite),
                    "dependent_question_id": str(dependent),
                    "dependency_type": str(
                        edge.get("dependency_type", "SCIENTIFIC_DEPENDENCY")),
                    "source": f"gap_governance:{edge.get('from')}->{edge.get('to')}",
                })
    return edges


def mark_reentry_ready(store: dict[str, Any], wid: str,
                       reentry_id: str) -> dict[str, Any]:
    """Move a gap to VALIDATION_REQUIRED once repair evidence is present.

    This is the governed ``OPEN -> repair complete -> VALIDATION_REQUIRED``
    step of the re-entry path.  It requires only the pre-rerun evidence
    subset; the rerun, assurance and finding evidence are applied afterwards
    by :func:`resolve_via_reentry`.
    """
    import copy
    if not str(reentry_id).strip():
        raise GapGovernanceError("REENTRY_ID_REQUIRED")
    nxt = copy.deepcopy(store)
    index = next((i for i, item in enumerate(nxt["work_items"])
                  if str(item.get("gap_work_item_id")) == str(wid)), None)
    if index is None:
        raise GapGovernanceError("UNKNOWN_WORK_ITEM:" + str(wid))
    tgt = dict(nxt["work_items"][index])
    if tgt.get("status") in (STATUS_RESOLVED, STATUS_SUPERSEDED):
        raise GapGovernanceError("REENTRY_ON_CLOSED_GAP:" + str(wid))
    required = PRE_RERUN_EVIDENCE.get(str(tgt.get("gap_type")))
    if required is None:
        raise GapGovernanceError("REENTRY_UNGOVERNED_GAP_TYPE:" + str(wid))
    have = set(tgt.get("resolution_evidence", ()))
    missing = sorted(required - have)
    if missing:
        raise GapGovernanceError(
            "REENTRY_READY_WITHOUT_EVIDENCE:" + str(wid) + ":" + ",".join(missing))
    bindings = list(tgt.get("reentry_bindings", ()))
    if not any(str(b.get("reentry_id")) == str(reentry_id) for b in bindings):
        bindings.append({
            "reentry_id": str(reentry_id),
            "gap_type": str(tgt.get("gap_type")),
            "pre_rerun_evidence": sorted(required),
            "pre_rerun_evidence_role": "PROGRESS_INPUTS_ONLY",
            "governed_satisfaction_required": True,
            "bound_state": STATUS_VALIDATION_REQUIRED,
        })
    tgt["reentry_bindings"] = bindings
    tgt["status"] = STATUS_VALIDATION_REQUIRED
    tgt["substate"] = "AWAITING_GOVERNED_SATISFACTION_AUTHORIZATION"
    nxt["work_items"][index] = tgt
    validate_store(nxt)
    nxt["store_fingerprint"] = _fp(
        {k: v for k, v in nxt.items() if k != "store_fingerprint"})
    return nxt


def validate_reentry_linkage(store: Mapping[str, Any], wid: str,
                             linkage: Mapping[str, Any]) -> Mapping[str, Any]:
    """Fail closed unless the linkage names a governed, verified V2 outcome."""
    if not isinstance(linkage, Mapping):
        raise GapGovernanceError("REENTRY_LINKAGE_MISSING:" + str(wid))
    missing = [key for key in REENTRY_LINKAGE_KEYS
               if not str(linkage.get(key) or "").strip()]
    if missing:
        raise GapGovernanceError(
            "REENTRY_LINKAGE_INCOMPLETE:" + str(wid) + ":" + ",".join(missing))
    tgt = get_work_item(store, wid)
    if str(linkage.get("work_item_id")) != str(wid):
        raise GapGovernanceError("REENTRY_LINKAGE_WORK_ITEM_MISMATCH:" + str(wid))
    if str(linkage.get("assurance_status")) != "VERIFIED":
        raise GapGovernanceError(
            "REENTRY_LINKAGE_ASSURANCE_NOT_VERIFIED:" + str(wid))
    finding_id = str(linkage.get("finding_id"))
    affected = [str(q) for q in tgt.get("affected_question_ids", ())]
    if finding_id.rsplit(":", 1)[0] not in affected:
        raise GapGovernanceError(
            "REENTRY_LINKAGE_FINDING_NOT_AFFECTED:" + str(wid))
    try:
        version = int(finding_id.rsplit(":", 1)[1].lstrip("v"))
    except (IndexError, ValueError):
        raise GapGovernanceError("REENTRY_LINKAGE_FINDING_ID_INVALID:" + finding_id)
    if version < 2:
        raise GapGovernanceError(
            "REENTRY_LINKAGE_FINDING_NOT_SUPERSEDING:" + str(wid))
    return dict(tgt)


def resolve_via_reentry(store: dict[str, Any], wid: str,
                        linkage: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve a gap only through an explicit governed re-entry linkage."""
    import copy
    validate_reentry_linkage(store, wid, linkage)
    finding_id = str(linkage.get("finding_id"))
    version = int(finding_id.rsplit(":", 1)[1].lstrip("v"))
    nxt = copy.deepcopy(store)
    index = next(i for i, item in enumerate(nxt["work_items"])
                 if str(item.get("gap_work_item_id")) == str(wid))
    tgt = dict(nxt["work_items"][index])
    history = list(tgt.get("resolution_linkage", ()))
    history.append({
        "reentry_id": str(linkage.get("reentry_id")),
        "scientific_result_fingerprint": str(
            linkage.get("scientific_result_fingerprint")),
        "certification_id": str(linkage.get("certification_id")),
        "finding_id": finding_id,
        "assurance_status": str(linkage.get("assurance_status")),
    })
    tgt["resolution_linkage"] = history
    nxt["work_items"][index] = tgt
    nxt = apply_resolution_evidence(
        nxt, wid, ["scientific_rerun_completed", "assurance_revalidated",
                   "new_finding_version"])
    nxt = supersede_with_new_finding(nxt, wid, version)
    return nxt


def record_observation_gap_transition(
        store: dict[str, Any], *, requirement: Mapping[str, Any]) -> dict[str, Any]:
    """Queue a newly proven observation gap without altering the frozen 29-gap register."""
    import copy
    nxt = copy.deepcopy(store)
    transitions = list(nxt.get("observation_gap_transitions", ()))
    qid = str(requirement.get("question_id", "")).strip().upper()
    if not qid:
        raise GapGovernanceError("OBSERVATION_GAP_QUESTION_REQUIRED")
    if any(str(item.get("question_id")) == qid for item in transitions):
        raise GapGovernanceError("DUPLICATE_OBSERVATION_GAP_TRANSITION:" + qid)
    # Preserve the established diagnostic ordering, then enforce the new
    # identity boundary before the object is admitted to the store.
    required_transition_fields = {
        "question_id", "gap_work_item_id", "missing_observable",
        "required_producer", "required_dataset_domain", "required_fields",
        "semantic_definition", "required_grain", "required_canonical_identity",
        "required_capture_timestamp_event", "required_lineage",
        "allowed_values_type", "minimum_completeness_rule",
        "evidence_contract_consumer", "historical_backfill_possible",
        "future_collection_only", "expected_reentry_trigger_class",
        "adjudication_evidence_fingerprint",
        "source_implementation_work_item_id", "source_reentry_id", "status",
    }
    missing = sorted(required_transition_fields - set(requirement))
    if missing:
        raise GapGovernanceError(
            "OBSERVATION_GAP_TRANSITION_INCOMPLETE:" + ",".join(missing))
    source = get_work_item(
        store, str(requirement["source_implementation_work_item_id"]))
    if source.get("status") != STATUS_RESOLVED:
        raise GapGovernanceError(
            "OBSERVATION_GAP_BEFORE_IMPLEMENTATION_EXHAUSTED:" + qid)
    if str(requirement.get("status")) != STATUS_OPEN:
        raise GapGovernanceError("NEW_OBSERVATION_GAP_NOT_OPEN:" + qid)
    if requirement.get("historical_backfill_possible") is not False:
        raise GapGovernanceError("HISTORICAL_ABSENCE_NOT_PROVEN:" + qid)
    if requirement.get("future_collection_only") is not True:
        raise GapGovernanceError("FUTURE_COLLECTION_RULE_MISSING:" + qid)
    from research_engine.control_plane.stage4_identity import (
        Stage4IdentityError, requirement_id_for_question,
        validate_requirement_id,
    )
    try:
        rid = validate_requirement_id(str(
            requirement.get("observation_requirement_id") or ""))
        if rid != requirement_id_for_question(qid):
            raise GapGovernanceError(
                "OBSERVATION_REQUIREMENT_QUESTION_MISMATCH:" + qid)
    except Stage4IdentityError as exc:
        raise GapGovernanceError(str(exc)) from exc
    transitions.append(dict(requirement))
    nxt["observation_gap_transitions"] = transitions
    validate_store(nxt)
    nxt["store_fingerprint"] = _fp(
        {k: v for k, v in nxt.items() if k != "store_fingerprint"})
    return nxt


def persist_canonical(store: Mapping[str, Any] | None = None) -> Path:
    material = store if store is not None else build_store()
    validate_store(material)
    CANONICAL_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CANONICAL_STATE_PATH.write_text(
        json.dumps(material, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    return CANONICAL_STATE_PATH
def write_audit_artifacts(store: Mapping[str, Any],
                          base: str = "analysis/assurance"
                          ) -> dict[str, str]:
    out = Path(base)
    out.mkdir(parents=True, exist_ok=True)
    gov_p = out / ("stage4_gap_governance_" + STAMP + ".json")
    gov_p.write_text(json.dumps(store, indent=2, sort_keys=True) + "\n",
                     encoding="utf-8")
    dep = {"edges": list(store.get("dependency_edges", ())),
           "work_items": [str(i.get("gap_work_item_id"))
                          for i in store.get("work_items", ())]}
    dep_p = out / ("stage4_gap_dependency_graph_" + STAMP + ".json")
    dep_p.write_text(json.dumps(dep, indent=2, sort_keys=True) + "\n",
                     encoding="utf-8")
    lines = ["# Stage 4 gap governance (" + STAMP + ")", "",
             "store: `" + str(store.get("store_fingerprint")) + "`",
             "items: " + str(store.get("unique_work_items")),
             "rels: " + str(store.get("certified_gap_relationships")), ""]
    for item in store.get("work_items", ()):
        lines.append("- " + str(item.get("gap_work_item_id"))
                     + " status=" + str(item.get("status")))
    md_p = out / ("stage4_gap_governance_" + STAMP + ".md")
    md_p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    mx = ["# Gap resolution matrix", "",
          "| work_item | type | status | resolution | reentry |",
          "|---|---|---|---|---|"]
    for item in store.get("work_items", ()):
        mx.append("| " + str(item.get("gap_work_item_id"))
                  + " | " + str(item.get("gap_type"))
                  + " | " + str(item.get("status"))
                  + " | " + str(item.get("resolution_criteria"))
                  + " | " + str(item.get("reentry_condition")) + " |")
    mx_p = out / ("stage4_gap_resolution_matrix_" + STAMP + ".md")
    mx_p.write_text("\n".join(mx) + "\n", encoding="utf-8")
    return {"governance": str(gov_p),
            "dependency_graph": str(dep_p),
            "governance_md": str(md_p),
            "resolution_matrix": str(mx_p)}


# ---------------------------------------------------------------------------
# ROOT CHANGE 1 -- dataset-authority resolution.
#
# The frozen work items above still RECORD that their contract once named
# ``shadow_trades``.  That history is immutable and is deliberately preserved.
# What this section changes is the ANSWERABLE question: given a governed gap,
# which dataset is the current authority for its evidence, and is the gap now
# satisfied?  Both answers come from the persisted, fail-closed Root-1 overlay
# rather than from the stale declaration.
# ---------------------------------------------------------------------------

def load_dataset_authority(
        path: Path = DATASET_AUTHORITY_PATH) -> Mapping[str, Any]:
    """Load and validate the persisted shadow_runtime_v1 authority overlay."""
    from research_engine.control_plane import stage4_dataset_authority as A

    if not path.exists():
        raise GapGovernanceError("DATASET_AUTHORITY_OVERLAY_MISSING")
    state = A._read_json(path)
    A.validate_store(state)
    return state


def current_evidence_authority(
        gap_id: str,
        authority: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Current persisted evidence authority for one governed gap.

    Fail closed: an unknown gap, or a gap that Root 1 did not classify, is an
    error rather than a silent fallback to the phantom ``shadow_trades``.
    """
    from research_engine.control_plane import stage4_dataset_authority as A

    resolved = authority if authority is not None else load_dataset_authority()
    for row in resolved["affected_gap_dispositions"]:
        if str(row["gap_id"]) == str(gap_id):
            return {
                "gap_id": str(row["gap_id"]),
                "previous_dataset_reference": str(
                    row["previous_dataset_reference"]),
                "current_dataset": str(row["corrected_dataset"]),
                "current_dataset_version": str(row["corrected_dataset_version"]),
                "disposition": str(row["disposition"]),
                "blocked_by_root_changes": list(row["blocked_by_root_changes"]),
                "remaining_gap_kinds": list(row["remaining_gap_kinds"]),
            }
    raise GapGovernanceError("NO_DATASET_AUTHORITY_FOR_GAP:" + str(gap_id))


def observation_requirement_authority(
        requirement_id: str,
        authority: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Current persisted authority for one observation requirement."""
    from research_engine.control_plane import stage4_dataset_authority as A

    from research_engine.control_plane.stage4_identity import (
        Stage4IdentityError, validate_requirement_id,
    )
    try:
        rid = validate_requirement_id(requirement_id)
    except Stage4IdentityError as exc:
        raise GapGovernanceError(str(exc)) from exc
    resolved = authority if authority is not None else load_dataset_authority()
    transition = A.transition_for(rid, resolved)
    identity = dict(transition.get("evidence_identity") or {})
    if not identity:
        raise GapGovernanceError("EVIDENCE_IDENTITY_MISSING:" + rid)
    # Read-side governance validation: the population identity this consumer is
    # about to trust must resolve against the population authority.  Nothing is
    # coerced and nothing is assumed; an unresolved population fails closed.
    population_registry = A.snapshot_registry()
    resolved_snapshots = [
        population_registry.require(str(snapshot_id)).to_dict()
        for snapshot_id in identity.get("dataset_snapshot_ids", ())]
    return {
        "observation_requirement_id": rid,
        "previous_dataset_reference": str(
            transition["previous_dataset_reference"]),
        "current_dataset": str(transition["corrected_dataset"]),
        "current_dataset_version": str(transition["corrected_dataset_version"]),
        "current_producer": str(transition["corrected_producer"]),
        # -- separated evidence identity (Stage 4 Refinement 2) --
        "dataset_name": str(identity.get("dataset_name")),
        "schema_version": str(identity.get("schema_version")),
        "schema_generation": identity.get("schema_generation"),
        "schema_generation_state": str(
            identity.get("schema_generation_state")),
        "dataset_snapshot_ids": list(
            identity.get("dataset_snapshot_ids", ())),
        "snapshot_identity_state": str(
            identity.get("snapshot_identity_state")),
        "resolved_dataset_snapshots": resolved_snapshots,
        "producer_versions": list(identity.get("producer_versions", ())),
        "producer_lineage_state": str(
            identity.get("producer_lineage_state")),
        "evidence_set_ids": list(identity.get("evidence_set_ids", ())),
        "dataset_version_field_meaning": str(
            identity.get("legacy_dataset_version_field_meaning")),
        "classification": str(transition["shadow_runtime_classification"]),
        "satisfies_current_contract": bool(
            transition["satisfies_current_contract"]),
        "dataset_contract_sufficient": bool(
            transition["dataset_contract_sufficient"]),
        "final_satisfaction_authority": str(
            transition["final_satisfaction_authority"]),
        "satisfaction_decision_id": str(
            transition["satisfaction_decision_id"]),
        "satisfaction_decision_state": str(
            transition["satisfaction_decision_state"]),
        "satisfaction_decision_fingerprint": str(
            transition["satisfaction_decision_fingerprint"]),
        "governed_satisfied": bool(transition["governed_satisfied"]),
        "legacy_resolution_labels_role": "THRESHOLD_INPUTS_ONLY",
        "failing_gates": list(transition["failing_gates"]),
        "blocked_by_root_change": transition["blocked_by_root_change"],
    }


def authoritative_evidence_datasets() -> tuple[str, ...]:
    """Datasets selectable as CURRENT evidence for observation contracts.

    ``shadow_trades`` is deliberately absent: it is a declared, zero-object
    dataset and is not authoritative for any current observation contract.
    """
    return (SHADOW_RUNTIME_DATASET,)


__all__ = [
    "ALLOWED_GAP_TYPES", "CANONICAL_STATE_PATH",
    "DATASET_AUTHORITY_PATH", "SHADOW_RUNTIME_DATASET",
    "EXISTING_FEATURE_MAP", "GAP_TYPE_DATA",
    "GAP_TYPE_IMPL", "GapGovernanceError", "PRE_RERUN_EVIDENCE",
    "REENTRY_LINKAGE_KEYS", "REENTRY_READY_STATUSES",
    "UNGOVERNED_TRIGGER_REASONS", "apply_resolution_evidence",
    "authoritative_evidence_datasets", "build_store",
    "consume_gap_for_science", "create_q71_gap",
    "current_evidence_authority", "equivalent_pairs",
    "gap_dependency_edges", "get_work_item",
    "load_dataset_authority", "mark_reentry_ready",
    "observation_requirement_authority", "persist_canonical",
    "reference_existing_gap",
    "record_observation_gap_transition",
    "reentry_ready_statuses", "resolve_via_reentry",
    "shared_underlying_groups", "supersede_with_new_finding",
    "validate_reentry_linkage", "validate_store",
    "work_item_for_question", "write_audit_artifacts"]
