"""
Contract Enforcement Engine — Dependency-aware validation orchestrator.

This is the SINGLE authority through which all persistence validation flows.
It coordinates validators via a directed dependency graph, classifies severity,
manages quarantine, and provides observability metrics.

EXECUTION MODEL:
    Validators execute in topological order derived from their dependency graph.
    A validator only runs if ALL its declared dependencies have PASSED.
    If a dependency FAILS, all downstream validators are SKIPPED.
    Independent validators (no shared failure chain) continue normally.

VALIDATION PIPELINE:
    Generate Record
         │
         ▼
    Dependency-Aware Scheduler
         │
    ┌────┴─────────────────────────────────────────┐
    │  SCHEMA_001 → FEATURE_001 → IMMUTABILITY_001 │
    │  SCHEMA_001 → PERSISTENCE_001                │
    │  SCHEMA_001 + FEATURE_001 + ... → CAUSAL_001 │
    └──────────────────────────────────────────────┘
         │
         ▼
    Severity Classification (max severity across all violations)
         │
    ┌────┴──────────┐
    │               │
    ▼               ▼
  Valid         Quarantined
    │               │
    ▼               ▼
  Persist      Persist to Quarantine
    │
    ▼
  Next Layer

Usage:
    from core.contracts import get_enforcer

    enforcer = get_enforcer()
    result = enforcer.validate(record, layer="shadow_trades")

    if result.should_propagate:
        persist_downstream(record)
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from core.contracts.base_validator import BaseValidator
from core.contracts.dependency_graph import DependencyGraph, GraphValidationError, ValidatorState
from core.contracts.quarantine import QuarantineRecord, QuarantineStore
from core.contracts.severity import Severity
from core.contracts.validator_identity import ValidatorIdentity, ValidatorRegistry
from core.contracts.violation import ContractViolation

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# VALIDATOR EXECUTION RECORD
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class ValidatorExecution:
    """Execution record for a single validator during a validation run."""
    validator_id: str
    validator_name: str
    state: ValidatorState
    violations: list[ContractViolation] = field(default_factory=list)
    skip_reason: str = ""
    elapsed_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "validator_id": self.validator_id,
            "validator_name": self.validator_name,
            "state": self.state.value,
            "violation_count": len(self.violations),
            "skip_reason": self.skip_reason,
            "elapsed_ms": round(self.elapsed_ms, 3),
        }


# ═══════════════════════════════════════════════════════════════════════════════
# VALIDATION RESULT
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class ValidationResult:
    """
    Result of validating a record through the enforcement pipeline.

    Attributes:
        valid: True if no violations at ERROR or above
        max_severity: Highest severity encountered
        violations: All violations found
        quarantined: Whether the record was quarantined
        quarantine_record: The quarantine envelope (if quarantined)
        elapsed_ms: Validation duration in milliseconds
        validators_run: Number of validators actually executed
        execution_log: Per-validator execution state (PASSED/FAILED/SKIPPED/...)
    """

    valid: bool
    max_severity: Severity
    violations: list[ContractViolation] = field(default_factory=list)
    quarantined: bool = False
    quarantine_record: QuarantineRecord | None = None
    elapsed_ms: float = 0.0
    validators_run: int = 0
    execution_log: list[ValidatorExecution] = field(default_factory=list)

    @property
    def should_propagate(self) -> bool:
        """Whether the record is safe for downstream propagation."""
        return not self.max_severity.blocks_propagation

    @property
    def needs_alert(self) -> bool:
        """Whether an architecture alert should be raised."""
        return self.max_severity.requires_alert

    @property
    def needs_halt(self) -> bool:
        """Whether processing should halt."""
        return self.max_severity.requires_halt

    @property
    def skipped_validators(self) -> list[str]:
        """Validator IDs that were skipped due to dependency failure."""
        return [e.validator_id for e in self.execution_log if e.state == ValidatorState.SKIPPED]

    @property
    def failed_validators(self) -> list[str]:
        """Validator IDs that produced ERROR+ violations."""
        return [e.validator_id for e in self.execution_log if e.state == ValidatorState.FAILED]

    def to_dict(self) -> dict[str, Any]:
        """Serialize for logging/metrics."""
        return {
            "valid": self.valid,
            "max_severity": self.max_severity.name,
            "violation_count": len(self.violations),
            "quarantined": self.quarantined,
            "elapsed_ms": round(self.elapsed_ms, 3),
            "validators_run": self.validators_run,
            "skipped": self.skipped_validators,
            "failed": self.failed_validators,
            "violations": [v.to_dict() for v in self.violations[:5]],
            "execution_log": [e.to_dict() for e in self.execution_log],
        }


# ═══════════════════════════════════════════════════════════════════════════════
# CONTRACT ENFORCER (singleton)
# ═══════════════════════════════════════════════════════════════════════════════

class ContractEnforcer:
    """
    Dependency-aware contract enforcement engine.

    Manages registered validators, builds a dependency graph, executes
    validators in topological order with skip-propagation, handles
    quarantine, and tracks metrics.

    Thread-safe. Deterministic. Read-only validation.
    """

    def __init__(self, *, quarantine_store: QuarantineStore | None = None) -> None:
        self._validators: list[BaseValidator] = []
        self._validators_by_id: dict[str, BaseValidator] = {}
        self._quarantine = quarantine_store or QuarantineStore()
        self._registry = ValidatorRegistry()
        self._graph: DependencyGraph | None = None
        self._graph_built = False
        self._lock = threading.Lock()

        # ─── Metrics ──────────────────────────────────────────────────
        self._metrics = {
            "total_validated": 0,
            "info_count": 0,
            "warning_count": 0,
            "error_count": 0,
            "critical_count": 0,
            "fatal_count": 0,
            "quarantine_count": 0,
            "clean_count": 0,
            "total_validation_ms": 0.0,
            "total_skipped": 0,
        }
        self._validator_failures: dict[str, int] = {}
        self._contract_violations: dict[str, int] = {}
        self._layer_violations: dict[str, int] = {}

    # ─── VALIDATOR REGISTRATION ───────────────────────────────────────

    def register(self, validator: BaseValidator) -> None:
        """
        Register a validator with the enforcement engine.

        Validators are executed in dependency-graph order after build_graph().
        Adding a validator requires NO changes to the engine.
        Identity metadata is captured in the governance registry.
        """
        if not isinstance(validator, BaseValidator):
            raise TypeError(f"Validator must subclass BaseValidator, got {type(validator).__name__}")

        self._validators.append(validator)
        self._validators_by_id[validator.validator_id] = validator

        # Register identity in governance registry
        try:
            identity = validator.identity
            self._registry._register(identity)
        except Exception as exc:
            logger.warning(
                "[CONTRACT_ENGINE] identity_registration_warning validator=%s: %s",
                validator.name, exc,
            )

        # Invalidate any existing graph (must rebuild)
        self._graph_built = False

        logger.info(
            "[CONTRACT_ENGINE] registered validator=%s id=%s version=%d contract=%s depends_on=%s",
            validator.name, validator.validator_id, validator.validator_version,
            validator.contract_name, list(validator.depends_on),
        )

    def unregister(self, validator_name: str) -> bool:
        """Remove a validator by name. Returns True if found and removed."""
        before = len(self._validators)
        removed = [v for v in self._validators if v.name == validator_name]
        self._validators = [v for v in self._validators if v.name != validator_name]
        for v in removed:
            self._validators_by_id.pop(v.validator_id, None)
        self._graph_built = False
        return len(self._validators) < before

    def build_graph(self) -> None:
        """
        Build and validate the dependency graph from registered validators.

        Called automatically on first validate() if not already built.
        Can be called explicitly for startup validation (fail-fast).

        Raises:
            GraphValidationError: If graph has cycles, missing refs, etc.
        """
        graph = DependencyGraph()

        for validator in self._validators:
            graph.add_node(
                validator.validator_id,
                depends_on=list(validator.depends_on),
            )

        graph.build()  # Raises GraphValidationError on invalid graph
        self._graph = graph
        self._graph_built = True

        logger.info(
            "[CONTRACT_ENGINE] dependency graph built — order=%s",
            graph.execution_order,
        )

    @property
    def validator_count(self) -> int:
        return len(self._validators)

    @property
    def registered_validators(self) -> list[str]:
        return [v.name for v in self._validators]

    @property
    def execution_order(self) -> list[str]:
        """Current execution order (validator IDs). Graph must be built."""
        self._ensure_graph()
        assert self._graph is not None
        return self._graph.execution_order

    # ─── VALIDATION PIPELINE (DEPENDENCY-AWARE) ───────────────────────

    def validate(
        self,
        record: dict[str, Any],
        *,
        layer: str = "unknown",
        correlation_id: str = "",
    ) -> ValidationResult:
        """
        Run validators in dependency order against a record.

        Args:
            record: The record to validate (read-only — never modified)
            layer: Which persistence layer is requesting validation
            correlation_id: Decision Spine ID linking all artefacts from one cycle

        EXECUTION MODEL:
            1. Resolve execution order from dependency graph
            2. For each validator in order:
                a. Check if dependencies all PASSED
                b. If any dependency FAILED → SKIP this validator
                c. If applies_to() → False → NOT_APPLICABLE
                d. Run validate() → collect violations
                e. Classify state (PASSED if no ERROR+, FAILED otherwise)
            3. Compute max severity
            4. Quarantine if needed
            5. Return result with full execution log

        GUARANTEES:
            - Never mutates the record
            - Never raises to caller
            - Deterministic (same input → same result)
            - Short-circuits on FATAL
            - Skipped validators never produce violations
        """
        self._ensure_graph()
        assert self._graph is not None

        start = time.perf_counter()
        all_violations: list[ContractViolation] = []
        execution_log: list[ValidatorExecution] = []
        validators_run = 0
        max_severity = Severity.INFO

        # Track per-validator state for dependency resolution
        states: dict[str, ValidatorState] = {}
        failed_ids: set[str] = set()

        try:
            for vid in self._graph.execution_order:
                validator = self._validators_by_id.get(vid)
                if validator is None:
                    continue

                # ─── DEPENDENCY CHECK: are all prerequisites satisfied? ─
                skip_reason = self._check_dependencies(vid, states)
                if skip_reason:
                    exec_record = ValidatorExecution(
                        validator_id=vid,
                        validator_name=validator.name,
                        state=ValidatorState.SKIPPED,
                        skip_reason=skip_reason,
                    )
                    execution_log.append(exec_record)
                    states[vid] = ValidatorState.SKIPPED
                    continue

                # ─── APPLICABILITY CHECK ──────────────────────────────
                try:
                    if not validator.applies_to(record, layer=layer):
                        exec_record = ValidatorExecution(
                            validator_id=vid,
                            validator_name=validator.name,
                            state=ValidatorState.NOT_APPLICABLE,
                        )
                        execution_log.append(exec_record)
                        # NOT_APPLICABLE counts as PASSED for dependency purposes
                        states[vid] = ValidatorState.PASSED
                        continue
                except Exception:
                    states[vid] = ValidatorState.PASSED
                    continue

                # ─── EXECUTE VALIDATOR ────────────────────────────────
                validators_run += 1
                v_start = time.perf_counter()

                try:
                    violations = validator.validate(record, layer=layer)
                except Exception as exc:
                    logger.warning(
                        "[CONTRACT_ENGINE] validator_error name=%s error=%s",
                        validator.name, exc,
                    )
                    with self._lock:
                        self._validator_failures[validator.name] = (
                            self._validator_failures.get(validator.name, 0) + 1
                        )
                    exec_record = ValidatorExecution(
                        validator_id=vid,
                        validator_name=validator.name,
                        state=ValidatorState.ERROR,
                        elapsed_ms=(time.perf_counter() - v_start) * 1000,
                    )
                    execution_log.append(exec_record)
                    # ERROR state counts as FAILED for dependency purposes
                    states[vid] = ValidatorState.FAILED
                    failed_ids.add(vid)
                    continue

                v_elapsed = (time.perf_counter() - v_start) * 1000

                # ─── CLASSIFY VALIDATOR STATE ─────────────────────────
                has_error_plus = any(v.severity >= Severity.ERROR for v in violations) if violations else False

                if has_error_plus:
                    state = ValidatorState.FAILED
                    failed_ids.add(vid)
                else:
                    state = ValidatorState.PASSED

                # Collect violations
                if violations:
                    all_violations.extend(violations)
                    for v in violations:
                        if v.severity > max_severity:
                            max_severity = v.severity

                exec_record = ValidatorExecution(
                    validator_id=vid,
                    validator_name=validator.name,
                    state=state,
                    violations=violations or [],
                    elapsed_ms=v_elapsed,
                )
                execution_log.append(exec_record)
                states[vid] = state

                # Short-circuit on FATAL
                if max_severity >= Severity.FATAL:
                    # Mark all remaining as SKIPPED
                    remaining = [
                        oid for oid in self._graph.execution_order
                        if oid not in states
                    ]
                    for oid in remaining:
                        rv = self._validators_by_id.get(oid)
                        if rv:
                            execution_log.append(ValidatorExecution(
                                validator_id=oid,
                                validator_name=rv.name,
                                state=ValidatorState.SKIPPED,
                                skip_reason="FATAL severity reached",
                            ))
                            states[oid] = ValidatorState.SKIPPED
                    break

        except Exception as exc:
            logger.error("[CONTRACT_ENGINE] engine_error: %s", exc)

        elapsed_ms = (time.perf_counter() - start) * 1000

        # ─── QUARANTINE if severity >= ERROR ──────────────────────────
        quarantined = False
        quarantine_record = None

        if max_severity.requires_quarantine and all_violations:
            quarantine_record = self._quarantine.quarantine(
                record=record,
                violations=all_violations,
                layer=layer,
            )
            quarantined = True

        # ─── UPDATE METRICS ───────────────────────────────────────────
        skipped_count = sum(1 for e in execution_log if e.state == ValidatorState.SKIPPED)

        with self._lock:
            self._metrics["total_validated"] += 1
            self._metrics["total_validation_ms"] += elapsed_ms
            self._metrics["total_skipped"] += skipped_count

            if max_severity == Severity.INFO:
                self._metrics["info_count"] += 1
            elif max_severity == Severity.WARNING:
                self._metrics["warning_count"] += 1
            elif max_severity == Severity.ERROR:
                self._metrics["error_count"] += 1
            elif max_severity == Severity.CRITICAL:
                self._metrics["critical_count"] += 1
            elif max_severity == Severity.FATAL:
                self._metrics["fatal_count"] += 1

            if quarantined:
                self._metrics["quarantine_count"] += 1
            else:
                self._metrics["clean_count"] += 1

            for v in all_violations:
                self._contract_violations[v.contract_name] = (
                    self._contract_violations.get(v.contract_name, 0) + 1
                )
            if all_violations:
                self._layer_violations[layer] = (
                    self._layer_violations.get(layer, 0) + 1
                )

        # ─── BUILD RESULT ─────────────────────────────────────────────
        result = ValidationResult(
            valid=(max_severity < Severity.ERROR),
            max_severity=max_severity,
            violations=all_violations,
            quarantined=quarantined,
            quarantine_record=quarantine_record,
            elapsed_ms=elapsed_ms,
            validators_run=validators_run,
            execution_log=execution_log,
        )

        # ─── VIOLATION STORE (forensic correlation) ───────────────────
        # Store all violations for lookup/correlation. Never blocks.
        if all_violations:
            try:
                from core.contracts.violation_id import get_violation_store
                store = get_violation_store()
                record_id = str(
                    record.get("trade_id")
                    or record.get("record_id")
                    or record.get("cycle_id")
                    or ""
                )
                # Resolve correlation_id: explicit param > record field > active context
                effective_cor_id = correlation_id or record.get("correlation_id", "")
                if not effective_cor_id:
                    try:
                        from core.correlation import get_active_correlation
                        sym = record.get("symbol", "")
                        effective_cor_id = get_active_correlation(sym) if sym else ""
                    except ImportError:
                        pass

                for v in all_violations:
                    v_dict = v.to_dict()
                    # Inject correlation_id into serialized form for store
                    if effective_cor_id and not v_dict.get("correlation_id"):
                        v_dict["correlation_id"] = effective_cor_id
                    store.store(v_dict, record_id=record_id)
            except Exception:
                pass  # Store failure must never affect validation

        # ─── ALERTS ──────────────────────────────────────────────────
        if result.needs_alert:
            self._raise_alert(result, layer)

        return result

    def _check_dependencies(self, validator_id: str, states: dict[str, ValidatorState]) -> str:
        """
        Check if all dependencies of a validator have PASSED.

        Returns empty string if all satisfied, or a skip reason if not.
        """
        assert self._graph is not None
        deps = self._graph.get_dependencies(validator_id)

        for dep_id in deps:
            dep_state = states.get(dep_id)
            if dep_state is None:
                # Dependency not yet executed (shouldn't happen with topo sort)
                return f"dependency '{dep_id}' not yet executed"
            if dep_state == ValidatorState.FAILED or dep_state == ValidatorState.ERROR:
                return f"dependency '{dep_id}' FAILED"
            if dep_state == ValidatorState.SKIPPED:
                return f"dependency '{dep_id}' was SKIPPED"

        return ""

    def _ensure_graph(self) -> None:
        """Build graph if not already built. Lazy initialization."""
        if not self._graph_built:
            self.build_graph()

    # ─── BATCH VALIDATION ─────────────────────────────────────────────

    def validate_batch(
        self,
        records: list[dict[str, Any]],
        *,
        layer: str = "unknown",
    ) -> tuple[list[dict[str, Any]], list[ValidationResult]]:
        """
        Validate a batch of records. Returns (clean_records, all_results).

        Clean records are those that passed validation (should_propagate=True).
        Quarantined records are NOT included in the clean list.
        """
        clean: list[dict[str, Any]] = []
        results: list[ValidationResult] = []

        for record in records:
            result = self.validate(record, layer=layer)
            results.append(result)

            if result.should_propagate:
                clean.append(record)

            if result.needs_halt:
                logger.critical(
                    "[CONTRACT_ENGINE] FATAL violation — halting batch at record %d/%d",
                    len(results), len(records),
                )
                break

        return clean, results

    # ─── ALERTING ─────────────────────────────────────────────────────

    def _raise_alert(self, result: ValidationResult, layer: str) -> None:
        """Raise architecture alert for CRITICAL/FATAL violations."""
        try:
            primary = result.violations[0] if result.violations else None
            logger.critical(
                "[CONTRACT_ALERT] severity=%s layer=%s contract=%s "
                "rule_id=%s violation_id=%s reason=%s "
                "violations=%d quarantined=%s",
                result.max_severity.name, layer,
                primary.contract_name if primary else "?",
                primary.rule_id if primary else "?",
                primary.violation_id if primary else "?",
                primary.reason if primary else "?",
                len(result.violations),
                result.quarantined,
            )

            try:
                from core.discord_notifier import send_discord
                vid = primary.violation_id if primary else "?"
                cor = primary.correlation_id if primary else ""
                cor_part = f"`{cor}` | " if cor else ""
                send_discord("errors", (
                    f"🚨 **CONTRACT {result.max_severity.name}** | "
                    f"{cor_part}"
                    f"`{vid}` | "
                    f"layer=`{layer}` | "
                    f"rule=`{primary.rule_id if primary else '?'}` | "
                    f"reason: {primary.reason if primary else '?'}"
                ))
            except Exception:
                pass

        except Exception:
            pass

    # ─── METRICS / OBSERVABILITY ──────────────────────────────────────

    def metrics(self) -> dict[str, Any]:
        """Return structured enforcement metrics for monitoring."""
        with self._lock:
            total = self._metrics["total_validated"]
            quarantine_rate = (
                self._metrics["quarantine_count"] / total if total > 0 else 0.0
            )
            avg_ms = (
                self._metrics["total_validation_ms"] / total if total > 0 else 0.0
            )

            return {
                "total_validated": total,
                "severity_counts": {
                    "INFO": self._metrics["info_count"],
                    "WARNING": self._metrics["warning_count"],
                    "ERROR": self._metrics["error_count"],
                    "CRITICAL": self._metrics["critical_count"],
                    "FATAL": self._metrics["fatal_count"],
                },
                "quarantine_rate": round(quarantine_rate, 4),
                "quarantine_total": self._metrics["quarantine_count"],
                "clean_total": self._metrics["clean_count"],
                "total_skipped": self._metrics["total_skipped"],
                "avg_validation_ms": round(avg_ms, 3),
                "validator_failures": dict(self._validator_failures),
                "most_violated_contracts": dict(
                    sorted(self._contract_violations.items(), key=lambda x: x[1], reverse=True)[:10]
                ),
                "violations_by_layer": dict(self._layer_violations),
                "registered_validators": self.registered_validators,
                "execution_order": self.execution_order if self._graph_built else [],
            }

    def reset_metrics(self) -> None:
        """Reset all metrics counters (useful for testing)."""
        with self._lock:
            for key in self._metrics:
                if isinstance(self._metrics[key], int):
                    self._metrics[key] = 0
                else:
                    self._metrics[key] = 0.0
            self._validator_failures.clear()
            self._contract_violations.clear()
            self._layer_violations.clear()

    @property
    def quarantine_store(self) -> QuarantineStore:
        """Access the quarantine store for review/recovery."""
        return self._quarantine

    @property
    def registry(self) -> ValidatorRegistry:
        """Read-only governance registry of all validator identities."""
        return self._registry

    @property
    def dependency_graph(self) -> DependencyGraph | None:
        """Access the dependency graph (None if not built yet)."""
        return self._graph


# ═══════════════════════════════════════════════════════════════════════════════
# SINGLETON ACCESS
# ═══════════════════════════════════════════════════════════════════════════════

_enforcer: ContractEnforcer | None = None
_init_lock = threading.Lock()


def get_enforcer() -> ContractEnforcer:
    """Get or create the singleton ContractEnforcer."""
    global _enforcer
    if _enforcer is None:
        with _init_lock:
            if _enforcer is None:
                _enforcer = ContractEnforcer()
                _register_default_validators(_enforcer)
    return _enforcer


def _register_default_validators(enforcer: ContractEnforcer) -> None:
    """Register the built-in validators on first initialization."""
    # Register all contract rules into global registry
    try:
        from core.contracts.rules import register_all_rules
        register_all_rules()
    except Exception:
        pass

    try:
        from core.contracts.validators.schema_validator import SchemaValidator
        enforcer.register(SchemaValidator())
    except ImportError:
        pass

    try:
        from core.contracts.validators.feature_role_validator import FeatureRoleValidator
        enforcer.register(FeatureRoleValidator())
    except ImportError:
        pass

    try:
        from core.contracts.validators.immutability_validator import ImmutabilityValidator
        enforcer.register(ImmutabilityValidator())
    except ImportError:
        pass

    try:
        from core.contracts.validators.persistence_validator import PersistenceValidator
        enforcer.register(PersistenceValidator())
    except ImportError:
        pass

    try:
        from core.contracts.validators.causal_validator import CausalValidator
        enforcer.register(CausalValidator())
    except ImportError:
        pass

    try:
        from core.contracts.validators.correlation_validator import CorrelationValidator
        enforcer.register(CorrelationValidator())
    except ImportError:
        pass
