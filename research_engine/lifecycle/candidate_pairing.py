"""
Candidate Prospective Pairing — the SINGLE honest candidate↔incumbent pairing contract.

This module is the one implementation of candidate prospective pairing. Both
the pair counter (``candidate_auto_evaluator``) and the statistical evaluator
(``candidate_evaluator``) consume it, so the counted population and the
evaluated population can never drift.

PAIRING CONTRACT (deterministic, one-to-one, lineage-exact)
===========================================================

Candidate side — the candidate's prospective simulated outcome:
    Source dataset:  ``shadow_trades`` (V1 STR shape, written by
    ShadowTradeEngine when a candidate shadow closes).
    Identification:  ``identity.shadow_type == "CANDIDATE_<candidate_id>"``
                     (minted by candidate_shadow_hook.open_candidate_shadows).
    Completeness:    only CLOSE records count (``event_type == "CLOSE"``;
                     historical rows without event_type are CLOSE by contract).
    Outcome:         ``simulated_outcome.pnl_r_multiple`` (+ mfe_r / mae_r).

Incumbent side — what the DEPLOYED logic actually did on the same opportunity:
    Source dataset:  ``trade_truth`` (the authoritative realised-outcome
                     dataset). The candidate shadow hook runs ONLY on the
                     EXECUTE path (core/runtime/engine_execution_handler.py)
                     and opens the candidate shadow with the SAME
                     ``correlation_id`` under which the live trade executes and
                     trade_truth is minted (correlation propagation contract).
    Identification:  ``identity.correlation_id`` exact string equality with the
                     candidate shadow's correlation_id.
    Completeness:    ``outcome.r_multiple_realised`` present, correlation_id
                     non-empty.

Why trade_truth is the incumbent (and why the horizon-shadow lineage is NOT):
    - The retired ``V10_PRIMARY`` baseline no longer exists (Phase 1I-C).
    - The canonical horizon-shadow lineage (``nshadow_*`` /
      ``HORIZON_ALTERNATIVE``) uses a DIFFERENT geometry source
      (STRUCTURE_BASED vs decision intent) and a different identity model;
      Phase 1I-C explicitly declined to substitute it as a baseline.
    - Candidate shadows never enter the ``shadow_runtime_v1`` stream at all
      (that stream only contains runtime-minted ``nshadow_*`` horizon shadows).
    - Therefore the strongest honest incumbent for a candidate shadow is the
      realised outcome of the deployed logic on the SAME opportunity, joined
      by the exact correlation_id minted in the same execution-prep call.

Match rule (all required; any failure → the pair is NOT built):
    1. Exact ``correlation_id`` equality (never symbol-only, time-approximate,
       PnL-similar, or list-order pairing).
    2. ``identity.symbol`` equality on both sides (cross-symbol never matches).
    3. PROSPECTIVE on both sides: candidate ``decision_snapshot.
       timestamp_decision_utc`` >= candidate activation boundary AND incumbent
       ``timestamps.entry_timestamp_broker`` >= same boundary.
    4. One-to-one: a correlation_id with >1 incumbent trade_truth row is
       AMBIGUOUS and excluded entirely; a correlation_id with >1 candidate
       close row is deduplicated only when the outcomes are identical, else
       AMBIGUOUS and excluded.
    5. Horizon guard: if BOTH sides carry a non-empty horizon and they differ,
       the pair is excluded (candidate shadows currently carry none; the guard
       protects the future schema).
    6. Complete outcomes only: records without a usable R value are never
       counted.

A candidate shadow with no honest incumbent comparator stays UNMATCHED — it is
never paired heuristically and never counted.

This module is READ-ONLY research infrastructure. It NEVER modifies trading
behaviour, shadow collection, V1 schemas, or S3 layout.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

_CANDIDATE_SHADOW_PREFIX = "CANDIDATE_"


# ═══════════════════════════════════════════════════════════════════════════════
# RESULT MODELS
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class PairingDiagnostics:
    """Explicit accounting for every excluded population — no silent drops."""

    candidate_records_total: int = 0
    candidate_for_other_or_none: int = 0      # shadow_type not CANDIDATE_<id>
    candidate_unclosed: int = 0               # OPEN / incomplete lifecycle
    candidate_missing_outcome: int = 0        # no usable R value
    candidate_empty_correlation: int = 0      # no lineage — never pairable
    candidate_before_boundary: int = 0        # pre-activation (not prospective)
    candidate_deduped: int = 0                # identical replayed closes collapsed
    candidate_ambiguous: int = 0              # same COR, conflicting outcomes
    incumbent_records_total: int = 0
    incumbent_unusable: int = 0               # no R / no correlation_id
    incumbent_ambiguous: int = 0              # >1 trade_truth row for one COR
    incumbent_before_boundary: int = 0
    symbol_mismatch: int = 0
    horizon_mismatch: int = 0
    unmatched_no_incumbent: int = 0           # candidate COR absent from truth
    matched_pairs: int = 0

    def to_dict(self) -> dict[str, int]:
        return dict(self.__dict__)


@dataclass
class PairingResult:
    """Matched pairs + the full exclusion accounting."""

    pairs: list[dict[str, Any]] = field(default_factory=list)
    diagnostics: PairingDiagnostics = field(default_factory=PairingDiagnostics)


# ═══════════════════════════════════════════════════════════════════════════════
# POPULATION LOADING (sanctioned S3 data-access layer only)
# ═══════════════════════════════════════════════════════════════════════════════


def load_pairing_populations() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Load (candidate_records, incumbent_records) via the sanctioned S3 layer.

    Candidate shadows are persisted by ShadowTradeEngine into the canonical
    ``shadow_trades`` dataset (the only dataset that carries
    ``shadow_type=CANDIDATE_*`` records); incumbent realised outcomes come from
    ``trade_truth``. Both reads go through research_engine.data_access.loaders
    (S3ResearchDataSource) — no local fallback, run-level cached.
    """
    from research_engine.data_access.loaders import (
        load_shadow_trades,
        load_trade_truth,
    )

    return load_shadow_trades(), load_trade_truth()


