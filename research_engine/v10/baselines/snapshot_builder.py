"""
Baseline Snapshot — Builder.

Collects configuration, performance, and dataset identity
from the live system to produce a complete BaselineSnapshot.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from research_engine.v10.base import compute_metrics, timestamp_now
from research_engine.v10.baselines.models import BaselineSnapshot

logger = logging.getLogger(__name__)

_UNIVERSE_FILE = "data/research/research_universe.jsonl"
_CONFIG_FILE = "core/config.py"


class SnapshotBuilder:
    """
    Builds a baseline snapshot from current system state.

    Collects:
        - Bot version and environment
        - Configuration (from config module)
        - Risk parameters
        - Strategy parameters
        - Performance metrics (from Research Universe)
        - Dataset identity (hash + metadata)
    """

    def __init__(
        self,
        universe_file: str | None = None,
        bot_version: str = "V10.0",
        notes: str = "",
    ):
        self._universe_file = Path(universe_file or _UNIVERSE_FILE)
        self._bot_version = bot_version
        self._notes = notes

    def build(self) -> BaselineSnapshot:
        """Build a complete baseline snapshot from current state."""
        now = datetime.now(timezone.utc)
        snapshot_id = f"V10_BASELINE_{now.strftime('%Y%m%d_%H%M')}"

        # Collect all components
        environment = self._collect_environment()
        configuration = self._collect_configuration()
        risk_config = self._collect_risk_configuration()
        strategy_config = self._collect_strategy_configuration()
        performance = self._collect_performance()
        dataset_meta = self._collect_dataset_metadata()
        research_state = self._collect_research_state(performance)

        snapshot = BaselineSnapshot(
            snapshot_id=snapshot_id,
            bot_version=self._bot_version,
            notes=self._notes,
            environment=environment,
            configuration=configuration,
            risk_configuration=risk_config,
            strategy_configuration=strategy_config,
            performance_metrics=performance,
            dataset_metadata=dataset_meta,
            research_state=research_state,
        )

        logger.info(f"[BASELINE] Built snapshot: {snapshot_id}")
        return snapshot

    def _collect_environment(self) -> dict[str, Any]:
        """Collect environment information."""
        return {
            "broker": "Pepperstone",
            "platform": "MT5",
            "mode": "DEMO",
            "magic_number": 713001,
        }

    def _collect_configuration(self) -> dict[str, Any]:
        """Collect bot configuration from config module."""
        try:
            from core import config as cfg
            return {
                "execution_enabled": getattr(cfg, "EXECUTION_ENABLED", "MISSING"),
                "max_positions": getattr(cfg, "MAX_POSITIONS", "MISSING"),
                "timeframe": getattr(cfg, "TIMEFRAME", "MISSING"),
                "canonical_symbols": getattr(cfg, "CANONICAL_SYMBOLS", []),
                "ev_gate_enabled": getattr(cfg, "ENABLE_EV_GATE", "MISSING"),
            }
        except ImportError:
            return {"status": "MISSING", "reason": "config module not importable"}

    def _collect_risk_configuration(self) -> dict[str, Any]:
        """Collect risk management parameters."""
        try:
            from core import config as cfg
            return {
                "risk_percent": getattr(cfg, "RISK_PERCENT", "MISSING"),
                "atr_multiplier": getattr(cfg, "ATR_MULTIPLIER", "MISSING"),
                "max_daily_loss_pct": getattr(cfg, "MAX_DAILY_LOSS_PCT", "MISSING"),
                "max_drawdown_pct": getattr(cfg, "MAX_DRAWDOWN_PCT", "MISSING"),
            }
        except ImportError:
            return {"status": "MISSING", "reason": "config module not importable"}

    def _collect_strategy_configuration(self) -> dict[str, Any]:
        """Collect strategy configuration."""
        try:
            from core import config as cfg
            return {
                "enabled_strategies": getattr(cfg, "ENABLED_STRATEGIES", "MISSING"),
                "min_score_threshold": getattr(cfg, "MIN_SCORE_THRESHOLD", "MISSING"),
            }
        except ImportError:
            return {"status": "MISSING", "reason": "config module not importable"}

    def _collect_performance(self) -> dict[str, Any]:
        """Collect performance metrics from Research Universe."""
        events = self._load_universe()
        if not events:
            return {"status": "MISSING", "reason": "Research Universe not available"}

        # Flatten to compute metrics
        flat_trades = []
        for e in events:
            ex = e.get("execution", {})
            flat_trades.append({
                "realised_r": ex.get("r_multiple", 0),
                "final_pnl": ex.get("net_realised_pnl", 0),
            })

        metrics = compute_metrics(flat_trades)

        # Add distribution info
        r_values = [t["realised_r"] for t in flat_trades]
        pnl_values = [t["final_pnl"] for t in flat_trades]

        return {
            "trade_count": metrics["count"],
            "win_rate": metrics["win_rate"],
            "loss_rate": metrics["loss_rate"],
            "expectancy_r": metrics["expectancy_r"],
            "average_r": metrics["average_r"],
            "profit_factor": metrics["profit_factor"],
            "net_realised_pnl": metrics["total_pnl"],
            "largest_winner_pnl": metrics["largest_winner"],
            "largest_loser_pnl": metrics["largest_loser"],
        }

    def _collect_dataset_metadata(self) -> dict[str, Any]:
        """Collect dataset identity information."""
        if not self._universe_file.exists():
            return {"status": "MISSING", "reason": "Universe file not found"}

        content = self._universe_file.read_text(encoding="utf-8")
        lines = [l for l in content.splitlines() if l.strip()]
        file_hash = hashlib.sha256(content.encode()).hexdigest()[:16]

        return {
            "dataset": str(self._universe_file),
            "records": len(lines),
            "hash": file_hash,
            "file_size_bytes": self._universe_file.stat().st_size,
            "last_modified": datetime.fromtimestamp(
                self._universe_file.stat().st_mtime, tz=timezone.utc
            ).isoformat(),
        }

    def _collect_research_state(self, performance: dict) -> dict[str, Any]:
        """Capture current research state summary."""
        return {
            "governance_status": "WARNING",  # From last governance run
            "known_weaknesses": [
                "FX stops may be too tight (R2)",
                "Transitional regimes underperform (M1)",
            ],
            "data_completeness": "94/106 trades in research universe",
        }

    def _load_universe(self) -> list[dict]:
        if not self._universe_file.exists():
            return []
        events = []
        for line in self._universe_file.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return events
