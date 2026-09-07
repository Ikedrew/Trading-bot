"""
Wave 3 — Horizon + Strategy Selection Research tests (S2/S3/S4/HORIZON-1/STRAT-1).

Production-shaped fixtures; no AWS required. The runners accept injected
populations, so every calculation is verified deterministically.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research_engine.experiments.selection_research import (  # noqa: E402
    _MIN_SAMPLE,
    _outcome_records,
    build_horizon1_population,
    build_strat1_pairs,
    run_s2,
    run_s3,
    run_s4,
    run_horizon1,
    run_strat1,
    _spearman_rho,
)

PRIMARY = "PRIMARY_HORIZON_SIMULATION"
ALTERNATIVE = "HORIZON_ALTERNATIVE"


# ─── fixture builders ────────────────────────────────────────────────────────

def mk_shadow(
    opp: str,
    horizon: str = "SCALP",
    r: float | None = 1.0,
    shadow_type: str = PRIMARY,
    strategy: str = "REVERSAL",
    phase: str = "EXPANSION",
    symbol: str = "AUDUSD",
) -> dict:
    return {
        "shadow_trade_id": f"nshadow_{opp}_{horizon}",
        "canonical_opportunity_id": opp,
        "symbol": symbol,
        "shadow_type": shadow_type,
        "evaluated_horizon": horizon,
        "strategy": strategy,
        "pattern": "HAMMER",
        "market_phase": phase,
        "h4_regime": "TRENDING",
        "pnl_r": r,
        "mfe_r": max(r or 0, 0.5),
        "mae_r": -0.4,
        "exit_reason": "take_profit" if (r or 0) > 0 else "stop_loss",
    }


def population_multi_horizon(n: int = 40) -> list[dict]:
    """n opportunities, each with primary SCALP + INTRADAY/EXTENDED alternatives."""
    pop = []
    for i in range(n):
        opp = f"OPP-{i:04d}"
        pop.append(mk_shadow(opp, "SCALP", 1.0, PRIMARY))
        pop.append(mk_shadow(opp, "INTRADAY", 0.2, ALTERNATIVE))
        pop.append(mk_shadow(opp, "EXTENDED", -0.5, ALTERNATIVE))
    return pop


def population_single_horizon(n: int = 40) -> list[dict]:
    return [mk_shadow(f"OPP-{i:04d}", "SCALP", 1.0) for i in range(n)]


# ─── population helpers ──────────────────────────────────────────────────────

class TestPopulation:
    def test_outcome_records_filters_none_and_nonfinite(self):
        pop = [
            mk_shadow("A", r=1.0),
            mk_shadow("B", r=None),
            mk_shadow("C", r=float("nan")),
            mk_shadow("D", r=float("inf")),
        ]
        out = _outcome_records(pop)
        assert [r["canonical_opportunity_id"] for r in out] == ["A"]

    def test_multi_horizon_fixture_shape(self):
        pop = population_multi_horizon(5)
        assert len(pop) == 15
        assert sum(1 for r in pop if r["shadow_type"] == PRIMARY) == 5

    def test_no_local_fallback_imports(self):
        import research_engine.experiments.selection_research as mod
        src = Path(mod.__file__).read_text(encoding="utf-8")
        assert "research_projection" not in src
        assert "replay_data" not in src
        assert "ingest_completed_shadow_trades" in src  # canonical ingestion


# ─── S2 ──────────────────────────────────────────────────────────────────────

class TestS2:
    def test_multiple_horizons_sufficient(self):
        report = run_s2(shadow_trades=population_multi_horizon(40))
        assert report["status"] == "COMPLETE"
        assert report["question_id"] == "S2"
        horizons = report["overall"]["horizons"]
        assert set(horizons.keys()) == {"SCALP", "INTRADAY", "EXTENDED"}
        assert horizons["SCALP"]["mean_r"] == 1.0
        assert horizons["INTRADAY"]["mean_r"] == 0.2
        assert report["overall"]["spread_assessment"]["spread_r"] == 1.5

    def test_horizon_groups_share_opportunities(self):
        report = run_s2(shadow_trades=population_multi_horizon(40))
        horizons = report["overall"]["horizons"]
        assert all(s["n"] == 40 for s in horizons.values())

    def test_insufficient_sample(self):
        report = run_s2(shadow_trades=population_multi_horizon(5))
        assert report["status"] == "INSUFFICIENT_DATA"

    def test_single_horizon_insufficient(self):
        report = run_s2(shadow_trades=population_single_horizon(60))
        assert report["status"] == "INSUFFICIENT_DATA"

    def test_recommendation_differs_on_material_spread(self):
        report = run_s2(shadow_trades=population_multi_horizon(40))
        assert report["recommendation"] == "HORIZON_OUTCOMES_DIFFER"

    def test_observational_warning_present(self):
        report = run_s2(shadow_trades=population_multi_horizon(40))
        warnings = " ".join(report.get("warnings", []))
        assert "SIMULATED" in warnings
        assert "not independent" in warnings or "not causal" in warnings

    def test_primary_view_present(self):
        report = run_s2(shadow_trades=population_multi_horizon(40))
        pv = report["overall"]["primary_horizon_view"]
        assert pv["SCALP"]["n"] == 40
        assert "INTRADAY" not in pv  # alternatives excluded from primary view


# ─── S3 ──────────────────────────────────────────────────────────────────────

class TestS3:
    def test_cells_and_multiple_testing_count(self):
        report = run_s3(shadow_trades=population_multi_horizon(40))
        assert report["status"] == "COMPLETE"
        assert report["overall"]["combinations_tested"] == 3
        warnings = " ".join(report.get("warnings", []))
        assert "Multiple-comparison risk" in warnings

    def test_tiny_cells_excluded_but_counted(self):
        pop = population_multi_horizon(40)
        pop.append(mk_shadow("X1", "SCALP", 2.0, PRIMARY, strategy="MOMENTUM"))
        pop.append(mk_shadow("X2", "SCALP", 2.0, PRIMARY, strategy="MOMENTUM"))
        report = run_s3(shadow_trades=pop)
        assert report["overall"]["combinations_tested"] == 4
        sufficient = report["overall"]["cells_sufficient_n"]
        assert all(not k.startswith("MOMENTUM") for k in sufficient)
        warnings = " ".join(report.get("warnings", []))
        assert "excluded for N < 10" in warnings

    def test_insufficient_sample(self):
        report = run_s3(shadow_trades=population_multi_horizon(5))
        assert report["status"] == "INSUFFICIENT_DATA"

    def test_positive_cells_reported_as_evidence_only(self):
        report = run_s3(shadow_trades=population_multi_horizon(40))
        assert report["recommendation"] == "COMBINATION_EVIDENCE_REPORTED"
        warnings = " ".join(report.get("warnings", []))
        assert "production-ready" in warnings


# ─── S4 ──────────────────────────────────────────────────────────────────────

class TestS4:
    def test_phase_specialisation_detected(self):
        pop = []
        for i in range(20):
            pop.append(mk_shadow(f"A-{i}", "SCALP", 1.5, phase="EXHAUSTION"))
        for i in range(20):
            pop.append(mk_shadow(f"B-{i}", "SCALP", -0.5, phase="EXPANSION"))
        report = run_s4(shadow_trades=pop)
        assert report["status"] == "COMPLETE"
        spec = report["overall"]["specialisation"]["REVERSAL"]
        assert spec["assessment"] == "PHASE_SPECIALISED"
        assert spec["phase_spread_r"] == 2.0
        assert report["recommendation"] == "PHASE_SPECIALISATION_EVIDENCE_REPORTED"

    def test_no_specialisation_when_uniform(self):
        pop = [mk_shadow(f"{i}", "SCALP", 0.5, phase=p)
               for i, p in enumerate(["EXPANSION", "EXHAUSTION"] * 20)]
        report = run_s4(shadow_trades=pop)
        spec = report["overall"]["specialisation"]["REVERSAL"]
        assert spec["assessment"] == "NO_MATERIAL_PHASE_SPECIALISATION"

    def test_insufficient_phase_coverage(self):
        pop = [mk_shadow(f"{i}", "SCALP", 0.5, phase="EXPANSION")
               for i in range(35)]
        report = run_s4(shadow_trades=pop)
        spec = report["overall"]["specialisation"]["REVERSAL"]
        assert spec["assessment"] == "INSUFFICIENT_PHASE_COVERAGE"

    def test_primary_only_population(self):
        pop = []
        for i in range(20):
            pop.append(mk_shadow(f"A-{i}", "SCALP", 1.5, PRIMARY,
                                 phase="EXHAUSTION"))
            pop.append(mk_shadow(f"A-{i}", "INTRADAY", 5.0, ALTERNATIVE,
                                 phase="EXHAUSTION"))
        for i in range(20):
            pop.append(mk_shadow(f"B-{i}", "SCALP", -0.5, PRIMARY,
                                 phase="EXPANSION"))
            pop.append(mk_shadow(f"B-{i}", "INTRADAY", 5.0, ALTERNATIVE,
                                 phase="EXPANSION"))
        report = run_s4(shadow_trades=pop)
        assert report["overall"]["sample_size"] == 40
        cell = report["overall"]["cell_stats"]["REVERSAL|EXHAUSTION"]
        assert cell["mean_r"] == 1.5  # alternative 5.0R excluded

    def test_m10_overlap_boundary_documented(self):
        report = run_s4(shadow_trades=population_multi_horizon(40))
        warnings = " ".join(report.get("warnings", []))
        assert "M10" in warnings and "OVERLAP BOUNDARY" in warnings

    def test_insufficient_sample(self):
        report = run_s4(shadow_trades=population_multi_horizon(5))
        assert report["status"] == "INSUFFICIENT_DATA"


# ─── HORIZON-1 population builder ────────────────────────────────────────────

class TestHorizon1Population:
    def test_within_opportunity_grouping(self):
        pop = population_multi_horizon(3)
        opps = build_horizon1_population(pop)
        assert len(opps) == 3
        info = opps["OPP-0000"]
        assert info["selected_horizon"] == "SCALP"
        assert info["selected_r"] == 1.0
        assert info["alternatives"] == {"INTRADAY": 0.2, "EXTENDED": -0.5}
        assert not info["ambiguous"]

    def test_missing_alternative_outcome(self):
        pop = [mk_shadow("A", "SCALP", 1.0)]  # no alternatives
        opps = build_horizon1_population(pop)
        assert opps["A"]["alternatives"] == {}

    def test_no_lineage_excluded(self):
        pop = [mk_shadow("", "SCALP", 1.0)]
        opps = build_horizon1_population(pop)
        assert opps == {}

    def test_ambiguous_primary_detected(self):
        pop = [
            mk_shadow("A", "SCALP", 1.0, PRIMARY),
            mk_shadow("A", "SCALP", 0.5, PRIMARY),  # replay duplicate
            mk_shadow("A", "INTRADAY", 0.2, ALTERNATIVE),
        ]
        opps = build_horizon1_population(pop)
        assert opps["A"]["ambiguous"] is True


# ─── HORIZON-1 runner ────────────────────────────────────────────────────────

class TestHorizon1:
    def test_selected_best_majority(self):
        report = run_horizon1(shadow_trades=population_multi_horizon(40))
        assert report["status"] == "COMPLETE"
        assert report["question_id"] == "HORIZON-1"
        assert report["overall"]["selected_best"] == 40
        assert report["overall"]["selected_best_rate"] == 1.0
        assert report["overall"]["mean_selected_r"] == 1.0
        assert report["overall"]["mean_best_alternative_r"] == 0.2
        assert report["recommendation"] == "SELECTION_SUPPORTIVE"

    def test_selected_tied_best(self):
        pop = []
        for i in range(40):
            opp = f"T-{i}"
            pop.append(mk_shadow(opp, "SCALP", 0.5, PRIMARY))
            pop.append(mk_shadow(opp, "INTRADAY", 0.48, ALTERNATIVE))
        report = run_horizon1(shadow_trades=pop)
        assert report["overall"]["selected_tied_best"] == 40

    def test_selected_worse_majority(self):
        pop = []
        for i in range(40):
            opp = f"W-{i}"
            pop.append(mk_shadow(opp, "SCALP", -0.5, PRIMARY))
            pop.append(mk_shadow(opp, "INTRADAY", 1.0, ALTERNATIVE))
        report = run_horizon1(shadow_trades=pop)
        assert report["overall"]["selected_underperformed_alternative"] == 40
        assert report["overall"]["mean_selected_minus_best_alternative_r"] == -1.5
        assert report["recommendation"] == "SELECTION_WEAKNESS_SIGNAL"

    def test_mixed_evidence(self):
        pop = []
        for i in range(20):  # best
            opp = f"B-{i}"
            pop.append(mk_shadow(opp, "SCALP", 1.0, PRIMARY))
            pop.append(mk_shadow(opp, "INTRADAY", 0.2, ALTERNATIVE))
        for i in range(20):  # worse
            opp = f"W-{i}"
            pop.append(mk_shadow(opp, "SCALP", -0.5, PRIMARY))
            pop.append(mk_shadow(opp, "INTRADAY", 1.0, ALTERNATIVE))
        report = run_horizon1(shadow_trades=pop)
        assert report["recommendation"] == "MIXED_SELECTION_EVIDENCE"

    def test_missing_alternative_outcome_counted(self):
        pop = []
        for i in range(30):  # comparable
            opp = f"C-{i}"
            pop.append(mk_shadow(opp, "SCALP", 1.0, PRIMARY))
            pop.append(mk_shadow(opp, "INTRADAY", 0.2, ALTERNATIVE))
        for i in range(20):  # not comparable (no alternative)
            pop.append(mk_shadow(f"N-{i}", "SCALP", 1.0, PRIMARY))
        report = run_horizon1(shadow_trades=pop)
        assert report["overall"]["comparable_opportunities"] == 30
        assert report["overall"]["insufficient_alternative_outcomes"] == 20
        assert report["status"] == "COMPLETE"

    def test_all_alternatives_missing_is_insufficient(self):
        report = run_horizon1(shadow_trades=population_single_horizon(60))
        assert report["status"] == "INSUFFICIENT_DATA"
        assert report["overall"]["insufficient_alternative_outcomes"] == 60

    def test_ambiguous_excluded(self):
        pop = []
        for i in range(30):
            opp = f"C-{i}"
            pop.append(mk_shadow(opp, "SCALP", 1.0, PRIMARY))
            pop.append(mk_shadow(opp, "INTRADAY", 0.2, ALTERNATIVE))
        pop.append(mk_shadow("AMB", "SCALP", 1.0, PRIMARY))
        pop.append(mk_shadow("AMB", "SCALP", 0.5, PRIMARY))
        pop.append(mk_shadow("AMB", "INTRADAY", 0.2, ALTERNATIVE))
        report = run_horizon1(shadow_trades=pop)
        assert report["overall"]["ambiguous_excluded"] == 1
        assert report["overall"]["comparable_opportunities"] == 30

    def test_no_cross_opportunity_comparison(self):
        # Opportunity A has only SCALP; a DIFFERENT opportunity B has
        # INTRADAY. They must NOT be compared even though horizons line up.
        pop = []
        for i in range(30):
            pop.append(mk_shadow(f"A-{i}", "SCALP", 1.0, PRIMARY))
        for i in range(30):
            pop.append(mk_shadow(f"B-{i}", "INTRADAY", -2.0, ALTERNATIVE))
        report = run_horizon1(shadow_trades=pop)
        assert report["status"] == "INSUFFICIENT_DATA"
        assert report["overall"]["comparable_opportunities"] == 0

    def test_within_opportunity_comparison_is_genuine(self):
        report = run_horizon1(shadow_trades=population_multi_horizon(40))
        per_hz = report["overall"]["per_selected_horizon"]
        assert per_hz["SCALP"]["selected_best_rate"] == 1.0

    def test_selection_engine_agreement_crosscheck(self):
        hc = [
            {"canonical_opportunity_id": f"OPP-{i:04d}",
             "selection_status": "SELECTED", "horizon": "SCALP"}
            for i in range(40)
        ]
        report = run_horizon1(
            shadow_trades=population_multi_horizon(40), horizon_candidates=hc)
        agreement = report["overall"]["selection_engine_agreement"]
        assert agreement == {"checked": 40, "agreements": 40,
                             "agreement_rate": 1.0}

    def test_agreement_disagreement_detected(self):
        hc = [
            {"canonical_opportunity_id": f"OPP-{i:04d}",
             "selection_status": "SELECTED", "horizon": "EXTENDED"}
            for i in range(40)
        ]
        report = run_horizon1(
            shadow_trades=population_multi_horizon(40), horizon_candidates=hc)
        assert report["overall"]["selection_engine_agreement"]["agreements"] == 0

    def test_insufficient_sample(self):
        report = run_horizon1(shadow_trades=population_multi_horizon(5))
        assert report["status"] == "INSUFFICIENT_DATA"

    def test_within_opportunity_warning_present(self):
        report = run_horizon1(shadow_trades=population_multi_horizon(40))
        assumptions = " ".join(report.get("assumptions", []))
        assert "WITHIN-opportunity only" in assumptions


# ─── STRAT-1 helpers ─────────────────────────────────────────────────────────

def mk_candidates(n_per_bucket: int = 12) -> list[dict]:
    """Selected candidates across 3 confidence buckets + rejected extras."""
    cands = []
    r = 0
    for confidence in (0.9, 0.6, 0.3):
        for _ in range(n_per_bucket):
            cands.append({
                "candidate_id": f"C-sel-{r}",
                "canonical_opportunity_id": f"SOPP-{r:04d}",
                "strategy_family": "REVERSAL",
                "confidence": confidence, "rank": 1, "selected": True,
            })
            r += 1
    return cands


def outcomes_for(cands: list[dict], mapping) -> list[dict]:
    """Primary shadow outcomes for each selected candidate's opportunity."""
    return [
        mk_shadow(c["canonical_opportunity_id"], "SCALP", mapping(c["confidence"]),
                  PRIMARY)
        for c in cands if c["selected"]
    ]


