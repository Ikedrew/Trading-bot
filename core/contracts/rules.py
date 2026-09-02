"""
Contract Rules — Immutable rule definitions for all validators.

Every individual validation check has a permanent, globally unique rule ID.
This file is the SINGLE source of truth for all rule metadata.

NAMING CONVENTION:
    {DOMAIN}_{ASPECT}_{SEQ}
    SCHEMA_SECTION_001    — Schema section checks
    SCHEMA_FIELD_001      — Schema field checks
    SCHEMA_TYPE_001       — Schema type checks
    SCHEMA_VERSION_001    — Schema version checks
    FEATURE_DECISION_001  — Feature role checks
    FEATURE_OUTCOME_001
    FEATURE_DERIVED_001
    IMMUTABLE_SNAPSHOT_001 — Immutability checks
    PERSIST_IDENTITY_001  — Persistence checks
    PERSIST_SYMBOL_001
    PERSIST_TIME_001
    PERSIST_PRICE_001
    PERSIST_R_001
    CAUSAL_CONTEXT_001    — Causal checks
    CAUSAL_HTF_001
    CAUSAL_LIFECYCLE_001
    CAUSAL_SIGN_001
"""

from __future__ import annotations

from core.contracts.contract_rule import ContractRule, get_rule_registry
from core.contracts.severity import Severity


# ═══════════════════════════════════════════════════════════════════════════════
# SCHEMA VALIDATOR RULES
# ═══════════════════════════════════════════════════════════════════════════════

SCHEMA_SECTION_001 = ContractRule(
    rule_id="SCHEMA_SECTION_001",
    title="Missing Required Section",
    description="A required top-level section (timestamps, prices, outcome) is absent from the record.",
    validator_id="SCHEMA_001",
    severity=Severity.ERROR,
    confidence=100,
    documentation="trade_truth_schema",
    recommendation="Ensure all required sections are populated before persistence.",
    introduced_in="Arc1",
)

SCHEMA_SECTION_002 = ContractRule(
    rule_id="SCHEMA_SECTION_002",
    title="Section Wrong Type",
    description="A required section exists but is not a dict (wrong container type).",
    validator_id="SCHEMA_001",
    severity=Severity.ERROR,
    confidence=100,
    documentation="trade_truth_schema",
    recommendation="Section must be a dict. Check serialization pipeline.",
    introduced_in="Arc1",
)

SCHEMA_FIELD_001 = ContractRule(
    rule_id="SCHEMA_FIELD_001",
    title="Missing Required Field",
    description="A required field within a section is absent.",
    validator_id="SCHEMA_001",
    severity=Severity.ERROR,
    confidence=100,
    documentation="trade_truth_schema",
    recommendation="Populate all required fields before persistence.",
    introduced_in="Arc1",
)

SCHEMA_TYPE_001 = ContractRule(
    rule_id="SCHEMA_TYPE_001",
    title="Field Type Mismatch",
    description="A critical numeric field has a non-numeric type.",
    validator_id="SCHEMA_001",
    severity=Severity.WARNING,
    confidence=100,
    documentation="trade_truth_schema",
    recommendation="Ensure numeric fields contain int or float values.",
    introduced_in="Arc1",
)

SCHEMA_VERSION_001 = ContractRule(
    rule_id="SCHEMA_VERSION_001",
    title="Missing Schema Version",
    description="Record does not contain schema_version field (may be legacy format).",
    validator_id="SCHEMA_001",
    severity=Severity.INFO,
    confidence=100,
    documentation="trade_truth_schema",
    recommendation="Populate schema_version before persistence.",
    introduced_in="Arc1",
)

# ═══════════════════════════════════════════════════════════════════════════════
# FEATURE ROLE VALIDATOR RULES
# ═══════════════════════════════════════════════════════════════════════════════

FEATURE_DECISION_001 = ContractRule(
    rule_id="FEATURE_DECISION_001",
    title="Empty Decision Field",
    description="A DECISION-role field is present but empty string (should be non-empty or absent).",
    validator_id="FEATURE_001",
    severity=Severity.WARNING,
    confidence=65,
    documentation="feature_role_contract_v1",
    recommendation="Populate decision fields at signal time or omit entirely.",
    introduced_in="Arc1",
)

