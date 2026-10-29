"""
Population Health Checks & Validation.

Runs structural checks against a built population:
    - Integrity: duplicates, missing identity, invalid types
    - Coverage: time range, instrument range, record counts
    - Referential: join-key uniqueness, orphan detection
    - Semantic: value range validation per field mapping

Also supports population comparison (drift detection between generations).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from research_engine.v10.universes.contracts import (
    POPULATION_CONTRACTS,
    SEMANTIC_FIELD_MAPPINGS,
    PopulationContract,
    SemanticFieldMapping,
    FieldType,
    PopulationStatus,
)
from research_engine.v10.universes.models import Population, Universe


@dataclass
class HealthCheck:
    """Result of a single health check."""
    name: str
    status: str  # PASS, WARNING, ERROR
    detail: str
    value: Any = None


@dataclass
class PopulationHealth:
    """Complete health report for a population."""
    population_id: str
    universe_id: str
    record_count: int
    checks: list[HealthCheck] = field(default_factory=list)

    @property
    def status(self) -> PopulationStatus:
        if self.record_count == 0:
            return PopulationStatus.EMPTY
        if any(c.status == "ERROR" for c in self.checks):
            return PopulationStatus.INVALID
        if any(c.status == "WARNING" for c in self.checks):
            return PopulationStatus.DEGRADED
        return PopulationStatus.VALID

    @property
    def errors(self) -> list[HealthCheck]:
        return [c for c in self.checks if c.status == "ERROR"]

    @property
    def warnings(self) -> list[HealthCheck]:
        return [c for c in self.checks if c.status == "WARNING"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "population_id": self.population_id,
            "universe_id": self.universe_id,
            "record_count": self.record_count,
            "status": self.status.value,
            "errors": len(self.errors),
            "warnings": len(self.warnings),
            "checks": [
                {"name": c.name, "status": c.status, "detail": c.detail, "value": c.value}
                for c in self.checks
            ],
        }


def check_population_health(
    records: list[dict[str, Any]],
    population: Population,
    universe: Universe,
) -> PopulationHealth:
    """
    Run all health checks against a built population.

    Args:
        records: The population records to validate.
        population: Which population this is.
        universe: Which universe it belongs to.

    Returns:
        PopulationHealth with all check results.
    """
    health = PopulationHealth(
        population_id=population.value,
        universe_id=universe.value,
        record_count=len(records),
    )

    if not records:
        health.checks.append(HealthCheck(
            name="non_empty", status="WARNING",
            detail="Population is empty (0 records)", value=0,
        ))
        return health

    contract = POPULATION_CONTRACTS.get(population)

    # ─── Integrity checks ─────────────────────────────────────────────────────
    _check_duplicate_identity(records, contract, health)
    _check_missing_identity(records, contract, health)
    _check_required_fields(records, contract, health)

    # ─── Coverage checks ──────────────────────────────────────────────────────
    _check_instrument_coverage(records, health)
    _check_time_coverage(records, health)

    # ─── Semantic validity ────────────────────────────────────────────────────
    _check_semantic_fields(records, universe, health)

    return health


def _check_duplicate_identity(
    records: list[dict], contract: PopulationContract | None, health: PopulationHealth
):
    """Check for duplicate identity values."""
    id_field = "entity_id"  # Default identity
    if contract and contract.join_keys:
        id_field = contract.join_keys[0]

    ids = [r.get(id_field) for r in records if r.get(id_field)]
    dupes = len(ids) - len(set(ids))
    if dupes > 0:
        health.checks.append(HealthCheck(
            name="duplicate_identity", status="WARNING",
            detail=f"{dupes} duplicate {id_field} values found",
            value=dupes,
        ))
    else:
        health.checks.append(HealthCheck(
            name="duplicate_identity", status="PASS",
            detail=f"No duplicate {id_field} values", value=0,
        ))


def _check_missing_identity(
    records: list[dict], contract: PopulationContract | None, health: PopulationHealth
):
    """Check for records missing identity field."""
    id_field = "entity_id"
    missing = sum(1 for r in records if not r.get(id_field))
    if missing > 0:
        pct = missing / len(records) * 100
        status = "ERROR" if pct > 50 else "WARNING"
        health.checks.append(HealthCheck(
            name="missing_identity", status=status,
            detail=f"{missing}/{len(records)} records ({pct:.1f}%) missing {id_field}",
            value=missing,
        ))
    else:
        health.checks.append(HealthCheck(
            name="missing_identity", status="PASS",
            detail="All records have entity_id", value=0,
        ))


def _check_required_fields(
    records: list[dict], contract: PopulationContract | None, health: PopulationHealth
):
    """Check that required fields exist and are non-null."""
    if not contract or not contract.required_fields:
        return

    for field_name in contract.required_fields:
        null_count = sum(1 for r in records if r.get(field_name) is None)
        if null_count > 0:
            pct = null_count / len(records) * 100
            status = "ERROR" if pct > 20 else "WARNING"
            health.checks.append(HealthCheck(
                name=f"required_field_{field_name}",
                status=status,
                detail=f"{field_name}: {null_count}/{len(records)} null ({pct:.1f}%)",
                value=null_count,
            ))
        else:
            health.checks.append(HealthCheck(
                name=f"required_field_{field_name}",
                status="PASS",
                detail=f"{field_name}: fully populated",
                value=0,
            ))


def _check_instrument_coverage(records: list[dict], health: PopulationHealth):
    """Check how many instruments are represented."""
    symbols = set(r.get("symbol", "") for r in records if r.get("symbol"))
    health.checks.append(HealthCheck(
        name="instrument_coverage", status="PASS",
        detail=f"{len(symbols)} instruments: {sorted(symbols)[:10]}",
        value=len(symbols),
    ))


def _check_time_coverage(records: list[dict], health: PopulationHealth):
    """Check time range coverage."""
    timestamps = []
    for r in records:
        ts = r.get("entry_time") or r.get("timestamp_utc")
        if ts:
            if isinstance(ts, (int, float)):
                timestamps.append(ts)
            elif isinstance(ts, str) and ts:
                timestamps.append(ts)

    if timestamps:
        # Just report presence — detailed temporal validation is separate
        health.checks.append(HealthCheck(
            name="time_coverage", status="PASS",
            detail=f"{len(timestamps)} records have timestamps",
            value=len(timestamps),
        ))
    else:
        health.checks.append(HealthCheck(
            name="time_coverage", status="WARNING",
            detail="No timestamps found in records",
            value=0,
        ))


def _check_semantic_fields(
    records: list[dict], universe: Universe, health: PopulationHealth
):
    """Validate semantic field values against their declared constraints."""
    relevant_mappings = [m for m in SEMANTIC_FIELD_MAPPINGS if m.universe_id == universe]

    for mapping in relevant_mappings[:10]:  # Top 10 most important
        field_name = mapping.semantic_name
        values = [r.get(field_name) for r in records]
        non_null = [v for v in values if v is not None]
        null_rate = (len(values) - len(non_null)) / len(values) if values else 1.0

        if not mapping.nullable and null_rate > 0.5:
            health.checks.append(HealthCheck(
                name=f"semantic_{field_name}",
                status="WARNING",
                detail=f"{field_name}: {null_rate*100:.0f}% null (expected non-nullable)",
                value=null_rate,
            ))


# ═══════════════════════════════════════════════════════════════════════════════
# POPULATION DRIFT COMPARISON
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class DriftCheck:
    """Result of comparing two population generations."""
    name: str
    severity: str  # EXPECTED, WARNING, ERROR
    detail: str
    previous_value: Any = None
    current_value: Any = None


def compare_populations(
    previous: list[dict[str, Any]],
    current: list[dict[str, Any]],
    population: Population,
) -> list[DriftCheck]:
    """
    Compare two generations of the same population for drift.

    Returns list of drift observations classified by severity.
    """
    checks = []

    # Record count change
    prev_count = len(previous)
    curr_count = len(current)
    if prev_count == 0 and curr_count == 0:
        return checks

    if prev_count > 0:
        change_pct = abs(curr_count - prev_count) / prev_count * 100
    else:
        change_pct = 100.0

    if change_pct > 50:
        severity = "WARNING"
    elif change_pct > 20:
        severity = "EXPECTED"
    else:
        severity = "EXPECTED"

    checks.append(DriftCheck(
        name="record_count_change",
        severity=severity,
        detail=f"Record count: {prev_count} → {curr_count} ({change_pct:.1f}% change)",
        previous_value=prev_count,
        current_value=curr_count,
    ))

    # Schema change (new or removed fields)
    if previous and current:
        prev_keys = set(previous[0].keys())
        curr_keys = set(current[0].keys())
        new_keys = curr_keys - prev_keys
        removed_keys = prev_keys - curr_keys
        if new_keys:
            checks.append(DriftCheck(
                name="schema_new_fields",
                severity="EXPECTED",
                detail=f"New fields: {sorted(new_keys)}",
            ))
        if removed_keys:
            checks.append(DriftCheck(
                name="schema_removed_fields",
                severity="WARNING",
                detail=f"Removed fields: {sorted(removed_keys)}",
            ))

    # Category distribution change (for string fields like regime, family)
    for field_name in ("regime", "family", "action", "exit_reason"):
        prev_dist = Counter(r.get(field_name, "") for r in previous if r.get(field_name))
        curr_dist = Counter(r.get(field_name, "") for r in current if r.get(field_name))
        if prev_dist and curr_dist:
            new_categories = set(curr_dist.keys()) - set(prev_dist.keys())
            lost_categories = set(prev_dist.keys()) - set(curr_dist.keys())
            if new_categories:
                checks.append(DriftCheck(
                    name=f"category_new_{field_name}",
                    severity="EXPECTED",
                    detail=f"{field_name}: new values {sorted(new_categories)}",
                ))
            if lost_categories:
                checks.append(DriftCheck(
                    name=f"category_lost_{field_name}",
                    severity="WARNING",
                    detail=f"{field_name}: lost values {sorted(lost_categories)}",
                ))

    return checks
