"""
Canonical identity helper tests — Stage 1 of the lineage remediation.

Proves:
    - deterministic minting
    - mandatory timestamp normalization (int vs float bar_time)
    - symbol/pattern normalization
    - empty-component guard
    - replay stability
"""

from core.identity.canonical import make_canonical_opportunity_id


class TestDeterministicMinting:
    def test_format(self):
        cid = make_canonical_opportunity_id(
            symbol="EURUSD", bar_time=1784800000, pattern="TWEEZER_TOP"
        )
        assert cid == "EURUSD*1784800000*TWEEZER_TOP"

    def test_same_inputs_same_id(self):
        a = make_canonical_opportunity_id(symbol="GBPUSD", bar_time=1000, pattern="HAMMER")
        b = make_canonical_opportunity_id(symbol="GBPUSD", bar_time=1000, pattern="HAMMER")
        assert a == b
        assert a == "GBPUSD*1000*HAMMER"


class TestTimestampNormalization:
    def test_int_vs_float_identical(self):
        a = make_canonical_opportunity_id(symbol="EURUSD", bar_time=1784800000, pattern="HAMMER")
        b = make_canonical_opportunity_id(symbol="EURUSD", bar_time=1784800000.0, pattern="HAMMER")
        assert a == b

    def test_float_fractional_truncates(self):
        cid = make_canonical_opportunity_id(symbol="EURUSD", bar_time=1784800000.9, pattern="HAMMER")
        assert "*1784800000*" in cid


class TestComponentNormalization:
    def test_symbol_case_and_whitespace(self):
        a = make_canonical_opportunity_id(symbol=" eurusd ", bar_time=1, pattern="HAMMER")
        b = make_canonical_opportunity_id(symbol="EURUSD", bar_time=1, pattern="HAMMER")
        assert a == b

    def test_pattern_case(self):
        a = make_canonical_opportunity_id(symbol="EURUSD", bar_time=1, pattern="hammer")
        assert a.endswith("*HAMMER")


class TestEmptyGuards:
    def test_empty_pattern_returns_empty(self):
        assert make_canonical_opportunity_id(symbol="EURUSD", bar_time=1, pattern="") == ""

    def test_empty_symbol_returns_empty(self):
        assert make_canonical_opportunity_id(symbol="", bar_time=1, pattern="HAMMER") == ""


class TestReplayStability:
    def test_no_runtime_inputs_affect_id(self):
        """cycle_id, correlation ids, session ids must not exist in the ID."""
        cid = make_canonical_opportunity_id(symbol="EURUSD", bar_time=42, pattern="HAMMER")
        assert "COR" not in cid
        assert "HORIZON" not in cid
        assert cid.count("*") == 2

    def test_distinct_patterns_distinct_ids_same_bar(self):
        a = make_canonical_opportunity_id(symbol="EURUSD", bar_time=42, pattern="HAMMER")
        b = make_canonical_opportunity_id(symbol="EURUSD", bar_time=42, pattern="MORNING_STAR")
        assert a != b