FEATURE_OUTCOME_001 = ContractRule(
    rule_id="FEATURE_OUTCOME_001",
    title="Outcome Field Type Error",
    description="An OUTCOME-role field has incorrect type (expected numeric or string for exit_reason).",
    validator_id="FEATURE_001",
    severity=Severity.WARNING,
    confidence=70,
    documentation="feature_role_contract_v1",
    recommendation="Ensure outcome fields match their declared types.",
    introduced_in="Arc1",
)

FEATURE_DERIVED_001 = ContractRule(
    rule_id="FEATURE_DERIVED_001",
    title="Derived Field Inconsistency",
    description="A DERIVED field (exit_efficiency) is inconsistent with its source fields (r_multiple / mfe_r).",
    validator_id="FEATURE_001",
    severity=Severity.ERROR,
    confidence=95,
    documentation="feature_role_contract_v1",
    recommendation="Recompute derived fields from their source fields at the same write point.",
    introduced_in="Arc1",
)

# ═══════════════════════════════════════════════════════════════════════════════
# IMMUTABILITY VALIDATOR RULES
# ═══════════════════════════════════════════════════════════════════════════════

IMMUTABLE_SNAPSHOT_001 = ContractRule(
    rule_id="IMMUTABLE_SNAPSHOT_001",
    title="Mutable HTF Snapshot",
    description="htf_snapshot is a mutable dict in a layer that requires frozen snapshots.",
    validator_id="IMMUTABILITY_001",
    severity=Severity.ERROR,
    confidence=100,
    documentation="snapshot_immutability_v1",
    recommendation="Apply _refreeze_node() or _deep_freeze() after deserialization.",
    introduced_in="Arc1",
)

IMMUTABLE_SNAPSHOT_002 = ContractRule(
    rule_id="IMMUTABLE_SNAPSHOT_002",
    title="Mutable Strategy Meta",
    description="strategy_meta is a mutable dict in a layer that requires frozen snapshots.",
    validator_id="IMMUTABILITY_001",
    severity=Severity.WARNING,
    confidence=100,
    documentation="snapshot_immutability_v1",
    recommendation="Freeze strategy_meta on deserialization alongside htf_snapshot.",
    introduced_in="Arc1",
)

IMMUTABLE_SNAPSHOT_003 = ContractRule(
    rule_id="IMMUTABLE_SNAPSHOT_003",
    title="Mutable Nested Container",
    description="A mutable list or dict found inside a frozen snapshot field.",
    validator_id="IMMUTABILITY_001",
    severity=Severity.WARNING,
    confidence=100,
    documentation="snapshot_immutability_v1",
    recommendation="Ensure recursive freeze converts all nested lists to tuples and dicts to MappingProxyType.",
    introduced_in="Arc1",
)

# ═══════════════════════════════════════════════════════════════════════════════
# PERSISTENCE VALIDATOR RULES
# ═══════════════════════════════════════════════════════════════════════════════

PERSIST_IDENTITY_001 = ContractRule(
    rule_id="PERSIST_IDENTITY_001",
    title="Missing Record Identity",
    description="Record has no trade_id or record_id field.",
    validator_id="PERSISTENCE_001",
    severity=Severity.WARNING,
    confidence=95,
    documentation="persistence_integrity_v1",
    recommendation="Assign a unique trade_id before persistence.",
    introduced_in="Arc1",
)

PERSIST_SYMBOL_001 = ContractRule(
    rule_id="PERSIST_SYMBOL_001",
    title="Invalid Symbol",
    description="Record has no valid symbol field (empty or missing).",
    validator_id="PERSISTENCE_001",
    severity=Severity.ERROR,
    confidence=100,
    documentation="persistence_integrity_v1",
    recommendation="Populate symbol from the trading pair before persistence.",
    introduced_in="Arc1",
)

PERSIST_TIME_001 = ContractRule(
    rule_id="PERSIST_TIME_001",
    title="Non-Positive Entry Time",
    description="entry_time is zero or negative (invalid Unix timestamp).",
    validator_id="PERSISTENCE_001",
    severity=Severity.ERROR,
    confidence=95,
    documentation="persistence_integrity_v1",
    recommendation="Use closed-bar timestamp for entry_time.",
    introduced_in="Arc1",
)

