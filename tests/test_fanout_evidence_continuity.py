"""Evidence-continuity tests for the fan-out execution boundary.

REPAIR 3: trade_truth records for fan-out closed trades must be explicitly
self-attributing (account_id / broker / broker_server) while preserving the
canonical lineage. REPAIR 5: fan-out execution_results carry a real decision
timestamp, canonical-vs-broker SL/TP, the negotiated filling mode, and one
correctly-attributed execution_attempt per account execution that reached
order_send. Local persistence only (conftest redirects every sink).
"""

import json
from pathlib import Path
from types import SimpleNamespace as NS

from core.mt5_symbol_spec import FILLING_FOK, FILLING_IOC
from core.trade_journal import TradeRecord, persist_trade
from core.trade_truth import build_trade_truth


# ─── REPAIR 3: trade_truth account attribution ────────────────────────────────

def _trade_record(account_id, broker, broker_server, broker_symbol):
    return TradeRecord(
        trade_id=f"trade_{account_id}_abc123",
        position_ticket=424242,
        symbol="EURUSD",
        magic=713001,
        pattern_name="PATTERN",
        direction="BUY",
        entry_time=1789000000.0,
        exit_time=1789000600.0,
        duration_seconds=600.0,
        entry_price=1.10002,
        exit_price=1.10102,
        initial_volume=0.1,
        final_volume=0.1,
        realised_pnl=10.0,
        close_reason="manual_close",
        initial_sl=1.09902,
        initial_tp=1.10152,
        recorded_at_utc="2026-09-12T00:00:00Z",
        correlation_id="cor-lineage-1",
        canonical_opportunity_id="opp-lineage-1",
        observation_id="obs-lineage-1",
        decision_id="dec-lineage-1",
        trade_horizon="SCALP",
        account_id=account_id,
        broker=broker,
        broker_server=broker_server,
        broker_symbol=broker_symbol,
    )


