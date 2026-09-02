"""
Data Access Layer — Read-only loaders for existing persistence layers.

Reads JSONL files from the logs/ directory. Never writes. Never modifies source data.
Handles missing files and schema mismatches gracefully.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger(__name__)

# Default persistence root (relative to project root)
_DEFAULT_LOGS_DIR = "logs"


def _get_logs_dir() -> Path:
    """Resolve the logs directory path."""
    # Walk up from this file to find project root
    here = Path(__file__).resolve().parent.parent.parent
    return here / _DEFAULT_LOGS_DIR


def _iter_jsonl_files(subdir: str, symbol: str | None = None) -> Iterator[Path]:
    """Iterate over JSONL files in a persistence subdirectory."""
    base = _get_logs_dir() / subdir
    if not base.exists():
        logger.warning("[RESEARCH_LOADER] directory not found: %s", base)
        return

    if symbol:
        # Symbol-partitioned: logs/{subdir}/{SYMBOL}/*.jsonl
        sym_dir = base / symbol
        if sym_dir.exists():
            for f in sorted(sym_dir.glob("*.jsonl")):
                yield f
    else:
        # Date-partitioned: logs/{subdir}/*.jsonl or logs/{subdir}/{SYM}/*.jsonl
        # Try flat first
        flat_files = sorted(base.glob("*.jsonl"))
        if flat_files:
            yield from flat_files
        else:
            # Try symbol subdirectories
            for sym_dir in sorted(base.iterdir()):
                if sym_dir.is_dir():
                    for f in sorted(sym_dir.glob("*.jsonl")):
                        yield f


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    """Load all records from a single JSONL file."""
    records = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    records.append(json.loads(stripped))
                except json.JSONDecodeError as e:
                    logger.debug("[RESEARCH_LOADER] %s:%d parse error: %s", path.name, line_num, e)
    except Exception as e:
        logger.warning("[RESEARCH_LOADER] failed to read %s: %s", path, e)
    return records


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC LOADERS
# ═══════════════════════════════════════════════════════════════════════════════


def load_shadow_trades(symbol: str | None = None, *, outcomes_only: bool = True) -> list[dict[str, Any]]:
    """
    Load shadow trade records.

    Source: logs/shadow_trades/{SYMBOL}/{DATE}.jsonl
    Key fields: trade_id, correlation_id, canonical_opportunity_id, symbol,
                pnl_r_multiple, exit_reason, bars_held, mfe_r, mae_r,
                direction, entry_price, stop_loss, take_profit

    Remediation Stage 8: lifecycle OPEN events are NEVER completed outcomes.
    With ``outcomes_only=True`` (default) records carrying ``event_type=="OPEN""
    are excluded. Historical records without ``event_type`` are CLOSE/outcome
    records and always included.
    """
    records = []
    skipped_open = 0
    for path in _iter_jsonl_files("shadow_trades", symbol):
        loaded = _load_jsonl(path)
        if outcomes_only:
            kept = []
            for rec in loaded:
                if rec.get("event_type") == "OPEN":
                    skipped_open += 1
                    continue
                kept.append(rec)
            records.extend(kept)
        else:
            records.extend(loaded)
    logger.info(
        "[RESEARCH_LOADER] loaded %d shadow trade records (excluded %d OPEN events)",
        len(records), skipped_open,
    )
    return records


def load_trade_truth(symbol: str | None = None) -> list[dict[str, Any]]:
    """
    Load trade truth records (actual broker outcomes).

    Source: logs/trade_truth/{SYMBOL}/{DATE}.jsonl
    Key fields: identity.trade_id, identity.correlation_id, identity.symbol,
                outcome.r_multiple_realised, outcome.pnl_realised, exit.exit_reason
    """
    records = []
    for path in _iter_jsonl_files("trade_truth", symbol):
        records.extend(_load_jsonl(path))
    logger.info("[RESEARCH_LOADER] loaded %d trade truth records", len(records))
    return records


def load_decision_ledger(symbol: str | None = None) -> list[dict[str, Any]]:
    """
    Load decision ledger records.

    Source: logs/decision_ledger/{SYMBOL}/{DATE}.jsonl
    Key fields: symbol, cycle_id, decision, reason, signal_score, regime,
                correlation_id, entity_id, execution_intent
    """
    records = []
    for path in _iter_jsonl_files("decision_ledger", symbol):
        records.extend(_load_jsonl(path))
    logger.info("[RESEARCH_LOADER] loaded %d decision ledger records", len(records))
    return records


def load_decision_trace(symbol: str | None = None) -> list[dict[str, Any]]:
    """
    Load decision trace records (detailed engine reasoning).

    Source: logs/decision_trace/{SYMBOL}/{DATE}.jsonl
    Key fields: entity_id, symbol, cycle_id, action, terminal_stage,
                score_strategy, components, regime, pattern_name
    """
    records = []
    for path in _iter_jsonl_files("decision_trace", symbol):
        records.extend(_load_jsonl(path))
    logger.info("[RESEARCH_LOADER] loaded %d decision trace records", len(records))
    return records


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 3A: INTELLIGENCE CHAIN LOADERS
# ═══════════════════════════════════════════════════════════════════════════════


def load_opportunities(symbol: str | None = None) -> list[dict[str, Any]]:
    """
    Load opportunity records (market intelligence — what appeared).

    Source: logs/opportunities/{SYMBOL}/{DATE}.jsonl
    Key fields: opportunity_id, symbol, direction, pattern, state,
                rejection_reason, rejection_stage, h4_regime, h1_direction,
                pattern_confidence, overall_score, entity_id, cycle_id
    Schema: opportunity_v1
    """
    records = []
    for path in _iter_jsonl_files("opportunities", symbol):
        records.extend(_load_jsonl(path))
    logger.info("[RESEARCH_LOADER] loaded %d opportunity records", len(records))
    return records


def load_assessments(symbol: str | None = None) -> list[dict[str, Any]]:
    """
    Load assessment records (opportunity evaluation — how good was it).

    Source: logs/assessments/{SYMBOL}/{DATE}.jsonl
    Key fields: assessment_id, opportunity_id, symbol, score_neutral,
                score_strategy, ev, p_success, rr_effective, market_state,
                selected_strategy, strategy_confidence, entity_id, cycle_id
    Schema: assessment_v1
    """
    records = []
    for path in _iter_jsonl_files("assessments", symbol):
        records.extend(_load_jsonl(path))
    logger.info("[RESEARCH_LOADER] loaded %d assessment records", len(records))
    return records


def load_portfolio_rankings() -> list[dict[str, Any]]:
    """
    Load portfolio ranking records (cross-symbol opportunity comparison).

    Source: logs/portfolio_rankings/{DATE}.jsonl
    Key fields: ranking_id, cycle_id, total_candidates, eligible_count,
                selected_symbol, selected_rank_score, ranking_method,
                candidates (list with opportunity_id, rank_position, selection_status)
    Schema: portfolio_ranking_v1
    Partition: Date only (cross-symbol — one record per cycle)
    """
    records = []
    for path in _iter_jsonl_files("portfolio_rankings"):
        records.extend(_load_jsonl(path))
    logger.info("[RESEARCH_LOADER] loaded %d portfolio ranking records", len(records))
    return records


def load_shadow_comparisons() -> list[dict[str, Any]]:
    """
    Load portfolio shadow comparison records (ranking vs actual execution).

    Source: logs/portfolio_shadow/{DATE}.jsonl
    Key fields: cycle_id, agreement, disagreement_type, disagreement_detail,
                actual_executed_symbols, ranking_selected_symbol,
                outranked_symbols, total_candidates, eligible_candidates
    Partition: Date only (cross-symbol)
    """
    records = []
    for path in _iter_jsonl_files("portfolio_shadow"):
        records.extend(_load_jsonl(path))
    logger.info("[RESEARCH_LOADER] loaded %d shadow comparison records", len(records))
    return records


def load_execution_results(symbol: str | None = None) -> list[dict[str, Any]]:
    """
    Load execution result records (broker response to order submission).

    Source: logs/execution_results/{SYMBOL}/{DATE}.jsonl
    Key fields: symbol, result_ok, retcode, deal, fill_price, slippage,
                side, volume, sl, tp, pattern, decision_id, correlation_id,
                entity_id, protection_status, broker_confirmed_sl
    """
    records = []
    for path in _iter_jsonl_files("execution_results", symbol):
        records.extend(_load_jsonl(path))
    logger.info("[RESEARCH_LOADER] loaded %d execution result records", len(records))
    return records


def load_execution_context(symbol: str | None = None) -> list[dict[str, Any]]:
    """
    Load execution context records (pre-trade environment snapshot).

    Source: logs/execution_context/{SYMBOL}/{DATE}.jsonl
    Key fields: correlation_id, symbol, timestamp_utc,
                market_access (session_state, spread, bid, ask),
                infrastructure (latency_ms, feed_state),
                risk_environment (drawdown_pct, daily_loss_pct, open_positions)
    """
    records = []
    for path in _iter_jsonl_files("execution_context", symbol):
        records.extend(_load_jsonl(path))
    logger.info("[RESEARCH_LOADER] loaded %d execution context records", len(records))
    return records


def load_protection_audit(symbol: str | None = None) -> list[dict[str, Any]]:
    """
    Load protection audit records (post-fill SL/TP verification).

    Source: logs/protection_audit/{SYMBOL}/{DATE}.jsonl
    Key fields: symbol, position_ticket, correlation_id,
                requested_sl, broker_confirmed_sl, requested_tp, broker_confirmed_tp,
                protection_status, correction_attempted, correction_success
    """
    records = []
    for path in _iter_jsonl_files("protection_audit", symbol):
        records.extend(_load_jsonl(path))
    logger.info("[RESEARCH_LOADER] loaded %d protection audit records", len(records))
    return records


def load_risk_deviation(symbol: str | None = None) -> list[dict[str, Any]]:
    """
    Load risk deviation records (planned vs actual risk measurement).

    Source: logs/risk_deviation/{SYMBOL}/{DATE}.jsonl
    Key fields: trade_id, symbol, correlation_id, planned_risk_R,
                actual_risk_R, risk_deviation, risk_classification,
                entry_price, exit_price, initial_sl, direction
    """
    records = []
    for path in _iter_jsonl_files("risk_deviation", symbol):
        records.extend(_load_jsonl(path))
    logger.info("[RESEARCH_LOADER] loaded %d risk deviation records", len(records))
    return records


# NOTE (Production V1 cleanup): load_decision_audit() was removed. The
# decision_audit dataset is retired; authoritative terminal decisions now come
# from decision_ledger and diagnostic reasoning from decision_trace. Offline
# consumers must read those retained V1 authorities directly.
