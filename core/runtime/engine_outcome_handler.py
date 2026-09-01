"""
Engine Outcome Handler — Handles Engine A NO_TRADE outcomes.

Encapsulates the multi-step workflow for processing a NO_TRADE decision:
narrative generation, output routing, evaluation dispatch, decision state
finalization, and audit persistence.

This module OWNS:
    - Rejection narrative generation
    - Output routing (AWS/Discord)
    - Evaluation dispatch for NO_TRADE
    - Decision state mutation for NO_TRADE
    - Decision audit persistence
    - Reasoning/uncertainty/attribution attachment
    - Filter hit classification

This module does NOT own:
    - Engine execution
    - Trading decisions
    - Flow control (continue/break)
    - Decision finalization (caller finalizes)
    - Runtime loop

Design: handler function — mutates decision state, never controls flow.
"""

from __future__ import annotations

import logging
from typing import Any

from core.decision_ledger import DecisionOutcome
from core.runtime.filter_hit_classifier import classify_new_engine_reason
from core.evaluation.evaluation_runner import evaluate as run_evaluation, EvaluationContext

logger = logging.getLogger(__name__)


def handle_no_trade_outcome(
    *,
    new_result: dict,
    new_engine_score: float,
    symbol: str,
    engine_state: Any,
    risk: Any,
    cycle_id: int,
    closed_time: int,
    candles: Any,
    closed_i: int,
    bid: float,
    ask: float,
    config: Any,
    runtime_session_id: str,
    cycle_decision: dict,
    cycle_drops: list,
    filter_hits: dict[str, int],
    observation_id: str = "",
    correlation_id: str = "",
) -> None:
    """
    Handle Engine A NO_TRADE outcome. Mutates cycle_decision, cycle_drops, filter_hits.

    Performs:
        1. Append to cycle_drops
        2. Classify and increment filter hits
        3. Generate trade narrative (fire-and-forget)
        4. Route to AWS/Discord (fire-and-forget)
        5. Run evaluation (legacy shadow if enabled)
        6. Set decision state fields
        7. Persist decision audit
        8. Attach reasoning/uncertainty/attribution metadata

    After this function returns, the caller should call _finalize_decision() + continue.

    Never raises.
    """
    try:
        # 1. Cycle drops
        cycle_drops.append((symbol, "V10", new_result.get("reason", "?")))

        # 2. Filter hit classification
        _ne_reason = new_result.get("reason", "")
        _fh_result = classify_new_engine_reason(_ne_reason)
        filter_hits[_fh_result.filter_key] += 1

        # 3. Trade narrative (passive, read-only)
        # V10 mode has its own formatter — skip legacy narrative
        _narrative_text = None
        _is_v10 = getattr(config, "ENGINE_MODE", "LEGACY") == "V10"
        if not _is_v10:
            try:
                from core.pipeline.trade_narrative import build_trade_narrative
                _narrative_text = build_trade_narrative(
                    symbol=symbol,
                    decision=new_result,
                    engine_state=engine_state,
                    cycle_id=cycle_id,
                    mt5_time=float(closed_time),
                )
                print(_narrative_text)
            except Exception:
                pass

        # 4. Route to AWS + Discord (passive)
        if not _is_v10:
            try:
                from core.pipeline.output_router import process_engine_output
                process_engine_output(
                    symbol=symbol,
                    decision=new_result,
                    engine_state=engine_state,
                    cycle_id=cycle_id,
                    audit_output=None,
                    narrative_output=_narrative_text,
                )
            except Exception:
                pass

        # 5. Evaluation (legacy shadow comparison if enabled)
        run_evaluation(EvaluationContext(
            cycle_id=cycle_id, symbol=symbol,
            closed_time=closed_time, candles=candles, closed_i=closed_i,
            bid=bid, ask=ask, config=config, risk=risk,
            engine_state=engine_state, htf_context=None,
            new_engine_result=new_result, new_engine_score=new_engine_score,
            new_engine_action="NO_TRADE",
        ))

        # 6. Set decision state
        cycle_decision["decision"] = DecisionOutcome.NO_TRADE
        cycle_decision["reason"] = new_result.get("reason", "")
        cycle_decision["entity_id"] = new_result.get("entity_id", "")
        _assessment = new_result.get("assessment")
        if _assessment:
            cycle_decision["signal_score"] = _assessment.score_strategy
            cycle_decision["signal_score_semantic"] = "assessment_strategy_weighted_score"
            cycle_decision["assessment_strategy_weighted_score"] = _assessment.score_strategy
            cycle_decision["signal_type"] = _assessment.pattern
            cycle_decision["regime"] = _assessment.regime
        else:
            cycle_decision["signal_score"] = new_engine_score
            cycle_decision["signal_score_semantic"] = "engine_score_legacy_projection"
            cycle_decision["signal_type"] = new_result.get("strategy", None)
        cycle_decision["pattern_state"] = "detected"
        cycle_decision["last_stage"] = new_result.get("reason", "").split(":")[0] if ":" in new_result.get("reason", "") else "V10"

        # 7. Decision audit
        try:
            from core.decision_audit import persist_new_engine_decision_audit
            persist_new_engine_decision_audit(
                symbol=symbol,
                cycle_id=cycle_id,
                engine_result=new_result,
                engine_state=engine_state,
                candles=candles,
                closed_i=closed_i,
                entity_id=new_result.get("entity_id", ""),
                observation_id=observation_id,
                correlation_id=correlation_id,
                canonical_opportunity_id=new_result.get("canonical_opportunity_id", ""),
                strategy_ts_utc_ms=new_result.get("strategy_ts_utc_ms", 0),
                runtime_session_id=runtime_session_id,
            )
        except Exception:
            pass  # Audit failure must never block trading

        # 8. Attach reasoning metadata
        _reasoning_obj = new_result.get("reasoning")
        if _reasoning_obj and hasattr(_reasoning_obj, "to_dict"):
            cycle_decision["reasoning"] = _reasoning_obj.to_dict()
        _uncertainty_obj = new_result.get("uncertainty")
        if _uncertainty_obj and hasattr(_uncertainty_obj, "to_dict"):
            cycle_decision["uncertainty"] = _uncertainty_obj.to_dict()
        _attribution_obj = new_result.get("attribution")
        if _attribution_obj and hasattr(_attribution_obj, "to_dict"):
            cycle_decision["score_attribution"] = _attribution_obj.to_dict()

        # 9. Attach dual EV comparison + feed promotion monitor
        _dual_ev = new_result.get("dual_ev")
        if _dual_ev:
            cycle_decision["dual_ev"] = _dual_ev
            try:
                from core.research_assessment.promotion_monitor import record_comparison
                record_comparison(_dual_ev)
            except Exception:
                pass

            # 10. Create research shadow trade for RESEARCH_WOULD_EXECUTE
            if _dual_ev.get("execution_difference") == "RESEARCH_WOULD_EXECUTE":
                _rejected = new_result.get("rejected_trade")
                if _rejected:
                    try:
                        from core.research_assessment.research_shadow_engine import open_research_trade
                        open_research_trade(
                            trade_id=f"research_{cycle_id}_{symbol}",
                            cycle_id=cycle_id,
                            symbol=symbol,
                            direction=_rejected["side"],
                            entry_price=_rejected["entry_reference"],
                            stop_loss=_rejected["sl"],
                            take_profit=_rejected["tp"],
                            entry_time=float(closed_time),
                            pattern=_rejected.get("pattern", ""),
                            candidate_id=_dual_ev.get("candidate_id", ""),
                            score=new_engine_score,
                        )
                    except Exception:
                        pass  # Research shadow must never affect production

    except Exception:
        # Handler failure must never crash the runtime
        # Decision state may be partially set — finalize will enforce invariants
        pass
