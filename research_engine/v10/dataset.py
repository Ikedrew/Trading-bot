"""
V10 Research Dataset — Reusable data loading with instrument views.

Provides filtered access to the research-ready trade dataset.
Builds on core/research_anomaly.py for anomaly classification.

Views:
    FULL       — all validated trades
    FX_ONLY    — FX_MAJOR + FX_JPY instruments
    INDEX_ONLY — INDEX instruments
    CFD_ONLY   — COMMODITY + CRYPTO + other non-FX/non-INDEX
    NORMALISED — anomaly_status == NORMAL (no extremes, FX only)
"""

from __future__ import annotations

import json
import logging
import re
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# The research-ready trade dataset is a DERIVED research artifact (computed
# offline from source datasets), persisted to and read from S3 as the single
# source of truth. Not a production-contract runtime dataset — rebuildable.
_RESEARCH_READY_ARTIFACT = "research_ready_trades"
_DECISION_TRACE_DATASET = "decision_trace"


class DatasetView(str, Enum):
    FULL = "FULL"
    FX_ONLY = "FX_ONLY"
    INDEX_ONLY = "INDEX_ONLY"
    CFD_ONLY = "CFD_ONLY"
    NORMALISED = "NORMALISED"


# Instrument classification (self-contained — no heavy imports)
_FX_CLASSES = frozenset({"FX_MAJOR", "FX_JPY"})
_INDEX_CLASS = "INDEX"
_CFD_CLASSES = frozenset({"COMMODITY", "CRYPTO", "UNKNOWN"})

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


def load_trades(view: DatasetView = DatasetView.FULL, base_dir: str | None = None) -> list[dict[str, Any]]:
    """
    Load research-ready trades with optional instrument filtering.

    This is the primary data access function for all V10 research experiments.

    Source of truth: S3 research artifact ``research_ready_trades`` (a derived,
    rebuildable dataset), read via the shared S3 access layer. Local logs are not
    consulted. An empty result means the artifact is absent in S3 (a real gap).

    Args:
        view: Which instrument subset to return
        base_dir: Deprecated/ignored — retained for signature compatibility.

    Returns:
        List of trade dicts with realised_r computed
    """
    from research_engine.data_access.s3_source import get_default_source

    trades = [dict(t) for t in get_default_source().read_artifact(_RESEARCH_READY_ARTIFACT)]
    if not trades:
        logger.warning("[V10_RESEARCH] research-ready artifact empty/absent in S3")
        return []

    # Ensure instrument_class and realised_r are present
    for t in trades:
        if not t.get("instrument_class"):
            t["instrument_class"] = _classify_instrument(t.get("symbol", ""))
        if "realised_r" not in t:
            _compute_r(t)

    # Apply view filter
    return _filter_view(trades, view)


def _compute_r(t: dict) -> None:
    """Compute realised R-multiple for a trade."""
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
    """Filter trades by instrument view."""
    if view == DatasetView.FULL:
        return trades
    elif view == DatasetView.FX_ONLY:
        return [t for t in trades if t.get("instrument_class", "") in _FX_CLASSES]
    elif view == DatasetView.INDEX_ONLY:
        return [t for t in trades if t.get("instrument_class", "") == _INDEX_CLASS]
    elif view == DatasetView.CFD_ONLY:
        return [t for t in trades if t.get("instrument_class", "") in _CFD_CLASSES]
    elif view == DatasetView.NORMALISED:
        # FX only + no extreme R (|R| <= 5)
        return [t for t in trades
                if t.get("instrument_class", "") in _FX_CLASSES
                and abs(t.get("realised_r", 0)) <= 5.0]
    return trades


def enrich_with_decision_trace(trades: list[dict], trace_dir: str | None = None) -> int:
    """
    Enrich trades with component scores from decision traces.

    Source: S3 dataset ``decision_trace`` via the shared access layer.
    Modifies trades in-place. Returns count of successfully enriched trades.
    """
    from research_engine.data_access.s3_source import get_default_source

    # Build index of EXECUTE decisions from S3 decision_trace.
    dt_by_key: dict[str, dict] = {}
    for d in get_default_source().read_dataset(_DECISION_TRACE_DATASET):
        if d.get("action") == "EXECUTE":
            key = f"{d.get('symbol', '')}_{d.get('cycle_id', '')}"
            dt_by_key[key] = d
            eid = d.get("entity_id", "")
            if eid:
                dt_by_key[eid] = d

    enriched = 0
    for t in trades:
        cor_id = t.get("correlation_id", "")
        symbol = t.get("symbol", "")
        decision = None

        # Remediation Stage 8: current-epoch lineage uses the explicit
        # canonical_opportunity_id field — NO regex reconstruction.
        canonical = (
            (t.get("identity") or {}).get("canonical_opportunity_id")
            or t.get("canonical_opportunity_id", "")
        )
        if canonical and canonical in dt_by_key:
            decision = dt_by_key[canonical]

        # Historical fallback (read-only compat for pre-canonical records)
        if not decision:
            match = re.match(r"COR-\d{8}-(\d+)-", cor_id)
            if match:
                decision = dt_by_key.get(f"{symbol}_{int(match.group(1))}")
        if not decision:
            entry_time = t.get("entry_time", 0)
            if entry_time and symbol:
                decision = dt_by_key.get(f"{symbol}_{int(entry_time)}")

        if decision:
            enriched += 1
            t["dt_score"] = decision.get("score_strategy") or decision.get("score_neutral") or 0
            t["dt_components"] = decision.get("components") or {}
            t["dt_ev"] = decision.get("ev") or 0
            t["dt_p_success"] = decision.get("p_success") or 0
            t["dt_confirmation"] = decision.get("confirmation_score") or 0
            t["dt_regime"] = decision.get("regime", "")
        else:
            t["dt_score"] = t.get("score") or 0
            t["dt_components"] = {}
            t["dt_ev"] = t.get("ev") or 0
            t["dt_p_success"] = 0
            t["dt_confirmation"] = 0
            t["dt_regime"] = t.get("regime", "")

    return enriched
