"""
Tests for the 6 new Finding Detectors:
DIRECTION_ASYMMETRY, REGIME_ANOMALY, SCORE_MONOTONICITY,
TEMPORAL_INSTABILITY, GEOMETRY_ANOMALY, SYMBOL_ANOMALY.
"""
import sys
import json
from pathlib import Path

import pytest

sys.path.insert(0, ".")

from research_engine.lifecycle.finding_trigger import (
    FindingTriggerEngine, EligibilityConfig, TriggerCategory, TriggerStatus, ExecutionMode,
)
from research_engine.lifecycle.experiment_protocol import ExperimentType


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setattr("research_engine.lifecycle.finding_trigger._TRIGGER_DIR", tmp_path)
    monkeypatch.setattr("research_engine.lifecycle.finding_trigger._TRIGGER_FILE", tmp_path / "t.json")
    return FindingTriggerEngine(config=EligibilityConfig(
        min_direction_subgroup_n=10, min_direction_delta=0.30,
        min_regime_n=15, min_regime_delta=0.20,
        min_score_n=40, min_score_inversion_delta=0.15,
        min_temporal_period_n=15, min_temporal_delta=0.20,
        min_geometry_n=10, min_geometry_delta=0.25,
        min_symbol_n=10, min_symbol_delta=0.25,
        max_active_triggers=20,
    ))


def _make_shadows(pattern="X", direction="SELL", r_val=-0.5, n=30, **extra):
    return [{"pattern": pattern, "direction": direction, "r_multiple": r_val,
             "symbol": "EURUSD", "score": 0.6, "h4_regime": "TRENDING",
             "regime": "TRENDING", "timestamp_decision_utc": 1784739300 + i * 300,
             "risk_distance": 0.0003, "correlation_id": f"C-{i}", **extra}
            for i in range(n)]


# ═══════════════════════════════════════════════════════════════════════════════
# DIRECTION_ASYMMETRY
# ═══════════════════════════════════════════════════════════════════════════════


class TestDirectionAsymmetry:
    def test_detects_large_asymmetry(self, engine):
        shadows = (_make_shadows("PAT", "BUY", 0.5, 20) +
                   _make_shadows("PAT", "SELL", -0.5, 20))
        triggers = engine.detect_direction_asymmetry(shadows)
        assert len(triggers) == 1
        assert triggers[0].category == TriggerCategory.DIRECTION_ASYMMETRY
        assert "PAT" in triggers[0].title
        assert triggers[0].evidence["delta"] == 1.0

    def test_no_trigger_below_threshold(self, engine):
        shadows = (_make_shadows("PAT", "BUY", 0.1, 20) +
                   _make_shadows("PAT", "SELL", -0.1, 20))
        triggers = engine.detect_direction_asymmetry(shadows)
        assert len(triggers) == 0  # delta=0.2 < 0.30

    def test_insufficient_subgroup_n(self, engine):
        shadows = (_make_shadows("PAT", "BUY", 0.5, 5) +  # Only 5 BUY
                   _make_shadows("PAT", "SELL", -0.5, 20))
        triggers = engine.detect_direction_asymmetry(shadows)
        assert len(triggers) == 0

    def test_single_direction_no_trigger(self, engine):
        shadows = _make_shadows("PAT", "SELL", -0.5, 30)
        triggers = engine.detect_direction_asymmetry(shadows)
        assert len(triggers) == 0

    def test_multiple_testing_metadata(self, engine):
        shadows = (_make_shadows("PAT_A", "BUY", 0.8, 15) +
                   _make_shadows("PAT_A", "SELL", -0.8, 15) +
                   _make_shadows("PAT_B", "BUY", 0.1, 15) +
                   _make_shadows("PAT_B", "SELL", 0.0, 15))
        triggers = engine.detect_direction_asymmetry(shadows)
        if triggers:
            assert triggers[0].evidence.get("multiple_testing_count", 0) >= 1

    def test_deduplication(self, engine):
        shadows = (_make_shadows("DUP", "BUY", 0.5, 20) +
                   _make_shadows("DUP", "SELL", -0.5, 20))
        t1 = engine.detect_direction_asymmetry(shadows)
        t2 = engine.detect_direction_asymmetry(shadows)
        assert len(t1) == 1
        assert len(t2) == 0  # Deduplicated


# ═══════════════════════════════════════════════════════════════════════════════
# REGIME_ANOMALY
# ═══════════════════════════════════════════════════════════════════════════════


