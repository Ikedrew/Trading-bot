"""
Unit tests for risk/metrics.py — verify counters, averages, snapshot, and reset.
"""

import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from risk.metrics import RiskMetrics


class TestAcceptedTracking:
    def test_accepted_increments_counter(self):
        m = RiskMetrics()
        m.record_accepted(pattern="HAMMER", rr=2.0, sl_distance=0.001)
        assert m.accepted_total == 1

    def test_accepted_tracks_pattern(self):
        m = RiskMetrics()
        m.record_accepted(pattern="HAMMER", rr=2.0, sl_distance=0.001)
        m.record_accepted(pattern="HAMMER", rr=2.5, sl_distance=0.002)
        m.record_accepted(pattern="ENGULFING", rr=3.0, sl_distance=0.003)
        assert m.accepted_by_pattern["HAMMER"] == 2
        assert m.accepted_by_pattern["ENGULFING"] == 1


class TestRejectedTracking:
    def test_rejected_increments_counter(self):
        m = RiskMetrics()
        m.record_rejected(reason="ZERO_RISK", pattern="HAMMER")
        assert m.rejected_total == 1

    def test_rejected_tracks_reason(self):
        m = RiskMetrics()
        m.record_rejected(reason="ZERO_RISK", pattern="HAMMER")
        m.record_rejected(reason="ZERO_RISK", pattern="ENGULFING")
        m.record_rejected(reason="UNSUPPORTED", pattern="UNKNOWN")
        assert m.rejections_by_reason["ZERO_RISK"] == 2
        assert m.rejections_by_reason["UNSUPPORTED"] == 1

    def test_rejected_tracks_pattern(self):
        m = RiskMetrics()
        m.record_rejected(reason="ZERO_RISK", pattern="HAMMER")
        m.record_rejected(reason="UNSUPPORTED", pattern="HAMMER")
        assert m.rejections_by_pattern["HAMMER"] == 2


class TestRRTracking:
    def test_avg_rr_single(self):
        m = RiskMetrics()
        m.record_accepted(pattern="X", rr=2.5, sl_distance=0.001)
        assert m.avg_rr == 2.5

    def test_avg_rr_multiple(self):
        m = RiskMetrics()
        m.record_accepted(pattern="X", rr=2.0, sl_distance=0.001)
        m.record_accepted(pattern="X", rr=3.0, sl_distance=0.001)
        assert m.avg_rr == 2.5

    def test_avg_rr_empty(self):
        m = RiskMetrics()
        assert m.avg_rr == 0.0

    def test_avg_sl_distance(self):
        m = RiskMetrics()
        m.record_accepted(pattern="X", rr=2.0, sl_distance=0.001)
        m.record_accepted(pattern="X", rr=2.0, sl_distance=0.003)
        assert abs(m.avg_sl_distance - 0.002) < 1e-9


class TestSnapshot:
    def test_snapshot_values_consistent(self):
        m = RiskMetrics()
        m.record_accepted(pattern="HAMMER", rr=2.0, sl_distance=0.001)
        m.record_rejected(reason="ZERO_RISK", pattern="ENGULFING")
        s = m.snapshot()
        assert s["accepted_total"] == 1
        assert s["rejected_total"] == 1
        assert s["total_evaluated"] == 2
        assert s["acceptance_rate"] == 50.0
        assert s["rejection_rate"] == 50.0
        assert s["avg_rr"] == 2.0
        assert s["avg_sl_distance"] == 0.001
        assert s["rejections_by_reason"] == {"ZERO_RISK": 1}
        assert s["accepted_by_pattern"] == {"HAMMER": 1}

    def test_snapshot_empty(self):
        m = RiskMetrics()
        s = m.snapshot()
        assert s["total_evaluated"] == 0
        assert s["acceptance_rate"] == 0.0


class TestReset:
    def test_reset_clears_all(self):
        m = RiskMetrics()
        m.record_accepted(pattern="X", rr=2.0, sl_distance=0.001)
        m.record_rejected(reason="Y", pattern="Z")
        m.reset()
        assert m.accepted_total == 0
        assert m.rejected_total == 0
        assert m.avg_rr == 0.0
        assert m.avg_sl_distance == 0.0
        assert m.rejections_by_reason == {}
        assert m.accepted_by_pattern == {}


class TestBoundedMemory:
    def test_rolling_window_bounded(self):
        m = RiskMetrics()
        # Record more than window size
        for i in range(300):
            m.record_accepted(pattern="X", rr=float(i), sl_distance=float(i) * 0.001)
        # Deque should be capped at 200
        assert len(m._rr_values) == 200
        assert len(m._sl_distances) == 200


# ─── RUNNER ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    test_classes = [
        TestAcceptedTracking, TestRejectedTracking, TestRRTracking,
        TestSnapshot, TestReset, TestBoundedMemory,
    ]
    passed = 0
    failed = 0
    errors = []

    for cls in test_classes:
        instance = cls()
        for method_name in dir(instance):
            if not method_name.startswith("test_"):
                continue
            try:
                getattr(instance, method_name)()
                passed += 1
            except AssertionError as e:
                failed += 1
                errors.append(f"  FAIL {cls.__name__}.{method_name}: {e}")
            except Exception as e:
                failed += 1
                errors.append(f"  ERROR {cls.__name__}.{method_name}: {type(e).__name__}: {e}")

    print(f"\nRISK METRICS TESTS: {passed} passed, {failed} failed")
    if errors:
        for e in errors:
            print(e)
    else:
        print("ALL PASS")
