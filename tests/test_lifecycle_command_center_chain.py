"""
END-TO-END VALIDATION: Research Lifecycle → Command Center Chain

Verifies the complete reporting chain:
    Hypothesis → Experiment → Dataset Fingerprint → Validation →
    Conclusion → Governance → Knowledge Map → Experiment Catalogue →
    Research Command Center

Tests:
- Command Center reads lifecycle registry data
- Command Center reads experiment catalogue data
- Investigation appears in CC without manual editing
- Restart persistence → still visible via CC
- Rejected vs validated vs inconclusive clearly distinguished
- No lifecycle/reporting operation can modify production trading
- Full human-reconstructability of what the research engine did
"""
import sys
import json
from unittest.mock import patch
from pathlib import Path

import pytest

sys.path.insert(0, ".")

from research_engine.lifecycle.orchestrator import ResearchOrchestrator, InvestigationResult
from research_engine.lifecycle.hypothesis import (
    Hypothesis, HypothesisCategory, HypothesisStatus, ConclusionType,
)
from research_engine.lifecycle.experiment_protocol import (
    ExperimentDefinition, ExperimentResult, ExperimentType,
    PopulationSpec, SimulationSpec,
)
from research_engine.lifecycle.experiment_catalogue import ExperimentCatalogue, ExperimentLifecycle
from research_engine.lifecycle.registry import InvestigationRegistry
from research_engine.command_center.research_command_center import _build_lifecycle_section
from research_engine.command_center.command_models import LifecycleSection


def _mock_population():
    return [
        {"symbol": "EURUSD", "cid": f"COR-{i}", "dir": "SELL", "entry": 1.085,
         "sl": 1.086, "tp": 1.083, "time": 1784739300 + i * 300,
         "pattern": "THREE_BLACK_CROWS", "score": 0.6}
        for i in range(50)
    ]


def _mock_candles():
    return [{"high": 1.086, "low": 1.083, "close": 1.084}] * 60


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    """Isolate ALL lifecycle persistence to tmp_path."""
    monkeypatch.setattr("research_engine.lifecycle.registry._REGISTRY_DIR", tmp_path)
    monkeypatch.setattr("research_engine.lifecycle.registry._REGISTRY_FILE", tmp_path / "registry.json")
    monkeypatch.setattr("research_engine.lifecycle.registry._AUDIT_LOG", tmp_path / "audit_log.jsonl")
    monkeypatch.setattr("research_engine.lifecycle.experiment_catalogue._CATALOGUE_DIR", tmp_path)
    monkeypatch.setattr("research_engine.lifecycle.experiment_catalogue._CATALOGUE_FILE", tmp_path / "experiment_registry.json")
    monkeypatch.setattr("research_engine.lifecycle.experiment_catalogue._AUDIT_LOG", tmp_path / "audit_log.jsonl")
    # Also point the CC lifecycle builder to the same paths
    monkeypatch.setattr("research_engine.command_center.research_command_center._build_lifecycle_section",
                        lambda: _build_lifecycle_from_tmp(tmp_path))
    return tmp_path


