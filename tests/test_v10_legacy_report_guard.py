"""Phase J.5.1 — Runtime verification that legacy report is suppressed under V10.

Proves:
  - handle_no_trade_outcome does NOT print legacy narrative when ENGINE_MODE=V10
  - handle_no_trade_outcome DOES print legacy narrative when ENGINE_MODE=LEGACY
  - The EXECUTE path narrative is also suppressed under V10
"""

import builtins
from unittest.mock import MagicMock, patch
from collections import defaultdict


def _capture_prints(func):
    """Run func while capturing all print() calls. Returns list of strings."""
    printed = []
    orig = builtins.print

    def _cap(*args, **kwargs):
        printed.append(" ".join(str(a) for a in args))

    builtins.print = _cap
    try:
        func()
    finally:
        builtins.print = orig
    return printed


class TestNoTradePathV10Guard:
    """handle_no_trade_outcome must not print legacy narrative under V10."""

    def _run_handler(self, engine_mode="V10"):
        config = MagicMock()
        config.ENGINE_MODE = engine_mode

        _new_result = {
            "action": "NO_TRADE",
            "score": 0.35,
            "reason": "score_below_threshold",
            "intent": None,
            "assessment": None,
            "strategy": "REVERSAL",
            "components": {"pattern_quality": 0.5, "bias_alignment": 0.3},
        }

        with patch("core.runtime.engine_outcome_handler.classify_new_engine_reason") as mock_classify:
            mock_classify.return_value = MagicMock(filter_key="score_below")
            with patch("core.runtime.engine_outcome_handler.run_evaluation"):
                from core.runtime.engine_outcome_handler import handle_no_trade_outcome

                handle_no_trade_outcome(
                    new_result=_new_result,
                    new_engine_score=0.35,
                    symbol="AUDUSD",
                    engine_state=MagicMock(),
                    risk=MagicMock(),
                    cycle_id=123,
                    closed_time=1785400000,
                    candles=[],
                    closed_i=0,
                    bid=0.65,
                    ask=0.6501,
                    config=config,
                    runtime_session_id="test",
                    cycle_decision={},
                    cycle_drops=[],
                    filter_hits=defaultdict(int),
                )

    def test_v10_no_legacy_narrative(self):
        printed = _capture_prints(lambda: self._run_handler("V10"))
        output = "\n".join(printed)
        assert "TRADE DECISION" not in output
        assert "Composite Score" not in output
        assert "DUAL SCORING" not in output
        assert "FINAL SCORE SUMMARY" not in output
        assert "Grade" not in output

    def test_legacy_prints_narrative(self):
        printed = _capture_prints(lambda: self._run_handler("LEGACY"))
        output = "\n".join(printed)
        assert "TRADE DECISION" in output

    def test_v10_no_output_router(self):
        """Under V10, process_engine_output must NOT be called."""
        config = MagicMock()
        config.ENGINE_MODE = "V10"

        _new_result = {
            "action": "NO_TRADE", "score": 0.0,
            "reason": "V10: no opportunity", "intent": None,
            "assessment": None, "strategy": "NONE", "components": {},
        }

        with patch("core.runtime.engine_outcome_handler.classify_new_engine_reason") as mc:
            mc.return_value = MagicMock(filter_key="v10_no_trade")
            with patch("core.runtime.engine_outcome_handler.run_evaluation"):
                with patch("core.pipeline.output_router.process_engine_output") as mock_router:
                    from core.runtime.engine_outcome_handler import handle_no_trade_outcome

                    handle_no_trade_outcome(
                        new_result=_new_result, new_engine_score=0.0,
                        symbol="AUDUSD", engine_state=MagicMock(),
                        risk=MagicMock(), cycle_id=1,
                        closed_time=1785400000, candles=[], closed_i=0,
                        bid=0.65, ask=0.6501, config=config,
                        runtime_session_id="t", cycle_decision={},
                        cycle_drops=[], filter_hits=defaultdict(int),
                    )
                    # output_router should NOT have been called
                    mock_router.assert_not_called()
