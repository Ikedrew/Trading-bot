"""Cumulative Wave A3 safe definition assessment.

The existing registry, runners, evidence resolver, readiness rules, report
contracts, and focused tests do not establish complete, mutually consistent
scientific definitions for these targets.  They therefore remain unchanged and
fail-closed.  This module records the exact authority conflicts without changing
runner, evidence, readiness, report, or collection behaviour.
"""
from __future__ import annotations

from dataclasses import replace

from research_engine.registry.research_question_models import (
    ResearchQuestionDefinition,
)


WAVE_A3_1_TARGETS = frozenset({"M3", "M7", "P1"})
WAVE_A3_2_TARGETS = frozenset({"R3", "R4", "R5"})
WAVE_A3_3_TARGETS = frozenset({"EX9", "D2", "X5"})
WAVE_A3_TARGETS = WAVE_A3_1_TARGETS | WAVE_A3_2_TARGETS | WAVE_A3_3_TARGETS

# No current A3 target can be closed without choosing new scientific semantics or
# changing an existing runner.  An empty override set is intentional.
WAVE_A3_RESOLVED = frozenset()
WAVE_A3_UNRESOLVED = WAVE_A3_TARGETS
WAVE_A3_OVERRIDES: dict[str, dict] = {}

WAVE_A3_UNRESOLVED_REASONS = {
    "M3": (
        "Registry intent asks whether market_phase improves prediction beyond "
        "H4 regime alone. The dedicated runner (market_research.run_m3) does "
        "not perform predictive validation, model comparison, time ordering, "
        "or an out-of-sample test. It pools CURRENT completed shadow records "
        "across symbols, strategies, and simulated horizons, treats each "
        "record as an observation, and compares the unweighted standard "
        "deviation of regime-cell mean R with regime+phase-cell mean R. It "
        "sets phase_adds_predictive_value when combined dispersion is >115% "
        "of regime-only dispersion and declares COMPLETE with any positive "
        "number of combined records; there is no scientifically sufficient "
        "minimum sample or per-cell threshold. Account executions are not in "
        "the population, but the runner defines no canonical-opportunity "
        "deduplication rule. Calling this dispersion proxy incremental "
        "predictive power would invent semantics not established by the "
        "implementation. Left fail-closed."
    ),
    "M7": (
        "Registry intent asks whether combining regime and phase improves "
        "predictive power beyond either alone. The dedicated runner "
        "(market_research.run_m7) performs an observational, pooled comparison "
        "of the unweighted standard deviation of cell mean R for regime-only, "
        "phase-only, and regime+phase segmentations. It performs no predictive "
        "validation, time/phase ordering, within-symbol or within-strategy "
        "control, or out-of-sample comparison. It sets combined_adds_value on "
        "a strict dispersion comparison and declares COMPLETE with any "
        "positive number of combined records; there is no scientifically "
        "sufficient minimum sample or per-cell threshold. Each CURRENT "
        "completed shadow record is counted, with no account execution input "
        "and no canonical-opportunity deduplication rule. The registry's "
        "predictive claim cannot be reduced to this descriptive association "
        "at the definition layer. Left fail-closed."
    ),
    "P1": (
        "Registry intent asks for expected promotion effects on EV, win rate, "
        "drawdown, trade frequency, and risk using shadow_trades plus "
        "decision_trace. The dedicated runner (promotion_impact."
        "run_promotion_impact) reads only CURRENT shadow records and performs "
        "no decision_trace join. It estimates two fixed in-sample scenarios: "
        "remove pattern groups with >=10 records and mean R below -0.2R, and "
        "raise the score threshold from 0.35 to 0.45. It reports EV changes "
        "and trade-count changes plus current win rate, but does not estimate "
        "promotion effects on drawdown or risk and does not estimate changed "
        "win rate. Readiness requires 100 shadow records, 95% outcome, 80% "
        "entity lineage, and 50% canonical strategy coverage; the runner then "
        "requires 100 R outcomes and >=10 remaining trades for threshold "
        "feasibility. Its unit is a completed shadow record grouped by pattern "
        "or score, without canonical-opportunity deduplication; account "
        "executions are absent, but multiple shadow simulations for one "
        "opportunity can count separately. Declaring the missing join, broader "
        "outcomes, independent opportunity unit, or causal/predictive impact "
        "would require new semantics or runner changes. Left fail-closed."
    ),
    "R3": (
        "Registry intent asks for the probability that the system eventually "
        "reaches catastrophic drawdown given measured edge, variance, win "
        "rate, and position sizing. The runner now loads CURRENT canonical "
        "completed shadow evidence and requires >=50 records, >=95% outcome "
        "coverage, and >=80% entity-lineage coverage. Its unit is nevertheless "
        "one completed shadow simulation record, with no canonical-opportunity "
        "deduplication; account executions are absent, but correlated horizons "
        "from one opportunity can be sampled as separate strategy outcomes. "
        "The Monte Carlo is a finite 10,000-path, 5,000-trade empirical "
        "bootstrap that samples R outcomes independently with replacement, "
        "assumes a stationary identically distributed outcome law and fixed "
        "1% additive risk, ignores the registry's measured position_size field, "
        "and defines ruin as 50% peak drawdown. The separate "
        "analytical approximation uses an odds ratio raised to "
        "1/ruin_threshold (=2 capital units), not the runner's 1% risk-per-trade "
        "bankroll units, so the two methods do not encode the same bankroll "
        "model. This supports conditional scenario modelling, not a literal "
        "eventual-ruin probability forecast. All runner fingerprints omit an "
        "explicit validated epoch and therefore default to UNVERIFIED, which "
        "the report-validity layer cannot accept as VALID_CURRENT. Repair "
        "requires an authoritative independent-outcome/deduplication rule, a "
        "single mathematically coherent bankroll/risk model, explicit finite-"
        "horizon scenario semantics and assumptions, and CURRENT fingerprint "
        "provenance asserted only after validation. Left fail-closed."
    ),
    "R4": (
        "Registry intent asks for a realised-drawdown suspension threshold "
        "based on unacceptable historical recovery probability. The runner "
        "now loads CURRENT canonical completed shadow evidence and requires "
        ">=50 records with >=95% outcome coverage. It reconstructs an additive "
        "1%-risk synthetic equity curve from R outcomes in loader order, not "
        "account equity. Ordering is essential, but the canonical shadow "
        "ingestion sorts reconstructed lifecycles by shadow_trade_id and the "
        "loader appends another shadow population; run_drawdown_threshold does "
        "not sort by entry_time. Its unit is one completed shadow simulation "
        "record with no canonical-opportunity deduplication; account executions "
        "are absent, but correlated horizon simulations can count separately. "
        "The runner examines a fixed threshold grid (5%, 10%, 15%, 20%, 25%, "
        "30%, 40%, 50%) on one historical replay, selects the first threshold "
        "with observed recovery below 50%, defaults to 50% when none exists, "
        "and heuristically sets resume to half the halt threshold when needed. "
        "This is not an optimal-policy test and cannot safely support automatic "
        "suspension while chronology is undefined. Runner fingerprints also "
        "omit explicit validated epoch and default to UNVERIFIED, so new output "
        "cannot be VALID_CURRENT. Repair requires a canonical chronological "
        "independent-observation population, explicit simulation-versus-account "
        "equity semantics, a scientifically justified policy comparison and "
        "sufficiency criterion, and validated CURRENT fingerprint provenance. "
        "Left fail-closed."
    ),
    "R5": (
        "Registry intent asks which of fixed risk, Kelly, fractional Kelly, "
        "fixed lot, and dynamic sizing maximises long-term growth subject to "
        "acceptable drawdown. The runner now loads CURRENT canonical completed "
        "shadow evidence and requires >=50 records with >=95% outcome coverage. "
        "Its unit is one completed shadow simulation record without canonical-"
        "opportunity deduplication; account executions are absent, but multiple "
        "horizon simulations from one opportunity can inflate the strategy-risk "
        "sample. It sequentially replays the loader order, which is not "
        "explicitly chronological, and compares only fixed 0.5%/1%/2%, full "
        "Kelly, half Kelly, and quarter Kelly. Fixed-lot and dynamic models from "
        "the registry are not implemented. Kelly is estimated in-sample from "
        "the same win/loss distribution being replayed and assumes stationary, "
        "independent, identically distributed outcomes; the runner chooses the "
        "highest in-sample return under a hard-coded 30% maximum drawdown and "
        "annualises by 252 observations without establishing observation "
        "frequency. This is a heuristic historical scenario comparison, not "
        "proven long-term optimisation or a predictive sizing policy. Runner "
        "fingerprints omit explicit validated epoch and default to UNVERIFIED, "
        "so new output cannot be VALID_CURRENT. Repair requires an authoritative "
        "chronological independent-outcome population, the registry's complete "
        "model set or narrowed approved intent, explicit Kelly/stationarity and "
        "annualisation assumptions, out-of-sample or resampled policy evaluation, "
        "and validated CURRENT fingerprint provenance. Left fail-closed."
    ),
    "EX9": (
        "Registry intent asks whether a proposed exit policy reduces timeout "
        "frequency and converts timeout losses into captured profits. The "
        "dedicated runner (exit_depth.run_ex9) explicitly performs only an "
        "observational description of CURRENT completed shadow lifecycles: it "
        "compares max_bars_timeout with non-timeout exits, reports timeout rate, "
        "win rates, MFE, bars held, and the rate of MFE >0.05R followed by a "
        "non-positive outcome. It does not simulate any proposed policy over "
        "ordered trade_state_progression and therefore cannot estimate a "
        "counterfactual reduction or converted profit. The registry declares "
        ">=200 records, while the runner starts at >=30 total records and calls "
        "the report COMPLETE with >=10 timeout records; its loader additionally "
        "requires MAE even though that field is not part of the registry "
        "question. Its independent observation is one completed shadow "
        "lifecycle (shadow_trade_id plus canonical_opportunity_id and evaluated "
        "horizon), not an account execution. Account fanout cannot inflate it, "
        "but multiple horizon simulations for one canonical opportunity remain "
        "separate analytical records. Repair requires an approved observational "
        "narrowing or a defined proposed-policy counterfactual simulator, plus "
        "one aligned total/cell sufficiency contract. Left fail-closed."
    ),
    "D2": (
        "The production probability authority is ProbabilityEstimator score_v1: "
        "a pre-decision heuristic estimate of trade success derived from a score "
        "calibrator, confirmation modifier, and market-state dampening, clamped "
        "to [0.10, 0.85]. ProbabilityEstimate carries producer, model, calibration "
        "version, and inputs, but decision_trace persists only the numeric "
        "p_success and does not persist that provenance or a versioned outcome-"
        "target definition. The registered runner (legacy_canonical.run_q04) "
        "reads the literal p_success field (not confidence/score aliases), mixes "
        "unfiltered decision_trace rows with canonical and separate research "
        "shadow populations, and never joins a prediction to its outcome. It "
        "compares the mean of every available prediction with the win rate of a "
        "different shadow population, defining success as pnl_r_multiple > 0; "
        "missing pnl_r_multiple can default to 0 and count as a loss. It requires "
        ">=20 shadow outcomes but only one prediction, performs no probability-"
        "bin or proper calibration analysis, and defines no canonical-opportunity "
        "deduplication. Account executions are absent, yet multiple shadow "
        "horizons can inflate the outcome population. Repair requires a versioned "
        "persisted p_success authority and success target at the canonical "
        "decision/opportunity grain, deterministic leakage-safe prediction-to-"
        "outcome pairing, CURRENT-only evidence, and an aligned calibration "
        "method and sufficiency rule. Left fail-closed."
    ),
    "X5": (
        "The production ev field is computed pre-decision as p_success * TP price "
        "distance - p_failure * SL price distance and is documented as a "
        "comparative ranking heuristic in instrument-price units, not expected R "
        "or money P&L. decision_trace persists the literal ev field at Stage 4, "
        "but not an EV semantic/model version or its probability/reward/risk "
        "provenance; the producer also retains an inline probability fallback. "
        "Neither the runner nor canonical resolver accepts expected_value, "
        "expectancy, predicted_r, or realised-r aliases for ev, so aliases do not "
        "supply the missing authority. The registered runner "
        "(execution_protection_research.run_x5) joins decision_trace to "
        "trade_truth by canonical_opportunity_id and requires >=30 pairs, but "
        "subtracts mean realised R from mean price-distance EV as though their "
        "units were commensurate. It excludes a canonical opportunity whenever "
        "multi-account fanout produces multiple trade_truth outcomes, while it "
        "does not deduplicate repeated decision_trace rows; therefore it has no "
        "settled canonical prediction observation for execution leakage. Repair "
        "requires one versioned persisted pre-decision EV with explicit unit and "
        "inputs, a like-for-like realised outcome metric, and an approved "
        "canonical-decision/account aggregation contract before the existing "
        "join can support this claim. Left fail-closed."
    ),
}


def apply_wave_a3_definitions(
    definitions: dict[str, ResearchQuestionDefinition],
) -> dict[str, ResearchQuestionDefinition]:
    """Return definitions unchanged because every current A3 target is unresolved."""
    result = dict(definitions)
    for qid, overrides in WAVE_A3_OVERRIDES.items():
        # Kept structurally consistent with earlier Wave A modules so a future
        # evidence-backed tranche can add safe overrides without a new framework.
        result[qid] = replace(result[qid], **overrides)
    return result