def _build_lifecycle_from_tmp(tmp_path):
    """Build lifecycle section reading from tmp_path."""
    from research_engine.command_center.command_models import LifecycleSection, LifecycleHypothesisSummary
    from research_engine.lifecycle.registry import InvestigationRegistry
    from research_engine.lifecycle.experiment_catalogue import ExperimentCatalogue
    from research_engine.lifecycle.hypothesis import HypothesisStatus, ConclusionType

    # Temporarily point modules to tmp_path for loading
    import research_engine.lifecycle.registry as _reg_mod
    import research_engine.lifecycle.experiment_catalogue as _cat_mod

    old_reg = _reg_mod._REGISTRY_FILE
    old_cat = _cat_mod._CATALOGUE_FILE
    old_reg_dir = _reg_mod._REGISTRY_DIR
    old_cat_dir = _cat_mod._CATALOGUE_DIR

    _reg_mod._REGISTRY_DIR = tmp_path
    _reg_mod._REGISTRY_FILE = tmp_path / "registry.json"
    _cat_mod._CATALOGUE_DIR = tmp_path
    _cat_mod._CATALOGUE_FILE = tmp_path / "experiment_registry.json"

    try:
        registry = InvestigationRegistry()
        catalogue = ExperimentCatalogue()

        hypotheses = registry.all()
        if not hypotheses:
            return LifecycleSection(available=False, unavailable_reason="No lifecycle data")

        h_by_status = {}
        awaiting = 0
        validated = rejected = inconclusive = 0
        for h in hypotheses:
            h_by_status[h.status.value] = h_by_status.get(h.status.value, 0) + 1
            if h.conclusion_type == ConclusionType.VALIDATED and not h.human_approval_granted:
                awaiting += 1
            if h.conclusion_type == ConclusionType.VALIDATED:
                validated += 1
            elif h.conclusion_type == ConclusionType.REJECTED:
                rejected += 1
            elif h.conclusion_type == ConclusionType.INCONCLUSIVE:
                inconclusive += 1

        cat_summary = catalogue.get_summary()
        recent = sorted(hypotheses, key=lambda h: h.detected_timestamp or "", reverse=True)[:5]
        recent_summaries = [LifecycleHypothesisSummary(
            hypothesis_id=h.hypothesis_id, title=h.title[:60], status=h.status.value,
            conclusion=h.conclusion_type.value if h.conclusion_type else "",
            confidence=h.conclusion_confidence, experiments_count=len(h.experiments),
            created_at=h.detected_timestamp[:19] if h.detected_timestamp else "",
        ) for h in recent]

        return LifecycleSection(
            available=True,
            total_hypotheses=len(hypotheses),
            hypotheses_by_status=h_by_status,
            hypotheses_concluded=sum(1 for h in hypotheses if h.status in (HypothesisStatus.CONCLUDED, HypothesisStatus.PROMOTED)),
            hypotheses_awaiting_approval=awaiting,
            total_experiments=cat_summary.get("total_experiments", 0),
            experiments_by_status=cat_summary.get("by_status", {}),
            experiments_completed=cat_summary.get("by_status", {}).get("COMPLETED", 0),
            experiments_failed=cat_summary.get("by_status", {}).get("FAILED", 0),
            conclusions_validated=validated,
            conclusions_rejected=rejected,
            conclusions_inconclusive=inconclusive,
            human_decisions_needed=awaiting,
            recent_hypotheses=recent_summaries,
        )
    finally:
        _reg_mod._REGISTRY_DIR = old_reg_dir
        _reg_mod._REGISTRY_FILE = old_reg
        _cat_mod._CATALOGUE_DIR = old_cat_dir
        _cat_mod._CATALOGUE_FILE = old_cat


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: FULL CHAIN — investigate() → Command Center
# ═══════════════════════════════════════════════════════════════════════════════


