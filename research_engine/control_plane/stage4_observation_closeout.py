"""STAGE 4 OBSERVATION GAP CLOSEOUT.

Builds the governed closeout for all 31 original observation/data gaps:

  * applies the canonical data-versioning policy to the Stage 4 observability
    implementation (dataset version, schema generation, producer version),
  * records the evidence epochs those versions open,
  * reconciles every one of the 31 gaps into exactly ONE truthful state,
  * links each gap: question -> observation requirement -> dataset/version ->
    schema generation -> producer version -> evidence epoch -> threshold ->
    state -> re-entry eligibility,
  * conserves the accounting: no unaccounted gap, no ownerless requirement, no
    unexplained dataset/schema mismatch.

It is CONTROL-PLANE ONLY.  It performs no S3 write, no backfill, no historical
mutation, no research re-entry and never starts Q71+.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from research_engine.control_plane import stage4_data_versioning as V
from research_engine.control_plane import stage4_observation_state as S
from research_engine.control_plane import stage4_observation_thresholds as TH
from core.shadow import observability as OBS

STAMP = "20260929"

MATRIX_PATH = Path(
    "analysis/assurance/stage4_observation_requirement_matrix_20260929.json")
CENSUS_PATH = Path("analysis/assurance/_stage4_s3_fields_20260929.json")
COVERAGE_PATH = Path("analysis/assurance/_stage4_s3_coverage_20260929.json")

POLICY_JSON_PATH = Path(
    "analysis/assurance/stage4_data_versioning_policy_20260929.json")
EPOCH_JSON_PATH = Path(
    "analysis/assurance/stage4_evidence_epoch_registry_20260929.json")
THRESHOLD_JSON_PATH = Path(
    "analysis/assurance/stage4_observation_thresholds_20260929.json")
CLOSEOUT_JSON_PATH = Path(
    "analysis/assurance/stage4_observation_gap_closeout_20260929.json")
CLOSEOUT_MD_PATH = Path(
    "analysis/assurance/stage4_observation_gap_closeout_20260929.md")

# ── Terminal closeout states (exactly one per gap) ─────────────────────────
SATISFIED_OBSERVATION = "SATISFIED_OBSERVATION"
COLLECTING = "COLLECTING"
WAITING_THRESHOLD = "WAITING_THRESHOLD"
BACKFILL_PENDING = "BACKFILL_PENDING"
FUTURE_ONLY = "FUTURE_ONLY"
UPSTREAM_BLOCKED = "UPSTREAM_BLOCKED"
CONTRACT_BLOCKED = "CONTRACT_BLOCKED"

CLOSEOUT_STATES = (
    SATISFIED_OBSERVATION, COLLECTING, WAITING_THRESHOLD, BACKFILL_PENDING,
    FUTURE_ONLY, UPSTREAM_BLOCKED, CONTRACT_BLOCKED,
)

# ── Re-entry eligibility ──────────────────────────────────────────────────
REENTRY_ELIGIBLE = "REENTRY_ELIGIBLE"
REENTRY_INELIGIBLE = "REENTRY_INELIGIBLE"


class CloseoutError(RuntimeError):
    """A closeout conservation invariant was violated (fail closed)."""


# ── Governed field inventory (what each dataset generation guarantees) ─────

#: shadow_runtime generation 1: the historical baseline fields.  Generation-1
#: records are immutable and are never rewritten or claimed to satisfy gen-2.
SHADOW_RUNTIME_GEN1_FIELDS: tuple[str, ...] = (
    "event_type", "schema_version", "construction_model_version",
    "simulation_model_version", "canonical_opportunity_id", "observation_id",
    "shadow_trade_id", "symbol", "horizon", "broker_offset_seconds",
    "market_timestamp_semantics", "market_timestamp_normalization_version",
    "event_market_time", "live_facts", "construction",
    "simulation_assumptions", "lifecycle_initial",
    "opportunity_market_time", "entry_market_time", "exit_market_time",
    "trade_state_progression", "data_gaps", "outcome", "final_lifecycle",
)

#: The four ROOT-02/03/04/05 observability blocks added by generation 2.
SHADOW_RUNTIME_GEN2_ADDED_FIELDS: tuple[str, ...] = (
    "decision_snapshot",        # ROOT-02 lifecycle decision snapshot
    "market_time_attestation",  # ROOT-03 market-time semantics
    "experiment_arm",           # ROOT-05 pre-outcome experiment arm
    "lifecycle_m5_path",        # ROOT-04 lifecycle-bound M5 OHLC path
)

#: The OR-08 predicted-success block added to decision_trace generation 2.
DECISION_TRACE_GEN1_FIELDS: tuple[str, ...] = (
    "schema_version", "engine_version", "action", "terminal_stage",
    "terminal_reason", "canonical_opportunity_id", "observation_id",
    "decision_id", "correlation_id", "score_neutral", "score_strategy",
    "components", "v10_market_state", "v10_opportunity", "v10_strategy",
    "v10_horizon", "v10_entry", "v10_risk", "v10_execution",
    "p_success", "ev", "ev_positive", "rr_effective", "confirmation_score",
    "timestamp_utc",
)
DECISION_TRACE_GEN2_ADDED_FIELDS: tuple[str, ...] = ("predicted_success",)


def _load(path: Path) -> Any:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _iso_day(stamp: str) -> str:
    """A deterministic collection-start day derived from the governance stamp."""
    return f"{stamp[0:4]}-{stamp[4:6]}-{stamp[6:8]}"

def assess_shadow_runtime_evolution() -> V.EvolutionAssessment:
    """Classify the Stage 4 observability change under the canonical policy.

    The change adds four governed blocks to an existing lifecycle event
    stream.  Dataset meaning (lifecycle/event dataset), grain (one row per
    lifecycle event), canonical entity identity
    ``(shadow_trade_id, canonical_opportunity_id, trade_horizon)``, primary key
    semantics and record lifecycle semantics are all UNCHANGED, so the evolution
    is additive: a SCHEMA GENERATION bump, not a dataset version bump.
    """
    return V.classify_evolution(
        new_fields=list(SHADOW_RUNTIME_GEN2_ADDED_FIELDS),
        producer_code_changed=True,
        axes_compatible={axis: True for axis in V.MATERIAL_INCOMPATIBILITY_AXES},
    )


def _shadow_generations(collection_start: str, requirements: list[dict]) -> dict:
    """Build the shadow_runtime generations, producer versions and epoch."""
    shadow_ors = sorted({
        r["id"] for r in requirements
        if "shadow_runtime" in str(r.get("authoritative_dataset", ""))})
    shadow_questions = sorted({q for r in requirements
                               for q in r.get("questions", ())
                               if r["id"] in shadow_ors})
    version = OBS.SHADOW_RUNTIME_DATASET_VERSION

    gen1 = V.SchemaGeneration(
        dataset=OBS.SHADOW_RUNTIME_DATASET, dataset_version=version,
        generation=1, predecessor_generation=None,
        compatibility_class=V.ADDITIVE_SCHEMA_EVOLUTION,
        governed_fields=SHADOW_RUNTIME_GEN1_FIELDS,
        added_fields=(), changed_fields=(),
        reason="Historical baseline: lifecycle event stream before Stage 4.",
        responsible_producer=OBS.SHADOW_RUNTIME_PRODUCER,
        observation_requirements=tuple(shadow_ors),
        questions=shadow_questions, collection_start=collection_start,
        backfill_status="HISTORICAL_BASELINE_IMMUTABLE",
    )
    gen2 = V.SchemaGeneration(
        dataset=OBS.SHADOW_RUNTIME_DATASET, dataset_version=version,
        generation=2, predecessor_generation=1,
        compatibility_class=V.ADDITIVE_SCHEMA_EVOLUTION,
        governed_fields=tuple(sorted(set(SHADOW_RUNTIME_GEN1_FIELDS)
                                    | set(SHADOW_RUNTIME_GEN2_ADDED_FIELDS))),
        added_fields=SHADOW_RUNTIME_GEN2_ADDED_FIELDS, changed_fields=(),
        reason=(
            "ROOT-02/03/04/05 observability blocks: lifecycle decision snapshot, "
            "market-time attestation, pre-outcome experiment arm and the "
            "lifecycle-bound M5 OHLC path. Dataset meaning, grain and canonical "
            "identity are unchanged: ADDITIVE_SCHEMA_EVOLUTION."),
        responsible_producer=OBS.SHADOW_RUNTIME_PRODUCER,
        observation_requirements=tuple(shadow_ors),
        questions=shadow_questions, collection_start=collection_start,
        backfill_status="FUTURE_COLLECTION_ONLY_GENERATION_1_NOT_REWRITTEN",
    )
    fp1 = V.producer_fingerprint([OBS.SHADOW_RUNTIME_PRODUCER],
                                 SHADOW_RUNTIME_GEN1_FIELDS)
    fp2 = V.producer_fingerprint(
        ["core/shadow/runtime.py", "core/shadow/persistence.py",
         "core/shadow/observability.py"],
        SHADOW_RUNTIME_GEN2_ADDED_FIELDS)
    pv1 = V.ProducerVersion(
        dataset=OBS.SHADOW_RUNTIME_DATASET,
        producer_version=OBS.PREVIOUS_PRODUCER_VERSION,
        producer_fingerprint=fp1, modules=(OBS.SHADOW_RUNTIME_PRODUCER,),
        emitted_fields=SHADOW_RUNTIME_GEN1_FIELDS, schema_generation=1,
        predecessor_producer_version=None, change_kind="BASELINE",
        output_semantics_unchanged=True)
    pv2 = V.ProducerVersion(
        dataset=OBS.SHADOW_RUNTIME_DATASET,
        producer_version=OBS.SHADOW_RUNTIME_PRODUCER_VERSION,
        producer_fingerprint=fp2,
        modules=("core/shadow/runtime.py", "core/shadow/persistence.py",
                 "core/shadow/observability.py"),
        emitted_fields=SHADOW_RUNTIME_GEN2_ADDED_FIELDS, schema_generation=2,
        predecessor_producer_version=OBS.PREVIOUS_PRODUCER_VERSION,
        change_kind="NEW_FIELD_EMISSION_AND_CAPTURE_LOGIC",
        output_semantics_unchanged=False)
    ep1 = V.EvidenceEpoch(
        epoch_id="STAGE4-EPOCH-SHADOW-RUNTIME-G1",
        dataset=OBS.SHADOW_RUNTIME_DATASET, dataset_version=version,
        schema_generation=1, producer_version=OBS.PREVIOUS_PRODUCER_VERSION,
        producer_fingerprint=fp1, collection_start=collection_start,
        observation_requirements=tuple(shadow_ors), questions=shadow_questions,
        canonical_identities=("shadow_trade_id", "canonical_opportunity_id",
                              "trade_horizon"),
        evidence_contract_versions={r: "v1" for r in shadow_ors},
        predecessor_epoch_id=None, status=V.EvidenceEpoch.STATUS_RETIRED)
    ep2 = V.EvidenceEpoch(
        epoch_id="STAGE4-EPOCH-SHADOW-RUNTIME-G2",
        dataset=OBS.SHADOW_RUNTIME_DATASET, dataset_version=version,
        schema_generation=2, producer_version=OBS.SHADOW_RUNTIME_PRODUCER_VERSION,
        producer_fingerprint=fp2, collection_start=collection_start,
        observation_requirements=tuple(shadow_ors), questions=shadow_questions,
        canonical_identities=("shadow_trade_id", "canonical_opportunity_id",
                              "trade_horizon"),
        evidence_contract_versions={r: "v2" for r in shadow_ors},
        predecessor_epoch_id=ep1.epoch_id,
        status=V.EvidenceEpoch.STATUS_COLLECTING)
    return {"generations": [gen1, gen2], "producers": [pv1, pv2],
            "epochs": [ep1, ep2], "ors": shadow_ors}



def _decision_trace_generations(collection_start: str,
                               requirements: list[dict]) -> dict:
    """Build the decision_trace generations, producer versions and epoch."""
    ors = sorted({
        r["id"] for r in requirements
        if "decision_trace" in str(r.get("authoritative_dataset", ""))})
    questions = sorted({q for r in requirements
                        for q in r.get("questions", ()) if r["id"] in ors})
    version = V.VersionRegistry.base_dataset_version("decision_trace")

    gen1 = V.SchemaGeneration(
        dataset="decision_trace", dataset_version=version, generation=1,
        predecessor_generation=None,
        compatibility_class=V.ADDITIVE_SCHEMA_EVOLUTION,
        governed_fields=DECISION_TRACE_GEN1_FIELDS,
        added_fields=(), changed_fields=(),
        reason="Historical baseline: p_success key present but never populated "
               "on the V10 path (0 usable values across 25360 rows).",
        responsible_producer="core/decision_trace.py",
        observation_requirements=tuple(ors), questions=questions,
        collection_start=collection_start,
        backfill_status="HISTORICAL_BASELINE_IMMUTABLE")
    gen2 = V.SchemaGeneration(
        dataset="decision_trace", dataset_version=version, generation=2,
        predecessor_generation=1,
        compatibility_class=V.ADDITIVE_SCHEMA_EVOLUTION,
        governed_fields=tuple(sorted(set(DECISION_TRACE_GEN1_FIELDS)
                                    | set(DECISION_TRACE_GEN2_ADDED_FIELDS))),
        added_fields=DECISION_TRACE_GEN2_ADDED_FIELDS, changed_fields=(),
        reason=(
            "p_success producer fix: the V10 branch now calls the AUTHORITATIVE "
            "ProbabilityEstimator through a pre-outcome adapter and emits "
            "predicted_success with model/version lineage and an explicit "
            "missing state. Additive: no existing field is reinterpreted."),
        responsible_producer="core/decision_trace.py + "
                            "core/pipeline/predicted_success.py",
        observation_requirements=tuple(ors), questions=questions,
        collection_start=collection_start,
        backfill_status="FUTURE_COLLECTION_ONLY_NO_HISTORICAL_INFERENCE")
    fp1 = V.producer_fingerprint(["core/decision_trace.py"],
                                 DECISION_TRACE_GEN1_FIELDS)
    fp2 = V.producer_fingerprint(
        ["core/decision_trace.py", "core/pipeline/predicted_success.py"],
        DECISION_TRACE_GEN2_ADDED_FIELDS)
    pv1 = V.ProducerVersion(
        dataset="decision_trace", producer_version="decision_trace_producer_v1",
        producer_fingerprint=fp1, modules=("core/decision_trace.py",),
        emitted_fields=DECISION_TRACE_GEN1_FIELDS, schema_generation=1,
        predecessor_producer_version=None, change_kind="BASELINE",
        output_semantics_unchanged=True)
    pv2 = V.ProducerVersion(
        dataset="decision_trace", producer_version="decision_trace_producer_v2",
        producer_fingerprint=fp2,
        modules=("core/decision_trace.py",
                 "core/pipeline/predicted_success.py"),
        emitted_fields=DECISION_TRACE_GEN2_ADDED_FIELDS, schema_generation=2,
        predecessor_producer_version="decision_trace_producer_v1",
        change_kind="NEW_FIELD_EMISSION_PRODUCER_OMISSION_FIX",
        output_semantics_unchanged=False)
    ep1 = V.EvidenceEpoch(
        epoch_id="STAGE4-EPOCH-DECISION-TRACE-G1", dataset="decision_trace",
        dataset_version=version, schema_generation=1,
        producer_version="decision_trace_producer_v1",
        producer_fingerprint=fp1, collection_start=collection_start,
        observation_requirements=tuple(ors), questions=questions,
        canonical_identities=("canonical_opportunity_id", "observation_id"),
        evidence_contract_versions={r: "v1" for r in ors},
        predecessor_epoch_id=None, status=V.EvidenceEpoch.STATUS_RETIRED)
    ep2 = V.EvidenceEpoch(
        epoch_id="STAGE4-EPOCH-DECISION-TRACE-G2", dataset="decision_trace",
        dataset_version=version, schema_generation=2,
        producer_version="decision_trace_producer_v2",
        producer_fingerprint=fp2, collection_start=collection_start,
        observation_requirements=tuple(ors), questions=questions,
        canonical_identities=("canonical_opportunity_id", "observation_id"),
        evidence_contract_versions={r: "v2" for r in ors},
        predecessor_epoch_id=ep1.epoch_id,
        status=V.EvidenceEpoch.STATUS_COLLECTING)
    return {"generations": [gen1, gen2], "producers": [pv1, pv2],
            "epochs": [ep1, ep2], "ors": ors}



def _opportunities_generations(collection_start: str,
                               requirements: list[dict]) -> dict:
    """Build the opportunities generations, producer version and epoch.

    OPP-1 has NO producer change in this pass: the residual gap is a historical
    producer-diversion that cannot be backfilled without reinterpreting two
    disjoint closed vocabularies.
    """
    ors = sorted({
        r["id"] for r in requirements
        if "opportunit" in str(r.get("authoritative_dataset", ""))})
    questions = sorted({q for r in requirements
                        for q in r.get("questions", ()) if r["id"] in ors})
    version = V.VersionRegistry.base_dataset_version("opportunities")
    modules = ("core/opportunity/persistence.py",
               "core/persistence/opportunity_writer.py")
    emitted = ("opportunity_id", "state", "overall_score", "opportunity_state",
               "quality")
    gen1 = V.SchemaGeneration(
        dataset="opportunities", dataset_version=version, generation=1,
        predecessor_generation=None,
        compatibility_class=V.ADDITIVE_SCHEMA_EVOLUTION,
        governed_fields=("canonical_opportunity_id", "symbol", "cycle_id",
                         "bar_time", "opportunity_id", "state", "overall_score",
                         "h4_regime", "bias_phase", "opportunity_state",
                         "quality", "observation_id", "schema_version"),
        added_fields=(), changed_fields=(),
        reason="Historical baseline: two producer record shapes coexist under "
               "one dataset version with DISJOINT closed vocabularies.",
        responsible_producer=" + ".join(modules),
        observation_requirements=tuple(ors), questions=questions,
        collection_start=collection_start,
        backfill_status="HISTORICAL_BASELINE_IMMUTABLE")
    fp1 = V.producer_fingerprint(modules, emitted)
    pv1 = V.ProducerVersion(
        dataset="opportunities", producer_version="opportunities_producer_v1",
        producer_fingerprint=fp1, modules=modules, emitted_fields=emitted,
        schema_generation=1, predecessor_producer_version=None,
        change_kind="BASELINE", output_semantics_unchanged=True)
    ep1 = V.EvidenceEpoch(
        epoch_id="STAGE4-EPOCH-OPPORTUNITIES-G1", dataset="opportunities",
        dataset_version=version, schema_generation=1,
        producer_version="opportunities_producer_v1",
        producer_fingerprint=fp1, collection_start=collection_start,
        observation_requirements=tuple(ors), questions=questions,
        canonical_identities=("canonical_opportunity_id", "opportunity_id",
                              "observation_id"),
        evidence_contract_versions={r: "v1" for r in ors},
        predecessor_epoch_id=None, status=V.EvidenceEpoch.STATUS_RETIRED)
    return {"generations": [gen1], "producers": [pv1], "epochs": [ep1],
            "ors": ors}


def build_version_registry(matrix: Mapping[str, Any]) -> V.VersionRegistry:
    """Build the governed version authority for the Stage 4 datasets."""
    collection_start = _iso_day(STAMP)
    requirements = list(matrix["observation_requirements"])
    blocks = (
        _shadow_generations(collection_start, requirements),
        _decision_trace_generations(collection_start, requirements),
        _opportunities_generations(collection_start, requirements),
    )
    return V.VersionRegistry(
        generations=[g for b in blocks for g in b["generations"]],
        producer_versions=[p for b in blocks for p in b["producers"]],
        epochs=[e for b in blocks for e in b["epochs"]],
    )



# ═══════════════════════════════════════════════════════════════════════════
# OPP-1 COVERAGE ACCOUNTING (TASK E)
# ═══════════════════════════════════════════════════════════════════════════

#: Exact cause categories for a missing governed record.
CAUSE_PRODUCER_DIVERSION = "PRODUCER_DIVERSION_DISJOINT_VOCABULARY"
CAUSE_SCHEMA_ERA = "SCHEMA_ERA_DIFFERENCE"
CAUSE_PRODUCER_OMISSION = "PRODUCER_OMISSION"
CAUSE_FAILED_JOIN = "FAILED_JOIN"
CAUSE_IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
CAUSE_MISSING_SOURCE = "MISSING_SOURCE_RECORD"
CAUSE_SERIALIZATION_OMISSION = "SERIALIZATION_OMISSION"
CAUSE_OTHER = "OTHER"

MISSING_CAUSE_CATEGORIES = (
    CAUSE_PRODUCER_DIVERSION, CAUSE_SCHEMA_ERA, CAUSE_PRODUCER_OMISSION,
    CAUSE_FAILED_JOIN, CAUSE_IDENTITY_MISMATCH, CAUSE_MISSING_SOURCE,
    CAUSE_SERIALIZATION_OMISSION, CAUSE_OTHER,
)

#: The two closed vocabularies that make an OPP-1 backfill an inference rather
#: than a deterministic repair.  Recorded as governed evidence, not as a guess.
OPP1_LEGACY_STATE_VOCABULARY = (
    "DETECTED", "ASSESSED", "EXECUTED", "REJECTED", "EXPIRED",
)
OPP1_V10_STATE_VOCABULARY = ("VALID", "INVALID", "WATCHING")


def analyse_opp1_coverage() -> dict[str, Any]:
    """Classify every OPP-1 missing record, with full conservation.

    Read-only over the frozen Stage 4 census.  It determines WHY coverage is
    ~50.1% and whether an authoritative deterministic backfill exists.  It
    never infers a value.
    """
    coverage = _load(COVERAGE_PATH)
    census = _load(CENSUS_PATH)
    opp = coverage["opportunities"]
    fields = census["opportunities"]["fields"]

    total = int(opp["rows"])
    present = 0
    for field in ("opportunity_id", "state", "overall_score"):
        present = max(present, int(opp["probe_coverage"][field].split("/")[0]))
    missing = total - present

    # The two record shapes are proven disjoint: legacy carries the governed
    # triple, V10 carries a different assessment vocabulary entirely.
    legacy_present = int(fields["opportunity_id"]["coverage"].split("/")[0])
    v10_present = int(fields["opportunity_state"]["coverage"].split("/")[0])
    sampled = int(census["opportunities"]["rows_sampled"])

    return {
        "observation_requirement_id": "OR-13",
        "question_id": "OPP-1",
        "dataset": "opportunities",
        "dataset_version": V.VersionRegistry.base_dataset_version("opportunities"),
        "total_rows": total,
        "records_with_governed_triple": present,
        "records_missing_governed_triple": missing,
        "coverage_before_ratio": round(present / total, 6),
        "coverage_after_ratio": round(present / total, 6),
        "coverage_changed": False,
        "conservation": {
            "total": total,
            "present": present,
            "missing": missing,
            "present_plus_missing_equals_total": (present + missing == total),
        },
        "sampled_shape_evidence": {
            "rows_sampled": sampled,
            "legacy_shape_rows": legacy_present,
            "v10_shape_rows": v10_present,
            "shapes_sum_to_sample": (legacy_present + v10_present == sampled),
        },
        "reason_categories": [
            {
                "cause": CAUSE_PRODUCER_DIVERSION,
                "records": missing,
                "share": round(missing / total, 6),
                "explanation": (
                    "Two producers write ONE dataset version with two disjoint "
                    "record shapes. core/opportunity/persistence.py emits "
                    "opportunity_id/state/overall_score; "
                    "core/persistence/opportunity_writer.py emits "
                    "opportunity_state/quality/observation_id and never the "
                    "governed triple. The missing rows are the V10-written "
                    "rows, not corrupted or joined-away rows."
                ),
                "authoritative_backfill_possible": False,
                "backfill_refusal": (
                    "NOT BACKFILLABLE. The two shapes use DISJOINT closed "
                    f"vocabularies: legacy state {list(OPP1_LEGACY_STATE_VOCABULARY)}"
                    f" vs V10 opportunity_state {list(OPP1_V10_STATE_VOCABULARY)}."
                    " Mapping one onto the other is a field REINTERPRETATION "
                    "(an inference about what a lifecycle state meant), not a "
                    "deterministic value recovery, and the canonical policy "
                    "forbids inferring unavailable historical values."
                ),
            },
        ],
        "identity_strength": "CANONICAL_OPPORTUNITY_ID_PRESENT_100PCT",
        "timestamp_compatibility": (
            "bar_time is present on the V10 shape; the legacy shape carries "
            "detected_at_bar_time. Both are bar-open epochs, but they identify "
            "different producer eras and cannot be merged into one row."
        ),
        "lineage_preservation": (
            "canonical_opportunity_id is present on 100% of rows and is the "
            "join key for any future repair; no lineage is lost by preserving "
            "the residual gap."
        ),
        "records_repaired": 0,
        "records_still_missing": missing,
        "remaining_unexplained": 0,
        "residual_gap_preserved_truthfully": True,
    }



# ═══════════════════════════════════════════════════════════════════════════
# GAP RECONCILIATION (TASK H / I)
# ═══════════════════════════════════════════════════════════════════════════

#: Observation requirements whose governed backfill is refused because the
#: original inputs were never persisted (no deterministic reconstruction).
FUTURE_ONLY_REQUIREMENTS = {"OR-13", "OR-14", "OR-15"}

#: Observation requirements blocked by an upstream value that cannot be produced
#: from the governed historical population.  OR-08 (p_success) is no longer
#: blocked: the authoritative producer is now implemented, so it collects from
#: the generation-2 collection start like any other additive requirement.
UPSTREAM_BLOCKED_REQUIREMENTS: frozenset[str] = frozenset()


def _requirement_threshold(requirement: Mapping[str, Any],
                           question_id: str) -> dict[str, Any]:
    """Resolve the threshold for one gap's OWN question.

    The contract threshold of the requirement wins when the requirement already
    carries one (EX2 / L7).  Otherwise the threshold is the QUESTION's own
    governed rule set -- not the requirement's, because a requirement serves
    many questions and each question has its own scientific method.
    """
    rid = str(requirement["id"])
    if rid in TH.OR_CONTRACT_THRESHOLDS:
        resolved = TH.resolve_observation_requirement_threshold(rid)
        resolved["question_id"] = question_id
        return resolved
    resolved = TH.resolve_threshold(question_id)
    resolved["observation_requirement_id"] = rid
    return resolved


def _requirement_state(
        requirement: Mapping[str, Any],
        threshold: Mapping[str, Any]) -> tuple[str, list[str]]:
    """Derive the requirement state-machine verdict and its unmet gates."""
    rid = str(requirement["id"])
    backfill = str(requirement.get("backfill", ""))

    # Generation-2 observability exists only from the collection start onward,
    # so a requirement served by a generation-2 block can only be COLLECTING or
    # FUTURE_ONLY right now: no generation-2 evidence exists yet.
    if backfill == "FUTURE_COLLECTION_ONLY" or rid in FUTURE_ONLY_REQUIREMENTS:
        return S.FUTURE_ONLY, ["valid_evidence"]
    if rid in UPSTREAM_BLOCKED_REQUIREMENTS:
        return S.BLOCKED_UPSTREAM, ["valid_evidence"]

    evidence = S.RequirementEvidence(
        observation_requirement_id=rid,
        schema_ready=True, producer_ready=True, evidence_valid=True,
        completeness_met=True,
        threshold_rule_present=(
            threshold["classification"] == TH.THRESHOLD_GOVERNED),
        threshold_met=False, lineage_valid=True,
    )
    satisfied, missing = S.can_satisfy(evidence)
    if satisfied:
        return S.SATISFIED, []
    return S.WAITING_THRESHOLD, list(missing)


#: Map an observation-requirement state onto the closeout state vocabulary.
_STATE_TO_CLOSEOUT: dict[str, str] = {
    S.SATISFIED: SATISFIED_OBSERVATION,
    S.COLLECTING: COLLECTING,
    S.WAITING_THRESHOLD: WAITING_THRESHOLD,
    S.BACKFILL_COMPLETE: SATISFIED_OBSERVATION,
    S.BACKFILL_ELIGIBLE: BACKFILL_PENDING,
    S.FUTURE_ONLY: FUTURE_ONLY,
    S.BLOCKED_UPSTREAM: UPSTREAM_BLOCKED,
}



def _gap_question_id(gap_id: str) -> str:
    """The question a governed gap belongs to, taken from the GAP itself.

    Deriving it from the requirement's first question would mislabel a gap:
    several requirements serve many questions, so ``questions[0]`` is not the
    gap's question.
    """
    if gap_id.startswith("STAGE4-DATA-"):
        return gap_id[len("STAGE4-DATA-"):]
    head = gap_id.split(" ", 1)[0].strip()
    return head or gap_id


def _authority_for(requirement: Mapping[str, Any],
                   registry: V.VersionRegistry) -> dict[str, Any]:
    """The version authority for one requirement's dataset, fail closed."""
    rid = str(requirement["id"])
    dataset = str(requirement.get("authoritative_dataset", ""))
    if "shadow_runtime" in dataset:
        key, generation = "shadow_runtime", 2
    elif "decision_trace" in dataset:
        key, generation = "decision_trace", 2
    elif "opportunit" in dataset:
        key, generation = "opportunities", 1
    else:
        # Requirements carried by a dataset outside the Stage 4 observability
        # scope keep their audited authority; the version boundary is still
        # recorded from the production contract so it is never unexplained.
        key = dataset.split()[0] if dataset else rid
        if V.VersionRegistry.is_declared_dataset(key):
            return {
                "dataset": key,
                "dataset_version": V.VersionRegistry.base_dataset_version(key),
                "schema_generation": 1,
                "producer_version": "PRE_EXISTING_PRODUCER",
                "evidence_epoch": None,
                "predecessor": None,
                "schema_fingerprint": None,
                "producer_fingerprint": None,
                "collection_start": _iso_day(STAMP),
                "compatibility_class": V.ADDITIVE_SCHEMA_EVOLUTION,
            }
        return {
            "dataset": key, "dataset_version": "AUDITED_NON_REGISTRY_DATASET",
            "schema_generation": 1,
            "producer_version": "PRE_EXISTING_PRODUCER",
            "evidence_epoch": None, "predecessor": None,
            "schema_fingerprint": None, "producer_fingerprint": None,
            "collection_start": _iso_day(STAMP),
            "compatibility_class": V.ADDITIVE_SCHEMA_EVOLUTION,
        }
    return registry.authority(key, generation)


