"""
Validation Hardening — Regime Source to Timeframe Mapping.

Protects the observability contract:
    regime_source = "H4_MARKET_CONTEXT" → regime_timeframe = "H4"
    regime_source = "M5_CLASSIFIER"     → regime_timeframe = "M5"
    regime_source = ""                  → regime_timeframe = ""

These invariants must NEVER silently break in future changes.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from core.decision_trace import build_decision_trace, DecisionTrace


# ─── HELPERS ──────────────────────────────────────────────────────────────────


def _make_engine_result(**overrides) -> dict:
    """Create a minimal valid engine_result dict with optional overrides."""
    base = {
        "action": "NO_TRADE",
        "reason": "score_below_threshold",
        "score": 0.4,
        "entity_id": "EURUSD_1000",
        "symbol": "EURUSD",
        "cycle_id": 1,
        "components": {"pattern_quality": 0.5, "bias_alignment": 0.5},
        "activation_regime": "TRANSITIONAL",
        "activation_regime_confidence": 0.3,
        "regime_source": "",
    }
    base.update(overrides)
    return base


# ─── CONTRACT: REGIME_SOURCE → REGIME_TIMEFRAME MAPPING ──────────────────────


class TestRegimeSourceToTimeframeMapping:
    """
    The mapping from regime_source to regime_timeframe is a fixed contract.
    If regime_source changes, regime_timeframe MUST reflect the correct timeframe.
    """

    def test_h4_market_context_maps_to_h4(self):
        """regime_source=H4_MARKET_CONTEXT → regime_timeframe=H4."""
        result = _make_engine_result(regime_source="H4_MARKET_CONTEXT")
        trace = build_decision_trace(engine_result=result)
        assert trace.regime_source == "H4_MARKET_CONTEXT"
        assert trace.regime_timeframe == "H4"

    def test_m5_classifier_maps_to_m5(self):
        """regime_source=M5_CLASSIFIER → regime_timeframe=M5."""
        result = _make_engine_result(regime_source="M5_CLASSIFIER")
        trace = build_decision_trace(engine_result=result)
        assert trace.regime_source == "M5_CLASSIFIER"
        assert trace.regime_timeframe == "M5"

    def test_empty_source_maps_to_empty_timeframe(self):
        """regime_source='' → regime_timeframe='' (pre-migration data)."""
        result = _make_engine_result(regime_source="")
        trace = build_decision_trace(engine_result=result)
        assert trace.regime_source == ""
        assert trace.regime_timeframe == ""

    def test_missing_source_maps_to_empty_timeframe(self):
        """Missing regime_source key → regime_timeframe='' (safe default)."""
        result = _make_engine_result()
        del result["regime_source"]
        trace = build_decision_trace(engine_result=result)
        assert trace.regime_source == ""
        assert trace.regime_timeframe == ""


# ─── CONTRACT: REGIME_SOURCE ONLY VALID VALUES ────────────────────────────────


class TestRegimeSourceAllowedValues:
    """
    Only known regime_source values should produce a timeframe.
    Unknown values must result in empty timeframe (fail-safe).
    """

    VALID_SOURCES = ("H4_MARKET_CONTEXT", "M5_CLASSIFIER", "")

    def test_all_valid_sources_produce_known_timeframe(self):
        """Every valid source maps to a known timeframe or empty string."""
        expected = {
            "H4_MARKET_CONTEXT": "H4",
            "M5_CLASSIFIER": "M5",
            "": "",
        }
        for source, expected_tf in expected.items():
            result = _make_engine_result(regime_source=source)
            trace = build_decision_trace(engine_result=result)
            assert trace.regime_timeframe == expected_tf, (
                f"regime_source={source!r} expected timeframe={expected_tf!r}, got={trace.regime_timeframe!r}"
            )

    def test_unknown_source_produces_empty_timeframe(self):
        """An unknown regime_source value must NOT produce a timeframe (fail-safe)."""
        result = _make_engine_result(regime_source="UNKNOWN_FUTURE_SOURCE")
        trace = build_decision_trace(engine_result=result)
        assert trace.regime_timeframe == "", (
            f"Unknown source should map to empty timeframe, got={trace.regime_timeframe!r}"
        )

    def test_none_source_handled_gracefully(self):
        """regime_source=None should not crash, should produce empty string."""
        result = _make_engine_result(regime_source=None)
        trace = build_decision_trace(engine_result=result)
        # Should not crash; timeframe should be empty
        assert trace.regime_timeframe == ""


# ─── CONTRACT: SERIALIZATION PRESERVES FIELDS ─────────────────────────────────


class TestSerializationContract:
    """
    The to_dict() output must always include regime_source and regime_timeframe.
    These fields must survive the serialization round-trip.
    """

    def test_to_dict_includes_regime_source(self):
        """to_dict() output must contain 'regime_source' key."""
        result = _make_engine_result(regime_source="H4_MARKET_CONTEXT")
        trace = build_decision_trace(engine_result=result)
        d = trace.to_dict()
        assert "regime_source" in d
        assert d["regime_source"] == "H4_MARKET_CONTEXT"

    def test_to_dict_includes_regime_timeframe(self):
        """to_dict() output must contain 'regime_timeframe' key."""
        result = _make_engine_result(regime_source="M5_CLASSIFIER")
        trace = build_decision_trace(engine_result=result)
        d = trace.to_dict()
        assert "regime_timeframe" in d
        assert d["regime_timeframe"] == "M5"

    def test_to_dict_fields_are_strings(self):
        """Both fields must serialize as strings (not None, not int)."""
        for source in ("H4_MARKET_CONTEXT", "M5_CLASSIFIER", ""):
            result = _make_engine_result(regime_source=source)
            trace = build_decision_trace(engine_result=result)
            d = trace.to_dict()
            assert isinstance(d["regime_source"], str)
            assert isinstance(d["regime_timeframe"], str)

    def test_to_dict_regime_fields_adjacent(self):
        """regime, regime_confidence, regime_source, regime_timeframe should all be present."""
        result = _make_engine_result(
            regime_source="H4_MARKET_CONTEXT",
            activation_regime="TRENDING",
            activation_regime_confidence=0.85,
        )
        trace = build_decision_trace(engine_result=result)
        d = trace.to_dict()
        assert "regime" in d
        assert "regime_confidence" in d
        assert "regime_source" in d
        assert "regime_timeframe" in d


# ─── CONTRACT: REGIME + SOURCE CONSISTENCY ────────────────────────────────────


class TestRegimeSourceConsistency:
    """
    When H4 is the source, regime must reflect what H4 classified.
    When M5 is the source, regime reflects M5 classification.
    """

    def test_h4_source_with_trending_regime(self):
        """H4 source should carry the H4-classified regime value."""
        result = _make_engine_result(
            regime_source="H4_MARKET_CONTEXT",
            activation_regime="TRENDING",
            activation_regime_confidence=0.9,
        )
        trace = build_decision_trace(engine_result=result)
        assert trace.regime == "TRENDING"
        assert trace.regime_source == "H4_MARKET_CONTEXT"
        assert trace.regime_confidence == 0.9

    def test_m5_source_with_transitional_regime(self):
        """M5 fallback should carry the M5-classified regime value."""
        result = _make_engine_result(
            regime_source="M5_CLASSIFIER",
            activation_regime="TRANSITIONAL",
            activation_regime_confidence=0.3,
        )
        trace = build_decision_trace(engine_result=result)
        assert trace.regime == "TRANSITIONAL"
        assert trace.regime_source == "M5_CLASSIFIER"
        assert trace.regime_confidence == 0.3

    def test_regime_values_are_valid_enums(self):
        """Regime must always be one of the known regime strings."""
        valid_regimes = {"TRENDING", "RANGE", "TRANSITIONAL", None}
        for source in ("H4_MARKET_CONTEXT", "M5_CLASSIFIER", ""):
            for regime in ("TRENDING", "RANGE", "TRANSITIONAL"):
                result = _make_engine_result(
                    regime_source=source,
                    activation_regime=regime,
                )
                trace = build_decision_trace(engine_result=result)
                assert trace.regime in valid_regimes


# ─── CONTRACT: ENGINE RESULT PASSTHROUGH ──────────────────────────────────────


class TestEngineResultPassthrough:
    """
    The regime_source field in engine_result must pass through to DecisionTrace
    without modification, transformation, or loss.
    """

    def test_regime_source_not_modified(self):
        """The trace must contain exactly what the engine produced."""
        for source in ("H4_MARKET_CONTEXT", "M5_CLASSIFIER"):
            result = _make_engine_result(regime_source=source)
            trace = build_decision_trace(engine_result=result)
            assert trace.regime_source == source

    def test_build_trace_never_invents_source(self):
        """If engine_result has no regime_source, trace must not invent one."""
        result = _make_engine_result()
        del result["regime_source"]
        trace = build_decision_trace(engine_result=result)
        assert trace.regime_source == ""
        assert trace.regime_timeframe == ""

    def test_build_trace_error_path_preserves_defaults(self):
        """On trace build failure, defaults must be empty strings (not None)."""
        # Pass garbage that might cause _build_trace to fail
        trace = build_decision_trace(engine_result={"action": "NO_TRADE", "entity_id": "X"})
        assert isinstance(trace.regime_source, str)
        assert isinstance(trace.regime_timeframe, str)
