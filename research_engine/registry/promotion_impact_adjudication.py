"""Frozen HD05 science for canonical P1 promotion-impact analysis.

Governance authority only: this module does not load evidence, run P1, write a
report, approve/apply a candidate, or mutate a baseline or production.
"""
from __future__ import annotations

from typing import Final


HD05_VERSION: Final = "hd05_p1_deployed_promotion_impact_v1"
HD05_TARGETS: Final = ("P1",)

ELIGIBLE_PROMOTION: Final = (
    "One eligible promotion is an exact CandidateRegistry candidate bound to a "
    "source baseline/config hash and frozen treatment; one COMPLETED human ACCEPT "
    "bound to its exact evaluation and recommendation; one canonical "
    "APPROVED_NOT_DEPLOYED application; subsequent DEPLOYED and exactly one VERIFIED "
    "ApplicationLedger row with identical provenance; one COMPLETED ApplicationService "
    "operation whose verification digest matches the ledger; an old source-baseline "
    "snapshot, a distinct resulting-baseline snapshot, and an activated pointer proving "
    "the exact source-to-result transition. Recommendation, candidate creation, "
    "validation success, ACCEPT/APPROVED alone, or application without verified "
    "activation is not a promotion."
)
PROMOTION_AUTHORITIES: Final = {
    "candidate": "CandidateRegistry CandidateRecord plus frozen evaluation treatment provenance",
    "human_decision": "CandidateDecisionStore COMPLETED ACCEPT",
    "application": "ApplicationLedger APPROVED_NOT_DEPLOYED -> DEPLOYED -> VERIFIED history",
    "deployment": "ApplicationService operation with phase=COMPLETED and matching verification digest",
    "source_baseline": "operation.old_snapshot snapshot_id + config_hash",
    "resulting_baseline": "operation.snapshot snapshot_id + config_hash",
    "activation": "operation.activated_pointer",
}
FORBIDDEN_PROMOTION_INFERENCES: Final = (
    "Git commits, report or filesystem timestamps, candidate creation, recommendation, "
    "evaluation success, human approval without application, APPROVED_NOT_DEPLOYED, "
    "runtime configuration drift, candidate status alone, inferred first trade, and "
    "chronological coincidence cannot establish a governed promotion."
)

PROMOTION_IDENTITY_FIELDS: Final = (
    "candidate_id", "evaluation_id", "recommendation_id", "application_id",
    "operation_id", "treatment_id", "treatment_spec_sha256",
    "from_baseline_id", "from_baseline_config_hash",
    "to_baseline_id", "to_baseline_config_hash",
)
PROMOTION_IDENTITY: Final = (
    "promotion_id is SHA-256 of canonical JSON containing exactly "
    "PROMOTION_IDENTITY_FIELDS in their declared names. treatment_spec_sha256 is the "
    "SHA-256 of the already-frozen canonical treatment_spec and is the candidate/treatment "
    "version identity because CandidateRecord has no independent candidate-version field. "
    "Repeated identical persistence is one promotion; any missing field, divergent ledger/"
    "operation/snapshot identity, reuse of an application/operation for another transition, "
    "or conflicting content for one promotion_id fails closed. No new promotion ledger is created."
)

EFFECTIVE_BOUNDARY_FIELD: Final = "ApplicationService.operation.activated_pointer.activated_at"
EFFECTIVE_BOUNDARY: Final = (
    "The effective boundary is activated_pointer.activated_at from the COMPLETED governed "
    "deployment operation. The same activated_pointer must name the resulting baseline as "
    "active_baseline_id, the source baseline as previous_baseline_id, operation_id as reason, "
    "and the governed application actor. VERIFIED ApplicationLedger.occurred_at is verification "
    "history, not activation time. Candidate/recommendation/approval/report/Git/filesystem "
    "times and inferred first trade are forbidden substitutes. Missing, unparsable, or "
    "conflicting activation chronology makes that promotion BLOCKED and is never reconstructed."
)