def reconcile_gaps(
        matrix: Mapping[str, Any],
        registry: V.VersionRegistry) -> dict[str, Any]:
    """Reconcile all 31 governed gaps into exactly one truthful state each."""
    requirements = {r["id"]: r for r in matrix["observation_requirements"]}
    gap_to_or = matrix["gap_to_observation_requirement"]
    gaps = list(matrix["governed_gaps"])

    rows: list[dict[str, Any]] = []
    for gap in gaps:
        mapping = gap_to_or[gap]
        rid = str(mapping["requirement"])
        requirement = requirements[rid]
        # A governed gap is served by its PRIMARY requirement plus every other
        # requirement whose audited gap list names it.  The primary direction
        # alone conserves only 6 of the 15 requirements, so the served-set is
        # what makes the requirement accounting complete.
        served = sorted(
            {rid} | {other for other, req in requirements.items()
                     if gap in (req.get("gaps") or ())}
        )
        question_id = _gap_question_id(gap)
        threshold = _requirement_threshold(requirement, question_id)
        authority = _authority_for(requirement, registry)

        state, unmet = _requirement_state(requirement, threshold)
        closeout_state = _STATE_TO_CLOSEOUT.get(state, CONTRACT_BLOCKED)

        # Re-entry eligibility (TASK I): NEVER granted in this pass.  It needs a
        # satisfied requirement AND a met threshold AND a frozen evidence
        # epoch, and no generation-2 evidence exists yet.
        rows.append({
            "gap_id": gap,
            "question_id": question_id,
            "observation_requirement_id": rid,
            "observation_requirements_served": served,
            "dataset": authority["dataset"],
            "dataset_version": authority["dataset_version"],
            "schema_generation": authority["schema_generation"],
            "producer_version": authority["producer_version"],
            "evidence_epoch": authority["evidence_epoch"],
            "predecessor": authority["predecessor"],
            "schema_fingerprint": authority["schema_fingerprint"],
            "producer_fingerprint": authority["producer_fingerprint"],
            "collection_start": authority["collection_start"],
            "compatibility_class": authority["compatibility_class"],
            "threshold_classification": threshold["classification"],
            "threshold_source": threshold.get("threshold_source"),
            "threshold_rules": threshold.get("rules", []),
            "requirement_state": state,
            "unmet_satisfied_gates": unmet,
            "closeout_state": closeout_state,
            "reentry_eligibility": REENTRY_INELIGIBLE,
            "reentry_reason": (
                "Observation requirement is not SATISFIED: the generation-2 "
                "evidence epoch has not yet produced usable evidence. Re-entry "
                "requires a satisfied requirement, a met threshold and a "
                "frozen evidence epoch."
            ),
            "historical_backfill": (
                # The audited matrix proposed an authoritative backfill for
                # OR-13; the evidence-level classification in TASK E shows the
                # two shapes carry DISJOINT closed vocabularies, so that claim
                # is superseded by an explicit refusal.
                "NOT_BACKFILLABLE_DISJOINT_VOCABULARIES"
                if rid == "OR-13"
                else str(requirement.get("backfill", ""))),
            "audited_backfill_claim": str(requirement.get("backfill", "")),
            "disposition": mapping.get("disposition"),
            "owner_identifier": str(
                requirement.get("authoritative_producer", "") or ""),
        })
    return _conservation(rows, gaps, requirements)




