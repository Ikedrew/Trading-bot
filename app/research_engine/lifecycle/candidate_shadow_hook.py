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

    Wave 4D.2: candidate_trade_id() is the single MINT site for that
    trade_id format and extract_treatment_id() is the strict, fail-closed
    PARSE — candidate_pairing uses it to propagate the treatment identity
    explicitly into every evaluable pair.

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
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# Length of the deterministic treatment-identity hex digest.
_TREATMENT_ID_LEN = 16
# Strict shape of the treatment-identity digest segment inside trade_id.
_TREATMENT_ID_RE = re.compile(r"^[0-9a-f]{16}$")

# Prefix of every candidate-shadow trade_id minted by open_candidate_shadows.
_CANDIDATE_TRADE_ID_PREFIX = "candidate_"

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
    treatment_spec: str | None = None


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


def candidate_trade_id(candidate_id: str, cycle_id: Any, symbol: str, treatment_id: str) -> str:
    """Mint the candidate-shadow trade_id (Wave 4D.1 format).

    This is the SINGLE authoritative representation of treatment identity in
    persisted candidate-shadow evidence today:
        candidate_<candidate_id>_<cycle_id>_<symbol>_<treatment_id>
    Keep mint and parse (extract_treatment_id) co-located so the format can
    never drift.
    """
    return f"{_CANDIDATE_TRADE_ID_PREFIX}{candidate_id}_{cycle_id}_{symbol}_{treatment_id}"