PERSIST_TIME_002 = ContractRule(
    rule_id="PERSIST_TIME_002",
    title="Non-Positive Exit Time",
    description="exit_time is zero or negative (invalid Unix timestamp).",
    validator_id="PERSISTENCE_001",
    severity=Severity.ERROR,
    confidence=95,
    documentation="persistence_integrity_v1",
    recommendation="Use closed-bar timestamp for exit_time.",
    introduced_in="Arc1",
)

PERSIST_TIME_003 = ContractRule(
    rule_id="PERSIST_TIME_003",
    title="Time Travel Detected",
    description="exit_time occurs before entry_time (temporal impossibility).",
    validator_id="PERSISTENCE_001",
    severity=Severity.ERROR,
    confidence=90,
    documentation="persistence_integrity_v1",
    recommendation="Verify timestamp ordering. Check for clock sync issues.",
    introduced_in="Arc1",
)

PERSIST_PRICE_001 = ContractRule(
    rule_id="PERSIST_PRICE_001",
    title="Non-Positive Entry Price",
    description="entry_price is zero or negative (impossible for FX).",
    validator_id="PERSISTENCE_001",
    severity=Severity.ERROR,
    confidence=100,
    documentation="persistence_integrity_v1",
    recommendation="Verify broker fill price before persistence.",
    introduced_in="Arc1",
)

PERSIST_PRICE_002 = ContractRule(
    rule_id="PERSIST_PRICE_002",
    title="Zero Risk Distance",
    description="entry_price equals stop_loss (division by zero in R computation).",
    validator_id="PERSISTENCE_001",
    severity=Severity.CRITICAL,
    confidence=100,
    documentation="persistence_integrity_v1",
    recommendation="Ensure SL is always different from entry. Reject zero-risk trades.",
    introduced_in="Arc1",
)

PERSIST_R_001 = ContractRule(
    rule_id="PERSIST_R_001",
    title="R-Multiple Exceeds Sanity Bounds",
    description="R-multiple is outside [-50, 50] range — possible inflation bug or data corruption.",
    validator_id="PERSISTENCE_001",
    severity=Severity.CRITICAL,
    confidence=99,
    documentation="persistence_integrity_v1",
    recommendation="Check for legacy R inflation bug (mixing cash PnL with price distance).",
    introduced_in="Arc1",
)

# ═══════════════════════════════════════════════════════════════════════════════
# CAUSAL VALIDATOR RULES
# ═══════════════════════════════════════════════════════════════════════════════

CAUSAL_CONTEXT_001 = ContractRule(
    rule_id="CAUSAL_CONTEXT_001",
    title="Outcome Without Decision Context",
    description="Record has r_multiple but no decision context (pattern/strategy missing).",
    validator_id="CAUSAL_001",
    severity=Severity.WARNING,
    confidence=50,
    documentation="causal_chain_integrity_v1",
    recommendation="Ensure strategy_meta is populated before trade close.",
    introduced_in="Arc1",
)

CAUSAL_HTF_001 = ContractRule(
    rule_id="CAUSAL_HTF_001",
    title="Missing HTF Source for Regime",
    description="Record has edges.regime but no htf_snapshot (causal source absent).",
    validator_id="CAUSAL_001",
    severity=Severity.WARNING,
    confidence=45,
    documentation="causal_chain_integrity_v1",
    recommendation="Populate htf_snapshot at signal time before graph build.",
    introduced_in="Arc1",
)

CAUSAL_LIFECYCLE_001 = ContractRule(
    rule_id="CAUSAL_LIFECYCLE_001",
    title="MFE Without Bar Progression",
    description="mfe_r is positive but bars_held is zero (impossible without lifecycle).",
    validator_id="CAUSAL_001",
    severity=Severity.WARNING,
    confidence=60,
    documentation="causal_chain_integrity_v1",
    recommendation="Verify lifecycle tracking updates bars_held correctly.",
    introduced_in="Arc1",
)

