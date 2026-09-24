"""Focused governance tests for frozen HD11/HD12 learning/adaptation contracts."""
from __future__ import annotations
import research_engine.registry.learning_adaptation_adjudication as A
from research_engine.registry.master_repair_ledger import HUMAN_SEMANTIC_DECISIONS as _H
from research_engine.registry.master_repair_ledger import REPAIR_WAVES as _W
from research_engine.registry.master_repair_ledger import STRUCTURALLY_NON_OPERATIONAL_IDS as _N
from research_engine.registry.master_repair_ledger import operational_baseline as _base
from research_engine.registry.research_question_registry import REGISTRY_BY_ID as _R
from research_engine.registry.wave_a_no_runner_definitions import WAVE_A_NO_RUNNER_DESIGNS as _D
from research_engine.runner_discovery import discover_runners as _disc
def test_a_versions():
 assert A.TARGETS == ("L1", "L2", "L3", "L4", "L6", "L7")
 assert A.FULL["version"] == A.LEARNING_VERSION
 assert A.HD11["version"] == A.HD11_VERSION
 assert A.HD12["version"] == A.HD12_VERSION
 assert _H["HD11"].implementation_blocked_until_decision is False
 assert _H["HD12"].implementation_blocked_until_decision is False
 assert _W["RW10"].implemented is False and _W["RW11"].implemented is False
def test_b_distinct():
 assert A.L1_DISTINCT == "E2 pooled expectancy"
 assert A.L3_DISTINCT == "D1 attribution"
 assert A.L3_TESTS == ("weight", "regime", "mapping")
 assert A.FUTURE_RUNNERS["L1"]["report"] == "l1_pattern_degradation.json"
 assert A.FUTURE_L3["report"] == "l3_architecture_assumption_validity.json"
 assert A.FUTURE_L7["report"] == "l7_adaptation_evidence.json"
 assert A.L1_ESTIMAND.startswith("late-minus-early")
 assert A.L7_OPTION.startswith("A ")
 assert _D["L6"].lifecycle == "PROPOSED"
def test_c_forbidden():
 assert A.FORBIDDEN_SOURCES["L1"] == ("q5_pattern_degradation.json",)
 assert A.FORBIDDEN_L2 == ("q15_learning_velocity.json",)
 assert A.FORBIDDEN_L3 == ("q1_component_reward.json",)
 assert A.FORBIDDEN_L4 == ("q17_drawdown_precursors.json",)
 assert A.FORBIDDEN_SOURCES["L7"] == ("l7_shadow_ab_validation.json",)
 assert A.E2_NEVER_L1.startswith("E2") and A.D1_NEVER_L3.startswith("D1")
 assert A.COUNTS_NEVER.startswith("trade") and A.LEGACY7_BANNED.startswith("chronological")
 assert A.PROPOSED_NEVER.startswith("PROPOSED")
def test_d_crosscut():
 assert "CURRENT" in A.CROSS_CURRENT and "VALID_CURRENT" in A.CROSS_COMPLETE
 assert "never define N" in A.CROSS_FANOUT
 assert "never defaulted to zero" in A.CROSS_MISSING
 assert A.CROSS_NULL.startswith("Sufficient") and "production" in A.CROSS_PROD
 assert A.SELF_STATUS_FORBIDDEN is True
def test_e_l1_implemented_only():
 assert _base() == (59, 11)
 assert "L1" not in _N
 assert set(A.TARGETS) - {"L1"} <= set(_N)
 assert "L6" not in _disc() and _R["L6"].runner_module == ""
 assert _R["L1"].report_filename == "l1_pattern_degradation.json"
 assert _R["L1"].runner_function == "run_l1"
 assert _R["L3"].report_filename == "q1_component_reward.json"
