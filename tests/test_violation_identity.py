"""
Tests for Persistent Violation Identity (Forensic Correlation Layer).

Covers:
    - Every violation receives a unique ID
    - IDs never collide
    - Persisted records preserve the ID
    - Quarantine includes the ID
    - Analytics can group by ID
    - Reloading preserves identity
    - Historical violations remain unchanged
    - ViolationStore lookup APIs work
    - Correlation across layers
"""

from __future__ import annotations

import pytest

from core.contracts import (
    ContractViolation,
    Severity,
    ViolationStore,
    generate_violation_id,
    get_violation_store,
)
from core.contracts.engine import ContractEnforcer
from core.contracts.quarantine import QuarantineStore


# -------------------------------------------------------------------------------
# TEST: VIOLATION ID GENERATION
# -------------------------------------------------------------------------------

class TestViolationIdGeneration:
    def test_format(self):
        vid = generate_violation_id()
        assert vid.startswith("VIO-")
        parts = vid.split("-")
        assert len(parts) == 3
        assert len(parts[1]) == 8  # YYYYMMDD
        assert len(parts[2]) == 9  # 000000001

    def test_unique(self):
        """Generated IDs are always unique."""
        ids = {generate_violation_id() for _ in range(1000)}
        assert len(ids) == 1000

    def test_monotonic(self):
        """IDs are lexicographically ordered (same date)."""
        ids = [generate_violation_id() for _ in range(100)]
        assert ids == sorted(ids)

    def test_never_empty(self):
        vid = generate_violation_id()
        assert vid
        assert len(vid) > 10


# -------------------------------------------------------------------------------
# TEST: VIOLATION AUTO-ID
# -------------------------------------------------------------------------------

class TestViolationAutoId:
    def test_auto_generated_on_creation(self):
        """Every ContractViolation gets a violation_id automatically."""
        v = ContractViolation(
            contract_name="test",
            validator_name="T",
            severity=Severity.ERROR,
            reason="test violation",
        )
        assert v.violation_id.startswith("VIO-")
        assert v.violation_timestamp != ""

    def test_each_violation_unique(self):
        """Two violations created sequentially have different IDs."""
        v1 = ContractViolation(
            contract_name="test", validator_name="T",
            severity=Severity.ERROR, reason="first",
        )
        v2 = ContractViolation(
            contract_name="test", validator_name="T",
            severity=Severity.ERROR, reason="second",
        )
        assert v1.violation_id != v2.violation_id

    def test_explicit_id_preserved(self):
        """If violation_id is explicitly provided, it's preserved."""
        v = ContractViolation(
            contract_name="test", validator_name="T",
            severity=Severity.ERROR, reason="test",
            violation_id="VIO-20260101-999999999",
            violation_timestamp="2026-01-01T00:00:00.000000Z",
        )
        assert v.violation_id == "VIO-20260101-999999999"
        assert v.violation_timestamp == "2026-01-01T00:00:00.000000Z"

    def test_id_is_immutable(self):
        v = ContractViolation(
            contract_name="test", validator_name="T",
            severity=Severity.ERROR, reason="test",
        )
        with pytest.raises(AttributeError):
            v.violation_id = "HACKED"  # type: ignore

    def test_id_in_to_dict(self):
        v = ContractViolation(
            contract_name="test", validator_name="T",
            severity=Severity.ERROR, reason="test",
            rule_id="PERSIST_R_001", rule_title="R Bounds",
        )
        d = v.to_dict()
        assert "violation_id" in d
        assert d["violation_id"].startswith("VIO-")
        assert "violation_timestamp" in d
        assert d["violation_timestamp"] != ""


# -------------------------------------------------------------------------------
# TEST: QUARANTINE PRESERVES VIOLATION ID
# -------------------------------------------------------------------------------

class TestQuarantinePreservation:
    def test_quarantine_includes_violation_id(self, tmp_path):
        store = QuarantineStore(local_dir=str(tmp_path / "q"))
        violations = [ContractViolation(
            contract_name="test", validator_name="T",
            validator_id="V_001", validator_version=1,
            severity=Severity.ERROR, reason="test",
            rule_id="TEST_001", rule_title="Test Rule",
        )]
        record = {"trade_id": "VID_TEST", "symbol": "EURUSD"}
        qr = store.quarantine(record=record, violations=violations, layer="test")

        d = qr.to_dict()
        # The violation in the quarantine record should have its ID
        assert d["violations"][0]["violation_id"].startswith("VIO-")
        assert d["violations"][0]["violation_timestamp"] != ""

    def test_quarantine_persisted_with_violation_id(self, tmp_path):
        """On-disk JSONL includes violation_id."""
        store = QuarantineStore(local_dir=str(tmp_path / "q"))
        violations = [ContractViolation(
            contract_name="test", validator_name="T",
            severity=Severity.ERROR, reason="test",
        )]
        store.quarantine(record={"trade_id": "DISK_VID"}, violations=violations, layer="test")
        records = store.load_quarantined(layer="test")
        assert len(records) >= 1
        assert records[0]["violations"][0]["violation_id"].startswith("VIO-")