def extract_treatment_id(trade_id: Any) -> str | None:
    """Wave 4D.2 — strictly recover the treatment_id from a candidate-shadow
    trade_id minted by open_candidate_shadows (see candidate_trade_id()).

    Returns the 16-hex treatment_id, or None when the string is not EXACTLY
    that shape (missing or malformed identity). Callers MUST fail closed on
    None: no loose matching, no heuristic recovery, no silent acceptance.
    """
    s = trade_id if isinstance(trade_id, str) else ""
    if not s.startswith(_CANDIDATE_TRADE_ID_PREFIX):
        return None
    head, sep, treatment_id = s.rpartition("_")
    if not sep or not head:
        return None
    # "candidate_<tid>" alone carries no cycle/symbol segments — malformed.
    if "_" not in head:
        return None
    if not _TREATMENT_ID_RE.fullmatch(treatment_id):
        return None
    return treatment_id


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
        # Wave 5.3C fidelity repair: single canonical pure geometry.
        # Fixed, declared-by-contract treatment: invert direction, keep the
        # original risk distance, take profit at 3R. No declared parameters.
        # Canonical reference_entry is the supplied midpoint (bid+ask)/2
        # from the execution/preparation tick; R = abs(reference-entry -
        # incumbent_stop). Delegates to the shared helper so shadow and
        # future production derive identical geometry. The passed
        # risk_distance is intentionally NOT used here: canonical R is
        # derived inside the helper (identical on the real path where
        # risk_distance == abs(entry_price - stop_loss)).
        from research_engine.lifecycle.direction_inversion_geometry import (  # noqa: PLC0415
            canonical_direction_inversion,
        )
        declared = {}
        _geo = canonical_direction_inversion(
            incumbent_direction=direction,
            reference_entry=entry_price,
            incumbent_stop=stop_loss,
        )
        if _geo is None:
            return None, "malformed_computed_geometry"
        params = {"direction": _geo.inverted_direction,
                  "stop_loss": _geo.stop, "take_profit": _geo.target}

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
    from research_engine.lifecycle.treatment_provenance import canonical_spec
    try:
        scope = canonical_candidate_scope(change_definition)
    except _ScopeError:
        return None, "malformed_scope"
    treatment_spec = canonical_spec({
        "change_type": change_type, "declared": declared,
        "scope": scope, "treatment_id": treatment_id,
    })
    params = {**params, "treatment_id": treatment_id}
    return (
        TreatmentResolution(
            change_type=change_type,
            declared=declared,
            params=params,
            treatment_id=treatment_id,
            treatment_spec=treatment_spec,
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
    treatment_reference_entry: float | None = None,
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

        ref = treatment_reference_entry
        try:
            import math as _mm
            bad = isinstance(ref, bool) or not isinstance(ref, (int, float))
            bad = bad or not _mm.isfinite(float(ref))
            ref = entry_price if bad else float(ref)
        except Exception:
            ref = entry_price
        # Canonical R uses the treatment reference (same-sample midpoint),
        # not a stale entry_price. On the real path ref == entry_price so
        # this is identical; on legacy direct calls with entry_price only,
        # ref falls back to entry_price (bit-identical behaviour).
        risk_distance = abs(ref - stop_loss)
        if risk_distance <= 0:
            return 0

        engine = get_shadow_engine()

        for candidate in candidates:
            try:
                resolution, reason = resolve_candidate_treatment(
                    change_definition=candidate.change_definition,
                    direction=direction,
                    entry_price=ref,
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
                # Wave 4D.2: candidate_trade_id() is the single mint site;
                # extract_treatment_id() is the strict parse back.
                trade_id = candidate_trade_id(
                    candidate.candidate_id, cycle_id, symbol,
                    resolution.treatment_id,
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
                    treatment_spec=resolution.treatment_spec,
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


# ─── Wave 4D.3: experiment scope is distinct from treatment definition ───────
# TREATMENT parameters (change_definition["symbol"], ["stop_multiplier"], etc.)
# describe WHAT the treatment changes. EXPERIMENT SCOPE describes WHICH
# observations are eligible for treatment. The runtime MUST NOT infer scope
# from a treatment parameter merely because it is named "symbol"/"pattern".
#
# Scope authority (smallest truthful representation over the existing contract):
#   change_definition["scope"] = {"symbols": [...], "patterns": [...]}
# is the ONLY authoritative, EXPLICIT scope object. It is optional; when absent
# the documented default population is BROAD (the candidate is eligible for
# every opportunity of its resolvable treatment). A legacy top-level "patterns"
# key is preserved as a scope field for backward compatibility, because that was
# already the documented/tested scope contract. A top-level "symbol" key is NOT
# treated as generic scope: for symbol_exclusion it is a TREATMENT parameter
# (the symbol being excluded); for any other type it is not scope at all.
_SCOPE_KEY = "scope"


class _ScopeError(ValueError):
    """Raised when an explicit candidate scope object is malformed."""


def _string_list(value: Any) -> list[str] | None:
    """Coerce a scope value to a non-empty list[str], or None if unset.

    Raises _ScopeError for a malformed explicit scope value (present but not a
    list of non-empty strings) so the caller can fail closed.
    """
    if value is None:
        return None
    if not isinstance(value, (list, tuple)):
        raise _ScopeError("scope value must be a list of strings")
    items: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise _ScopeError("scope list entries must be non-empty strings")
        items.append(item)
    return items or None


def canonical_candidate_scope(defn: dict) -> dict:
    """Freeze Wave 4D.3 membership, including empty-pattern legacy fallback."""
    scope = defn.get(_SCOPE_KEY, {})
    if scope and not isinstance(scope, dict):
        raise _ScopeError("malformed scope")
    scope = scope if isinstance(scope, dict) else {}
    symbols = _string_list(scope.get("symbols"))
    patterns = _string_list(scope.get("patterns"))
    if patterns is None:
        patterns = _string_list(defn.get("patterns"))
    return {"symbols": sorted(set(symbols)) if symbols else None,
            "patterns": sorted(set(patterns)) if patterns else None}


def candidate_in_scope(candidate: Any, *, symbol: str, pattern: str) -> tuple[bool, str]:
    """Wave 4D.3 — decide eligibility using EXPLICIT scope only (fail closed on
    malformed explicit scope). Returns (in_scope, reason).

    reason is one of:
        in_scope | out_of_scope_symbol | out_of_scope_pattern |
        malformed_scope | symbol_exclusion_wrong_symbol
    """
    defn = candidate.change_definition if isinstance(candidate.change_definition, dict) else {}
    change_type = defn.get("type", "")

    # Explicit scope object is the ONLY authoritative scope source.
    scope = defn.get(_SCOPE_KEY, {})
    if scope and not isinstance(scope, dict):
        return False, "malformed_scope"
    scope = scope if isinstance(scope, dict) else {}

    try:
        scope_symbols = _string_list(scope.get("symbols"))
        # Legacy documented scope field: top-level "patterns" (pre-4D.3
        # contract). Explicit scope.patterns takes precedence when present.
        scope_patterns = _string_list(scope.get("patterns"))
        if scope_patterns is None:
            scope_patterns = _string_list(defn.get("patterns"))
    except _ScopeError:
        return False, "malformed_scope"

    if scope_patterns is not None and pattern not in scope_patterns:
        return False, "out_of_scope_pattern"
    if scope_symbols is not None and symbol not in scope_symbols:
        return False, "out_of_scope_symbol"

    # symbol_exclusion: "symbol" is a TREATMENT parameter (the excluded symbol),
    # NOT experiment scope. It is only ever eligible on that symbol, but it opens
    # no shadow by design (resolve_candidate_treatment returns
    # symbol_exclusion_no_shadow). We keep the applicability truthful without
    # converting the treatment parameter into a generic symbol scope.
    if change_type == "symbol_exclusion":
        excluded = defn.get("symbol", "")
        if symbol != excluded:
            return False, "symbol_exclusion_wrong_symbol"

    return True, "in_scope"


def _candidate_applies(candidate: Any, *, symbol: str, pattern: str) -> bool:
    """Determine if this candidate should shadow this specific opportunity.

    Wave 4D.3: eligibility is decided by EXPLICIT experiment scope
    (candidate_in_scope). Treatment parameters (e.g. a non-exclusion "symbol"
    field) never silently narrow the experiment. Malformed explicit scope fails
    closed (candidate does not apply).
    """
    in_scope, _reason = candidate_in_scope(candidate, symbol=symbol, pattern=pattern)
    return in_scope