def _read_truth_records(symbol_dir):
    records = []
    for f in sorted(Path(symbol_dir).rglob("*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(json.loads(line))
    return records


def test_vantage_closed_trade_truth_carries_account_identity(tmp_path):
    from core.trade_journal import _get_trade_truth_dir
    record = _trade_record("VANTAGE", "Vantage", "Vantage-Demo", "EURUSD.van")
    assert persist_trade(record) is True
    truths = _read_truth_records(_get_trade_truth_dir())
    assert truths, "trade_truth record must be persisted"
    identity = truths[-1]["identity"]
    assert identity["account_id"] == "VANTAGE"
    assert identity["broker"] == "Vantage"
    assert identity["broker_server"] == "Vantage-Demo"
    assert identity["broker_symbol"] == "EURUSD.van"
    assert identity["position_ticket"] == 424242
    # Canonical lineage preserved verbatim alongside the account identity.
    assert identity["correlation_id"] == "cor-lineage-1"
    assert identity["canonical_opportunity_id"] == "opp-lineage-1"


def test_metaquotes_closed_trade_truth_carries_account_identity(tmp_path):
    from core.trade_journal import _get_trade_truth_dir
    record = _trade_record("METAQUOTES", "MetaQuotes", "MetaQuotes-Demo", "EURUSD.mq")
    assert persist_trade(record) is True
    truths = _read_truth_records(_get_trade_truth_dir())
    identity = truths[-1]["identity"]
    assert identity["account_id"] == "METAQUOTES"
    assert identity["broker"] == "MetaQuotes"
    assert identity["broker_server"] == "MetaQuotes-Demo"
    assert identity["correlation_id"] == "cor-lineage-1"


def test_build_trade_truth_identity_fields_are_explicit():
    record = build_trade_truth(
        trade_id="trade_VANTAGE_xyz",
        correlation_id="cor-1",
        canonical_opportunity_id="opp-1",
        symbol="EURUSD",
        account_id="VANTAGE",
        broker="Vantage",
        broker_server="Vantage-Demo",
        broker_symbol="EURUSD.van",
        position_ticket=99,
        entry_fill_price=1.1,
        exit_fill_price=1.101,
        volume_executed=0.1,
        entry_timestamp_broker=1789000000.0,
        exit_timestamp_broker=1789000600.0,
        exit_reason="manual_close",
    )
    assert record["identity"]["account_id"] == "VANTAGE"
    assert record["identity"]["broker"] == "Vantage"
    assert record["identity"]["broker_server"] == "Vantage-Demo"
    assert record["identity"]["position_ticket"] == 99
    assert record["identity"]["canonical_opportunity_id"] == "opp-1"


# === CONTINUE: REPAIR 5 tests appended below ===

# --- REPAIR 5: decision timestamp + execution_attempts coverage -----------------

def _fanout_outcome(*, account_id, broker, broker_server, broker_symbol,
                     decision_id, correlation_id, canonical_opportunity_id,
                     observation_id, lifecycle_side='BUY', ok=True,
                     retcode=10009, executed=True, filling_mode='FOK',
                     broker_sl=1.09902, broker_tp=1.10102, lifecycle_bid=1.1,
                     lifecycle_ask=1.10002, volume=0.05, order=7, deal=7,
                     entry=1.10002, sl=1.09902, tp=1.10102, pattern='PATTERN'):
    from types import SimpleNamespace as _NS
    target = _NS(account_id=account_id, broker=broker, broker_server=broker_server,
                  broker_symbol=broker_symbol, canonical_symbol='EURUSD',
                  canonical_opportunity_id=canonical_opportunity_id,
                  correlation_id=correlation_id, decision_id=decision_id,
                  observation_id=observation_id, account_execution_id='e-1',
                  trade_id='t-1', requested_volume=volume, entry=entry, sl=sl, tp=tp,
                  pattern=pattern, entity_id='ent-1')
    return {'target': target, 'executed': executed, 'status': 'FILLED' if executed else 'BLOCKED',
            'ok': ok, 'retcode': retcode, 'deal': deal, 'order': order,
            'comment': 'ok', 'fill_price': 1.10002, 'lifecycle_side': lifecycle_side,
            'lifecycle_bid': lifecycle_bid, 'lifecycle_ask': lifecycle_ask,
            'volume': volume, 'filling_mode': filling_mode,
            'canonical_sl': sl, 'canonical_tp': tp, 'broker_sl': broker_sl,
            'broker_tp': broker_tp}


def test_fanout_execution_results_carry_real_decision_timestamp(tmp_path):
    from core.persistence.execution_result_writer import persist_execution_result
    persist_execution_result(
        symbol='EURUSD', cycle_id=10, result_ok=True, retcode=10009, deal=21,
        order=22, comment='ok', fill_price=1.10002, side='BUY', volume=0.05,
        entry_reference=1.10002, sl=1.09901, tp=1.10102,
        requested_sl=1.099005, requested_tp=1.101015, pattern='PATTERN',
        decision_id='dec-4', correlation_id='cor-4', entity_id='ent-4',
        observation_id='obs-4', canonical_opportunity_id='opp-4',
        account_id='VANTAGE', broker='Vantage', broker_server='Vantage-Demo',
        position_ticket=22, broker_symbol='EURUSD.van', filling_mode=2,
        decision_ts_utc_ms=1789000000000, slippage=0.0, slippage_measured=True,
        bid_at_execution=1.1, ask_at_execution=1.10002, risk_distance=0.001,
    )
    import json
    from pathlib import Path
    found = [json.loads(l) for p in Path('logs/execution_results/EURUSD').rglob('*.jsonl')
             for l in p.read_text(encoding='utf-8').splitlines() if l.strip()]
    assert found, 'an execution_result record must be persisted'
    r = found[-1]
    assert r['decision_ts_utc_ms'] == 1789000000000, 'real decision timestamp, not 0'
    assert r['filling_mode'] == 2, 'negotiated filling mode persisted'
    assert r['account_id'] == 'VANTAGE'
    assert r['broker'] == 'Vantage'
    assert r['broker_server'] == 'Vantage-Demo'


def test_fanout_execution_attempt_written_for_submitted_order(tmp_path, monkeypatch):
    from core.runtime.fanout_execution import _persist_account_attempts
    import core.persistence.execution_attempts_writer as _a
    written = []
    monkeypatch.setattr(_a, 'persist_execution_attempt', lambda **kw: written.append(kw) or True)
    outcomes = [{'target': _fanout_outcome(account_id='METAQUOTES', broker='MetaQuotes',
                                            broker_server='MetaQuotes-Demo', broker_symbol='EURUSD.mq',
                                            decision_id='dec-2', correlation_id='cor-2',
                                            canonical_opportunity_id='opp-2', observation_id='obs-2',
                                            lifecycle_side='SELL', volume=0.1, order=11, deal=11)['target'],
                 'executed': True, 'status': 'FILLED', 'ok': True, 'retcode': 10009,
                 'deal': 11, 'order': 11, 'comment': 'ok', 'fill_price': 1.10002,
                 'lifecycle_side': 'SELL', 'lifecycle_bid': 1.1, 'lifecycle_ask': 1.10002,
                 'volume': 0.1, 'filling_mode': 'FOK', 'canonical_sl': 1.09902,
                 'canonical_tp': 1.10102, 'broker_sl': 1.09902, 'broker_tp': 1.10102}]
    _persist_account_attempts(outcomes, cycle_id=8)
    assert len(written) == 1
    a = written[0]
    assert a['account_id'] == 'METAQUOTES'
    assert a['broker'] == 'MetaQuotes'
    assert a['broker_server'] == 'MetaQuotes-Demo'
    assert a['action_type'] == 'ENTRY'
    assert a['attempt_number'] == 1
    assert a['retry_reason'] is None
    assert a['side'] == 'SELL'
    assert a['symbol'] == 'EURUSD'
    assert a['decision_id'] == 'dec-2'
    assert a['canonical_opportunity_id'] == 'opp-2'
    assert a['correlation_id'] == 'cor-2'
    assert a['cycle_id'] == 8


def test_fanout_block_prevented_attempt_not_written(tmp_path, monkeypatch):
    from core.runtime.fanout_execution import _persist_account_attempts
    import core.persistence.execution_attempts_writer as _a
    written = []
    monkeypatch.setattr(_a, 'persist_execution_attempt', lambda **kw: written.append(kw) or True)
    outcomes = [
        {'target': _fanout_outcome(account_id='VANTAGE', broker='Vantage',
                                    broker_server='Vantage-Demo', broker_symbol='EURUSD.van',
                                    decision_id='d-a', correlation_id='c-a',
                                    canonical_opportunity_id='o-a', observation_id='obs-a',
                                    executed=True, lifecycle_side='BUY')['target'],
         'executed': True, 'status': 'FILLED', 'ok': True, 'retcode': 10009,
         'deal': 1, 'order': 1, 'comment': 'ok', 'fill_price': 1.10002,
         'lifecycle_side': 'BUY', 'lifecycle_bid': 1.1, 'lifecycle_ask': 1.10002,
         'volume': 0.05, 'filling_mode': 'FOK', 'canonical_sl': 1.09902,
         'canonical_tp': 1.10102, 'broker_sl': 1.09902, 'broker_tp': 1.10102},
        {'target': None, 'executed': False, 'status': 'BLOCKED', 'comment': 'SYMBOL_UNAVAILABLE'},
        {'target': None, 'executed': False, 'status': 'OBSERVED', 'comment': 'EXECUTION_DISABLED'},
    ]
    _persist_account_attempts(outcomes, cycle_id=9)
    assert len(written) == 1
    assert written[0]['account_id'] == 'VANTAGE'


def test_fanout_execution_result_preserves_canonical_and_broker_sl_tp(tmp_path):
    from core.persistence.execution_result_writer import persist_execution_result
    persist_execution_result(
        symbol='EURUSD', cycle_id=10, result_ok=True, retcode=10009, deal=21,
        order=22, comment='ok', fill_price=1.10002, side='BUY', volume=0.05,
        entry_reference=1.10002, sl=1.09901, tp=1.10102,
        requested_sl=1.099005, requested_tp=1.101015, pattern='PATTERN',
        decision_id='dec-4', correlation_id='cor-4', entity_id='ent-4',
        observation_id='obs-4', canonical_opportunity_id='opp-4',
        account_id='VANTAGE', broker='Vantage', broker_server='Vantage-Demo',
        position_ticket=22, broker_symbol='EURUSD.van', filling_mode=2,
        decision_ts_utc_ms=1789000000000, slippage=0.0, slippage_measured=True,
        bid_at_execution=1.1, ask_at_execution=1.10002, risk_distance=0.001,
    )
    import json
    from pathlib import Path
    found = [json.loads(l) for p in Path('logs/execution_results/EURUSD').rglob('*.jsonl')
             for l in p.read_text(encoding='utf-8').splitlines() if l.strip()]
    assert found
    r = found[-1]
    assert r['sl'] == 1.09901
    assert r['tp'] == 1.10102
    assert r['request']['sl'] == 1.099005
    assert r['request']['tp'] == 1.101015
    assert r['filling_mode'] == 2
    assert r['decision_ts_utc_ms'] == 1789000000000
    assert r['account_id'] == 'VANTAGE'
    assert r['broker'] == 'Vantage'
    assert r['broker_server'] == 'Vantage-Demo'



# --- REPAIR 5: decision timestamp + execution_attempts coverage -----------------

def _fanout_outcome(*, account_id, broker, broker_server, broker_symbol,
                     decision_id, correlation_id, canonical_opportunity_id,
                     observation_id, lifecycle_side='BUY', ok=True,
                     retcode=10009, executed=True, filling_mode='FOK',
                     broker_sl=1.09902, broker_tp=1.10102, lifecycle_bid=1.1,
                     lifecycle_ask=1.10002, volume=0.05, order=7, deal=7,
                     entry=1.10002, sl=1.09902, tp=1.10102, pattern='PATTERN'):
    from types import SimpleNamespace as _NS
    target = _NS(account_id=account_id, broker=broker, broker_server=broker_server,
                  broker_symbol=broker_symbol, canonical_symbol='EURUSD',
                  canonical_opportunity_id=canonical_opportunity_id,
                  correlation_id=correlation_id, decision_id=decision_id,
                  observation_id=observation_id, account_execution_id='e-1',
                  trade_id='t-1', requested_volume=volume, entry=entry, sl=sl, tp=tp,
                  pattern=pattern, entity_id='ent-1')
    return {'target': target, 'executed': executed, 'status': 'FILLED' if executed else 'BLOCKED',
            'ok': ok, 'retcode': retcode, 'deal': deal, 'order': order,
            'comment': 'ok', 'fill_price': 1.10002, 'lifecycle_side': lifecycle_side,
            'lifecycle_bid': lifecycle_bid, 'lifecycle_ask': lifecycle_ask,
            'volume': volume, 'filling_mode': filling_mode,
            'canonical_sl': sl, 'canonical_tp': tp, 'broker_sl': broker_sl,
            'broker_tp': broker_tp}


def _read_writer_records(tmp_path, subdir='execution_results'):
    found = []
    for p in Path(tmp_path / subdir).rglob('*.jsonl'):
        found.extend(json.loads(l) for l in p.read_text(encoding='utf-8').splitlines() if l.strip())
    return found


def test_fanout_execution_results_carry_real_decision_timestamp(tmp_path):
    from core.persistence.execution_result_writer import persist_execution_result
    persist_execution_result(
        symbol='EURUSD', cycle_id=10, result_ok=True, retcode=10009, deal=21,
        order=22, comment='ok', fill_price=1.10002, side='BUY', volume=0.05,
        entry_reference=1.10002, sl=1.09901, tp=1.10102,
        requested_sl=1.099005, requested_tp=1.101015, pattern='PATTERN',
        decision_id='dec-4', correlation_id='cor-4', entity_id='ent-4',
        observation_id='obs-4', canonical_opportunity_id='opp-4',
        account_id='VANTAGE', broker='Vantage', broker_server='Vantage-Demo',
        position_ticket=22, broker_symbol='EURUSD.van', filling_mode=2,
        decision_ts_utc_ms=1789000000000, slippage=0.0, slippage_measured=True,
        bid_at_execution=1.1, ask_at_execution=1.10002, risk_distance=0.001,
    )
    r = _read_writer_records(tmp_path)[-1]
    assert r['decision_ts_utc_ms'] == 1789000000000, 'real decision timestamp, not 0'
    assert r['filling_mode'] == 2, 'negotiated filling mode persisted'
    assert r['account_id'] == 'VANTAGE'
    assert r['broker'] == 'Vantage'
    assert r['broker_server'] == 'Vantage-Demo'


def test_fanout_execution_attempt_written_for_submitted_order(tmp_path, monkeypatch):
    from core.runtime.fanout_execution import _persist_account_attempts
    import core.persistence.execution_attempts_writer as _a
    written = []
    monkeypatch.setattr(_a, 'persist_execution_attempt', lambda **kw: written.append(kw) or True)
    outcomes = [{'target': _fanout_outcome(account_id='METAQUOTES', broker='MetaQuotes',
                                            broker_server='MetaQuotes-Demo', broker_symbol='EURUSD.mq',
                                            decision_id='dec-2', correlation_id='cor-2',
                                            canonical_opportunity_id='opp-2', observation_id='obs-2',
                                            lifecycle_side='SELL', volume=0.1, order=11, deal=11)['target'],
                 'executed': True, 'status': 'FILLED', 'ok': True, 'retcode': 10009,
                 'deal': 11, 'order': 11, 'comment': 'ok', 'fill_price': 1.10002,
                 'lifecycle_side': 'SELL', 'lifecycle_bid': 1.1, 'lifecycle_ask': 1.10002,
                 'volume': 0.1, 'filling_mode': 'FOK', 'canonical_sl': 1.09902,
                 'canonical_tp': 1.10102, 'broker_sl': 1.09902, 'broker_tp': 1.10102}]
    _persist_account_attempts(outcomes, cycle_id=8)
    assert len(written) == 1
    a = written[0]
    assert a['account_id'] == 'METAQUOTES'
    assert a['broker'] == 'MetaQuotes'
    assert a['broker_server'] == 'MetaQuotes-Demo'
    assert a['action_type'] == 'ENTRY'
    assert a['attempt_number'] == 1
    assert a['retry_reason'] is None
    assert a['side'] == 'SELL'
    assert a['symbol'] == 'EURUSD'
    assert a['decision_id'] == 'dec-2'
    assert a['canonical_opportunity_id'] == 'opp-2'
    assert a['correlation_id'] == 'cor-2'
    assert a['cycle_id'] == 8


def test_fanout_block_prevented_attempt_not_written(tmp_path, monkeypatch):
    from core.runtime.fanout_execution import _persist_account_attempts
    import core.persistence.execution_attempts_writer as _a
    written = []
    monkeypatch.setattr(_a, 'persist_execution_attempt', lambda **kw: written.append(kw) or True)
    outcomes = [
        {'target': _fanout_outcome(account_id='VANTAGE', broker='Vantage',
                                    broker_server='Vantage-Demo', broker_symbol='EURUSD.van',
                                    decision_id='d-a', correlation_id='c-a',
                                    canonical_opportunity_id='o-a', observation_id='obs-a',
                                    executed=True, lifecycle_side='BUY')['target'],
         'executed': True, 'status': 'FILLED', 'ok': True, 'retcode': 10009,
         'deal': 1, 'order': 1, 'comment': 'ok', 'fill_price': 1.10002,
         'lifecycle_side': 'BUY', 'lifecycle_bid': 1.1, 'lifecycle_ask': 1.10002,
         'volume': 0.05, 'filling_mode': 'FOK', 'canonical_sl': 1.09902,
         'canonical_tp': 1.10102, 'broker_sl': 1.09902, 'broker_tp': 1.10102},
        {'target': None, 'executed': False, 'status': 'BLOCKED', 'comment': 'SYMBOL_UNAVAILABLE'},
        {'target': None, 'executed': False, 'status': 'OBSERVED', 'comment': 'EXECUTION_DISABLED'},
    ]
    _persist_account_attempts(outcomes, cycle_id=9)
    assert len(written) == 1
    assert written[0]['account_id'] == 'VANTAGE'


def test_fanout_execution_result_preserves_canonical_and_broker_sl_tp(tmp_path):
    from core.persistence.execution_result_writer import persist_execution_result
    persist_execution_result(
        symbol='EURUSD', cycle_id=10, result_ok=True, retcode=10009, deal=21,
        order=22, comment='ok', fill_price=1.10002, side='BUY', volume=0.05,
        entry_reference=1.10002, sl=1.09901, tp=1.10102,
        requested_sl=1.099005, requested_tp=1.101015, pattern='PATTERN',
        decision_id='dec-4', correlation_id='cor-4', entity_id='ent-4',
        observation_id='obs-4', canonical_opportunity_id='opp-4',
        account_id='VANTAGE', broker='Vantage', broker_server='Vantage-Demo',
        position_ticket=22, broker_symbol='EURUSD.van', filling_mode=2,
        decision_ts_utc_ms=1789000000000, slippage=0.0, slippage_measured=True,
        bid_at_execution=1.1, ask_at_execution=1.10002, risk_distance=0.001,
    )
    r = _read_writer_records(tmp_path)[-1]
    assert r['sl'] == 1.09901
    assert r['tp'] == 1.10102
    assert r['request']['sl'] == 1.099005
    assert r['request']['tp'] == 1.101015
    assert r['filling_mode'] == 2
    assert r['decision_ts_utc_ms'] == 1789000000000
    assert r['account_id'] == 'VANTAGE'
    assert r['broker'] == 'Vantage'
    assert r['broker_server'] == 'Vantage-Demo'