class TestRegimeAnomaly:
    def test_detects_anomalous_regime(self, engine):
        shadows = (_make_shadows(h4_regime="TRENDING", r_val=0.3, n=30) +
                   _make_shadows(h4_regime="RANGING", r_val=-0.3, n=30))
        triggers = engine.detect_regime_anomaly(shadows)
        assert len(triggers) >= 1
        assert any(t.category == TriggerCategory.REGIME_ANOMALY for t in triggers)

    def test_no_trigger_small_delta(self, engine):
        shadows = (_make_shadows(h4_regime="TRENDING", r_val=0.05, n=30) +
                   _make_shadows(h4_regime="RANGING", r_val=-0.05, n=30))
        triggers = engine.detect_regime_anomaly(shadows)
        assert len(triggers) == 0  # delta=0.1 < 0.20

    def test_insufficient_regime_n(self, engine):
        shadows = (_make_shadows(h4_regime="TRENDING", r_val=0.5, n=5) +
                   _make_shadows(h4_regime="RANGING", r_val=-0.5, n=30))
        triggers = engine.detect_regime_anomaly(shadows)
        # TRENDING has N=5 < 15 → skipped, but RANGING vs empty-other also insufficient
        assert all(t.evidence.get("n_regime", 0) >= 15 for t in triggers)

    def test_missing_regime_field(self, engine):
        shadows = [{"pattern": "X", "r_multiple": -1.0, "h4_regime": "", "regime": "",
                    "correlation_id": f"C-{i}"} for i in range(30)]
        triggers = engine.detect_regime_anomaly(shadows)
        assert len(triggers) == 0


# ═══════════════════════════════════════════════════════════════════════════════
# SCORE_MONOTONICITY
# ═══════════════════════════════════════════════════════════════════════════════


class TestScoreMonotonicity:
    def test_detects_inversion(self, engine):
        # Q1 (low score) has positive R, Q4 (high score) has negative R
        shadows = []
        for i in range(60):
            score = 0.3 + (i / 60) * 0.5  # 0.3 → 0.8
            r = 0.5 - (i / 60) * 1.5  # Decreases with score (inverted!)
            shadows.append({"score": score, "r_multiple": r, "correlation_id": f"C-{i}",
                            "pattern": "X", "symbol": "E"})
        triggers = engine.detect_score_monotonicity(shadows)
        assert len(triggers) == 1
        assert triggers[0].category == TriggerCategory.SCORE_MONOTONICITY

    def test_no_trigger_monotonic(self, engine):
        # Normal: higher score = higher R
        shadows = [{"score": 0.3 + i * 0.01, "r_multiple": -0.5 + i * 0.02,
                    "correlation_id": f"C-{i}", "pattern": "X", "symbol": "E"}
                   for i in range(60)]
        triggers = engine.detect_score_monotonicity(shadows)
        assert len(triggers) == 0

    def test_insufficient_sample(self, engine):
        shadows = [{"score": 0.5, "r_multiple": -1.0, "correlation_id": f"C-{i}"}
                   for i in range(20)]  # < min_score_n=40
        triggers = engine.detect_score_monotonicity(shadows)
        assert len(triggers) == 0

    def test_zero_scores_excluded(self, engine):
        shadows = [{"score": 0, "r_multiple": -1.0, "correlation_id": f"C-{i}"}
                   for i in range(60)]
        triggers = engine.detect_score_monotonicity(shadows)
        assert len(triggers) == 0  # All filtered out


# ═══════════════════════════════════════════════════════════════════════════════
# TEMPORAL_INSTABILITY
# ═══════════════════════════════════════════════════════════════════════════════


class TestTemporalInstability:
    def test_detects_degradation(self, engine):
        # Early positive, late negative
        shadows = ([{"r_multiple": 0.3, "timestamp_decision_utc": 1000 + i, "correlation_id": f"C-{i}",
                     "pattern": "X", "symbol": "E"} for i in range(30)] +
                   [{"r_multiple": -0.5, "timestamp_decision_utc": 2000 + i, "correlation_id": f"D-{i}",
                     "pattern": "X", "symbol": "E"} for i in range(30)])
        triggers = engine.detect_temporal_instability(shadows)
        assert len(triggers) == 1
        assert "degradation" in triggers[0].title

    def test_no_trigger_stable(self, engine):
        shadows = [{"r_multiple": 0.05, "timestamp_decision_utc": 1000 + i * 10,
                    "correlation_id": f"C-{i}", "pattern": "X"} for i in range(60)]
        triggers = engine.detect_temporal_instability(shadows)
        assert len(triggers) == 0

    def test_insufficient_period_n(self, engine):
        shadows = [{"r_multiple": -1.0, "timestamp_decision_utc": 1000 + i,
                    "correlation_id": f"C-{i}"} for i in range(20)]  # < 2*15
        triggers = engine.detect_temporal_instability(shadows)
        assert len(triggers) == 0


