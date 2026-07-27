"""
MTF Calibration — Metrics Collector and Reporter.

Tracks comparison metrics between baseline (M5-only) and MTF-augmented decisions.
Pure data collection — no trading logic.
"""

from __future__ import annotations

import csv
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class CycleResult:
    """Single bar evaluation result for comparison."""
    timestamp: int
    symbol: str
    baseline_should_trade: bool
    baseline_reason: str
    baseline_score: float
    mtf_should_trade: bool
    mtf_reason: str
    mtf_score: float
    htf_block: bool
    htf_block_reason: str
    htf_score_adjustment: float
    htf_min_score_adjustment: float
    h4_regime: str
    h1_bias: str
    m15_quality: float


@dataclass
class CalibrationMetrics:
    """Aggregated calibration metrics."""
    total_bars: int = 0
    baseline_trades: int = 0
    mtf_trades: int = 0
    htf_blocks_total: int = 0
    htf_blocks_h1_contradiction: int = 0
    htf_blocks_m15_structure: int = 0
    htf_score_adjustments_applied: int = 0
    htf_score_adj_sum: float = 0.0
    htf_min_score_adj_sum: float = 0.0
    agreements: int = 0  # both systems agree (both trade or both don't)
    blocked_would_have_traded: int = 0  # baseline=trade, mtf=blocked
    results: list[CycleResult] = field(default_factory=list)

    @property
    def block_rate(self) -> float:
        """Percentage of baseline trades blocked by HTF."""
        if self.baseline_trades == 0:
            return 0.0
        return self.blocked_would_have_traded / self.baseline_trades * 100

    @property
    def agreement_rate(self) -> float:
        """Percentage of bars where both systems agree."""
        if self.total_bars == 0:
            return 0.0
        return self.agreements / self.total_bars * 100

    @property
    def h1_block_rate(self) -> float:
        if self.baseline_trades == 0:
            return 0.0
        return self.htf_blocks_h1_contradiction / self.baseline_trades * 100

    @property
    def m15_block_rate(self) -> float:
        if self.baseline_trades == 0:
            return 0.0
        return self.htf_blocks_m15_structure / self.baseline_trades * 100

    @property
    def avg_score_adjustment(self) -> float:
        if self.htf_score_adjustments_applied == 0:
            return 0.0
        return self.htf_score_adj_sum / self.htf_score_adjustments_applied

    def record(self, result: CycleResult) -> None:
        """Record a single cycle result."""
        self.total_bars += 1
        self.results.append(result)

        if result.baseline_should_trade:
            self.baseline_trades += 1
        if result.mtf_should_trade:
            self.mtf_trades += 1

        # Agreement
        if result.baseline_should_trade == result.mtf_should_trade:
            self.agreements += 1

        # Blocking analysis
        if result.htf_block:
            self.htf_blocks_total += 1
            if "h1" in result.htf_block_reason.lower() or "contradiction" in result.htf_block_reason.lower():
                self.htf_blocks_h1_contradiction += 1
            if "m15" in result.htf_block_reason.lower() or "structure" in result.htf_block_reason.lower():
                self.htf_blocks_m15_structure += 1

        if result.baseline_should_trade and not result.mtf_should_trade and result.htf_block:
            self.blocked_would_have_traded += 1

        # Score adjustments
        if result.htf_score_adjustment != 0.0 or result.htf_min_score_adjustment != 0.0:
            self.htf_score_adjustments_applied += 1
            self.htf_score_adj_sum += result.htf_score_adjustment
            self.htf_min_score_adj_sum += result.htf_min_score_adjustment


def generate_report(metrics: CalibrationMetrics, symbol: str) -> str:
    """Generate human-readable calibration report."""
    lines = [
        "=" * 60,
        f"MTF CALIBRATION REPORT — {symbol}",
        "=" * 60,
        "",
        f"Total bars evaluated:        {metrics.total_bars}",
        f"Baseline trades (M5-only):   {metrics.baseline_trades}",
        f"MTF trades (with HTF):       {metrics.mtf_trades}",
        f"Net trade reduction:          {metrics.baseline_trades - metrics.mtf_trades}",
        "",
        "─── BLOCKING ANALYSIS ───",
        f"Total HTF blocks:            {metrics.htf_blocks_total}",
        f"  H1 contradiction blocks:   {metrics.htf_blocks_h1_contradiction} ({metrics.h1_block_rate:.1f}% of baseline trades)",
        f"  M15 structure blocks:      {metrics.htf_blocks_m15_structure} ({metrics.m15_block_rate:.1f}% of baseline trades)",
        f"Block rate (of baseline):    {metrics.block_rate:.1f}%",
        "",
        "─── AGREEMENT ───",
        f"Agreement rate:              {metrics.agreement_rate:.1f}%",
        f"Blocked would-have-traded:   {metrics.blocked_would_have_traded}",
        "",
        "─── SCORING IMPACT ───",
        f"Score adjustments applied:   {metrics.htf_score_adjustments_applied}",
        f"Average score adjustment:    {metrics.avg_score_adjustment:.3f}",
        "",
        "─── RISK ASSESSMENT ───",
    ]

    if metrics.block_rate > 60:
        lines.append("⚠️  OVERBLOCKING: HTF blocks >60% of trades — system too restrictive")
    elif metrics.block_rate > 40:
        lines.append("⚠️  HIGH BLOCKING: HTF blocks 40-60% — consider relaxing thresholds")
    elif metrics.block_rate > 20:
        lines.append("✅ MODERATE FILTERING: HTF blocks 20-40% — reasonable selectivity")
    elif metrics.block_rate > 5:
        lines.append("✅ LIGHT FILTERING: HTF blocks 5-20% — minimal impact")
    else:
        lines.append("⚠️  UNDERBLOCKING: HTF blocks <5% — system may be too permissive")

    lines.append("")
    lines.append("─── RECOMMENDATIONS ───")

    if metrics.h1_block_rate > 30:
        lines.append("• REDUCE MTF_H1_CONTRADICTION_THRESHOLD (currently too sensitive)")
    if metrics.m15_block_rate > 30:
        lines.append("• REDUCE MTF_M15_MIN_STRUCTURE_QUALITY (currently too strict)")
    if metrics.block_rate < 5:
        lines.append("• INCREASE sensitivity — HTF is not filtering enough noise")
    if metrics.block_rate > 50:
        lines.append("• DECREASE all penalty values by 30-50%")

    lines.append("")
    lines.append("=" * 60)
    return "\n".join(lines)


def export_csv(metrics: CalibrationMetrics, output_path: str) -> None:
    """Export detailed results to CSV."""
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "timestamp", "symbol", "baseline_trade", "mtf_trade",
            "htf_block", "block_reason", "score_delta",
            "h4_regime", "h1_bias", "m15_quality",
            "baseline_score", "mtf_score",
        ])
        for r in metrics.results:
            writer.writerow([
                r.timestamp, r.symbol,
                r.baseline_should_trade, r.mtf_should_trade,
                r.htf_block, r.htf_block_reason,
                round(r.htf_score_adjustment, 3),
                r.h4_regime, r.h1_bias, round(r.m15_quality, 3),
                round(r.baseline_score, 2), round(r.mtf_score, 2),
            ])