def test_f_l3_weight_contract_frozen():
 assert A.L3_WEIGHT_QUESTION.startswith("Does the historically applied")
 assert "canonical-opportunity grain" in A.L3_WEIGHT_QUESTION
 assert A.L3_WEIGHT_COMPONENTS == ("pattern_quality", "bias_alignment", "market_quality", "trend_alignment", "chop_clarity", "volatility_quality", "bias_stability", "confirmation_pre", "htf_alignment", "h4_alignment")
 assert "decision_trace_v1.persisted per-opportunity weight vector" in " ".join(A.L3_WEIGHT_EVIDENCE_FIELDS)
 assert "mandatory" in " ".join(A.L3_WEIGHT_EVIDENCE_FIELDS)
 assert "PRIMARY_HORIZON_SIMULATION" in " ".join(A.L3_WEIGHT_EVIDENCE_FIELDS)
 assert "One distinct identity.canonical_opportunity_id is one paired" in A.L3_WEIGHT_GRAIN
 assert "D1 rows may be reused as input rows only" in A.L3_WEIGHT_GRAIN
 assert A.L3_WEIGHT_ESTIMAND.startswith("Spearman rank-correlation rho")
 assert "equal opportunity weight" in A.L3_WEIGHT_ESTIMAND
 assert "rho > 0 supports weight validity" in A.L3_WEIGHT_DIRECTION
 assert "rho == 0 is the null" in A.L3_WEIGHT_DIRECTION
 assert "average-rank ties" in A.L3_WEIGHT_INFERENCE
 assert "Fisher-z 95% interval" in A.L3_WEIGHT_INFERENCE
 assert "exactly one raw p-value" in A.L3_WEIGHT_INFERENCE
 assert "Holm-adjusted p-value only" in A.L3_WEIGHT_INFERENCE
 assert A.L3_WEIGHT_MIN == {"distinct_paired_opportunities": 100}
 assert "implies VALID" in A.L3_WEIGHT_VALID_RULE
 assert "Holm-adjusted p <= 0.05" in A.L3_WEIGHT_VALID_RULE
 assert "interval lower > 0" in A.L3_WEIGHT_VALID_RULE
 assert "implies INVALID" in A.L3_WEIGHT_INVALID_RULE
 assert "interval upper < 0" in A.L3_WEIGHT_INVALID_RULE
 assert "NO_RELIABLE_WEIGHT_ASSOCIATION" in A.L3_WEIGHT_NULL_RULE
 assert "never completes L3 alone" in A.L3_WEIGHT_NULL_RULE
 assert "at least 100 distinct canonical opportunities" in A.L3_WEIGHT_SUFFICIENCY
 assert "never defaulted to zero" in A.L3_WEIGHT_SUFFICIENCY
 assert "inestimable and therefore insufficient" in A.L3_WEIGHT_SUFFICIENCY
def test_g_l3_weight_family_and_status():
 assert "exactly one two-sided p-value" in A.L3_WEIGHT_HOLM_FAMILY
 assert "(weight, regime, mapping)" in A.L3_WEIGHT_HOLM_FAMILY
 assert "alpha 0.05" in A.L3_WEIGHT_HOLM_FAMILY
 assert "a weight VALID alone never completes L3" in A.L3_WEIGHT_HOLM_FAMILY
 assert "missing persisted per-opportunity weight vector" in A.L3_WEIGHT_BLOCKED
 assert "runtime/config constants" in A.L3_WEIGHT_BLOCKED
 assert "decision_ledger substitution" in A.L3_WEIGHT_BLOCKED
 assert "WAITING_DATA" in A.L3_WEIGHT_WAITING
 assert "fewer than 100 distinct paired" in A.L3_WEIGHT_WAITING
 assert A.L3_WEIGHT_PROFILE_MANDATORY is True
 assert "mandatory weight identity is absent" in A.L3_WEIGHT_PROFILE_ABSENT
 assert "without creating or reconstructing" in A.L3_WEIGHT_PROFILE_ABSENT
 assert "never stand in for historical evidence" in A.L3_WEIGHT_RUNTIME_SUBSTITUTION_FORBIDDEN
 assert "never establish weight validity" in A.L3_WEIGHT_D1_BOUNDARY
 assert "forbidden for L3" in A.L3_WEIGHT_D1_BOUNDARY
 assert A.L3_WEIGHT_DECISION_LEDGER_NOT_AUTHORITY is True
 assert "no new authority is added here" in A.L3_WEIGHT_DECISION_LEDGER_RULE
def test_h_l3_weight_deterministic_and_nonoperational():
 import math
 def _rank_avg(xs):
  order = sorted(range(len(xs)), key=lambda i: xs[i])
  ranks = [0.0] * len(xs)
  i = 0
  while i < len(order):
   j = i
   while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
    j += 1
   avg = (i + 1 + j + 1) / 2.0
   for k in range(i, j + 1):
    ranks[order[k]] = avg
   i = j + 1
  return ranks
 def _spearman(xs, ys):
  rx, ry = _rank_avg(xs), _rank_avg(ys)
  n = len(xs)
  mx, my = sum(rx) / n, sum(ry) / n
  cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
  vx = sum((a - mx) ** 2 for a in rx)
  vy = sum((b - my) ** 2 for b in ry)
  assert vx > 0 and vy > 0
  return cov / math.sqrt(vx * vy)
 xs = [float(i) for i in range(1, 101)]
 ys = [float(i) for i in range(1, 101)]
 rho = _spearman(xs, ys)
 assert rho == 1.0
 assert _spearman(xs, ys) == rho
 n = 100
 z = math.atanh(max(-0.999999, min(0.999999, rho * 0.999999)))
 se = 1.0 / math.sqrt(n - 3)
 lo = math.tanh(z - 1.96 * se)
 assert lo > 0
 raws = {"weight": 0.01, "regime": 0.04, "mapping": 0.20}
 ordered = sorted(raws, key=lambda k: raws[k])
 m = len(ordered)
 adj = {}
 for rank, key in enumerate(ordered):
  adj[key] = min(1.0, (m - rank) * raws[key])
  assert adj[key] >= 0.0 and adj[key] <= 1.0
 assert ordered == ["weight", "regime", "mapping"]