CAUSAL_SIGN_001 = ContractRule(
    rule_id="CAUSAL_SIGN_001",
    title="Stop Loss Exit With Positive R",
    description="exit_reason is 'stop_loss' but r_multiple is positive — causally impossible.",
    validator_id="CAUSAL_001",
    severity=Severity.ERROR,
    confidence=85,
    documentation="causal_chain_integrity_v1",
    recommendation="Verify exit classification logic. SL fills should always produce negative R.",
    introduced_in="Arc1",
)

CAUSAL_SIGN_002 = ContractRule(
    rule_id="CAUSAL_SIGN_002",
    title="Take Profit Exit With Negative R",
    description="exit_reason is 'take_profit' but r_multiple is negative — causally impossible.",
    validator_id="CAUSAL_001",
    severity=Severity.ERROR,
    confidence=85,
    documentation="causal_chain_integrity_v1",
    recommendation="Verify exit classification logic. TP fills should always produce positive R.",
    introduced_in="Arc1",
)


# ═══════════════════════════════════════════════════════════════════════════════
# CORRELATION VALIDATOR RULES
# ═══════════════════════════════════════════════════════════════════════════════

CORRELATION_MISSING_001 = ContractRule(
    rule_id="CORRELATION_MISSING_001",
    title="Missing Correlation ID",
    description="Record has no correlation_id field — cannot link to decision cycle spine.",
    validator_id="CORRELATION_001",
    severity=Severity.ERROR,
    confidence=100,
    documentation="correlation_contract_v1",
    recommendation="Ensure correlation_id is generated at decision entry point and propagated to all downstream layers.",
    introduced_in="Arc1",
)

CORRELATION_FORMAT_001 = ContractRule(
    rule_id="CORRELATION_FORMAT_001",
    title="Invalid Correlation ID Format",
    description="correlation_id is present but does not match required COR-{date}-{cycle}-{symbol}-{hash} format.",
    validator_id="CORRELATION_001",
    severity=Severity.WARNING,
    confidence=90,
    documentation="correlation_contract_v1",
    recommendation="Verify generate_correlation_id() is the sole source. Never construct IDs manually.",
    introduced_in="Arc1",
)

CORRELATION_SPINE_001 = ContractRule(
    rule_id="CORRELATION_SPINE_001",
    title="Correlation ID Symbol Mismatch",
    description="The symbol embedded in correlation_id does not match the record's symbol field — possible spine corruption or cross-layer reassignment.",
    validator_id="CORRELATION_001",
    severity=Severity.CRITICAL,
    confidence=95,
    documentation="correlation_contract_v1",
    recommendation="correlation_id must never be regenerated or reassigned downstream. Investigate source of mismatch.",
    introduced_in="Arc1",
)


# ═══════════════════════════════════════════════════════════════════════════════
# REGISTRATION HELPER
# ═══════════════════════════════════════════════════════════════════════════════

ALL_RULES: list[ContractRule] = [
    # Schema
    SCHEMA_SECTION_001, SCHEMA_SECTION_002, SCHEMA_FIELD_001,
    SCHEMA_TYPE_001, SCHEMA_VERSION_001,
    # Feature
    FEATURE_DECISION_001, FEATURE_OUTCOME_001, FEATURE_DERIVED_001,
    # Immutability
    IMMUTABLE_SNAPSHOT_001, IMMUTABLE_SNAPSHOT_002, IMMUTABLE_SNAPSHOT_003,
    # Persistence
    PERSIST_IDENTITY_001, PERSIST_SYMBOL_001, PERSIST_TIME_001,
    PERSIST_TIME_002, PERSIST_TIME_003, PERSIST_PRICE_001,
    PERSIST_PRICE_002, PERSIST_R_001,
    # Causal
    CAUSAL_CONTEXT_001, CAUSAL_HTF_001, CAUSAL_LIFECYCLE_001,
    CAUSAL_SIGN_001, CAUSAL_SIGN_002,
    # Correlation
    CORRELATION_MISSING_001, CORRELATION_FORMAT_001, CORRELATION_SPINE_001,
]


def register_all_rules() -> None:
    """Register all contract rules into the global registry."""
    registry = get_rule_registry()
    registry.register_many(ALL_RULES)
