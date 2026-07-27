"""
MTF Calibration — Shadow Mode Metrics Collector.

Aggregates comparison data between baseline (M5-only) and MTF-augmented decisions
during shadow mode operation. Produces structured diagnostics and session reports.

Ownership: core/timeframes/calibration.py
Constraints:
    - NEVER modifies EngineState
    - NEVER affects execution path
    - NEVER changes scoring logic
    - Only logs + aggregates
    - Runs in shadow mode only

Usage:
    from core.timeframes.calibration import mtf_calibration
    mtf_calibration.record(symbol, baseline_dec, mtf_dec, htf_context)
    mtf_calibration.emit_summary()
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class _RegimeStats:
    """Per-regime aggregated statistics."""
    evaluations: int = 0
    blocks: int = 0
    score_delta_sum: float = 0.0
    divergences: int = 0

    @property
    def block_rate(self) -> float:
        return (self.blocks / self.evaluations * 100) if self.evaluations > 0 else 0.0

    @property
    def avg_score_delta(self) -> float:
        return (self.score_delta_sum / self.evaluations) if self.evaluations > 0 else 0.0

    @property
    def divergence_rate(self) -> float:
        return (self.divergences / self.evaluations * 100) if self.evaluations > 0 else 0.0


class MTFCalibrationCollector:
    """
    Shadow mode metrics collector. Tracks decision divergence between
    baseline and MTF pipelines without affecting execution.

    Thread-unsafe (single-threaded runtime). Reset between sessions.
    """

    def __init__(self) -> None:
        self.total_evaluations: int = 0
        self.baseline_trades: int = 0
        self.mtf_trades: int = 0
        self.htf_blocks: int = 0
        self.divergences: int = 0
        self.score_delta_sum: float = 0.0
        self.score_deltas: list[float] = []

        # Per-block-reason counters
        self.block_reasons: dict[str, int] = defaultdict(int)

        # Per-regime stats
        self.regime_stats: dict[str, _RegimeStats] = defaultdict(_RegimeStats)

        # Per-bias stats
        self.bias_stats: dict[str, _RegimeStats] = defaultdict(_RegimeStats)

    def record(
        self,
        *,
        symbol: str,
        baseline_should_trade: bool,
        baseline_score: int,
        baseline_reason: str,
        mtf_should_trade: bool,
        mtf_score: int,
        mtf_reason: str,
        htf_blocked: bool,
        block_reason: str,
        h4_regime: str,
        h1_bias: str,
        m15_quality: float,
    ) -> None:
        """
        Record a single bar evaluation comparison.
        Called from live_scanner shadow mode block.
        """
        self.total_evaluations += 1
        score_delta = mtf_score - baseline_score
        self.score_delta_sum += score_delta
        self.score_deltas.append(score_delta)

        if baseline_should_trade:
            self.baseline_trades += 1
        if mtf_should_trade:
            self.mtf_trades += 1
        if htf_blocked:
            self.htf_blocks += 1
            if block_reason:
                # Categorize block reason
                if "h1" in block_reason.lower() or "contradiction" in block_reason.lower():
                    self.block_reasons["h1_contradiction"] += 1
                elif "m15" in block_reason.lower() or "structure" in block_reason.lower():
                    self.block_reasons["m15_structure"] += 1
                else:
                    self.block_reasons["other"] += 1

        # Divergence
        diverged = baseline_should_trade != mtf_should_trade
        if diverged:
            self.divergences += 1

        # Per-regime tracking
        regime_key = h4_regime or "UNKNOWN"
        rs = self.regime_stats[regime_key]
        rs.evaluations += 1
        rs.score_delta_sum += score_delta
        if htf_blocked:
            rs.blocks += 1
        if diverged:
            rs.divergences += 1

        # Per-bias tracking
        bias_key = h1_bias or "UNKNOWN"
        bs = self.bias_stats[bias_key]
        bs.evaluations += 1
        bs.score_delta_sum += score_delta
        if htf_blocked:
            bs.blocks += 1
        if diverged:
            bs.divergences += 1

        # Structured calibration log
        logger.info(
            "[CALIBRATION] symbol=%s baseline=%s mtf=%s delta=%d block=%s regime=%s bias=%s m15=%.2f",
            symbol,
            "TRADE" if baseline_should_trade else "NO_TRADE",
            "TRADE" if mtf_should_trade else "NO_TRADE",
            score_delta,
            block_reason or "none",
            regime_key,
            bias_key,
            m15_quality,
        )

    @property
    def block_rate(self) -> float:
        """% of baseline trades blocked by MTF."""
        if self.baseline_trades == 0:
            return 0.0
        return self.htf_blocks / self.baseline_trades * 100

    @property
    def divergence_rate(self) -> float:
        """% of evaluations where baseline and MTF disagree."""
        if self.total_evaluations == 0:
            return 0.0
        return self.divergences / self.total_evaluations * 100

    @property
    def avg_score_delta(self) -> float:
        if self.total_evaluations == 0:
            return 0.0
        return self.score_delta_sum / self.total_evaluations

    def emit_summary(self) -> None:
        """Emit structured calibration summary to log."""
        if self.total_evaluations == 0:
            logger.info("[CALIBRATION_SUMMARY] no evaluations recorded")
            return

        logger.info(
            "[CALIBRATION_SUMMARY] evaluations=%d baseline_trades=%d mtf_trades=%d "
            "blocks=%d block_rate=%.1f%% divergence_rate=%.1f%% avg_score_delta=%.2f",
            self.total_evaluations,
            self.baseline_trades,
            self.mtf_trades,
            self.htf_blocks,
            self.block_rate,
            self.divergence_rate,
            self.avg_score_delta,
        )

        # Block reason breakdown
        if self.block_reasons:
            parts = " ".join(f"{k}={v}" for k, v in sorted(self.block_reasons.items()))
            logger.info("[CALIBRATION_BLOCKS] %s", parts)

        # Per-regime breakdown
        for regime, stats in sorted(self.regime_stats.items()):
            if stats.evaluations > 0:
                logger.info(
                    "[CALIBRATION_REGIME] regime=%s evals=%d block_rate=%.1f%% "
                    "avg_delta=%.2f divergence=%.1f%%",
                    regime, stats.evaluations, stats.block_rate,
                    stats.avg_score_delta, stats.divergence_rate,
                )

        # Assessment
        if self.block_rate > 60:
            logger.warning("[CALIBRATION_ASSESSMENT] OVERBLOCKING — MTF blocks >60%% of trades")
        elif self.block_rate > 40:
            logger.warning("[CALIBRATION_ASSESSMENT] HIGH_BLOCKING — consider relaxing thresholds")
        elif self.block_rate > 15:
            logger.info("[CALIBRATION_ASSESSMENT] MODERATE_FILTERING — reasonable selectivity")
        elif self.block_rate > 5:
            logger.info("[CALIBRATION_ASSESSMENT] LIGHT_FILTERING — minimal impact")
        else:
            logger.info("[CALIBRATION_ASSESSMENT] UNDERBLOCKING — MTF may be too permissive")

    def reset(self) -> None:
        """Reset all counters (between sessions)."""
        self.total_evaluations = 0
        self.baseline_trades = 0
        self.mtf_trades = 0
        self.htf_blocks = 0
        self.divergences = 0
        self.score_delta_sum = 0.0
        self.score_deltas.clear()
        self.block_reasons.clear()
        self.regime_stats.clear()
        self.bias_stats.clear()


# Module-level singleton
mtf_calibration = MTFCalibrationCollector()