STATISTICAL_UNIT: Final = (
    "One CURRENT canonical opportunity/decision is one statistical observation. The exact "
    "identity is the nonempty composite (entity_id, canonical_opportunity_id), with "
    "canonical_opportunity_id as the statistical cluster. Account/broker fanout and repeated "
    "horizons cannot enlarge N. Only one completed PRIMARY_HORIZON_SIMULATION outcome is "
    "eligible. Replay-identical duplicates collapse once with accounting; partial identity, "
    "nonidentical duplicates, multiple primary outcomes, or conflicting lineage fail closed."
)

PRE_POST_MEMBERSHIP: Final = {
    "PRE": (
        "Observation carries the exact from_baseline_id and from_baseline_config_hash, "
        "its decision and completed primary outcome precede the effective boundary, and "
        "it falls after the governed activation of that source baseline."
    ),
    "POST": (
        "Observation carries the exact to_baseline_id and to_baseline_config_hash, its "
        "decision is at/after the effective boundary, and its completed primary outcome "
        "precedes the next governed activation or rollback boundary."
    ),
    "window": (
        "Use the maximal contiguous verified tenure of each named baseline. No arbitrary "
        "calendar window is substituted and no lookahead is used."
    ),
    "purge": (
        "Exclude opportunities decided before but completed at/after the boundary and any "
        "opportunity spanning the next transition/rollback. This boundary-spanning exclusion "
        "is the purge; no additional arbitrary time buffer is imposed."
    ),
    "failure": (
        "Chronology alone never assigns baseline membership. Missing or conflicting baseline/"
        "config identity is BLOCKED authority, not PRE/POST and not WAITING_DATA."
    ),
}

OUTCOME_AUTHORITY: Final = (
    "The primary outcome is finite simulated_outcome.pnl_r_multiple from exactly one completed "
    "CURRENT PRIMARY_HORIZON_SIMULATION lifecycle for the same composite identity, ordered by "
    "the persisted decision and exit timestamps. Positive R is beneficial; negative R is "
    "adverse. Missing R remains missing and is never zero. Raw account P&L, broker money P&L, "
    "alternative-horizon averages, dashboard projections, and candidate experiment outcomes "
    "cannot substitute."
)

PRIMARY_ESTIMAND: Final = {
    "name": "mix_standardized_post_minus_pre_mean_realised_r",
    "definition": (
        "POST mean realised R minus PRE mean realised R, standardized to the pooled common-"
        "support distribution of the mandatory mix controls. Also report the raw unadjusted "
        "difference, but it is not the primary conclusion when mix differs."
    ),
    "sign": "positive favours the promoted/resulting baseline; negative favours the source baseline",
    "null": "H0: mix-standardized POST-minus-PRE mean realised R equals 0",
    "inference": (
        "two-sided alpha 0.05 deterministic stratified opportunity-level permutation test "
        "and 95% percentile bootstrap confidence interval, both seeded from the immutable "
        "promotion snapshot digest"
    ),
    "classification": (
        "PROMOTION_IMPROVED when the 95% interval is wholly above 0; PROMOTION_DEGRADED when "
        "wholly below 0; otherwise NO_RELIABLE_DIFFERENCE after all sufficiency gates pass."
    ),
}

SECONDARY_METRICS: Final = {
    "win_rate": {
        "authority": "the same primary R authority; win iff R>0",
        "purpose": "detect a change in success frequency",
        "role": "single predeclared inferential secondary; POST minus PRE with two-sided score test and 95% interval",
        "direction": "higher is favourable, interpreted with expectancy",
    },
    "drawdown_r": {
        "authority": "chronologically ordered cumulative primary-horizon R",
        "purpose": "expose change in peak-to-trough outcome-path drawdown",
        "role": "diagnostic",
        "direction": "smaller absolute maximum drawdown is favourable",
    },
    "opportunity_frequency": {
        "authority": "complete baseline-stamped eligible opportunity stream and verified active-baseline days",
        "purpose": "show opportunity/day and EXECUTE/eligible-opportunity changes",
        "role": "diagnostic; unavailable denominator is reported, never inferred",
        "direction": "trade-off only; neither higher nor lower is inherently favourable",
    },
    "dispersion_tail": {
        "authority": "the same primary R authority",
        "purpose": "report standard deviation, downside semideviation, and fifth percentile R",
        "role": "diagnostic",
        "direction": "lower downside dispersion and a higher fifth percentile are favourable",
    },
    "capital_risk": {
        "authority": "persisted decision-time intended risk fraction/amount and governed exposure snapshots",
        "purpose": "separate leverage/sizing/exposure impact from trading-quality impact",
        "role": "diagnostic normally; mandatory authority when the frozen treatment changes sizing or exposure",
        "direction": "higher risk/exposure is adverse unless explicitly accepted as a separately governed trade-off",
    },
    "execution_cost": {
        "authority": "producer-measured execution slippage/cost for executed opportunities only",
        "purpose": "detect execution-quality degradation where the promotion can affect execution",
        "role": "diagnostic and NOT_APPLICABLE when treatment cannot affect execution",
        "direction": "lower absolute R-normalized cost is favourable",
    },
}
NO_WEIGHTED_SCORE: Final = (
    "P1 emits no weighted promotion score, ranking, or requirement that every secondary metric "
    "improve. It reports expectancy, uncertainty, and explicit trade-offs separately."
)

