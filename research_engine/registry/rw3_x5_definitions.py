"""Implemented X5 contract completing the RW3 foundation.

X5 evaluates whether the PRE-DECISION predicted EV (D3's versioned
predicted_ev_r_v1, in R units) corresponds to subsequent realised R at
canonical-opportunity level, and whether that relationship persists on later
unseen evidence.  X5 is distinct from D3: D3 evaluates a predicted-EV GATE
separating outcome quality; X5 evaluates whether predicted EV ITSELF ranks and
calibrates to realised R.

X5 reuses D3's predicted-EV authority exactly and never uses the production
Stage-4 raw price-distance ev.  Unit compatibility (both sides in R) does not
calibrate the probability model; heuristic_score_v1 remains UNCALIBRATED.
"""
from __future__ import annotations

from dataclasses import replace

from research_engine.registry.research_question_models import (
    CompletionRule, EvidenceAuthority, EvidenceProducer, JoinContract,
    ResearchQuestionDefinition,
)

X5_IMPLEMENTED_TARGETS = frozenset({"X5"})

X5_OVERRIDE = {
    "hypothesis": "Pre-decision predicted_ev_r_v1 (R units) corresponds to subsequent realised R for the same canonical opportunity — ranking and magnitude — and the correspondence persists from earlier discovery into later unseen validation.",
    "null_hypothesis": "Pre-decision predicted_ev_r_v1 does not reliably correspond to subsequent realised R, or any discovery correspondence does not persist in later validation.",
    "population_definition": "CURRENT decision traces with authoritative pre-decision p_success and valid pre-decision entry/stop/target geometry (yielding predicted_ev_r_v1) paired to CURRENT shadow realised R, collapsed to one independent observation per canonical_opportunity_id; account and horizon fanout cannot increase n.",
    "metric_definition": "Predictor is D3's predicted_ev_r_v1 = p_success * reward_r - (1 - p_success) * risk_r (risk_r=1.0, reward_r = pre-decision TP/SL distance ratio, R units); production Stage-4 raw ev and ev/expected_value/score/confidence aliases are rejected. Compares paired predicted vs realised R via mean/median, prediction error (realised_r - predicted_ev_r), MAE, RMSE, Spearman rank relationship, discovery-frozen predicted-EV calibration bins applied unchanged to later validation, and positive-EV vs non-positive-EV validation outcome groups. Ranking/directional value is reported separately from magnitude calibration. Findings: VALIDATED_EV_SIGNAL, DISCOVERY_ONLY, NO_RELIABLE_EV_SIGNAL, SYSTEMATIC_MISCALIBRATION, INSUFFICIENT_EVIDENCE. Probability calibration state is reported from D2; heuristic_score_v1 remains UNCALIBRATED and predicted EV is never presented as fully calibrated expected return.",
    "evidence_authorities": (
        EvidenceAuthority(dataset="decision_trace", schema_version="CURRENT", producer=EvidenceProducer.DECISION_TRACE, field_path="p_success", semantic_meaning="authoritative pre-decision probability frozen before the outcome, input to predicted_ev_r_v1"),
        EvidenceAuthority(dataset="decision_trace", schema_version="CURRENT", producer=EvidenceProducer.DECISION_TRACE, field_path="v10_entry", semantic_meaning="pre-decision entry/stop/target geometry used to derive reward_r (R units) for predicted_ev_r_v1"),
        EvidenceAuthority(dataset="shadow_trades", schema_version="CURRENT", producer=EvidenceProducer.SHADOW_TRADES, field_path="simulated_outcome.pnl_r_multiple", semantic_meaning="subsequent realised R outcome in R units, paired to the same canonical opportunity"),
    ),
    "join_contract": JoinContract(join_keys=("canonical_opportunity_id",), cardinality="one_to_many", conflict_policy="reject", description="Exactly one predicted_ev_r_v1 prediction/version/timestamp joins each canonical opportunity to exactly one subsequent shadow outcome. Repeated shadow horizons/accounts collapse within opportunity; conflicting predictions, invalid geometry, duplicate-horizon outcomes, or post-decision timestamps fail closed."),
    "epoch_requirement": "CURRENT",
    "minimum_sample": 100,
    "completion_rule": CompletionRule(rule_type="paired_chronological_predicted_ev_vs_realised_r", threshold=100, description="COMPLETE only after >=100 paired canonical opportunities, >=60 earlier discovery and >=40 later unseen validation observations, with >=15 validation opportunities in each of the positive-EV and non-positive-EV groups (calibration bins used for interpretation need >=10). Bin boundaries are frozen from discovery only. COMPLETE means the paired chronological predicted-EV-vs-realised-R evaluation ran validly (a negative/no-signal or miscalibration result is still COMPLETE); it does not mean predicted EV is good and changes no production logic."),
}


def apply_x5_definition(definitions: dict[str, ResearchQuestionDefinition]) -> dict[str, ResearchQuestionDefinition]:
    result = dict(definitions)
    result["X5"] = replace(result["X5"], **X5_OVERRIDE)
    return result
