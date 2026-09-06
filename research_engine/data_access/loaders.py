"""
Data Access Layer — Read-only dataset loaders for the Research Engine.

SOURCE OF TRUTH: S3 (via the shared S3ResearchDataSource). These loaders do NOT
read production source data from local ``logs/``. Local logs remain only for
live-runtime persistence/debugging and are not a research source.

Each loader asks the shared layer for a logical dataset by its production-contract
name; the shared layer owns bucket/prefix/schema resolution, pagination, symbol
and date pruning, JSONL decoding, deterministic ordering, run-level caching, and
explicit missing/failed-read semantics (a missing dataset returns an empty list;
an S3 error raises ResearchDataSourceError — never a silent local fallback).

Public function signatures are unchanged from the pre-migration local loaders so
existing callers (main.py, shadow_ev, edge_candidates, edge_attribution, tests)
keep working — only the SOURCE changed from logs/ to S3.
"""

from __future__ import annotations

import logging
from typing import Any

from research_engine.data_access.s3_source import get_default_source

logger = logging.getLogger(__name__)


def _read(dataset: str, symbol: str | None = None, **kwargs: Any) -> list[dict[str, Any]]:
    """Read a dataset from the shared S3 source (run-scoped, cached)."""
    return get_default_source().read_dataset(dataset, symbol=symbol, **kwargs)


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC LOADERS  (dataset source = S3, resolved via production data contract)
# ═══════════════════════════════════════════════════════════════════════════════


def load_shadow_trades(symbol: str | None = None, *, outcomes_only: bool = True) -> list[dict[str, Any]]:
    """
    Load shadow trade records.

    Source: S3 dataset ``shadow_trades`` (supporting/shadow_trades).
    Key fields: trade_id, correlation_id, canonical_opportunity_id, symbol,
                pnl_r_multiple, exit_reason, bars_held, mfe_r, mae_r,
                direction, entry_price, stop_loss, take_profit

    lifecycle OPEN events are NEVER completed outcomes. With
    ``outcomes_only=True`` (default) records carrying ``event_type=="OPEN"`` are
    excluded. Historical records without ``event_type`` are CLOSE/outcome records
    and always included.
    """
    loaded = _read("shadow_trades", symbol)
    skipped_open = 0
    if outcomes_only:
        kept = []
        for rec in loaded:
            if rec.get("event_type") == "OPEN":
                skipped_open += 1
                continue
            kept.append(rec)
        loaded = kept
    logger.info(
        "[RESEARCH_LOADER] loaded %d shadow trade records (excluded %d OPEN events)",
        len(loaded), skipped_open,
    )
    return loaded


def load_trade_journal(symbol: str | None = None) -> list[dict[str, Any]]:
    """
    Load trade journal records (realised trade lifecycle — entry/exit/PnL).

    Source: S3 dataset ``trade_journal`` (projections/trade_journal).
    Key fields: trade_id, position_ticket, symbol, direction, entry_price,
                exit_price, initial_sl, initial_tp, net_pnl, close_reason,
                trade_horizon, correlation_id
    """
    records = _read("trade_journal", symbol)
    logger.info("[RESEARCH_LOADER] loaded %d trade journal records", len(records))
    return records


def load_trade_truth(symbol: str | None = None) -> list[dict[str, Any]]:
    """
    Load trade truth records (actual broker outcomes).

    Source: S3 dataset ``trade_truth`` (core/trade_truth).
    Key fields: identity.trade_id, identity.correlation_id, identity.symbol,
                outcome.r_multiple_realised, outcome.pnl_realised, exit.exit_reason
    """
    records = _read("trade_truth", symbol)
    logger.info("[RESEARCH_LOADER] loaded %d trade truth records", len(records))
    return records


def load_decision_ledger(symbol: str | None = None) -> list[dict[str, Any]]:
    """
    Load decision ledger records.

    Source: S3 dataset ``decision_ledger`` (core/decision_ledger).
    Key fields: symbol, cycle_id, decision, reason, signal_score, regime,
                correlation_id, entity_id, execution_intent
    """
    records = _read("decision_ledger", symbol)
    logger.info("[RESEARCH_LOADER] loaded %d decision ledger records", len(records))
    return records


