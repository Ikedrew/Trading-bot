"""
Portfolio Ranking Shadow Comparison — Compares actual execution vs ranking selection.

After each cycle, determines whether the system executed the SAME opportunity
that the portfolio ranker would have selected. Logs discrepancies for research.

This module is PURELY OBSERVATIONAL. It does NOT:
    - Gate or block trades
    - Modify execution
    - Change risk decisions
    - Affect the pipeline in any way

It answers: "Would portfolio authority have made a different choice?"

Storage: logs/portfolio_rankings/{YYYY-MM-DD}.jsonl (appended to ranking records)
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_LOCAL_DIR = "logs/portfolio_shadow"
from core.config import NEW_RUNTIME_S3_BUCKET
from core.production_data_contract import s3_base_prefix

_S3_BUCKET = NEW_RUNTIME_S3_BUCKET
_S3_PREFIX = s3_base_prefix("portfolio_shadow")
_SCHEMA_VERSION = "portfolio_shadow_v1"


@dataclass
class ShadowComparison:
    """One cycle's comparison between actual execution and ranking recommendation."""

    # Identity
    cycle_id: int
    runtime_session_id: str
    compared_at_utc: str

    # What actually happened
    actual_executed_symbols: list[str]  # Symbols that actually filled this cycle
    actual_execution_count: int

    # What ranking recommends
    ranking_selected_symbol: str       # What the ranker would have chosen
    ranking_selected_rank_score: float

    # Comparison
    agreement: bool                    # True if ranking agrees with execution
    disagreement_type: str             # "" | "WRONG_SYMBOL" | "EXTRA_EXECUTIONS" | "NO_EXECUTION_NEEDED"
    disagreement_detail: str           # Human-readable explanation

    # Context
    total_candidates: int
    eligible_candidates: int
    outranked_symbols: list[str]       # Symbols that were outranked

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def compute_shadow_comparison(
    *,
    pool: Any,
    executed_symbols: list[str],
    cycle_id: int,
    runtime_session_id: str = "",
) -> ShadowComparison:
    """
    Compare actual execution outcome with ranking recommendation.

    Args:
        pool: OpportunityPool from rank_candidates()
        executed_symbols: List of symbols that actually executed this cycle
        cycle_id: Current cycle
        runtime_session_id: Bot session identifier

    Returns:
        ShadowComparison record with agreement/disagreement analysis.
    """
    now = datetime.now(timezone.utc)
    compared_at_utc = now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    # Extract ranking recommendation
    selected = getattr(pool, "selected", None)
    ranking_selected_symbol = getattr(selected, "symbol", "") if selected else ""
    ranking_selected_rank_score = float(getattr(selected, "rank_score", 0.0)) if selected else 0.0

    total_candidates = getattr(pool, "total_candidates", 0)
    eligible_candidates = getattr(pool, "eligible_count", 0)

    # Outranked symbols (eligible but not selected)
    outranked_symbols = []
    for c in getattr(pool, "candidates", []):
        if getattr(c, "selection_status", "") == "OUTRANKED":
            outranked_symbols.append(getattr(c, "symbol", ""))

    # Determine agreement
    actual_count = len(executed_symbols)

    if actual_count == 0 and not ranking_selected_symbol:
        # Both agree: nothing to execute
        agreement = True
        disagreement_type = ""
        disagreement_detail = ""
    elif actual_count == 1 and executed_symbols[0] == ranking_selected_symbol:
        # Perfect agreement: executed the ranked best
        agreement = True
        disagreement_type = ""
        disagreement_detail = ""
    elif actual_count == 0 and ranking_selected_symbol:
        # Ranking would have selected something, but nothing executed
        agreement = False
        disagreement_type = "NO_EXECUTION_NEEDED"
        disagreement_detail = (
            f"Ranking selected {ranking_selected_symbol} (score={ranking_selected_rank_score:.8f}) "
            f"but nothing executed this cycle (guards may have blocked)"
        )
    elif actual_count >= 1 and ranking_selected_symbol and executed_symbols[0] != ranking_selected_symbol:
        # Executed a different symbol than ranking recommends
        agreement = False
        disagreement_type = "WRONG_SYMBOL"
        disagreement_detail = (
            f"Executed {executed_symbols[0]} but ranking recommends {ranking_selected_symbol} "
            f"(rank_score={ranking_selected_rank_score:.8f})"
        )
    elif actual_count > 1:
        # Multiple executions — ranking would only allow 1
        agreement = False
        disagreement_type = "EXTRA_EXECUTIONS"
        disagreement_detail = (
            f"Executed {actual_count} symbols {executed_symbols} but ranking would select only "
            f"{ranking_selected_symbol}"
        )
    else:
        agreement = True
        disagreement_type = ""
        disagreement_detail = ""

    return ShadowComparison(
        cycle_id=cycle_id,
        runtime_session_id=runtime_session_id,
        compared_at_utc=compared_at_utc,
        actual_executed_symbols=executed_symbols,
        actual_execution_count=actual_count,
        ranking_selected_symbol=ranking_selected_symbol,
        ranking_selected_rank_score=round(ranking_selected_rank_score, 8),
        agreement=agreement,
        disagreement_type=disagreement_type,
        disagreement_detail=disagreement_detail,
        total_candidates=total_candidates,
        eligible_candidates=eligible_candidates,
        outranked_symbols=outranked_symbols,
    )


