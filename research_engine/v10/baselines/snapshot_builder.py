"""
Baseline Snapshot — Builder.

Collects configuration, performance, and dataset identity
from the live system to produce a complete BaselineSnapshot.
"""

from __future__ import annotations

import hashlib
import json
import logging
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
        # Collect all components
        environment = self._collect_environment()
        configuration = self._collect_configuration()
        risk_config = self._collect_risk_configuration()
        strategy_config = self._collect_strategy_configuration()
        performance = self._collect_performance()
        dataset_meta = self._collect_dataset_metadata()
        research_state = self._collect_research_state(performance)

        # ─── Wave 4C.1: canonical baseline identity ───────────────────────
        # config_hash: the EXISTING material-configuration identity primitive
        # (core.research_events.compute_config_hash()) — deliberately reused,
        # not re-implemented. NOTE (established in Wave 4C.0, NOT expanded
        # here): compute_config_hash()'s material-parameter coverage is
        # incomplete; its hash therefore under-approximates true config
        # identity. That limitation is documented, not repaired, in this wave.
        #
        # identity_hash: deterministic content hash over the captured state
        # below (canonical sorted-key JSON; created_at/notes excluded because
        # they are wall-clock/caller labels, not captured state). Equivalent
        # captured state → identical identity_hash → identical snapshot_id:
        # collision-safe, restart-stable, and NOT dependent on wall-clock
        # minute resolution (the old V10_BASELINE_%Y%m%d_%H%M format).
        from core.research_events import compute_config_hash
        config_hash = compute_config_hash()

        identity_content = {
            "bot_version": self._bot_version,
            "environment": environment,
            "configuration": configuration,
            "risk_configuration": risk_config,
            "strategy_configuration": strategy_config,
            "performance_metrics": performance,
            "dataset_metadata": dataset_meta,
            "research_state": research_state,
        }
        identity_hash = hashlib.sha256(
            json.dumps(identity_content, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()[:16]

        snapshot_id = f"V10_BASELINE_{identity_hash}"

        snapshot = BaselineSnapshot(
            snapshot_id=snapshot_id,
            bot_version=self._bot_version,
            notes=self._notes,
            config_hash=config_hash,
            identity_hash=identity_hash,
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

    @staticmethod
    def from_verified_fake_state(previous: BaselineSnapshot, effective_state: dict,
                                 operation_id: str) -> BaselineSnapshot:
        """Capture injected fake policy without collecting unrelated live data.

        Existing baseline context is frozen, not recollected at recovery. This
        hash describes a fake policy authority ONLY, not core config identity.
        """
        data = json.loads(json.dumps(previous.to_dict(), allow_nan=False))
        data["configuration"]["wave5_fake_policy"] = effective_state
        data["config_hash"] = hashlib.sha256(json.dumps({
            "parent_config": previous.config_hash, "fake_policy": effective_state,
        }, sort_keys=True, allow_nan=False).encode()).hexdigest()
        content = {k: v for k, v in data.items()
                   if k not in ("snapshot_id", "created_at", "notes", "identity_hash")}
        identity = hashlib.sha256(json.dumps(content, sort_keys=True, allow_nan=False).encode()).hexdigest()
        data.update(snapshot_id=f"V10_BASELINE_{identity}", identity_hash=identity,
                    notes=f"Verified fake policy operation {operation_id}")
        return BaselineSnapshot.from_dict(data)

    @staticmethod
    def from_verified_real_state(previous: BaselineSnapshot, effective_state: dict,
                                 operation_id: str) -> BaselineSnapshot:
        """Capture verified real optimisation policy (Wave 5.3, direction_inversion).

        Same freeze semantics as from_verified_fake_state: existing baseline
        context is frozen, never recollected. The effective policy participates
        in config/identity, so NORMAL vs direction_inversion and distinct
        frozen scopes produce distinct identities.
        """
        from core.optimisation_policy import validate_effective_state

        cleaned = validate_effective_state(
            json.loads(json.dumps(effective_state, allow_nan=False)))
        data = json.loads(json.dumps(previous.to_dict(), allow_nan=False))
        data["configuration"]["optimisation_policy"] = cleaned
        data["config_hash"] = hashlib.sha256(json.dumps({
            "parent_config": previous.config_hash, "optimisation_policy": cleaned,
        }, sort_keys=True, allow_nan=False).encode()).hexdigest()
        content = {k: v for k, v in data.items()
                   if k not in ("snapshot_id", "created_at", "notes", "identity_hash")}
        identity = hashlib.sha256(json.dumps(content, sort_keys=True, allow_nan=False).encode()).hexdigest()
        data.update(snapshot_id=f"V10_BASELINE_{identity}", identity_hash=identity,
                    notes=f"Verified optimisation policy operation {operation_id}")
        return BaselineSnapshot.from_dict(data)

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
        """Collect dataset identity information from the S3 research universe artifact."""
        events = self._load_universe()
        if not events:
            return {"status": "MISSING", "reason": "Research Universe not available"}

        # Deterministic content hash over the parsed records (S3 is authoritative;
        # no local file size/mtime is applicable to an S3 artifact).
        content = "\n".join(json.dumps(e, sort_keys=True, default=str) for e in events)
        content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]

        return {
            "dataset": "research_universe",
            "records": len(events),
            "hash": content_hash,
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
        # S3 is authoritative; local override ignored.
        from research_engine.data_access.s3_source import get_default_source
        return get_default_source().read_artifact("research_universe")
