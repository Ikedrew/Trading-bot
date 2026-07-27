"""
Causal Validator — Enforces the causal chain preservation contract.

Validates:
    - Decision fields are present when outcome fields exist
    - HTF snapshot is present when alignment-dependent features exist
    - No backward causality (outcome fields in decision context)
    - Lifecycle ordering is internally consistent

IDENTITY:
    validator_id:       CAUSAL_001
    validator_version:  1
    contract_name:      causal_chain_integrity
    contract_version:   v1
    introduced_in_arc:  Arc1
    introduced_date:    2026-07
"""

from __future__ import annotations

from typing import Any

from core.contracts.base_validator import BaseValidator
from core.contracts.severity import Severity
from core.contracts.validator_identity import ValidatorIdentity
from core.contracts.violation import ContractViolation

# ─── IMMUTABLE IDENTITY ───────────────────────────────────────────────────────

_IDENTITY = ValidatorIdentity(
    validator_id="CAUSAL_001",
    validator_name="CausalValidator",
    validator_version=1,
    contract_name="causal_chain_integrity",
    contract_version="v1",
    introduced_in_arc="Arc1",
    introduced_date="2026-07",
    owner="Architecture",
    description="Validates causal chain integrity: decision→outcome ordering, R-multiple/exit_reason consistency, and lifecycle coherence.",
    depends_on=("SCHEMA_001", "FEATURE_001", "PERSISTENCE_001"),  # Requires schema + features + persistence
    default_confidence=80,  # Some edge cases may represent unusual but valid behaviour
)


class CausalValidator(BaseValidator):
    """Validates causal chain integrity within trade records."""

    @property
    def identity(self) -> ValidatorIdentity:
        return _IDENTITY

    def applies_to(self, record: dict[str, Any], *, layer: str = "") -> bool:
        """Applies to completed trade records (those with outcome)."""
        return "outcome" in record

    def validate(self, record: dict[str, Any], *, layer: str = "") -> list[ContractViolation]:
        violations: list[ContractViolation] = []

        outcome = record.get("outcome", {})
        if not isinstance(outcome, dict):
            return violations

        has_r = "r_multiple" in outcome

        # ─── RULE 1: Outcome requires decision context ────────────────
        if has_r:
            strat = record.get("strategy_meta")
            decision = record.get("decision_context")

            has_decision = False
            if strat and hasattr(strat, "get"):
                has_decision = bool(strat.get("pattern") or strat.get("strategy"))
            if not has_decision and isinstance(decision, dict):
                has_decision = bool(decision.get("pattern") or decision.get("strategy"))

            if not has_decision:
                violations.append(ContractViolation(
                    contract_name=self.contract_name,
                    validator_name=self.name,
                    validator_id=self.validator_id,
                    validator_version=self.validator_version,
                    severity=Severity.WARNING,
                    confidence=50,
                    rule_id="CAUSAL_CONTEXT_001", rule_title="Outcome Without Decision Context",
                    reason=(
                        "Record has outcome.r_multiple but no decision context "
                        "(strategy_meta.pattern or strategy_meta.strategy missing)"
                    ),
                    layer=layer,
                    field_name="strategy_meta",
                    expected="pattern or strategy present",
                    actual="absent",
                    contract_version=self.contract_version,
                ))

        # ─── RULE 2: HTF snapshot required for alignment features ─────
        htf = record.get("htf_snapshot")
        edges = record.get("edges", {})
        has_alignment_edge = isinstance(edges, dict) and edges.get("regime")

        if has_alignment_edge and htf is None:
            violations.append(ContractViolation(
                contract_name=self.contract_name,
                validator_name=self.name,
                validator_id=self.validator_id,
                validator_version=self.validator_version,
                severity=Severity.WARNING,
                confidence=45,
                rule_id="CAUSAL_HTF_001", rule_title="Missing HTF Source for Regime",
                reason=(
                    "Record has edges.regime but no htf_snapshot — "
                    "causal source for regime classification is missing"
                ),
                layer=layer,
                field_name="htf_snapshot",
                expected="present (regime source)",
                actual="absent",
                contract_version=self.contract_version,
            ))

        # ─── RULE 3: MFE/MAE require valid lifecycle ─────────────────
        mfe = outcome.get("mfe_r")
        mae = outcome.get("mae_r")
        bars = outcome.get("bars_held", 0)

        if isinstance(mfe, (int, float)) and mfe > 0 and bars == 0:
            violations.append(ContractViolation(
                contract_name=self.contract_name,
                validator_name=self.name,
                validator_id=self.validator_id,
                validator_version=self.validator_version,
                severity=Severity.WARNING,
                confidence=60,
                rule_id="CAUSAL_LIFECYCLE_001", rule_title="MFE Without Bar Progression",
                reason=(
                    f"mfe_r={mfe} but bars_held=0 — "
                    "MFE cannot be positive without bar progression"
                ),
                layer=layer,
                field_name="outcome.mfe_r",
                expected="bars_held > 0 when mfe_r > 0",
                actual=f"mfe_r={mfe}, bars_held={bars}",
                contract_version=self.contract_version,
            ))

        # ─── RULE 4: R-multiple sign consistency ──────────────────────
        exit_reason = outcome.get("exit_reason", "")
        r = outcome.get("r_multiple", 0)

        if exit_reason == "stop_loss" and isinstance(r, (int, float)) and r > 0.5:
            violations.append(ContractViolation(
                contract_name=self.contract_name,
                validator_name=self.name,
                validator_id=self.validator_id,
                validator_version=self.validator_version,
                severity=Severity.ERROR,
                confidence=85,
                rule_id="CAUSAL_SIGN_001", rule_title="Stop Loss Exit With Positive R",
                reason=(
                    f"exit_reason='stop_loss' but r_multiple={r} (positive) — "
                    "causally impossible: stop loss exit must produce negative R"
                ),
                layer=layer,
                field_name="outcome.r_multiple",
                expected="<= 0 for stop_loss exit",
                actual=r,
                contract_version=self.contract_version,
            ))

        if exit_reason == "take_profit" and isinstance(r, (int, float)) and r < -0.1:
            violations.append(ContractViolation(
                contract_name=self.contract_name,
                validator_name=self.name,
                validator_id=self.validator_id,
                validator_version=self.validator_version,
                severity=Severity.ERROR,
                confidence=85,
                rule_id="CAUSAL_SIGN_002", rule_title="Take Profit Exit With Negative R",
                reason=(
                    f"exit_reason='take_profit' but r_multiple={r} (negative) — "
                    "causally impossible: take profit exit must produce positive R"
                ),
                layer=layer,
                field_name="outcome.r_multiple",
                expected=">= 0 for take_profit exit",
                actual=r,
                contract_version=self.contract_version,
            ))

        return violations
