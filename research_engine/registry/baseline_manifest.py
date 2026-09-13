"""V1 Baseline Manifest — Initial canonical 70-question programme.

Records which questions belonged to the original V1 baseline programme.
This answers 'which questions belonged to the initial canonical programme?'
while len(REGISTRY) may grow in future waves.

FROZEN: this tuple must not be derived dynamically from REGISTRY at import
time for membership semantics — it records the original programme IDs.
"""

# V1 baseline version — must start at 1
BASELINE_VERSION: int = 1

# The original canonical 70-question programme IDs (frozen V1 record).
BASELINE_QUESTION_IDS: tuple[str, ...] = (
    "E1", "E2", "E3", "E4", "E5",
    "M1", "M2", "M3", "M4", "M5", "M6", "M7", "M8", "M9", "M10", "M11",
    "D1", "D2", "D3", "D4", "D5", "D6",
    "S1", "S2", "S3", "S4", "S5", "S6", "S7",
    "X1", "X2", "X3", "X4", "X6",
    "R1", "R2", "R3", "R4", "R5", "RISK-1",
    "L1", "L2", "L3", "L4", "L5", "L6", "L7",
    "G1", "G2", "G3",
    "P1",
    "EX1", "EX2", "EX3", "EX4", "EX5", "EX6", "EX7", "EX8", "EX9", "EX10",
    "MGMT-1", "MGMT-2", "HORIZON-1", "STRAT-1",
    "X5", "EXEC1", "PROT1", "PORT-1", "OPP-1",
)

# Convenience: the canonical V1 baseline as a frozen set for membership checks
BASELINE_QUESTION_SET: frozenset[str] = frozenset(BASELINE_QUESTION_IDS)


def is_baseline_question(question_id: str) -> bool:
    """Check if a question ID is part of the V1 baseline programme."""
    return question_id in BASELINE_QUESTION_SET


def validate_registry_against_baseline(registry_ids) -> dict:
    """Compare a live registry ID collection against the frozen V1 baseline.

    Returns a dict with missing/extra IDs. Empty missing means the original
    programme remains intact.
    """
    live = set(registry_ids)
    missing = sorted(set(BASELINE_QUESTION_IDS) - live)
    extra = sorted(live - set(BASELINE_QUESTION_IDS))
    return {"missing": missing, "extra": extra, "baseline_count": len(BASELINE_QUESTION_IDS)}


__all__ = [
    "BASELINE_VERSION",
    "BASELINE_QUESTION_IDS",
    "BASELINE_QUESTION_SET",
    "is_baseline_question",
    "validate_registry_against_baseline",
]