"""
Unit tests for ObserverRegistry — pipeline observer dispatch.

Tests:
    - Every observer receives notification
    - Ordering preserved
    - Exception in one observer does not stop remaining observers
    - Arguments passed correctly
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.pipeline.observers import ObserverRegistry, ObserverContext


# ─── FIXTURES ─────────────────────────────────────────────────────────────────


def _make_context(**overrides) -> ObserverContext:
    """Build a default ObserverContext with sensible test values."""
    defaults = dict(
        symbol="EURUSD",
        cycle_id=42,
        bar_time=1700000000.0,
        engine_result={"action": "NO_TRADE", "reason": "test"},
        engine_state=MagicMock(bias_phase="EXPANSION"),
        candles=[MagicMock()],
        closed_i=0,
        bid=1.1000,
        ask=1.1002,
        config=MagicMock(),
        detected_patterns=["pattern_a"],
        risk_manager=MagicMock(),
        htf_context=MagicMock(),
        runtime_session_id="abc123",
        decision_funnel=MagicMock(),
    )
    defaults.update(overrides)
    return ObserverContext(**defaults)


# ─── TESTS ────────────────────────────────────────────────────────────────────


class TestAllObserversNotified:
    """Every observer receives notification when notify_all is called."""

    @patch("core.decision_trace.persist_decision_trace")
    @patch("core.decision_trace.build_decision_trace")
    @patch("core.pipeline.shadow_rooms.run_shadow_rooms")
    @patch("core.pipeline.visibility_layer.emit_visibility_trace")
    @patch("core.pipeline.entity_tracker.track_opportunity")
    @patch("core.pipeline.forensic_logger.log_full_cycle")
    @patch("core.pipeline.event_observer.observe_engine_output")
    def test_all_observers_called(self, mock_event, mock_forensic, mock_entity,
                                   mock_vis, mock_shadow, mock_trace_build, mock_trace_persist):
        """All 6 observers are invoked."""
        mock_trace_build.return_value = {"trace": "data"}
        ctx = _make_context()
        registry = ObserverRegistry()

        registry.notify_all(ctx)

        mock_event.assert_called_once_with(ctx.engine_result)
        mock_forensic.assert_called_once()
        mock_entity.assert_called_once()
        mock_vis.assert_called_once()
        mock_shadow.assert_called_once()
        mock_trace_build.assert_called_once()
        mock_trace_persist.assert_called_once_with({"trace": "data"})
        ctx.decision_funnel.record_trace.assert_called_once_with({"trace": "data"})

    @patch("core.decision_trace.persist_decision_trace")
    @patch("core.decision_trace.build_decision_trace")
    @patch("core.pipeline.shadow_rooms.run_shadow_rooms")
    @patch("core.pipeline.visibility_layer.emit_visibility_trace")
    @patch("core.pipeline.entity_tracker.track_opportunity")
    @patch("core.pipeline.forensic_logger.log_full_cycle")
    @patch("core.pipeline.event_observer.observe_engine_output")
    def test_forensic_logger_args(self, mock_event, mock_forensic, mock_entity,
                                   mock_vis, mock_shadow, mock_trace_build, mock_trace_persist):
        """Forensic logger receives symbol, cycle_id, engine_result, mt5_time."""
        mock_trace_build.return_value = {"trace": "data"}
        ctx = _make_context()
        registry = ObserverRegistry()

        registry.notify_all(ctx)

        mock_forensic.assert_called_once_with(
            symbol="EURUSD",
            cycle_id=42,
            engine_result=ctx.engine_result,
            mt5_time=1700000000.0,
        )

    @patch("core.decision_trace.persist_decision_trace")
    @patch("core.decision_trace.build_decision_trace")
    @patch("core.pipeline.shadow_rooms.run_shadow_rooms")
    @patch("core.pipeline.visibility_layer.emit_visibility_trace")
    @patch("core.pipeline.entity_tracker.track_opportunity")
    @patch("core.pipeline.forensic_logger.log_full_cycle")
    @patch("core.pipeline.event_observer.observe_engine_output")
    def test_entity_tracker_args(self, mock_event, mock_forensic, mock_entity,
                                  mock_vis, mock_shadow, mock_trace_build, mock_trace_persist):
        """Entity tracker receives symbol, bar_time, engine_result, cycle_id."""
        mock_trace_build.return_value = {"trace": "data"}
        ctx = _make_context()
        registry = ObserverRegistry()

        registry.notify_all(ctx)

        mock_entity.assert_called_once_with(
            symbol="EURUSD",
            bar_time=1700000000.0,
            engine_result=ctx.engine_result,
            cycle_id=42,
        )

    @patch("core.decision_trace.persist_decision_trace")
    @patch("core.decision_trace.build_decision_trace")
    @patch("core.pipeline.shadow_rooms.run_shadow_rooms")
    @patch("core.pipeline.visibility_layer.emit_visibility_trace")
    @patch("core.pipeline.entity_tracker.track_opportunity")
    @patch("core.pipeline.forensic_logger.log_full_cycle")
    @patch("core.pipeline.event_observer.observe_engine_output")
    def test_visibility_layer_args(self, mock_event, mock_forensic, mock_entity,
                                    mock_vis, mock_shadow, mock_trace_build, mock_trace_persist):
        """Visibility layer receives symbol, cycle_id, bar_time, engine_result, bias_phase."""
        mock_trace_build.return_value = {"trace": "data"}
        ctx = _make_context()
        registry = ObserverRegistry()

        registry.notify_all(ctx)

        mock_vis.assert_called_once_with(
            symbol="EURUSD",
            cycle_id=42,
            bar_time=1700000000.0,
            engine_result=ctx.engine_result,
            bias_phase="EXPANSION",
        )

    @patch("core.decision_trace.persist_decision_trace")
    @patch("core.decision_trace.build_decision_trace")
    @patch("core.pipeline.shadow_rooms.run_shadow_rooms")
    @patch("core.pipeline.visibility_layer.emit_visibility_trace")
    @patch("core.pipeline.entity_tracker.track_opportunity")
    @patch("core.pipeline.forensic_logger.log_full_cycle")
    @patch("core.pipeline.event_observer.observe_engine_output")
    def test_shadow_rooms_args(self, mock_event, mock_forensic, mock_entity,
                                mock_vis, mock_shadow, mock_trace_build, mock_trace_persist):
        """Shadow rooms receives full argument set."""
        mock_trace_build.return_value = {"trace": "data"}
        ctx = _make_context()
        registry = ObserverRegistry()

        registry.notify_all(ctx)

        mock_shadow.assert_called_once_with(
            symbol="EURUSD",
            cycle_id=42,
            bar_time=1700000000.0,
            candles=ctx.candles,
            closed_i=0,
            bid=1.1000,
            ask=1.1002,
            engine_state=ctx.engine_state,
            config=ctx.config,
            detected_patterns=["pattern_a"],
            risk_manager=ctx.risk_manager,
            htf_context=ctx.htf_context,
            live_engine_result=ctx.engine_result,
        )

    @patch("core.decision_trace.persist_decision_trace")
    @patch("core.decision_trace.build_decision_trace")
    @patch("core.pipeline.shadow_rooms.run_shadow_rooms")
    @patch("core.pipeline.visibility_layer.emit_visibility_trace")
    @patch("core.pipeline.entity_tracker.track_opportunity")
    @patch("core.pipeline.forensic_logger.log_full_cycle")
    @patch("core.pipeline.event_observer.observe_engine_output")
    def test_decision_trace_args(self, mock_event, mock_forensic, mock_entity,
                                  mock_vis, mock_shadow, mock_trace_build, mock_trace_persist):
        """Decision trace is built with engine_result, runtime_session_id, pattern_count."""
        mock_trace_build.return_value = {"trace": "data"}
        ctx = _make_context()
        registry = ObserverRegistry()

        registry.notify_all(ctx)

        mock_trace_build.assert_called_once_with(
            engine_result=ctx.engine_result,
            runtime_session_id="abc123",
            pattern_count=1,
            v10_pipeline_result=None,
            observation_id="",
            decision_id="",
            correlation_id="",
        )
        mock_trace_persist.assert_called_once_with({"trace": "data"})
        ctx.decision_funnel.record_trace.assert_called_once_with({"trace": "data"})


class TestObserverIsolation:
    """Exception in one observer does not stop remaining observers."""

    @patch("core.decision_trace.persist_decision_trace")
    @patch("core.decision_trace.build_decision_trace")
    @patch("core.pipeline.shadow_rooms.run_shadow_rooms")
    @patch("core.pipeline.visibility_layer.emit_visibility_trace")
    @patch("core.pipeline.entity_tracker.track_opportunity")
    @patch("core.pipeline.forensic_logger.log_full_cycle")
    @patch("core.pipeline.event_observer.observe_engine_output", side_effect=RuntimeError("boom"))
    def test_first_observer_failure_does_not_block_others(
        self, mock_event, mock_forensic, mock_entity,
        mock_vis, mock_shadow, mock_trace_build, mock_trace_persist
    ):
        """If event_observer raises, remaining observers still fire."""
        mock_trace_build.return_value = {"trace": "data"}
        ctx = _make_context()
        registry = ObserverRegistry()

        registry.notify_all(ctx)

        # All subsequent observers still called
        mock_forensic.assert_called_once()
        mock_entity.assert_called_once()
        mock_vis.assert_called_once()
        mock_shadow.assert_called_once()
        mock_trace_build.assert_called_once()

    @patch("core.decision_trace.persist_decision_trace")
    @patch("core.decision_trace.build_decision_trace")
    @patch("core.pipeline.shadow_rooms.run_shadow_rooms")
    @patch("core.pipeline.visibility_layer.emit_visibility_trace", side_effect=RuntimeError("vis_fail"))
    @patch("core.pipeline.entity_tracker.track_opportunity")
    @patch("core.pipeline.forensic_logger.log_full_cycle")
    @patch("core.pipeline.event_observer.observe_engine_output")
    def test_middle_observer_failure_does_not_block_later(
        self, mock_event, mock_forensic, mock_entity,
        mock_vis, mock_shadow, mock_trace_build, mock_trace_persist
    ):
        """If visibility_layer raises, shadow_rooms and decision_trace still fire."""
        mock_trace_build.return_value = {"trace": "data"}
        ctx = _make_context()
        registry = ObserverRegistry()

        registry.notify_all(ctx)

        mock_shadow.assert_called_once()
        mock_trace_build.assert_called_once()

    def test_all_observers_fail_no_exception_raised(self):
        """Even if every observer raises, notify_all never raises."""
        ctx = _make_context()
        registry = ObserverRegistry()

        with patch("core.pipeline.event_observer.observe_engine_output", side_effect=Exception("1")), \
             patch("core.pipeline.forensic_logger.log_full_cycle", side_effect=Exception("2")), \
             patch("core.pipeline.entity_tracker.track_opportunity", side_effect=Exception("3")), \
             patch("core.pipeline.visibility_layer.emit_visibility_trace", side_effect=Exception("4")), \
             patch("core.pipeline.shadow_rooms.run_shadow_rooms", side_effect=Exception("5")), \
             patch("core.decision_trace.build_decision_trace", side_effect=Exception("6")):
            # Should not raise
            registry.notify_all(ctx)


class TestObserverOrdering:
    """Observer dispatch order is preserved."""

    def test_dispatch_order(self):
        """Observers fire in correct sequence: event→forensic→entity→visibility→shadow→trace."""
        ctx = _make_context()
        registry = ObserverRegistry()
        call_order = []

        def _track(name):
            def _fn(*args, **kwargs):
                call_order.append(name)
                if name == "build_decision_trace":
                    return {"trace": "data"}
            return _fn

        with patch("core.pipeline.event_observer.observe_engine_output", side_effect=_track("event_observer")), \
             patch("core.pipeline.forensic_logger.log_full_cycle", side_effect=_track("forensic_logger")), \
             patch("core.pipeline.entity_tracker.track_opportunity", side_effect=_track("entity_tracker")), \
             patch("core.pipeline.visibility_layer.emit_visibility_trace", side_effect=_track("visibility_layer")), \
             patch("core.pipeline.shadow_rooms.run_shadow_rooms", side_effect=_track("shadow_rooms")), \
             patch("core.decision_trace.build_decision_trace", side_effect=_track("build_decision_trace")), \
             patch("core.decision_trace.persist_decision_trace", side_effect=_track("persist_decision_trace")):
            registry.notify_all(ctx)

        assert call_order == [
            "event_observer",
            "forensic_logger",
            "entity_tracker",
            "visibility_layer",
            "shadow_rooms",
            "build_decision_trace",
            "persist_decision_trace",
        ]


class TestBiasPhaseInjection:
    """Forensic logger receives _bias_phase injected into engine_result."""

    @patch("core.decision_trace.persist_decision_trace")
    @patch("core.decision_trace.build_decision_trace")
    @patch("core.pipeline.shadow_rooms.run_shadow_rooms")
    @patch("core.pipeline.visibility_layer.emit_visibility_trace")
    @patch("core.pipeline.entity_tracker.track_opportunity")
    @patch("core.pipeline.forensic_logger.log_full_cycle")
    @patch("core.pipeline.event_observer.observe_engine_output")
    def test_bias_phase_added_to_engine_result(
        self, mock_event, mock_forensic, mock_entity,
        mock_vis, mock_shadow, mock_trace_build, mock_trace_persist
    ):
        """engine_result['_bias_phase'] is set before forensic_logger is called."""
        mock_trace_build.return_value = {"trace": "data"}
        engine_result = {"action": "NO_TRADE", "reason": "test"}
        engine_state = MagicMock(bias_phase="CONTRACTION")
        ctx = _make_context(engine_result=engine_result, engine_state=engine_state)
        registry = ObserverRegistry()

        registry.notify_all(ctx)

        # Verify _bias_phase was injected
        assert engine_result["_bias_phase"] == "CONTRACTION"
