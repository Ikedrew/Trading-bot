"""
P1 — Promotion Impact Analysis (leakage-safe counterfactual policy design).

Question:
    If a specific recommendation is promoted into production, what measurable
    change in EV, win rate, trade frequency, and risk is expected?

A "promotion" here is a CANDIDATE REMOVAL POLICY: exclude opportunities whose
PRE-OUTCOME pattern group is judged unfavourable. The counterfactual design is:

  - TREATMENT membership (which opportunities the policy removes) is defined
    from PRE-OUTCOME information only (pattern group). It is NEVER defined from
    realised outcome.
  - The candidate policy (which negative-EV pattern groups to remove) is LEARNED
    on an EARLIER discovery partition, FROZEN, then applied UNCHANGED to a LATER
    unseen validation partition.
  - The effect is the within-validation contrast: status-quo (keep all
    validation opportunities) vs candidate policy (drop removed-pattern
    validation opportunities). Both use the same later opportunities, so there
    is no future leakage and no incomparable-population mixing.

This is an OBSERVATIONAL COUNTERFACTUAL on CURRENT shadow evidence — not a causal
proof. One canonical_opportunity_id is one independent observation; account
fanout and repeated horizons collapse; MISSING outcomes are excluded (never 0R).

This module is PURELY RESEARCH. It does NOT modify trading logic.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from typing import Any

from research_engine.experiments.experiment_base import (
    ReadinessStatus,
    build_fingerprint,
    build_report,
    load_shadow_trades,
    persist_report,
    update_knowledge_map,
)
# Reuse the canonical CURRENT shadow outcome pairing (one observation per
# canonical_opportunity_id; account fanout / repeated horizons collapse; missing
# outcomes excluded; conflicts fail closed) established in RW2/RW3/OPP-1.
from research_engine.experiments.market_prediction_rw2 import (
    build_opportunity_observations,
)

P1_REPORT_FILENAME = "p1_promotion_impact.json"
PROMOTION_POLICY_VERSION = "p1_pattern_removal_policy_v1"

_P1_MIN_TOTAL = 100          # valid paired canonical opportunities
_P1_MIN_DISCOVERY = 60
_P1_MIN_VALIDATION = 40
_P1_MIN_GROUP = 15           # per removed/kept validation side for a directional claim
_P1_MIN_PATTERN_CELL = 10    # discovery cell size to be eligible for the policy
_P1_REMOVE_EV_THRESHOLD = -0.2   # discovery pattern EV below this is a removal candidate


def _finding(status: str, discovery_delta: float | None, validation_delta: float | None) -> str:
    """Counterfactual finding: does the candidate policy's discovery benefit
    persist as an EV improvement on later unseen validation opportunities?"""
    if status != "COMPLETE":
        return "INSUFFICIENT_EVIDENCE"
    if discovery_delta is None or validation_delta is None:
        return "NO_RELIABLE_COUNTERFACTUAL_SIGNAL"
    # discovery_delta / validation_delta = policy_EV - status_quo_EV.
    if discovery_delta > 0 and validation_delta > 0:
        return "COUNTERFACTUAL_SUPPORT"
    if discovery_delta > 0 and validation_delta <= 0:
        return "DISCOVERY_ONLY"
    if validation_delta < 0 and discovery_delta < 0:
        return "COUNTERFACTUAL_HARM_SIGNAL"
    return "NO_RELIABLE_COUNTERFACTUAL_SIGNAL"


def _group_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"n": 0, "mean_r": None, "median_r": None, "win_rate": None}
    values = [r["outcome_r"] for r in rows]
    return {
        "n": len(rows),
        "mean_r": round(sum(values) / len(values), 4),
        "median_r": round(statistics.median(values), 4),
        "win_rate": round(sum(1 for v in values if v > 0) / len(values), 4),
    }


def _max_drawdown_r(rows: list[dict[str, Any]]) -> float | None:
    """Deterministic max drawdown (R units) of the chronological cumulative-R
    curve for a set of opportunity observations. Diagnostic only."""
    if not rows:
        return None
    ordered = sorted(rows, key=lambda r: (r["_order"], r["canonical_opportunity_id"]))
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    for row in ordered:
        cumulative += row["outcome_r"]
        peak = max(peak, cumulative)
        max_dd = max(max_dd, peak - cumulative)
    return round(max_dd, 4)


def build_p1_observations(
    shadow_trades: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """One opportunity observation per canonical_opportunity_id carrying its
    PRE-OUTCOME pattern group and its CURRENT shadow realised R.

    Reuses build_opportunity_observations: account fanout and repeated horizons
    collapse; MISSING outcomes are excluded (never 0R); conflicting evidence
    fails closed.
    """
    outcomes, diagnostics = build_opportunity_observations(shadow_trades)
    rows: list[dict[str, Any]] = []
    missing = 0
    for obs in outcomes:
        if obs.outcome_r is None:
            missing += 1
            continue
        rows.append({
            "canonical_opportunity_id": obs.canonical_opportunity_id,
            "pattern": (getattr(obs, "pattern", "") or "UNKNOWN"),  # pre-outcome group
            "outcome_r": obs.outcome_r,                              # explicit 0.0 valid
            "won": 1.0 if obs.outcome_r > 0 else 0.0,
            "_order": obs.decision_time,
        })
    rows.sort(key=lambda r: (r["_order"], r["canonical_opportunity_id"]))
    return rows, {
        **diagnostics,
        "paired_opportunities": len(rows),
        "missing_outcomes_excluded": missing,
        "conflicting_opportunities": tuple(diagnostics.get("ambiguous_opportunities", ())),
    }


def _split(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    unique = sorted({r["_order"] for r in rows})
    if len(unique) < 2:
        return rows, []
    index = max(1, min(len(unique) - 1, int(len(unique) * 0.60)))
    boundary = unique[index]
    return ([r for r in rows if r["_order"] < boundary],
            [r for r in rows if r["_order"] >= boundary])


def learn_removal_policy(discovery: list[dict[str, Any]]) -> tuple[frozenset[str], list[dict[str, Any]]]:
    """Learn the candidate removal policy on DISCOVERY ONLY.

    A pattern group is a removal candidate when it has >= _P1_MIN_PATTERN_CELL
    discovery observations and a discovery mean R below _P1_REMOVE_EV_THRESHOLD.
    Returns (removed_patterns, per_pattern_discovery_diagnostics). The policy is
    frozen here and applied unchanged to validation; validation never redefines it.
    """
    by_pattern: dict[str, list[float]] = defaultdict(list)
    for row in discovery:
        by_pattern[row["pattern"]].append(row["outcome_r"])
    removed: set[str] = set()
    diagnostics: list[dict[str, Any]] = []
    for pattern, values in sorted(by_pattern.items()):
        mean_r = sum(values) / len(values)
        eligible = len(values) >= _P1_MIN_PATTERN_CELL
        is_removed = eligible and mean_r < _P1_REMOVE_EV_THRESHOLD
        if is_removed:
            removed.add(pattern)
        diagnostics.append({
            "pattern": pattern,
            "discovery_n": len(values),
            "discovery_mean_r": round(mean_r, 4),
            "eligible": eligible,
            "removed": is_removed,
        })
    return frozenset(removed), diagnostics


def analyse(shadow_trades: list[dict[str, Any]]) -> dict[str, Any]:
    rows, diagnostics = build_p1_observations(shadow_trades)
    discovery, validation = _split(rows)

    removed_patterns, pattern_diagnostics = learn_removal_policy(discovery)

    # Apply the FROZEN policy to each partition. TREATMENT membership (removed vs
    # kept) is pre-outcome (pattern), never from realised R.
    def _partition(part: list[dict[str, Any]]):
        removed = [r for r in part if r["pattern"] in removed_patterns]
        kept = [r for r in part if r["pattern"] not in removed_patterns]
        return removed, kept

    def _policy_delta(part: list[dict[str, Any]]) -> float | None:
        """policy EV (kept only) minus status-quo EV (all): the promotion effect."""
        if not part:
            return None
        _removed, kept = _partition(part)
        if not kept:
            return None
        status_quo_ev = sum(r["outcome_r"] for r in part) / len(part)
        policy_ev = sum(r["outcome_r"] for r in kept) / len(kept)
        return policy_ev - status_quo_ev

    d_removed, d_kept = _partition(discovery)
    v_removed, v_kept = _partition(validation)
    discovery_delta = _policy_delta(discovery)
    validation_delta = _policy_delta(validation)

    status_quo_val = _group_stats(validation)
    policy_val = _group_stats(v_kept)
    removed_val = _group_stats(v_removed)

    # Impact metrics (validation), all in R / rate units.
    val_win_rate_delta = (
        policy_val["win_rate"] - status_quo_val["win_rate"]
        if policy_val["win_rate"] is not None and status_quo_val["win_rate"] is not None
        else None
    )
    val_frequency_delta_pct = (
        round((len(v_kept) - len(validation)) / len(validation) * 100.0, 2)
        if validation else None
    )
    status_quo_dd = _max_drawdown_r(validation)
    policy_dd = _max_drawdown_r(v_kept)
    val_drawdown_delta_r = (
        round(policy_dd - status_quo_dd, 4)
        if policy_dd is not None and status_quo_dd is not None else None
    )

    paired = len(rows)
    if diagnostics["conflicting_opportunities"]:
        status = ReadinessStatus.BLOCKED
        reason = "Conflicting outcome evidence for a canonical opportunity"
    elif not removed_patterns:
        # No candidate policy exists on discovery -> nothing to evaluate; the
        # design is valid but there is no promotion to test yet.
        if paired < _P1_MIN_TOTAL or len(discovery) < _P1_MIN_DISCOVERY or len(validation) < _P1_MIN_VALIDATION:
            status = ReadinessStatus.INSUFFICIENT_DATA
            reason = (
                f"paired/discovery/validation={paired}/{len(discovery)}/{len(validation)}; "
                f"need {_P1_MIN_TOTAL}/{_P1_MIN_DISCOVERY}/{_P1_MIN_VALIDATION}"
            )
        else:
            status = ReadinessStatus.COMPLETE
            reason = "No discovery pattern group met the removal criterion; candidate promotion policy is empty (no impact)"
    elif paired < _P1_MIN_TOTAL or len(discovery) < _P1_MIN_DISCOVERY or len(validation) < _P1_MIN_VALIDATION:
        status = ReadinessStatus.INSUFFICIENT_DATA
        reason = (
            f"paired/discovery/validation={paired}/{len(discovery)}/{len(validation)}; "
            f"need {_P1_MIN_TOTAL}/{_P1_MIN_DISCOVERY}/{_P1_MIN_VALIDATION}"
        )
    elif len(v_removed) < _P1_MIN_GROUP or len(v_kept) < _P1_MIN_GROUP:
        status = ReadinessStatus.INSUFFICIENT_DATA
        reason = (
            f"validation removed/kept={len(v_removed)}/{len(v_kept)}; "
            f"need >= {_P1_MIN_GROUP} in each side"
        )
    else:
        status = ReadinessStatus.COMPLETE
        reason = "Chronological counterfactual promotion-policy evaluation completed on later unseen opportunities"

    finding = (
        "NO_CANDIDATE_POLICY" if (status == ReadinessStatus.COMPLETE and not removed_patterns)
        else _finding(status if isinstance(status, str) else str(status), discovery_delta, validation_delta)
    )

    overall = {
        "canonical_question": "P1",
        "hypothesis": "A candidate promotion policy that removes pre-outcome-defined unfavourable pattern groups improves realised-R expectancy (with reported win-rate, trade-frequency and drawdown effects), and that improvement persists from earlier discovery into later unseen validation opportunities.",
        "null_hypothesis": "The candidate promotion policy does not improve realised-R expectancy on later unseen opportunities, or any discovery improvement does not persist.",
        "research_classification": "observational_counterfactual_promotion_policy",
        "causal_claim": "NONE — observational counterfactual on shadow evidence; not proof the policy causes profit in production",
        "inference_limitation": "Treatment membership is pre-outcome (pattern group); the policy is learned on discovery, frozen, and evaluated on later unseen validation. This supports a leakage-safe counterfactual comparison, NOT causal identification.",
        "unit_of_analysis": "one canonical_opportunity_id; account fanout and repeated horizons collapse",
        "treatment_definition": "candidate promotion policy = remove opportunities whose pre-outcome pattern group is a discovery removal candidate (discovery n>=10 and discovery mean R < -0.2R)",
        "comparator_definition": "status-quo (keep all opportunities) vs candidate policy (drop removed-pattern opportunities), contrasted WITHIN the same later-unseen validation opportunities",
        "counterfactual_authority": "pre-outcome pattern group (decision_snapshot.pattern) determines treatment membership; realised R is used only to EVALUATE the frozen policy, never to define it",
        "policy_version": PROMOTION_POLICY_VERSION,
        "evidence_authority": "CURRENT shadow decision_snapshot.pattern (pre-outcome) + simulated_outcome.pnl_r_multiple (outcome), one per canonical_opportunity_id",
        "outcome_authority": "CURRENT shadow simulated_outcome.pnl_r_multiple",
        "evidence_epoch": "CURRENT",
        "removed_patterns": sorted(removed_patterns),
        "discovery_pattern_diagnostics": pattern_diagnostics,
        "paired_opportunities": paired,
        "missing_outcome_count": diagnostics["missing_outcomes_excluded"],
        "discovery": {
            "n": len(discovery),
            "removed_n": len(d_removed),
            "kept_n": len(d_kept),
            "policy_minus_status_quo_ev_r": (round(discovery_delta, 4) if discovery_delta is not None else None),
        },
        "later_unseen_validation": {
            "n": len(validation),
            "removed_n": len(v_removed),
            "kept_n": len(v_kept),
            "status_quo": status_quo_val,
            "policy_kept": policy_val,
            "removed_side": removed_val,
            "policy_minus_status_quo_ev_r": (round(validation_delta, 4) if validation_delta is not None else None),
            "win_rate_delta": (round(val_win_rate_delta, 4) if val_win_rate_delta is not None else None),
            "trade_frequency_delta_pct": val_frequency_delta_pct,
            "status_quo_max_drawdown_r": status_quo_dd,
            "policy_max_drawdown_r": policy_dd,
            "drawdown_delta_r": val_drawdown_delta_r,
        },
        "effect_metric": {
            "name": "policy_minus_status_quo_ev_r",
            "units": "R-multiple (realised R expectancy difference)",
            "definition": "mean realised R of policy-kept validation opportunities minus mean realised R of all validation opportunities",
        },
        "finding_classification": finding,
        "completion_reason": reason,
        "limitations": [
            "Observational counterfactual on shadow evidence; no causal claim and no production change.",
            "Treatment (removed) membership is pre-outcome pattern group; never derived from realised R.",
            "Policy is learned on discovery, frozen, and evaluated on later unseen validation; validation never redefines it.",
            "One canonical opportunity is one observation; account fanout and repeated horizons never inflate n.",
            "MISSING outcomes are excluded, never imputed to 0R; explicit realised 0.0R is valid.",
            "Drawdown is a deterministic cumulative-R diagnostic, not a broker equity curve.",
            "COMPLETE means the counterfactual evaluation ran validly, not that the promotion is beneficial.",
        ],
        "diagnostics": diagnostics,
        "sufficiency": {
            "minimum_total": _P1_MIN_TOTAL,
            "minimum_discovery": _P1_MIN_DISCOVERY,
            "minimum_validation": _P1_MIN_VALIDATION,
            "minimum_group": _P1_MIN_GROUP,
            "minimum_pattern_cell": _P1_MIN_PATTERN_CELL,
        },
    }

    confidence = "MEDIUM" if status == ReadinessStatus.COMPLETE else "INSUFFICIENT_DATA"
    if status == ReadinessStatus.COMPLETE and removed_patterns:
        recommendation = (
            f"OBSERVATIONAL COUNTERFACTUAL FINDING [{finding}]: validation policy-minus-status-quo EV "
            + (f"{validation_delta:+.4f}R" if validation_delta is not None else "N/A")
            + f" (discovery " + (f"{discovery_delta:+.4f}R)" if discovery_delta is not None else "N/A)")
        )
    elif status == ReadinessStatus.COMPLETE:
        recommendation = f"OBSERVATIONAL COUNTERFACTUAL FINDING [{finding}]: no candidate promotion policy on discovery"
    else:
        recommendation = f"{getattr(status, 'value', status)}: {reason}"

    report = build_report(
        question_id="P1", status=status,
        overall=overall, confidence=confidence,
        dataset={
            "source": "shadow_trades",
            "sample_size": paired,
            "independent_observations": paired,
            "removed_patterns": len(removed_patterns),
        },
        fingerprint=build_fingerprint(
            paired, max(0, diagnostics.get("observations", paired) - paired),
            source="shadow_trades", validation_score=confidence, epoch="CURRENT",
        ),
        recommendation=recommendation,
        assumptions=[
            "Independent unit = canonical_opportunity_id; account fanout and repeated horizons collapse.",
            "Treatment = pre-outcome pattern-group removal policy; membership never derived from realised outcome.",
            "Candidate policy learned on discovery, frozen, evaluated on later unseen validation; no validation tuning.",
            "Effect metric = policy-kept validation EV minus status-quo validation EV (R units).",
            "MISSING outcomes excluded, never imputed to 0R; explicit 0.0R valid; conflicts fail closed.",
            "Observational counterfactual on shadow evidence; no causal claim; no production change.",
        ],
        warnings=[],
        provenance={
            "experiment_module": "research_engine.experiments.promotion_impact",
            "registry_id": "P1",
            "function": "run_promotion_impact",
            "report_filename": P1_REPORT_FILENAME,
            "policy_version": PROMOTION_POLICY_VERSION,
            "partition": "deterministic chronological 60/40 by opportunity time",
            "pipeline": "Question -> Experiment -> Dataset -> Output -> Knowledge -> Command Centre",
        },
    )
    return report


def run_promotion_impact(shadow_trades: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Run P1 as a leakage-safe counterfactual promotion-policy evaluation."""
    if shadow_trades is None:
        shadow_trades = load_shadow_trades(epoch="CURRENT")
    report = analyse(shadow_trades)
    persist_report(report, P1_REPORT_FILENAME)
    update_knowledge_map("P1", report["overall"]["finding_classification"], report["recommendation"])
    return report


if __name__ == "__main__":
    result = run_promotion_impact()
    o = result.get("overall", {})
    print(f"P1: finding={o.get('finding_classification')} removed={o.get('removed_patterns')} status={result.get('status')}")