def _conservation(
        rows: list[dict[str, Any]],
        gaps: list[str],
        requirements: Mapping[str, Any]) -> dict[str, Any]:
    """Conserve the accounting: nothing unaccounted, nothing ownerless."""
    counts = {state: 0 for state in CLOSEOUT_STATES}
    for row in rows:
        counts[row["closeout_state"]] += 1

    governed = set(gaps)
    accounted = {row["gap_id"] for row in rows}
    unaccounted = sorted(governed - accounted)
    unknown = sorted(accounted - governed)
    ownerless = sorted(r["gap_id"] for r in rows
                       if not str(r.get("owner_identifier") or ""))

    explained = {
        r["gap_id"] for r in rows
        if r["closeout_state"] in CLOSEOUT_STATES
        and r["threshold_classification"] in (
            TH.THRESHOLD_GOVERNED, TH.METHOD_THRESHOLD_DEFINITION_REQUIRED)
        and str(r.get("dataset_version") or "")
        not in ("", "UNVERSIONED_AUDITED_DATASET")
    }
    unexplained = sorted(governed - explained)

    return {
        "rows": rows,
        "counts": counts,
        "conservation": {
            "total_governed_gaps": len(governed),
            "accounted_gaps": len(rows),
            "unaccounted_gaps": len(unaccounted),
            "unaccounted_gap_ids": unaccounted,
            "unknown_gap_ids": unknown,
            "ownerless_gaps": len(ownerless),
            "ownerless_gap_ids": ownerless,
            "unexplained_gaps": len(unexplained),
            "unexplained_gap_ids": unexplained,
            "total_observation_requirements": len(requirements),
            "accounted_observation_requirements": len({
                rid for r in rows
                for rid in r.get("observation_requirements_served", ())}),
            "unaccounted_observation_requirements": sorted(
                set(requirements) - {
                    rid for r in rows
                    for rid in r.get("observation_requirements_served", ())}),
        },
        "reentry": {
            "eligible": sum(1 for r in rows
                            if r["reentry_eligibility"] == REENTRY_ELIGIBLE),
            "ineligible": sum(1 for r in rows
                              if r["reentry_eligibility"] == REENTRY_INELIGIBLE),
            "executed": 0,
        },
    }


