"""V10 Research Dataset — Lambda-compatible (no local file dependency)."""
from __future__ import annotations
import json, logging, re
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

class DatasetView(str, Enum):
    FULL = "FULL"
    FX_ONLY = "FX_ONLY"
    INDEX_ONLY = "INDEX_ONLY"
    CFD_ONLY = "CFD_ONLY"
    NORMALISED = "NORMALISED"

_FX_CLASSES = frozenset({"FX_MAJOR", "FX_JPY"})
_FX_SYMBOLS = frozenset({
    "EURUSD", "GBPUSD", "AUDUSD", "NZDUSD", "USDCAD", "USDCHF", "USDJPY",
    "EURJPY", "GBPJPY", "EURGBP", "AUDCAD", "AUDNZD", "NZDCAD",
})
_INDEX_SYMBOLS = frozenset({"US500", "NAS100", "US30", "GER40", "UK100", "JPN225"})

def _classify_instrument(symbol: str) -> str:
    s = symbol.upper().rstrip("_SB").rstrip(".C")
    if s in _FX_SYMBOLS or (len(s) == 6 and s[:3].isalpha() and s[3:].isalpha()):
        return "FX_JPY" if "JPY" in s else "FX_MAJOR"
    for idx in _INDEX_SYMBOLS:
        if idx in s:
            return "INDEX"
    return "COMMODITY"

def load_trades(view: DatasetView = DatasetView.FULL, trades: list[dict] | None = None, base_dir: str | None = None) -> list[dict[str, Any]]:
    """Load and filter trades. Accepts pre-loaded trades for Lambda."""
    if trades is None:
        # Local fallback
        data_file = Path(base_dir or "logs/research_ready_trade_dataset") / "research_ready_trades.jsonl"
        if not data_file.exists():
            return []
        trades = [json.loads(l) for l in data_file.read_text(encoding="utf-8").splitlines() if l.strip()]

    for t in trades:
        if not t.get("instrument_class"):
            t["instrument_class"] = _classify_instrument(t.get("symbol", ""))
        if "realised_r" not in t:
            _compute_r(t)
    return _filter_view(trades, view)

def _compute_r(t: dict) -> None:
    entry = t.get("entry_price", 0)
    sl = t.get("stop_loss", 0)
    exit_price = t.get("exit_price", 0)
    direction = t.get("direction", "")
    risk_distance = abs(entry - sl) if entry > 0 and sl > 0 else 0
    if risk_distance > 0 and exit_price > 0:
        price_move = (exit_price - entry) if direction == "BUY" else (entry - exit_price)
        t["realised_r"] = round(price_move / risk_distance, 4)
    else:
        t["realised_r"] = 0.0

def _filter_view(trades: list[dict], view: DatasetView) -> list[dict]:
    if view == DatasetView.FULL:
        return trades
    elif view == DatasetView.FX_ONLY:
        return [t for t in trades if t.get("instrument_class", "") in _FX_CLASSES]
    elif view == DatasetView.INDEX_ONLY:
        return [t for t in trades if t.get("instrument_class", "") == "INDEX"]
    elif view == DatasetView.CFD_ONLY:
        return [t for t in trades if t.get("instrument_class", "") in ("COMMODITY", "CRYPTO", "UNKNOWN")]
    elif view == DatasetView.NORMALISED:
        return [t for t in trades if t.get("instrument_class", "") in _FX_CLASSES and abs(t.get("realised_r", 0)) <= 5.0]
    return trades
