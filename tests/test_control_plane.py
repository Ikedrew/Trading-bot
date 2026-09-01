"""
Control Plane Tests.

Validates:
    - Models serialize correctly
    - Engine indexes universes and questions
    - Question products are created and updated independently
    - Run manifests track reproducibility
    - Question development enforces growth limits
    - Status generation produces navigable output
    - No question is executed during this phase
    - Optimisation remains downstream
"""

import json
import tempfile
from pathlib import Path

import pytest

from research_engine.v10.control_plane.models import (
    ControlPlaneState,
    FindingOutcome,
    GrowthLimits,
    QuestionLifecycle,
    QuestionProductIndex,
    ResearchRunManifest,
    UniverseHealth,
)
from research_engine.v10.control_plane.engine import ControlPlaneEngine
from research_engine.v10.control_plane.question_products import QuestionProductManager
from research_engine.v10.control_plane.question_development import (
    CandidateQuestion,
    QuestionDevelopmentSystem,
)
from research_engine.v10.control_plane.finding_schema import (
    ResearchFinding,
    ResearchGap,
    compare_findings,
)
from research_engine.v10.control_plane.status import generate_status_text


# ═══════════════════════════════════════════════════════════════════════════════
# MODEL TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestModels:

    def test_question_lifecycle_values(self):
        assert len(QuestionLifecycle) == 8

    def test_finding_outcome_values(self):
        assert len(FindingOutcome) == 6

    def test_growth_limits_defaults(self):
        gl = GrowthLimits()
        assert gl.max_active_questions == 60
        assert gl.auto_activate_questions is False
        assert gl.auto_optimise is False
        assert gl.require_approval_for_activation is True

    def test_control_plane_state_serialises(self):
        state = ControlPlaneState(engine_version="1.0.0")
        d = state.to_dict()
        assert d["engine_version"] == "1.0.0"
        assert "growth_limits" in d

    def test_run_manifest_serialises(self):
        m = ResearchRunManifest(
            run_id="run_001", timestamp="2026-08-09T00:00:00Z",
            engine_version="1.0", question_bank_version="1.0",
            questions_requested=45, questions_executed=40,
            questions_blocked=5, questions_failed=0,
            questions_inconclusive=3, findings_generated=37,
            anomalies_detected=2, exceptional_views=1,
            candidate_questions_generated=3,
        )
        d = m.to_dict()
        assert d["run_id"] == "run_001"
        assert d["questions_executed"] == 40


# ═══════════════════════════════════════════════════════════════════════════════
# ENGINE TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestControlPlaneEngine:

    def test_index_questions(self, tmp_path):
        engine = ControlPlaneEngine(
            state_file=tmp_path / "state.json",
            questions_dir=tmp_path / "questions",
        )
        # Use the real question bank
        from research_engine.v10.universes.question_bank import QUESTION_BANK
        engine.index_questions(QUESTION_BANK)
        assert engine.state.questions_active >= 40
        assert len(engine.state.questions) == len(QUESTION_BANK)

    def test_register_run(self, tmp_path):
        engine = ControlPlaneEngine(state_file=tmp_path / "state.json")
        manifest = ResearchRunManifest(
            run_id="run_test", timestamp="2026-08-09T01:00:00Z",
            engine_version="1.0", question_bank_version="1.0",
            questions_requested=10, questions_executed=8,
            questions_blocked=2, questions_failed=0,
            questions_inconclusive=1, findings_generated=7,
            anomalies_detected=0, exceptional_views=0,
            candidate_questions_generated=1,
        )
        engine.register_run(manifest)
        assert engine.state.last_run_id == "run_test"
        assert engine.state.latest_run is manifest

    def test_save_and_load_state(self, tmp_path):
        engine = ControlPlaneEngine(state_file=tmp_path / "state.json")
        engine._state.engine_version = "2.0.0"
        engine._state.questions_active = 45
        engine.save_state()

        engine2 = ControlPlaneEngine(state_file=tmp_path / "state.json")
        loaded = engine2.load_state()
        assert loaded is True
        assert engine2.state.engine_version == "2.0.0"

    def test_navigation_by_angle(self, tmp_path):
        engine = ControlPlaneEngine(
            state_file=tmp_path / "s.json",
            questions_dir=tmp_path / "q",
        )
        from research_engine.v10.universes.question_bank import QUESTION_BANK
        engine.index_questions(QUESTION_BANK)

        exec_qs = engine.get_questions_by_angle("EXECUTION")
        assert len(exec_qs) > 0
        dec_qs = engine.get_questions_by_angle("DECISION")
        assert len(dec_qs) > 0