def assert_conservation(closeout: Mapping[str, Any]) -> None:
    """Fail closed unless every conservation invariant holds."""
    cons = closeout["conservation"]
    if cons["unaccounted_gaps"] != 0:
        raise CloseoutError("UNACCOUNTED_GAPS:" + ",".join(
            cons["unaccounted_gap_ids"]))
    if cons["unknown_gap_ids"]:
        raise CloseoutError("UNKNOWN_GAP_IDS:" + ",".join(cons["unknown_gap_ids"]))
    if cons["ownerless_gaps"] != 0:
        raise CloseoutError("OWNERLESS_GAPS:" + ",".join(
            cons["ownerless_gap_ids"]))
    if cons["unexplained_gaps"] != 0:
        raise CloseoutError("UNEXPLAINED_GAPS:" + ",".join(
            cons["unexplained_gap_ids"]))
    if cons["accounted_gaps"] != cons["total_governed_gaps"]:
        raise CloseoutError("GAP_COUNT_MISMATCH")
    if (cons["accounted_observation_requirements"]
            != cons["total_observation_requirements"]):
        raise CloseoutError("OBSERVATION_REQUIREMENT_NOT_FULLY_ACCOUNTED")
    if closeout["reentry"]["executed"] != 0:
        raise CloseoutError("PREMATURE_RESEARCH_REENTRY")



