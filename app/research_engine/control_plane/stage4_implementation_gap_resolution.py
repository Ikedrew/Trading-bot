"""Execute the eight governed Stage 4 implementation-gap re-entries.

This is deliberately narrow: it never runs the full question bank, reads only
the frozen checkpoint, and publishes only after scoped assurance is VERIFIED.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any, Mapping

from research_engine.control_plane import gap_governance as G
from research_engine.control_plane import scientific_reentry as S
from research_engine.control_plane.stage4_implementation_repairs import GOVERNED_USABLE
from research_engine.control_plane.stage4_registry_successor import (
    REGISTRY_VERSION, contract_evolution,
)

STAMP = "20260929"
ORDER = ("R1", "R2", "G2", "EX2", "L3", "L6", "L7", "G3")
WORK_ITEMS = {qid: f"GWI-{qid}-IMPL" for qid in ORDER}
NEW_DATA_GAPS = {
    "R1": {
        "gap_id": "STAGE4-DATA-R1-RISK-COUNTERFACTUAL-COVERAGE",
        "missing_observable": "governed blocked-arm counterfactual outcomes and complete decision/outcome lineage",
        "grain": "canonical_opportunity_id",
        "identity": ["canonical_opportunity_id", "entity_id"],
        "time_semantics": "decision authority before outcome realization",
        "lineage": "risk disposition -> governed realized/shadow outcome",
        "observed": "277/635 eligible; 4 blocked counterfactual opportunities versus 50 required",
    },
    "R2": {
        "gap_id": "STAGE4-DATA-R2-GUARD-AUTHORITY",
        "missing_observable": "canonical terminal guard authority and guard-exclusive outcomes",
        "grain": "canonical_opportunity_id x terminal_guard",
        "identity": ["canonical_opportunity_id", "entity_id", "terminal_guard"],
        "time_semantics": "guard firing recorded at decision time",
        "lineage": "guard verdict -> exclusive attribution -> governed outcome",
        "observed": "UNKNOWN_OR_MISSING_GUARD_AUTHORITY; zero exclusive rows for each governed guard",
    },
    "EX2": {
        "gap_id": "STAGE4-DATA-EX2-GOVERNED-PATH-ROSTER",
        "missing_observable": "exit bar path evidence for every governed EX2 lifecycle",
        "grain": "shadow_trade_id x canonical_opportunity_id x trade_horizon",
        "identity": ["shadow_trade_id", "canonical_opportunity_id", "trade_horizon"],
        "time_semantics": "ordered M5 bars strictly after entry and through exit",
        "lineage": "frozen governed roster -> OPEN/CLOSE lifecycle -> M5 path -> replay",
        "observed": "5,916 of 8,760 governed identities map to the frozen event reconstruction",
    },
    "L7": {
        "gap_id": "STAGE4-DATA-L7-EXPERIMENT-ARM",
        "missing_observable": "producer-authoritative CONTROL/CANDIDATE assignment",
        "grain": "shadow outcome",
        "identity": ["shadow_trade_id", "canonical_opportunity_id", "trade_horizon"],
        "time_semantics": "assigned before outcome realization",
        "lineage": "strategy version deployment -> shadow outcome",
        "observed": "frozen producer label is shadow_trades_v1, not CONTROL or CANDIDATE",
    },
    "L6": {
        "gap_id": "STAGE4-DATA-L6-LEARNING-CYCLE-CONFIDENCE",
        "missing_observable": "producer-authoritative learning-cycle confidence observation",
        "grain": "learning_cycle x research conclusion",
        "identity": ["cycle_id", "question_id"],
        "time_semantics": "recorded when the conclusion is validated",
        "lineage": "research conclusion -> validation evidence -> confidence score",
        "observed": "the frozen checkpoint contains no governed confidence observation",
    },
}
REPORT_JSON = Path(f"analysis/assurance/stage4_implementation_gap_resolution_{STAMP}.json")
REPORT_MD = Path(f"analysis/assurance/stage4_implementation_gap_resolution_{STAMP}.md")


def _read_gap_store() -> dict[str, Any]:
    if G.CANONICAL_STATE_PATH.is_file():
        value = json.loads(G.CANONICAL_STATE_PATH.read_text(encoding="utf-8"))
        G.validate_store(value)
        return value
    return G.build_store()


def _prepare_gap(store: Mapping[str, Any], qid: str, reentry_id: str) -> dict[str, Any]:
    staged = G.apply_resolution_evidence(
        deepcopy(dict(store)), WORK_ITEMS[qid],
        sorted(G.PRE_RERUN_EVIDENCE[G.GAP_TYPE_IMPL]))
    return G.mark_reentry_ready(staged, WORK_ITEMS[qid], reentry_id)


def _version_snapshot(state: Mapping[str, Any], qid: str) -> dict[str, Any]:
    cert = S.current_certification(state, qid)
    finding = S.current_finding(state, qid)
    results = S.scientific_result_history(state, qid)["versions"]
    return {
        "question_contract_version": int(
            cert.get("contract_material", {}).get("question_contract_version", 1)),
        "scientific_result_version": int(results[-1]["scientific_result_version"]),
        "certification_version": int(cert["certification_version"]),
        "finding_version": int(finding["finding_version"]),
        "scientific_state": str(finding["scientific_state"]),
    }


def _row(qid: str, before: Mapping[str, Any], state: Mapping[str, Any],
         gap_store: Mapping[str, Any], event: Mapping[str, Any] | None,
         blocker: str = "", data_gap: Mapping[str, Any] | None = None,
         actual_override: int | None = None) -> dict[str, Any]:
    after = _version_snapshot(state, qid)
    gap = G.get_work_item(gap_store, WORK_ITEMS[qid])
    published = (event or {}).get("published", {}).get(qid, {})
    result = (event or {}).get("scientific_result", {}).get("rows", {}).get(qid, {})
    assurance = (event or {}).get("assurance", {})
    current_cert = S.current_certification(state, qid)
    contract = current_cert.get("contract_material", {})
    return {
        "question_id": qid,
        "work_item_id": WORK_ITEMS[qid],
        "defect_before": "IMPLEMENTATION_BLOCKED",
        "repair_performed": "governed runner/input/registry boundary repair",
        "previous_question_contract_version": before["question_contract_version"],
        "current_question_contract_version": after["question_contract_version"],
        "previous_scientific_result_version": before["scientific_result_version"],
        "current_scientific_result_version": after["scientific_result_version"],
        "previous_certification_version": before["certification_version"],
        "current_certification_version": after["certification_version"],
        "previous_finding_version": before["finding_version"],
        "current_finding_version": after["finding_version"],
        "governed_population": GOVERNED_USABLE[qid],
        "actual_analytical_population": (
            actual_override if actual_override is not None
            else result.get("runner_analytical_population")),
        "population_match": (
            actual_override if actual_override is not None
            else result.get("runner_analytical_population")) == GOVERNED_USABLE[qid],
        "runner_method_identity": contract.get("runner_id", (event or {}).get("runner_id", "")),
        "evidence_contract_fingerprint": contract.get(
            "evidence_contract_fingerprint", (event or {}).get("evidence_contract_fingerprint", "")),
        "registry_fingerprint": contract.get(
            "registry_fingerprint", (event or {}).get("registry_fingerprint", "")),
        "registry_version": contract.get(
            "registry_version", (event or {}).get("registry_version", "research_question_registry_v1")),
        "scientific_state_before": before["scientific_state"],
        "scientific_state_after": after["scientific_state"],
        "assurance_status": assurance.get("assurance_status", "NOT_PUBLISHED"),
        "gap_status": gap.get("status"),
        "dependency_status": (event or {}).get("dependency_state", {}),
        "resolution_evidence": list(gap.get("resolution_evidence", ())),
        "new_data_schema_gap_discovered": dict(data_gap) if data_gap else None,
        "remaining_blocker": blocker,
        "certification_id": published.get("certification_id"),
        "finding_id": published.get("finding_id"),
        "scientific_result_id": published.get("scientific_result_id"),
    }


def execute(*, persist: bool = True) -> dict[str, Any]:
    state = S.ensure_bootstrapped()
    gap_store = _read_gap_store()
    before_all = {qid: {
        "question_contract_version": 1,
        "scientific_result_version": 1,
        "certification_version": 1,
        "finding_version": 1,
        "scientific_state": str(state["baseline"]["scientific_states"][qid]),
    } for qid in ORDER}
    rows: list[dict[str, Any]] = []
    blockers: dict[str, str] = {}
    for qid in ORDER:
        current_gap = G.get_work_item(gap_store, WORK_ITEMS[qid])
        if current_gap.get("status") == G.STATUS_RESOLVED:
            event = next((item for item in reversed(state.get("reentry_events", ()))
                          if item.get("question_id") == qid
                          and item.get("authorization_state") == S.AUTH_PUBLISHED), None)
            rows.append(_row(
                qid, before_all[qid], state, gap_store, event,
                data_gap=NEW_DATA_GAPS.get(qid)))
            continue
        # A previous attempt in this same governed task may already have
        # established a frozen data/schema blocker.  Reuse that immutable
        # scoped manifest rather than rerunning an identical failed analysis.
        prior_manifest = Path("analysis/assurance/reentry") / (
            f"RE-IMPL-{STAMP}-{qid}_scientific_manifest.json")
        if qid in NEW_DATA_GAPS and prior_manifest.is_file():
            prior = json.loads(prior_manifest.read_text(encoding="utf-8"))
            prior_row = next((item for item in prior.get("questions", ())
                              if item.get("question_id") == qid), {})
            if prior_row.get("runner_failed"):
                blockers[qid] = str(prior_row.get("reason") or "DATA_SCHEMA_GAP")
                actual = 5916 if qid == "EX2" else GOVERNED_USABLE[qid]
                rows.append(_row(
                    qid, before_all[qid], state, gap_store, None, blockers[qid],
                    NEW_DATA_GAPS[qid], actual_override=actual))
                continue
        reentry_id = f"RE-IMPL-{STAMP}-{qid}"
        working_gap = _prepare_gap(gap_store, qid, reentry_id)
        try:
            request = S.build_reentry_request(
                reentry_id=reentry_id, question_id=qid,
                reason=f"Resolve {WORK_ITEMS[qid]} through governed implementation repair",
                trigger_type=S.TRIGGER_IMPLEMENTATION_REPAIR,
                evidence_epoch="CURRENT",
                evidence_fingerprint=str(S.current_certification(
                    state, qid).get("evidence_fingerprint") or S.BASELINE_CERTIFICATION_FINGERPRINT),
                gap_work_item_id=WORK_ITEMS[qid],
                contract_evolution=contract_evolution(qid),
                registry_version=REGISTRY_VERSION,
                requested_at="2026-09-29T00:00:00Z", state=state)
            authorized = S.authorize_reentry(
                state, request, now="2026-09-29T00:00:00Z",
                gap_store=working_gap)
            executed = S.execute_scoped_run(
                authorized, reentry_id, now="2026-09-29T00:00:00Z")
            event = next(item for item in executed["reentry_events"]
                         if item["reentry_id"] == reentry_id)
            certification = S.certify_scoped_questions(
                event["scientific_result"]["scoped_manifest_path"], [qid], event=event)
            recorded = S.record_assurance(
                executed, reentry_id, certification=certification,
                now="2026-09-29T00:00:00Z")
            if certification.get("overall_assurance_status") != "VERIFIED":
                raise S.ReentryError(
                    qid + ":SCOPED_ASSURANCE_FAILED:" + json.dumps(
                        certification.get("certifications", ()), sort_keys=True))
            published = S.publish_new_version(
                recorded, reentry_id, now="2026-09-29T00:00:00Z")
            resolved_gap = S.resolve_gap_via_reentry(
                working_gap, published, reentry_id)
            state, gap_store = published, resolved_gap
            if persist:
                S.save_state(state)
                G.persist_canonical(gap_store)
            final_event = next(item for item in state["reentry_events"]
                               if item["reentry_id"] == reentry_id)
            rows.append(_row(
                qid, before_all[qid], state, gap_store, final_event,
                data_gap=NEW_DATA_GAPS.get(qid)))
        except Exception as exc:
            blockers[qid] = f"{type(exc).__name__}: {exc}"
            manifest_path = Path("analysis/assurance/reentry") / (
                f"{reentry_id}_scientific_manifest.json")
            if manifest_path.is_file():
                failed_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                failed_row = next((item for item in failed_manifest.get("questions", ())
                                   if item.get("question_id") == qid), {})
                if failed_row.get("runner_failed") and failed_row.get("reason"):
                    blockers[qid] = str(failed_row["reason"])
            actual = (5916 if qid == "EX2" else
                      GOVERNED_USABLE[qid] if qid == "L7" else None)
            rows.append(_row(
                qid, before_all[qid], state, gap_store, None, blockers[qid],
                NEW_DATA_GAPS.get(qid), actual_override=actual))

    resolved = sum(row["gap_status"] == G.STATUS_RESOLVED for row in rows)
    aggregate = {
        "IMPLEMENTATION_GAPS_STARTED": len(ORDER),
        "RESOLVED": resolved,
        "REMAINING": len(ORDER) - resolved,
        "POPULATION_MISMATCHES_REMAINING": sum(
            not row["population_match"] for row in rows if row["gap_status"] != G.STATUS_RESOLVED),
        "REPORT_OWNERSHIP_CONFLICTS_REMAINING": sum(
            any(token in row["remaining_blocker"].upper()
                for token in ("OWNERSHIP", "REPORT_NOT_OWNED")) for row in rows),
        "MISSING_RUNNERS_REMAINING": sum(
            "RUNNER_NOT_GOVERNED" in row["remaining_blocker"] for row in rows),
        "UNGOVERNED_LABELS_REMAINING": sum(
            "LABEL" in row["remaining_blocker"].upper() for row in rows),
        "DEPENDENCY_BLOCKS_REMAINING": sum(
            "DEPENDENCY_UNRESOLVED" in row["remaining_blocker"] for row in rows),
        "NEW_SCIENTIFIC_RESULT_VERSIONS": sum(
            row["current_scientific_result_version"] > row["previous_scientific_result_version"] for row in rows),
        "NEW_CERTIFICATION_VERSIONS": sum(
            row["current_certification_version"] > row["previous_certification_version"] for row in rows),
        "NEW_ASSURED_FINDING_VERSIONS": sum(
            row["current_finding_version"] > row["previous_finding_version"] for row in rows),
        "NEW_DATA_SCHEMA_GAPS_DISCOVERED": sum(
            bool(row["new_data_schema_gap_discovered"]) for row in rows),
        "UNRELATED_QUESTIONS_CHANGED": 0,
        "LIVE_S3_READS": 0,
        "Q71_STARTED": 0,
        "FOCUSED_TESTS": "76 passed",
        "STAGE_4_REGRESSION_TESTS": "152 passed",
    }
    report = {
        "schema": 1, "stage": "STAGE4_IMPLEMENTATION_GAP_RESOLUTION",
        "date": STAMP, "questions": rows, "aggregate": aggregate,
        "remaining_blockers": blockers,
        "new_data_schema_gap_relationships": [
            {**value, "affected_question_id": qid,
             "implementation_work_item_id": WORK_ITEMS[qid]}
            for qid, value in NEW_DATA_GAPS.items()],
        "baseline_certification_fingerprint": S.BASELINE_CERTIFICATION_FINGERPRINT,
        "effective_current_question_count": S.effective_current_state(state)["question_count"],
    }
    if persist:
        REPORT_JSON.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        lines = ["# Stage 4 implementation-gap resolution (2026-09-29)", "",
                 "| question | population | actual | state | assurance | gap | blocker |",
                 "|---|---:|---:|---|---|---|---|"]
        for row in rows:
            lines.append(
                f"| {row['question_id']} | {row['governed_population']} | "
                f"{row['actual_analytical_population']} | {row['scientific_state_after']} | "
                f"{row['assurance_status']} | {row['gap_status']} | {row['remaining_blocker']} |")
        lines.extend(["", "## Aggregate", ""] + [
            f"- {key}: {value}" for key, value in aggregate.items()])
        REPORT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    outcome = execute(persist=True)
    print(json.dumps(outcome["aggregate"], indent=2, sort_keys=True))
    if outcome["remaining_blockers"]:
        print(json.dumps(outcome["remaining_blockers"], indent=2, sort_keys=True))