def test_i_l3_regime_contract_frozen():
 assert A.L3_REGIME_QUESTION.startswith("Does the historically persisted canonical MarketContext Regime")
 assert "canonical-opportunity grain" in A.L3_REGIME_QUESTION
 assert A.L3_REGIME_CELLS == ("TRENDING", "RANGING", "TRANSITIONAL")
 assert A.L3_REGIME_TAXONOMY_FROZEN == ("TRENDING", "RANGING", "TRANSITIONAL")
 assert A.L3_REGIME_ESTIMAND.startswith("Vector of opportunity-weighted mean R")
 assert "market_context_v1.regime" in " ".join(A.L3_REGIME_EVIDENCE_FIELDS)
 assert "diagnostic consistency evidence only" in " ".join(A.L3_REGIME_EVIDENCE_FIELDS)
 assert "PRIMARY_HORIZON_SIMULATION" in " ".join(A.L3_REGIME_EVIDENCE_FIELDS)
 assert "one grouped observation" in A.L3_REGIME_GRAIN
 assert "Kruskal-Wallis H" in A.L3_REGIME_INFERENCE
 assert "chi-square df=2" in A.L3_REGIME_INFERENCE
 assert "exactly one raw p-value" in A.L3_REGIME_INFERENCE
 assert A.L3_REGIME_MIN == {"distinct_grouped_opportunities": 100, "per_cell": 30}
 assert "implies VALID" in A.L3_REGIME_VALID_RULE
 assert "Holm-adjusted p <= 0.05" in A.L3_REGIME_VALID_RULE
 assert "INVALID is not inferable" in A.L3_REGIME_INVALID_RULE
 assert "no direction may be invented" in A.L3_REGIME_INVALID_RULE
 assert "NO_RELIABLE_REGIME_SEPARATION" in A.L3_REGIME_NULL_RULE
 assert "never completes L3 alone" in A.L3_REGIME_NULL_RULE
 assert "at least 30 in TRENDING" in A.L3_REGIME_SUFFICIENCY
 assert "inestimable and therefore insufficient" in A.L3_REGIME_SUFFICIENCY
 assert "exactly one two-sided p-value" in A.L3_REGIME_HOLM
 assert "(weight, regime, mapping)" in A.L3_REGIME_HOLM
 assert "a regime VALID alone never completes L3" in A.L3_REGIME_HOLM
 assert "missing/non-unique entity_id lineage" in A.L3_REGIME_BLOCKED
 assert "regime_source as authority" in A.L3_REGIME_BLOCKED
 assert "new/renamed regime taxonomy" in A.L3_REGIME_BLOCKED
 assert "WAITING_DATA" in A.L3_REGIME_WAITING
 assert A.L3_REGIME_AUTHORITY_MANDATORY is True
 assert "persisted core.market_context.models.Regime" in A.L3_REGIME_SOURCE_RULE
 assert "never a stronger authority" in A.L3_REGIME_SOURCE_RULE
 assert "no new regime authority is added here" in A.L3_REGIME_SOURCE_RULE
 assert "forbidden for L3" in A.L3_REGIME_M1_BOUNDARY