# ═══════════════════════════════════════════════════════════════════════════════
# FIELD EXTRACTION (production shapes only)
# ═══════════════════════════════════════════════════════════════════════════════


def _candidate_fields(rec: dict[str, Any]) -> dict[str, Any] | None:
    """Extract the pairing fields from a V1 STR candidate shadow record."""
    if not isinstance(rec, dict):
        return None
    identity = rec.get("identity") or {}
    snap = rec.get("decision_snapshot") or {}
    out = rec.get("simulated_outcome") or {}
    return {
        "trade_id": str(identity.get("trade_id", "") or ""),
        "correlation_id": str(identity.get("correlation_id", "") or ""),
        "entity_id": str(identity.get("entity_id") or ""),
        "symbol": str(identity.get("symbol", "") or ""),
        "shadow_type": str(identity.get("shadow_type") or ""),
        "timestamp": float(snap.get("timestamp_decision_utc") or 0.0),
        "r": out.get("pnl_r_multiple"),
        "mfe_r": out.get("mfe_r"),
        "mae_r": out.get("mae_r"),
        "horizon": str(snap.get("trade_horizon") or ""),
    }


def _incumbent_fields(rec: dict[str, Any]) -> dict[str, Any] | None:
    """Extract the pairing fields from a trade_truth record."""
    if not isinstance(rec, dict):
        return None
    identity = rec.get("identity") or {}
    ts = rec.get("timestamps") or {}
    outcome = rec.get("outcome") or {}
    return {
        "trade_id": str(identity.get("trade_id", "") or ""),
        "correlation_id": str(identity.get("correlation_id", "") or ""),
        "entity_id": str(identity.get("canonical_opportunity_id") or ""),
        "symbol": str(identity.get("symbol", "") or ""),
        "timestamp": float(ts.get("entry_timestamp_broker") or 0.0),
        "r": outcome.get("r_multiple_realised"),
        "mfe_r": outcome.get("mfe_r"),
        "mae_r": outcome.get("mae_r"),
        "horizon": "",
    }


