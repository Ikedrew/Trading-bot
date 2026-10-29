"""HD11/HD12 governance for L1/L2/L3/L4/L6/L7. Governance only."""
from __future__ import annotations
HD11_VERSION = "hd11_learning_chronology_architecture_v1"
HD12_VERSION = "hd12_learning_cycle_confidence_v1"
LEARNING_VERSION = "hd11_hd12_learning_adaptation_v1"
TARGETS = ("L1", "L2", "L3", "L4", "L6", "L7")
HD11_TARGETS = ("L1", "L2", "L3", "L4", "L7")
ALPHA = 0.05

FUTURE_RUNNERS = {"L1": {"module": "research_engine.experiments.pattern_degradation", "function": "run_l1", "report": "l1_pattern_degradation.json"}}
FORBIDDEN_SOURCES = {"L1": ("q5_pattern_degradation.json",), "L7": ("l7_shadow_ab_validation.json",)}
FUTURE_L2 = {"module": "research_engine.experiments.architecture_improvement", "function": "run_l2", "report": "l2_architecture_improvement.json"}
FUTURE_L3 = {"module": "research_engine.experiments.architecture_assumption_validity", "function": "run_l3", "report": "l3_architecture_assumption_validity.json"}
FUTURE_L4 = {"module": "research_engine.experiments.market_behaviour_stability", "function": "run_l4", "report": "l4_market_behaviour_stability.json"}
FUTURE_L6 = {"module": "research_engine.experiments.learning_cycle_validation", "function": "run_l6", "report": "l6_learning_cycle_validation.json"}
FUTURE_L7 = {"module": "research_engine.experiments.adaptation_evidence", "function": "run_l7", "report": "l7_adaptation_evidence.json"}
FORBIDDEN_L2 = ("q15_learning_velocity.json",)
FORBIDDEN_L3 = ("q1_component_reward.json",)
FORBIDDEN_L4 = ("q17_drawdown_precursors.json",)
CROSS_CURRENT = "Only validity-approved CURRENT evidence may enter any L population."
CROSS_GRAIN = "One canonical opportunity (identity.canonical_opportunity_id) is one unit."
CROSS_FANOUT = "Account executions collapse to one observation per opportunity; never define N."
CROSS_HORIZON = "Primary horizon is unit of analysis; extra horizons weight 1/k_o."
CROSS_TIME = "Order by canonical entry_time UTC epoch seconds; tie by opportunity id."
CROSS_NOLOOK = "Boundaries/windows/arms/grids frozen before outcomes inspected."
CROSS_PROV = "Future L reports carry exact digest, identities, inference config, family."
CROSS_MISSING = "Missing fields excluded with diagnostic, never defaulted to zero."
CROSS_FAIL = "Ambiguity/mixed epoch/conflict resolves to BLOCKED."
CROSS_COMPLETE = "COMPLETE needs VALID_CURRENT owned report from sufficient evidence."
CROSS_NULL = "Sufficient null may COMPLETE where declared."
CROSS_PROD = "No L finding carries production-mutation authority."