def test_j_l3_mapping_contract_frozen():
 assert A.L3_MAPPING_QUESTION.startswith("Does the historically persisted V10")
 assert "canonical-opportunity grain" in A.L3_MAPPING_QUESTION
 assert A.L3_MAPPING_FAMILIES == ("LIQUIDITY_SWEEP_REVERSAL", "FALSE_BREAK", "TREND_CONTINUATION", "BREAKOUT_EXPANSION", "MEAN_REVERSION", "RANGE_REACTION")
 assert A.L3_MAPPING_ESTIMAND.startswith("Vector of opportunity-weighted mean R")
 assert "identity.strategy_id frozen at shadow OPEN" in " ".join(A.L3_MAPPING_EVIDENCE_FIELDS)
 assert "required but not established" in " ".join(A.L3_MAPPING_EVIDENCE_FIELDS)
 assert "core.v10.strategy_family.StrategyFamily" in " ".join(A.L3_MAPPING_EVIDENCE_FIELDS)
 assert "one grouped observation" in A.L3_MAPPING_GRAIN
 assert "Kruskal-Wallis H" in A.L3_MAPPING_INFERENCE
 assert "chi-square df=5" in A.L3_MAPPING_INFERENCE
 assert "exactly one raw p-value" in A.L3_MAPPING_INFERENCE
 assert A.L3_MAPPING_MIN == {"distinct_grouped_opportunities": 100, "per_cell": 30}
 assert "implies VALID" in A.L3_MAPPING_VALID_RULE
 assert "INVALID is not inferable" in A.L3_MAPPING_INVALID_RULE
 assert "no direction may be invented" in A.L3_MAPPING_INVALID_RULE
 assert "NO_RELIABLE_MAPPING_SEPARATION" in A.L3_MAPPING_NULL_RULE
 assert "never completes L3 alone" in A.L3_MAPPING_NULL_RULE
 assert "at least 30 in every frozen family" in A.L3_MAPPING_SUFFICIENCY
 assert "never establishes mapping validity" in A.L3_MAPPING_SUFFICIENCY
 assert "inestimable and therefore insufficient" in A.L3_MAPPING_SUFFICIENCY
 assert "exactly one two-sided p-value" in A.L3_MAPPING_HOLM
 assert "a mapping VALID alone never completes L3" in A.L3_MAPPING_HOLM
 assert "selected_strategy" in A.L3_MAPPING_BLOCKED
 assert "pattern-inferred family substitution" in A.L3_MAPPING_BLOCKED
 assert "WAITING_DATA" in A.L3_MAPPING_WAITING
 assert "never proves mapping validity" in A.L3_MAPPING_LABEL_NOT_PROOF
 assert "never establish mapping validity" in A.L3_MAPPING_E3_BOUNDARY
 assert A.L3_MAPPING_AUTHORITY_MANDATORY is True
 assert "legacy activation" in A.L3_MAPPING_SOURCE_RULE
 assert "live_facts.strategy" in A.L3_MAPPING_SOURCE_RULE
 assert "mapping remains BLOCKED" in A.L3_MAPPING_SOURCE_RULE
 assert "no new authority is added here" in A.L3_MAPPING_SOURCE_RULE
 assert "No existing persisted shadow OPEN field" in A.L3_MAPPING_EVIDENCE_BLOCKER
 assert "do not reconstruct" in A.L3_MAPPING_EVIDENCE_BLOCKER
def test_k_l3_three_test_holm_and_completion_frozen():
 assert A.L3_TESTS == ("weight", "regime", "mapping")
 assert A.L3_HOLM_ORDER == ("weight", "regime", "mapping")
 assert "Exactly one raw two-sided p-value" in A.L3_HOLM_FAMILY
 assert "frozen tie order" in A.L3_HOLM_FAMILY
 assert "alpha 0.05" in A.L3_HOLM_FAMILY
 assert "any BLOCKED subtest implies L3 BLOCKED" in A.L3_COMPLETION_PRECEDENCE
 assert "implies L3 WAITING_DATA" in A.L3_COMPLETION_PRECEDENCE
 assert "may COMPLETE" in A.L3_COMPLETION_PRECEDENCE
 assert "not missing evidence" in A.L3_SUFFICIENT_NULL_COMPLETE
 assert "does not require all assumptions to be VALID" in A.L3_SUFFICIENT_NULL_COMPLETE
 assert A.D1_NEVER_L3.startswith("D1")
 assert "never completes L3" in A.L3_D1_SELF_BOUNDARY
 assert "self-declared status never completes" in A.L3_D1_SELF_BOUNDARY
 assert A.SELF_STATUS_FORBIDDEN is True
 raws = {"weight": 0.01, "regime": 0.04, "mapping": 0.20}
 ordered = sorted(raws, key=lambda k: (raws[k], list(A.L3_HOLM_ORDER).index(k)))
 m = len(ordered)
 adj = {k: min(1.0, (m - r) * raws[k]) for r, k in enumerate(ordered)}
 assert ordered == ["weight", "regime", "mapping"]
 assert adj["weight"] == min(1.0, 3 * 0.01)
 assert adj["regime"] == min(1.0, 2 * 0.04)
 assert adj["mapping"] == min(1.0, 1 * 0.20)
 def _status(blocked, waiting):
  if any(blocked): return "BLOCKED"
  if any(waiting): return "WAITING_DATA"
  return "EVALUATE"
 assert _status([True, False, False], [False, True, False]) == "BLOCKED"
 assert _status([False, False, False], [False, True, False]) == "WAITING_DATA"
 assert _status([False, False, False], [False, False, False]) == "EVALUATE"
 assert "L3" in _N
 import importlib.util as _ilu
 assert _ilu.find_spec(A.FUTURE_L3["module"]) is None
 assert _R["L3"].runner_module == "research_engine.experiments.component_reward"
 assert _R["L3"].report_filename == "q1_component_reward.json"
 assert _W["RW10"].implemented is False
 assert _base() == (59, 11)