def _parse_boundary(candidate_activated_at: str) -> float:
    """Parse the candidate activation ISO timestamp to unix seconds (0 on failure)."""
    if not candidate_activated_at:
        return 0.0
    from datetime import datetime

    try:
        dt = datetime.fromisoformat(str(candidate_activated_at).replace("Z", "+00:00"))
        return dt.timestamp()
    except (ValueError, TypeError):
        return 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# THE PAIRING IMPLEMENTATION (single source of truth)
# ═══════════════════════════════════════════════════════════════════════════════


def build_prospective_pairs(
    *,
    candidate_id: str,
    candidate_activated_at: str,
    candidate_records: list[dict[str, Any]] | None = None,
    incumbent_records: list[dict[str, Any]] | None = None,
) -> PairingResult:
    """Build the honest matched candidate↔incumbent prospective pairs.

    Args:
        candidate_id: the candidate whose pairs are being built.
        candidate_activated_at: ISO timestamp; observations on EITHER side
            stamped before this boundary are excluded (prospectivity).
        candidate_records: candidate-shadow CLOSE records (dataset
            ``shadow_trades``). Loaded via the sanctioned S3 layer when None.
        incumbent_records: realised incumbent outcomes (dataset
            ``trade_truth``). Loaded via the sanctioned S3 layer when None.

    Returns:
        PairingResult with chronologically ordered pairs (each carrying
        candidate_r / baseline_r / symbol / lineage identity) and full
        exclusion diagnostics. The pair list is EXACTLY the population the
        evaluator consumes — count and evaluation cannot drift.
    """
    diag = PairingDiagnostics()
    boundary = _parse_boundary(candidate_activated_at)
    expected_shadow_type = f"{_CANDIDATE_SHADOW_PREFIX}{candidate_id}"

    # ─── load populations when not injected ────────────────────────────────
    if candidate_records is None or incumbent_records is None:
        loaded_cand, loaded_inc = load_pairing_populations()
        candidate_records = candidate_records if candidate_records is not None else loaded_cand
        incumbent_records = incumbent_records if incumbent_records is not None else loaded_inc

    candidate_records = [r for r in (candidate_records or []) if isinstance(r, dict)]
    incumbent_records = [r for r in (incumbent_records or []) if isinstance(r, dict)]
    diag.candidate_records_total = len(candidate_records)
    diag.incumbent_records_total = len(incumbent_records)

    # ─── candidate side: filter to THIS candidate's complete closes ────────
    cand_by_cor: dict[str, list[dict[str, Any]]] = {}
    for rec in candidate_records:
        f = _candidate_fields(rec)
        if f is None:
            continue
        if f["shadow_type"] != expected_shadow_type:
            diag.candidate_for_other_or_none += 1
            continue
        if rec.get("event_type", "CLOSE") not in ("CLOSE",):
            diag.candidate_unclosed += 1
            continue
        if f["r"] is None:
            diag.candidate_missing_outcome += 1
            continue
        if not f["correlation_id"]:
            diag.candidate_empty_correlation += 1
            continue
        if boundary and f["timestamp"] and f["timestamp"] < boundary:
            diag.candidate_before_boundary += 1
            continue
        cand_by_cor.setdefault(f["correlation_id"], []).append(f)

    # duplicate candidate closes per opportunity: identical → dedupe;
    # conflicting → ambiguous (excluded entirely — never fabricate a pair)
    resolved_candidates: dict[str, dict[str, Any]] = {}
    for cor, rows in cand_by_cor.items():
        if len(rows) == 1:
            resolved_candidates[cor] = rows[0]
            continue
        distinct_r = {round(float(r["r"]), 6) for r in rows}
        if len(distinct_r) == 1:
            diag.candidate_deduped += len(rows) - 1
            resolved_candidates[cor] = rows[0]
        else:
            diag.candidate_ambiguous += len(rows)

    # ─── incumbent side: index by correlation_id (one-to-one required) ─────
    inc_by_cor: dict[str, list[dict[str, Any]]] = {}
    for rec in incumbent_records:
        f = _incumbent_fields(rec)
        if f is None:
            continue
        if f["r"] is None or not f["correlation_id"]:
            diag.incumbent_unusable += 1
            continue
        if boundary and f["timestamp"] and f["timestamp"] < boundary:
            diag.incumbent_before_boundary += 1
            continue
        inc_by_cor.setdefault(f["correlation_id"], []).append(f)

    resolved_incumbents: dict[str, dict[str, Any]] = {}
    for cor, rows in inc_by_cor.items():
        if len(rows) == 1:
            resolved_incumbents[cor] = rows[0]
        else:
            # >1 realised outcome for one correlation_id — ambiguous lineage,
            # excluded entirely rather than heuristically resolved.
            diag.incumbent_ambiguous += len(rows)

    # ─── deterministic one-to-one matching ─────────────────────────────────
    pairs: list[dict[str, Any]] = []
    for cor, cand in resolved_candidates.items():
        inc = resolved_incumbents.get(cor)
        if inc is None:
            diag.unmatched_no_incumbent += 1
            continue
        if cand["symbol"] != inc["symbol"]:
            diag.symbol_mismatch += 1
            continue
        if cand["horizon"] and inc["horizon"] and cand["horizon"] != inc["horizon"]:
            diag.horizon_mismatch += 1
            continue
        pairs.append({
            "candidate_id": candidate_id,
            "correlation_id": cor,
            "entity_id": cand["entity_id"] or inc["entity_id"],
            "symbol": cand["symbol"],
            "candidate_trade_id": cand["trade_id"],
            "incumbent_trade_id": inc["trade_id"],
            "candidate_r": float(cand["r"]),
            "baseline_r": float(inc["r"]),
            "candidate_mfe_r": cand["mfe_r"],
            "candidate_mae_r": cand["mae_r"],
            "baseline_mfe_r": inc["mfe_r"],
            "baseline_mae_r": inc["mae_r"],
            "candidate_timestamp": cand["timestamp"],
            "incumbent_timestamp": inc["timestamp"],
            "timestamp": cand["timestamp"],  # chronological key (OOS split)
            "candidate_horizon": cand["horizon"],
            "incumbent_horizon": inc["horizon"],
        })

    # chronological ordering (temporal stability / OOS split depend on it)
    pairs.sort(key=lambda p: (p["timestamp"], p["correlation_id"]))
    diag.matched_pairs = len(pairs)

    if diag.candidate_ambiguous or diag.incumbent_ambiguous:
        logger.warning(
            "[CANDIDATE_PAIRING] ambiguous lineage excluded: "
            "candidate_ambiguous=%d incumbent_ambiguous=%d candidate=%s",
            diag.candidate_ambiguous, diag.incumbent_ambiguous, candidate_id,
        )
    logger.info(
        "[CANDIDATE_PAIRING] candidate=%s matched=%d (cand_rows=%d inc_rows=%d "
        "no_incumbent=%d pre_boundary=%d/%d ambiguous=%d/%d)",
        candidate_id, len(pairs), diag.candidate_records_total,
        diag.incumbent_records_total, diag.unmatched_no_incumbent,
        diag.candidate_before_boundary, diag.incumbent_before_boundary,
        diag.candidate_ambiguous, diag.incumbent_ambiguous,
    )
    return PairingResult(pairs=pairs, diagnostics=diag)


def count_prospective_pairs(
    *,
    candidate_id: str,
    candidate_activated_at: str,
    candidate_records: list[dict[str, Any]] | None = None,
    incumbent_records: list[dict[str, Any]] | None = None,
) -> int:
    """Count honest matched prospective pairs for a candidate.

    The count is computed by the SAME implementation the evaluator consumes
    (``build_prospective_pairs``) — count N always equals the pair population
    the evaluator can build.
    """
    return len(build_prospective_pairs(
        candidate_id=candidate_id,
        candidate_activated_at=candidate_activated_at,
        candidate_records=candidate_records,
        incumbent_records=incumbent_records,
    ).pairs)