RISK_NORMALISATION: Final = (
    "Trading-quality impact is evaluated in opportunity-level R, never raw money P&L. Capital/"
    "risk impact is a separate panel using persisted sizing and exposure authority. A treatment "
    "that changes sizing, risk-per-trade, leverage, or portfolio exposure cannot receive a global "
    "PROMOTION_IMPROVED conclusion unless that authority is present and the report explicitly "
    "separates edge from capital amplification. Missing historical risk authority is never "
    "invented; for a risk-changing treatment it BLOCKS the global P1 conclusion."
)

MANDATORY_MIX_CONTROLS: Final = (
    "identity.symbol", "authoritative StrategyFamily", "evaluated_horizon",
    "decision-time H4 Regime",
)
DIAGNOSTIC_MIX_CONTROLS: Final = ("market phase", "session")
MIX_CONTROL: Final = (
    "Compute PRE/POST total-variation distance separately for every mandatory dimension; "
    "TV>=0.10 is a material mix shift under the existing L4 convention. The primary estimate "
    "uses only common-support levels and standardizes their within-level POST-minus-PRE effects "
    "to pooled weights. Every level used inferentially requires >=30 PRE and >=30 POST canonical "
    "opportunities; sparse levels are reported, never manufactured or pooled into OTHER. If no "
    "full-rank common-support adjustment remains, valid but accumulating evidence is WAITING_DATA. "
    "Missing/ambiguous mandatory control authority is BLOCKED. Unadjusted pooling cannot support "
    "a conclusion after any material mix shift. Market phase and session are diagnostics unless "
    "a treatment explicitly targets them, when they become mandatory."
)

MULTIPLE_PROMOTIONS: Final = (
    "Evaluate every eligible governed promotion separately under its immutable promotion_id. "
    "The latest eligible non-rolled-back transition is the CURRENT primary target, but earlier "
    "results remain institutional memory. Never blend transitions to complete the latest one. "
    "No inferential aggregate is canonical: promotions with different treatments, scopes, source/"
    "result baselines, or measurement contracts are not comparable, and promotion rows are never "
    "treated as opportunity replicates. Any later meta-analysis is a separate predeclared study."
)

CANDIDATE_VS_PROMOTION: Final = (
    "Candidate validation asks whether Candidate C beat Baseline B under its governed experiment. "
    "P1 starts only after C was actually applied and the resulting baseline became effective, and "
    "asks whether fresh baseline-stamped deployed observations reproduced improvement. Candidate "
    "evaluation, recommendation, or approval evidence is provenance only and never supplies P1's "
    "POST outcome. A candidate may win validation, be promoted, and then yield degradation or no "
    "reliable deployed difference."
)

MINIMUM_PRE_N: Final = 100
MINIMUM_POST_N: Final = 100
MINIMUM_CELL_PER_ARM: Final = 30
ALPHA: Final = 0.05
CONFIDENCE_LEVEL: Final = 0.95
STATISTICAL_GOVERNANCE: Final = (
    "Require PRE>=100 and POST>=100 distinct canonical opportunities after all lineage, boundary, "
    "primary-horizon, and control exclusions; >=30 per arm for each inferential control level. "
    "Inference is deterministic and preserves the canonical-opportunity cluster. The single "
    "inferential win-rate secondary needs no multiplicity adjustment. Other secondaries are "
    "diagnostic. If more secondary hypotheses are later promoted to inferential status, declare "
    "one family before reading outcomes and apply deterministic Holm at alpha 0.05. A sufficiently "
    "evidenced null or supported degradation is a valid completed result."
)

