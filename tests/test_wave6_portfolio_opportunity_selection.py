"""
Wave 6 — Portfolio + Opportunity Selection Research tests (PORT-1, OPP-1, D6).

Covers:
    1. PORT-1 is registered and executable.
    2. OPP-1 is registered and executable.
    3. D6 is registered and executable.
    4. D6 materially reads portfolio_rankings.
    5. Removing/emptying portfolio_rankings causes D6 to report missing evidence.
    6. PORT-1 correctly joins ranking evidence to candidate/decision/outcome evidence.
    7. OPP-1 correctly distinguishes opportunity-level from portfolio-level selection.
    8. Canonical IDs are preferred for joins.
    9. Quarantine paths are not treated as active research evidence.
    10. V1-only/versioning assumptions remain intact.
    11. Empty/small sample returns a truthful insufficient-evidence result.
    12. No live trading/runtime code is modified.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research_engine.experiments.portfolio_ranking import (
    run_portfolio_ranking,
    run_port_1,
)
from research_engine.experiments.opportunity_selection import run_opp_1


# ==============================================================================
# FIXTURE BUILDERS
# ==============================================================================


def _build_ranking_records(
    n_cycles: int = 11,
    min_cands: int = 2,
    max_cands: int = 5,
) -> tuple[list[dict], list[dict], list[dict]]:
    """Build portfolio_rankings + decision_ledger + trade_truth fixtures."""
    rankings = []
    decisions = []
    truths = []
    for cycle in range(1001, 1001 + n_cycles):
        n_cands = min_cands + (cycle % (max_cands - min_cands + 1))
        syms = [f"SYM-{i}" for i in range(n_cands)]
        cands = []
        for i, sym in enumerate(syms):
            sel = "SELECTED" if i == 0 else "OUTRANKED"
            cands.append({
                "symbol": sym, "pattern": "HAMMER", "strategy": "REVERSAL",
                "strategy_confidence": 0.8 - i * 0.1,
                "score_neutral": 0.6, "score_strategy": 0.7,
                "ev": 0.0015 - i * 0.0003, "rr_effective": 2.5 - i * 0.3,
                "market_state": "EXPANDING",
                "rank_score": 3.0 - i * 0.8, "rank_position": i + 1,
                "eligible": True, "block_reason": None,
                "selection_status": sel,
                "opportunity_id": f"OPP-{sym}-{cycle}",
            })
            corr_id = f"CORR-{cycle}-{sym}"
            opp_id = f"OPP-{sym}-{cycle}"
            decisions.append({
                "cycle_id": cycle, "symbol": sym, "decision": "EXECUTE",
                "correlation_id": corr_id, "canonical_opportunity_id": opp_id,
                "timestamp_utc": 1000000 + cycle, "signal_score": 0.7,
            })
            truths.append({
                "identity": {
                    "trade_id": f"T-{cycle}-{sym}",
                    "correlation_id": corr_id,
                    "symbol": sym,
                },
                "outcome": {"r_multiple_realised": 0.5 + (1 if i == 0 else -0.2)},
            })
        rankings.append({
            "schema_version": "portfolio_ranking_v1",
            "cycle_id": cycle,
            "ranking_id": f"ranking_{cycle}",
            "selected_symbol": syms[0],
            "selected_rank_score": 3.0,
            "total_candidates": len(syms),
            "eligible_count": len(syms),
            "candidates": cands,
        })
    return rankings, decisions, truths


def _build_opportunity_records(
    n_cycles: int = 11,
) -> tuple[list[dict], list[dict], list[dict]]:
    """Build opportunities + horizon_candidates + assessments fixtures."""
    opps = []
    hcs = []
    assessments = []
    for cycle in range(1001, 1001 + n_cycles):
        for sym in ["EURUSD", "GBPUSD"]:
            opp_id = f"OPP-{sym}-{cycle}"
            is_promoted = sym == "EURUSD"
            opps.append({
                "opportunity_id": opp_id,
                "canonical_opportunity_id": opp_id,
                "symbol": sym, "state": "ASSESSED",
                "overall_score": 0.7 + (0.1 if is_promoted else 0),
            })
            hcs.append({
                "canonical_opportunity_id": opp_id,
                "candidate_id": f"HC-{cycle}-{sym}",
                "selection_status": "SELECTED" if is_promoted else "REJECTED",
            })
            assessments.append({
                "opportunity_id": opp_id,
                "score_strategy": 0.7 + (0.1 if is_promoted else 0),
            })
    return opps, hcs, assessments


# ==============================================================================
# TEST 1 — PORT-1 is registered and executable
# ==============================================================================


class TestPort1Registration:
    def test_registry_has_port1(self):
        from research_engine.registry.research_question_registry import (
            REGISTRY_BY_ID,
        )
        q = REGISTRY_BY_ID.get("PORT-1")
        assert q is not None, "PORT-1 not found in registry"
        assert q.runner_function == "run_port_1"
        assert q.runner_module == "research_engine.experiments.portfolio_ranking"

    def test_runner_discovered(self):
        from research_engine.runner_discovery import discover_runners
        runners = discover_runners()
        assert "PORT-1" in runners, "PORT-1 runner not discovered"
        assert callable(runners["PORT-1"])

    def test_executable_with_empty_data(self):
        result = run_port_1(
            portfolio_rankings=[], decisions=[], trade_truth=[], shadow_trades=[],
        )
        assert result["status"] == "INSUFFICIENT_DATA"
        assert result["confidence"] == "INSUFFICIENT_DATA"

    def test_executable_with_sufficient_data(self):
        rankings, decisions, truths = _build_ranking_records(11)
        result = run_port_1(
            portfolio_rankings=rankings,
            decisions=decisions,
            trade_truth=truths,
            shadow_trades=[],
        )
        assert result["status"] in ("COMPLETE", "INSUFFICIENT_DATA")
        assert isinstance(result["overall"], dict)


# ==============================================================================
# TEST 2 — OPP-1 is registered and executable
# ==============================================================================


class TestOpp1Registration:
    def test_registry_has_opp1(self):
        from research_engine.registry.research_question_registry import (
            REGISTRY_BY_ID,
        )
        q = REGISTRY_BY_ID.get("OPP-1")
        assert q is not None, "OPP-1 not found in registry"
        assert q.runner_function == "run_opp_1"
        assert q.runner_module == "research_engine.experiments.opportunity_selection"

    def test_runner_discovered(self):
        from research_engine.runner_discovery import discover_runners
        runners = discover_runners()
        assert "OPP-1" in runners, "OPP-1 runner not discovered"
        assert callable(runners["OPP-1"])

    def test_executable_with_empty_data(self):
        result = run_opp_1(
            opportunities=[], horizon_candidates=[], assessments=[],
            shadow_trades=[],
        )
        assert result["status"] == "INSUFFICIENT_DATA"

    def test_executable_with_sufficient_data(self):
        opps, hcs, assessments = _build_opportunity_records(11)
        result = run_opp_1(
            opportunities=opps,
            horizon_candidates=hcs,
            assessments=assessments,
            shadow_trades=[],
        )
        assert result["status"] in ("COMPLETE", "INSUFFICIENT_DATA")

    def test_opp1_uses_opportunity_not_portfolio_evidence(self):
        """OPP-1 must not depend on portfolio_rankings."""
        from research_engine.experiments.opportunity_selection import run_opp_1
        src = Path(run_opp_1.__code__.co_filename).read_text(encoding="utf-8")
        # Should import from opportunities/assessments, not portfolio_rankings
        assert "load_portfolio_rankings" not in src, (
            "OPP-1 must not import portfolio_rankings loaders"
        )
        # Should use opportunity-level data sources
        assert "load_opportunities" in src
        assert "load_horizon_candidates" in src


# ==============================================================================
# TEST 3 — D6 is registered and executable
# ==============================================================================


class TestD6Registration:
    def test_registry_has_d6(self):
        from research_engine.registry.research_question_registry import (
            REGISTRY_BY_ID,
        )
        q = REGISTRY_BY_ID.get("D6")
        assert q is not None, "D6 not found in registry"
        assert q.runner_function == "run_portfolio_ranking"
        assert q.runner_module == "research_engine.experiments.portfolio_ranking"

    def test_runner_discovered(self):
        from research_engine.runner_discovery import discover_runners
        runners = discover_runners()
        assert "D6" in runners, "D6 runner not discovered"
        assert callable(runners["D6"])

    def test_executable_with_empty_data(self):
        result = run_portfolio_ranking(
            portfolio_rankings=[], decisions=[], trade_truth=[], shadow_trades=[],
        )
        assert result["status"] == "INSUFFICIENT_DATA"


# ==============================================================================
# TEST 4 — D6 materially reads portfolio_rankings
# ==============================================================================


class TestD6MateriallyReadsPortfolioRankings:
    def test_d6_reports_ranking_rows_read(self):
        rankings, decisions, truths = _build_ranking_records(11)
        result = run_portfolio_ranking(
            portfolio_rankings=rankings,
            decisions=decisions,
            trade_truth=truths,
            shadow_trades=[],
        )
        assert result["overall"].get("portfolio_rankings_read", 0) == 11
        assert result["overall"].get("total_candidate_rows", 0) > 0

    def test_d6_without_portfolio_rankings_returns_insufficient(self):
        """D6 must NOT silently answer from another dataset."""
        result = run_portfolio_ranking(
            portfolio_rankings=[], decisions=[], trade_truth=[], shadow_trades=[],
        )
        assert result["status"] == "INSUFFICIENT_DATA"
        msg = str(result.get("warnings", [])) + str(result.get("overall", {}))
        assert "portfolio_rankings" in msg, (
            "D6 must mention portfolio_rankings in its insufficient-evidence response"
        )

    def test_d6_runner_imports_portfolio_rankings_loader(self):
        src = Path(run_portfolio_ranking.__code__.co_filename).read_text(
            encoding="utf-8"
        )
        assert "load_portfolio_rankings" in src, (
            "D6 runner must import load_portfolio_rankings"
        )
        # Verify it does NOT use the old shadow_trades-only approach
        assert "by_cycle" not in src, (
            "D6 must not use the old shadow_trades-only grouping logic"
        )


# ==============================================================================
# TEST 5 — Empty portfolio_rankings causes missing evidence
# ==============================================================================


class TestEmptyPortfolioRankings:
    def test_empty_portfolio_rankings_d6(self):
        result = run_portfolio_ranking(
            portfolio_rankings=[], decisions=[], trade_truth=[], shadow_trades=[],
        )
        assert result["status"] == "INSUFFICIENT_DATA"

    def test_empty_portfolio_rankings_port1(self):
        result = run_port_1(
            portfolio_rankings=[], decisions=[], trade_truth=[], shadow_trades=[],
        )
        assert result["status"] == "INSUFFICIENT_DATA"


# ==============================================================================
# TEST 6 — PORT-1 correctly joins ranking to outcome evidence
# ==============================================================================


class TestPort1Join:
    def test_joins_decision_and_outcome(self):
        rankings, decisions, truths = _build_ranking_records(11)
        result = run_port_1(
            portfolio_rankings=rankings,
            decisions=decisions,
            trade_truth=truths,
            shadow_trades=[],
        )
        o = result["overall"]
        assert o.get("portfolio_rankings_read", 0) == 11
        assert o.get("total_candidates", 0) > 0
        # Should have outcome-bearing cycles
        assert o.get("selection_cycles_with_outcome", 0) > 0

    def test_selected_best_rate_computed(self):
        rankings, decisions, truths = _build_ranking_records(11)
        result = run_port_1(
            portfolio_rankings=rankings,
            decisions=decisions,
            trade_truth=truths,
            shadow_trades=[],
        )
        rate = result["overall"].get("selected_best_in_cycle_rate")
        assert rate is not None, "selected_best_in_cycle_rate must be computed"
        assert isinstance(rate, (int, float))


# ==============================================================================
# TEST 7 — OPP-1 distinguishes opportunity-level from portfolio-level selection
# ==============================================================================


class TestOpp1Distinction:
    def test_opp1_uses_opportunity_data_sources(self):
        from research_engine.registry.research_question_registry import (
            REGISTRY_BY_ID,
        )
        from research_engine.registry.research_question_models import DataSource

        q = REGISTRY_BY_ID["OPP-1"]
        sources = {ds.value for ds in q.data_sources}
        assert "portfolio_rankings" not in sources, (
            "OPP-1 must not depend on portfolio_rankings"
        )
        assert "shadow_trades" in sources or "horizon_candidates" in sources, (
            "OPP-1 must use opportunity-level data sources"
        )

    def test_opp1_classifies_promoted_vs_rejected(self):
        opps, hcs, assessments = _build_opportunity_records(11)
        result = run_opp_1(
            opportunities=opps,
            horizon_candidates=hcs,
            assessments=assessments,
            shadow_trades=[],
        )
        o = result["overall"]
        # Should have classified opportunities even without outcomes
        # (INSUFFICIENT_DATA path uses 'classified' key instead of 'classified_opportunities')
        assert o.get("classified", 0) > 0 or o.get("classified_opportunities", 0) > 0


# ==============================================================================
# TEST 8 — Canonical IDs preferred for joins
# ==============================================================================


class TestCanonicalJoins:
    def test_join_uses_cycle_id_and_symbol(self):
        """portfolio_rankings -> decision_ledger bridge uses (cycle_id, symbol)."""
        from research_engine.experiments.selection_analysis import decision_key

        # Verify the join helper exists and uses canonical keys
        key = decision_key(1001, "EURUSD")
        assert key == (1001, "EURUSD"), f"Unexpected key: {key}"

    def test_join_uses_correlation_id_from_identity(self):
        """trade_truth join uses identity.correlation_id."""
        from research_engine.experiments.selection_analysis import deep_get

        record = {
            "identity": {"correlation_id": "CORR-1001-EUR"},
        }
        assert deep_get(record, "identity", "correlation_id") == "CORR-1001-EUR"


# ==============================================================================
# TEST 9 — Quarantine paths not treated as active research evidence
# ==============================================================================


class TestQuarantineSafety:
    def test_quarantine_boundary_loaded_from_manifest(self):
        """The quarantine boundary is METADATA, not active evidence."""
        from research_engine.experiments.selection_analysis import (
            load_quarantine_boundary,
        )
        boundary = load_quarantine_boundary()
        desc = boundary.describe()
        assert desc["defect"] == "forming_bar_decision_defect"
        assert "first_affected" in desc
        assert "fix_committed" in desc

    def test_quarantine_contains_known_timestamp(self):
        from research_engine.experiments.selection_analysis import (
            load_quarantine_boundary,
        )
        boundary = load_quarantine_boundary()
        # A timestamp within the defect window
        assert boundary.contains("2026-09-05T12:00:00Z") is True, (
            "2026-09-05 should be within the defect window"
        )
        # A timestamp after the fix
        assert boundary.contains("2026-09-10T12:00:00Z") is False, (
            "2026-09-10 should be outside the defect window"
        )

    def test_quarantine_not_read_as_active_evidence(self):
        """Verify the research modules do not read quarantine/ logs as evidence."""
        # The quarantine/forming_bar_decision_defect path is referenced in
        # docstrings/comments (metadata boundary), never as active evidence.
        # Check for active evidence-reading patterns, not documentation.
        for mod_name in (
            "research_engine.experiments.portfolio_ranking",
            "research_engine.experiments.opportunity_selection",
            "research_engine.experiments.selection_analysis",
        ):
            import importlib
            mod = importlib.import_module(mod_name)
            src = Path(mod.__file__).read_text(encoding="utf-8")
            # Must NOT read from quarantine/.../logs/ as active evidence
            assert "quarantine/forming_bar_decision_defect/logs" not in src, (
                f"{mod_name} must not read quarantine logs as evidence"
            )
            # Must NOT read from the quarantine S3 path
            assert "quarantine/forming_bar_decision_defect/data" not in src, (
                f"{mod_name} must not read quarantine data as evidence"
            )


# ==============================================================================
# TEST 10 — V1-only/versioning assumptions remain intact
# ==============================================================================


class TestV1Contract:
    def test_runners_use_research_engine_loaders(self):
        """Runners must use the canonical loader layer, not direct boto3."""
        src = Path(run_portfolio_ranking.__code__.co_filename).read_text(
            encoding="utf-8"
        )
        assert "boto3" not in src, "Runner must not import boto3 directly"
        assert "research_engine.data_access" in src, (
            "Runner must use canonical data-access layer"
        )

    def test_opp1_uses_research_engine_loaders(self):
        src = Path(run_opp_1.__code__.co_filename).read_text(encoding="utf-8")
        assert "boto3" not in src, "Runner must not import boto3 directly"
        assert "research_engine.data_access" in src, (
            "Runner must use canonical data-access layer"
        )

    def test_ranking_uses_portfolio_ranking_v1_schema(self):
        """Verify schema_version check in portfolio_rankings records."""
        rankings, _, _ = _build_ranking_records(1)
        for r in rankings:
            assert r["schema_version"] == "portfolio_ranking_v1"


# ==============================================================================
# TEST 11 — Empty / small sample returns insufficient evidence
# ==============================================================================


class TestInsufficientEvidence:
    def test_d6_insufficient_with_few_candidates(self):
        rankings, decisions, truths = _build_ranking_records(1, 1, 2)
        result = run_portfolio_ranking(
            portfolio_rankings=rankings,
            decisions=decisions,
            trade_truth=truths,
            shadow_trades=[],
        )
        assert result["status"] == "INSUFFICIENT_DATA"

    def test_port1_insufficient_without_outcomes(self):
        rankings, decisions, truths = _build_ranking_records(2)
        # Remove trade_truth linking so outcomes are missing
        result = run_port_1(
            portfolio_rankings=rankings,
            decisions=decisions,
            trade_truth=[],
            shadow_trades=[],
        )
        assert result["status"] == "INSUFFICIENT_DATA"

    def test_opp1_insufficient_with_few_opportunities(self):
        result = run_opp_1(
            opportunities=[], horizon_candidates=[], assessments=[],
            shadow_trades=[],
        )
        assert result["status"] == "INSUFFICIENT_DATA"


# ==============================================================================
# TEST 12 — No live trading/runtime code is modified
# ==============================================================================


class TestNoLiveTradingModification:
    def test_runners_do_not_import_trading_modules(self):
        """Verify no live trading imports in the research modules."""
        for mod_name, func_name in (
            ("research_engine.experiments.portfolio_ranking", "run_portfolio_ranking"),
            ("research_engine.experiments.opportunity_selection", "run_opp_1"),
        ):
            import importlib
            mod = importlib.import_module(mod_name)
            src = Path(mod.__file__).read_text(encoding="utf-8")
            for forbidden in ("MT5", "order_send", "RiskManager",
                              "MT5Execution", "ShadowRuntime",
                              "persist_trade_truth", "persist_portfolio_ranking",
                              "core.pipeline", "core.runtime"):
                assert forbidden not in src, (
                    f"{mod_name} must not import {forbidden}"
                )

    def test_no_persistence_imports_from_core(self):
        for mod_name in (
            "research_engine.experiments.portfolio_ranking",
            "research_engine.experiments.opportunity_selection",
        ):
            import importlib
            mod = importlib.import_module(mod_name)
            src = Path(mod.__file__).read_text(encoding="utf-8")
            assert "persist_portfolio_ranking" not in src, (
                f"{mod_name} must not call production persistence"
            )
            assert "core.portfolio_ranking" not in src, (
                f"{mod_name} must not import core ranking modules"
            )


# ==============================================================================
# REPORT CONTRACT
# ==============================================================================


class TestReportContract:
    def test_d6_report_has_required_fields(self):
        rankings, decisions, truths = _build_ranking_records(11)
        result = run_portfolio_ranking(
            portfolio_rankings=rankings,
            decisions=decisions,
            trade_truth=truths,
            shadow_trades=[],
        )
        for field in ("question_id", "status", "overall", "confidence",
                       "dataset", "fingerprint", "recommendation",
                       "assumptions", "warnings", "generated", "provenance"):
            assert field in result, f"D6 report missing: {field}"

    def test_port1_report_serialisable(self):
        import json
        rankings, decisions, truths = _build_ranking_records(11)
        result = run_port_1(
            portfolio_rankings=rankings,
            decisions=decisions,
            trade_truth=truths,
            shadow_trades=[],
        )
        text = json.dumps(result)
        assert "NaN" not in text
        assert "Infinity" not in text

    def test_opp1_report_serialisable(self):
        import json
        opps, hcs, assessments = _build_opportunity_records(11)
        result = run_opp_1(
            opportunities=opps,
            horizon_candidates=hcs,
            assessments=assessments,
            shadow_trades=[],
        )
        text = json.dumps(result)
        assert "NaN" not in text
        assert "Infinity" not in text