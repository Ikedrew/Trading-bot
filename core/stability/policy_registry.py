"""
Static Policy Registry — Immutable mapping of cohort keys to policy modes.

Deterministic. Static. Human-editable. Source-controlled. Auditable.

No functions. No classes. No imports from analysis/engine/runtime/broker/MT5.
No ML logic. No heuristics. No calculations. No side effects. No mutation.
Only a lookup table.
"""

# ─── ALLOWED POLICY VALUES ────────────────────────────────────────────────────
#
# "RUNNER_MODE"   — High-confidence environment, aggressive trailing allowed
# "NORMAL_MODE"   — Standard operation, no special constraints
# "PROTECT_MODE"  — Defensive posture, reduced aggressiveness
# "BLOCK_MODE"    — Trade entry denied for this cohort
#

# ─── POLICY REGISTRY ─────────────────────────────────────────────────────────

POLICY_REGISTRY: dict[str, str] = {
    # ─── STRONG confirmation ──────────────────────────────────────────
    "STRONG+EARLY+TRENDING": "RUNNER_MODE",
    "STRONG+MID+TRENDING": "NORMAL_MODE",
    "STRONG+LATE+TRENDING": "NORMAL_MODE",
    "STRONG+EARLY+RANGING": "NORMAL_MODE",
    "STRONG+MID+RANGING": "NORMAL_MODE",
    "STRONG+LATE+RANGING": "PROTECT_MODE",

    # ─── MODERATE confirmation ────────────────────────────────────────
    "MODERATE+EARLY+TRENDING": "NORMAL_MODE",
    "MODERATE+MID+TRENDING": "NORMAL_MODE",
    "MODERATE+LATE+TRENDING": "PROTECT_MODE",
    "MODERATE+EARLY+RANGING": "NORMAL_MODE",
    "MODERATE+MID+RANGING": "PROTECT_MODE",
    "MODERATE+LATE+RANGING": "PROTECT_MODE",

    # ─── WEAK confirmation ────────────────────────────────────────────
    "WEAK+EARLY+TRENDING": "PROTECT_MODE",
    "WEAK+MID+TRENDING": "PROTECT_MODE",
    "WEAK+LATE+TRENDING": "PROTECT_MODE",
    "WEAK+EARLY+RANGING": "PROTECT_MODE",
    "WEAK+MID+RANGING": "PROTECT_MODE",
    "WEAK+LATE+RANGING": "BLOCK_MODE",

    # ─── UNKNOWN / fallback ───────────────────────────────────────────
    "UNKNOWN+UNKNOWN+UNKNOWN": "NORMAL_MODE",
    "UNKNOWN+EARLY+TRENDING": "NORMAL_MODE",
    "UNKNOWN+MID+TRENDING": "NORMAL_MODE",
    "UNKNOWN+LATE+TRENDING": "PROTECT_MODE",
    "UNKNOWN+UNKNOWN+RANGING": "PROTECT_MODE",
    "UNKNOWN+UNKNOWN+TRENDING": "NORMAL_MODE",
}