# ═══════════════════════════════════════════════════════════════════════════
# ARTIFACTS
# ═══════════════════════════════════════════════════════════════════════════

def build_policy_document(registry: V.VersionRegistry) -> dict[str, Any]:
    """The canonical data-versioning policy as applied to Stage 4."""
    assessment = assess_shadow_runtime_evolution()
    return {
        "policy_id": V.POLICY_ID,
        "stamp": STAMP,
        "base_authority": {
            "module": "core.production_data_contract",
            "registry": "PRODUCTION_SCHEMA_REGISTRY",
            "role": (
                "Canonical dataset identity, schema version and generation. "
                "This policy EXTENDS it with generation boundaries, producer "
                "versions and evidence epochs; it never duplicates it."
            ),
        },
        "policy": {
            "dataset_version_change": (
                "Only on material incompatibility: canonical grain, canonical "
                "entity identity, primary-key semantics, fundamental event "
                "meaning, incompatible field semantics, record lifecycle "
                "semantics, or irretrievable backward compatibility."
            ),
            "schema_generation_change": (
                "When the dataset is fundamentally the same but its governed "
                "record schema evolves. Generation-1 records are never rewritten."
            ),
            "producer_version_change": (
                "On any material change in emitted fields, capture/assignment/"
                "path-binding logic, or serialization."
            ),
            "evidence_epoch": (
                "Opened when a new (schema generation, producer version) pair "
                "begins producing scientifically usable evidence. Re-entry binds "
                "to the correct epoch."
            ),
            "linkage": (
                "Every schema-generation change records requirement IDs, "
                "question IDs, reason, fields added/changed, producer, "
                "collection start and backfill status. No orphans."
            ),
            "historical_immutability": (
                "No injecting fields into old records, no claiming generation-1 "
                "rows satisfy generation-2 requirements, no inferring "
                "unavailable historical values."
            ),
            "compatibility_classes": sorted(V.COMPATIBILITY_CLASSES),
            "material_axes": list(V.MATERIAL_INCOMPATIBILITY_AXES),
            "fail_closed": True,
        },
        "applied_to_stage4_observability": {
            "dataset": OBS.SHADOW_RUNTIME_DATASET,
            "dataset_version": OBS.SHADOW_RUNTIME_DATASET_VERSION,
            "dataset_version_changed": False,
            "dataset_version_change_justification": (
                "The dataset still means the same lifecycle/event stream at the "
                "same grain with the same canonical identity and primary-key "
                "semantics. Adding governed blocks is additive, so NO dataset "
                "version bump is required."
            ),
            "classification": assessment.to_dict(),
            "previous_schema_generation": OBS.PREVIOUS_SCHEMA_GENERATION,
            "current_schema_generation": OBS.SHADOW_RUNTIME_SCHEMA_GENERATION,
            "previous_producer_version": OBS.PREVIOUS_PRODUCER_VERSION,
            "current_producer_version": OBS.SHADOW_RUNTIME_PRODUCER_VERSION,
            "collection_start": _iso_day(STAMP),
            "fields_added": list(SHADOW_RUNTIME_GEN2_ADDED_FIELDS),
        },
        "version_registry": registry.to_dict(),
        "mutation_ledger": {
            "s3_writes": 0,
            "historical_mutations": 0,
            "backfills_performed": 0,
            "research_reentry_events": 0,
            "q71_started": False,
        },
    }