def persist_shadow_comparison(comparison: ShadowComparison) -> None:
    """
    Persist shadow comparison to local JSONL + S3 mirror. Fire-and-forget.

    Only persists when there IS a disagreement (reduces noise).
    Agreement cycles are not persisted (they're the common case).
    """
    _ledger = None
    _obligation = None
    try:
        from core.lifecycle_evidence_obligations import (
            ObligationStatus, obligation_ledger,
        )
        _ledger = obligation_ledger()
        _should_persist = not (comparison.agreement and comparison.total_candidates <= 1)
        _identity_valid = bool(comparison.runtime_session_id)
        _event_id = (
            f"portfolio-shadow:{comparison.cycle_id}:{comparison.runtime_session_id}"
        )
        _obligation = _ledger.create(
            lifecycle_event_id=_event_id, lifecycle_stage="PORTFOLIO_CYCLE",
            expected_dataset="portfolio_shadow",
            identity={"cycle_id": comparison.cycle_id,
                      "runtime_session_id": comparison.runtime_session_id,
                      "symbol": "PORTFOLIO"},
            originating_timestamp=comparison.compared_at_utc,
            due_state="EXPECTED_NOW" if _should_persist else "NOT_REQUIRED",
            due_after="COMPARISON_LOCAL_WRITE" if _should_persist
            else "AGREEMENT_WITH_SINGLE_CANDIDATE",
            requirement_type="CONDITIONAL",
            current_status=(
                ObligationStatus.NOT_YET_DUE if _should_persist and _identity_valid
                else ObligationStatus.PRODUCER_FAILED if _should_persist
                else ObligationStatus.NOT_APPLICABLE
            ),
            producer="core.portfolio_ranking.shadow_comparison.persist_shadow_comparison",
            producer_trigger="COMPARISON_ENABLED",
            failure_reason=("RUNTIME_SESSION_ID_MISSING"
                            if _should_persist and not _identity_valid else None),
        )
        # Only persist interesting cases (disagreements or multi-candidate cycles)
        if not _should_persist:
            return  # Common case: nothing interesting

        now = datetime.now(timezone.utc)
        date_str = now.strftime("%Y-%m-%d")

        path = Path(_LOCAL_DIR) / f"{date_str}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)

        record = comparison.to_dict()
        record["schema_version"] = _SCHEMA_VERSION
        line = json.dumps(record, separators=(",", ":"), default=str)

        from core.canonical_delivery import try_prepare_local_jsonl_handoffs
        try_prepare_local_jsonl_handoffs(
            dataset="portfolio_shadow", content=line + "\n", symbol="",
            partition_date=date_str, local_path=path,
        )

        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        try:
            os.write(fd, (line + "\n").encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)

        if _should_persist and _identity_valid:
            from core.lifecycle_evidence_obligations import record_producer_outcome
            record_producer_outcome(
                _ledger, _obligation, succeeded=True,
                observed_record_id=_event_id,
                provenance={"local_path_authority": "LOCAL_ONLY"},
            )

        # ─── S3 MIRROR (Hive-partitioned, fire-and-forget) ───────────
        try:
            from core.canonical_delivery import enqueue_canonical_delivery
            enqueue_canonical_delivery(
                dataset="portfolio_shadow", payload=record, symbol="",
                partition_date=date_str,
                lifecycle_obligation_id=(
                    _obligation.obligation_id if _obligation is not None else None
                ),
            )
        except Exception:
            pass  # S3 failure must NEVER affect portfolio shadow persistence
        # ─── END S3 MIRROR ────────────────────────────────────────────

        # Log disagreements at WARNING level
        if not comparison.agreement:
            logger.warning(
                "[PORTFOLIO_SHADOW] cycle=%d | %s | %s",
                comparison.cycle_id, comparison.disagreement_type,
                comparison.disagreement_detail,
            )

    except Exception as exc:
        if _ledger is not None and _obligation is not None \
                and _obligation.current_status != "NOT_APPLICABLE":
            try:
                from core.lifecycle_evidence_obligations import record_producer_outcome
                record_producer_outcome(
                    _ledger, _obligation, succeeded=False,
                    failure_reason=f"PORTFOLIO_SHADOW_LOCAL_WRITE:{type(exc).__name__}",
                )
            except Exception:
                pass
        logger.error("[PORTFOLIO_SHADOW_ERROR] %s", exc)


# ═══════════════════════════════════════════════════════════════════════════════
# S3 MIRROR (Hive-partitioned, fire-and-forget)
# ═══════════════════════════════════════════════════════════════════════════════


def _write_s3_portfolio_shadow(date_str: str, line: str) -> None:
    """Compatibility entry point; canonical delivery is now outbox-owned."""
    from core.canonical_delivery import enqueue_canonical_jsonl
    enqueue_canonical_jsonl(
        dataset="portfolio_shadow", content=line,
        partition_date=date_str,
    )