RESULT_VOCABULARY: Final = (
    "PROMOTION_IMPROVED", "PROMOTION_DEGRADED", "NO_RELIABLE_DIFFERENCE",
    "INSUFFICIENT_EVIDENCE", "PROMOTION_UNMEASURABLE",
)
READINESS_SEMANTICS: Final = {
    "COMPLETE": (
        "Exact eligible promotion, valid effective boundary, authoritative PRE/POST membership, "
        "CURRENT outcome/control authority, all total/cell gates, deterministic evaluation, and "
        "the uniquely owned VALID_CURRENT P1 report. PROMOTION_IMPROVED, PROMOTION_DEGRADED, or "
        "NO_RELIABLE_DIFFERENCE may COMPLETE."
    ),
    "WAITING_DATA": (
        "All scientific authorities and machinery are valid, but PRE/POST N, common-support cell "
        "coverage, or an applicable metric's sample is below its frozen threshold."
    ),
    "BLOCKED": (
        "Promotion/application identity, activation chronology, baseline/config membership, "
        "treatment identity, outcome lineage, mandatory mix authority, or mandatory risk authority "
        "is missing, ambiguous, corrupt, or conflicting. Missing promotion authority is never "
        "WAITING_DATA and historical chronology is never reconstructed."
    ),
}

INSTITUTIONAL_MEMORY: Final = (
    "The owned P1 report preserves the immutable promotion identity, result vocabulary, estimates, "
    "uncertainty, diagnostics, exclusions, and provenance. It is research evidence only. It may "
    "generate future hypotheses or candidate research about expectancy/drawdown, conditional "
    "effects, prospective failure, frequency, or risk trade-offs, but cannot roll back, re-promote, "
    "create/apply a candidate, modify a baseline, or change production. Those actions require their "
    "separate governed human/candidate/application authorities."
)

HISTORICAL_AUTHORITY: Final = (
    "Architecture support exists prospectively: CandidateRegistry, CandidateDecisionStore, "
    "ApplicationLedger, COMPLETED ApplicationService operations, baseline snapshots/pointers, and "
    "candidate impact/continuity records can prove immutable transition identity. The inspected "
    "workspace contains three PROPOSED candidates but no local human-decision store, application "
    "ledger, deployment operation, baseline snapshot/pointer history, or candidate-impact history; "
    "therefore it contains zero locally provable eligible promotions. candidate_impact_history can "
    "reconstruct transition order but deliberately has no wall-clock boundary and cannot substitute "
    "for operation.activated_pointer.activated_at. General CURRENT shadow/decision evidence also "
    "does not expose authoritative source baseline/config identity. Historical P1 evaluation is "
    "therefore BLOCKED, not WAITING_DATA; missing facts must not be reconstructed."
)

STAGE4_GAPS: Final = (
    "Retain completed deployment operation files and activated_pointer.activated_at as transition-boundary evidence.",
    "Persist source baseline_id and baseline_config_hash on every eligible canonical decision/outcome at creation time.",
    "Persist treatment_id on affected deployed observations where scoped attribution is required.",
    "Provide authoritative risk-per-trade and portfolio-exposure snapshots for risk/sizing-changing promotions.",
    "Preserve a complete eligible-opportunity/time denominator for opportunity-frequency comparisons.",
    "Do not use candidate_impact_history ordering as wall-clock deployment chronology.",
)

FUTURE_RUNNER: Final = "research_engine.experiments.promotion_impact.run_p1"
FUTURE_REPORT: Final = "p1_promotion_impact.json"
OWNERSHIP: Final = (
    "P1 is the sole owner of p1_promotion_impact.json. No candidate evaluation, recommendation, "
    "application, impact-history, dashboard, or other question report can complete P1."
)

