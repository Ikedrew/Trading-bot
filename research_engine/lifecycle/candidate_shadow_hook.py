"""
Candidate Shadow Hook — Opens paired candidate shadow observations.

Called from engine_execution_handler.py after shadow observations are opened.
For each candidate in SHADOW_TESTING status, opens an additional shadow trade
with the candidate's modified geometry.

Wave 4D.1 — treatment fidelity contract (FAIL CLOSED):
    DECLARED treatment (change_definition) must equal the APPLIED treatment
    and must equal the RECORDED treatment provenance. Resolution is
    canonical (resolve_candidate_treatment): supported types are
    direction_inversion, geometry_modification (stop_multiplier REQUIRED),
    symbol_exclusion (opens no shadow by design). Unsupported/malformed
    treatments (including regime_conditioning, which previously SILENTLY
    fell back to baseline geometry) produce NO candidate observation.
    Deterministic treatment_id (canonical JSON + SHA-256, no wall-clock) is
    embedded in the persisted trade_id as provenance. No production/baseline
    state is ever touched.

CONTRACT:
    - Observation-only: never modifies production configuration
    - Never calls MT5Execution or broker
    - Never alters the V10 decision or existing shadow
    - Never prevents trade execution
    - Failure is contained (log + skip; never crashes the cycle)
    - Uses existing ShadowTradeEngine.open_trade()
    - Preserves correlation_id and entity_id for pairing

PAIRED OBSERVATION MODEL:
    Same opportunity (correlation_id, entity_id):
        CANDIDATE_{id}   → candidate counterfactual outcome

    RETIRED (Phase 1I-C): the legacy V10_PRIMARY baseline shadow type is
    removed from the architecture and is no longer created at runtime
    (live_scanner emits only the canonical HORIZON_ALTERNATIVE lineage).
    Candidate shadows are paired with the DEPLOYED logic's realised outcome
    (trade_truth) on the same opportunity via the exact execution
    correlation_id — see research_engine.lifecycle.candidate_pairing for the
    honest pairing contract used by evaluation.

This module NEVER modifies production V10.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# Length of the deterministic treatment-identity hex digest.
_TREATMENT_ID_LEN = 16

# ─── Wave 4D.1: treatment fidelity contract ──────────────────────────────────
# Types the shadow runtime can ACTUALLY apply as a DISTINCT treatment.
# regime_conditioning is deliberately NOT resolvable: the runtime cannot apply
# a regime-conditioned treatment today. Returning baseline geometry for it
# (the previous behaviour) was a SILENT BASELINE FALLBACK and is now fail
# closed — such candidates produce NO candidate observation.
_RESOLVABLE_TREATMENT_TYPES: set[str] = {
    "direction_inversion",
    "geometry_modification",
    "symbol_exclusion",
}


@dataclass(frozen=True)
class TreatmentResolution:
    """Canonical resolution of a candidate's declared treatment.

    declared    — the treatment parameters taken from change_definition
                  (canonicalised; no implicit defaults invented).
    params      — the EXACT shadow geometry that will be applied
                  (direction / stop_loss / take_profit / treatment_id).
    treatment_id— deterministic identity over (change_type, declared,
                  applied); no wall-clock input. Equivalent declared
                  treatments on the same opportunity → same identity;
                  materially different declarations → different identity.
    """

    change_type: str
    declared: dict[str, Any]
    params: dict[str, Any]
    treatment_id: str


def _canonical_treatment_id(
    change_type: str, declared: dict[str, Any], applied: dict[str, Any]
) -> str:
    """Deterministic treatment identity (canonical JSON + SHA-256)."""
    payload = json.dumps(
        {"change_type": change_type, "declared": declared, "applied": applied},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:_TREATMENT_ID_LEN]


def resolve_candidate_treatment(
    *,
    change_definition: dict[str, Any],
    direction: str,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    risk_distance: float,
    symbol: str,
    pattern: str,
) -> tuple[TreatmentResolution | None, str]:
    """
    Wave 4D.1 — canonical treatment resolution (FAIL CLOSED).

    Resolves a candidate's declared change_definition into the EXACT
    treatment the shadow runtime will apply, with a deterministic treatment
    identity. Invariant: DECLARED == APPLIED == identity-RECORDED.

    Returns (resolution|None, reason). None is a fail-closed skip:
        missing_change_type | malformed_change_definition |
        missing_stop_multiplier | malformed_stop_multiplier |
        missing_symbol | unsupported_change_type: <type> |
        regime_conditioning_not_applicable | symbol_exclusion_no_shadow |
        malformed_computed_geometry
    Only "symbol_exclusion_no_shadow" is a correct-by-design skip (an
    exclusion candidate opens NO shadow on any symbol); every other reason
    is a treatment-fidelity failure.
    """
    if not isinstance(change_definition, dict):
        return None, "malformed_change_definition"
    change_type = change_definition.get("type", "")
    if not change_type:
        return None, "missing_change_type"

    params: dict[str, Any]
    declared: dict[str, Any]

    if change_type == "direction_inversion":
        # Fixed, declared-by-contract treatment: invert direction, keep the
        # original risk distance, take profit at 3R. No declared parameters.
        declared = {}
        inv_dir = "BUY" if direction == "SELL" else "SELL"
        if inv_dir == "BUY":
            new_sl = entry_price - risk_distance
            new_tp = entry_price + risk_distance * 3.0
        else:
            new_sl = entry_price + risk_distance
            new_tp = entry_price - risk_distance * 3.0
        params = {"direction": inv_dir, "stop_loss": new_sl, "take_profit": new_tp}

    elif change_type == "geometry_modification":
        # The multiplier is a DECLARED treatment parameter. There is NO
        # implicit default: a missing/malformed multiplier fails closed
        # (previously it silently substituted 1.5 — declared != applied).
        if "stop_multiplier" not in change_definition:
            return None, "missing_stop_multiplier"
        raw = change_definition["stop_multiplier"]
        if (
            isinstance(raw, bool)
            or not isinstance(raw, (int, float))
            or not math.isfinite(float(raw))
            or float(raw) <= 0
        ):
            return None, "malformed_stop_multiplier"
        multiplier = float(raw)
        declared = {"stop_multiplier": multiplier}
        if direction == "BUY":
            new_sl = entry_price - risk_distance * multiplier
        else:
            new_sl = entry_price + risk_distance * multiplier
        params = {
            "direction": direction,
            "stop_loss": new_sl,
            "take_profit": take_profit,  # original TP preserved by contract
        }

    elif change_type == "symbol_exclusion":
        excluded = change_definition.get("symbol", "")
        if not isinstance(excluded, str) or not excluded.strip():
            return None, "missing_symbol"
        # Exclusion candidates never open a candidate shadow (the treatment
        # is "don't trade this symbol" — there is nothing to observe).
        return None, "symbol_exclusion_no_shadow"

    elif change_type == "regime_conditioning":
        return None, (
            "regime_conditioning_not_applicable: the current shadow runtime "
            "cannot apply a regime-conditioned treatment (fail closed — no "
            "silent baseline fallback)"
        )

    else:
        return None, f"unsupported_change_type: {change_type}"

    # Applied geometry must be real numbers — never NaN/inf masquerading as
    # a treatment.
    for key in ("stop_loss", "take_profit"):
        if not math.isfinite(float(params[key])):
            return None, "malformed_computed_geometry"

    treatment_id = _canonical_treatment_id(change_type, declared, params)
    params = {**params, "treatment_id": treatment_id}
    return (
        TreatmentResolution(
            change_type=change_type,
            declared=declared,
            params=params,
            treatment_id=treatment_id,
        ),
        "ok",
    )


def _translate_change_definition(
    *,
    change_definition: dict[str, Any],
    direction: str,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    risk_distance: float,
    symbol: str,
    pattern: str,
) -> dict[str, Any] | None:
    """
    Backward-compatible shim over resolve_candidate_treatment().

    Returns the resolved shadow parameters (including treatment_id) or None
    when the treatment fails closed. Reason detail is available from
    resolve_candidate_treatment() directly.
    """
    resolution, _reason = resolve_candidate_treatment(
        change_definition=change_definition,
        direction=direction,
        entry_price=entry_price,
        stop_loss=stop_loss,
        take_profit=take_profit,
        risk_distance=risk_distance,
        symbol=symbol,
        pattern=pattern,
    )
    if resolution is None:
        return None
    return dict(resolution.params)



def open_candidate_shadows(
    *,
    symbol: str,
    cycle_id: int,
    direction: str,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    entry_time: float,
    entry_bar_index: int,
    correlation_id: str,
    entity_id: str,
    pattern: str,
    score: float,
    bid: float,
    ask: float,
    strategy: str = "",
) -> int:
    """
    Open candidate shadow observations for all active SHADOW_TESTING candidates.
    
    Called after the canonical shadow observations are created. Uses the same
    opportunity context but applies candidate-specific geometry modifications.
    
    Returns number of candidate shadows opened.
    Never raises — all errors are logged and suppressed.
    """
    count = 0
    try:
        from research_engine.v10.candidates.candidate_registry import CandidateRegistry
        from research_engine.v10.candidates.models import CandidateStatus
        from core.shadow_trades import get_shadow_engine

        registry = CandidateRegistry()
        candidates = registry.list_by_status(CandidateStatus.SHADOW_TESTING)

        if not candidates:
            return 0

        risk_distance = abs(entry_price - stop_loss)
        if risk_distance <= 0:
            return 0

        engine = get_shadow_engine()

        for candidate in candidates:
            try:
                resolution, reason = resolve_candidate_treatment(
                    change_definition=candidate.change_definition,
                    direction=direction,
                    entry_price=entry_price,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    risk_distance=risk_distance,
                    symbol=symbol,
                    pattern=pattern,
                )

                if resolution is None:
                    # Wave 4D.1 fail-closed: NO candidate observation is
                    # produced for an unresolvable/malformed/unsupported
                    # treatment — never a silent baseline fallback.
                    # symbol_exclusion_no_shadow is the one correct-by-design
                    # skip (an exclusion opens no shadow); anything else is a
                    # treatment-fidelity failure worth a WARNING.
                    if reason == "symbol_exclusion_no_shadow":
                        logger.debug(
                            "[CANDIDATE_SHADOW] %s: %s (by design)",
                            candidate.candidate_id, reason,
                        )
                    else:
                        logger.warning(
                            "[CANDIDATE_SHADOW_FAIL_CLOSED] candidate=%s "
                            "treatment not resolvable: %s — no candidate "
                            "observation emitted",
                            candidate.candidate_id, reason,
                        )
                    continue

                params = resolution.params

                # Check if candidate applies to this opportunity
                if not _candidate_applies(candidate, symbol=symbol, pattern=pattern):
                    continue

                # Open candidate shadow with the RESOLVED treatment geometry.
                # Wave 4D.1 treatment provenance: the deterministic
                # treatment_id is embedded in the persisted trade_id, so the
                # candidate-side evidence (shadow_trades dataset) carries the
                # exact treatment identity alongside shadow_type =
                # CANDIDATE_<candidate_id> and the pairing correlation_id.
                trade_id = (
                    f"candidate_{candidate.candidate_id}_{cycle_id}_{symbol}"
                    f"_{resolution.treatment_id}"
                )

                engine.open_trade(
                    trade_id=trade_id,
                    cycle_id=cycle_id,
                    symbol=symbol,
                    direction=params["direction"],
                    entry_price=entry_price,
                    stop_loss=params["stop_loss"],
                    take_profit=params["take_profit"],
                    entry_time=entry_time,
                    strategy=strategy,
                    pattern=pattern,
                    score=score,
                    lot_size=0.01,
                    entry_bar_index=entry_bar_index,
                    correlation_id=correlation_id,
                    entity_id=entity_id,
                    spread_at_entry=abs(ask - bid) if (bid > 0 and ask > 0) else 0.0,
                    bid_at_entry=bid,
                    ask_at_entry=ask,
                    # Candidate shadow lineage
                    shadow_type=f"CANDIDATE_{candidate.candidate_id}",
                    v10_action="CANDIDATE_SHADOW",
                )

                count += 1
                logger.debug(
                    "[CANDIDATE_SHADOW] opened trade_id=%s candidate=%s symbol=%s dir=%s",
                    trade_id, candidate.candidate_id, symbol, params["direction"],
                )

            except Exception as e:
                logger.debug("[CANDIDATE_SHADOW_ERROR] candidate=%s error=%s",
                             candidate.candidate_id, str(e)[:100])
                continue  # Individual candidate failure must never block

    except Exception as e:
        logger.debug("[CANDIDATE_SHADOW_HOOK_ERROR] %s", str(e)[:100])

    return count


def _candidate_applies(candidate: Any, *, symbol: str, pattern: str) -> bool:
    """Determine if this candidate should shadow this specific opportunity."""
    defn = candidate.change_definition
    change_type = defn.get("type", "")

    # Pattern-specific candidates only apply to their pattern
    target_patterns = defn.get("patterns", [])
    if target_patterns and pattern not in target_patterns:
        return False

    # Symbol-specific candidates only apply to their symbol
    target_symbol = defn.get("symbol", "")
    if target_symbol and symbol != target_symbol:
        return False

    # Symbol exclusion applies only to the excluded symbol
    if change_type == "symbol_exclusion":
        return symbol == defn.get("symbol", "")

    return True