def load_decision_trace(symbol: str | None = None) -> list[dict[str, Any]]:
    """
    Load decision trace records (detailed engine reasoning).

    Source: S3 dataset ``decision_trace`` (supporting/decision_trace).
    Key fields: entity_id, symbol, cycle_id, action, terminal_stage,
                score_strategy, components, regime, pattern_name
    """
    records = _read("decision_trace", symbol)
    logger.info("[RESEARCH_LOADER] loaded %d decision trace records", len(records))
    return records


# ═══════════════════════════════════════════════════════════════════════════════
# INTELLIGENCE CHAIN LOADERS
# ═══════════════════════════════════════════════════════════════════════════════


def load_opportunities(symbol: str | None = None) -> list[dict[str, Any]]:
    """
    Load opportunity records (market intelligence — what appeared).

    Source: S3 dataset ``opportunities`` (core/opportunities). Schema: opportunity_v1.
    Key fields: opportunity_id, symbol, direction, pattern, state,
                rejection_reason, rejection_stage, h4_regime, h1_direction,
                pattern_confidence, overall_score, entity_id, cycle_id
    """
    records = _read("opportunities", symbol)
    logger.info("[RESEARCH_LOADER] loaded %d opportunity records", len(records))
    return records


def load_assessments(symbol: str | None = None) -> list[dict[str, Any]]:
    """
    Load assessment records (opportunity evaluation — how good was it).

    Source: S3 dataset ``assessments`` (core/assessments). Schema: assessment_v1.
    Key fields: assessment_id, opportunity_id, symbol, score_neutral,
                score_strategy, ev, p_success, rr_effective, market_state,
                selected_strategy, strategy_confidence, entity_id, cycle_id
    """
    records = _read("assessments", symbol)
    logger.info("[RESEARCH_LOADER] loaded %d assessment records", len(records))
    return records


def load_portfolio_rankings() -> list[dict[str, Any]]:
    """
    Load portfolio ranking records (cross-symbol opportunity comparison).

    Source: S3 dataset ``portfolio_rankings`` (supporting/portfolio_rankings).
    Schema: portfolio_ranking_v1. Partition: date only (cross-symbol).
    """
    records = _read("portfolio_rankings")
    logger.info("[RESEARCH_LOADER] loaded %d portfolio ranking records", len(records))
    return records


def load_shadow_comparisons() -> list[dict[str, Any]]:
    """
    Load portfolio shadow comparison records (ranking vs actual execution).

    Source: S3 dataset ``portfolio_shadow`` (projections/portfolio_shadow).
    Partition: date only (cross-symbol).
    """
    records = _read("portfolio_shadow")
    logger.info("[RESEARCH_LOADER] loaded %d shadow comparison records", len(records))
    return records


def load_execution_results(symbol: str | None = None) -> list[dict[str, Any]]:
    """
    Load execution result records (broker response to order submission).

    Source: S3 dataset ``execution_results`` (core/execution_results).
    Key fields: symbol, result_ok, retcode, deal, fill_price, slippage,
                side, volume, sl, tp, pattern, decision_id, correlation_id,
                entity_id, protection_status, broker_confirmed_sl
    """
    records = _read("execution_results", symbol)
    logger.info("[RESEARCH_LOADER] loaded %d execution result records", len(records))
    return records


def load_execution_context(symbol: str | None = None) -> list[dict[str, Any]]:
    """
    Load execution context records (pre-trade environment snapshot).

    Source: S3 dataset ``execution_context`` (supporting/execution_context).
    Key fields: correlation_id, symbol, timestamp_utc,
                market_access (session_state, spread, bid, ask),
                infrastructure (latency_ms, feed_state),
                risk_environment (drawdown_pct, daily_loss_pct, open_positions)
    """
    records = _read("execution_context", symbol)
    logger.info("[RESEARCH_LOADER] loaded %d execution context records", len(records))
    return records


