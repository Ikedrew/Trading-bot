"""Wave 5.3C — Canonical pure direction_inversion geometry.

ONE canonical implementation shared by:

A. candidate shadow treatment (research_engine.lifecycle.candidate_shadow_hook)
B. future production treatment (Wave 5.3 production consumer — not yet built)

Canonical reference_entry semantics: (bid + ask) / 2 from the
execution/preparation tick used by the existing candidate shadow path.

Preserves EXACT Wave 4D behaviour — do not "improve" the geometry:

    R = abs(reference_entry - incumbent_stop)

    incumbent SELL -> inverted BUY:
        stop   = reference_entry - R
        target = reference_entry + 3R

    incumbent BUY -> inverted SELL:
        stop   = reference_entry + R
        target = reference_entry - 3R

Pure: no I/O, no wall-clock, no broker access, no mutable global state.
Fail closed (returns None) on invalid input.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DirectionInversionGeometry:
    """Structured result so shadow + future production use without recomputation."""

    incumbent_direction: str
    inverted_direction: str
    reference_entry: float
    risk_distance: float
    stop: float
    target: float


def _is_finite_number(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def canonical_treatment_reference_entry(bid: Any, ask: Any) -> float | None:
    """Canonical treatment reference: (bid + ask) / 2 from ONE tick sample.

    Fail closed (None) on non-numeric / non-finite inputs. Pure, no I/O.
    """
    if not _is_finite_number(bid) or not _is_finite_number(ask):
        return None
    ref = (float(bid) + float(ask)) / 2.0
    if not math.isfinite(ref):
        return None
    return ref


def canonical_direction_inversion(
    *,
    incumbent_direction: Any,
    reference_entry: Any,
    incumbent_stop: Any,
) -> DirectionInversionGeometry | None:
    """Canonical pure direction_inversion geometry (Wave 4D semantics, fail closed).

    Returns DirectionInversionGeometry on valid input, else None.
    """
    if incumbent_direction not in ("BUY", "SELL"):
        return None
    if not _is_finite_number(reference_entry) or not _is_finite_number(incumbent_stop):
        return None
    ref = float(reference_entry)
    stop_in = float(incumbent_stop)
    risk = abs(ref - stop_in)
    if not math.isfinite(risk) or risk <= 0:
        return None
    if incumbent_direction == "SELL":
        inverted = "BUY"
        stop = ref - risk
        target = ref + risk * 3.0
    else:  # incumbent BUY
        inverted = "SELL"
        stop = ref + risk
        target = ref - risk * 3.0
    if not math.isfinite(stop) or not math.isfinite(target):
        return None
    return DirectionInversionGeometry(
        incumbent_direction=incumbent_direction,
        inverted_direction=inverted,
        reference_entry=ref,
        risk_distance=risk,
        stop=stop,
        target=target,
    )
