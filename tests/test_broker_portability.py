from types import SimpleNamespace
from unittest.mock import patch

import pytest

from core.mt5_symbol_spec import MT5SymbolSpec, validate_stops, validate_volume
from core.symbol_resolver import (
    broker_symbol_for,
    clear_resolved_symbols,
    resolve_broker_symbol,
)
from execution.mt5_execution import _validate_order
from risk.models import OrderIntent
from strategy.signals import Side
from data.mt5_data import MT5DataFeed


def _symbols(*names):
    return [SimpleNamespace(name=name) for name in names]


@pytest.fixture(autouse=True)
def _clear_resolution_cache():
    clear_resolved_symbols()
    yield
    clear_resolved_symbols()


def test_exact_canonical_symbol_resolution():
    with patch("core.symbol_resolver.mt5.symbols_get", return_value=_symbols("EURUSD")), \
         patch("core.symbol_resolver.mt5.symbol_select", return_value=True):
        assert resolve_broker_symbol("EURUSD", select=False) == "EURUSD"


def test_nas100_resolves_to_ustec_alias():
    with patch("core.symbol_resolver.mt5.symbols_get", return_value=_symbols("USTEC")):
        assert resolve_broker_symbol(
            "NAS100", aliases={"NAS100": ("USTEC", "USTECH100M")}, select=False
        ) == "USTEC"
        assert broker_symbol_for("NAS100") == "USTEC"


def test_ordered_alias_preference_is_deterministic():
    with patch("core.symbol_resolver.mt5.symbols_get", return_value=_symbols("USTECH100M", "USTEC")):
        assert resolve_broker_symbol(
            "NAS100", aliases={"NAS100": ("USTEC", "USTECH100M")}, select=False
        ) == "USTEC"


def test_unordered_multiple_aliases_are_rejected_as_ambiguous():
    with patch("core.symbol_resolver.mt5.symbols_get", return_value=_symbols("USTECH100M", "USTEC")):
        with pytest.raises(ValueError, match="Ambiguous alias"):
            resolve_broker_symbol(
                "NAS100", aliases={"NAS100": {"USTEC", "USTECH100M"}}, select=False
            )


def test_missing_symbol_rejected():
    with patch("core.symbol_resolver.mt5.symbols_get", return_value=_symbols("EURUSD")):
        with pytest.raises(ValueError, match="No MT5 symbol found"):
            resolve_broker_symbol("NAS100", aliases={}, select=False)


def _info(**overrides):
    values = dict(
        visible=True, trade_mode=4, digits=5, point=0.00001,
        volume_min=0.01, volume_max=500.0, volume_step=0.01,
        trade_stops_level=0, trade_freeze_level=0, filling_mode=1,
        trade_exemode=2, trade_contract_size=100000.0,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def test_disabled_trade_mode_rejected():
    with patch("execution.mt5_execution.mt5_call", return_value=_info(trade_mode=0)):
        assert _validate_order("US500", 0.10) == (False, "SYMBOL_NOT_TRADEABLE")


def test_volume_below_min_rejected_without_upward_normalization():
    with patch("execution.mt5_execution.mt5_call", return_value=_info(volume_min=0.1, volume_step=0.1)):
        assert _validate_order("US500", 0.01) == (False, "VOLUME_BELOW_MIN")


def test_valid_fx_volume_accepted():
    with patch("execution.mt5_execution.mt5_call", return_value=_info()):
        assert _validate_order("EURUSD", 0.01) == (True, "")


@pytest.mark.parametrize(
    ("digits", "raw", "expected"),
    [(5, 1.2345678, 1.23457), (3, 154.7656, 154.766), (2, 2345.678, 2345.68)],
)
def test_price_normalization_by_broker_digits(digits, raw, expected):
    spec = MT5SymbolSpec.from_info("TEST", _info(digits=digits))
    assert spec.normalize_price(raw) == expected


def test_zero_stops_level_invents_no_restriction():
    spec = MT5SymbolSpec.from_info("EURUSD", _info(trade_stops_level=0))
    assert validate_stops(spec, market_price=1.1, sl=1.099999, tp=1.100001) is None


def test_nonzero_stops_level_rejects_close_sl_and_tp():
    spec = MT5SymbolSpec.from_info(
        "EURUSD", _info(point=0.00001, trade_stops_level=20)
    )
    assert validate_stops(spec, market_price=1.1, sl=1.0999, tp=1.101) == "SL_TOO_CLOSE"
    assert validate_stops(spec, market_price=1.1, sl=1.099, tp=1.1001) == "TP_TOO_CLOSE"


def test_canonical_intent_identity_survives_broker_resolution():
    with patch("core.symbol_resolver.mt5.symbols_get", return_value=_symbols("USTEC")):
        resolve_broker_symbol("NAS100", aliases={"NAS100": ("USTEC",)}, select=False)
    intent = OrderIntent(
        symbol="NAS100", side=Side.BUY, volume=0.1,
        entry_reference=20000.0, sl=19990.0, tp=20020.0,
    )
    assert intent.symbol == "NAS100"
    assert broker_symbol_for(intent.symbol) == "USTEC"


def test_canonical_identity_with_broker_symbol_at_mt5_tick_boundary():
    feed = MT5DataFeed("NAS100")
    tick = SimpleNamespace(bid=20000.0, ask=20001.0, time=1788794253)
    with patch("data.mt5_data.mt5.symbols_get", return_value=_symbols("USTEC")), \
         patch("core.symbol_resolver.mt5.symbols_get", return_value=_symbols("USTEC")), \
         patch("core.symbol_resolver.mt5.symbol_select", return_value=True), \
         patch("data.mt5_data.mt5_call", return_value=tick) as mt5_call:
        assert feed.resolve_symbol() == "USTEC"
        assert feed.last_tick("NAS100")[:2] == (20000.0, 20001.0)
    assert mt5_call.call_args.args[1] == "USTEC"


def test_v1_version_constants_remain_unchanged():
    from core import config
    from core.production_data_contract import DATA_CONTRACT_VERSION

    assert DATA_CONTRACT_VERSION == "production_v1"
    assert config.CANONICAL_SYMBOLS == [
        "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD",
        "AUDUSD", "NZDUSD", "NAS100", "US500", "XAUUSD",
    ]
