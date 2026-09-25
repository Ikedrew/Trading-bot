"""Frozen HD13/HD14 governance for canonical G1/G2; no operational wiring.

This module freezes scientific semantics for future runners only. It does not
load, reconstruct, persist, or mutate evidence and it defines no report.
"""
from __future__ import annotations

from typing import Final

HD13_VERSION: Final = "hd13_g1_all70_dataset_suitability_v1"
HD14_VERSION: Final = "hd14_g2_canonical_lineage_coverage_v1"
ADJUDICATION_VERSION: Final = "hd13_hd14_data_governance_v1"

G1_POPULATION: Final = (
    "Exactly the 70 frozen canonical question identities, once each, including "
    "G1 and G2, plus G3. Alias projections add no rows; implemented, "
    "report-bearing, priority, or other subsets are not substituted."
)
G1_REQUIREMENT_CATEGORIES: Final = (
    "SCIENCE_CONTRACT", "SOURCE_AUTHORITY", "FIELD_COVERAGE", "JOINABILITY",
    "SAMPLE_SUFFICIENCY", "CURRENT_PROVENANCE",
)
G1_REQUIREMENT_STATUSES: Final = (
    "PASS", "FAIL", "INSUFFICIENT", "UNKNOWN", "NOT_APPLICABLE",
)
G1_QUESTION_STATUSES: Final = (
    "SUITABLE", "WAITING_DATA", "UNSUITABLE", "BLOCKED", "UNKNOWN",
)
G1_SCIENCE_SEMANTICS: Final = (
    "Every question must have a frozen, internally resolvable intended-question "
    "contract and exact requirement inventory. Missing, contradictory, or "
    "under-specified science is BLOCKED. NO_RUNNER is structural, not evidence "
    "unsuitability: assess the frozen scientific proposal, not report existence."
)
G1_SOURCE_SEMANTICS: Final = (
    "Emit one result per distinct declared source/evidence authority. PASS means "
    "a recognized producer/schema is present in the immutable CURRENT snapshot; "
    "a proven empty CURRENT source has authority but no sample. A proven missing, "
    "mixed, unavailable, or nonauthoritative required source is FAIL. An "
    "undeclared/unclassifiable authority is UNKNOWN. Runtime reconstruction, "
    "dashboards, and aliases are not authorities."
)
G1_FIELD_SEMANTICS: Final = (
    "Emit one result per distinct canonical required field/evidence field path. "
    "PASS requires the authoritative schema path and usable values in every row "
    "admitted to the question population. A proven absent path or missing required "
    "value is FAIL. A present field on zero admitted rows defers to sample "
    "sufficiency; an undeclared/unclassifiable path is UNKNOWN."
)
G1_JOIN_SEMANTICS: Final = (
    "For each declared join expose exact key paths, grain, cardinality, temporal "
    "rule where applicable, and conflict rule. PASS means all eligible rows join "
    "as declared and preserve required fields. A proven duplicate, partial key, "
    "collision, wrong cardinality, or time/causality violation is FAIL. An "
    "existing valid path with zero rows is INSUFFICIENT; missing authority/key is "
    "FAIL; no declared join is NOT_APPLICABLE."
)
G1_SAMPLE_SEMANTICS: Final = (
    "Evaluate only the effective contract's frozen distinct-grain, total-sample, "
    "coverage, and per-cell rules using canonical identities, not raw rows. PASS "
    "means every applicable rule passes. INSUFFICIENT means source/schema/field/join "
    "machinery exists and a reachable frozen threshold is not met. No frozen rule "
    "is NOT_APPLICABLE while observed n is emitted; never invent a threshold."
)
G1_WAITING_BLOCKED_UNKNOWN: Final = (
    "WAITING_DATA is valid when science/source/field/join/CURRENT requirements pass "
    "or are N/A and only sample rules are INSUFFICIENT. BLOCKED means the question "
    "science/requirement contract is missing, contradictory, or under-specified. "
    "UNKNOWN means the snapshot cannot support assessment and remains explicit. A "
    "Known absence of required source/field/join authority is FAIL, never UNKNOWN; "
    "no UNKNOWN is inferred away or silently dropped."
)
G1_AGGREGATION: Final = (
    "Per-question precedence: BLOCKED, UNKNOWN, UNSUITABLE (any FAIL), WAITING_DATA "
    "(any INSUFFICIENT), otherwise SUITABLE. Apply the same precedence across all "
    "70; overall SUITABLE requires all 70 SUITABLE. Retain every contributing row "
    "and count. This audits evidence suitability, not conclusion truth/value."
)
G1_COMPLETION: Final = (
    "COMPLETE requires exactly 70 unique questions, their exact effective "
    "requirement inventories, valid CURRENT provenance for every assessed "
    "requirement, and zero UNKNOWN. Overall SUITABLE, WAITING_DATA, UNSUITABLE, or "
    "BLOCKED may COMPLETE when valid and exhaustive, with no UNKNOWN. UNKNOWN is "
    "non-completable and cannot be defaulted to FAIL."
)
G1_SELF_SEMANTICS: Final = (
    "G1 is assessed from its frozen HD13 contract, canonical registry digest, and "
    "bound CURRENT evidence snapshot. Never read its own report, result, readiness, "
    "or completion as evidence. No other report completes G1, and G1 is assessed "
    "nonrecursively on exactly the same ALL-70 basis as every other question."
)
G1_SELF: Final = G1_SELF_SEMANTICS
G2_MINIMUM_ELIGIBLE_OPPORTUNITIES: Final = 100
G2_COVERAGE_THRESHOLD: Final = 0.50
G2_CLASSIFICATIONS: Final = ("VALID", "MISSING", "AMBIGUOUS", "CONFLICTING")
G2_ELIGIBLE_SCOPE: Final = (
    "Every CURRENT canonical opportunity proposed for evaluation by the frozen "
    "primary/horizon scope, represented on the decision side, completed-outcome "
    "side, or both. Eligibility is not conditioned on outcome presence. Do not "
    "use account fanout, broker executions, alternative horizons, or current "
    "runtime observations to enlarge the population."
)
G2_ELIGIBILITY: Final = (
    "The denominator population is the exhaustive set-union of eligible CURRENT "
    "decision-side and shadow/outcome-side canonical opportunities, including "
    "decision-only or outcome-only identities. It is not conditioned on a match."
)
G2_DENOMINATOR_ACCOUNTING: Final = (
    "One canonical composite opportunity is one denominator unit; account fanout "
    "and repeated horizons never enlarge D. D equals the sum of VALID, MISSING, "
    "AMBIGUOUS, and CONFLICTING classifications. Every eligible member is "
    "classified exactly once and is not silently dropped; unresolved eligible "
    "orphans are reported separately and forbid COMPLETE."
)
G2_DENOMINATOR: Final = (
    "D is the exhaustive union cardinality of eligible composite canonical "
    "identities observed on either side, including decision-only and outcome-only "
    "opportunities. A record without a complete composite identity cannot be keyed; "
    "report it in an unresolved-identity bucket and forbid COMPLETE until frozen "
    "authority proves it ineligible. Never remove it, widen D with account fanout, "
    "or manufacture a zero outcome."
)
G2_CANONICAL_IDENTITY: Final = (
    "The join identity is the exact ordered composite (entity_id, "
    "canonical_opportunity_id), nonempty on both sides. entity_id alone is "
    "insufficient. correlation_id, cycle_id, account_id, broker identity, "
    "decision_id, shadow_trade_id, and other convenience IDs are never join or "
    "eligibility keys. Differing symbol/strategy/horizon metadata under an equal "
    "equal key is a conflict, not permission to join past it."
)
G2_VALID_JOIN: Final = (
    "VALID requires exactly one eligible decision record and exactly one eligible "
    "completed outcome lifecycle for the same composite identity, with consistent "
    "canonical metadata and a completed finite outcome. The authoritative outcome "
    "lifecycle collapse may pair the normal OPEN and CLOSE events for one shadow "
    "trade before counting; that declared lifecycle construction is not outcome "
    "fanout. Account fanout, alternative horizons, duplicate records, conflicting "
    "identities, and more than one distinct completed outcome are not collapsed "
    "into one valid lineage: they are not two opportunities, are never zero-filled, "
    "and retain the required classification."
)
G2_TAXONOMY: Final = (
    "VALID: one decision and one completed outcome agree on full composite identity "
    "and canonical metadata. MISSING: one record on one side and no counterpart on "
    "the other (including open/no-outcome), with no imputation. AMBIGUOUS: multiple "
    "plausible records or partial/nonunique identity components prevent a "
    "determinate canonical pair. CONFLICTING: complete-key records disagree on "
    "canonical metadata or lifecycle/outcome multiplicity. These classes are "
    "mutually exclusive and exhaustive."
)
G2_ACCOUNTING: Final = (
    "Emit one classification per unique eligible key and require D = VALID + "
    "MISSING + AMBIGUOUS + CONFLICTING. Decisions-only, outcomes-only, invalid, "
    "ambiguous, and conflicting rows remain visible. Any CURRENT record that may "
    "be eligible but cannot receive a complete key is an unresolved orphan; count "
    "and report it and forbid COMPLETE until frozen authority resolves eligibility. "
    "No row/key is silently dropped."
)
G2_COVERAGE: Final = (
    "lineage_coverage = VALID / D, with exact numerator, denominator, all four "
    "class counts, unresolved-orphan count, and percentage. D=0 is UNDEFINED and "
    "non-completable. Do not condition on joined cases or zero-fill missing outcomes."
)
G2_READINESS_MINIMUM: Final = (
    "D>=100 eligible distinct composite canonical opportunities is required for "
    "inferential completion after exhaustive classification. D<100 is "
    "INSUFFICIENT_DATA, not COMPLETE. The proposed >=30 symbol/session rule remains "
    "diagnostic only and adds no global threshold."
)
G2_SCIENTIFIC_THRESHOLD: Final = (
    "For a complete D>=100 audit, point coverage>=0.50 yields "
    "LINEAGE_THRESHOLD_MET; coverage<0.50 yields LINEAGE_THRESHOLD_NOT_MET. This "
    "freezes the proposed and registry 50% decision threshold; it is not a new "
    "population-parameter test, and there is no additional confidence-interval "
    "threshold."
)
G2_COMPLETION: Final = (
    "COMPLETE requires one valid atomic CURRENT snapshot, D>=100, the exact eligible "
    "union, one exhaustive classification per key, no unresolved eligible orphans, "
    "complete provenance, and numerator/denominator plus threshold result. "
    "LINEAGE_THRESHOLD_NOT_MET is valid and COMPLETE. D<100 is INSUFFICIENT_DATA; "
    "unresolved accounting is BLOCKED. G1 cannot complete G2."
)
G1_SNAPSHOT_CONTRACT: Final = (
    "Freeze one validity-approved CURRENT evidence snapshot once and atomically, "
    "with identity/as-of time, contract and registry/definition digests, source and "
    "schema identity, included/excluded accounting, and duplicate-preserving "
    "component digests. Use the same fixed snapshot for all 70 assessments. No "
    "source is added, replaced, queried again, or reconstructed at runtime."
)
G2_SNAPSHOT_CONTRACT: Final = (
    "Freeze the exact eligible CURRENT decision/outcome snapshot once and atomically, "
    "with identity/as-of time, registry/definition digest, source/schema identity, "
    "included/excluded accounting, and duplicate-preserving component digests. No "
    "live/runtime reconstruction, dashboard evidence, or mixed epoch is permitted."
)
SNAPSHOT_PROVENANCE_CONTRACT: Final = (
    "G1 and G2 may bind to one immutable validity-approved CURRENT snapshot contract "
    "but must record separate selections/results. Atomically freeze snapshot identity "
    "and as-of time; record contract and registry/definition digests, source/schema "
    "identity, included/excluded counts and reasons, duplicate-preserving per-component "
    "SHA-256 digests, and input-output accounting. Only CURRENT evidence establishes "
    "suitability/lineage. LEGACY, TRANSITIONAL, INVALIDATED, SUPERSEDED, mixed, "
    "dashboard-only, or runtime-reconstructed evidence cannot be substituted; current "
    "runtime state cannot repair missing history."
)
SHARED_SEPARATION: Final = (
    "G1 and G2 remain separate questions, estimands, runners, reports, and results. "
    "Neither report supplies the other's evidence and cannot complete the other question."
)
