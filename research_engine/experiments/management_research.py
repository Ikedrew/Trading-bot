"""
Management-Action Research — MGMT-1 / MGMT-2 (Wave 2)

Implements two canonical research questions interrogating actual trade
management behaviour using `management_actions_v1` joined to authoritative
outcome evidence.

SCIENTIFIC BOUNDARY:
    Management actions are NOT randomly assigned. They are triggered by price
    movement, risk changes, protection mismatches, or deteriorating trade
    state. All comparisons between managed and unmanaged trades are
    OBSERVATIONAL ASSOCIATIONS, not causal effects.

    The engine cannot answer "would the trade have performed better without
    the management action?" — it can only report "trades receiving action X
    had outcome Y."

    A CLOSE action may be lifecycle recording (TP/SL reached) rather than a
    discretionary management decision. Action semantics are classified
    explicitly.

Populations come exclusively from canonical S3 datasets
(`management_actions_v1` + `trade_truth_v1` + `shadow_runtime_v1`).
No local fallback, no parallel path.
"""
from __future__ import annotations

import logging
import math
import statistics
from collections import defaultdict
from typing import Any

logger = logging.getLogger(__name__)

_MIN_SAMPLE_MGMT1 = 30
_MIN_SAMPLE_MGMT2 = 15  # per action type
_MIN_SEGMENT = 10


# ═══════════════════════════════════════════════════════════════════════════════
# POPULATION BUILDERS
# ═══════════════════════════════════════════════════════════════════════════════


def _load_actions() -> list[dict[str, Any]]:
    """Load management actions from canonical S3."""
    from research_engine.data_access.loaders import load_management_actions
    return load_management_actions()


def _load_outcomes() -> list[dict[str, Any]]:
    """Load trade_truth outcomes from canonical S3."""
    from research_engine.data_access.loaders import load_trade_truth
    return load_trade_truth()


def _extract_outcome(rec: dict[str, Any]) -> dict[str, Any] | None:
    """Extract outcome fields from a trade_truth record."""
    identity = rec.get("identity") or {}
    outcome = rec.get("outcome") or {}
    r = outcome.get("r_multiple_realised")
    if r is None:
        return None
    return {
        "trade_id": identity.get("trade_id", ""),
        "correlation_id": identity.get("correlation_id", ""),
        "r_multiple": float(r),
        "win": r > 0,
        "exit_reason": (rec.get("exit") or {}).get("exit_reason", ""),
    }