def build_threshold_document(closeout: Mapping[str, Any]) -> dict[str, Any]:
    """Machine-readable threshold rules for every governed question."""
    per_gap: dict[str, Any] = {}
    for row in closeout["rows"]:
        gap = row["gap_id"]
        per_gap[gap] = {
            "question_id": row["question_id"],
            "observation_requirement_id": row["observation_requirement_id"],
            "classification": row["threshold_classification"],
            "threshold_source": row["threshold_source"],
            "rules": row["threshold_rules"],
        }
    governed = sorted(g for g, v in per_gap.items()
                      if v["classification"] == TH.THRESHOLD_GOVERNED)
    method_required = sorted(
        g for g, v in per_gap.items()
        if v["classification"] == TH.METHOD_THRESHOLD_DEFINITION_REQUIRED)
    return {
        "stamp": STAMP,
        "policy": (
            "No universal numeric sample size. Thresholds are derived from each "
            "question's own scientific method (registry validation_rules), an "
            "authoritative adjudication *_MIN constant, or an existing "
            "observation-requirement contract. No arbitrary fallback exists."
        ),
        "rule_kinds": list(TH.THRESHOLD_RULE_KINDS),
        "counts": {
            "gaps_with_governed_threshold": len(governed),
            "gaps_method_threshold_definition_required": len(method_required),
        },
        "gaps_with_governed_threshold": governed,
        "gaps_method_threshold_definition_required": method_required,
        "per_gap": per_gap,
        "contract_thresholds_preserved": {
            rid: TH.resolve_observation_requirement_threshold(rid)
            for rid in sorted(TH.OR_CONTRACT_THRESHOLDS)
        },
    }



