"""
Tests for Research Command Centre (Phase 2).

Validates:
    - Report generation from each data source
    - Dashboard integration
    - Knowledge map / registry integration
    - EV analysis integration
    - Confirmed findings from Q reports
    - Rejected hypotheses parsing
    - Architecture status extraction
    - Blocker detection
    - Recommendation generation
    - Promotion protection (never promote without validation)
    - Contamination detection
    - CLI output rendering
    - JSON serialization

Does NOT test trading logic — command centre is reporting only.
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_project_root = str(Path(__file__).resolve().parent.parent.parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from research_engine.command_center.command_models import (
    ArchitectureAuthority,
    ArchitectureStatus,
    Blocker,
    ConfirmedFindings,
    CoverageField,
    DataHealth,
    DatasetFingerprint,
    EVBreakdownEntry,
    PatternFinding,
    QuestionEntry,
    QuestionProvenance,
    Recommendation,
    RejectedHypothesis,
    ResearchCommandReport,
    ResearchReadiness,
    ResearchTraceability,
    SystemEdge,
    SystemState,
)
from research_engine.command_center.research_command_center import (
    _build_architecture_status,
    _build_blockers,
    _build_confirmed_findings,
    _build_data_health,
    _build_recommendation,
    _build_rejected_hypotheses,
    _build_research_readiness,
    _build_system_edge,
    _build_system_state,
    _build_traceability,
    _deep_get,
    _make_cov,
    generate_command_report,
    print_report,
)


# ═══════════════════════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════════════════════


def _shadow_record(
    strategy="CONTINUATION",
    r_multiple=0.5,
    entity_id="EURUSD_1700000000",
    horizon="SCALP",
    regime="TRENDING",
    h1_bias="BULLISH",
    phase="IMPULSE",
    pattern="BOS_PULLBACK",
) -> dict:
    """Create a shadow trade record for testing."""
    return {
        "schema_version": "shadow_trades_v2",
        "identity": {
            "trade_id": "test_001",
            "correlation_id": "COR-123",
            "symbol": "EURUSD",
            "strategy_id": strategy,
            "entity_id": entity_id,
        },
        "decision_snapshot": {
            "pattern": pattern,
            "score": 0.55,
            "regime": regime,
            "h4_regime": regime,
            "h1_bias": h1_bias,
            "trade_horizon": horizon,
            "market_phase": phase,
        },
        "simulated_outcome": {
            "pnl_r_multiple": r_multiple,
            "exit_reason": "take_profit" if r_multiple > 0 else "stop_loss",
            "bars_held": 12,
        },
    }


def _make_records(n, **kwargs) -> list[dict]:
    """Create N shadow records."""
    return [_shadow_record(entity_id=f"EURUSD_{1700000000+i}", **kwargs) for i in range(n)]


def _sample_dashboard() -> dict:
    return {
        "total_questions": 25,
        "questions": {
            "Q1": {"question": "Which components predict R?", "priority": "P0", "status": "COMPLETE", "recommendation": "WEIGHT_ADJUSTMENT"},
            "Q16": {"question": "Shadow vs live correlation?", "priority": "P0", "status": "BLOCKED", "blocker": "Needs live trades", "recommendation": "BLOCKED"},
            "Q19": {"question": "System expected value?", "priority": "P0", "status": "COMPLETE", "recommendation": "POSITIVE_EDGE"},
        },
        "summary": {"complete": 23, "ready": 0, "blocked": 1, "waiting_data": 1},
    }


def _sample_knowledge() -> dict:
    return {
        "confirmed_facts": [
            "Q19: System has positive expected value (+0.55R)",
            "Q4: Score calibration is needed (monotonic but miscalibrated by 15pp)",
            "ARCH: H4 owns regime classification (100% authority post-migration)",
            "ARCH: H1 owns structural direction + BOS",
            "ARCH: M15 owns setup quality (market_quality + chop_clarity)",
            "ARCH: M5 owns execution timing only (pattern, confirmation, bias FSM)",
            "ARCH: Score is monotonically related to win probability",
        ],
        "rejected_hypotheses": [
            "REJECTED: strategy_confidence is a valid probability input (always 0 in 98% of decisions)",
            "REJECTED: M5 can determine market regime (collapsed to 99% TRANSITIONAL)",
        ],
        "next_experiments": ["NEXT: Apply empirical calibration curve"],
    }


def _sample_q5_report() -> dict:
    return {
        "question_id": "Q5",
        "metrics": {
            "pattern_performance": {
                "TWEEZER_TOP": {"n": 254, "wr": 0.5433, "avg_r": 0.1401},
                "THREE_BLACK_CROWS": {"n": 205, "wr": 0.0195, "avg_r": -0.9451},
                "MORNING_STAR": {"n": 63, "wr": 0.3968, "avg_r": 0.0249},
                "TINY_PATTERN": {"n": 5, "wr": 0.6, "avg_r": 0.2},  # Below threshold
            },
        },
    }


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: REPORT GENERATION
# ═══════════════════════════════════════════════════════════════════════════════


class TestReportGeneration:
    @patch("research_engine.command_center.research_command_center._load_jsonl_sample")
    @patch("research_engine.command_center.research_command_center._count_jsonl")
    @patch("research_engine.command_center.research_command_center._load_all_reports")
    @patch("research_engine.command_center.research_command_center._load_json")
    def test_empty_state(self, mock_json, mock_reports, mock_count, mock_sample):
        mock_json.return_value = None
        mock_reports.return_value = {}
        mock_count.return_value = 0
        mock_sample.return_value = []

        report = generate_command_report()
        assert isinstance(report, ResearchCommandReport)
        assert report.system_state.infrastructure == "NOT_READY"
        assert report.data_health.record_count == 0

    @patch("research_engine.command_center.research_command_center._load_jsonl_sample")
    @patch("research_engine.command_center.research_command_center._count_jsonl")
    @patch("research_engine.command_center.research_command_center._load_all_reports")
    @patch("research_engine.command_center.research_command_center._load_json")
    def test_with_data(self, mock_json, mock_reports, mock_count, mock_sample):
        mock_json.return_value = _sample_dashboard()
        mock_reports.return_value = {"Q5": _sample_q5_report()}
        mock_count.return_value = 200
        mock_sample.return_value = _make_records(200)

        report = generate_command_report()
        assert report.system_state.infrastructure == "READY"
        assert report.data_health.record_count == 400  # 200 per dir x 2

    def test_to_dict_serializable(self):
        report = _make_full_report()
        d = report.to_dict()
        serialized = json.dumps(d, default=str)
        assert isinstance(serialized, str)
        parsed = json.loads(serialized)
        assert "system_state" in parsed
        assert "system_edge" in parsed
        assert "architecture_status" in parsed


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: DASHBOARD INTEGRATION
# ═══════════════════════════════════════════════════════════════════════════════


class TestDashboardIntegration:
    def test_research_readiness_from_registry(self):
        """Registry is source of truth for question count."""
        from research_engine.registry.research_question_registry import REGISTRY
        rr = _build_research_readiness(_sample_dashboard())
        assert rr.total_questions == len(REGISTRY)
        assert rr.complete >= 3  # At least 3 researched via legacy mapping

    def test_active_questions_from_registry(self):
        """Active questions come from registry, not just dashboard."""
        from research_engine.registry.research_question_registry import REGISTRY
        rr = _build_research_readiness(_sample_dashboard())
        assert rr.total_questions == len(REGISTRY)

    def test_none_dashboard_still_has_registry(self):
        """No dashboard still produces all registered questions."""
        from research_engine.registry.research_question_registry import REGISTRY
        rr = _build_research_readiness(None)
        assert rr.total_questions == len(REGISTRY)
        assert rr.complete == 0  # No researched without dashboard


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: REGISTRY / KNOWLEDGE INTEGRATION
# ═══════════════════════════════════════════════════════════════════════════════


class TestKnowledgeIntegration:
    def test_confirmed_findings_from_knowledge(self):
        cf = _build_confirmed_findings(_sample_knowledge(), {"Q5": _sample_q5_report()})
        # Non-ARCH facts become findings
        assert any("positive expected value" in f for f in cf.findings)
        assert any("calibration" in f for f in cf.findings)

    def test_pattern_findings_from_q5(self):
        cf = _build_confirmed_findings(None, {"Q5": _sample_q5_report()})
        # Should include patterns with n >= 10
        names = [p.name for p in cf.pattern_findings]
        assert "TWEEZER_TOP" in names
        assert "THREE_BLACK_CROWS" in names
        # TINY_PATTERN (n=5) excluded
        assert "TINY_PATTERN" not in names

    def test_pattern_findings_sorted_by_ev(self):
        cf = _build_confirmed_findings(None, {"Q5": _sample_q5_report()})
        evs = [p.ev for p in cf.pattern_findings]
        assert evs == sorted(evs, reverse=True)

    def test_rejected_hypotheses_parsed(self):
        rh = _build_rejected_hypotheses(_sample_knowledge())
        assert len(rh) == 2
        assert "strategy_confidence" in rh[0].hypothesis
        assert "always 0 in 98%" in rh[0].reason

    def test_rejected_hypotheses_empty(self):
        assert _build_rejected_hypotheses(None) == []


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: EV ANALYSIS INTEGRATION
# ═══════════════════════════════════════════════════════════════════════════════


class TestEVIntegration:
    def test_ev_from_q19_report(self):
        q19 = {
            "question_id": "Q19",
            "timestamp": "2026-07-21T22:40:40Z",
            "dataset": {"sample_size": 501},
            "metrics": {"expected_value": 0.5407, "confidence": "HIGH", "edge_classification": "STRONG_EDGE"},
            "data": {
                "expected_value": 0.5407,
                "total_trades": 501,
                "confidence": "HIGH",
                "edge_classification": "STRONG_EDGE",
                "win_rate": 0.45,
                "profit_factor": 1.8,
                "ev_trend": "stable",
                "pattern_breakdown": [
                    {"pattern": "TWEEZER_TOP", "expected_value": 0.851, "trades": 202, "win_rate": 0.54},
                ],
            },
        }
        edge = _build_system_edge([], {"Q19": q19})
        assert edge.current_ev == 0.5407
        assert edge.confidence == "HIGH"
        assert edge.eligible_trades == 501
        assert len(edge.best_patterns) == 1
        assert edge.best_patterns[0].name == "TWEEZER_TOP"

    def test_ev_fallback_from_shadow_records(self):
        records = _make_records(50, r_multiple=0.3)
        edge = _build_system_edge(records, {})
        assert edge.current_ev is not None
        assert edge.current_ev == pytest.approx(0.3, abs=0.01)
        assert edge.eligible_trades == 50
        assert edge.confidence == "MEDIUM"

    def test_ev_empty_data(self):
        edge = _build_system_edge([], {})
        assert edge.confidence == "INSUFFICIENT_DATA"


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: ARCHITECTURE STATUS
# ═══════════════════════════════════════════════════════════════════════════════


class TestArchitectureStatus:
    def test_authorities_extracted(self):
        arch = _build_architecture_status(_sample_knowledge())
        tfs = [a.timeframe for a in arch.authorities]
        assert "H4" in tfs
        assert "H1" in tfs
        assert "M15" in tfs
        assert "M5" in tfs
        # All confirmed
        assert all(a.confirmed for a in arch.authorities)

    def test_additional_facts_captured(self):
        arch = _build_architecture_status(_sample_knowledge())
        # "Score is monotonically related..." doesn't match H4/H1/M15/M5
        assert len(arch.additional_facts) >= 1

    def test_none_knowledge(self):
        arch = _build_architecture_status(None)
        assert arch.authorities == []


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: BLOCKERS
# ═══════════════════════════════════════════════════════════════════════════════


class TestBlockers:
    def test_lineage_blocker(self):
        dh = DataHealth(
            record_count=100, source="shadow_trades",
            outcome_coverage=CoverageField("outcome", 0.95, "OK"),
            lineage_coverage=CoverageField("entity_id", 0.3, "LOW"),
            context_fields=[CoverageField("H4 regime", 0.1, "LOW")],
            contamination_count=0, dataset_verdict="NOT READY",
        )
        blockers = _build_blockers(dh, None)
        areas = [b.area for b in blockers]
        assert "lineage" in areas
        assert "context:H4 regime" in areas

    def test_contamination_blocker(self):
        dh = DataHealth(
            record_count=100, source="shadow_trades",
            outcome_coverage=CoverageField("outcome", 0.95, "OK"),
            lineage_coverage=CoverageField("entity_id", 0.9, "OK"),
            context_fields=[],
            contamination_count=15, dataset_verdict="NOT READY",
        )
        blockers = _build_blockers(dh, None)
        assert any("contamination" in b.area for b in blockers)

    def test_blocked_question_blocker(self):
        blockers = _build_blockers(
            DataHealth(100, "s", CoverageField("o", 0.9, "OK"), CoverageField("e", 0.9, "OK"), [], 0, "OK"),
            _sample_dashboard(),
        )
        assert any("Q16" in b.area for b in blockers)

    def test_no_blockers_clean(self):
        dh = DataHealth(
            record_count=200, source="shadow_trades",
            outcome_coverage=CoverageField("outcome", 0.99, "OK"),
            lineage_coverage=CoverageField("entity_id", 0.95, "OK"),
            context_fields=[CoverageField("H4 regime", 0.9, "OK")],
            contamination_count=0, dataset_verdict="READY FOR FULL RESEARCH",
        )
        blockers = _build_blockers(dh, {"questions": {}})
        assert len(blockers) == 0


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: PROMOTION PROTECTION
# ═══════════════════════════════════════════════════════════════════════════════


class TestPromotionProtection:
    def test_insufficient_data_blocks_promotion(self):
        dh = DataHealth(
            record_count=30, source="shadow_trades",
            outcome_coverage=CoverageField("outcome", 0.9, "OK"),
            lineage_coverage=CoverageField("entity_id", 0.4, "LOW"),
            context_fields=[], contamination_count=0, dataset_verdict="NOT READY",
        )
        state = _build_system_state(dh, trace_count=100, shadow_count=30)
        assert state.promotion_decisions == "INSUFFICIENT_DATA"

    def test_contamination_blocks_promotion(self):
        dh = DataHealth(
            record_count=200, source="shadow_trades",
            outcome_coverage=CoverageField("outcome", 0.99, "OK"),
            lineage_coverage=CoverageField("entity_id", 0.95, "OK"),
            context_fields=[], contamination_count=5, dataset_verdict="PARTIAL",
        )
        state = _build_system_state(dh, trace_count=500, shadow_count=200)
        assert state.promotion_decisions == "INSUFFICIENT_DATA"

    def test_promotion_ready_when_clean(self):
        dh = DataHealth(
            record_count=200, source="shadow_trades",
            outcome_coverage=CoverageField("outcome", 0.99, "OK"),
            lineage_coverage=CoverageField("entity_id", 0.95, "OK"),
            context_fields=[], contamination_count=0, dataset_verdict="READY",
        )
        state = _build_system_state(dh, trace_count=500, shadow_count=200)
        assert state.promotion_decisions == "READY"


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: CONTAMINATION DETECTION
# ═══════════════════════════════════════════════════════════════════════════════


class TestContamination:
    def test_contaminated_records_counted(self):
        records = [
            _shadow_record(strategy="NONE_SCALP"),
            _shadow_record(strategy="CONTINUATION"),
            _shadow_record(strategy="REVERSAL_INTRADAY"),
        ]
        dh = _build_data_health(records, 3)
        assert dh.contamination_count == 2

    def test_clean_records_zero(self):
        records = _make_records(5, strategy="CONTINUATION")
        dh = _build_data_health(records, 5)
        assert dh.contamination_count == 0


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: RECOMMENDATION
# ═══════════════════════════════════════════════════════════════════════════════


class TestRecommendation:
    def test_empty_data_recommends_start(self):
        dh = DataHealth(0, "shadow_trades", CoverageField("o", 0, "MISSING"),
                        CoverageField("e", 0, "MISSING"), [], 0, "NO DATA")
        rec = _build_recommendation(dh, [])
        assert "INITIAL SETUP" in rec.current_phase
        assert "Start" in rec.required_action

    def test_low_lineage_recommends_collection(self):
        dh = DataHealth(100, "shadow_trades", CoverageField("o", 0.9, "OK"),
                        CoverageField("entity_id", 0.4, "LOW"),
                        [CoverageField("H4 regime", 0.8, "OK")], 0, "NOT READY")
        rec = _build_recommendation(dh, [])
        assert "DATA COLLECTION" in rec.current_phase
        assert "entity_id" in rec.required_action.lower() or "Lineage" in rec.reason

    def test_ready_recommends_research(self):
        dh = DataHealth(200, "shadow_trades", CoverageField("o", 0.95, "OK"),
                        CoverageField("entity_id", 0.92, "OK"),
                        [CoverageField("H4 regime", 0.85, "OK"),
                         CoverageField("H1 bias", 0.82, "OK"),
                         CoverageField("market phase", 0.81, "OK")],
                        0, "READY FOR FULL RESEARCH")
        rec = _build_recommendation(dh, [])
        assert "RESEARCH" in rec.current_phase
        assert "run" in rec.required_action.lower() or "Run" in rec.required_action


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: CLI OUTPUT
# ═══════════════════════════════════════════════════════════════════════════════


class TestCLIOutput:
    def test_print_no_crash(self, capsys):
        report = _make_full_report()
        print_report(report)
        captured = capsys.readouterr()
        assert "RESEARCH COMMAND CENTRE" in captured.out
        assert "SYSTEM STATE" in captured.out
        assert "DATA HEALTH" in captured.out
        assert "CONFIRMED FINDINGS" in captured.out
        assert "SYSTEM EDGE" in captured.out
        assert "ARCHITECTURE" in captured.out
        assert "BLOCKERS" in captured.out
        assert "RECOMMENDED NEXT ACTION" in captured.out

    def test_all_12_sections_present(self, capsys):
        report = _make_full_report()
        print_report(report)
        captured = capsys.readouterr()
        for i in range(1, 13):
            assert f"  {i}." in captured.out

    def test_json_output_valid(self):
        report = _make_full_report()
        output = json.dumps(report.to_dict(), default=str)
        parsed = json.loads(output)
        assert "system_state" in parsed
        assert "data_health" in parsed
        assert "system_edge" in parsed


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: HELPERS
# ═══════════════════════════════════════════════════════════════════════════════


class TestHelpers:
    def test_deep_get_nested(self):
        assert _deep_get({"a": {"b": {"c": 42}}}, "a", "b", "c") == 42

    def test_deep_get_missing(self):
        assert _deep_get({"a": 1}, "a", "b") is None

    def test_deep_get_none(self):
        assert _deep_get({"a": None}, "a", "b") is None

    def test_make_cov_ok(self):
        c = _make_cov("test", 90, 100)
        assert c.status == "OK"
        assert c.pct == pytest.approx(0.9)

    def test_make_cov_low(self):
        c = _make_cov("test", 30, 100)
        assert c.status == "LOW"

    def test_make_cov_missing(self):
        c = _make_cov("test", 0, 100)
        assert c.status == "MISSING"

    def test_make_cov_zero_total(self):
        c = _make_cov("test", 0, 0)
        assert c.pct == 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# UTILITIES
# ═══════════════════════════════════════════════════════════════════════════════


def _make_full_report() -> ResearchCommandReport:
    """Create a realistic report for rendering tests."""
    return ResearchCommandReport(
        generated_at="2026-07-27T00:00:00Z",
        system_state=SystemState("READY", "COLLECTING", "WAITING_DATA", "INSUFFICIENT_DATA"),
        data_health=DataHealth(
            record_count=794, source="shadow_trades",
            outcome_coverage=CoverageField("outcome", 1.0, "OK"),
            lineage_coverage=CoverageField("entity_id", 0.457, "LOW"),
            context_fields=[
                CoverageField("H4 regime", 0.191, "LOW"),
                CoverageField("H1 bias", 0.092, "LOW"),
                CoverageField("market phase", 0.030, "LOW"),
                CoverageField("strategy", 0.13, "LOW"),
                CoverageField("trade_horizon", 0.01, "LOW"),
            ],
            contamination_count=281,
            dataset_verdict="NOT READY FOR FULL RESEARCH",
        ),
        research_readiness=ResearchReadiness(25, 23, 0, 1, 1),
        active_questions=[
            QuestionEntry("Q1", "Component reward", "P0", "COMPLETE", "WEIGHT_ADJUSTMENT"),
            QuestionEntry("Q16", "Shadow validation", "P0", "BLOCKED", "BLOCKED", "Needs live trades"),
            QuestionEntry("Q19", "Expected value", "P0", "COMPLETE", "POSITIVE_EDGE"),
        ],
        confirmed_findings=ConfirmedFindings(
            findings=["Q19: System has positive expected value (+0.55R)"],
            pattern_findings=[
                PatternFinding("TWEEZER_TOP", 0.140, 254, "HIGH", 0.543),
                PatternFinding("THREE_BLACK_CROWS", -0.945, 205, "HIGH", 0.019),
            ],
        ),
        rejected_hypotheses=[
            RejectedHypothesis("strategy_confidence is a valid probability input", "always 0 in 98% of decisions"),
            RejectedHypothesis("M5 can determine market regime", "collapsed to 99% TRANSITIONAL"),
        ],
        system_edge=SystemEdge(
            current_ev=0.5407, dataset_name="q19_ev_2026-07-21",
            eligible_trades=501, confidence="HIGH",
            edge_classification="STRONG_EDGE", win_rate=0.45,
            profit_factor=1.8, ev_trend="stable",
            best_patterns=[EVBreakdownEntry("TWEEZER_TOP", 0.851, 202, 0.54)],
            worst_patterns=[EVBreakdownEntry("THREE_BLACK_CROWS", -0.945, 205, 0.02)],
            warnings=["Strategy contamination exists in historical data"],
        ),
        architecture_status=ArchitectureStatus(
            authorities=[
                ArchitectureAuthority("H4", "Regime classification", True),
                ArchitectureAuthority("H1", "Structural direction + BOS", True),
                ArchitectureAuthority("M15", "Setup quality", True),
                ArchitectureAuthority("M5", "Execution timing", True),
            ],
            additional_facts=["Score is monotonically related to win probability"],
        ),
        blockers=[
            Blocker("lineage", "entity_id at 45.7% (need 80%)", "Cannot perform full lifecycle joins"),
            Blocker("contamination", "281 records with combined strategy_horizon", "Strategy analysis contaminated"),
        ],
        recommendation=Recommendation(
            current_phase="DATA COLLECTION",
            reason="Lineage incomplete: 45.7%",
            missing_items=["entity_id accumulation", "H4 regime in outcomes", "strategy separation"],
            required_action="Collect live shadow trades.",
            do_not="Do NOT modify strategy logic yet.",
        ),
        traceability=ResearchTraceability(
            questions=[
                QuestionProvenance(
                    question_id="Q19", question_title="Expected value",
                    experiment_module="experiments.expected_value",
                    expected_output_location="analysis/reports/q19_expected_value.json",
                    last_run_timestamp="2026-07-21T22:40:40Z",
                    status="COMPLETE", result_available=True,
                    displayed_in_command_center=True,
                    dataset_fingerprint=DatasetFingerprint("shadow_trades_2026-07-21", 501, 293, "HIGH"),
                ),
                QuestionProvenance(
                    question_id="Q5", question_title="Pattern degradation",
                    experiment_module="experiments.research_runner",
                    expected_output_location="analysis/reports/q5_pattern_degradation.json",
                    last_run_timestamp="2026-07-21T22:40:43Z",
                    status="COMPLETE", result_available=True,
                    displayed_in_command_center=True,
                    dataset_fingerprint=DatasetFingerprint("shadow_trades_2026-07-21", 1220, 0, "HIGH"),
                ),
                QuestionProvenance(
                    question_id="Q16", question_title="Shadow validation",
                    experiment_module="experiments.shadow_validation",
                    expected_output_location="analysis/reports/q16_shadow_validation.json",
                    last_run_timestamp="2026-07-21T22:40:40Z",
                    status="MISSING_OUTPUT", result_available=False,
                    displayed_in_command_center=False,
                    warning="Marked COMPLETE but no experiment output found. Action: Re-run experiment.",
                ),
            ],
            warnings=["Q16: Marked COMPLETE but no experiment output found. Action: Re-run experiment."],
            total_complete=25,
            total_with_output=7,
            total_missing_output=18,
            total_stale=6,
        ),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: RESEARCH TRACEABILITY
# ═══════════════════════════════════════════════════════════════════════════════


class TestTraceability:
    """Test Section 11: Research Traceability and Provenance."""

    def test_provenance_from_dashboard_with_reports(self):
        """Questions with matching reports get full provenance."""
        from research_engine.registry.research_question_registry import REGISTRY
        dashboard = {
            "questions": {
                "Q5": {
                    "question": "What patterns degrade?",
                    "status": "COMPLETE",
                    "runner": "experiments.research_runner",
                    "last_run": "2026-07-21T22:40:43Z",
                    "report_file": "analysis/reports/q5_pattern_degradation.json",
                    "recommendation": "COMPLETE",
                },
            },
        }
        reports = {
            "Q5": {
                "question_id": "Q5",
                "timestamp": "2026-07-21T22:40:43Z",
                "dataset": {"source": "shadow_trades", "sample_size": 1220},
                "metrics": {},
            },
        }
        trace = _build_traceability(dashboard, reports, shadow_count=1500)
        assert len(trace.questions) == len(REGISTRY)  # All registry questions
        # E2 has legacy_ids=("Q5","Q24") — picks up the Q5 report
        e2 = next(q for q in trace.questions if q.question_id == "E2")
        assert e2.result_available is True
        assert e2.status == "COMPLETE"
        assert e2.dataset_fingerprint is not None
        assert e2.dataset_fingerprint.records_used == 1220
        assert e2.dataset_fingerprint.validation_score == "HIGH"

    def test_missing_output_detected(self):
        """Question marked COMPLETE without matching report generates warning."""
        # R1 has legacy_ids=("Q10",) — mark as COMPLETE via dashboard but no report
        dashboard = {
            "questions": {
                "Q10": {
                    "question": "Guard efficacy?",
                    "status": "COMPLETE",
                    "runner": "not_implemented",
                    "last_run": "2026-07-21T22:40:52Z",
                    "report_file": "analysis/reports/q10_guard_efficacy.json",
                    "recommendation": "INSUFFICIENT_DATA",
                },
            },
        }
        reports = {}  # No matching report
        trace = _build_traceability(dashboard, reports, shadow_count=800)
        # R1 and R2 both have legacy_ids=("Q10",) — both get COMPLETE status
        missing = [q for q in trace.questions if q.status == "MISSING_OUTPUT"]
        assert len(missing) >= 1
        assert any("Re-run" in q.warning for q in missing)

    def test_stale_report_detected(self):
        """Reports older than 7 days are flagged as stale."""
        # D1 has legacy_ids=("Q1",)
        dashboard = {
            "questions": {
                "Q1": {
                    "question": "Component reward?",
                    "status": "COMPLETE",
                    "runner": "experiments.component_reward",
                    "last_run": "2026-01-01T00:00:00Z",
                    "report_file": "analysis/reports/q1_component_reward.json",
                    "recommendation": "WEIGHT_ADJUSTMENT",
                },
            },
        }
        reports = {
            "Q1": {
                "question_id": "Q1",
                "timestamp": "2026-01-01T00:00:00Z",
                "dataset": {"source": "shadow_trades", "sample_size": 469},
                "metrics": {},
            },
        }
        trace = _build_traceability(dashboard, reports, shadow_count=800)
        assert trace.total_stale >= 1
        stale = [q for q in trace.questions if "days old" in q.warning]
        assert len(stale) >= 1

    def test_dataset_fingerprint_built(self):
        """Dataset fingerprint correctly computed from report data."""
        # E1 has legacy_ids=("Q19",)
        dashboard = {
            "questions": {
                "Q19": {
                    "question": "Expected value?",
                    "status": "COMPLETE",
                    "runner": "experiments.expected_value",
                    "last_run": "2026-07-27T00:00:00Z",
                    "report_file": "analysis/reports/q19_expected_value.json",
                    "recommendation": "POSITIVE_EDGE",
                },
            },
        }
        reports = {
            "Q19": {
                "question_id": "Q19",
                "timestamp": "2026-07-27T00:00:00Z",
                "dataset": {"source": "shadow_trades", "sample_size": 501},
                "metrics": {"expected_value": 0.54},
            },
        }
        trace = _build_traceability(dashboard, reports, shadow_count=800)
        # E1 should pick up the Q19 report via legacy_ids
        e1 = next(q for q in trace.questions if q.question_id == "E1")
        assert e1.dataset_fingerprint is not None
        fp = e1.dataset_fingerprint
        assert fp.dataset_id == "shadow_trades_2026-07-27"
        assert fp.records_used == 501
        assert fp.records_excluded == 299  # 800 - 501
        assert fp.validation_score == "HIGH"

    def test_fingerprint_low_sample(self):
        """Small sample size gets LOW validation score."""
        # X3 has legacy_ids=("Q9",)
        dashboard = {
            "questions": {
                "Q9": {
                    "question": "Spread quality?",
                    "status": "COMPLETE",
                    "runner": "not_implemented",
                    "last_run": "2026-07-27T00:00:00Z",
                    "report_file": "analysis/reports/q9.json",
                    "recommendation": "COMPLETE",
                },
            },
        }
        reports = {
            "Q9": {
                "question_id": "Q9",
                "timestamp": "2026-07-27T00:00:00Z",
                "dataset": {"source": "execution_context", "sample_size": 15},
                "metrics": {},
            },
        }
        trace = _build_traceability(dashboard, reports, shadow_count=800)
        x3 = next(q for q in trace.questions if q.question_id == "X3")
        assert x3.dataset_fingerprint is not None
        assert x3.dataset_fingerprint.validation_score == "LOW"

    def test_displayed_in_command_center_flag(self):
        """Reports that exist are flagged as displayed."""
        dashboard = {
            "questions": {
                "Q19": {"question": "EV?", "status": "COMPLETE", "runner": "experiments.expected_value", "last_run": "2026-07-27T00:00:00Z", "report_file": "x", "recommendation": "POSITIVE_EDGE"},
            },
        }
        reports = {
            "Q19": {"question_id": "Q19", "timestamp": "2026-07-27", "dataset": {"source": "s", "sample_size": 100}, "metrics": {}},
        }
        trace = _build_traceability(dashboard, reports, shadow_count=500)
        # E1 gets the Q19 report via legacy mapping → displayed
        e1 = next(q for q in trace.questions if q.question_id == "E1")
        assert e1.displayed_in_command_center is True

    def test_empty_dashboard(self):
        """No dashboard produces traceability from registry (all questions present)."""
        from research_engine.registry.research_question_registry import REGISTRY
        trace = _build_traceability(None, {}, 0)
        assert len(trace.questions) == len(REGISTRY)  # All registry questions
        assert trace.total_complete == 0

    def test_section_11_renders(self, capsys):
        """Section 11 appears in CLI output."""
        report = _make_full_report()
        print_report(report)
        captured = capsys.readouterr()
        assert "RESEARCH TRACEABILITY" in captured.out
        assert "PROVENANCE CHAIN" in captured.out
        assert "Q19" in captured.out
        assert "MISSING OUTPUTS" in captured.out

    def test_traceability_in_json(self):
        """Traceability section serializes correctly."""
        report = _make_full_report()
        d = report.to_dict()
        assert "traceability" in d
        tr = d["traceability"]
        assert tr["total_complete"] == 25
        assert tr["total_missing_output"] == 18
        assert len(tr["questions"]) == 3
        # Check fingerprint serialization
        q19 = next(q for q in tr["questions"] if q["question_id"] == "Q19")
        assert q19["dataset_fingerprint"]["records_used"] == 501
        assert q19["dataset_fingerprint"]["dataset_id"] == "shadow_trades_2026-07-21"