# ═══════════════════════════════════════════════════════════════════════════════
# QUESTION PRODUCT TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestQuestionProducts:

    def test_initialise_product(self, tmp_path):
        mgr = QuestionProductManager(base_dir=tmp_path)
        path = mgr.initialise_product("E-001", {"title": "System Expectancy"})
        assert (path / "question.json").exists()
        assert (path / "history").is_dir()

    def test_save_finding(self, tmp_path):
        mgr = QuestionProductManager(base_dir=tmp_path)
        mgr.initialise_product("E-001", {"title": "System Expectancy"})

        finding = ResearchFinding(
            question_id="E-001", run_id="run_001",
            title="System Expectancy",
            outcome="POSITIVE", confidence="HIGH",
            conclusion="Expectancy is +0.15R per trade.",
            primary_metrics={"mean_r": 0.15, "win_rate": 0.42},
            limitations=["Small sample"],
        )
        path = mgr.save_finding(finding)
        assert path.exists()
        # History preserved
        history = tmp_path / "E-001" / "history" / "run_001.json"
        assert history.exists()
        # latest.md created
        assert (tmp_path / "E-001" / "latest.md").exists()

    def test_historical_findings_never_overwritten(self, tmp_path):
        mgr = QuestionProductManager(base_dir=tmp_path)
        mgr.initialise_product("E-001", {"title": "test"})

        f1 = ResearchFinding(question_id="E-001", run_id="run_001", outcome="INCONCLUSIVE")
        f2 = ResearchFinding(question_id="E-001", run_id="run_002", outcome="POSITIVE")
        mgr.save_finding(f1)
        mgr.save_finding(f2)

        # Both in history
        history = list((tmp_path / "E-001" / "history").glob("*.json"))
        assert len(history) == 2

        # latest.json has the newest
        latest = mgr.get_latest_finding("E-001")
        assert latest["outcome"] == "POSITIVE"
        assert latest["run_id"] == "run_002"

    def test_get_history(self, tmp_path):
        mgr = QuestionProductManager(base_dir=tmp_path)
        mgr.initialise_product("M-001", {"title": "Regime"})
        mgr.save_finding(ResearchFinding(question_id="M-001", run_id="run_001", outcome="INCONCLUSIVE"))
        mgr.save_finding(ResearchFinding(question_id="M-001", run_id="run_002", outcome="POSITIVE"))

        history = mgr.get_history("M-001")
        assert len(history) == 2

    def test_no_finding_returns_none(self, tmp_path):
        mgr = QuestionProductManager(base_dir=tmp_path)
        assert mgr.get_latest_finding("NONEXISTENT") is None


