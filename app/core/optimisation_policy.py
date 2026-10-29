"""Wave 5.3 durable production optimisation-policy authority.

Production EFFECTIVE STATE, separate from CandidateRecord / Recommendation /
HumanDecision / ApplicationRecord / env vars / Python source configuration.

Supports EXACTLY NORMAL and DIRECTION_INVERSION. Canonical, deterministic,
restart-safe, atomically persisted, independently readable. No timestamp
affects effective-state identity. Import/construct never mutates state.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any

NORMAL_KIND = "normal"
DIRECTION_INVERSION_KIND = "direction_inversion"
_POLICY_SCHEMA_VERSION = 1
_DEFAULT_PATH = Path("data/production/optimisation_policy.json")


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def normal_state() -> dict[str, Any]:
    return {"kind": NORMAL_KIND}


def direction_inversion_state(
    *,
    treatment_id: str,
    treatment_spec: str,
    application_id: str = "",
    candidate_id: str = "",
) -> dict[str, Any]:
    from research_engine.lifecycle.treatment_provenance import validate_treatment_spec

    if not isinstance(treatment_id, str) or not treatment_id.strip():
        raise ValueError("Missing treatment_id")
    if not isinstance(treatment_spec, str) or not treatment_spec:
        raise ValueError("Missing frozen treatment_spec")
    validate_treatment_spec(treatment_spec, treatment_id)
    value = json.loads(treatment_spec)
    if value.get("change_type") != "direction_inversion":
        raise ValueError("Unsupported treatment type for production policy")
    state: dict[str, Any] = {
        "kind": DIRECTION_INVERSION_KIND,
        "treatment_id": treatment_id,
        "treatment_spec": treatment_spec,
    }
    if application_id:
        if not isinstance(application_id, str):
            raise ValueError("Invalid application_id")
        state["application_id"] = application_id
    if candidate_id:
        if not isinstance(candidate_id, str):
            raise ValueError("Invalid candidate_id")
        state["candidate_id"] = candidate_id
    if json.loads(canonical(state)) != state:
        raise ValueError("Non-deterministic policy envelope")
    return state


def validate_effective_state(state: Any) -> dict[str, Any]:
    from research_engine.lifecycle.treatment_provenance import validate_treatment_spec

    if not isinstance(state, dict):
        raise ValueError("Malformed optimisation policy: not an object")
    kind = state.get("kind")
    if kind == NORMAL_KIND:
        if set(state) != {"kind"}:
            raise ValueError("Malformed NORMAL policy")
        return {"kind": NORMAL_KIND}
    if kind == DIRECTION_INVERSION_KIND:
        allowed = {"kind", "treatment_id", "treatment_spec", "application_id", "candidate_id"}
        if not set(state) <= allowed:
            raise ValueError("Malformed direction_inversion policy")
        tid = state.get("treatment_id")
        spec = state.get("treatment_spec")
        if not isinstance(tid, str) or not tid.strip():
            raise ValueError("Missing treatment_id")
        if not isinstance(spec, str) or not spec:
            raise ValueError("Missing frozen treatment_spec")
        validate_treatment_spec(spec, tid)
        value = json.loads(spec)
        if value.get("change_type") != "direction_inversion":
            raise ValueError("Unsupported treatment type for production policy")
        cleaned: dict[str, Any] = {
            "kind": DIRECTION_INVERSION_KIND,
            "treatment_id": tid,
            "treatment_spec": spec,
        }
        for key in ("application_id", "candidate_id"):
            if key in state:
                extra = state[key]
                if not isinstance(extra, str) or not extra:
                    raise ValueError("Invalid provenance")
                cleaned[key] = extra
        return cleaned
    raise ValueError("Unsupported optimisation policy kind")


def intended_state_from_approval(*, application: dict[str, Any]) -> dict[str, Any]:
    extra: dict[str, Any] = {"kind": DIRECTION_INVERSION_KIND}
    extra["treatment_id"] = application.get("treatment_id")
    extra["treatment_spec"] = application.get("treatment_spec")
    if isinstance(application.get("application_id"), str) and application.get("application_id"):
        extra["application_id"] = application["application_id"]
    if isinstance(application.get("candidate_id"), str) and application.get("candidate_id"):
        extra["candidate_id"] = application["candidate_id"]
    return validate_effective_state(extra)


def is_normal(state: Any) -> bool:
    return isinstance(state, dict) and state.get("kind") == NORMAL_KIND


def effective_identity(state: dict[str, Any]) -> str:
    return digest(validate_effective_state(state))


def policy_scope(state: dict[str, Any]) -> dict[str, Any]:
    cleaned = validate_effective_state(state)
    if cleaned.get("kind") != DIRECTION_INVERSION_KIND:
        raise ValueError("NORMAL policy carries no scope")
    return json.loads(cleaned["treatment_spec"])["scope"]


def _scope_list(value: Any) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list) or not value:
        raise ValueError("malformed scope list")
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError("malformed scope entry")
    if value != sorted(set(value)):
        raise ValueError("non-canonical scope list")
    return list(value)


def in_scope(*, state: dict[str, Any], symbol: str, pattern: str) -> tuple[bool, str]:
    cleaned = validate_effective_state(state)
    if cleaned.get("kind") != DIRECTION_INVERSION_KIND:
        return False, "policy_normal"
    scope = json.loads(cleaned["treatment_spec"])["scope"]
    if not isinstance(scope, dict) or set(scope) != {"symbols", "patterns"}:
        return False, "malformed_scope"
    try:
        symbols = _scope_list(scope.get("symbols"))
        patterns = _scope_list(scope.get("patterns"))
    except ValueError:
        return False, "malformed_scope"
    if not isinstance(symbol, str) or not isinstance(pattern, str):
        return False, "malformed_scope"
    if patterns is not None and pattern not in patterns:
        return False, "out_of_scope_pattern"
    if symbols is not None and symbol not in symbols:
        return False, "out_of_scope_symbol"
    return True, "in_scope"


def read_policy_file(path: str | Path | None = None) -> dict[str, Any]:
    target = Path(path) if path is not None else _DEFAULT_PATH
    if not target.exists():
        return normal_state()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"Malformed optimisation policy file: {exc}") from exc
    if isinstance(raw, dict) and "state" in raw and set(raw) <= {"schema_version", "state"}:
        if raw.get("schema_version") != _POLICY_SCHEMA_VERSION:
            raise ValueError("Unsupported optimisation policy schema")
        return validate_effective_state(raw["state"])
    return validate_effective_state(raw)


def write_policy_file(state: dict[str, Any], path: str | Path | None = None) -> dict[str, Any]:
    cleaned = validate_effective_state(state)
    target = Path(path) if path is not None else _DEFAULT_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical({"schema_version": _POLICY_SCHEMA_VERSION, "state": cleaned})
    tmp = target.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, target)
    return read_policy_file(target)


def apply_direction_inversion(
    *,
    policy_state: dict[str, Any],
    symbol: str,
    pattern: str,
    incumbent_direction: str,
    incumbent_stop: Any,
    treatment_reference_entry: Any,
) -> dict[str, Any] | None:
    cleaned = validate_effective_state(policy_state)
    if cleaned.get("kind") == NORMAL_KIND:
        return None
    ok, _reason = in_scope(state=cleaned, symbol=symbol, pattern=pattern)
    if not ok:
        return None
    if incumbent_direction not in ("BUY", "SELL"):
        raise ValueError("Invalid incumbent direction")
    if isinstance(treatment_reference_entry, bool) or not isinstance(
        treatment_reference_entry, (int, float)
    ):
        raise ValueError("Missing treatment_reference_entry")
    if not math.isfinite(float(treatment_reference_entry)):
        raise ValueError("Missing treatment_reference_entry")
    if isinstance(incumbent_stop, bool) or not isinstance(incumbent_stop, (int, float)):
        raise ValueError("Invalid incumbent stop")
    if not math.isfinite(float(incumbent_stop)):
        raise ValueError("Invalid incumbent stop")
    from research_engine.lifecycle.direction_inversion_geometry import (
        canonical_direction_inversion,
    )

    geo = canonical_direction_inversion(
        incumbent_direction=incumbent_direction,
        reference_entry=float(treatment_reference_entry),
        incumbent_stop=float(incumbent_stop),
    )
    if geo is None:
        raise ValueError("Cannot compute canonical direction_inversion geometry")
    return {
        "direction": geo.inverted_direction,
        "stop_loss": geo.stop,
        "take_profit": geo.target,
        "treatment_id": cleaned["treatment_id"],
        "scope_decision": "in_scope",
    }


def apply_production_intent(
    *,
    intent: Any,
    symbol: str,
    pattern: str = "",
    treatment_reference_entry: Any = None,
    policy_state: dict[str, Any] | None = None,
    policy_path: str | Path | None = None,
) -> tuple[Any, bool]:
    """THE canonical runtime consumption point (Wave 5.3 Step 4/7).

    Boundary owns I/O exactly once: when policy_state is None it is loaded
    via read_policy_file(policy_path). Pure geometry itself never reads
    files (see apply_direction_inversion). No hidden mutable globals; no
    broker/MT5 calls; no second tick sampled — treatment_reference_entry
    must be the same-sample value carried by the caller (5.3C carrier).

    Returns (intent_out, applied). NORMAL or out-of-scope returns the
    ORIGINAL incumbent object unchanged with applied=False. In-scope
    returns a NEW intent with inverted direction + canonical stop/target,
    preserving all unrelated incumbent fields/provenance, with applied=True.
    In-scope but uncomputable (missing/non-finite reference, bad stop)
    raises fail-closed — callers must NOT fall back to structural entry.
    """
    state = (
        validate_effective_state(json.loads(canonical(policy_state)))
        if policy_state is not None
        else read_policy_file(policy_path)
    )
    if is_normal(state):
        return intent, False
    incumbent_pattern = pattern or getattr(intent, "pattern", "") or ""
    side = getattr(intent, "side", None)
    if hasattr(side, "name"):
        direction = side.name
    else:
        direction = side
    stop = getattr(intent, "sl", getattr(intent, "stop_loss", None))
    ok, _reason = in_scope(state=state, symbol=symbol, pattern=incumbent_pattern)
    if not ok:
        return intent, False
    result = apply_direction_inversion(
        policy_state=state,
        symbol=symbol,
        pattern=incumbent_pattern,
        incumbent_direction=direction,
        incumbent_stop=stop,
        treatment_reference_entry=treatment_reference_entry,
    )
    if result is None:
        return intent, False
    try:
        from strategy.signals import Side as _Side

        new_side = _Side[result["direction"]]
    except Exception as exc:
        raise ValueError("Cannot map inverted direction") from exc
    try:
        import dataclasses as _dc

        if _dc.is_dataclass(intent):
            return (
                _dc.replace(intent, side=new_side, sl=result["stop_loss"], tp=result["take_profit"]),
                True,
            )
    except Exception as exc:
        raise ValueError("Cannot preserve incumbent intent fields") from exc
    # Duck-typed mutable intent (tests): copy + set, never mutate original.
    try:
        import copy as _copy

        clone = _copy.copy(intent)
        if hasattr(clone, "side"):
            try:
                clone.side = new_side
            except Exception as exc:
                raise ValueError("Cannot preserve incumbent intent fields") from exc
        if hasattr(clone, "sl"):
            try:
                clone.sl = result["stop_loss"]
            except Exception as exc:
                raise ValueError("Cannot preserve incumbent intent fields") from exc
        if hasattr(clone, "tp"):
            try:
                clone.tp = result["take_profit"]
            except Exception as exc:
                raise ValueError("Cannot preserve incumbent intent fields") from exc
        return clone, True
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("Cannot preserve incumbent intent fields") from exc