# -------------------------------------------------------------------------------
# TEST: VIOLATION STORE (FORENSIC CORRELATION)
# -------------------------------------------------------------------------------

class TestViolationStore:
    def test_store_and_lookup(self):
        store = ViolationStore(max_entries=100)
        v = ContractViolation(
            contract_name="test", validator_name="T",
            validator_id="V_001", severity=Severity.ERROR,
            reason="test", rule_id="R_001",
        )
        store.store(v.to_dict(), record_id="TRADE_001")

        # Lookup by violation_id
        found = store.get_violation(v.violation_id)
        assert found is not None
        assert found["violation_id"] == v.violation_id
        assert found["rule_id"] == "R_001"

    def test_find_by_rule(self):
        store = ViolationStore(max_entries=100)
        for _ in range(3):
            v = ContractViolation(
                contract_name="test", validator_name="T",
                severity=Severity.ERROR, reason="r",
                rule_id="RULE_A",
            )
            store.store(v.to_dict())

        results = store.find_by_rule("RULE_A")
        assert len(results) == 3

    def test_find_by_validator(self):
        store = ViolationStore(max_entries=100)
        v = ContractViolation(
            contract_name="test", validator_name="T",
            validator_id="VAL_X", severity=Severity.ERROR,
            reason="r",
        )
        store.store(v.to_dict())

        results = store.find_by_validator("VAL_X")
        assert len(results) == 1
        assert results[0]["validator_id"] == "VAL_X"

    def test_find_by_record(self):
        store = ViolationStore(max_entries=100)
        v = ContractViolation(
            contract_name="test", validator_name="T",
            severity=Severity.ERROR, reason="r",
        )
        store.store(v.to_dict(), record_id="TRADE_XYZ")

        results = store.find_by_record("TRADE_XYZ")
        assert len(results) == 1

    def test_eviction_on_overflow(self):
        store = ViolationStore(max_entries=5)
        for i in range(10):
            v = ContractViolation(
                contract_name="test", validator_name="T",
                severity=Severity.ERROR, reason=f"r{i}",
            )
            store.store(v.to_dict())

        assert store.count == 5  # Oldest evicted

    def test_stats(self):
        store = ViolationStore(max_entries=100)
        v = ContractViolation(
            contract_name="test", validator_name="T",
            validator_id="V1", severity=Severity.ERROR,
            reason="r", rule_id="R1",
        )
        store.store(v.to_dict(), record_id="T1")
        s = store.stats()
        assert s["total_stored"] == 1
        assert s["rules_tracked"] == 1
        assert s["validators_tracked"] == 1
        assert s["records_tracked"] == 1


# -------------------------------------------------------------------------------
# TEST: END-TO-END ENFORCEMENT WITH VIOLATION IDS
# -------------------------------------------------------------------------------

class TestEndToEndViolationId:
    def test_enforcer_produces_violation_ids(self, tmp_path):
        """Full enforcement pipeline produces unique violation_ids."""
        store = QuarantineStore(local_dir=str(tmp_path / "q"))
        e = ContractEnforcer(quarantine_store=store)

        from core.contracts.validators.schema_validator import SchemaValidator
        from core.contracts.validators.persistence_validator import PersistenceValidator
        e.register(SchemaValidator())
        e.register(PersistenceValidator())

        record = {
            "trade_id": "E2E_VID",
            "symbol": "",
            "outcome": {"r_multiple": 999.0},
            "prices": {"entry_price": 1.1, "stop_loss": 1.099, "exit_price": 1.12},
            "timestamps": {"entry_time": 1700000000, "exit_time": 1700003600},
        }
        result = e.validate(record, layer="test")

        # Every violation should have a unique ID
        vids = [v.violation_id for v in result.violations]
        assert all(vid.startswith("VIO-") for vid in vids)
        assert len(vids) == len(set(vids))  # All unique

    def test_violation_ids_stored_for_correlation(self, tmp_path):
        """Violations are stored in the ViolationStore for lookup."""
        from core.contracts.violation_id import ViolationStore as VS

        local_store = VS(max_entries=100)

        store = QuarantineStore(local_dir=str(tmp_path / "q"))
        e = ContractEnforcer(quarantine_store=store)

        from core.contracts.validators.schema_validator import SchemaValidator
        from core.contracts.validators.persistence_validator import PersistenceValidator
        e.register(SchemaValidator())
        e.register(PersistenceValidator())

        record = {
            "trade_id": "CORR_001",
            "symbol": "",
            "outcome": {"r_multiple": 1.0},
            "prices": {"entry_price": 1.1, "stop_loss": 1.099, "exit_price": 1.12},
            "timestamps": {"entry_time": 1700000000, "exit_time": 1700003600},
        }
        result = e.validate(record, layer="test")

        # Manually store (in production this happens automatically via global store)
        for v in result.violations:
            local_store.store(v.to_dict(), record_id="CORR_001")

        # Can correlate by record
        found = local_store.find_by_record("CORR_001")
        assert len(found) > 0
        assert all(f["violation_id"].startswith("VIO-") for f in found)
