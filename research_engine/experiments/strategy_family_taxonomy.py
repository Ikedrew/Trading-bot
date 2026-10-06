"""Canonical StrategyFamily taxonomy authority for governed research.

The single authoritative current StrategyFamily universe is
``core.v10.strategy_family.StrategyFamily`` — the V10 engine's decision about
what type of trade an opportunity is.  Research coverage/classification must
derive its expected family set from that enum, never from a manually duplicated
or legacy list.

Legacy research names remain aliases only and resolve deterministically to
their current successors.  Retired names and any unrecognised value fail
closed: they never resurrect a current family, and they never silently count as
a clean strategy label.

This module is read-only and stateless.  It performs no filesystem, network, or
S3 access, and it never consults the legacy ``core.strategy_family`` layer as an
authority.
"""

from __future__ import annotations

from core.v10.strategy_family import StrategyFamily

# Active current families in deterministic enum declaration order, excluding
# ``NONE`` (the explicit "no family" value, never a clean strategy label).
CURRENT_STRATEGY_FAMILIES: tuple[str, ...] = tuple(
    family.value for family in StrategyFamily if family is not StrategyFamily.NONE
)

# Deterministic legacy -> current alias mapping.  ``FALSE_BREAK`` is identical
# in both taxonomies, so it needs no entry and is treated as a current family.
LEGACY_ALIASES: dict[str, str] = {
    "REVERSAL": StrategyFamily.LIQUIDITY_SWEEP_REVERSAL.value,
    "CONTINUATION": StrategyFamily.TREND_CONTINUATION.value,
}

# Legacy pattern-family names with no current V10 equivalent.  ``MEAN_REVERSION``
# is shared by both taxonomies (identical name) and is therefore current, not
# retired.  ``BREAKOUT`` is the legacy pattern-family placeholder (0 patterns,
# never realised); the current ``BREAKOUT_EXPANSION`` is a distinct realised
# entry strategy, not a rename of it.
RETIRED_FAMILIES: frozenset[str] = frozenset({"MOMENTUM", "BREAKOUT"})

NONE_VALUE: str = StrategyFamily.NONE.value

# Classification outcomes returned by ``classify_strategy_family``.
CURRENT = "CURRENT"
LEGACY_ALIAS = "LEGACY_ALIAS"
RETIRED = "RETIRED"
NONE = "NONE"
MISSING = "MISSING"
UNKNOWN = "UNKNOWN"


def normalize_strategy_family(raw: object) -> str | None:
    """Return the canonical current StrategyFamily for ``raw``, or ``None``.

    Current names pass through unchanged; legacy aliases resolve to their
    current successor.  Missing, ``NONE``, retired, and unknown values return
    ``None`` (fail closed).
    """
    if raw is None:
        return None
    value = str(raw).strip()
    if value == "":
        return None
    upper = value.upper()
    if upper in CURRENT_STRATEGY_FAMILIES:
        return upper
    return LEGACY_ALIASES.get(upper)


def classify_strategy_family(raw: object) -> tuple[str, str | None]:
    """Classify a raw strategy value against the current taxonomy.

    Returns ``(status, canonical_family)``.  ``canonical_family`` is the current
    family value for CURRENT and LEGACY_ALIAS statuses, otherwise ``None``.
    """
    if raw is None:
        return MISSING, None
    value = str(raw).strip()
    if value == "":
        return MISSING, None
    upper = value.upper()
    if upper == NONE_VALUE:
        return NONE, None
    if upper in CURRENT_STRATEGY_FAMILIES:
        return CURRENT, upper
    if upper in LEGACY_ALIASES:
        return LEGACY_ALIAS, LEGACY_ALIASES[upper]
    if upper in RETIRED_FAMILIES:
        return RETIRED, None
    return UNKNOWN, None


def observed_current_families(raw_values: object) -> tuple[str, ...]:
    """Return the current families observed across ``raw_values``, each once.

    This is the family-coverage denominator helper: every current family is
    counted at most once regardless of how many records carry it, and legacy
    aliases resolve to their current family so duplicates never inflate the
    observed set.  Deterministic: families appear in canonical enum order.
    """
    seen: set[str] = set()
    for raw in raw_values:
        canonical = normalize_strategy_family(raw)
        if canonical is not None:
            seen.add(canonical)
    return tuple(family for family in CURRENT_STRATEGY_FAMILIES if family in seen)