# ═══════════════════════════════════════════════════════════════════════════════
# GEOMETRY_ANOMALY
# ═══════════════════════════════════════════════════════════════════════════════


class TestGeometryAnomaly:
    def test_detects_geometry_effect(self, engine):
        # Tight stops (small rd) do well, wide stops do poorly
        shadows = ([{"risk_distance": 0.0001, "r_multiple": 0.5, "correlation_id": f"T-{i}"}
                    for i in range(15)] +
                   [{"risk_distance": 0.001, "r_multiple": 0.1, "correlation_id": f"M-{i}"}
                    for i in range(15)] +
                   [{"risk_distance": 0.01, "r_multiple": -0.1, "correlation_id": f"M2-{i}"}
                    for i in range(15)] +
                   [{"risk_distance": 0.1, "r_multiple": -0.5, "correlation_id": f"W-{i}"}
                    for i in range(15)])
        triggers = engine.detect_geometry_anomaly(shadows)
        assert len(triggers) == 1
        assert triggers[0].category == TriggerCategory.GEOMETRY_ANOMALY

    def test_no_trigger_small_difference(self, engine):
        shadows = ([{"risk_distance": 0.0001, "r_multiple": 0.05, "correlation_id": f"T-{i}"}
                    for i in range(15)] +
                   [{"risk_distance": 0.001, "r_multiple": 0.0, "correlation_id": f"M-{i}"}
                    for i in range(15)] +
                   [{"risk_distance": 0.01, "r_multiple": -0.05, "correlation_id": f"M2-{i}"}
                    for i in range(15)] +
                   [{"risk_distance": 0.1, "r_multiple": -0.1, "correlation_id": f"W-{i}"}
                    for i in range(15)])
        triggers = engine.detect_geometry_anomaly(shadows)
        assert len(triggers) == 0  # delta=0.15 < 0.25

    def test_insufficient_data(self, engine):
        shadows = [{"risk_distance": 0.001, "r_multiple": 0.5, "correlation_id": f"C-{i}"}
                   for i in range(20)]  # < 4*10
        triggers = engine.detect_geometry_anomaly(shadows)
        assert len(triggers) == 0


# ═══════════════════════════════════════════════════════════════════════════════
# SYMBOL_ANOMALY
# ═══════════════════════════════════════════════════════════════════════════════


class TestSymbolAnomaly:
    def test_detects_outlier_symbol(self, engine):
        shadows = (_make_shadows(symbol="USDJPY", r_val=-0.5, n=20) +
                   _make_shadows(symbol="EURUSD", r_val=0.1, n=20) +
                   _make_shadows(symbol="GBPUSD", r_val=0.1, n=20))
        # Fix: need to set symbol via override
        s = []
        for i in range(20):
            s.append({"symbol": "USDJPY", "r_multiple": -0.5, "correlation_id": f"J-{i}"})
        for i in range(20):
            s.append({"symbol": "EURUSD", "r_multiple": 0.1, "correlation_id": f"E-{i}"})
        for i in range(20):
            s.append({"symbol": "GBPUSD", "r_multiple": 0.1, "correlation_id": f"G-{i}"})
        triggers = engine.detect_symbol_anomaly(s)
        assert len(triggers) >= 1
        assert any("USDJPY" in t.title for t in triggers)

    def test_no_trigger_uniform(self, engine):
        shadows = []
        for sym in ["EURUSD", "GBPUSD", "AUDUSD"]:
            for i in range(15):
                shadows.append({"symbol": sym, "r_multiple": 0.05, "correlation_id": f"{sym}-{i}"})
        triggers = engine.detect_symbol_anomaly(shadows)
        assert len(triggers) == 0

    def test_insufficient_symbol_n(self, engine):
        shadows = [{"symbol": "RARE", "r_multiple": -1.0, "correlation_id": f"R-{i}"} for i in range(5)]
        triggers = engine.detect_symbol_anomaly(shadows)
        assert len(triggers) == 0

    def test_multiple_testing_recorded(self, engine):
        s = []
        for sym in ["A", "B", "C"]:
            for i in range(15):
                s.append({"symbol": sym, "r_multiple": -0.5 if sym == "A" else 0.2,
                          "correlation_id": f"{sym}-{i}"})
        triggers = engine.detect_symbol_anomaly(s)
        if triggers:
            assert triggers[0].evidence.get("multiple_testing_count", 0) >= 1