# ═══════════════════════════════════════════════════════════════════════════════
# QUESTION DEVELOPMENT TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestQuestionDevelopment:

    def test_propose_candidate(self):
        qds = QuestionDevelopmentSystem()
        candidate = CandidateQuestion(
            candidate_id="CAND-001",
            title="New question about regime drift",
            research_intent="Does regime change frequency predict drawdown?",
            source_finding_id="finding_M001_run001",
            source_question_id="M-001",
            evidence="M-001 finding showed regime changes correlate with losses",
            proposed_angles=["MARKET", "EXECUTION"],
        )
        accepted, reason = qds.propose_candidate(candidate)
        assert accepted is True
        assert len(qds.candidates) == 1

    def test_reject_without_evidence(self):
        qds = QuestionDevelopmentSystem()
        candidate = CandidateQuestion(
            candidate_id="CAND-002",
            title="Random question",
            research_intent="Something",
            source_finding_id="f1",
            source_question_id="Q1",
            evidence="",  # Empty!
        )
        accepted, reason = qds.propose_candidate(candidate)
        assert accepted is False
        assert "Evidence required" in reason

    def test_reject_without_lineage(self):
        qds = QuestionDevelopmentSystem()
        candidate = CandidateQuestion(
            candidate_id="CAND-003",
            title="No lineage",
            research_intent="Something",
            source_finding_id="",  # Empty!
            source_question_id="",
            evidence="Some evidence",
        )
        accepted, reason = qds.propose_candidate(candidate)
        assert accepted is False
        assert "lineage" in reason.lower()

    def test_reject_duplicate(self):
        qds = QuestionDevelopmentSystem()
        c1 = CandidateQuestion(
            candidate_id="CAND-A", title="Same Title",
            research_intent="x", source_finding_id="f1",
            source_question_id="Q1", evidence="yes",
        )
        c2 = CandidateQuestion(
            candidate_id="CAND-B", title="same title",  # Case-insensitive dup
            research_intent="y", source_finding_id="f2",
            source_question_id="Q2", evidence="yes",
        )
        qds.propose_candidate(c1)
        accepted, reason = qds.propose_candidate(c2)
        assert accepted is False
        assert "Duplicate" in reason

    def test_candidate_queue_limit(self):
        limits = GrowthLimits(max_candidate_questions=2)
        qds = QuestionDevelopmentSystem(limits=limits)
        for i in range(3):
            c = CandidateQuestion(
                candidate_id=f"C-{i}", title=f"Q {i}",
                research_intent="x", source_finding_id=f"f{i}",
                source_question_id="Q1", evidence="yes",
            )
            accepted, _ = qds.propose_candidate(c)
            if i < 2:
                assert accepted
            else:
                assert not accepted

    def test_activation_requires_approval(self):
        qds = QuestionDevelopmentSystem()
        qds.set_active_count(40)
        c = CandidateQuestion(
            candidate_id="CAND-X", title="Needs approval",
            research_intent="x", source_finding_id="f1",
            source_question_id="Q1", evidence="yes",
            proposed_angles=["MARKET"],
        )
        qds.propose_candidate(c)
        qds.validate_candidate("CAND-X")

        # Without approval → rejected
        activated, reason = qds.activate_candidate("CAND-X")
        assert activated is False
        assert "approval" in reason.lower()

        # With approval → accepted
        activated, reason = qds.activate_candidate("CAND-X", approved_by="researcher")
        assert activated is True

    def test_active_limit_enforced(self):
        limits = GrowthLimits(max_active_questions=45)
        qds = QuestionDevelopmentSystem(limits=limits)
        qds.set_active_count(45)  # Already at limit

        c = CandidateQuestion(
            candidate_id="CAND-OVER", title="Over limit",
            research_intent="x", source_finding_id="f1",
            source_question_id="Q1", evidence="yes",
            proposed_angles=["EXECUTION"],
        )
        qds.propose_candidate(c)
        qds.validate_candidate("CAND-OVER")
        activated, reason = qds.activate_candidate("CAND-OVER", approved_by="admin")
        assert activated is False
        assert "limit reached" in reason.lower()

    def test_auto_activate_disabled_by_default(self):
        gl = GrowthLimits()
        assert gl.auto_activate_questions is False
        assert gl.auto_optimise is False


# ═══════════════════════════════════════════════════════════════════════════════
# STATUS GENERATION TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestStatusGeneration:

    def test_generates_text(self):
        state = ControlPlaneState(
            engine_version="1.0.0",
            last_updated="2026-08-09T02:00:00Z",
        )
        state.universes = [
            UniverseHealth("EXECUTION", "VALID", 94, 4, "2026-08-09", "abc"),
            UniverseHealth("DECISION", "VALID", 7841, 10, "2026-08-09", "def"),
        ]
        state.questions_active = 45
        state.questions_run = 0

        text = generate_status_text(state)
        assert "RESEARCH ENGINE CONTROL CENTRE" in text
        assert "EXECUTION" in text
        assert "45" in text

    def test_next_action_index_universes(self):
        state = ControlPlaneState()
        text = generate_status_text(state)
        assert "Index universes" in text

    def test_next_action_first_run(self):
        state = ControlPlaneState()
        state.universes = [UniverseHealth("X", "VALID", 1, 1, "", "")]
        state.questions_run = 0
        text = generate_status_text(state)
        assert "first research run" in text

    def test_no_legacy_imports(self):
        import inspect
        from research_engine.v10.control_plane import models, engine, status
        for mod in (models, engine, status):
            source = inspect.getsource(mod)
            imports = [l for l in source.splitlines() if l.strip().startswith(("import", "from"))]
            for line in imports:
                assert "research_question_registry" not in line
                assert "v10_research_registry" not in line