L1_MEANING = "Per-pattern expectancy decline over entry-time order; not pooled performance."
L1_DISTINCT = "E2 pooled expectancy"
L1_ESTIMAND = "late-minus-early opportunity-weighted mean R per pattern"
L1_MIN = {"overall": 200, "per_pattern_window": 30, "patterns": 2}
L2_MEANING = "Post-vs-pre expectancy change across versioned boundary under mix controls."
L2_ESTIMAND = "post-minus-pre opportunity-weighted mean R"
L2_MIN = {"pre": 100, "post": 100, "cell": 30}
L3_MEANING = "Weight/regime/mapping validity via three subtests; D1 input only."
L3_DISTINCT = "D1 attribution"
L3_TESTS = ("weight", "regime", "mapping")
L3_JOIN = "OPEN-to-CLOSE on canonical_opportunity_id one_to_one reject"
L4_MEANING = "Market-behaviour stability threatening strategy assumptions."
L4_METRIC = "mix shift plus conditional-R shift across entry-time windows"
L4_MIN = {"per_window": 100, "per_cell_window": 30}
L4_REGIME_CELLS = ("TRENDING", "RANGING", "TRANSITIONAL")
L4_JOIN_AUTHORITY = (
    "CURRENT market_context_v1.regime produced by MarketContextBuilder, joined "
    "as-of to CURRENT completed PRIMARY_HORIZON_SIMULATION shadow evidence; "
    "shadow_runtime_v1 governed OPEN entry time is the temporal authority and "
    "simulated_outcome.pnl_r_multiple is the opportunity outcome"
)
L4_JOIN = (
    "For each canonical opportunity, require equal market-context and OPEN "
    "symbols, then select the unique latest market_context_v1 record whose "
    "bar_time is <= the governed normalized shadow OPEN entry time. This is a "
    "many-opportunities-to-one-context as-of join. Semantically identical "
    "duplicates at the selected time collapse to one context record; exact-time "
    "ties with conflicting context are BLOCKED. Future context, correlation-ID "
    "fallback, current-runtime reconstruction, and inferred context are forbidden. "
    "No preceding context excludes that opportunity with a diagnostic"
)
L4_GRAIN = (
    "One distinct identity.canonical_opportunity_id is one research observation; "
    "only its completed primary-horizon shadow outcome enters. Duplicate or "
    "conflicting primary outcomes are BLOCKED, and account fanout never enlarges N"
)
L4_WINDOWS = (
    "After authority, join, field, and canonical-grain exclusions, order eligible "
    "opportunities by (governed normalized OPEN entry_time UTC epoch seconds, "
    "canonical_opportunity_id). Freeze exactly two non-overlapping outcome-independent "
    "windows: for even N, EARLY is the first N/2 and LATE is the last N/2; for odd "
    "N, exclude the single observation at zero-based index floor(N/2), record it "
    "diagnostically, and assign the observations before it to EARLY and after it "
    "to LATE. Filesystem/report time and outcome values never define ordering or "
    "boundaries"
)
L4_MIX_ESTIMAND = (
    "Total-variation distance TV = 0.5 * sum over TRENDING, RANGING, TRANSITIONAL "
    "of abs(p_late(regime) - p_early(regime)), where each p is the window share "
    "of eligible canonical opportunities; this is the sole mix-shift estimand"
)
L4_MIX_MATERIALITY = {"tv_gte": 0.10}
L4_MIX_INFERENCE = (
    "Pearson chi-square test of homogeneity on the frozen 2x3 EARLY/LATE by "
    "canonical-Regime count table, df=2, two-sided upper-tail p-value; average or "
    "random tie handling is unnecessary for counts; exactly one raw p-value enters "
    "the L4 Holm family"
)
L4_R_ESTIMAND = (
    "For each canonical Regime independently, delta_R(regime) = opportunity-weighted "
    "late mean simulated_outcome.pnl_r_multiple minus opportunity-weighted early "
    "mean simulated_outcome.pnl_r_multiple, with every canonical opportunity weight 1"
)
L4_R_CONTRASTS = (
    "TRENDING_late_minus_early_mean_R",
    "RANGING_late_minus_early_mean_R",
    "TRANSITIONAL_late_minus_early_mean_R",
)
L4_R_MATERIALITY = {"adverse_delta_r_lte": -0.25}
L4_R_INFERENCE = (
    "For each frozen Regime use a deterministic two-sided independent-window Welch "
    "mean-difference t test with unequal variances, Satterthwaite degrees of freedom, "
    "and a 95% confidence interval for late-minus-early mean R. Canonical-opportunity "
    "collapse occurs before inference, so no account-row clustering remains; existing "
    "opportunity-clustered uncertainty is required only if legitimate repeated rows "
    "survive a future authority contract. Each Regime contributes exactly one raw "
    "two-sided p-value and there is no omnibus interaction endpoint"
)
L4_HOLM_ORDER = (
    "regime_mix_shift",
    "TRENDING_R_shift",
    "RANGING_R_shift",
    "TRANSITIONAL_R_shift",
)
L4_MULTIPLICITY = (
    "Exactly four raw p-values in the frozen order (regime_mix_shift, "
    "TRENDING_R_shift, RANGING_R_shift, TRANSITIONAL_R_shift) form one Holm "
    "step-down family at alpha 0.05. Sort by (raw p-value, frozen-order index); "
    "compare ordered p_(i) to 0.05/(4-i+1) until the first non-rejection, and "
    "compute monotone adjusted p_(i) as min(1, max over j<=i of "
    "((4-j+1)*p_(j))); map results back to frozen endpoint order"
)
L4_SUFFICIENCY = (
    "All four endpoints must be estimable; each EARLY and LATE window requires at "
    "least 100 eligible distinct canonical opportunities and each of TRENDING, "
    "RANGING, TRANSITIONAL requires at least 30 observations in each window. Mix "
    "expected counts and every Welch standard error/degrees-of-freedom must be finite "
    "and defined. Missing or non-finite fields are excluded with diagnostics and are "
    "never zero-filled, imputed, or reconstructed"
)
L4_RESULT_RULE = (
    "MATERIAL_INSTABILITY iff at least one endpoint is Holm-significant and crosses "
    "its threat threshold: regime_mix_shift has TV >= 0.10, or a canonical-Regime "
    "R contrast has delta_R <= -0.25R. Otherwise sufficient and estimable evidence "
    "is STABLE. Thus significant but immaterial change, material change without "
    "reliable support, positive R improvement, and a sufficient null are STABLE; "
    "both STABLE and MATERIAL_INSTABILITY are legitimate completed findings"
)
L4_BLOCKED = (
    "BLOCKED on missing/invalid CURRENT authority, invalid or conflicting OPEN/CLOSE "
    "lifecycle identity, duplicate/conflicting primary outcome, invalid governed "
    "entry timestamp or normalization provenance, future-context use, conflicting "
    "latest-context ties, ambiguous/mixed-epoch lineage, correlation-ID fallback, "
    "current-runtime reconstruction, invalid windows, incomplete/reordered Holm "
    "family, legacy Q17 substitution, or self-declared status"
)
L4_WAITING = (
    "WAITING_DATA when authority, lineage, timestamps, windows, and machinery are "
    "valid but exclusions leave fewer than 100 eligible opportunities in either "
    "window, fewer than 30 in any required Regime/window cell, or any of the four "
    "endpoints is inestimable; a sufficient null is not WAITING_DATA"
)
L4_COMPLETION = (
    "L4 may COMPLETE only with sufficient and estimable evidence, all four endpoints "
    "evaluated in the one frozen Holm family, final result STABLE or "
    "MATERIAL_INSTABILITY, and an owned VALID_CURRENT L4 report. Legacy "
    "q17_drawdown_precursors.json and self-declared status never complete L4"
)
L6_MEANING = "Per-cycle pre/post improvement; PROPOSED input only."
L6_GATE = "E5 suitability required"
L6_MIN = {"per_arm": 100, "per_cell_arm": 30}
HD11 = {"version": HD11_VERSION, "questions": HD11_TARGETS, "blocked": False}
HD12 = {"version": HD12_VERSION, "questions": ("L6",), "blocked": False}
FULL = {"version": LEARNING_VERSION, "questions": TARGETS, "blocked": False}
SELF_STATUS_FORBIDDEN = True
E2_NEVER_L1 = "E2 completion never completes L1"
D1_NEVER_L3 = "D1 completion never completes L3"
COUNTS_NEVER = "trade/file/report counts never complete L2/L4/L7"
LEGACY7_BANNED = "chronological halves and always-COMPLETE are forbidden for L7"
PROPOSED_NEVER = "PROPOSED design never completes L6"

