"""Phase H: account-aware persistence verification. No live MT5, S3 or messages."""
import json
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch, MagicMock
import pytest

from core.persistence.execution_result_writer import persist_execution_result
from core.persistence.execution_attempts_writer import persist_execution_attempt
from core.persistence.management_actions_writer import persist_management_action
from core.protection_verification import ProtectionVerificationResult, verify_protection
from core.risk_deviation import compute_risk_deviation, persist_risk_deviation
from core.trade_journal import build_trade_record, TradeRecord
from core.trade_truth import build_trade_truth


@pytest.fixture
def tmp_dir(tmp_path):
    return str(tmp_path)


def _owner(**overrides):
    from core.position_ownership import PositionOwnership
    base = dict(
        account_id="METAQUOTES", broker="MetaQuotes", broker_server="MetaQuotes-Demo",
        position_ticket=12345, order_ticket=66, deal_ticket=777,
        canonical_symbol="EURUSD", broker_symbol="EURUSD",
        canonical_opportunity_id="opp-1", correlation_id="cor-1", decision_id="dec-1",
        account_execution_id="exec-1", trade_id="trade-1",
    )
    base.update(overrides)
    return PositionOwnership(**base)


def test_execution_result_persists_account_identity(tmp_dir):
    with patch("core.persistence.execution_result_writer._LOCAL_DIR", tmp_dir), \
         patch("core.persistence.execution_result_writer._write_s3"):
        persist_execution_result(
            symbol="EURUSD", cycle_id=1, result_ok=True, retcode=10009,
            deal=777, order=66, comment="ok", fill_price=1.1,
            side="BUY", volume=0.1, sl=1.09, tp=1.12,
            decision_id="dec-1", correlation_id="cor-1",
            canonical_opportunity_id="opp-1",
            account_id="METAQUOTES", broker="MetaQuotes",
            broker_server="MetaQuotes-Demo", position_ticket=12345,
            broker_symbol="EURUSD",
        )
    files = list(Path(tmp_dir).rglob("*.jsonl"))
    assert len(files) == 1
    record = json.loads(files[0].read_text().strip())
    assert record["account_id"] == "METAQUOTES"
    assert record["broker"] == "MetaQuotes"
    assert record["broker_server"] == "MetaQuotes-Demo"
    assert record["position_ticket"] == 12345
    assert record["broker_symbol"] == "EURUSD"


def test_execution_attempt_persists_account_identity(tmp_dir):
    with patch("core.persistence.execution_attempts_writer._LOCAL_DIR", tmp_dir), \
         patch("core.persistence.execution_attempts_writer._write_s3"):
        persist_execution_attempt(
            attempt_id="att-1", symbol="EURUSD", cycle_id=1,
            action_type="CLOSE", trade_id="trade-1",
            decision_id="dec-1", correlation_id="cor-1",
            canonical_opportunity_id="opp-1",
            account_id="METAQUOTES", broker="MetaQuotes",
            broker_server="MetaQuotes-Demo", position_ticket=12345,
            broker_symbol="EURUSD", broker_ok=True, retcode=10009,
        )
    files = list(Path(tmp_dir).rglob("*.jsonl"))
    assert len(files) == 1
    record = json.loads(files[0].read_text().strip())
    assert record["account_id"] == "METAQUOTES"
    assert record["broker"] == "MetaQuotes"
    assert record["broker_server"] == "MetaQuotes-Demo"
    assert record["position_ticket"] == 12345


def test_management_action_persists_account_identity(tmp_dir):
    with patch("core.persistence.management_actions_writer._LOCAL_DIR", tmp_dir), \
         patch("core.persistence.management_actions_writer._write_s3"):
        persist_management_action(
            management_action_id="ma-1", symbol="EURUSD",
            action_type="CLOSE", trade_id="trade-1",
            decision_id="dec-1", correlation_id="cor-1",
            canonical_opportunity_id="opp-1",
            account_id="METAQUOTES", broker="MetaQuotes",
            broker_server="MetaQuotes-Demo", position_ticket=12345,
            broker_symbol="EURUSD",
        )
    files = list(Path(tmp_dir).rglob("*.jsonl"))
    assert len(files) == 1
    record = json.loads(files[0].read_text().strip())
    assert record["account_id"] == "METAQUOTES"
    assert record["broker"] == "MetaQuotes"
    assert record["position_ticket"] == 12345


