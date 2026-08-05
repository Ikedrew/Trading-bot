"""
Shadow Ranking Persistence — Stores ranking results for research comparison.

Writes one JSONL record per cycle when EXECUTE candidates existed.
Enables post-hoc analysis: "Did the ranking recommendation match what executed?"

Storage: logs/ranking_shadow/YYYY-MM-DD.jsonl
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_LOCAL_DIR = "logs/ranking_shadow"


def persist_ranking_shadow(
    *,
    cycle_id: int,
    ranked_scores: list,
    actually_executed: str | None,
    runtime_session_id: str = "",
) -> None:
    """
    Persist shadow ranking result for one cycle.

    Args:
        cycle_id: Current scan cycle number
        ranked_scores: List of OpportunityScore objects from rank_for_execution()
        actually_executed: Symbol that was actually executed (or None)
        runtime_session_id: Bot session identifier
    """
    try:
        now = datetime.now(timezone.utc)
        recommended = ranked_scores[0] if ranked_scores else None

        record = {
            "timestamp_utc": now.isoformat(),
            "cycle_id": cycle_id,
            "runtime_session_id": runtime_session_id,
            "candidates_count": len(ranked_scores),
            "recommended_symbol": recommended.symbol if recommended else None,
            "recommended_score": recommended.final_rank_score if recommended else None,
            "recommended_opportunity_id": recommended.opportunity_id if recommended else None,
            "actually_executed": actually_executed,
            "match": (recommended.symbol == actually_executed) if recommended and actually_executed else None,
            "rankings": [
                {
                    "rank": s.rank_position,
                    "symbol": s.symbol,
                    "opportunity_id": s.opportunity_id,
                    "direction": s.direction,
                    "strategy_family": s.strategy_family,
                    "final_rank_score": round(s.final_rank_score, 4),
                    "opportunity_quality": round(s.opportunity_quality, 4),
                    "strategy_confidence": round(s.strategy_confidence, 4),
                    "htf_alignment": round(s.htf_alignment, 4),
                    "session_quality": round(s.session_quality, 4),
                    "risk_quality": round(s.risk_quality, 4),
                    "portfolio_adjustment": round(s.portfolio_adjustment, 4),
                    "ranking_reason": s.ranking_reason,
                }
                for s in ranked_scores
            ],
        }

        # Write to local JSONL
        date_str = now.strftime("%Y-%m-%d")
        local_dir = Path(_LOCAL_DIR)
        local_dir.mkdir(parents=True, exist_ok=True)
        file_path = local_dir / f"{date_str}.jsonl"

        with open(file_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")

    except Exception as exc:
        logger.warning("[RANKING_SHADOW_PERSIST] failed: %s", exc)