def build_action_population(
    actions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Flatten management actions to action-level records.

    Deduplicates by management_action_id. Excludes records with no
    action_type. Preserves lineage fields for outcome joining.
    """
    seen_ids: set[str] = set()
    population: list[dict[str, Any]] = []
    for rec in actions:
        aid = str(rec.get("management_action_id", "") or "")
        if not aid or aid in seen_ids:
            continue
        seen_ids.add(aid)
        action_type = str(rec.get("action_type", "") or "")
        if not action_type:
            continue
        population.append({
            "management_action_id": aid,
            "trade_id": str(rec.get("trade_id") or ""),
            "correlation_id": str(rec.get("correlation_id") or ""),
            "canonical_opportunity_id": str(rec.get("canonical_opportunity_id") or ""),
            "symbol": str(rec.get("symbol", "")),
            "action_type": action_type,
            "action_reason": str(rec.get("action_reason") or ""),
            "requested_sl": rec.get("requested_sl"),
            "requested_tp": rec.get("requested_tp"),
            "requested_volume": rec.get("requested_volume"),
            "timestamp_utc": str(rec.get("timestamp_utc", "")),
        })
    return population


def build_trade_level_population(
    actions: list[dict[str, Any]],
    outcomes: list[dict[str, Any]],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, dict[str, Any]], set[str]]:
    """
    Build the trade-level management population.

    Returns:
        managed_actions: {trade_id: [action_records]} — trades with ≥1 action
        outcome_by_id: {trade_id or correlation_id: outcome_record}
        managed_trade_ids: set of trade_ids that received management
    """
    action_pop = build_action_population(actions)

    # Group actions by trade_id
    managed_actions: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for a in action_pop:
        tid = a["trade_id"]
        if tid:
            managed_actions[tid].append(a)

    managed_trade_ids = set(managed_actions.keys())

    # Build outcome lookup by both trade_id and correlation_id
    outcome_by_id: dict[str, dict[str, Any]] = {}
    for rec in outcomes:
        extracted = _extract_outcome(rec)
        if extracted is None:
            continue
        if extracted["trade_id"]:
            outcome_by_id[extracted["trade_id"]] = extracted
        if extracted["correlation_id"]:
            outcome_by_id[extracted["correlation_id"]] = extracted

    return managed_actions, outcome_by_id, managed_trade_ids


# ═══════════════════════════════════════════════════════════════════════════════
# MGMT-1 — DOES MANAGEMENT APPEAR TO HELP OR HARM?
# ═══════════════════════════════════════════════════════════════════════════════


def _descriptive(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """Descriptive statistics for a group of outcome records."""
    rs = [t["r_multiple"] for t in trades]
    wins = [t for t in trades if t["win"]]
    return {
        "n": len(trades),
        "mean_r": round(statistics.mean(rs), 4) if rs else None,
        "median_r": round(statistics.median(rs), 4) if rs else None,
        "win_rate": round(len(wins) / len(trades), 4) if trades else None,
        "total_r": round(sum(rs), 2) if rs else None,
    }


def run_mgmt1() -> dict[str, Any]:
    """
    MGMT-1: Does actual trade management appear to improve or harm outcomes?

    OBSERVATIONAL ASSOCIATION ONLY — management actions are not randomly
    assigned. This analysis describes outcome patterns, not causal effects.
    """
    from research_engine.experiments.exit_management import (
        _confidence, _make_report, _MIN_SAMPLE,
    )

    actions = _load_actions()
    outcomes = _load_outcomes()

    managed_actions, outcome_by_id, managed_trade_ids = build_trade_level_population(
        actions, outcomes)

    # Split trade_truth into managed and unmanaged
    managed_trades: list[dict[str, Any]] = []
    unmanaged_trades: list[dict[str, Any]] = []
    for rec in outcomes:
        extracted = _extract_outcome(rec)
        if extracted is None:
            continue
        tid = extracted["trade_id"]
        cid = extracted["correlation_id"]
        if tid in managed_trade_ids or cid in managed_trade_ids:
            managed_trades.append(extracted)
        else:
            unmanaged_trades.append(extracted)

    n_total = len(managed_trades) + len(unmanaged_trades)
    if n_total < _MIN_SAMPLE_MGMT1:
        conf = "INSUFFICIENT_DATA" if n_total < 10 else "LOW"
        return _make_report(
            question_id="MGMT-1",
            status="INSUFFICIENT_DATA",
            overall={
                "finding": f"Insufficient outcome evidence: N={n_total} < {_MIN_SAMPLE_MGMT1}",
                "total_trades": n_total,
                "managed_trades": len(managed_trades),
                "unmanaged_trades": len(unmanaged_trades),
                "management_actions": len(actions),
            },
            confidence=conf,
            dataset={"source": "management_actions_v1 + trade_truth_v1", "sample_size": n_total},
            recommendation="WAIT",
        )

    managed_stats = _descriptive(managed_trades) if managed_trades else {"n": 0}
    unmanaged_stats = _descriptive(unmanaged_trades) if unmanaged_trades else {"n": 0}

    # Management coverage rate
    coverage = round(len(managed_trades) / n_total, 4) if n_total else 0

    # Action count per managed trade
    action_counts = [len(managed_actions.get(tid, [])) for tid in managed_trade_ids]

    # Determine conclusion
    m_r = managed_stats.get("mean_r")
    u_r = unmanaged_stats.get("mean_r")
    m_n = managed_stats.get("n", 0)
    u_n = unmanaged_stats.get("n", 0)

    if m_n == 0:
        status = "COMPLETE"
        recommendation = "NO_MANAGED_TRADES_OBSERVED"
        finding = "No managed trades with realised outcomes in the current evidence."
    elif u_n == 0:
        status = "COMPLETE"
        recommendation = "NO_UNMANAGED_COMPARISON_GROUP"
        finding = "All trades received management actions — no unmanaged comparison group."
    elif m_r is not None and u_r is not None:
        diff = round(m_r - u_r, 4)
        if diff > 0.2:
            recommendation = "MANAGEMENT_ASSOCIATED_WITH_BETTER_OUTCOMES"
        elif diff < -0.2:
            recommendation = "MANAGEMENT_ASSOCIATED_WITH_WORSE_OUTCOMES"
        else:
            recommendation = "MIXED_MANAGEMENT_SIGNAL"
        status = "COMPLETE"
        finding = (
            f"Managed trades (n={m_n}): mean R={m_r:.4f}. "
            f"Unmanaged trades (n={u_n}): mean R={u_r:.4f}. "
            f"Difference: {diff:+.4f}R. OBSERVATIONAL ASSOCIATION — not causal."
        )
    else:
        status = "COMPLETE"
        recommendation = "INSUFFICIENT_OUTCOME_DATA"
        finding = "Outcome data incomplete for managed/unmanaged comparison."

    return _make_report(
        question_id="MGMT-1",
        status=status,
        overall={
            "finding": finding,
            "total_trades": n_total,
            "managed": managed_stats,
            "unmanaged": unmanaged_stats,
            "management_coverage_rate": coverage,
            "mean_actions_per_managed_trade": round(
                statistics.mean(action_counts), 2) if action_counts else 0,
            "total_management_actions": len(actions),
            "methodology": "observational association (not causal)",
        },
        confidence=_confidence(n_total),
        dataset={
            "source": "management_actions_v1 + trade_truth_v1",
            "sample_size": n_total,
        },
        recommendation=recommendation,
        assumptions=[
            "Management actions are NOT randomly assigned — selection bias is expected",
            "Managed and unmanaged trades may differ systematically in conditions",
            "This analysis describes outcome patterns, NOT causal effects",
            "No counterfactual (what-if-no-management) can be established from observational data",
        ],
        warnings=[
            "OBSERVATIONAL ASSOCIATION ONLY — do not interpret as causal",
            "Selection confounding expected: managed trades may differ systematically from unmanaged",
        ],
    )


# ═══════════════════════════════════════════════════════════════════════════════
# MGMT-2 — WHICH MANAGEMENT ACTION TYPES APPEAR HELPFUL OR HARMFUL?
# ═══════════════════════════════════════════════════════════════════════════════

# CLOSE actions may be lifecycle recording rather than discretionary management
_BOOKKEEPING_CLOSE_REASONS = {"take_profit", "stop_loss", "tp_hit", "sl_hit", "trailing_stop"}


def _classify_action_semantics(action_type: str, action_reason: str) -> str:
    """Classify whether an action is discretionary management or lifecycle bookkeeping."""
    if action_type == "SLTP_MODIFY":
        return "DISCRETIONARY_MANAGEMENT"
    if action_type == "PARTIAL_CLOSE":
        return "DISCRETIONARY_MANAGEMENT"
    if action_type == "CLOSE":
        reason_lower = action_reason.lower()
        if any(kw in reason_lower for kw in _BOOKKEEPING_CLOSE_REASONS):
            return "LIFECYCLE_BOOKKEEPING"
        return "DISCRETIONARY_MANAGEMENT"
    return "UNKNOWN"


def run_mgmt2() -> dict[str, Any]:
    """
    MGMT-2: Which management action types appear helpful, harmful, or neutral?

    Per-action-type outcome analysis with explicit CLOSE semantics
    classification. OBSERVATIONAL ASSOCIATION ONLY.
    """
    from research_engine.experiments.exit_management import (
        _confidence, _make_report, _MIN_SAMPLE,
    )

    actions = _load_actions()
    outcomes = _load_outcomes()

    managed_actions, outcome_by_id, managed_trade_ids = build_trade_level_population(
        actions, outcomes)

    # Join actions to outcomes via trade_id or correlation_id
    action_type_trades: dict[str, list[dict[str, Any]]] = defaultdict(list)
    action_semantics: dict[str, str] = {}
    seen_trade_action: set[tuple[str, str]] = set()  # (trade_id, action_type) dedup

    for a in managed_actions_flat(actions):
        tid = a["trade_id"]
        cid = a["correlation_id"]
        atype = a["action_type"]
        areason = a["action_reason"]

        # find outcome for this trade
        outcome = outcome_by_id.get(tid) or outcome_by_id.get(cid)
        if outcome is None:
            continue

        # dedup: one trade per action_type (avoid double-counting retries)
        dedup_key = (tid or cid, atype)
        if dedup_key in seen_trade_action:
            continue
        seen_trade_action.add(dedup_key)

        semantics = _classify_action_semantics(atype, areason)
        action_type_trades[atype].append(outcome)
        action_semantics[atype] = semantics

    n_with_outcome = sum(len(trades) for trades in action_type_trades.values())
    if n_with_outcome < _MIN_SAMPLE_MGMT2:
        return _make_report(
            question_id="MGMT-2",
            status="INSUFFICIENT_DATA",
            overall={
                "finding": f"Insufficient action-type outcome data: N={n_with_outcome} < {_MIN_SAMPLE_MGMT2}",
                "action_types_found": sorted(action_type_trades.keys()),
                "trades_with_outcomes": n_with_outcome,
            },
            confidence="INSUFFICIENT_DATA" if n_with_outcome < 10 else "LOW",
            dataset={"source": "management_actions_v1 + trade_truth_v1", "sample_size": n_with_outcome},
            recommendation="WAIT",
        )

    # Per-action-type analysis
    by_type: dict[str, dict[str, Any]] = {}
    for atype in sorted(action_type_trades.keys()):
        trades = action_type_trades[atype]
        stats = _descriptive(trades)
        semantics = action_semantics.get(atype, "UNKNOWN")
        sufficient = stats["n"] >= _MIN_SAMPLE_MGMT2

        by_type[atype] = {
            "semantics": semantics,
            "sufficient_n": sufficient,
            **stats,
        }

    # Determine overall recommendation
    types_with_data = {t: d for t, d in by_type.items() if d["sufficient_n"]}
    if not types_with_data:
        recommendation = "INSUFFICIENT_PER_TYPE_DATA"
    else:
        # check if discretionary types show better/worse outcomes
        discretionary = {t: d for t, d in types_with_data.items()
                         if d.get("semantics") == "DISCRETIONARY_MANAGEMENT"}
        if not discretionary:
            recommendation = "INSUFFICIENT_DISCRETIONARY_DATA"
        else:
            recommendation = "FINDING: per-action-type outcome profile computed"

    return _make_report(
        question_id="MGMT-2",
        status="COMPLETE" if types_with_data else "INSUFFICIENT_DATA",
        overall={
            "finding": f"Action-type outcome analysis: {len(by_type)} types, "
                       f"{sum(d['n'] for d in by_type.values())} trades with outcomes. "
                       f"{len(types_with_data)} types have sufficient N.",
            "sample_size": n_with_outcome,
            "by_action_type": by_type,
            "methodology": "observational per-action-type outcome association (not causal)",
        },
        confidence=_confidence(n_with_outcome),
        dataset={
            "source": "management_actions_v1 + trade_truth_v1",
            "sample_size": n_with_outcome,
        },
        recommendation=recommendation,
        assumptions=[
            "OBSERVATIONAL ASSOCIATION ONLY — management actions are not randomly assigned",
            "Selection confounding expected: SLTP_MODIFY occurs when the management layer decides to adjust levels",
            "CLOSE actions classified by action_reason: lifecycle bookkeeping (TP/SL hit) vs discretionary",
            "Multiple actions per trade deduplicated by (trade_id, action_type)",
            "Broker acceptance/rejection of management actions lives in execution_attempts, not management_actions",
        ],
        warnings=[
            "OBSERVATIONAL — action types are not independent treatment conditions",
            "Per-type sample sizes may be small; check sufficient_n before drawing conclusions",
        ],
    )


def managed_actions_flat(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicated flat action records."""
    return build_action_population(actions)


def _descriptive_m2(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """Descriptive statistics for MGMT-2 action-type groups."""
    return _descriptive(trades)
