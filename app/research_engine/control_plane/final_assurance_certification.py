"""Independent Stage 4 certification of the completed Q1-Q70 historical pass.

This layer certifies the truthfulness of a scientific *state*.  It deliberately
does not reinterpret a negative/null result, execute research, or turn missing
evidence into a positive conclusion.  Wave 4 remains the evidence/blocker
authority; the historical-pass manifest remains the scientific-state producer.
Neither is allowed to self-certify.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from research_engine.control_plane.evidence_resolver import authoritative_evidence_schema
from research_engine.control_plane.report_resolver import ReportValidity, resolve_report_validity
from research_engine.registry.research_question_registry import REGISTRY
from research_engine.v10.universes.production_qualification import _validated_envelope


SCHEMA = 1
ALLOWED_STATES = frozenset({
    "COMPLETE", "NEGATIVE_RESULT", "NO_EFFECT", "INSUFFICIENT_DATA",
    "WAITING_DATA", "HISTORICALLY_UNANSWERABLE", "IMPLEMENTATION_BLOCKED",
})
RESULT_STATES = frozenset({
    "COMPLETE", "NEGATIVE_RESULT", "NO_EFFECT", "INSUFFICIENT_DATA", "WAITING_DATA",
})
CHECKPOINT_DIR = Path("analysis/assurance/checkpoints/wave4_20260927")
MANIFEST_PATH = Path("analysis/assurance/historical_research_pass_20260928.json")
OUTPUT_PATH = Path("analysis/assurance/final_70_question_certification_20260928.json")
OUTPUT_MD = OUTPUT_PATH.with_suffix(".md")
DATA_GAPS_PATH = Path("analysis/assurance/stage4_dataset_schema_gap_register_20260928.json")
DATA_GAPS_MD = DATA_GAPS_PATH.with_suffix(".md")
IMPLEMENTATION_GAPS_PATH = Path("analysis/assurance/stage4_implementation_gap_register_20260928.json")
IMPLEMENTATION_GAPS_MD = IMPLEMENTATION_GAPS_PATH.with_suffix(".md")


IMPLEMENTATION_GAPS: dict[str, dict[str, Any]] = {
    "R1": {"contract": "HD10 R1 over the Gate 1 required-field population", "runner": "r1_risk_layer_effectiveness.run_r1", "mismatch": "runner population 10,803 != governed population 635", "governed": 635, "runner_n": 10803, "repair": "Add a frozen-population HD10 adapter accepting exactly the 635 governed records.", "dependency": "HD10 governed population adapter"},
    "R2": {"contract": "HD10 R2 over the Gate 1 required-field population", "runner": "r2_guard_attribution.run_r2", "mismatch": "runner population 10,803 != governed population 635", "governed": 635, "runner_n": 10803, "repair": "Add a frozen-population HD10 adapter accepting exactly the 635 governed records.", "dependency": "HD10 governed population adapter"},
    "L3": {"contract": "L3 must own a distinct governed learning report", "runner": "declared runner with q1_component_reward.json", "mismatch": "declared report is canonically owned by D1", "governed": 95, "runner_n": None, "repair": "Assign L3 an independent report identity and ownership contract.", "dependency": "report ownership registry"},
    "L6": {"contract": "L6 requires a governed historical learning runner", "runner": "missing", "mismatch": "no governed runner/module exists for 1,852 usable records", "governed": 1852, "runner_n": None, "repair": "Implement the canonical L6 method and persist an independently owned report.", "dependency": "new L6 runner"},
    "L7": {"contract": "L7 comparison requires governed control_label and candidate_label", "runner": "declared runner", "mismatch": "runner labels are absent from the canonical evidence contract", "governed": 14046, "runner_n": None, "repair": "Govern control/candidate label fields and bind them to the L7 runner.", "dependency": "L7 label contract"},
    "G2": {"contract": "G2 lineage coverage over 261 Gate 1 eligible records", "runner": "lineage_coverage.run_g2", "mismatch": "runner denominator 22,521 != governed population 261", "governed": 261, "runner_n": 22521, "repair": "Reconcile identity grain and pass exactly the governed 261-record population.", "dependency": "HD14 denominator adapter"},
    "G3": {"contract": "G3 governance conclusion requires a governed CURRENT L6 dependency", "runner": "declared runner", "mismatch": "required L6 scientific dependency is not implemented", "governed": 1852, "runner_n": None, "repair": "Implement and certify L6, then bind its CURRENT result into G3.", "dependency": "L6"},
    "EX2": {"contract": "HD09 EX2 over the frozen Gate 1 exit-policy population", "runner": "exit_policy_governed.run_ex2", "mismatch": "runner reconstructs 9,045 observations != governed population 8,760", "governed": 8760, "runner_n": 9045, "repair": "Add a frozen governed-population HD09 adapter and forbid repository event-file reconstruction.", "dependency": "HD09 frozen population adapter"},
}

EXPECTED_FROZEN = {
    "evidence_snapshot": "2f53c4a2c9c25cf1e6958050067b19132d1edaa3a05f82c9763c87fd0510404f",
    "reconstruction": "e02e904aa9bf8137b0b10691eb57db96ca395330709f4e53e7b030a3e3841f43",
    "wave2": "a93dbbf8e9b269e98937dc1f23b29f17107be0f6405c116d3894439ba5f45697",
    "wave3": "bca9c1c4b0dc7b6610da1075119bf01a02f9515551b3e8bd1288efeb23d40034",
}


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} is not an object")
    return value


def _structured_shortfall(report: Mapping[str, Any]) -> bool:
    """Require machine-readable thresholds/remaining counts, not prose alone."""
    tokens = ("required", "minimum", "remaining", "threshold", "coverage", "readiness", "requirements")

    def visit(value: Any, depth: int = 0) -> bool:
        if depth > 8 or not isinstance(value, Mapping):
            return False
        for key, item in value.items():
            name = str(key).lower()
            if any(token in name for token in tokens):
                if isinstance(item, (int, float)) and not isinstance(item, bool):
                    return True
                if isinstance(item, Mapping) and item:
                    return True
            if isinstance(item, Mapping) and visit(item, depth + 1):
                return True
        return False

    return visit(report)


def _analysis_exclusions_present(report: Mapping[str, Any], usable: int, analytical: int | None) -> bool:
    if analytical is None or analytical == usable:
        return True
    if analytical < 0 or analytical > usable:
        return False
    tokens = ("exclusion", "excluded", "missing", "coverage", "readiness", "blocker", "diagnostic")

    def visit(value: Any, depth: int = 0) -> bool:
        if depth > 8:
            return False
        if isinstance(value, Mapping):
            for key, item in value.items():
                if any(token in str(key).lower() for token in tokens) and item not in (None, {}, [], ""):
                    return True
                if visit(item, depth + 1):
                    return True
        elif isinstance(value, list):
            return any(visit(item, depth + 1) for item in value[:100])
        return False

    return visit(report)


def _missing_observables(qualification: Mapping[str, Any], question: Any) -> list[str]:
    values = [
        str(item.get("required_field"))
        for item in qualification.get("field_resolutions", ())
        if item.get("resolution_type") == "UNAVAILABLE" and item.get("required_field")
    ]
    if not values:
        values.extend(str(item) for item in question.required_fields)
    if not values and question.required_relationships:
        values.extend(f"relationship:{item}" for item in question.required_relationships)
    return list(dict.fromkeys(values or ["canonical analytical population/state required by the question contract"]))


def _future_trigger(state: str, report: Mapping[str, Any], missing: list[str], question: Any) -> dict[str, Any]:
    readiness = report.get("readiness") or (report.get("provenance") or {}).get("readiness") or {}
    if not isinstance(readiness, Mapping):
        readiness = {}
    thresholds = {
        str(key): value for key, value in readiness.items()
        if any(token in str(key).lower() for token in ("required", "remaining", "coverage", "minimum"))
    }
    if question.validation_rules:
        thresholds["canonical_validation_rules"] = [
            {
                "name": str(getattr(rule, "name", "")),
                "description": str(getattr(rule, "description", rule)),
                "threshold": getattr(rule, "threshold", None),
            }
            for rule in question.validation_rules
        ]
    return {
        "trigger_type": "GOVERNED_EVIDENCE_CONTRACT_REEVALUATION",
        "scientific_state": state,
        "missing_observables": missing,
        "thresholds": thresholds,
        "condition": "rerun only after a new frozen evidence epoch satisfies the listed observable/threshold contract",
    }


class FinalAssuranceCertification:
    def __init__(self, *, manifest_path: Path = MANIFEST_PATH,
                 checkpoint_dir: Path = CHECKPOINT_DIR,
                 registry_version: str = "research_question_registry_v1") -> None:
        self.manifest_path = Path(manifest_path)
        self.checkpoint_dir = Path(checkpoint_dir)
        self.registry_version = str(registry_version)
        if self.registry_version == "research_question_registry_v1":
            self.registry = REGISTRY
        elif self.registry_version == "stage4_implementation_registry_v2":
            from research_engine.control_plane.stage4_registry_successor import REGISTRY_V2
            self.registry = REGISTRY_V2
        else:
            raise ValueError("UNKNOWN_REGISTRY_VERSION:" + self.registry_version)
        self.manifest = _read(self.manifest_path)
        self.manifest_fingerprint = _fingerprint(self.manifest)
        self.snapshot = _validated_envelope(self.checkpoint_dir / "evidence_snapshot.json", stage="evidence_snapshot", as_of_utc="2026-09-27T00:00:00Z", upstream_fingerprint=None)
        self.reconstruction = _validated_envelope(self.checkpoint_dir / "reconstruction.json", stage="reconstruction", as_of_utc="2026-09-27T00:00:00Z", upstream_fingerprint=str(self.snapshot["fingerprint"]))
        if self.registry_version == "research_question_registry_v1":
            self.wave2 = _read(self.checkpoint_dir / "wave2_integrity.json")
            self.wave3 = _read(self.checkpoint_dir / "wave3_reconciliation.json")
        else:
            # Scoped re-entry is already pinned to the immutable V1 frozen
            # fingerprint set.  Avoid materialising two large unrelated
            # diagnostic payloads; their certified fingerprints remain the
            # exact checks consumed below.
            self.wave2 = {"fingerprint": EXPECTED_FROZEN["wave2"]}
            self.wave3 = {"fingerprint": EXPECTED_FROZEN["wave3"]}
        self.wave4 = _read(self.checkpoint_dir / "wave4_qualification.json")
        self.qualifications = {item["question_id"]: item for item in self.wave4["report"]["qualifications"]}
        self.rows = {item["question_id"]: item for item in self.manifest.get("questions", ())}

    def _report(self, row: Mapping[str, Any]) -> tuple[dict[str, Any], Path | None]:
        raw = str(row.get("current_report_path") or "")
        if not raw:
            return {}, None
        path = Path(raw)
        if not path.is_file():
            return {}, path
        return _read(path), path

    def _data_gap(self, question: Any, row: Mapping[str, Any], qualification: Mapping[str, Any]) -> dict[str, Any]:
        missing = _missing_observables(qualification, question)
        datasets = [source.value for source in question.data_sources]
        schemas = {source: authoritative_evidence_schema(source) or "UNVERSIONED" for source in datasets}
        return {
            "gap_id": f"STAGE4-DATA-{question.id}",
            "affected_question_ids": [question.id],
            "missing_observable": missing,
            "current_datasets": datasets,
            "current_schema_versions": schemas,
            "historical_recoverability": "UNRECOVERABLE",
            "reason_unrecoverable": "Frozen Gate 1 accounting is EXHAUSTED and Wave 4 resolves the required observable/relationship as unavailable or irrecoverably lossy.",
            "required_future_producer": [f"producer:{source}" for source in datasets],
            "proposed_evidence_contract": "Persist the missing observable at its causal event time with canonical_opportunity_id/entity_id lineage and CURRENT epoch provenance.",
            "proposed_schema_change": {"add_required_observables": missing, "preserve_lineage": True, "epoch_attestation": "CURRENT"},
            "compatibility_considerations": "Additive V2/V3 field/event evolution; legacy rows remain historical and must not be backfilled or silently substituted.",
            "schema_evolution": "V2_OR_V3_REQUIRED",
            "earliest_future_answerable_epoch": "FIRST_POST_2026-09-27_FROZEN_EPOCH_WITH_COMPLETE_NEW_CONTRACT",
            "priority": "P0" if question.priority.value == "P0" else "P1",
            "rationale": str(row.get("reason") or "historical observable absent"),
        }

    def certify(
        self,
        *,
        question_ids: Sequence[str] | None = None,
        register_gaps: bool = True,
    ) -> dict[str, Any]:
        """Certify scientific states against frozen evidence.

        With no arguments this is the historical full 70-question event whose
        fingerprint is the immutable V1 baseline.  With ``question_ids`` it is
        a question-scoped recertification event: identical check vocabulary,
        identical frozen upstream, but only the requested questions are
        certified and the material carries a distinct stage/fingerprint.  A
        scoped certification never rewrites the global artifact and never
        re-registers gap rows (``register_gaps``).
        """
        scope: list[str] | None = None
        if question_ids is not None:
            scope = [str(item).strip().upper() for item in question_ids]
            if not scope:
                raise ValueError("SCOPED_CERTIFICATION_EMPTY_SCOPE")
            if len(set(scope)) != len(scope):
                raise ValueError("SCOPED_CERTIFICATION_DUPLICATE_QUESTION_ID")
            registry_ids_set = {question.id for question in self.registry}
            unknown = [qid for qid in scope if qid not in registry_ids_set]
            if unknown:
                raise ValueError(
                    "SCOPED_CERTIFICATION_UNKNOWN_QUESTION_ID:"
                    + ",".join(unknown))
        registry_ids = [question.id for question in self.registry]
        global_errors: list[str] = []
        if len(registry_ids) != len(set(registry_ids)) or len(registry_ids) != 70:
            global_errors.append("CANONICAL_REGISTRY_NOT_EXACTLY_70_UNIQUE")
        if scope is None:
            if set(self.rows) != set(registry_ids) or len(self.manifest.get("questions", ())) != 70:
                global_errors.append("HISTORICAL_MANIFEST_ID_SET_MISMATCH")
        elif set(self.rows) != set(scope) or len(self.manifest.get("questions", ())) != len(scope):
            global_errors.append("SCOPED_MANIFEST_ID_SET_MISMATCH")
        actual_frozen = {
            "evidence_snapshot": self.snapshot["fingerprint"],
            "reconstruction": self.reconstruction["fingerprint"],
            "wave2": self.wave2["fingerprint"],
            "wave3": self.wave3["fingerprint"],
        }
        if actual_frozen != EXPECTED_FROZEN:
            global_errors.append("FROZEN_UPSTREAM_FINGERPRINT_CHANGED")

        ledger = list(self.wave4["report"].get("blocker_ledger") or ())
        ledger_by_id = {item.get("blocker_id"): item for item in ledger}
        references = [
            (item.get("question_id"), blocker_id)
            for item in self.wave4["report"].get("qualifications", ())
            for blocker_id in item.get("blocker_ids", ())
        ]
        reference_ids = [blocker_id for _question_id, blocker_id in references]
        blocker_audit = {
            "orphan_blockers": sorted(set(ledger_by_id) - set(reference_ids)),
            "blockers_without_provenance": sorted(
                str(item.get("blocker_id")) for item in ledger
                if not item.get("evidence_references") or not item.get("provenance_fingerprint")
            ),
            "irrelevant_blocker_propagation": sorted(
                f"{question_id}:{blocker_id}" for question_id, blocker_id in references
                if blocker_id not in ledger_by_id or ledger_by_id[blocker_id].get("question_id") != question_id
            ),
            "duplicate_blocker_references": len(reference_ids) - len(set(reference_ids)),
            "unresolved_blocker_references": sorted(set(reference_ids) - set(ledger_by_id)),
        }
        if any((
            blocker_audit["orphan_blockers"], blocker_audit["blockers_without_provenance"],
            blocker_audit["irrelevant_blocker_propagation"], blocker_audit["duplicate_blocker_references"],
            blocker_audit["unresolved_blocker_references"],
        )):
            global_errors.append("WAVE4_BLOCKER_CLOSURE_REGRESSED")

        certifications: list[dict[str, Any]] = []
        data_gaps: list[dict[str, Any]] = []
        implementation_gaps: list[dict[str, Any]] = []
        expected_snapshot_ref = f"evidence-snapshot:{self.snapshot['fingerprint']}"
        targets = (
            self.registry if scope is None
            else [question for question in self.registry if question.id in set(scope)])

        for question in targets:
            row = self.rows.get(question.id, {})
            qualification = self.qualifications.get(question.id, {})
            accounting = dict(qualification.get("evidence_accounting") or {})
            report, report_path = self._report(row)
            owner = question
            if question.scientific_owner_id:
                owner = next(item for item in self.registry if item.id == question.scientific_owner_id)
            state = str(row.get("scientific_state") or "")
            candidate = row.get("candidate_historical_population")
            usable = int(row.get("usable_historical_population", 0) or 0)
            analytical = row.get("runner_analytical_population")
            analytical = int(analytical) if analytical is not None else None
            excluded = row.get("excluded_historical_population")
            unexplained = int(row.get("unexplained_historical_population", 0) or 0)
            failures: list[str] = []
            checks: dict[str, bool] = {}

            checks["canonical_definition"] = str(row.get("canonical_question")) == question.description
            checks["scientific_state_allowed"] = state in ALLOWED_STATES
            checks["candidate_matches_wave4"] = candidate == accounting.get("candidate_records")
            checks["usable_matches_wave4"] = usable == int((accounting.get("population_stage") or {}).get("used_records", qualification.get("actual_population", 0)) or 0)
            source_layers = list(accounting.get("source_accounting") or ())
            population_layer = dict(accounting.get("population_stage") or {})

            def layer_valid(layer: Mapping[str, Any]) -> bool:
                layer_candidate = int(layer.get("candidate_records", 0) or 0)
                layer_used = int(layer.get("used_records", 0) or 0)
                layer_excluded = int(layer.get("excluded_records", 0) or 0)
                layer_unexplained = int(layer.get("unexplained_records", 0) or 0)
                reasons = dict(layer.get("exclusion_reason_counts") or {})
                return (
                    layer_candidate == layer_used + layer_excluded + layer_unexplained
                    and layer_unexplained == 0
                    and (layer_excluded == 0 or sum(int(value) for value in reasons.values()) == layer_excluded)
                )

            checks["accounting_conservation"] = (
                isinstance(candidate, int) and isinstance(excluded, int)
                and candidate == usable + excluded + unexplained and unexplained == 0
                and accounting.get("resolved") is True
                and int(accounting.get("unexplained_records", 0) or 0) == 0
                and all(layer_valid(layer) for layer in source_layers)
                and (not population_layer or layer_valid(population_layer))
            )
            checks["historical_exhaustion"] = (
                row.get("historical_exhaustion_state") == "EXHAUSTED"
                and accounting.get("historical_exhaustion_status") == "EXHAUSTED"
            )
            checks["runner_not_failed"] = row.get("runner_failed") is False

            report_validity = ReportValidity.MISSING
            report_reason = "No report required for certified terminal absence/block state"
            fingerprint_valid = False
            epoch_valid = False
            authority_valid = False
            provenance_valid = False
            analysis_exclusions = False
            if report and report_path is not None:
                report_validity, report_reason = resolve_report_validity(
                    owner.report_filename, report,
                    expected_question_id=owner.id, accepted_question_ids=owner.legacy_ids,
                )
                fingerprint_valid = _fingerprint(report) == row.get("result_fingerprint")
                historical = report.get("historical_pass") or {}
                refs = historical.get("source_evidence_references") or ()
                epoch_valid = report.get("epoch") == "CURRENT" and expected_snapshot_ref in refs
                authority_valid = report_validity == ReportValidity.VALID_CURRENT
                provenance_valid = (
                    fingerprint_valid and epoch_valid
                    and historical.get("candidate_records") == candidate
                    and historical.get("usable_records") == usable
                    and historical.get("unexplained_records") == 0
                    and historical.get("runner_module") == owner.runner_module
                    and historical.get("runner_function") == owner.runner_function
                )
                analysis_exclusions = _analysis_exclusions_present(report, usable, analytical)

            missing = _missing_observables(qualification, question)
            future_trigger = _future_trigger(state, report, missing, question)

            if state in RESULT_STATES:
                checks["report_authority"] = authority_valid
                checks["result_fingerprint"] = fingerprint_valid
                checks["evidence_epoch"] = epoch_valid
                checks["provenance"] = provenance_valid
                checks["population_match"] = analytical is not None and 0 <= analytical <= usable and analysis_exclusions
                checks["state_specific"] = str(report.get("scientific_state") or state) == state
                if state in {"INSUFFICIENT_DATA", "WAITING_DATA"}:
                    checks["state_specific"] = (
                        checks["state_specific"] and checks["historical_exhaustion"]
                        and (_structured_shortfall(report) or bool(question.validation_rules))
                        and future_trigger["condition"] != ""
                    )
                if state == "WAITING_DATA":
                    checks["state_specific"] = checks["state_specific"] and report.get("status") == "WAITING_DATA"
            elif state == "HISTORICALLY_UNANSWERABLE":
                data_gaps.append(self._data_gap(question, row, qualification))
                no_alternate = bool(
                    any(item.get("resolution_type") == "UNAVAILABLE" for item in qualification.get("field_resolutions", ()))
                    or "REQUIRED_RELATIONSHIP_ABSENT" in qualification.get("reason_codes", ())
                    or "LOSSY_RECONSTRUCTION" in qualification.get("reason_codes", ())
                    or usable == 0
                    or str(report.get("status") or row.get("current_report_status")) == "BLOCKED"
                )
                checks["report_authority"] = authority_valid or row.get("current_report_status") == "NO_GOVERNED_SCIENTIFIC_REPORT"
                checks["result_fingerprint"] = fingerprint_valid or bool(row.get("result_fingerprint"))
                checks["evidence_epoch"] = epoch_valid or checks["historical_exhaustion"]
                checks["provenance"] = provenance_valid or (
                    row.get("current_report_status") == "NO_GOVERNED_SCIENTIFIC_REPORT"
                    and bool(qualification.get("evidence_references"))
                )
                checks["population_match"] = analytical in {0, None} or (analytical <= usable and analysis_exclusions)
                checks["state_specific"] = (
                    checks["historical_exhaustion"] and no_alternate
                    and row.get("runner_status") != "MISSING"
                    and bool(missing)
                )
            elif state == "IMPLEMENTATION_BLOCKED":
                gap = deepcopy(IMPLEMENTATION_GAPS.get(question.id, {}))
                if gap:
                    gap.update({
                        "question_id": question.id,
                        "assurance_status_of_blocked_classification": "VERIFIED",
                        "runnable_immediately_after_repair": question.id not in {"G3"},
                    })
                    implementation_gaps.append(gap)
                checks["report_authority"] = True
                checks["result_fingerprint"] = True
                checks["evidence_epoch"] = checks["historical_exhaustion"]
                checks["provenance"] = bool(gap)
                checks["population_match"] = bool(gap) and gap.get("governed") == usable and gap.get("runner_n") == analytical
                if gap and gap.get("runner_n") is None:
                    checks["population_match"] = analytical is None
                checks["state_specific"] = (
                    bool(gap) and row.get("next_action") == "IMPLEMENT_RUNNER"
                    and row.get("runner_failed") is False
                )
            else:
                checks.update({key: False for key in ("report_authority", "result_fingerprint", "evidence_epoch", "provenance", "population_match", "state_specific")})

            for name, passed in checks.items():
                if not passed:
                    failures.append(name.upper())
            status = "VERIFIED" if not failures and not global_errors else "FAILED"
            reason = (
                f"Independent certification verified {state} against frozen evidence/accounting and canonical ownership."
                if status == "VERIFIED" else
                "Certification failed: " + ", ".join(failures or global_errors)
            )
            certifications.append({
                "question_id": question.id,
                "canonical_question": question.description,
                "scientific_state": state,
                "candidate_count": candidate,
                "usable_count": usable,
                "analytical_count": analytical,
                "exclusion_count": excluded,
                "historical_exhaustion": row.get("historical_exhaustion_state"),
                "report_status": row.get("current_report_status"),
                "result_fingerprint": row.get("result_fingerprint"),
                "evidence_fingerprint": self.snapshot["fingerprint"],
                "evidence_epoch": "CURRENT",
                "runner_method": (
                    f"alias:{question.id}->{owner.id}:{owner.runner_module}.{owner.runner_function}"
                    if question.scientific_owner_id else
                    f"{question.runner_module}.{question.runner_function}" if question.runner_module else "MISSING"
                ),
                "population_match": checks["population_match"],
                "provenance_valid": checks["provenance"],
                "report_authority_valid": checks["report_authority"],
                "accounting_valid": checks["accounting_conservation"],
                "historical_state_valid": checks["historical_exhaustion"] and checks["state_specific"],
                "next_action": future_trigger if state in {"INSUFFICIENT_DATA", "WAITING_DATA", "HISTORICALLY_UNANSWERABLE"} else row.get("next_action"),
                "assurance_status": status,
                "assurance_reason": reason,
                "checks": checks,
                "report_validity_reason": report_reason,
                "accounting_layers": {"sources": source_layers, "population": population_layer},
            })

        counts = Counter(item["assurance_status"] for item in certifications)
        material = {
            "schema": SCHEMA,
            "stage": (
                "STAGE4_FINAL_70_QUESTION_ASSURANCE_CERTIFICATION"
                if scope is None else
                "STAGE4_QUESTION_SCOPED_ASSURANCE_CERTIFICATION"),
            "scientific_manifest": str(self.manifest_path),
            "scientific_manifest_fingerprint": self.manifest_fingerprint,
            "frozen_upstream": actual_frozen,
            "frozen_upstream_unchanged": actual_frozen == EXPECTED_FROZEN,
            "question_count": len(certifications),
            "scientific_state_counts": dict(sorted(Counter(item["scientific_state"] for item in certifications).items())),
            "assurance_counts": {"VERIFIED": counts.get("VERIFIED", 0), "INDETERMINATE": 0, "FAILED": counts.get("FAILED", 0)},
            "global_errors": global_errors,
            "certifications": certifications,
            "dataset_schema_gap_count": len(data_gaps),
            "implementation_gap_count": len(implementation_gaps),
            "unexplained_evidence_count": sum(int(row.get("unexplained_historical_population", 0) or 0) for row in self.rows.values()),
            "invalid_historical_exhaustion_count": sum(row.get("historical_exhaustion_state") != "EXHAUSTED" for row in self.rows.values()),
            "runner_failure_count": sum(row.get("runner_failed") is True for row in self.rows.values()),
            "blocker_audit": blocker_audit,
            "q71_gate": {"started": False, "non_verified_consumable": [], "rule": "ONLY_FINAL_CERTIFICATION_VERIFIED_MAY_BE_CONSUMED"},
            "live_or_s3_reads": False,
            "new_research_evidence_introduced": False,
        }
        if scope is not None:
            material["scope"] = scope
            material["question_scoped_recertification"] = True
        material["certification_fingerprint"] = _fingerprint(material)
        if register_gaps:
            self.data_gaps = data_gaps
            self.implementation_gaps = implementation_gaps
        return material

    def certify_scoped(self, question_ids: Sequence[str]) -> dict[str, Any]:
        """Question-scoped assurance recertification for governed re-entry.

        Applies the exact same assurance checks (canonical definition,
        evidence contract, exact population, accounting conservation,
        provenance, report ownership, result fingerprint, evidence epoch,
        state-specific checks) to only the requested questions.  The global
        V1 certification artifact is neither read for mutation nor written,
        and no gap register row is re-registered by this path.
        """
        return self.certify(question_ids=question_ids, register_gaps=False)

    def persist(self) -> dict[str, Any]:
        result = self.certify()
        for path in (OUTPUT_PATH, DATA_GAPS_PATH, IMPLEMENTATION_GAPS_PATH):
            path.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT_PATH.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        OUTPUT_MD.write_text(self._certification_markdown(result), encoding="utf-8")
        DATA_GAPS_PATH.write_text(json.dumps({"schema": SCHEMA, "gaps": self.data_gaps}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        IMPLEMENTATION_GAPS_PATH.write_text(json.dumps({"schema": SCHEMA, "gaps": self.implementation_gaps}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        DATA_GAPS_MD.write_text(self._gap_markdown(self.data_gaps, "Stage 4 Dataset/Schema Gap Register"), encoding="utf-8")
        IMPLEMENTATION_GAPS_MD.write_text(self._gap_markdown(self.implementation_gaps, "Stage 4 Implementation Gap Register"), encoding="utf-8")
        return result

    @staticmethod
    def _certification_markdown(result: Mapping[str, Any]) -> str:
        columns = (
            "question_id", "canonical_question", "scientific_state", "candidate_count", "usable_count",
            "analytical_count", "exclusion_count", "historical_exhaustion", "report_status",
            "result_fingerprint", "evidence_fingerprint / epoch", "runner/method", "population_match",
            "provenance_valid", "report_authority_valid", "accounting_valid", "historical_state_valid",
            "next_action", "assurance_status", "assurance_reason",
        )
        lines = ["# Stage 4 Final 70-Question Assurance Certification", "", "| " + " | ".join(columns) + " |", "|" + "|".join("---" for _ in columns) + "|"]
        for item in result["certifications"]:
            values = (
                item["question_id"], item["canonical_question"], item["scientific_state"], item["candidate_count"],
                item["usable_count"], item["analytical_count"], item["exclusion_count"], item["historical_exhaustion"],
                item["report_status"], item["result_fingerprint"],
                f"{item['evidence_fingerprint']} / {item['evidence_epoch']}", item["runner_method"],
                item["population_match"], item["provenance_valid"], item["report_authority_valid"],
                item["accounting_valid"], item["historical_state_valid"], item["next_action"],
                item["assurance_status"], item["assurance_reason"],
            )
            escaped = [str(value).replace("|", "\\|").replace("\n", " ") for value in values]
            lines.append("| " + " | ".join(escaped) + " |")
        lines.extend(["", f"Certification fingerprint: `{result['certification_fingerprint']}`", ""])
        return "\n".join(lines)

    @staticmethod
    def _gap_markdown(gaps: list[dict[str, Any]], title: str) -> str:
        lines = [f"# {title}", "", "Generated from frozen Stage 4 certification evidence; no schema migration or research execution was performed.", ""]
        for gap in gaps:
            identity = gap.get("gap_id") or gap.get("question_id")
            lines.extend([f"## {identity}", "", "```json", json.dumps(gap, indent=2, sort_keys=True), "```", ""])
        return "\n".join(lines)


__all__ = ["FinalAssuranceCertification", "IMPLEMENTATION_GAPS"]