L7_OPTION = "A intervention-gated adaptation; B/C forbidden"
L7_ESTIMAND = "candidate-minus-control mean R"
L7_MIN = {"control": 100, "candidate": 100, "cell": 30}

# --- HD11 L3 weight-validity subtest (frozen; governance only) ---
L3_WEIGHT_QUESTION = (
    "Does the historically applied per-opportunity scoring-weight composite "
    "retain positive rank association with realized R-multiple at "
    "canonical-opportunity grain?"
)
L3_WEIGHT_COMPONENTS = (
    "pattern_quality",
    "bias_alignment",
    "market_quality",
    "trend_alignment",
    "chop_clarity",
    "volatility_quality",
    "bias_stability",
    "confirmation_pre",
    "htf_alignment",
    "h4_alignment",
)
L3_WEIGHT_ESTIMAND = (
    "Spearman rank-correlation rho between the persisted historically applied "
    "composite score and realized R-multiple across distinct canonical "
    "opportunities with equal opportunity weight after CROSS fanout/horizon "
    "collapse"
)
L3_WEIGHT_DIRECTION = (
    "rho > 0 supports weight validity; rho < 0 contradicts validity; "
    "rho == 0 is the null of no rank association"
)
L3_WEIGHT_INFERENCE = (
    "Two-sided Spearman rho with average-rank ties; Fisher-z 95% interval "
    "(z=1.96, SE=1/sqrt(n-3)); two-sided t_{n-2} p-value from "
    "t=rho*sqrt((n-2)/(1-rho^2)); deterministic with no resampling; exactly one "
    "raw p-value enters the frozen three-test Holm step-down family at alpha "
    "0.05; VALID/INVALID use the Holm-adjusted p-value only"
)
L3_WEIGHT_EVIDENCE_FIELDS = (
    "decision_trace_v1.identity.canonical_opportunity_id",
    "decision_trace_v1.components.{10 canonical components}",
    "decision_trace_v1.score_strategy (persisted historically applied composite)",
    "decision_trace_v1.persisted per-opportunity weight vector OR versioned "
    "weight-profile identity plus frozen versioned profile table (mandatory)",
    "shadow identity.canonical_opportunity_id",
    "shadow simulated_outcome.pnl_r_multiple where "
    "shadow_type==PRIMARY_HORIZON_SIMULATION",
    "shadow_runtime_v1 OPEN entry-time authority for ordering only",
)
L3_WEIGHT_GRAIN = (
    "One distinct identity.canonical_opportunity_id is one paired observation; "
    "account executions collapse to one observation per opportunity with "
    "weight 1/k_o; primary horizon is the unit and extra horizons weight "
    "1/k_o; D1 rows may be reused as input rows only"
)
L3_WEIGHT_MIN = {"distinct_paired_opportunities": 100}
L3_WEIGHT_VALID_RULE = (
    "Sufficient (at least 100 distinct paired opportunities) and estimable "
    "with Holm-adjusted p <= 0.05 and rho_hat > 0 and Fisher-z 95% interval "
    "lower > 0 implies VALID"
)
L3_WEIGHT_INVALID_RULE = (
    "Sufficient (at least 100 distinct paired opportunities) and estimable "
    "with Holm-adjusted p <= 0.05 and rho_hat < 0 and Fisher-z 95% interval "
    "upper < 0 implies INVALID"
)
L3_WEIGHT_NULL_RULE = (
    "Sufficient and estimable with Holm-adjusted p > 0.05 or a 95% interval "
    "including 0 is a sufficient null and completes only the weight subtest "
    "as NO_RELIABLE_WEIGHT_ASSOCIATION; it is never VALID or INVALID and "
    "never completes L3 alone"
)
L3_WEIGHT_SUFFICIENCY = (
    "Per-test sufficiency is independent: at least 100 distinct canonical "
    "opportunities with complete paired (applied composite, R); missing "
    "fields are excluded with a diagnostic and never defaulted to zero; a "
    "constant margin, undefined rho, or undefined interval is inestimable "
    "and therefore insufficient"
)
L3_WEIGHT_HOLM_FAMILY = (
    "The weight subtest contributes exactly one two-sided p-value to the "
    "already-frozen three-test Holm step-down family over (weight, regime, "
    "mapping) in that order at alpha 0.05; the family is computed only when "
    "all three subtests are sufficient and estimable under frozen contracts; "
    "while regime/mapping remain unfrozen the family is incomplete and L3 "
    "cannot COMPLETE; a weight VALID alone never completes L3"
)
L3_WEIGHT_BLOCKED = (
    "BLOCKED on missing or invalid HD11 authority, on missing persisted "
    "per-opportunity weight vector/versioned weight-profile identity, on any "
    "substitution of current runtime/config constants for historical weights, "
    "on D1 artifact substitution, on decision_ledger substitution, on "
    "ambiguity/mixed epoch/conflict, on an invalid multiplicity family, or on "
    "self-declared status"
)
L3_WEIGHT_WAITING = (
    "WAITING_DATA on valid authority and machinery with complete mandatory "
    "weight identity but fewer than 100 distinct paired opportunities or an "
    "inestimable margin"
)
L3_WEIGHT_PROFILE_MANDATORY = True
L3_WEIGHT_PROFILE_ABSENT = (
    "decision_trace_v1 persists only a weights_used label plus components "
    "and scores; it does not persist an authoritative actual per-component "
    "weight vector or versioned weight-profile identity, so the mandatory "
    "weight identity is absent and the weight subtest is BLOCKED without "
    "creating or reconstructing it"
)
L3_WEIGHT_RUNTIME_SUBSTITUTION_FORBIDDEN = (
    "Current runtime/config weight constants must never stand in for "
    "historical evidence"
)
L3_WEIGHT_D1_BOUNDARY = (
    "D1 per-component mean-R separation and per-component Pearson "
    "correlation never establish weight validity; D1 completion never "
    "completes L3; q1_component_reward.json is forbidden for L3"
)
L3_WEIGHT_DECISION_LEDGER_NOT_AUTHORITY = True
L3_WEIGHT_DECISION_LEDGER_RULE = (
    "decision_ledger is not an evidence authority for weight validity; the "
    "existing adjudicated science does not authorize it and no new authority "
    "is added here"
)
L3_REGIME_QUESTION = (
    "Does the historically persisted canonical MarketContext Regime, derived "
    "from H4, separate realized R-multiple across canonical regimes at "
    "canonical-opportunity grain?"
)
L3_REGIME_CELLS = ("TRENDING", "RANGING", "TRANSITIONAL")
L3_REGIME_ESTIMAND = (
    "Vector of opportunity-weighted mean R per frozen canonical H4 regime "
    "cell (TRENDING, RANGING, TRANSITIONAL) across distinct canonical "
    "opportunities with equal opportunity weight after CROSS fanout/horizon "
    "collapse"
)
L3_REGIME_EVIDENCE_FIELDS = (
    "market_context_v1.regime (canonical core.market_context.models.Regime, "
    "produced by MarketContextBuilder from the persisted H4 summary)",
    "market_context_v1.entity_id (observation lineage; mandatory unique join)",
    "decision_trace_v1.regime/regime_source/regime_timeframe (diagnostic "
    "consistency evidence only; never stronger than market_context_v1.regime)",
    "decision_trace_v1.entity_id and decision_trace_v1.canonical_opportunity_id",
    "shadow identity.canonical_opportunity_id",
    "shadow simulated_outcome.pnl_r_multiple where "
    "shadow_type==PRIMARY_HORIZON_SIMULATION",
    "shadow_runtime_v1 OPEN entry-time authority for ordering only",
)
L3_REGIME_GRAIN = (
    "One distinct identity.canonical_opportunity_id is one grouped observation; "
    "account executions collapse to one observation per opportunity with "
    "weight 1/k_o; primary horizon is the unit and extra horizons weight "
    "1/k_o; D1 rows may be reused as input rows only"
)
L3_REGIME_INFERENCE = (
    "Deterministic Kruskal-Wallis H omnibus test over the three frozen "
    "canonical cells with average-rank ties and chi-square df=2 two-sided "
    "p-value; deterministic with no resampling; exactly one raw p-value "
    "enters the frozen three-test Holm step-down family at alpha 0.05; "
    "VALID/INVALID/sufficient-null use the Holm-adjusted p-value only"
)
L3_REGIME_MIN = {"distinct_grouped_opportunities": 100, "per_cell": 30}
L3_REGIME_VALID_RULE = (
    "Sufficient (at least 100 distinct grouped opportunities with at least "
    "30 in every frozen cell) and estimable with Holm-adjusted p <= 0.05 "
    "implies VALID for the frozen nondirectional separation question"
)
L3_REGIME_INVALID_RULE = (
    "INVALID is not inferable from this nondirectional omnibus contract: no "
    "canonical regime ordering or architecture direction exists to contradict; "
    "authority/lineage failures are BLOCKED and nonsignificance is the frozen "
    "sufficient-null result, so no direction may be invented"
)
L3_REGIME_NULL_RULE = (
    "Sufficient and estimable with Holm-adjusted p > 0.05 is a sufficient "
    "null and completes only the regime subtest as NO_RELIABLE_REGIME_SEPARATION; "
    "it is never VALID or INVALID and never completes L3 alone"
)
L3_REGIME_SUFFICIENCY = (
    "Per-test sufficiency is independent: at least 100 distinct canonical "
    "opportunities with complete grouped (frozen H4 regime, R) and at least "
    "30 in TRENDING, RANGING, and TRANSITIONAL; missing fields are excluded "
    "with a diagnostic and never defaulted to zero; fewer than 30 in any "
    "frozen cell, a constant margin, undefined H, or undefined p is "
    "inestimable and therefore insufficient"
)
L3_REGIME_HOLM = (
    "The regime subtest contributes exactly one two-sided p-value to the "
    "frozen three-test Holm step-down family over (weight, regime, mapping) "
    "in that order at alpha 0.05; the family is computed only when all three "
    "subtests are sufficient and estimable under frozen contracts; a regime "
    "VALID alone never completes L3"
)
L3_REGIME_BLOCKED = (
    "BLOCKED on missing or invalid HD11 authority, on missing persisted "
    "market_context_v1 canonical Regime or missing/non-unique entity_id lineage "
    "to the canonical opportunity, on treating decision_trace_v1 regime_source "
    "as authority rather than diagnostic consistency evidence, on any "
    "substitution of current runtime/config market state for historical "
    "classification, on any new/renamed regime taxonomy or cell substitution, "
    "on D1 artifact substitution, on decision_ledger substitution, on "
    "ambiguity/mixed epoch/conflict, on an invalid multiplicity family, or on "
    "self-declared status"
)
L3_REGIME_WAITING = (
    "WAITING_DATA on valid authority and machinery with complete mandatory "
    "historical H4 identity but fewer than 100 distinct grouped opportunities "
    "or fewer than 30 in any frozen cell or an inestimable margin"
)
L3_REGIME_AUTHORITY_MANDATORY = True
L3_REGIME_SOURCE_RULE = (
    "The authoritative historical field is market_context_v1.regime: the "
    "persisted core.market_context.models.Regime produced by MarketContextBuilder "
    "from its H4 summary. Link it through the existing entity_id observation "
    "lineage to a unique canonical opportunity and reject missing, non-unique, "
    "or conflicting joins. decision_trace_v1.regime_source and regime_timeframe "
    "are diagnostic consistency evidence only, never a stronger authority. The "
    "required cells are exactly TRENDING, RANGING, TRANSITIONAL; no new regime "
    "authority is added here"
)
L3_REGIME_TAXONOMY_FROZEN = ("TRENDING", "RANGING", "TRANSITIONAL")
L3_MAPPING_QUESTION = (
    "Does the historically persisted V10 StrategyFamily mapping separate "
    "realized R-multiple across canonical families at canonical-opportunity grain?"
)
L3_MAPPING_FAMILIES = (
    "LIQUIDITY_SWEEP_REVERSAL",
    "FALSE_BREAK",
    "TREND_CONTINUATION",
    "BREAKOUT_EXPANSION",
    "MEAN_REVERSION",
    "RANGE_REACTION",
)
L3_MAPPING_ESTIMAND = (
    "Vector of opportunity-weighted mean R per frozen canonical V10 "
    "StrategyFamily cell across distinct canonical opportunities with equal "
    "opportunity weight after CROSS fanout/horizon collapse"
)
L3_MAPPING_EVIDENCE_FIELDS = (
    "required but not established: shadow_trades_v1 identity.strategy_id frozen "
    "at shadow OPEN and demonstrably copied from the same-opportunity "
    "core.v10.strategy_family.StrategyFamily decision",
    "decision_trace_v1.identity.canonical_opportunity_id",
    "shadow identity.canonical_opportunity_id",
    "shadow simulated_outcome.pnl_r_multiple where "
    "shadow_type==PRIMARY_HORIZON_SIMULATION",
    "shadow_runtime_v1 OPEN entry-time authority for ordering only",
)
L3_MAPPING_GRAIN = (
    "One distinct identity.canonical_opportunity_id is one grouped observation; "
    "account executions collapse to one observation per opportunity with "
    "weight 1/k_o; primary horizon is the unit and extra horizons weight "
    "1/k_o; D1 rows may be reused as input rows only"
)
L3_MAPPING_INFERENCE = (
    "Deterministic Kruskal-Wallis H omnibus test over the six frozen canonical "
    "families with average-rank ties and chi-square df=5 two-sided p-value; "
    "deterministic with no resampling; exactly one raw p-value enters the "
    "frozen three-test Holm step-down family at alpha 0.05; VALID/INVALID/"
    "sufficient-null use the Holm-adjusted p-value only"
)
L3_MAPPING_MIN = {"distinct_grouped_opportunities": 100, "per_cell": 30}
L3_MAPPING_VALID_RULE = (
    "Sufficient (at least 100 distinct grouped opportunities with at least "
    "30 in every frozen family) and estimable with Holm-adjusted p <= 0.05 "
    "implies VALID for the frozen nondirectional separation question"
)
L3_MAPPING_INVALID_RULE = (
    "INVALID is not inferable from this nondirectional omnibus contract: no "
    "canonical family ordering or architecture direction exists to contradict; "
    "authority/lineage failures are BLOCKED and nonsignificance is the frozen "
    "sufficient-null result, so no direction may be invented"
)
L3_MAPPING_NULL_RULE = (
    "Sufficient and estimable with Holm-adjusted p > 0.05 is a sufficient "
    "null and completes only the mapping subtest as "
    "NO_RELIABLE_MAPPING_SEPARATION; it is never VALID or INVALID and never "
    "completes L3 alone"
)
L3_MAPPING_SUFFICIENCY = (
    "Per-test sufficiency is independent: at least 100 distinct canonical "
    "opportunities with complete grouped (frozen V10 family, R) and at least "
    "30 in every frozen family; NONE, unknown, legacy-only, and missing "
    "families are excluded with a diagnostic and never defaulted, imputed, "
    "or inferred from pattern; simple presence of a StrategyFamily label "
    "never establishes mapping validity; fewer than 30 in any frozen family, "
    "a constant margin, undefined H, or undefined p is inestimable and "
    "therefore insufficient"
)
L3_MAPPING_HOLM = (
    "The mapping subtest contributes exactly one two-sided p-value to the "
    "frozen three-test Holm step-down family over (weight, regime, mapping) "
    "in that order at alpha 0.05; the family is computed only when all three "
    "subtests are sufficient and estimable under frozen contracts; a mapping "
    "VALID alone never completes L3"
)
L3_MAPPING_BLOCKED = (
    "BLOCKED on missing or invalid HD11 authority, on missing persisted "
    "shadow OPEN strategy_id or on absence of proof that it was copied from the "
    "same-opportunity V10 StrategyFamily decision; current producers do not "
    "establish that lineage. BLOCKED also on any substitution of decision_trace "
    "selected_strategy, shadow live_facts.strategy, a later V10 record, or "
    "runtime/config family constants for the required frozen shadow OPEN "
    "mapping, on any new/renamed family taxonomy or legacy-label substitution, "
    "on pattern-inferred family substitution, on D1 artifact substitution, on "
    "decision_ledger substitution, on ambiguity/mixed epoch/conflict, on an "
    "invalid multiplicity family, or on self-declared status"
)
L3_MAPPING_WAITING = (
    "WAITING_DATA on valid authority and machinery with complete mandatory "
    "shadow OPEN mapping identity but fewer than 100 distinct grouped "
    "opportunities or fewer than 30 in any frozen family or an inestimable "
    "margin"
)
L3_MAPPING_LABEL_NOT_PROOF = (
    "Simple presence of a StrategyFamily label never proves mapping validity; "
    "only the frozen grouped heterogeneity test on sufficient evidence can "
    "establish VALID, INVALID, or sufficient-null"
)
L3_MAPPING_E3_BOUNDARY = (
    "E3 family-expectancy POSITIVE/NON_POSITIVE classifications never "
    "establish mapping validity; D1 completion never completes L3; "
    "q1_component_reward.json is forbidden for L3"
)
L3_MAPPING_AUTHORITY_MANDATORY = True
L3_MAPPING_SOURCE_RULE = (
    "The required authority is a shadow_trades_v1 identity.strategy_id frozen "
    "at OPEN and demonstrably copied from the same-opportunity "
    "core.v10.strategy_family.StrategyFamily decision. Existing legacy shadow "
    "OPEN receives _new_result.strategy (legacy activation), while the newer "
    "shadow runtime persists V10 family only as live_facts.strategy rather than "
    "identity.strategy_id; therefore the required OPEN identity lineage is not "
    "established and mapping remains BLOCKED. decision_trace selected_strategy "
    "is not an evidence authority and no new authority is added here"
)
L3_MAPPING_EVIDENCE_BLOCKER = (
    "No existing persisted shadow OPEN field is both identity.strategy_id and "
    "proven to originate from the same-opportunity V10 StrategyFamily authority; "
    "do not reconstruct, relabel, or substitute that missing lineage"
)
L3_REGIME_M1_BOUNDARY = (
    "M1 regime-prediction and E3 family-expectancy findings never establish "
    "regime-classification validity; D1 completion never completes L3; "
    "q1_component_reward.json is forbidden for L3"
)
L3_HOLM_FAMILY = (
    "Exactly one raw two-sided p-value from each of weight, regime, and "
    "mapping in the frozen order (weight, regime, mapping); one deterministic "
    "Holm step-down family at alpha 0.05 with frozen tie order (weight, "
    "regime, mapping); VALID/INVALID/sufficient-null use Holm-adjusted "
    "p-values only"
)
L3_HOLM_ORDER = ("weight", "regime", "mapping")
L3_COMPLETION_PRECEDENCE = (
    "Each subtest independently requires sufficient and estimable evidence; "
    "any BLOCKED subtest implies L3 BLOCKED; otherwise any WAITING_DATA or "
    "insufficient subtest implies L3 WAITING_DATA; otherwise all three "
    "sufficiently evaluated as VALID, INVALID, or sufficient-null implies L3 "
    "may COMPLETE"
)
L3_SUFFICIENT_NULL_COMPLETE = (
    "A sufficient null is a legitimate subtest result, not missing evidence; "
    "L3 completion does not require all assumptions to be VALID"
)
L3_D1_SELF_BOUNDARY = (
    "D1 completion never completes L3; self-declared status never completes "
    "L3; E2 never completes L1 by the same separation principle"
)


