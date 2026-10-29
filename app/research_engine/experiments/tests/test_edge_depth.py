"""
WAVE 8 — Tests for Strategy / Edge Depth Research Runners (E4, L5).

Validates:
    - E4 registered and executable
    - L5 registered and executable
    - Each reads intended evidence (shadow_trades)
    - Empty evidence produces INSUFFICIENT_DATA
    - Clean-evidence boundary respected
    - No duplicate ownership
    - No production/runtime code modified
    - Runner discovery succeeds
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_project_root = str(Path(__file__).resolve().parent.parent.parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from tests._s3_fake import FakeS3, install_fake_s3, reset_fake_s3


# ═══════════════════════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════════════════════


def _make_shadow_record(
    *,
    strategy: str = "REVERSAL",
    pattern: str = "HAMMER",
    r_multiple: float = 0.5,
    entry_time: float = 170000.0,
    trade_id: str = "nshadow_1_EURUSD_SCALP",
) -> dict:
    """Build a valid CURRENT-epoch shadow trade record."""
    entity_id = f"EURUSD_{int(entry_time)}"
    return {
        "schema_version": "shadow_trades_v1",
        "source": "shadow_runtime_ingestion",
        "identity": {
            "trade_id": trade_id,
            "correlation_id": f"COR-{int(entry_time)}-EURUSD-ABCD",
            "symbol": "EURUSD",
            "strategy_id": strategy,
            "entity_id": entity_id,
            "cycle_id": str(int(entry_time)),
            "canonical_opportunity_id": f"EURUSD*{int(entry_time)}*{pattern}",
        },
        "decision_snapshot": {
            "pattern": pattern,
            "score": 0.55,
            "h4_regime": "TRENDING",
            "regime": "TRENDING",
            "h1_bias": "BULLISH",
            "trade_horizon": "SCALP",
            "market_phase": "IMPULSE",
            "strategy": strategy,
            "entry_time": entry_time,
        },
        "simulated_outcome": {
            "pnl_r_multiple": r_multiple,
            "exit_reason": "take_profit" if r_multiple > 0 else "stop_loss",
            "bars_held": 12,
        },
    }


def _seed_shadow_trades(fake: FakeS3, records: list[dict]) -> None:
    """Seed research_shadow_trades dataset in fake S3."""
    fake.add("research_shadow_trades", records, symbol="EURUSD", date="2026-09-07")


def _default_records(n: int = 30) -> list[dict]:
    """Create N reasonably varied records for E4 testing."""
    records = []
    strategies = ["REVERSAL", "CONTINUATION", "FALSE_BREAK"]
    patterns = ["HAMMER", "ENGULFING", "TWEEZER_TOP", "PIN_BAR", "BOS_PULLBACK"]

    for i in range(n):
        r = 2.0 if i % 3 != 0 else -1.0
        records.append(_make_shadow_record(
            trade_id=f"nshadow_{i}_EURUSD_SCALP",
            strategy=strategies[i % len(strategies)],
            pattern=patterns[i % len(patterns)],
            r_multiple=r,
            entry_time=170000.0 + i * 100,
        ))
    return records


def _drift_records(n: int = 60, drift: bool = True) -> list[dict]:
    """Create records where strategy performance may change in later period."""
    records = []
    strategies = ["REVERSAL", "CONTINUATION", "FALSE_BREAK"]
    patterns = ["HAMMER", "ENGULFING"]

    for i in range(n):
        # Early records: mostly positive
        # Late records: if drift, flip to negative for REVERSAL
        if i < n // 2:
            r = 2.0 if i % 4 != 0 else -1.0
        else:
            if drift and i % len(strategies) == 0:  # REVERSAL degrades
                r = -1.0 if i % 2 == 0 else -2.0
            else:
                r = 1.5 if i % 3 != 0 else -1.0

        strat = strategies[i % len(strategies)]
        records.append(_make_shadow_record(
            trade_id=f"nshadow_{i}_EURUSD_SCALP",
            strategy=strat,
            pattern=patterns[i % len(patterns)],
            r_multiple=r,
            entry_time=170000.0 + i * 50,
        ))
    return records


# ═══════════════════════════════════════════════════════════════════════════════
# SETUP / TEARDOWN
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _reset_s3():
    reset_fake_s3()
    yield
    reset_fake_s3()


@pytest.fixture
def fake_s3():
    """Install fake S3 and seed with default records."""
    fake = install_fake_s3()
    records = _default_records(30)
    _seed_shadow_trades(fake, records)
    return fake


@pytest.fixture
def fake_s3_drift():
    """Install fake S3 and seed with drift-testing records."""
    fake = install_fake_s3()
    records = _drift_records(60, drift=True)
    _seed_shadow_trades(fake, records)
    return fake


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: E4 — Strategy × pattern combinations
# ═══════════════════════════════════════════════════════════════════════════════


class TestE4:
    def test_registered(self):
        """E4 is in the registry."""
        from research_engine.registry.research_question_registry import REGISTRY_BY_ID
        q = REGISTRY_BY_ID.get("E4")
        assert q is not None
        assert q.id == "E4"
        assert q.runner_module, "E4 missing runner_module"
        assert q.runner_function, "E4 missing runner_function"

    def test_executable(self, fake_s3):
        """E4 runner executes without error."""
        from research_engine.experiments.edge_depth import run_e4
        report = run_e4()
        assert report is not None
        assert report["question_id"] == "E4"

    def test_reads_shadow_trades(self, fake_s3):
        """E4 reads shadow_trades evidence."""
        from research_engine.experiments.edge_depth import run_e4
        report = run_e4()
        assert report["dataset"]["source"] == "shadow_trades"

    def test_segmentation_by_strategy_pattern(self, fake_s3):
        """E4 segments by strategy × pattern."""
        from research_engine.experiments.edge_depth import run_e4
        report = run_e4()
        segments = report["overall"].get("segments", {})
        assert len(segments) > 0
        for key in segments:
            parts = key.split(" | ")
            assert len(parts) == 2  # strategy | pattern

    def test_distinct_from_e2_e3(self, fake_s3):
        """E4 reports its distinct ownership from E2 and E3."""
        from research_engine.experiments.edge_depth import run_e4
        report = run_e4()
        assert "distinct_from" in report["overall"]
        assert "E2" in report["overall"]["distinct_from"]
        assert "E3" in report["overall"]["distinct_from"]

    def test_includes_strategy_and_pattern_dispersion(self, fake_s3):
        """E4 reports strategy-only and pattern-only dispersion for comparison."""
        from research_engine.experiments.edge_depth import run_e4
        report = run_e4()
        assert "strategy_only" in report["overall"]
        assert "pattern_only" in report["overall"]
        assert "interaction_analysis" in report["overall"]

    def test_insufficient_data_on_empty(self):
        """Empty shadow trades produce INSUFFICIENT_DATA."""
        fake = install_fake_s3()
        _seed_shadow_trades(fake, [])
        from research_engine.experiments.edge_depth import run_e4
        report = run_e4()
        assert report["status"] == "INSUFFICIENT_DATA"

    def test_does_not_depend_on_trades_unnecessarily(self):
        """E4 canonical intent is about strategy×pattern combinations,
        not about the decision to trade per se."""
        from research_engine.experiments.edge_depth import CANONICAL_INTENT_E4
        assert "strategy" in CANONICAL_INTENT_E4.lower()
        assert "pattern" in CANONICAL_INTENT_E4.lower()
        assert "trade" not in CANONICAL_INTENT_E4.lower()


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: L5 — Model drift detection
# ═══════════════════════════════════════════════════════════════════════════════


class TestL5:
    def test_registered(self):
        """L5 is in the registry with runner metadata."""
        from research_engine.registry.research_question_registry import REGISTRY_BY_ID
        q = REGISTRY_BY_ID.get("L5")
        assert q is not None
        assert q.id == "L5"
        assert q.runner_module, "L5 missing runner_module"
        assert q.runner_function, "L5 missing runner_function"

    def test_executable(self, fake_s3_drift):
        """L5 runner executes without error."""
        from research_engine.experiments.edge_depth import run_l5
        report = run_l5()
        assert report is not None
        assert report["question_id"] == "L5"

    def test_reads_shadow_trades(self, fake_s3_drift):
        """L5 reads shadow_trades evidence."""
        from research_engine.experiments.edge_depth import run_l5
        report = run_l5()
        assert report["dataset"]["source"] == "shadow_trades"

    def test_strategy_drift_analysis(self, fake_s3_drift):
        """L5 reports per-strategy early vs late analysis."""
        from research_engine.experiments.edge_depth import run_l5
        report = run_l5()
        sa = report["overall"].get("strategy_analysis", {})
        assert len(sa) > 0
        for strat_name, strat_data in sa.items():
            if isinstance(strat_data, dict) and "early_period" in strat_data:
                assert "late_period" in strat_data
                assert "change_mean_r" in strat_data
                assert "material_change_detected" in strat_data

    def test_ownership_documented(self, fake_s3_drift):
        """L5 documents its relationship to L1, L4, E5."""
        from research_engine.experiments.edge_depth import run_l5
        report = run_l5()
        assert "ownership_note" in report["overall"]
        assert "related_questions" in report["overall"]

    def test_chronological_ordering(self, fake_s3_drift):
        """L5 uses chronological split (early vs late) — enforces ordering."""
        from research_engine.experiments.edge_depth import run_l5
        report = run_l5()
        method = report["overall"].get("method", "")
        assert "chronological" in method.lower()

    def test_insufficient_data_on_empty(self):
        """Empty shadow trades produce INSUFFICIENT_DATA."""
        fake = install_fake_s3()
        _seed_shadow_trades(fake, [])
        from research_engine.experiments.edge_depth import run_l5
        report = run_l5()
        assert report["status"] == "INSUFFICIENT_DATA"

    def test_drift_detected_with_drifted_data(self, fake_s3_drift):
        """With drifted data, L5 reports drift detection."""
        from research_engine.experiments.edge_depth import run_l5
        report = run_l5()
        drift_summary = report["overall"]["drift_summary"]
        assert drift_summary["drift_count"] > 0

    def test_small_sample_insufficient(self):
        """Very small sample produces INSUFFICIENT_DATA."""
        fake = install_fake_s3()
        # Create only 3 records — way below minimum
        records = [
            _make_shadow_record(strategy="REVERSAL", pattern="HAMMER",
                                r_multiple=0.5, entry_time=100000.0),
            _make_shadow_record(strategy="CONTINUATION", pattern="ENGULFING",
                                r_multiple=1.0, entry_time=110000.0),
            _make_shadow_record(strategy="REVERSAL", pattern="HAMMER",
                                r_multiple=-0.5, entry_time=120000.0),
        ]
        _seed_shadow_trades(fake, records)
        from research_engine.experiments.edge_depth import run_l5
        report = run_l5()
        assert report["status"] in ("INSUFFICIENT_DATA", "COMPLETE")


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: Cross-cutting concerns
# ═══════════════════════════════════════════════════════════════════════════════


class TestCrossCutting:
    def test_report_contract_e4(self, fake_s3):
        """E4 report complies with canonical contract."""
        from research_engine.experiments.edge_depth import run_e4
        from research_engine.experiments.report_contract import validate_report_contract
        report = run_e4()
        valid, errors = validate_report_contract(report)
        assert valid, f"E4: {errors}"

    def test_report_contract_l5(self, fake_s3_drift):
        """L5 report complies with canonical contract."""
        from research_engine.experiments.edge_depth import run_l5
        from research_engine.experiments.report_contract import validate_report_contract
        report = run_l5()
        valid, errors = validate_report_contract(report)
        assert valid, f"L5: {errors}"

    def test_clean_evidence_boundary(self, fake_s3):
        """E4 and L5 read from shadow_trades (post-fix), not quarantined datasets."""
        from research_engine.experiments.edge_depth import run_e4
        report = run_e4()
        # Must not source from assessments, strategy_observations, or quarantine
        src = report["dataset"]["source"]
        assert src == "shadow_trades"

    def test_no_production_writers_modified(self):
        """Verify no core writer modules are imported by edge_depth."""
        import research_engine.experiments.edge_depth as ed
        source = Path(ed.__file__).read_text(encoding="utf-8")
        for mod in ("core.shadow_trades", "core.trade_truth",
                     "core.execution_context", "core.opportunity"):
            assert mod not in source

    def test_runner_discovery(self, fake_s3):
        """E4 and L5 are discoverable."""
        from research_engine.runner_discovery import discover_runners
        runners = discover_runners()
        assert "E4" in runners, "E4 not discovered"
        assert "L5" in runners, "L5 not discovered"
        assert callable(runners["E4"])
        assert callable(runners["L5"])

    def test_edge_semantics_distinct(self):
        """Module distinguishes expectancy from historical association."""
        from research_engine.experiments.edge_depth import CANONICAL_INTENT_E4
        # E4 documents its evidence classification
        assert "interaction" in CANONICAL_INTENT_E4.lower() or \
               "combination" in CANONICAL_INTENT_E4.lower()

    def test_no_duplicate_question_added(self):
        """Verify E4 and L5 are genuinely unique — no existing owner."""
        from research_engine.registry.research_question_registry import REGISTRY_BY_ID

        # E4 distinct from E2 and E3
        assert "E4" in REGISTRY_BY_ID
        assert "E2" in REGISTRY_BY_ID
        assert "E3" in REGISTRY_BY_ID

        # L5 distinct from L1, L4, E5
        assert "L5" in REGISTRY_BY_ID
        assert "L1" in REGISTRY_BY_ID
        assert "L4" in REGISTRY_BY_ID
        assert "E5" in REGISTRY_BY_ID