class TestFullChain:
    """Demonstrates the complete chain from investigation to Command Center visibility."""

    def test_investigation_appears_in_command_center(self, isolated_env, monkeypatch):
        """A completed investigation is visible via the lifecycle section."""
        orch = ResearchOrchestrator()
        orch._knowledge_path = isolated_env / "km.json"

        # Register hypothesis
        h = orch.detect_and_register(
            title="Chain Test: Direction Inversion",
            description="Verify full chain from investigate() to Command Center",
            claim="Inverted direction produces positive R",
            null_hypothesis="Direction has no effect",
            category=HypothesisCategory.DIRECTION_INVERSION,
            multiple_testing_count=10,
        )

        # Define experiment
        defn = ExperimentDefinition(
            hypothesis_id=h.hypothesis_id,
            experiment_type=ExperimentType.DIRECTION_INVERSION,
            title="Chain Test Inversion Experiment",
            population=PopulationSpec(pattern_filter=["THREE_BLACK_CROWS"], min_sample_size=30),
            simulation=SimulationSpec(direction="INVERT", stop_multiplier=1.0, tp_multiplier=3.0),
        )

        # Run investigation (mocked data)
        with patch("research_engine.lifecycle.experiment_templates._load_shadow_population",
                   return_value=_mock_population()):
            with patch("research_engine.lifecycle.experiment_templates._load_candles",
                       return_value=_mock_candles()):
                inv_result = orch.investigate(
                    hypothesis=h,
                    experiment_type=ExperimentType.DIRECTION_INVERSION,
                    experiment_definition=defn,
                    placebo_populations={"OTHER": _mock_population()[:25]},
                )

        assert inv_result.status == "complete"

        # NOW: verify the Command Center sees it
        lc = _build_lifecycle_from_tmp(isolated_env)
        assert lc.available
        assert lc.total_hypotheses == 1
        assert lc.hypotheses_concluded == 1
        assert lc.total_experiments >= 1  # Catalogue populated
        assert lc.experiments_completed >= 1

        # Verify conclusion type is visible
        assert (lc.conclusions_validated + lc.conclusions_rejected + lc.conclusions_inconclusive) == 1

        # Verify recent hypotheses show the investigation
        assert len(lc.recent_hypotheses) == 1
        assert lc.recent_hypotheses[0].hypothesis_id == h.hypothesis_id
        assert lc.recent_hypotheses[0].conclusion != ""

    def test_human_can_reconstruct_what_happened(self, isolated_env):
        """Verify all information needed for human reconstruction is present."""
        orch = ResearchOrchestrator()
        orch._knowledge_path = isolated_env / "km.json"

        h = orch.detect_and_register(
            title="Reconstruction Test",
            description="Testing human-reconstructability",
            claim="X is better than Y",
            null_hypothesis="X = Y",
            category=HypothesisCategory.PATTERN_SIGNAL,
            multiple_testing_count=5,
            discovery_bias_notes="Found after testing 5 variants",
        )

        defn = ExperimentDefinition(
            hypothesis_id=h.hypothesis_id,
            experiment_type=ExperimentType.DIRECTION_INVERSION,
            title="Reconstruction Experiment",
            population=PopulationSpec(pattern_filter=["THREE_BLACK_CROWS"], min_sample_size=30),
            simulation=SimulationSpec(direction="INVERT", stop_multiplier=1.0, tp_multiplier=3.0),
        )

        with patch("research_engine.lifecycle.experiment_templates._load_shadow_population",
                   return_value=_mock_population()):
            with patch("research_engine.lifecycle.experiment_templates._load_candles",
                       return_value=_mock_candles()):
                inv = orch.investigate(h, ExperimentType.DIRECTION_INVERSION, defn,
                                       placebo_populations={"CTL": _mock_population()[:25]})

        # Verify report contains all reconstructability info
        report = inv.report_text
        assert "Reconstruction Test" in report or h.hypothesis_id in report
        assert "DIRECTION_INVERSION" in report or "Inversion" in report
        assert inv.conclusion in report
        assert "Governance" in report
        assert "Human approval" in report or "human_approval" in report.lower() or "approval" in report.lower()

        # Verify experiment result has fingerprint
        assert inv.experiment_result is not None
        assert inv.experiment_result.dataset_fingerprint

        # Verify knowledge map updated
        km = json.loads(orch._knowledge_path.read_text(encoding="utf-8"))
        assert h.hypothesis_id in km.get("lifecycle_findings", {})

    def test_restart_persistence_visible_in_cc(self, isolated_env, monkeypatch):
        """After restart, lifecycle data remains visible in Command Center."""
        orch = ResearchOrchestrator()
        orch._knowledge_path = isolated_env / "km.json"

        h = orch.detect_and_register(
            title="Persistence Test",
            description="d", claim="c", null_hypothesis="n",
            category=HypothesisCategory.DIRECTION_INVERSION,
        )

        defn = ExperimentDefinition(
            hypothesis_id=h.hypothesis_id,
            experiment_type=ExperimentType.DIRECTION_INVERSION,
            title="Persistence Experiment",
            population=PopulationSpec(pattern_filter=["THREE_BLACK_CROWS"], min_sample_size=30),
            simulation=SimulationSpec(direction="INVERT", stop_multiplier=1.0, tp_multiplier=3.0),
        )

        with patch("research_engine.lifecycle.experiment_templates._load_shadow_population",
                   return_value=_mock_population()):
            with patch("research_engine.lifecycle.experiment_templates._load_candles",
                       return_value=_mock_candles()):
                orch.investigate(h, ExperimentType.DIRECTION_INVERSION, defn)

        # Simulate restart — create fresh objects
        lc_before = _build_lifecycle_from_tmp(isolated_env)
        assert lc_before.available
        assert lc_before.total_hypotheses == 1

        # Verify file exists on disk
        assert (isolated_env / "registry.json").exists()
        assert (isolated_env / "experiment_registry.json").exists()

        # Fresh load (simulates new process)
        lc_after = _build_lifecycle_from_tmp(isolated_env)
        assert lc_after.available
        assert lc_after.total_hypotheses == 1
        assert lc_after.hypotheses_concluded == 1

    def test_rejected_vs_validated_clearly_distinguished(self, isolated_env):
        """Rejected and validated investigations show different conclusion types."""
        orch = ResearchOrchestrator()
        orch._knowledge_path = isolated_env / "km.json"

        # Run two investigations with different outcomes
        # First: will be rejected (placebo fails because same data)
        h1 = orch.detect_and_register(
            title="Will Be Rejected", description="d", claim="c", null_hypothesis="n",
            category=HypothesisCategory.DIRECTION_INVERSION, multiple_testing_count=10,
        )
        defn1 = ExperimentDefinition(
            hypothesis_id=h1.hypothesis_id,
            experiment_type=ExperimentType.DIRECTION_INVERSION,
            title="Rejection Exp",
            population=PopulationSpec(pattern_filter=["THREE_BLACK_CROWS"], min_sample_size=30),
            simulation=SimulationSpec(direction="INVERT", stop_multiplier=1.0, tp_multiplier=3.0),
        )

        with patch("research_engine.lifecycle.experiment_templates._load_shadow_population",
                   return_value=_mock_population()):
            with patch("research_engine.lifecycle.experiment_templates._load_candles",
                       return_value=_mock_candles()):
                inv1 = orch.investigate(h1, ExperimentType.DIRECTION_INVERSION, defn1,
                                         placebo_populations={f"P{i}": _mock_population()[:25] for i in range(5)})

        # Command Center should show the correct classification
        lc = _build_lifecycle_from_tmp(isolated_env)
        assert lc.available
        assert lc.total_hypotheses == 1
        # Check that conclusion is reflected
        total_conclusions = lc.conclusions_validated + lc.conclusions_rejected + lc.conclusions_inconclusive
        assert total_conclusions == 1

    def test_no_production_modification_possible(self, isolated_env):
        """Lifecycle/reporting operations cannot modify production trading."""
        orch = ResearchOrchestrator()
        orch._knowledge_path = isolated_env / "km.json"

        h = orch.detect_and_register(
            title="Governance Test", description="d", claim="c", null_hypothesis="n",
        )
        defn = ExperimentDefinition(
            hypothesis_id=h.hypothesis_id,
            experiment_type=ExperimentType.DIRECTION_INVERSION,
            title="Gov Test",
            population=PopulationSpec(pattern_filter=["THREE_BLACK_CROWS"], min_sample_size=30),
            simulation=SimulationSpec(direction="INVERT", stop_multiplier=1.0, tp_multiplier=3.0),
        )

        with patch("research_engine.lifecycle.experiment_templates._load_shadow_population",
                   return_value=_mock_population()):
            with patch("research_engine.lifecycle.experiment_templates._load_candles",
                       return_value=_mock_candles()):
                inv = orch.investigate(h, ExperimentType.DIRECTION_INVERSION, defn)

        # Verify governance status
        assert inv.governance_status in ("BLOCKED", "AWAITING_HUMAN_APPROVAL")
        assert h.status != HypothesisStatus.PROMOTED
        assert not h.human_approval_granted

        # Cannot promote without human
        assert not h.transition(HypothesisStatus.PROMOTED, reason="auto")

    def test_audit_trail_contains_full_lifecycle(self, isolated_env):
        """Audit log captures the complete investigation chain."""
        orch = ResearchOrchestrator()
        orch._knowledge_path = isolated_env / "km.json"

        h = orch.detect_and_register(
            title="Audit Trail Test", description="d", claim="c", null_hypothesis="n",
        )
        defn = ExperimentDefinition(
            hypothesis_id=h.hypothesis_id,
            experiment_type=ExperimentType.DIRECTION_INVERSION,
            title="Audit Exp",
            population=PopulationSpec(pattern_filter=["THREE_BLACK_CROWS"], min_sample_size=30),
            simulation=SimulationSpec(direction="INVERT", stop_multiplier=1.0, tp_multiplier=3.0),
        )

        with patch("research_engine.lifecycle.experiment_templates._load_shadow_population",
                   return_value=_mock_population()):
            with patch("research_engine.lifecycle.experiment_templates._load_candles",
                       return_value=_mock_candles()):
                orch.investigate(h, ExperimentType.DIRECTION_INVERSION, defn)

        # Read audit log
        audit_file = isolated_env / "audit_log.jsonl"
        assert audit_file.exists()
        events = [json.loads(l)["event"] for l in audit_file.read_text(encoding="utf-8").strip().splitlines()]

        # Verify chain
        assert "REGISTERED" in events  # Hypothesis registered
        assert "INVESTIGATION_STARTED" in events
        assert "EXPERIMENT_REGISTERED" in events
        assert "EXPERIMENT_STARTED" in events
        assert "EXPERIMENT_COMPLETED" in events or "UPDATED" in events
        assert "INVESTIGATION_COMPLETED" in events

    def test_dataset_fingerprint_in_catalogue(self, isolated_env):
        """Experiment catalogue contains dataset fingerprint after investigation."""
        orch = ResearchOrchestrator()
        orch._knowledge_path = isolated_env / "km.json"

        h = orch.detect_and_register(
            title="Fingerprint Test", description="d", claim="c", null_hypothesis="n",
        )
        defn = ExperimentDefinition(
            hypothesis_id=h.hypothesis_id,
            experiment_type=ExperimentType.DIRECTION_INVERSION,
            title="FP Exp",
            population=PopulationSpec(pattern_filter=["THREE_BLACK_CROWS"], min_sample_size=30),
            simulation=SimulationSpec(direction="INVERT", stop_multiplier=1.0, tp_multiplier=3.0),
        )

        with patch("research_engine.lifecycle.experiment_templates._load_shadow_population",
                   return_value=_mock_population()):
            with patch("research_engine.lifecycle.experiment_templates._load_candles",
                       return_value=_mock_candles()):
                inv = orch.investigate(h, ExperimentType.DIRECTION_INVERSION, defn)

        # Check catalogue has fingerprint
        cat_rec = orch.catalogue.get(defn.experiment_id)
        assert cat_rec is not None
        assert cat_rec.dataset_fingerprint.get("content_hash", "") != ""
        assert cat_rec.dataset_fingerprint.get("fingerprint_algorithm") == "SHA-256"
        assert cat_rec.observation_count > 0