# ═══════════════════════════════════════════════════════════════════════════════
# FINDING SCHEMA TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestFindingSchema:

    def test_finding_serialises(self):
        f = ResearchFinding(
            question_id="E-001",
            title="System Expectancy",
            run_id="run_001",
            outcome="POSITIVE",
            confidence="HIGH",
            primary_metrics={"mean_r": 0.15, "win_rate": 0.42},
        )
        d = f.to_dict()
        assert d["question_id"] == "E-001"
        assert d["outcome"] == "POSITIVE"
        assert d["primary_metrics"]["mean_r"] == 0.15

    def test_finding_supports_question_specific_evidence(self):
        f = ResearchFinding(
            question_id="E-001",
            evidence={
                "r_distribution": {"mean": 0.15, "median": -0.5, "std": 1.2},
                "by_pattern": {"ENGULFING": {"mean_r": 0.3, "n": 12}},
            },
        )
        d = f.to_dict()
        assert "r_distribution" in d["evidence"]
        assert "by_pattern" in d["evidence"]

    def test_finding_supports_four_angle_evidence(self):
        f = ResearchFinding(
            question_id="EDM-001",
            angle_evidence={
                "EXECUTION": {"mean_r": 0.15},
                "DECISION": {"mean_score": 72},
                "MARKET": {"trending_pct": 0.45},
            },
        )
        d = f.to_dict()
        assert len(d["angle_evidence"]) == 3

    def test_finding_supports_views(self):
        f = ResearchFinding(
            normal_view={"mean_r": 0.15, "n": 94},
            anomaly_view={"mean_r": -1.2, "n": 3},
            exceptional_view={"mean_r": 2.5, "n": 2},
        )
        d = f.to_dict()
        assert d["normal_view"]["n"] == 94
        assert d["anomaly_view"]["n"] == 3

    def test_research_gap_serialises(self):
        gap = ResearchGap(
            gap_id="GAP-001",
            description="Insufficient data for regime×strategy interaction",
            gap_type="INSUFFICIENT_EVIDENCE",
            source_question_id="MS-001",
            source_run_id="run_001",
            suggested_question="Does regime change affect strategy confidence?",
            suggested_angles=["MARKET", "STRATEGY"],
        )
        d = gap.to_dict()
        assert d["gap_type"] == "INSUFFICIENT_EVIDENCE"

    def test_compare_findings_first_run(self):
        f = ResearchFinding(outcome="POSITIVE")
        result = compare_findings(None, f)
        assert result["status"] == "first_run"

    def test_compare_findings_detects_outcome_change(self):
        prev = {"outcome": "INCONCLUSIVE", "confidence": "LOW", "sample_sizes": {}, "primary_metrics": {}}
        curr = ResearchFinding(outcome="POSITIVE", confidence="HIGH")
        result = compare_findings(prev, curr)
        assert "outcome_changed" in result
        assert result["outcome_changed"]["from"] == "INCONCLUSIVE"
        assert result["outcome_changed"]["to"] == "POSITIVE"

    def test_compare_findings_no_change(self):
        prev = {"outcome": "POSITIVE", "confidence": "HIGH", "sample_sizes": {"all": 94},
                "primary_metrics": {"mean_r": 0.15}, "anomaly_view": {}, "research_gaps": []}
        curr = ResearchFinding(
            outcome="POSITIVE", confidence="HIGH",
            sample_sizes={"all": 94}, primary_metrics={"mean_r": 0.15},
        )
        result = compare_findings(prev, curr)
        assert result.get("status") == "no_material_change"