def load_protection_audit(symbol: str | None = None) -> list[dict[str, Any]]:
    """
    Load protection audit records (post-fill SL/TP verification).

    Source: S3 dataset ``protection_audit`` (supporting/protection_audit).
    Key fields: symbol, position_ticket, correlation_id,
                requested_sl, broker_confirmed_sl, requested_tp, broker_confirmed_tp,
                protection_status, correction_attempted, correction_success
    """
    records = _read("protection_audit", symbol)
    logger.info("[RESEARCH_LOADER] loaded %d protection audit records", len(records))
    return records


def load_risk_deviation(symbol: str | None = None) -> list[dict[str, Any]]:
    """
    Load risk deviation records (planned vs actual risk measurement).

    Source: S3 dataset ``risk_deviation`` (supporting/risk_deviation).
    Key fields: trade_id, symbol, correlation_id, planned_risk_R,
                actual_risk_R, risk_deviation, risk_classification,
                entry_price, exit_price, initial_sl, direction
    """
    records = _read("risk_deviation", symbol)
    logger.info("[RESEARCH_LOADER] loaded %d risk deviation records", len(records))
    return records


def load_horizon_candidates(symbol: str | None = None) -> list[dict[str, Any]]:
    """
    Load horizon candidate records (every evaluated horizon per opportunity).

    Source: S3 dataset ``horizon_candidates`` (supporting/horizon_candidates).
    Schema: horizon_candidates_v1. Key fields: candidate_id,
            canonical_opportunity_id, observation_id, entity_id, correlation_id,
            cycle_id, horizon, eligible, confidence, reasoning, evidence,
            selection_status (SELECTED / REJECTED / INELIGIBLE / NOT_APPLICABLE).
    """
    records = _read("horizon_candidates", symbol)
    logger.info("[RESEARCH_LOADER] loaded %d horizon candidate records", len(records))
    return records


def load_strategy_candidates(symbol: str | None = None) -> list[dict[str, Any]]:
    """
    Load strategy candidate records (every evaluated strategy candidate).

    Source: S3 dataset ``strategy_candidates`` (supporting/strategy_candidates).
    Schema: strategy_candidates_v1. Key fields: candidate_id,
            canonical_opportunity_id, observation_id, strategy_family,
            confidence, reasoning, supporting_conditions, selected, rank,
            bar_time.
    """
    records = _read("strategy_candidates", symbol)
    logger.info("[RESEARCH_LOADER] loaded %d strategy candidate records", len(records))
    return records


def load_execution_attempts(symbol: str | None = None) -> list[dict[str, Any]]:
    """
    Load execution attempt records (one record per individual broker call).

    Source: S3 dataset ``execution_attempts`` (supporting/execution_attempts).
    Schema: execution_attempts_v1. Key fields: attempt_id, attempt_number,
            action_type, retry_reason, broker_result.{ok,retcode,deal,comment},
            bid/ask/spread_at_attempt, slippage, correlation_id,
            canonical_opportunity_id, decision_id, trade_id.

    NOTE: attempts are NOT trade outcomes — one trade may have many attempts.
    """
    records = _read("execution_attempts", symbol)
    logger.info("[RESEARCH_LOADER] loaded %d execution attempt records", len(records))
    return records


def load_management_actions(symbol: str | None = None) -> list[dict[str, Any]]:
    """
    Load management action records (trade-management layer initiation events).

    Source: S3 dataset ``management_actions`` (supporting/management_actions).
    Schema: management_actions_v1. Key fields: management_action_id, trade_id,
            decision_id, canonical_opportunity_id, observation_id,
            correlation_id, cycle_id, action_type (SLTP_MODIFY / PARTIAL_CLOSE /
            CLOSE), action_reason, requested_sl, requested_tp, requested_volume.
    """
    records = _read("management_actions", symbol)
    logger.info("[RESEARCH_LOADER] loaded %d management action records", len(records))
    return records


# NOTE (Production V1 cleanup): load_decision_audit() was removed. The
# decision_audit dataset is retired; authoritative terminal decisions now come
# from decision_ledger and diagnostic reasoning from decision_trace. Offline
# consumers must read those retained V1 authorities directly.