def test_same_ticket_two_accounts_distinct(tmp_path):
    dir1 = str(tmp_path / "mq")
    dir2 = str(tmp_path / "van")
    with patch("core.persistence.execution_result_writer._LOCAL_DIR", dir1), \
         patch("core.persistence.execution_result_writer._write_s3"):
        persist_execution_result(
            symbol="EURUSD", cycle_id=1, result_ok=True, retcode=10009,
            deal=777, order=66, comment="ok",
            account_id="METAQUOTES", broker="MetaQuotes",
            broker_server="MetaQuotes-Demo", position_ticket=12345,
        )
    with patch("core.persistence.execution_result_writer._LOCAL_DIR", dir2), \
         patch("core.persistence.execution_result_writer._write_s3"):
        persist_execution_result(
            symbol="EURUSD", cycle_id=1, result_ok=True, retcode=10009,
            deal=888, order=77, comment="ok",
            account_id="VANTAGE", broker="Vantage",
            broker_server="Vantage-Live", position_ticket=12345,
            broker_symbol="EURUSD.x",
        )
    rec1 = json.loads(Path(dir1).rglob("*.jsonl").__next__().read_text().strip())
    rec2 = json.loads(Path(dir2).rglob("*.jsonl").__next__().read_text().strip())
    assert rec1["account_id"] == "METAQUOTES"
    assert rec2["account_id"] == "VANTAGE"
    assert rec1["position_ticket"] == rec2["position_ticket"] == 12345
    assert rec1["broker"] != rec2["broker"]


def test_trade_truth_account_identity_in_record():
    record = build_trade_truth(
        trade_id="trade-1", correlation_id="cor-1", symbol="EURUSD",
        account_id="METAQUOTES", broker="MetaQuotes",
        broker_server="MetaQuotes-Demo", position_ticket=12345,
        broker_symbol="EURUSD",
        entry_fill_price=1.1, exit_fill_price=1.12,
        volume_executed=0.1,
    )
    assert record["identity"]["account_id"] == "METAQUOTES"
    assert record["identity"]["broker"] == "MetaQuotes"
    assert record["identity"]["broker_server"] == "MetaQuotes-Demo"
    assert record["identity"]["position_ticket"] == 12345
    assert record["identity"]["broker_symbol"] == "EURUSD"


def test_trade_record_account_fields_populated():
    from core.trade_management.position import Position, PositionStatus
    from strategy.signals import Side
    owner = _owner()
    pos = Position(
        position_id='["METAQUOTES",12345]', symbol="EURUSD", side=Side.BUY,
        magic=713001, entry_price=1.1, initial_sl=1.09, initial_tp=1.12,
        stop_loss=1.09, take_profit=1.12, volume=0.1, open_time=1000.0,
        status=PositionStatus.CLOSED, mt5_ticket=12345, ownership=owner,
    )
    record = build_trade_record(
        position=pos, exit_price=1.12, exit_time=2000.0,
        close_reason="take_profit",
    )
    assert record.account_id == "METAQUOTES"
    assert record.broker == "MetaQuotes"
    assert record.broker_server == "MetaQuotes-Demo"
    assert record.broker_symbol == "EURUSD"
    assert record.position_ticket == 12345


def test_protection_audit_includes_ownership():
    result = ProtectionVerificationResult(
        symbol="EURUSD", position_ticket=12345, correlation_id="cor-1",
        requested_sl=1.09, requested_tp=1.12,
        broker_confirmed_sl=1.09, broker_confirmed_tp=1.12,
        protection_status="VERIFIED", protection_failure_reason="",
        verification_timestamp_utc="2026-09-10T00:00:00.000Z",
        verification_latency_ms=10, attempts=1,
        correction_attempted=False, correction_success=False, correction_detail="",
        account_id="METAQUOTES", broker="MetaQuotes",
        broker_server="MetaQuotes-Demo",
    )
    assert result.account_id == "METAQUOTES"
    assert result.broker == "MetaQuotes"
    assert result.broker_server == "MetaQuotes-Demo"
    assert result.position_ticket == 12345


def test_risk_deviation_includes_account():
    result = compute_risk_deviation(
        trade_id="trade-1", symbol="EURUSD", correlation_id="cor-1",
        direction="BUY", entry_price=1.1, exit_price=1.12, initial_sl=1.09,
        account_id="METAQUOTES", broker="MetaQuotes",
        broker_server="MetaQuotes-Demo",
    )
    assert result.account_id == "METAQUOTES"
    assert result.broker == "MetaQuotes"
    assert result.broker_server == "MetaQuotes-Demo"


def test_all_versions_remain_v1():
    from core.production_data_contract import current_schema
    for dataset in ["execution_results", "execution_attempts", "execution_context",
                    "management_actions", "trade_journal", "trade_truth",
                    "protection_audit", "risk_deviation"]:
        version = current_schema(dataset)
        assert version.endswith("_v1"), f"{dataset} version is {version}, expected v1"


def test_metaquotes_only_compatibility():
    record = build_trade_truth(
        trade_id="trade-1", correlation_id="cor-1", symbol="EURUSD",
        entry_fill_price=1.1, exit_fill_price=1.12, volume_executed=0.1,
    )
    assert record["identity"]["account_id"] == ""
    assert record["identity"]["broker"] == ""
