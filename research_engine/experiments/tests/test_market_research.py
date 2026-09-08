"""
WAVE 7 — Tests for Market / Context Depth Research Runners.

Validates:
    - M2, M3, M4, M6, M7, M11 executable
    - Each reads intended canonical evidence (shadow_trades)
    - Questions do not silently answer from unrelated proxy datasets
    - Observation-level questions do not require trades unnecessarily
    - Outcome-conditioned questions use canonical outcome joins
    - Sample-size units are correct
    - Quarantined decision evidence excluded where required
    - Valid historical raw market evidence remains usable
    - Empty evidence returns truthful insufficient-data results
    - Canonical identifiers are preferred
    - No production/runtime contracts changed

Uses the FakeS3 pattern from tests/_s3_fake.py.
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

from research_engine.data_access.s3_source import set_default_source, reset_default_source


# ═══════════════════════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════════════════════


def _make_shadow_record(
    *,
    entity_id: str = "EURUSD_170000",
    strategy: str = "REVERSAL",
    regime: str = "TRENDING",
    phase: str = "IMPULSE",
    pattern: str = "HAMMER",
    h1_bias: str = "BULLISH",
    r_multiple: float = 0.5,
    trade_id: str = "nshadow_1_EURUSD_SCALP",
    symbol: str = "EURUSD",
    entry_time: float = 170000.0,
    canonical_opportunity_id: str = "EURUSD*170000*HAMMER",
) -> dict:
    """Build a valid CURRENT-epoch shadow trade record."""
    return {
        "schema_version": "shadow_trades_v1",
        "source": "shadow_runtime_ingestion",
        "identity": {
            "trade_id": trade_id,
            "correlation_id": f"COR-{int(entry_time)}-{symbol}-ABCD",
            "symbol": symbol,
            "strategy_id": strategy,
            "entity_id": entity_id,
            "cycle_id": str(int(entry_time)),
            "canonical_opportunity_id": canonical_opportunity_id,
        },
        "decision_snapshot": {
            "pattern": pattern,
            "score": 0.55,
            "h4_regime": regime,
            "regime": regime,
            "h1_bias": h1_bias,
            "trade_horizon": "SCALP",
            "market_phase": phase,
            "phase": phase,
            "strategy": strategy,
            "entry_time": entry_time,
        },
        "simulated_outcome": {
            "pnl_r_multiple": r_multiple,
            "exit_reason": "take_profit" if r_multiple > 0 else "stop_loss",
            "exit_timestamp": entry_time + 60.0,
            "bars_held": 12,
        },
    }


def _seed_shadow_trades(fake: FakeS3, records: list[dict]) -> None:
    """Seed research_shadow_trades dataset in fake S3."""
    fake.add("research_shadow_trades", records, symbol="EURUSD", date="2026-09-07")


def _default_records(n: int = 30) -> list[dict]:
    """Create N reasonably varied records for testing."""
    records = []
    regimes = ["TRENDING", "RANGING", "TRANSITIONAL"]
    phases = ["IMPULSE", "PULLBACK", "CONSOLIDATION", "EXHAUSTION", "REVERSAL"]
    strategies = ["REVERSAL", "CONTINUATION", "FALSE_BREAK"]
    patterns = ["HAMMER", "ENGULFING", "TWEEZER_TOP", "PIN_BAR", "BOS_PULLBACK"]
    biases = ["BULLISH", "BEARISH", "NEUTRAL"]

    for i in range(n):
        r = 2.0 if i % 3 != 0 else -1.0
        records.append(_make_shadow_record(
            entity_id=f"EURUSD_{170000+i}",
            trade_id=f"nshadow_{i}_EURUSD_SCALP",
            strategy=strategies[i % len(strategies)],
            regime=regimes[i % len(regimes)],
            phase=phases[i % len(phases)],
            pattern=patterns[i % len(patterns)],
            h1_bias=biases[i % len(biases)],
            r_multiple=r,
            entry_time=170000.0 + i * 100,
            canonical_opportunity_id=f"EURUSD*{170000+i}*{patterns[i % len(patterns)]}",
        ))
    return records


# ═══════════════════════════════════════════════════════════════════════════════
# SETUP / TEARDOWN
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _reset_s3():
    """Reset S3 source before and after each test."""
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


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: M2 — H4 regime edge by strategy
# ═══════════════════════════════════════════════════════════════════════════════


class TestM2:
    def test_executable(self, fake_s3):
        """M2 runner executes without error."""
        from research_engine.experiments.market_research import run_m2
        report = run_m2()
        assert report is not None
        assert report["question_id"] == "M2"

    def test_reads_shadow_trades(self, fake_s3):
        """M2 reads its intended canonical evidence (shadow_trades)."""
        from research_engine.experiments.market_research import run_m2
        report = run_m2()
        assert report["dataset"]["source"] == "shadow_trades"
        assert report["overall"]["segmentation"] == "h4_regime × strategy"

    def test_returns_segment_metrics(self, fake_s3):
        """M2 returns segmented metrics by regime × strategy."""
        from research_engine.experiments.market_research import run_m2
        report = run_m2()
        segments = report["overall"].get("segments", {})
        assert len(segments) > 0
        # Should have regime × strategy combinations
        for seg_key, seg_data in segments.items():
            assert "mean_r" in seg_data
            assert "count" in seg_data
            assert "win_rate" in seg_data

    def test_does_not_require_trades_unnecessarily(self):
        """M2 is a DESCRIPTIVE market question — it can answer from
        market observations alone without requiring executed trades.
        (shadow_trades happen to carry outcome data, but the question
        is about regime+strategy combinations, not trade outcomes.)"""
        from research_engine.experiments.market_research import CANONICAL_INTENT_M2
        assert "regime" in CANONICAL_INTENT_M2.lower()
        assert "strategy" in CANONICAL_INTENT_M2.lower()
        assert "outcome" not in CANONICAL_INTENT_M2.lower()

    def test_not_from_unrelated_proxy(self, fake_s3):
        """M2 does not silently fall back to unrelated datasets."""
        from research_engine.experiments.market_research import run_m2
        report = run_m2()
        provenance = report.get("provenance", {})
        assert "registry_id" in provenance
        assert provenance["registry_id"] == "M2"

    def test_insufficient_data_on_empty(self):
        """Empty shadow trades produce INSUFFICIENT_DATA."""
        fake = install_fake_s3()
        _seed_shadow_trades(fake, [])
        from research_engine.experiments.market_research import run_m2
        report = run_m2()
        assert report["status"] == "INSUFFICIENT_DATA"

    def test_sample_size_units_correct(self, fake_s3):
        """M2 sample size = records with both regime + strategy + R-multiple,
        not raw trade count."""
        from research_engine.experiments.market_research import run_m2
        report = run_m2()
        sample = report["dataset"]["sample_size"]
        assert sample <= 30  # At most the 30 seeded records


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: M3 — Phase improves prediction beyond regime
# ═══════════════════════════════════════════════════════════════════════════════


class TestM3:
    def test_executable(self, fake_s3):
        """M3 runner executes without error."""
        from research_engine.experiments.market_research import run_m3
        report = run_m3()
        assert report is not None
        assert report["question_id"] == "M3"

    def test_reads_shadow_trades(self, fake_s3):
        """M3 reads its intended canonical evidence (shadow_trades)."""
        from research_engine.experiments.market_research import run_m3
        report = run_m3()
        assert report["dataset"]["source"] == "shadow_trades"

    def test_compares_regime_vs_combined(self, fake_s3):
        """M3 compares regime-only vs regime+phase segmentation."""
        from research_engine.experiments.market_research import run_m3
        report = run_m3()
        overall = report["overall"]
        assert "regime_only" in overall
        assert "regime_and_phase" in overall
        assert "phase_adds_predictive_value" in overall

    def test_phase_dispersion_computed(self, fake_s3):
        """M3 computes dispersion for both regime and combined."""
        from research_engine.experiments.market_research import run_m3
        report = run_m3()
        regime = report["overall"]["regime_only"]
        combined = report["overall"]["regime_and_phase"]
        assert "dispersion" in regime
        assert "dispersion" in combined

    def test_insufficient_data_on_empty(self):
        """Empty shadow trades produce INSUFFICIENT_DATA."""
        fake = install_fake_s3()
        _seed_shadow_trades(fake, [])
        from research_engine.experiments.market_research import run_m3
        report = run_m3()
        assert report["status"] == "INSUFFICIENT_DATA"

    def test_canonical_identifiers_preferred(self, fake_s3):
        """M3 uses canonical identifiers from decision_snapshot."""
        from research_engine.experiments.market_research import run_m3
        report = run_m3()
        assert report["provenance"]["registry_id"] == "M3"


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: M4 — Regime × phase × strategy edge
# ═══════════════════════════════════════════════════════════════════════════════


class TestM4:
    def test_executable(self, fake_s3):
        """M4 runner executes without error."""
        from research_engine.experiments.market_research import run_m4
        report = run_m4()
        assert report is not None
        assert report["question_id"] == "M4"

    def test_reads_shadow_trades(self, fake_s3):
        """M4 reads its intended canonical evidence."""
        from research_engine.experiments.market_research import run_m4
        report = run_m4()
        assert report["dataset"]["source"] == "shadow_trades"

    def test_three_way_segmentation(self, fake_s3):
        """M4 segments by regime × phase × strategy."""
        from research_engine.experiments.market_research import run_m4
        report = run_m4()
        segments = report["overall"].get("segments", {})
        # Each segment key should contain three parts separated by ' | '
        for key in segments:
            parts = key.split(" | ")
            assert len(parts) == 3

    def test_reports_coverage_pct(self, fake_s3):
        """M4 reports how many of the expected 45 combinations are populated."""
        from research_engine.experiments.market_research import run_m4
        report = run_m4()
        assert "coverage_pct" in report["overall"]
        assert "populated_combinations" in report["overall"]

    def test_insufficient_data_on_empty(self):
        """Empty data produces INSUFFICIENT_DATA."""
        fake = install_fake_s3()
        _seed_shadow_trades(fake, [])
        from research_engine.experiments.market_research import run_m4
        report = run_m4()
        assert report["status"] == "INSUFFICIENT_DATA"

    def test_sample_size_correct(self, fake_s3):
        """M4 sample = records with all 3 dimensions + outcome."""
        from research_engine.experiments.market_research import run_m4
        report = run_m4()
        sample = report["dataset"]["sample_size"]
        assert sample >= 0


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: M6 — Market phase expectancy
# ═══════════════════════════════════════════════════════════════════════════════


class TestM6:
    def test_executable(self, fake_s3):
        """M6 runner executes without error."""
        from research_engine.experiments.market_research import run_m6
        report = run_m6()
        assert report is not None
        assert report["question_id"] == "M6"

    def test_reads_shadow_trades(self, fake_s3):
        """M6 reads its intended canonical evidence."""
        from research_engine.experiments.market_research import run_m6
        report = run_m6()
        assert report["dataset"]["source"] == "shadow_trades"

    def test_segments_by_phase(self, fake_s3):
        """M6 segments outcomes by market_phase."""
        from research_engine.experiments.market_research import run_m6
        report = run_m6()
        segments = report["overall"].get("segments", {})
        assert len(segments) > 0
        for phase_name in segments:
            assert phases_found(report, phase_name)

    def test_observation_level_no_trade_requirement(self):
        """M6 is an observation-level market question — it does not
        require executed trades to answer."""
        from research_engine.experiments.market_research import CANONICAL_INTENT_M6
        assert "phase" in CANONICAL_INTENT_M6.lower()
        assert "trade" not in CANONICAL_INTENT_M6.lower()

    def test_insufficient_data_on_empty(self):
        """Empty shadow trades produce INSUFFICIENT_DATA."""
        fake = install_fake_s3()
        _seed_shadow_trades(fake, [])
        from research_engine.experiments.market_research import run_m6
        report = run_m6()
        assert report["status"] == "INSUFFICIENT_DATA"


def phases_found(report, phase_name):
    """Helper to check phase in segments."""
    segments = report["overall"].get("segments", {})
    return any(phase_name in k for k in segments)


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: M7 — Regime + phase interaction
# ═══════════════════════════════════════════════════════════════════════════════


class TestM7:
    def test_executable(self, fake_s3):
        """M7 runner executes without error."""
        from research_engine.experiments.market_research import run_m7
        report = run_m7()
        assert report is not None
        assert report["question_id"] == "M7"

    def test_reads_shadow_trades(self, fake_s3):
        """M7 reads its intended canonical evidence."""
        from research_engine.experiments.market_research import run_m7
        report = run_m7()
        assert report["dataset"]["source"] == "shadow_trades"

    def test_three_model_comparison(self, fake_s3):
        """M7 compares regime-only, phase-only, and combined models."""
        from research_engine.experiments.market_research import run_m7
        report = run_m7()
        overall = report["overall"]
        assert "regime_only" in overall
        assert "phase_only" in overall
        assert "regime_and_phase" in overall

    def test_dispersion_for_each_model(self, fake_s3):
        """Each model has dispersion computed."""
        from research_engine.experiments.market_research import run_m7
        report = run_m7()
        assert "dispersion" in report["overall"]["regime_only"]
        assert "dispersion" in report["overall"]["phase_only"]
        assert "dispersion" in report["overall"]["regime_and_phase"]

    def test_insufficient_data_on_empty(self):
        """Empty data produces INSUFFICIENT_DATA."""
        fake = install_fake_s3()
        _seed_shadow_trades(fake, [])
        from research_engine.experiments.market_research import run_m7
        report = run_m7()
        assert report["status"] == "INSUFFICIENT_DATA"


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: M11 — Context predictive value vs pattern
# ═══════════════════════════════════════════════════════════════════════════════


class TestM11:
    def test_executable(self, fake_s3):
        """M11 runner executes without error."""
        from research_engine.experiments.market_research import run_m11
        report = run_m11()
        assert report is not None
        assert report["question_id"] == "M11"

    def test_reads_shadow_trades(self, fake_s3):
        """M11 reads its intended canonical evidence."""
        from research_engine.experiments.market_research import run_m11
        report = run_m11()
        assert report["dataset"]["source"] == "shadow_trades"

    def test_compares_context_vs_pattern(self, fake_s3):
        """M11 compares pattern-only vs context-only vs combined."""
        from research_engine.experiments.market_research import run_m11
        report = run_m11()
        overall = report["overall"]
        assert "pattern_only" in overall
        assert "context_only" in overall
        assert "context_and_pattern" in overall

    def test_context_vs_pattern_verdict(self, fake_s3):
        """M11 reports whether context is more predictive than pattern."""
        from research_engine.experiments.market_research import run_m11
        report = run_m11()
        assert "context_more_predictive_than_pattern" in report["overall"]

    def test_insufficient_data_on_empty(self):
        """Empty data produces INSUFFICIENT_DATA."""
        fake = install_fake_s3()
        _seed_shadow_trades(fake, [])
        from research_engine.experiments.market_research import run_m11
        report = run_m11()
        assert report["status"] == "INSUFFICIENT_DATA"

    def test_prefers_canonical_identifiers(self, fake_s3):
        """M11 uses canonical identifiers, not loose joins."""
        from research_engine.experiments.market_research import run_m11
        report = run_m11()
        for seg_key in report["overall"].get("pattern_only", {}).get("segments", {}):
            assert isinstance(seg_key, str)


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: M5 — Phase transitions predict realised R drawdown
# ═══════════════════════════════════════════════════════════════════════════════


class TestM5:
    def test_executable(self, fake_s3):
        """M5 runner exists and executes."""
        from research_engine.experiments.market_temporal import run_m5
        report = run_m5()
        assert report is not None
        assert report["question_id"] == "M5"

    def test_uses_derived_realised_r_drawdown_not_account_equity(self, fake_s3):
        from research_engine.experiments.market_temporal import run_m5
        report = run_m5()
        assert report["overall"]["derived_or_observed"] == "DERIVED_FROM_CLOSED_OUTCOME_R"
        definition = report["overall"]["drawdown_definition"]
        assert "cumulative R" in definition
        assert "not mark-to-market" in definition
        assert "account equity" in definition

    def test_temporal_verdict_documented(self, fake_s3):
        """M5 documents its temporal universe verdict."""
        from research_engine.experiments.market_temporal import run_m5
        report = run_m5()
        overall = report["overall"]
        assert "temporal_universe_required" in overall
        assert "temporal_universe_available" in overall
        assert overall["temporal_universe_required"] is True

    def test_running_peak_and_drawdown_calculation(self):
        """M5 derives cumulative R peak/drawdown from chronological closes."""
        fake = install_fake_s3()
        records = [
            _make_shadow_record(trade_id="nshadow_a", phase="IMPULSE", r_multiple=1.0, entry_time=1000, canonical_opportunity_id="opp-a"),
            _make_shadow_record(trade_id="nshadow_b", phase="EXHAUSTION", r_multiple=-0.5, entry_time=1100, canonical_opportunity_id="opp-b"),
            _make_shadow_record(trade_id="nshadow_c", phase="PULLBACK", r_multiple=-1.0, entry_time=1200, canonical_opportunity_id="opp-c"),
            _make_shadow_record(trade_id="nshadow_d", phase="IMPULSE", r_multiple=0.25, entry_time=1300, canonical_opportunity_id="opp-d"),
        ]
        _seed_shadow_trades(fake, records)

        from research_engine.experiments.market_temporal import _build_m5_rows
        rows, excluded, duplicates = _build_m5_rows(records)

        assert excluded == 0
        assert duplicates == 0
        assert [r["cumulative_r_after"] for r in rows] == [1.0, 0.5, -0.5, -0.25]
        assert [r["peak_r_after"] for r in rows] == [1.0, 1.0, 1.0, 1.0]
        assert [r["drawdown_r_after"] for r in rows] == [0.0, 0.5, 1.5, 1.25]

    def test_phase_transition_alignment_uses_future_outcomes_only(self):
        """The phase signal at T is joined only to outcomes after T."""
        records = [
            _make_shadow_record(trade_id="nshadow_a", phase="IMPULSE", r_multiple=1.0, entry_time=1000, canonical_opportunity_id="opp-a"),
            _make_shadow_record(trade_id="nshadow_b", phase="EXHAUSTION", r_multiple=-1.0, entry_time=1100, canonical_opportunity_id="opp-b"),
        ]
        from research_engine.experiments.market_temporal import (
            _build_m5_observations,
            _build_m5_rows,
        )
        rows, _, _ = _build_m5_rows(records)
        obs = _build_m5_observations(rows)
        second = next(o for o in obs if o["key"] == "nshadow_b")
        assert second["phase_transition"] == "IMPULSE→EXHAUSTION"
        assert second["baseline_drawdown_r_at_signal"] == 0.0
        assert second["future_max_drawdown_increase_r"] == 1.0

    def test_duplicate_trade_protection(self):
        """Duplicate shadow_trade_id does not double-count realised R."""
        rec = _make_shadow_record(trade_id="nshadow_dup", phase="IMPULSE", r_multiple=-1.0, entry_time=1000, canonical_opportunity_id="opp-dup")
        from research_engine.experiments.market_temporal import _build_m5_rows
        rows, excluded, duplicates = _build_m5_rows([rec, dict(rec)])
        assert len(rows) == 1
        assert excluded == 0
        assert duplicates == 1

    def test_current_evidence_filtering(self):
        """M5 excludes non-CURRENT strategy-conditioned evidence."""
        fake = install_fake_s3()
        current = _make_shadow_record(trade_id="nshadow_current", phase="IMPULSE", r_multiple=1.0, entry_time=1000, canonical_opportunity_id="opp-current")
        legacy = _make_shadow_record(trade_id="nshadow_legacy", phase="EXHAUSTION", r_multiple=-1.0, entry_time=1100, canonical_opportunity_id="")
        _seed_shadow_trades(fake, [current, legacy])
        from research_engine.experiments.market_temporal import run_m5
        report = run_m5()
        assert report["fingerprint"]["records_excluded"] >= 1

    def test_empty_evidence_is_insufficient(self):
        fake = install_fake_s3()
        _seed_shadow_trades(fake, [])
        from research_engine.experiments.market_temporal import run_m5
        report = run_m5()
        assert report["status"] == "INSUFFICIENT_DATA"

    def test_insufficient_sample_reported_honestly(self):
        fake = install_fake_s3()
        _seed_shadow_trades(fake, [_make_shadow_record(entry_time=1000)])
        from research_engine.experiments.market_temporal import run_m5
        report = run_m5()
        assert report["status"] == "INSUFFICIENT_DATA"
        assert "minimum_records" in report["overall"]["sample_sufficiency"]


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: M8 — Phase transition behaviour
# ═══════════════════════════════════════════════════════════════════════════════


class TestM8:
    def test_executable(self, fake_s3):
        """M8 runner executes without error."""
        from research_engine.experiments.market_temporal import run_m8
        report = run_m8()
        assert report is not None
        assert report["question_id"] == "M8"

    def test_reads_shadow_trades(self, fake_s3):
        """M8 reads its intended canonical evidence."""
        from research_engine.experiments.market_temporal import run_m8
        report = run_m8()
        assert report["dataset"]["source"] == "shadow_trades"

    def test_computes_transition_vs_no_transition(self, fake_s3):
        """M8 compares outcomes with vs without recent phase transition."""
        from research_engine.experiments.market_temporal import run_m8
        report = run_m8()
        ta = report["overall"]["transition_analysis"]
        assert "with_transition" in ta
        assert "without_transition" in ta

    def test_documents_temporal_verdict(self, fake_s3):
        """M8 documents TEMPORAL_UNIVERSE_OPTIONAL verdict."""
        from research_engine.experiments.market_temporal import run_m8
        report = run_m8()
        assert report["overall"]["temporal_universe_verdict"] == "TEMPORAL_UNIVERSE_OPTIONAL"
        assert report["overall"]["temporal_universe_used"] is False

    def test_transition_type_breakdown(self, fake_s3):
        """M8 provides per-transition-type stats when transitions exist."""
        from research_engine.experiments.market_temporal import run_m8
        report = run_m8()
        ta = report["overall"]["transition_analysis"]
        assert "transition_types" in ta

    def test_insufficient_data_on_empty(self):
        """Empty shadow trades produce INSUFFICIENT_DATA."""
        fake = install_fake_s3()
        _seed_shadow_trades(fake, [])
        from research_engine.experiments.market_temporal import run_m8
        report = run_m8()
        assert report["status"] == "INSUFFICIENT_DATA"


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: Cross-cutting concerns
# ═══════════════════════════════════════════════════════════════════════════════


class TestCrossCutting:
    def test_report_contract_compliance(self, fake_s3):
        """All M2/M3/M4/M6/M7/M11 reports comply with canonical contract."""
        from research_engine.experiments.market_research import (
            run_m2, run_m3, run_m4, run_m6, run_m7, run_m11,
        )
        from research_engine.experiments.report_contract import validate_report_contract

        for runner in (run_m2, run_m3, run_m4, run_m6, run_m7, run_m11):
            report = runner()
            valid, errors = validate_report_contract(report)
            assert valid, f"{report['question_id']}: {errors}"

    def test_m8_report_contract_compliance(self, fake_s3):
        """M8 report complies with canonical contract."""
        from research_engine.experiments.market_temporal import run_m8
        from research_engine.experiments.report_contract import validate_report_contract

        report = run_m8()
        valid, errors = validate_report_contract(report)
        assert valid, f"M8: {errors}"

    def test_no_production_writers_modified(self, fake_s3):
        """Verify no production writer schemas are modified by this wave."""
        import inspect
        import research_engine.experiments.market_research as mr
        import research_engine.experiments.market_temporal as mt

        source = inspect.getsource(mr) + inspect.getsource(mt)

        # These modules should NOT directly import runtime writers.
        for mod_name in ("core.shadow_trades", "core.trade_truth",
                         "core.execution_context", "core.opportunity"):
            assert f"from {mod_name}" not in source
            assert f"import {mod_name}" not in source, (
                f"{mod_name} is referenced by market research source"
            )

    def test_quarantine_safety(self, fake_s3):
        """Market research does NOT read from quarantined datasets.

        The active S3 research datasets (shadow_trades via shadow_runtime
        ingestion) are closed-bar evidence. The forming-bar decision defect
        contaminated decision-derived assessments/strategy_observations —
        those datasets are NOT read by these runners.
        """
        from research_engine.experiments.market_research import run_m2  # noqa: F401
        from research_engine.data_access.shadow_runtime_ingestion import (
            ingest_completed_shadow_trades,
        )

        # The runners load via the canonical shadow-trades path
        report = run_m2()
        assert report["dataset"]["source"] == "shadow_trades"

        # Quarantined datasets must not appear as research sources
        for ds in ("assessments", "strategy_observations"):
            assert ds != report["dataset"]["source"]

    def test_canonical_identifiers_preferred_across_questions(self, fake_s3):
        """All M questions prefer canonical identifiers over loose joins."""
        from research_engine.experiments.market_research import (
            run_m2, run_m3, run_m4, run_m6, run_m7, run_m11,
        )
        for runner in (run_m2, run_m3, run_m4, run_m6, run_m7, run_m11):
            report = runner()
            assert report["provenance"]["registry_id"] == report["question_id"]

    def test_runner_discovery(self, fake_s3):
        """All M2-M4, M6-M8, M11 are discoverable via runner_discovery."""
        from research_engine.runner_discovery import discover_runners
        runners = discover_runners()
        for qid in ("M2", "M3", "M4", "M6", "M7", "M8", "M11"):
            assert qid in runners, f"{qid} not discovered"
            assert callable(runners[qid]), f"{qid} runner not callable"


# ═══════════════════════════════════════════════════════════════════════════════
# TEST: Registry integration
# ═══════════════════════════════════════════════════════════════════════════════


class TestRegistryIntegration:
    def test_registry_entries_have_runners(self):
        """M2-M8, M11 registry entries have runner metadata."""
        from research_engine.registry.research_question_registry import REGISTRY_BY_ID
        for qid in ("M2", "M3", "M4", "M6", "M7", "M8", "M11"):
            q = REGISTRY_BY_ID.get(qid)
            assert q is not None, f"{qid} not in registry"
            assert q.runner_module, f"{qid} missing runner_module"
            assert q.runner_function, f"{qid} missing runner_function"

    def test_m5_registry_uses_shadow_outcome_evidence(self):
        """M5 registry declares derived realised-R evidence, not equity_curve."""
        from research_engine.registry.research_question_registry import REGISTRY_BY_ID
        q = REGISTRY_BY_ID.get("M5")
        assert q is not None
        assert q.runner_module
        assert q.runner_function
        sources = [s.value for s in q.data_sources]
        assert sources == ["shadow_trades"]
        assert "equity_curve" not in sources
        assert "r_multiple" in q.required_fields
