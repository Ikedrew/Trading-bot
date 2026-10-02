"""
Portfolio Ranking Persistence — Local JSONL + S3 mirror for ranking decisions.

Captures the complete ranking output every cycle where candidates exist.
Enables research into:
    - Did we select the best available opportunity?
    - Were higher-ranked opportunities actually better?
    - How often do multiple symbols compete?
    - Were profitable opportunities outranked?

Storage:
    Local:  logs/portfolio_rankings/{YYYY-MM-DD}.jsonl
    S3:     s3://trading-bot-data-mk1/portfolio_rankings/date={YYYY-MM-DD}/part-000.jsonl

Partitioning: By date only (ranking is cross-symbol — one record covers all symbols).

This module is PURELY OBSERVATIONAL. It does NOT:
    - Affect trading decisions
    - Give ranking authority over execution
    - Block or gate trades
    - Modify any pipeline behaviour
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_LOCAL_DIR = "logs/portfolio_rankings"
from core.config import NEW_RUNTIME_S3_BUCKET
from core.production_data_contract import s3_base_prefix

_S3_BUCKET = NEW_RUNTIME_S3_BUCKET
_S3_PREFIX = s3_base_prefix("portfolio_rankings")

from core.production_data_contract import current_schema as _current_schema, current_generation as _current_generation
SCHEMA_VERSION = _current_schema("portfolio_rankings")
# Clean V1 baseline: dataset_version starts at generation 1 (was "2026.1").
DATASET_VERSION = _current_generation("portfolio_rankings")


def _build_portfolio_state_dict(portfolio_context: Any) -> dict[str, Any]:
    """Safely extract portfolio state for persistence."""
    if portfolio_context is None:
        return {}
    try:
        return {
            "total_open": getattr(portfolio_context, "total_open", 0),
            "currency_exposure": dict(getattr(portfolio_context, "currency_exposure", {})),
            "active_correlation_groups": list(getattr(portfolio_context, "active_correlation_groups", [])),
            "daily_risk_used_pct": round(getattr(portfolio_context, "daily_risk_used_pct", 0.0), 4),
            "daily_drawdown_pct": round(getattr(portfolio_context, "daily_drawdown_pct", 0.0), 4),
            "open_symbols": [p.get("symbol", "") for p in getattr(portfolio_context, "open_positions", [])],
        }
    except Exception:
        return {}


def persist_portfolio_ranking(
    pool: Any,
    *,
    runtime_session_id: str = "",
    open_positions_count: int = 0,
    max_open_positions: int = 1,
    portfolio_context: Any = None,
    candidate_enrichments: list[Any] | None = None,
) -> bool:
    """
    Persist a complete ranking event to local JSONL + S3 mirror.

    Called once per cycle after rank_candidates() produces an OpportunityPool.
    Fire-and-forget. Never raises. Never blocks the trading pipeline.

    Args:
        pool: OpportunityPool from opportunity_ranker.rank_candidates()
        runtime_session_id: Bot runtime session identifier
        open_positions_count: Number of positions open at ranking time
        max_open_positions: Configured max positions (for available slots calc)
        portfolio_context: PortfolioContext snapshot (from context.py)
        candidate_enrichments: List of CandidateContextEnrichment (one per candidate)
    """
    _ledger = None
    _obligation = None
    try:
        now = datetime.now(timezone.utc)
        date_str = now.strftime("%Y-%m-%d")
        timestamp_utc = now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

        # Build ranking record
        cycle_id = getattr(pool, "cycle_id", 0)
        ranking_id = f"ranking_{runtime_session_id or 'SESSION_UNKNOWN'}_{cycle_id}"

        # Extract candidate details
        candidates: list[dict[str, Any]] = []
        _enrichment_map: dict[str, Any] = {}
        if candidate_enrichments:
            for e in candidate_enrichments:
                _enrichment_map[getattr(e, "symbol", "")] = e

        for c in getattr(pool, "candidates", []):
            _sym = getattr(c, "symbol", "")
            candidate_record: dict[str, Any] = {
                "symbol": _sym,
                "pattern": getattr(c, "pattern", ""),
                "direction": "",  # Not on RankedCandidate currently
                "strategy": getattr(c, "strategy", ""),
                "strategy_confidence": round(getattr(c, "strategy_confidence", 0.0), 4),
                "score_neutral": round(getattr(c, "score_neutral", 0.0), 4),
                "score_strategy": round(getattr(c, "score_strategy", 0.0), 4),
                "ev": round(getattr(c, "ev", 0.0), 8),
                "rr_effective": round(getattr(c, "rr_effective", 0.0), 4),
                "market_state": getattr(c, "market_state", ""),
                "rank_score": round(getattr(c, "rank_score", 0.0), 8),
                "rank_position": getattr(c, "rank_position", 0),
                "eligible": getattr(c, "eligible", False),
                "block_reason": getattr(c, "block_reason", None),
                "selection_status": getattr(c, "selection_status", ""),
                # Join keys (constructed from available data)
                "opportunity_id": f"{_sym}_{cycle_id}_{getattr(c, 'pattern', '')}",
                "assessment_id": f"{_sym}_{cycle_id}_{getattr(c, 'pattern', '')}_assessment",
            }
            # Portfolio context enrichment (if available)
            _enrich = _enrichment_map.get(_sym)
            if _enrich is not None:
                candidate_record["portfolio_context"] = {
                    "correlation_penalty": round(getattr(_enrich, "correlation_penalty", 0.0), 8),
                    "exposure_penalty": round(getattr(_enrich, "exposure_penalty", 0.0), 8),
                    "diversification_bonus": round(getattr(_enrich, "diversification_bonus", 0.0), 8),
                    "risk_adjustment": round(getattr(_enrich, "risk_adjustment", 0.0), 8),
                    "portfolio_adjustment": round(getattr(_enrich, "portfolio_adjustment", 0.0), 8),
                    "final_rank_score": round(getattr(_enrich, "final_rank_score", 0.0), 8),
                    "original_rank_score": round(getattr(_enrich, "original_rank_score", 0.0), 8),
                    "correlated_positions_count": getattr(_enrich, "correlated_positions_count", 0),
                    "same_currency_exposure": round(getattr(_enrich, "same_currency_exposure", 0.0), 4),
                    "is_diversifying": getattr(_enrich, "is_diversifying", False),
                }
            candidates.append(candidate_record)

        # Selected candidate summary
        selected = getattr(pool, "selected", None)
        selected_symbol = getattr(selected, "symbol", "") if selected else ""
        selected_rank_score = round(getattr(selected, "rank_score", 0.0), 8) if selected else 0.0

        record: dict[str, Any] = {
            # Version
            "schema_version": SCHEMA_VERSION,
            "dataset_version": DATASET_VERSION,
            # Identity
            "ranking_id": ranking_id,
            "cycle_id": cycle_id,
            "runtime_session_id": runtime_session_id,
            "ranked_at_utc": timestamp_utc,
            # Pool summary
            "total_candidates": getattr(pool, "total_candidates", 0),
            "eligible_count": getattr(pool, "eligible_count", 0),
            "selected_symbol": selected_symbol,
            "selected_rank_score": selected_rank_score,
            "ranking_method": "ev_x_market_state_multiplier",
            # Portfolio context
            "open_positions_at_ranking": open_positions_count,
            "available_slots": max(0, max_open_positions - open_positions_count),
            "max_open_positions": max_open_positions,
            # Portfolio state snapshot (Phase 2C-Part2 enrichment)
            "portfolio_state": _build_portfolio_state_dict(portfolio_context),
            # Candidates
            "candidates": candidates,
        }

        try:
            from core.lifecycle_evidence_obligations import (
                ObligationStatus, record_producer_outcome,
            )
            from core.lifecycle_evidence_obligations import obligation_ledger
            _ledger = obligation_ledger()
            _obligation = _ledger.create(
                lifecycle_event_id=f"portfolio-ranking:{ranking_id}",
                lifecycle_stage="PORTFOLIO_CYCLE",
                expected_dataset="portfolio_rankings",
                identity={"ranking_id": ranking_id,
                          "symbol": selected_symbol or (candidates[0]["symbol"]
                                                        if candidates else "UNKNOWN")},
                originating_timestamp=timestamp_utc,
                due_state="EXPECTED_NOW" if candidates else "NOT_REQUIRED",
                due_after="PORTFOLIO_RANKING_LOCAL_WRITE" if candidates
                else "NO_RANKING_CANDIDATES",
                requirement_type="CONDITIONAL",
                current_status=(ObligationStatus.NOT_YET_DUE if candidates
                                else ObligationStatus.NOT_APPLICABLE),
                producer="core.portfolio_ranking.persistence.persist_portfolio_ranking",
                producer_trigger="RANKING_POOL_EXISTS" if candidates
                else "NO_RANKING_CANDIDATES",
            )
            if candidates and not runtime_session_id:
                from core.lifecycle_evidence_obligations import ObligationStatus
                _obligation = _ledger.update(
                    _obligation.obligation_id, ObligationStatus.PRODUCER_FAILED,
                    failure_reason="PORTFOLIO_RANKING_RUNTIME_SESSION_ID_MISSING",
                )
        except Exception as _obligation_exc:
            _ledger = None
            _obligation = None
            logger.warning("[LIFECYCLE_OBLIGATION] portfolio ranking creation failed: %s",
                           _obligation_exc)

        # ─── LOCAL PERSISTENCE ────────────────────────────────────────
        path = Path(_LOCAL_DIR) / f"{date_str}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)

        line = json.dumps(record, separators=(",", ":"), default=str)

        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        try:
            os.write(fd, (line + "\n").encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)

        if _ledger is not None and _obligation is not None \
                and _obligation.current_status != "NOT_APPLICABLE":
            from core.lifecycle_evidence_obligations import record_producer_outcome
            record_producer_outcome(
                _ledger, _obligation, succeeded=True,
                observed_record_id=ranking_id,
                provenance={"local_path_authority": "LOCAL_ONLY"},
            )

        # ─── S3 MIRROR ───────────────────────────────────────────────
        try:
            from core.canonical_delivery import enqueue_canonical_delivery
            enqueue_canonical_delivery(
                dataset="portfolio_rankings", payload=record, symbol="",
                partition_date=date_str,
                lifecycle_obligation_id=(
                    _obligation.obligation_id if _obligation is not None else None
                ),
            )
        except Exception:
            pass
        return True

    except Exception as exc:
        if _ledger is not None and _obligation is not None:
            try:
                from core.lifecycle_evidence_obligations import record_producer_outcome
                record_producer_outcome(
                    _ledger, _obligation, succeeded=False,
                    failure_reason=f"PORTFOLIO_RANKING_LOCAL_WRITE:{type(exc).__name__}",
                )
            except Exception:
                pass
        logger.error("[PORTFOLIO_RANKING_PERSIST_ERROR] error=%s", exc)
        return False


def _write_s3(date_str: str, line: str) -> None:
    """Compatibility entry point; canonical delivery is now outbox-owned."""
    from core.canonical_delivery import enqueue_canonical_jsonl
    enqueue_canonical_jsonl(
        dataset="portfolio_rankings", content=line,
        partition_date=date_str,
    )
