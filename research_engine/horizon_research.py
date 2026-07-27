"""
Horizon Research Pipeline — Generates research reports from completed trade data.

Integrates with the existing research engine infrastructure:
    - Uses data_access/loaders pattern for trade journal loading
    - Uses reports/generator pattern for report persistence
    - Follows standard report schema (question_id, metrics, conclusion)

Pipeline:
    Trade Journal JSONL → TradeRecord-like dicts → HorizonObservationBuilder
    → HorizonResearchReport → JSON report file (research_reports/)

THIS MODULE DOES NOT:
    - Modify execution behaviour
    - Enable inactive horizons
    - Change trade management
    - Update research contracts automatically
    - Import any execution modules
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# DATA LOADING (follows data_access/loaders.py pattern)
# ═══════════════════════════════════════════════════════════════════════════════

_DEFAULT_LOGS_DIR = "logs"


def _get_project_root() -> Path:
    """Resolve project root from this file's location."""
    return Path(__file__).resolve().parent.parent


def _load_trade_journal_records() -> list[dict[str, Any]]:
    """
    Load all trade journal records from logs/trade_journal/*.jsonl.

    Data source: core/trade_journal.py persist_trade() output.
    Format: one JSON record per line (TradeRecord fields).
    """
    journal_dir = _get_project_root() / _DEFAULT_LOGS_DIR / "trade_journal"
    records: list[dict[str, Any]] = []

    if not journal_dir.exists():
        logger.info("[HORIZON_RESEARCH] trade_journal directory not found: %s", journal_dir)
        return records

    for jsonl_file in sorted(journal_dir.glob("*.jsonl")):
        try:
            with open(jsonl_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            logger.warning("[HORIZON_RESEARCH] error reading %s: %s", jsonl_file.name, e)

    logger.info("[HORIZON_RESEARCH] loaded %d trade journal records", len(records))
    return records


# ═══════════════════════════════════════════════════════════════════════════════
# TRADE RECORD ADAPTER
# ═══════════════════════════════════════════════════════════════════════════════

class _TradeRecordProxy:
    """
    Lightweight adapter from dict → attribute access.

    The observation_builder expects objects with attribute access (like TradeRecord).
    This avoids importing the full TradeRecord dataclass or reconstructing frozen objects.
    """

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        return self._data.get(name, self._get_default(name))

    @staticmethod
    def _get_default(name: str) -> Any:
        """Sensible defaults for missing fields."""
        _defaults = {
            "trade_horizon": "SCALP",
            "direction": "BUY",
            "duration_seconds": 0.0,
            "entry_price": 0.0,
            "exit_price": 0.0,
            "initial_sl": 0.0,
            "initial_tp": 0.0,
            "max_favourable_price": 0.0,
            "close_reason": "unknown",
            "symbol": "UNKNOWN",
            "correlation_id": "",
        }
        return _defaults.get(name, None)


# ═══════════════════════════════════════════════════════════════════════════════
# RESEARCH PIPELINE
# ═══════════════════════════════════════════════════════════════════════════════

def run_horizon_research(
    *,
    min_sample_size: int = 20,
    persist: bool = True,
) -> dict[str, Any]:
    """
    Run the complete horizon research pipeline.

    Steps:
        1. Load completed trades from trade journal.
        2. Group trades by horizon.
        3. Build observations per horizon.
        4. Load matching research contracts.
        5. Generate reports.
        6. Persist results (if enabled).

    Args:
        min_sample_size: Minimum trades for valid assessment per horizon.
        persist: Whether to write report to research_reports/ directory.

    Returns:
        Dict with reports for each horizon + metadata.
    """
    from core.horizon.observation_builder import build_all_horizon_observations
    from core.horizon.research_report import (
        generate_horizon_report,
        OverallStatus,
    )
    from core.horizon.research_contract import get_active_contract

    # ─── 1. LOAD DATA ─────────────────────────────────────────────────
    raw_records = _load_trade_journal_records()
    trades = [_TradeRecordProxy(r) for r in raw_records]

    # ─── 2+3. BUILD OBSERVATIONS ──────────────────────────────────────
    observations = build_all_horizon_observations(trades)

    # ─── 4+5. GENERATE REPORTS ────────────────────────────────────────
    reports: dict[str, Any] = {}
    for horizon in ("SCALP", "INTRADAY", "EXTENDED"):
        contract = get_active_contract(horizon)
        obs = observations[horizon]

        if contract is None:
            reports[horizon] = {
                "horizon": horizon,
                "status": "NO_CONTRACT",
                "sample_size": 0,
            }
            continue

        report = generate_horizon_report(
            contract, obs, min_sample_size=min_sample_size
        )
        reports[horizon] = report.to_dict()

    # ─── 6. METADATA ──────────────────────────────────────────────────
    _now = datetime.now(timezone.utc)
    _total_trades = len(raw_records)

    # Determine analysis period from trade timestamps
    _period_start = ""
    _period_end = ""
    if raw_records:
        _times = [r.get("entry_time", 0) for r in raw_records if r.get("entry_time")]
        if _times:
            _min_t = min(_times)
            _max_t = max(_times)
            _period_start = datetime.fromtimestamp(_min_t, tz=timezone.utc).strftime("%Y-%m-%d")
            _period_end = datetime.fromtimestamp(_max_t, tz=timezone.utc).strftime("%Y-%m-%d")

    result = {
        "experiment_name": "horizon_research",
        "question_id": "HORIZON_PERFORMANCE",
        "question": "How does each horizon's observed behaviour compare to research contract expectations?",
        "generated_at": _now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "data_source": "trade_journal",
        "data_path": "logs/trade_journal/*.jsonl",
        "total_trades_loaded": _total_trades,
        "analysis_period": f"{_period_start} to {_period_end}" if _period_start else "no_data",
        "min_sample_size": min_sample_size,
        "horizons": reports,
    }

    # ─── 7. PERSIST ───────────────────────────────────────────────────
    if persist:
        _persist_report(result, _now)

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# PERSISTENCE (follows reports/generator.py pattern)
# ═══════════════════════════════════════════════════════════════════════════════

def _persist_report(report: dict[str, Any], timestamp: datetime) -> Path | None:
    """
    Write horizon research report to research_reports/ directory.

    Follows the same pattern as research_engine/reports/generator.py.
    """
    try:
        reports_dir = _get_project_root() / "research_reports"
        reports_dir.mkdir(parents=True, exist_ok=True)

        filename = f"horizon_research_{timestamp.strftime('%Y%m%d_%H%M%S')}.json"
        filepath = reports_dir / filename

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False, default=str)

        logger.info("[HORIZON_RESEARCH] report persisted: %s", filepath.name)
        return filepath
    except Exception as e:
        logger.warning("[HORIZON_RESEARCH] failed to persist report: %s", e)
        return None
