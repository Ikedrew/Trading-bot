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

SCHEMA_VERSION = "portfolio_ranking_v1"
DATASET_VERSION = "2026.1"


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
) -> None:
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
    try:
        now = datetime.now(timezone.utc)
        date_str = now.strftime("%Y-%m-%d")
        timestamp_utc = now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        timestamp_ms = int(now.timestamp() * 1000)

        # Build ranking record
        cycle_id = getattr(pool, "cycle_id", 0)
        ranking_id = f"ranking_{cycle_id}_{timestamp_ms}"

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

        # ─── S3 MIRROR ───────────────────────────────────────────────
        _write_s3(date_str, line)

    except Exception as exc:
        logger.error("[PORTFOLIO_RANKING_PERSIST_ERROR] error=%s", exc)


def _write_s3(date_str: str, line: str) -> None:
    """
    Mirror ranking record to S3. Fire-and-forget. Never raises.

    Follows standard pattern from decision_ledger.py.
    """
    try:
        from core import config
        if not getattr(config, "EVENT_STREAM_S3_MIRROR", False):
            return

        import boto3
        from botocore.config import Config as BotoConfig
        s3 = boto3.client(
            "s3",
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
            region_name=os.getenv("AWS_REGION", "eu-west-2"),
            config=BotoConfig(
                connect_timeout=3,
                read_timeout=5,
                retries={"max_attempts": 0},
            ),
        )
        key = f"{_S3_PREFIX}/date={date_str}/part-000.jsonl"
        body = line + "\n"

        # Read-append-write
        try:
            existing = s3.get_object(Bucket=_S3_BUCKET, Key=key)
            body = existing["Body"].read().decode("utf-8") + body
        except Exception:
            pass  # New file

        s3.put_object(
            Bucket=_S3_BUCKET, Key=key,
            Body=body.encode("utf-8"),
            ContentType="application/x-ndjson",
        )
    except Exception:
        pass  # S3 failure must never affect runtime
