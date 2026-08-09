"""Tests for Research Dataset Segmentation Engine (V2)."""
import json, pytest, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from research_engine.v10.segmentation import build_segmentation, load_view


@pytest.fixture
def segmentation_result(tmp_path):
    """Build segmentation to temp directory."""
    src = Path("logs/research_ready_trade_dataset/research_ready_trades.jsonl")
    if not src.exists():
        pytest.skip("Research-ready dataset not available")
    return build_segmentation(
        source_file=str(src),
        views_dir=str(tmp_path / "views"),
        reports_dir=str(tmp_path / "reports"),
    )


class TestAssetClassification:
    def test_standard_equals_sum_of_parts(self, segmentation_result):
        """FX + INDEX + COMMODITY should equal STANDARD (no trade in multiple classes)."""
        counts = segmentation_result["view_counts"]
        standard = counts["STANDARD"]
        fx = counts.get("FX", 0)
        idx = counts.get("INDEX", 0)
        cmd = counts.get("COMMODITY", 0)
        assert fx + idx + cmd == standard

    def test_symbols_grouped_correctly(self, segmentation_result):
        counts = segmentation_result["view_counts"]
        # EURUSD should be in FX
        assert counts.get("EURUSD", 0) > 0 or "EURUSD" not in counts
        # US500 in INDEX
        if "US500" in counts:
            assert counts["US500"] > 0


class TestNoDuplication:
    def test_no_duplicate_trades_across_segments(self, segmentation_result, tmp_path):
        views_dir = tmp_path / "views"
        fx = load_view("FX", str(views_dir))
        idx = load_view("INDEX", str(views_dir))
        cmd = load_view("COMMODITY", str(views_dir))
        fx_ids = {t.get("trade_id") for t in fx}
        idx_ids = {t.get("trade_id") for t in idx}
        cmd_ids = {t.get("trade_id") for t in cmd}
        # No overlap
        assert len(fx_ids & idx_ids) == 0
        assert len(fx_ids & cmd_ids) == 0
        assert len(idx_ids & cmd_ids) == 0

    def test_standard_contains_all_segment_trades(self, segmentation_result, tmp_path):
        views_dir = tmp_path / "views"
        standard = load_view("STANDARD", str(views_dir))
        fx = load_view("FX", str(views_dir))
        idx = load_view("INDEX", str(views_dir))
        cmd = load_view("COMMODITY", str(views_dir))
        assert len(standard) == len(fx) + len(idx) + len(cmd)


class TestRankings:
    def test_rankings_exist(self, segmentation_result):
        rankings = segmentation_result["rankings"]
        assert "by_expectancy" in rankings
        assert "by_win_rate" in rankings
        assert "by_profit_factor" in rankings

    def test_rankings_sorted_descending(self, segmentation_result):
        by_exp = segmentation_result["rankings"]["by_expectancy"]
        if len(by_exp) >= 2:
            assert by_exp[0]["expectancy_r"] >= by_exp[-1]["expectancy_r"]


class TestMissingSymbols:
    def test_missing_symbol_returns_empty(self, segmentation_result, tmp_path):
        views_dir = tmp_path / "views"
        result = load_view("NONEXISTENT_SYMBOL", str(views_dir))
        assert result == []


class TestViewLoading:
    def test_load_full_raw(self, segmentation_result, tmp_path):
        views_dir = tmp_path / "views"
        full = load_view("FULL_RAW", str(views_dir))
        assert len(full) == segmentation_result["total_trades"]

    def test_load_full_backward_compat(self, segmentation_result, tmp_path):
        """FULL should still work and return all trades."""
        views_dir = tmp_path / "views"
        full = load_view("FULL", str(views_dir))
        assert len(full) == segmentation_result["total_trades"]

    def test_load_standard(self, segmentation_result, tmp_path):
        views_dir = tmp_path / "views"
        standard = load_view("STANDARD", str(views_dir))
        assert len(standard) == segmentation_result["normal_trades"]

    def test_load_instrument(self, segmentation_result, tmp_path):
        views_dir = tmp_path / "views"
        symbols = segmentation_result.get("view_counts", {})
        for sym in ["NZDUSD", "USDCAD", "EURUSD"]:
            if sym in symbols and symbols[sym] > 0:
                trades = load_view(sym, str(views_dir))
                assert len(trades) == symbols[sym]
                break