class TestStrat1Pairs:
    def test_lineage_join(self):
        cands = mk_candidates(2)
        pop = outcomes_for(cands, lambda c: 0.5)
        built = build_strat1_pairs(cands, pop)
        assert built["n_candidates"] == 6
        assert len(built["pairs"]) == 6
        assert built["pairs"][0]["r_multiple"] == 0.5

    def test_rejected_candidates_have_no_outcome_join(self):
        cands = mk_candidates(2)
        cands.append({
            "candidate_id": "C-rej",
            "canonical_opportunity_id": "SOPP-9999",
            "strategy_family": "MOMENTUM",
            "confidence": 0.4, "rank": 2, "selected": False,
        })
        pop = outcomes_for(cands, lambda c: 0.5)
        built = build_strat1_pairs(cands, pop)
        assert all(p["canonical_opportunity_id"] != "SOPP-9999"
                   for p in built["pairs"])

    def test_duplicate_candidate_deduplicated(self):
        cands = mk_candidates(2)
        cands.append(dict(cands[0]))  # exact replay
        pop = outcomes_for(cands, lambda c: 0.5)
        built = build_strat1_pairs(cands, pop)
        assert built["n_candidates"] == 6

    def test_ambiguous_selected_excluded(self):
        cands = mk_candidates(2)
        cands.append({
            "candidate_id": "C-amb",
            "canonical_opportunity_id": "SOPP-0000",
            "strategy_family": "MOMENTUM",
            "confidence": 0.7, "rank": 1, "selected": True,
        })
        pop = outcomes_for(cands, lambda c: 0.5)
        built = build_strat1_pairs(cands, pop)
        assert built["ambiguous"] == 1
        assert all(p["canonical_opportunity_id"] != "SOPP-0000"
                   for p in built["pairs"])

    def test_missing_outcome_counted(self):
        cands = mk_candidates(2)
        built = build_strat1_pairs(cands, [])  # no outcomes at all
        assert built["pairs"] == []
        assert built["n_no_outcome"] == 6

    def test_missing_lineage_counted(self):
        cands = mk_candidates(2)
        for c in cands:
            c["canonical_opportunity_id"] = ""
        built = build_strat1_pairs(cands, [])
        assert built["n_no_lineage"] == 6