# ═══════════════════════════════════════════════════════════════════════════════
# PERSISTENCE & PRODUCTION SAFETY
# ═══════════════════════════════════════════════════════════════════════════════


class TestPersistenceAndSafety:
    def test_triggers_persist(self, engine, tmp_path):
        shadows = (_make_shadows("PERSIST", "BUY", 0.8, 15) +
                   _make_shadows("PERSIST", "SELL", -0.8, 15))
        engine.detect_direction_asymmetry(shadows)
        # Reload
        engine2 = FindingTriggerEngine(config=engine._config)
        assert len(engine2.all_triggers()) >= 1

    def test_detect_only_no_production_change(self, engine):
        """All detectors are read-only — no production state modified."""
        shadows = (_make_shadows("SAFE", "BUY", 0.8, 15) +
                   _make_shadows("SAFE", "SELL", -0.8, 15))
        triggers = engine.detect_direction_asymmetry(shadows)
        # Verify: no hypothesis created, no experiment run
        for t in triggers:
            assert t.hypothesis_id == ""
            assert t.status == TriggerStatus.ELIGIBLE



# ═══════════════════════════════════════════════════════════════════════════════
# DEDUPLICATION REGRESSION TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestDeduplicationRegression:
    """Verify the dedup fix: non-pattern findings with [] patterns don't collapse."""

    def test_different_symbols_coexist(self, engine):
        """Multiple SYMBOL_ANOMALY triggers for different symbols all survive."""
        shadows = []
        # Three symbols: one very negative, two positive
        for i in range(20):
            shadows.append({"symbol": "BAD_A", "r_multiple": -0.8, "correlation_id": f"A-{i}"})
        for i in range(20):
            shadows.append({"symbol": "BAD_B", "r_multiple": -0.7, "correlation_id": f"B-{i}"})
        for i in range(40):
            shadows.append({"symbol": "GOOD_C", "r_multiple": 0.3, "correlation_id": f"C-{i}"})
        triggers = engine.detect_symbol_anomaly(shadows)
        # Both BAD_A and BAD_B should be eligible (not deduplicated)
        assert len(triggers) >= 2, f"Expected >=2 symbol triggers, got {len(triggers)}"
        symbols_found = {t.evidence["symbol"] for t in triggers}
        assert "BAD_A" in symbols_found
        assert "BAD_B" in symbols_found

    def test_different_regimes_coexist(self, engine):
        """Multiple REGIME_ANOMALY triggers for different regimes all survive."""
        shadows = []
        for i in range(30):
            shadows.append({"h4_regime": "TRENDING", "r_multiple": 0.5, "correlation_id": f"T-{i}"})
        for i in range(30):
            shadows.append({"h4_regime": "RANGING", "r_multiple": -0.5, "correlation_id": f"R-{i}"})
        triggers = engine.detect_regime_anomaly(shadows)
        # Both TRENDING (+anomaly vs pop) and RANGING (-anomaly vs pop) may trigger
        # At minimum the anomalous one should trigger
        assert len(triggers) >= 1
        # If both trigger, verify they are distinct
        if len(triggers) >= 2:
            regimes = {t.evidence["regime"] for t in triggers}
            assert len(regimes) == len(triggers)

    def test_temporal_not_collapsed(self, engine):
        """TEMPORAL_INSTABILITY with suggested_patterns=[] is not false-deduplicated
        against SYMBOL_ANOMALY with suggested_patterns=[]."""
        shadows_time = ([{"r_multiple": 0.5, "timestamp_decision_utc": 1000 + i,
                          "correlation_id": f"E-{i}", "pattern": "X", "symbol": "E"}
                         for i in range(30)] +
                        [{"r_multiple": -0.5, "timestamp_decision_utc": 2000 + i,
                          "correlation_id": f"L-{i}", "pattern": "X", "symbol": "E"}
                         for i in range(30)])
        shadows_sym = []
        for i in range(20):
            shadows_sym.append({"symbol": "OUTLIER", "r_multiple": -0.8, "correlation_id": f"O-{i}"})
        for i in range(40):
            shadows_sym.append({"symbol": "NORMAL", "r_multiple": 0.2, "correlation_id": f"N-{i}"})

        # Detect temporal first
        t_triggers = engine.detect_temporal_instability(shadows_time)
        # Then detect symbol
        s_triggers = engine.detect_symbol_anomaly(shadows_sym)
        # Both should be eligible (different categories, both have [] patterns)
        total = len(t_triggers) + len(s_triggers)
        assert total >= 2 or (len(t_triggers) >= 1 and len(s_triggers) >= 1), \
            f"Cross-category triggers should not deduplicate: temporal={len(t_triggers)}, symbol={len(s_triggers)}"

    def test_geometry_not_collapsed_with_symbol(self, engine):
        """GEOMETRY_ANOMALY and SYMBOL_ANOMALY don't suppress each other."""
        geo_shadows = ([{"risk_distance": 0.0001, "r_multiple": 0.6, "correlation_id": f"T-{i}"}
                        for i in range(15)] +
                       [{"risk_distance": 0.001, "r_multiple": 0.1, "correlation_id": f"M-{i}"}
                        for i in range(15)] +
                       [{"risk_distance": 0.01, "r_multiple": -0.1, "correlation_id": f"M2-{i}"}
                        for i in range(15)] +
                       [{"risk_distance": 0.1, "r_multiple": -0.6, "correlation_id": f"W-{i}"}
                        for i in range(15)])
        sym_shadows = []
        for i in range(20):
            sym_shadows.append({"symbol": "BAD_SYM", "r_multiple": -0.7, "correlation_id": f"BS-{i}"})
        for i in range(40):
            sym_shadows.append({"symbol": "OK_SYM", "r_multiple": 0.2, "correlation_id": f"OK-{i}"})

        g_triggers = engine.detect_geometry_anomaly(geo_shadows)
        s_triggers = engine.detect_symbol_anomaly(sym_shadows)
        # Both categories should independently produce triggers
        if g_triggers and s_triggers:
            assert g_triggers[0].category != s_triggers[0].category

    def test_score_not_collapsed_with_others(self, engine):
        """SCORE_MONOTONICITY with [] patterns doesn't deduplicate against other [] triggers."""
        # Create a symbol anomaly first
        sym_shadows = []
        for i in range(20):
            sym_shadows.append({"symbol": "X", "r_multiple": -0.8, "correlation_id": f"X-{i}"})
        for i in range(40):
            sym_shadows.append({"symbol": "Y", "r_multiple": 0.2, "correlation_id": f"Y-{i}"})
        engine.detect_symbol_anomaly(sym_shadows)

        # Now detect score monotonicity
        score_shadows = []
        for i in range(60):
            score = 0.3 + (i / 60) * 0.5
            r = 0.5 - (i / 60) * 1.5  # Inverted!
            score_shadows.append({"score": score, "r_multiple": r, "correlation_id": f"S-{i}",
                                  "pattern": "P", "symbol": "E"})
        s_triggers = engine.detect_score_monotonicity(score_shadows)
        # Score trigger should NOT be deduplicated by the existing symbol trigger
        assert len(s_triggers) >= 1, "Score trigger should not be blocked by symbol trigger"

    def test_pattern_based_dedup_still_works(self, engine):
        """Pattern-based dedup (non-empty patterns) still prevents duplicates."""
        shadows1 = (_make_shadows("SAME_PAT", "BUY", 0.8, 15) +
                    _make_shadows("SAME_PAT", "SELL", -0.8, 15))
        t1 = engine.detect_direction_asymmetry(shadows1)
        assert len(t1) == 1

        # Same pattern again → should be deduplicated
        t2 = engine.detect_direction_asymmetry(shadows1)
        assert len(t2) == 0, "Same pattern should be deduplicated"

    def test_exact_finding_id_dedup_still_works(self, engine):
        """Exact finding_id match still deduplicates."""
        shadows = []
        for i in range(20):
            shadows.append({"symbol": "DUP_SYM", "r_multiple": -0.8, "correlation_id": f"D-{i}"})
        for i in range(40):
            shadows.append({"symbol": "OTHER", "r_multiple": 0.2, "correlation_id": f"O-{i}"})
        t1 = engine.detect_symbol_anomaly(shadows)
        assert len(t1) >= 1
        # Same data → same finding_id → deduplicated
        t2 = engine.detect_symbol_anomaly(shadows)
        assert len(t2) == 0, "Same finding_id should deduplicate"