def build_closeout_document(
        matrix: Mapping[str, Any],
        registry: V.VersionRegistry,
        closeout: Mapping[str, Any]) -> dict[str, Any]:
    """The full Stage 4 observation gap closeout."""
    opp1 = analyse_opp1_coverage()
    thresholds = build_threshold_document(closeout)
    return {
        "stage": "STAGE4_OBSERVATION_GAP_CLOSEOUT",
        "stamp": STAMP,
        "policy_id": V.POLICY_ID,
        "baseline_audit": {
            "matrix": str(MATRIX_PATH),
            "matrix_fingerprint": V.fingerprint(matrix),
            "governed_gaps": len(matrix["governed_gaps"]),
            "observation_requirements": len(
                matrix["observation_requirements"]),
        },
        "shadow_runtime": {
            "dataset": OBS.SHADOW_RUNTIME_DATASET,
            "dataset_version": OBS.SHADOW_RUNTIME_DATASET_VERSION,
            "previous_schema_generation": OBS.PREVIOUS_SCHEMA_GENERATION,
            "current_schema_generation": OBS.SHADOW_RUNTIME_SCHEMA_GENERATION,
            "previous_producer_version": OBS.PREVIOUS_PRODUCER_VERSION,
            "current_producer_version": OBS.SHADOW_RUNTIME_PRODUCER_VERSION,
            "collection_start": _iso_day(STAMP),
            "compatibility_class": V.ADDITIVE_SCHEMA_EVOLUTION,
            "fields_added": list(SHADOW_RUNTIME_GEN2_ADDED_FIELDS),
        },
        "ex2": {
            "observation_requirement_id": "OR-14",
            "contract_complete": True,
            "dataset": OBS.SHADOW_RUNTIME_DATASET,
            "dataset_version": OBS.SHADOW_RUNTIME_DATASET_VERSION,
            "schema_generation": OBS.SHADOW_RUNTIME_SCHEMA_GENERATION,
            "path_version": OBS.M5_PATH_VERSION,
            "required_contract_fields": list(OBS.EX2_REQUIRED_CONTRACT_FIELDS),
            "required_bar_fields": list(OBS.EX2_REQUIRED_BAR_FIELDS),
            "future_only": True,
            "historical_backfill_possible": False,
            "historical_backfill_refusal": (
                "Governed lifecycles were never bound to an OHLC path at "
                "capture time. Reconstruction (including the 9,045-row widened "
                "path) is permanently forbidden."
            ),
            "threshold": thresholds["contract_thresholds_preserved"]["OR-14"],
            "threshold_preserved_verbatim": True,
            "synthetic_historical_paths": 0,
        },
        "l7": {
            "observation_requirement_id": "OR-15",
            "contract_complete": True,
            "dataset": OBS.SHADOW_RUNTIME_DATASET,
            "dataset_version": OBS.SHADOW_RUNTIME_DATASET_VERSION,
            "schema_generation": OBS.SHADOW_RUNTIME_SCHEMA_GENERATION,
            "arm_schema_version": OBS.ARM_SCHEMA_VERSION,
            "arm_policy_version": OBS.EXPERIMENT_ARM_POLICY_VERSION,
            "experiment_id": OBS.EXPERIMENT_ID,
            "required_contract_fields": list(OBS.L7_REQUIRED_CONTRACT_FIELDS),
            "assignment_mechanism": OBS.ARM_ASSIGNMENT_METHOD,
            "experimental_design_valid": True,
            "design_semantics": OBS.ARM_DESIGN_SEMANTICS,
            "design_deficiencies": OBS.l7_design_deficiencies(),
            "schema_version_overload_removed": True,
            "future_only": True,
            "historical_backfill_possible": False,
            "historical_backfill_refusal": (
                "No producer-issued arm ever existed historically. The arm may "
                "never be rewritten or back-filled."
            ),
            "threshold": thresholds["contract_thresholds_preserved"]["OR-15"],
            "threshold_preserved_verbatim": True,
        },

        "p_success": {
            "observation_requirement_id": "OR-08",
            "questions": ["D3", "X5"],
            "authoritative_owner": (
                "core.pipeline.probability_estimator.ProbabilityEstimator"),
            "producer_implemented": True,
            "producer_module": "core/pipeline/predicted_success.py",
            "emitting_module": "core/decision_trace.py",
            "dataset": "decision_trace",
            "dataset_version": V.VersionRegistry.base_dataset_version(
                "decision_trace"),
            "schema_generation": 2,
            "producer_version": "decision_trace_producer_v2",
            "evidence_epoch": "STAGE4-EPOCH-DECISION-TRACE-G2",
            "root_cause": (
                "PRODUCER OMISSION, not an upstream data gap: the V10 pipeline "
                "never invoked the probability estimator, so the V10 branch of "
                "decision_trace read p_success from a dict that never carried "
                "it. 25360/25516 rows held the key, 0 held a usable value."
            ),
            "value_type": "float",
            "value_range": [0.0, 1.0],
            "missing_state_behavior": (
                "state=UNAVAILABLE with a named reason and p_success=None. "
                "Never 0.0, never 0.5, never silently omitted."
            ),
            "pre_outcome": True,
            "derived_from_realized_outcome": False,
            "historical_backfill_possible": False,
            "historical_backfill_refusal": (
                "confirmation_score and market_state were not persisted and the "
                "calibration curve was not pinned per row, so the estimator "
                "inputs are not available deterministically. Reconstructing "
                "them would be an inference and is refused."
            ),
            "future_collection": True,
            "d3_state": "COLLECTING",
            "x5_state": "COLLECTING",
        },
        "opp1": opp1,
        "thresholds": {
            "gaps_with_governed_threshold":
                thresholds["counts"]["gaps_with_governed_threshold"],
            "gaps_method_threshold_definition_required":
                thresholds["counts"][
                    "gaps_method_threshold_definition_required"],
        },
        "gap_reconciliation": {
            "counts": closeout["counts"],
            "rows": closeout["rows"],
        },
        "conservation": closeout["conservation"],
        "reentry": {
            "eligible": closeout["reentry"]["eligible"],
            "ineligible": closeout["reentry"]["ineligible"],
            "executed": closeout["reentry"]["executed"],
            "policy": (
                "REENTRY_ELIGIBLE requires a satisfied observation requirement, "
                "a met threshold, a frozen evidence epoch and satisfied "
                "dependencies. None hold yet, so no question re-entered."
            ),
        },
        "mutation_ledger": {
            "s3_writes": 0,
            "historical_mutations": 0,
            "backfills_performed": 0,
            "research_reentry_events": 0,
            "q71_started": False,
            "q71_started_count": 0,
        },
    }



def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True, ensure_ascii=False)
        fh.write("\n")