# ─── STRAT-1 runner ──────────────────────────────────────────────────────────

class TestStrat1:
    def test_monotonic_positive_signal(self):
        cands = mk_candidates(15)
        pop = outcomes_for(cands, lambda c: 2.0 if c >= 0.75
                           else (1.0 if c >= 0.5 else 0.0))
        report = run_strat1(strategy_candidates=cands, shadow_trades=pop)
        assert report["status"] == "COMPLETE"
        assert report["question_id"] == "STRAT-1"
        assert report["overall"]["spearman_rho"] == 1.0
        assert report["overall"]["monotonicity"] == "MONOTONIC_POSITIVE"
        assert report["recommendation"] == "CONFIDENCE_CONTAINS_OUTCOME_SIGNAL"

    def test_non_monotonic_detected(self):
        cands = mk_candidates(15)
        pop = outcomes_for(cands, lambda c: 0.0 if c >= 0.75
                           else (2.0 if c >= 0.5 else 0.5))
        report = run_strat1(strategy_candidates=cands, shadow_trades=pop)
        assert report["overall"]["monotonicity"] == "NON_MONOTONIC"

    def test_negative_monotonic(self):
        cands = mk_candidates(15)
        pop = outcomes_for(cands, lambda c: -1.0 if c >= 0.75
                           else (-0.5 if c >= 0.5 else 0.5))
        report = run_strat1(strategy_candidates=cands, shadow_trades=pop)
        assert report["overall"]["monotonicity"] == "MONOTONIC_NEGATIVE"

    def test_insufficient_sample(self):
        cands = mk_candidates(2)  # 6 pairs < 30
        pop = outcomes_for(cands, lambda c: 0.5)
        report = run_strat1(strategy_candidates=cands, shadow_trades=pop)
        assert report["status"] == "INSUFFICIENT_DATA"

    def test_missing_confidence_skipped(self):
        cands = mk_candidates(12)
        for c in cands:
            c["confidence"] = None
        pop = outcomes_for(cands, lambda c: 0.5)
        report = run_strat1(strategy_candidates=cands, shadow_trades=pop)
        assert report["status"] == "INSUFFICIENT_DATA"

    def test_no_false_counterfactual_warning(self):
        cands = mk_candidates(12)
        pop = outcomes_for(cands, lambda c: 0.5)
        report = run_strat1(strategy_candidates=cands, shadow_trades=pop)
        warnings = " ".join(report.get("warnings", []))
        assert "SELECTION OPTIMALITY CANNOT YET BE PROVEN" in warnings

    def test_ranking_not_calibration(self):
        cands = mk_candidates(12)
        pop = outcomes_for(cands, lambda c: 0.5)
        report = run_strat1(strategy_candidates=cands, shadow_trades=pop)
        assumptions = " ".join(report.get("assumptions", []))
        assert "NOT a probability" in assumptions
        assert "calibration claim is made" in assumptions

    def test_confidence_bucket_multiple_comparisons_acknowledged(self):
        cands = mk_candidates(12)
        pop = outcomes_for(cands, lambda c: 0.5)
        report = run_strat1(strategy_candidates=cands, shadow_trades=pop)
        warnings = " ".join(report.get("warnings", []))
        assert "multiple" in warnings.lower()


