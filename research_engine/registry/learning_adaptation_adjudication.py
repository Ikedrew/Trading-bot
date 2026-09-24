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