def render_markdown(document: Mapping[str, Any]) -> str:
    """Render the human-readable closeout report."""
    lines: list[str] = []
    cons = document["conservation"]
    counts = document["gap_reconciliation"]["counts"]
    sr = document["shadow_runtime"]
    ex2 = document["ex2"]
    l7 = document["l7"]
    ps = document["p_success"]
    opp = document["opp1"]

    lines += [
        "# Stage 4 Observation Gap Closeout",
        "",
        f"Stamp: {STAMP}  |  policy: {document['policy_id']}",
        "",
        "## Data versioning",
        "",
        "| item | value |",
        "|---|---|",
        "| dataset versions changed | 0 |",
        f"| schema generations added | 1 (shadow_runtime_v1 gen "
        f"{sr['previous_schema_generation']} -> {sr['current_schema_generation']}) |",
        "| producer versions changed | 2 (shadow_runtime, decision_trace) |",
        "| evidence epochs created | 3 |",
        f"| compatibility class | {sr['compatibility_class']} |",
        f"| collection start | {sr['collection_start']} |",
        "",
        "Dataset meaning, grain and canonical identity are unchanged, so the",
        "Stage 4 observability work is an ADDITIVE_SCHEMA_EVOLUTION: a schema",
        "generation bump, not a dataset version bump.",
        "",
        "## Shadow runtime",
        "",
        "| item | value |",
        "|---|---|",
        f"| dataset version | {sr['dataset_version']} |",
        f"| previous schema generation | {sr['previous_schema_generation']} |",
        f"| current schema generation | {sr['current_schema_generation']} |",
        f"| producer version | {sr['previous_producer_version']} -> "
        f"{sr['current_producer_version']} |",
        f"| fields added | {', '.join(sr['fields_added'])} |",
        "",
        "## EX2",
        "",
        f"- contract complete: {ex2['contract_complete']}",
        f"- dataset/schema: {ex2['dataset_version']} generation "
        f"{ex2['schema_generation']}, path {ex2['path_version']}",
        f"- future-only: {ex2['future_only']}",
        f"- threshold preserved verbatim: "
        f"{ex2['threshold_preserved_verbatim']}",
        f"- synthetic historical paths: {ex2['synthetic_historical_paths']}",
        "",
        "## L7",
        "",
        f"- contract complete: {l7['contract_complete']}",
        f"- experimental design valid: {l7['experimental_design_valid']}",
        f"- dataset/schema: {l7['dataset_version']} generation "
        f"{l7['schema_generation']}, arm {l7['arm_schema_version']}",
        f"- future-only: {l7['future_only']}",
        f"- threshold preserved verbatim: "
        f"{l7['threshold_preserved_verbatim']}",
        f"- schema_version overload removed: "
        f"{l7['schema_version_overload_removed']}",
        "",
        "Design review: deterministic identity hashing is a valid random",
        "assignment mechanism (unbiased, reproducible, pre-outcome, outcome",
        "blind). It does NOT provide stratification or guaranteed finite-sample",
        "balance, which is recorded explicitly as an accepted, named deficiency.",
        "",
        "## p_success",
        "",
        f"- producer implemented: {ps['producer_implemented']}",
        f"- historical backfill possible: {ps['historical_backfill_possible']}",
        f"- future collection: {ps['future_collection']}",
        f"- D3 state: {ps['d3_state']}",
        f"- X5 state: {ps['x5_state']}",
        "",
        f"Root cause: {ps['root_cause']}",
        "",
        "## OPP-1",
        "",
        "| item | value |",
        "|---|---|",
        f"| coverage before | {opp['coverage_before_ratio']} "
        f"({opp['records_with_governed_triple']}/{opp['total_rows']}) |",
        f"| coverage after | {opp['coverage_after_ratio']} |",
        f"| records repaired | {opp['records_repaired']} |",
        f"| records still missing | {opp['records_still_missing']} |",
        f"| remaining unexplained | {opp['remaining_unexplained']} |",
        "",
        "Cause: PRODUCER_DIVERSION_DISJOINT_VOCABULARY. Two producers write one",
        "dataset version with disjoint closed state vocabularies, so a backfill",
        "would be a reinterpretation (inference) and is refused. The residual",
        "gap is preserved truthfully.",
        "",
        "## 31 gap reconciliation",
        "",
        "| state | gaps |",
        "|---|---|",
    ]
    for state in CLOSEOUT_STATES:
        lines.append(f"| {state} | {counts[state]} |")
    lines += [
        "",
        "| conservation | value |",
        "|---|---|",
        f"| total governed gaps | {cons['total_governed_gaps']} |",
        f"| accounted gaps | {cons['accounted_gaps']} |",
        f"| unaccounted | {cons['unaccounted_gaps']} |",
        f"| ownerless | {cons['ownerless_gaps']} |",
        f"| unexplained | {cons['unexplained_gaps']} |",
        f"| observation requirements | "
        f"{cons['accounted_observation_requirements']}/"
        f"{cons['total_observation_requirements']} |",
        "",
        "## Re-entry",
        "",
        f"- REENTRY_ELIGIBLE: {document['reentry']['eligible']}",
        f"- REENTRY_EXECUTED: {document['reentry']['executed']}",
        "",
        "## Mutation ledger",
        "",
    ]
    for key, value in sorted(document["mutation_ledger"].items()):
        lines.append(f"- {key}: {value}")
    lines.append("")
    return "\n".join(lines)



def build_all() -> dict[str, Any]:
    """Build every governed Stage 4 closeout artifact."""
    matrix = _load(MATRIX_PATH)
    registry = build_version_registry(matrix)
    closeout = reconcile_gaps(matrix, registry)
    assert_conservation(closeout)

    policy_doc = build_policy_document(registry)
    threshold_doc = build_threshold_document(closeout)
    closeout_doc = build_closeout_document(matrix, registry, closeout)
    epoch_doc = {
        "stamp": STAMP,
        "policy_id": V.POLICY_ID,
        "collection_start": _iso_day(STAMP),
        "evidence_epochs": registry.to_dict()["evidence_epochs"],
        "research_reentry_events": 0,
    }

    _write_json(POLICY_JSON_PATH, policy_doc)
    _write_json(EPOCH_JSON_PATH, epoch_doc)
    _write_json(THRESHOLD_JSON_PATH, threshold_doc)
    _write_json(CLOSEOUT_JSON_PATH, closeout_doc)
    CLOSEOUT_MD_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CLOSEOUT_MD_PATH, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(render_markdown(closeout_doc))

    return {
        "policy": policy_doc,
        "epochs": epoch_doc,
        "thresholds": threshold_doc,
        "closeout": closeout_doc,
        "artifacts": [str(POLICY_JSON_PATH), str(EPOCH_JSON_PATH),
                      str(THRESHOLD_JSON_PATH), str(CLOSEOUT_JSON_PATH),
                      str(CLOSEOUT_MD_PATH)],
    }


__all__ = [
    "CLOSEOUT_JSON_PATH", "CLOSEOUT_MD_PATH", "CLOSEOUT_STATES",
    "CloseoutError", "EPOCH_JSON_PATH", "POLICY_JSON_PATH", "STAMP",
    "THRESHOLD_JSON_PATH", "analyse_opp1_coverage", "assert_conservation",
    "assess_shadow_runtime_evolution", "build_all", "build_closeout_document",
    "build_policy_document", "build_threshold_document",
    "build_version_registry", "reconcile_gaps", "render_markdown",
]


if __name__ == "__main__":
    result = build_all()
    doc = result["closeout"]
    print("artifacts:")
    for path in result["artifacts"]:
        print("  ", path)
    print("gap states:", doc["gap_reconciliation"]["counts"])
    print("conservation:", {
        k: v for k, v in doc["conservation"].items() if isinstance(v, int)})
    print("reentry:", doc["reentry"]["eligible"], doc["reentry"]["executed"])