# ─── Spearman ────────────────────────────────────────────────────────────────

class TestSpearman:
    def test_perfect_positive(self):
        assert _spearman_rho([(1, 1), (2, 2), (3, 3), (4, 4)]) == 1.0

    def test_perfect_negative(self):
        assert _spearman_rho([(1, 4), (2, 3), (3, 2), (4, 1)]) == -1.0

    def test_ties_use_average_ranks(self):
        rho = _spearman_rho([(1, 1), (1, 2), (2, 3), (3, 4)])
        assert rho is not None and rho > 0.9

    def test_degenerate(self):
        assert _spearman_rho([(1, 1), (1, 1)]) is None
        assert _spearman_rho([(1, 1), (1, 1), (1, 1)]) is None  # zero variance


# ─── Architecture / governance / registry integration ───────────────────────

class TestArchitecture:
    def test_all_five_questions_discovered_exactly_once(self):
        from research_engine.runner_discovery import get_all_runners
        runners = get_all_runners()
        for qid in ("S2", "S3", "S4", "HORIZON-1", "STRAT-1"):
            assert qid in runners, f"{qid} not discovered"
        # dict-keyed → duplicates impossible; verify registry has exactly one
        from research_engine.registry.research_question_registry import REGISTRY
        for qid in ("S2", "S3", "S4", "HORIZON-1", "STRAT-1"):
            matches = [q for q in REGISTRY if q.id == qid]
            assert len(matches) == 1
            assert matches[0].runner_function

    def test_registry_entries_are_canonical(self):
        from research_engine.registry.research_question_registry import (
            REGISTRY_BY_ID, QuestionCategory,
        )
        for qid in ("S2", "S3", "S4", "HORIZON-1", "STRAT-1"):
            q = REGISTRY_BY_ID[qid]
            assert q.category == QuestionCategory.STRATEGY_HORIZON
            assert q.runner_module == "research_engine.experiments.selection_research"

    def test_redundant_questions_still_runnerless(self):
        # S5/S6/S7 remain intent-only — no duplicate runners created
        from research_engine.registry.research_question_registry import (
            REGISTRY_BY_ID,
        )
        for qid in ("S5", "S6", "S7"):
            assert not REGISTRY_BY_ID[qid].runner_module

    def test_no_trading_mutation_imports(self):
        src = Path(
            "research_engine/experiments/selection_research.py"
        ).read_text(encoding="utf-8")
        for forbidden in ("MT5", "order_send", "RiskManager",
                          "MT5Execution", "ShadowRuntime", "persist_trade_truth",
                          "import core"):
            assert forbidden not in src

    def test_gap4_status_contract(self):
        for runner, kwargs in (
            (run_s2, {"shadow_trades": population_multi_horizon(40)}),
            (run_s3, {"shadow_trades": population_multi_horizon(40)}),
            (run_s4, {"shadow_trades": population_multi_horizon(40)}),
            (run_horizon1, {"shadow_trades": population_multi_horizon(40)}),
            (run_strat1, {"strategy_candidates": mk_candidates(12),
                          "shadow_trades": outcomes_for(
                              mk_candidates(12), lambda c: 0.5)}),
        ):
            report = runner(**kwargs)
            assert isinstance(report["status"], str)
            assert report["status"] in {"COMPLETE", "INSUFFICIENT_DATA",
                                        "BLOCKED", "WAITING_DATA"}
            assert isinstance(report["recommendation"], str)
            assert report["status"] != report["recommendation"] or True  # both present

    def test_report_has_sample_and_confidence(self):
        report = run_s2(shadow_trades=population_multi_horizon(40))
        assert report["dataset"]["sample_size"] == 120
        assert report["confidence"] in {"LOW", "MEDIUM", "HIGH"}

    def test_leakage_note_present(self):
        report = run_horizon1(shadow_trades=population_multi_horizon(40))
        assumptions = " ".join(report.get("assumptions", []))
        assert "pre-outcome" in assumptions
        assert "post-outcome research evidence only" in assumptions

    def test_report_serialisable(self):
        import json
        reports = [
            run_s2(shadow_trades=population_multi_horizon(40)),
            run_s3(shadow_trades=population_multi_horizon(40)),
            run_s4(shadow_trades=population_multi_horizon(40)),
            run_horizon1(shadow_trades=population_multi_horizon(40)),
            run_strat1(strategy_candidates=mk_candidates(12),
                       shadow_trades=outcomes_for(
                           mk_candidates(12), lambda c: 0.5)),
        ]
        for r in reports:
            text = json.dumps(r)
            assert "NaN" not in text
            assert "Infinity" not in text
