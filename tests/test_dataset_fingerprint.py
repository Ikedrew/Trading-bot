"""
Tests for Dataset Fingerprint — content-identity for research populations.

Verifies:
- Same records → same fingerprint
- Changed record → different fingerprint
- Changed observation count → different fingerprint
- JSON key ordering does NOT change fingerprint
- Different filtered populations produce different fingerprints
- Experiment result contains dataset fingerprint
- Fingerprint is deterministic across calls
- Empty population handled correctly
- Serialisation roundtrip
"""
import sys
import json

import pytest

sys.path.insert(0, ".")

from research_engine.lifecycle.dataset_fingerprint import (
    DatasetFingerprint,
    build_dataset_fingerprint,
    compute_content_hash,
    _canonicalise_value,
)


# ═══════════════════════════════════════════════════════════════════════════════
# CORE HASHING
# ═══════════════════════════════════════════════════════════════════════════════


class TestContentHash:
    def test_same_records_same_hash(self):
        """Identical records produce identical hash."""
        records = [{"symbol": "EURUSD", "time": 100, "r": 0.5}]
        h1 = compute_content_hash(records)
        h2 = compute_content_hash(records)
        assert h1 == h2

    def test_changed_record_different_hash(self):
        """Modified record produces different hash."""
        r1 = [{"symbol": "EURUSD", "time": 100, "r": 0.5}]
        r2 = [{"symbol": "EURUSD", "time": 100, "r": 0.6}]
        assert compute_content_hash(r1) != compute_content_hash(r2)

    def test_changed_count_different_hash(self):
        """Different number of records produces different hash."""
        r1 = [{"symbol": "A", "time": 1}]
        r2 = [{"symbol": "A", "time": 1}, {"symbol": "B", "time": 2}]
        assert compute_content_hash(r1) != compute_content_hash(r2)

    def test_key_ordering_does_not_change_hash(self):
        """JSON key ordering is normalised — same content, different key order → same hash."""
        r1 = [{"b": 2, "a": 1, "c": 3}]
        r2 = [{"a": 1, "c": 3, "b": 2}]
        assert compute_content_hash(r1) == compute_content_hash(r2)

    def test_record_ordering_does_not_change_hash(self):
        """Records in different order produce the same hash (sorted by canonical key)."""
        r1 = [{"symbol": "A", "time": 1}, {"symbol": "B", "time": 2}]
        r2 = [{"symbol": "B", "time": 2}, {"symbol": "A", "time": 1}]
        assert compute_content_hash(r1) == compute_content_hash(r2)

    def test_float_rounding_stability(self):
        """Tiny floating-point differences below precision threshold are normalised."""
        r1 = [{"value": 1.000000001}]  # 9th decimal
        r2 = [{"value": 1.000000002}]  # Differs at 9th decimal → rounds same at 8dp
        assert compute_content_hash(r1) == compute_content_hash(r2)

    def test_meaningful_float_difference(self):
        """Larger float differences produce different hashes."""
        r1 = [{"value": 1.001}]
        r2 = [{"value": 1.002}]
        assert compute_content_hash(r1) != compute_content_hash(r2)

    def test_filtered_population_different(self):
        """Subset of records produces different hash from full set."""
        full = [{"symbol": "A", "time": i} for i in range(10)]
        filtered = full[:5]
        assert compute_content_hash(full) != compute_content_hash(filtered)

    def test_deterministic_across_calls(self):
        """Same input always produces same output (no randomness)."""
        records = [{"x": i, "y": i * 2.5} for i in range(100)]
        h1 = compute_content_hash(records)
        h2 = compute_content_hash(records)
        h3 = compute_content_hash(records)
        assert h1 == h2 == h3

    def test_empty_records(self):
        """Empty list produces a deterministic hash."""
        h1 = compute_content_hash([])
        h2 = compute_content_hash([])
        assert h1 == h2


# ═══════════════════════════════════════════════════════════════════════════════
# FINGERPRINT BUILDER
# ═══════════════════════════════════════════════════════════════════════════════


class TestFingerprintBuilder:
    def test_basic_build(self):
        records = [{"symbol": "EURUSD", "time": 1000, "pattern": "TBC"}]
        fp = build_dataset_fingerprint(records, dataset_id="test", population="TBC only")
        assert fp.dataset_id == "test"
        assert fp.observation_count == 1
        assert fp.content_hash != "EMPTY_POPULATION"
        assert fp.fingerprint_algorithm == "SHA-256"
        assert len(fp.content_hash) == 64  # SHA-256 hex digest

    def test_empty_population(self):
        fp = build_dataset_fingerprint([], dataset_id="empty")
        assert fp.content_hash == "EMPTY_POPULATION"
        assert fp.observation_count == 0

    def test_timestamps_extracted(self):
        records = [
            {"symbol": "A", "time": 100},
            {"symbol": "B", "time": 200},
            {"symbol": "C", "time": 150},
        ]
        fp = build_dataset_fingerprint(records)
        assert fp.first_timestamp == 100
        assert fp.last_timestamp == 200

    def test_symbols_extracted(self):
        records = [{"symbol": "EURUSD"}, {"symbol": "GBPUSD"}, {"symbol": "EURUSD"}]
        fp = build_dataset_fingerprint(records)
        assert set(fp.symbols) == {"EURUSD", "GBPUSD"}

    def test_filters_recorded(self):
        records = [{"symbol": "A", "time": 1}]
        fp = build_dataset_fingerprint(records, filters_applied=["pattern=TBC", "direction=SELL"])
        assert "pattern=TBC" in fp.filters_applied

    def test_serialisation_roundtrip(self):
        records = [{"symbol": "A", "time": 1, "r": 0.5}]
        fp = build_dataset_fingerprint(records, dataset_id="test", population="pop1")
        data = fp.to_dict()
        fp2 = DatasetFingerprint.from_dict(data)
        assert fp2.content_hash == fp.content_hash
        assert fp2.observation_count == fp.observation_count
        assert fp2.dataset_id == fp.dataset_id

    def test_unavailable_placeholder(self):
        fp = DatasetFingerprint.unavailable("no original data")
        assert fp.dataset_id == "UNAVAILABLE"
        assert "UNAVAILABLE" in fp.content_hash


# ═══════════════════════════════════════════════════════════════════════════════
# CANONICALISATION
# ═══════════════════════════════════════════════════════════════════════════════


class TestCanonicalisation:
    def test_dict_keys_sorted(self):
        result = _canonicalise_value({"z": 1, "a": 2, "m": 3})
        keys = list(result.keys())
        assert keys == ["a", "m", "z"]

    def test_nested_dict_sorted(self):
        result = _canonicalise_value({"outer": {"z": 1, "a": 2}})
        inner_keys = list(result["outer"].keys())
        assert inner_keys == ["a", "z"]

    def test_float_rounded(self):
        result = _canonicalise_value(1.123456789123)
        assert result == 1.12345679  # Rounded to 8dp

    def test_none_preserved(self):
        result = _canonicalise_value(None)
        assert result is None

    def test_list_order_preserved(self):
        result = _canonicalise_value([3, 1, 2])
        assert result == [3, 1, 2]