# ═══════════════════════════════════════════════════════════════════════════════
# UPGRADED QUESTION PRODUCT TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestUpgradedProducts:

    def test_save_structured_finding(self, tmp_path):
        mgr = QuestionProductManager(base_dir=tmp_path)
        mgr.initialise_product("E-001", {"title": "Expectancy", "research_intent": "Is EV positive?"})

        finding = ResearchFinding(
            question_id="E-001",
            title="System Expectancy",
            run_id="run_001",
            run_timestamp="2026-08-09T03:00:00Z",
            outcome="POSITIVE",
            confidence="MEDIUM",
            conclusion="System has positive expectancy at +0.15R.",
            primary_metrics={"mean_r": 0.15, "win_rate": 0.42},
            sample_sizes={"all_trades": 94},
            universes_used=["EXECUTION"],
            populations_used=["all_trades"],
            evidence={"r_distribution": {"mean": 0.15, "std": 1.2}},
            limitations=["Small sample (94 trades)"],
        )
        path = mgr.save_finding(finding)
        assert path.exists()

        # Verify structure
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["outcome"] == "POSITIVE"
        assert data["primary_metrics"]["mean_r"] == 0.15
        assert data["evidence"]["r_distribution"]["mean"] == 0.15

        # History created
        history = tmp_path / "E-001" / "history" / "run_001.json"
        assert history.exists()
        # MD created
        md = tmp_path / "E-001" / "latest.md"
        assert md.exists()
        md_content = md.read_text(encoding="utf-8")
        assert "System Expectancy" in md_content
        assert "POSITIVE" in md_content

    def test_comparison_stored_in_finding(self, tmp_path):
        mgr = QuestionProductManager(base_dir=tmp_path)
        mgr.initialise_product("E-001", {"title": "test"})

        # First run
        f1 = ResearchFinding(
            question_id="E-001", run_id="run_001",
            outcome="INCONCLUSIVE", confidence="LOW",
            sample_sizes={"all": 50},
        )
        mgr.save_finding(f1)

        # Second run — outcome changed
        f2 = ResearchFinding(
            question_id="E-001", run_id="run_002",
            outcome="POSITIVE", confidence="HIGH",
            sample_sizes={"all": 150},
        )
        mgr.save_finding(f2)

        latest = mgr.get_latest_finding("E-001")
        assert latest["previous_run_id"] == "run_001"
        assert latest["previous_outcome"] == "INCONCLUSIVE"
        assert "outcome_changed" in latest["changes_from_previous"]

    def test_gaps_recorded_not_activated(self, tmp_path):
        mgr = QuestionProductManager(base_dir=tmp_path)
        mgr.initialise_product("M-001", {"title": "Regime"})

        gap = ResearchGap(
            gap_id="GAP-M001-001",
            description="Need more TRANSITIONAL data",
            gap_type="INSUFFICIENT_EVIDENCE",
            source_question_id="M-001",
            source_run_id="run_001",
            suggested_question="Does TRANSITIONAL→TRENDING transition predict recovery?",
            suggested_angles=["MARKET"],
            evidence="Only 5 TRANSITIONAL records observed",
        )
        finding = ResearchFinding(
            question_id="M-001", run_id="run_001",
            outcome="INCONCLUSIVE",
            research_gaps=[gap],
        )
        mgr.save_finding(finding)

        latest = mgr.get_latest_finding("M-001")
        assert len(latest["research_gaps"]) == 1
        assert latest["research_gaps"][0]["gap_type"] == "INSUFFICIENT_EVIDENCE"

    def test_question_independence(self, tmp_path):
        """One question failing must not break others."""
        mgr = QuestionProductManager(base_dir=tmp_path)
        mgr.initialise_product("E-001", {"title": "E1"})
        mgr.initialise_product("E-002", {"title": "E2"})

        # E-001 gets a finding
        f1 = ResearchFinding(question_id="E-001", run_id="r1", outcome="POSITIVE")
        mgr.save_finding(f1)

        # E-002 finding fails (simulate by not saving)
        # E-001's product remains intact
        assert mgr.get_latest_finding("E-001")["outcome"] == "POSITIVE"
        assert mgr.get_latest_finding("E-002") is None

    def test_product_health(self, tmp_path):
        mgr = QuestionProductManager(base_dir=tmp_path)
        mgr.initialise_product("S-001", {"title": "Strategy"})

        # Before any run
        health = mgr.product_health("S-001")
        assert health["has_definition"] is True
        assert health["has_latest_finding"] is False
        assert health["history_count"] == 0

        # After a run
        finding = ResearchFinding(question_id="S-001", run_id="r1", outcome="POSITIVE", confidence="HIGH")
        mgr.save_finding(finding)

        health = mgr.product_health("S-001")
        assert health["has_latest_finding"] is True
        assert health["history_count"] == 1
        assert health["latest_outcome"] == "POSITIVE"
        assert health["latest_confidence"] == "HIGH"

    def test_history_md_created(self, tmp_path):
        mgr = QuestionProductManager(base_dir=tmp_path)
        mgr.initialise_product("D-001", {"title": "Score"})
        finding = ResearchFinding(
            question_id="D-001", run_id="run_abc",
            title="Score Predictive Power",
            outcome="POSITIVE",
        )
        mgr.save_finding(finding)

        # Both JSON and MD in history
        assert (tmp_path / "D-001" / "history" / "run_abc.json").exists()
        assert (tmp_path / "D-001" / "history" / "run_abc.md").exists()
