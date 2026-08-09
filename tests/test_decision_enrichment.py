"""Tests for V10 Decision Trace Enrichment Layer."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from research_engine.v10.decision_enrichment import enrich_trades


@pytest.fixture(scope="module")
def enrichment_result(tmp_path_factory):
    """Run enrichment once to temp output."""
    tmp = tmp_path_factory.mktemp("enrich")
    src = Path("logs/research_ready_trade_dataset/research_ready_trades.jsonl")
    if not src.exists():
        pytest.skip("Research-ready dataset not available")
    return enrich_trades(
        source_file=str(src),
        output_file=str(tmp / "enriched.jsonl"),
        reports_dir=str(tmp / "reports"),
    )


@pytest.fixture(scope="module")
def enriched_trades(enrichment_result, tmp_path_factory):
    """Load the enriched trades file."""
    tmp = tmp_path_factory.getbasetemp() / "enrich0"
    out_file = tmp / "enriched.jsonl"
    trades = []
    for line in out_file.read_text(encoding="utf-8").splitlines():
        if line.strip():
            trades.append(json.loads(line))
    return trades


@pytest.fixture(scope="module")
def source_trades():
    """Load original source trades."""
    src = Path("logs/research_ready_trade_dataset/research_ready_trades.jsonl")
    if not src.exists():
        pytest.skip("Research-ready dataset not available")
    trades = []
    for line in src.read_text(encoding="utf-8").splitlines():
        if line.strip():
            trades.append(json.loads(line))
    return trades


class TestNoDuplication:
    def test_enriched_count_equals_source(self, enrichment_result, source_trades):
        assert enrichment_result["total_trades"] == len(source_trades)

    def test_no_duplicate_trade_ids(self, enriched_trades):
        ids = [t["trade_id"] for t in enriched_trades]
        assert len(ids) == len(set(ids))


class TestNoTradeLoss:
    def test_all_source_trades_in_output(self, enriched_trades, source_trades):
        source_ids = {t["trade_id"] for t in source_trades}
        enriched_ids = {t["trade_id"] for t in enriched_trades}
        assert source_ids == enriched_ids


class TestExistingFieldsPreserved:
    def test_core_fields_unchanged(self, enriched_trades, source_trades):
        """All original fields must remain with same values."""
        core_fields = [
            "trade_id", "position_ticket", "symbol", "direction",
            "entry_time", "exit_time", "entry_price", "exit_price",
            "stop_loss", "broker_pnl", "realised_r",
        ]
        source_by_id = {t["trade_id"]: t for t in source_trades}
        for et in enriched_trades:
            src = source_by_id[et["trade_id"]]
            for field in core_fields:
                assert et.get(field) == src.get(field), \
                    f"Field {field} changed for {et['trade_id']}: {src.get(field)} -> {et.get(field)}"


class TestEnrichmentRepeatable:
    def test_second_run_produces_same_result(self, tmp_path):
        """Running enrichment twice should produce identical output."""
        src = Path("logs/research_ready_trade_dataset/research_ready_trades.jsonl")
        if not src.exists():
            pytest.skip("Research-ready dataset not available")

        r1 = enrich_trades(
            source_file=str(src),
            output_file=str(tmp_path / "run1.jsonl"),
            reports_dir=str(tmp_path / "reports1"),
        )
        r2 = enrich_trades(
            source_file=str(src),
            output_file=str(tmp_path / "run2.jsonl"),
            reports_dir=str(tmp_path / "reports2"),
        )
        assert r1["matched"] == r2["matched"]
        assert r1["unmatched"] == r2["unmatched"]
        assert r1["match_methods"] == r2["match_methods"]


class TestEnrichmentContent:
    def test_matched_trades_have_dt_fields(self, enriched_trades):
        """Matched trades should have decision trace fields populated."""
        matched = [t for t in enriched_trades if t.get("dt_matched")]
        assert len(matched) > 0
        # Check a sample of enriched fields
        for t in matched[:5]:
            assert t.get("dt_match_method") != "unmatched"
            # At least strategy or score should be present
            has_strategy = t.get("dt_strategy") or t.get("dt_v10_strategy_family")
            has_score = t.get("dt_score_strategy") is not None
            assert has_strategy or has_score, f"Trade {t['trade_id']} matched but no enrichment data"
