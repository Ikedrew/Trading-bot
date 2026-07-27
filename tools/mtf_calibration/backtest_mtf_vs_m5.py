"""
MTF Calibration Backtest Engine.

Compares M5-only baseline decisions against M5+HTF augmented decisions
using historical MT5 data. Produces calibration metrics and recommendations.

Usage:
    python tools/mtf_calibration/backtest_mtf_vs_m5.py [--symbol EURUSD] [--bars 1000]

Requirements:
    - MT5 terminal running and connected
    - Project root on sys.path

This script is READ-ONLY — it does NOT modify live engine code.
It is a pure analysis tool and deterministic replay engine.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# Ensure project root on path
ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data.mt5_data import Candle
from core.timeframes.h4_regime import analyze_regime
from core.timeframes.h1_bias import analyze_bias
from core.timeframes.m15_structure import analyze_structure
from core.timeframes.integration import apply_htf_constraints
from core.timeframes.types import HTFContext, BiasDirection, RegimeClassification
from strategy.signals import Side

from tools.mtf_calibration.data_loader import (
    init_mt5,
    shutdown_mt5,
    load_all_timeframes,
)
from tools.mtf_calibration.metrics import (
    CalibrationMetrics,
    CycleResult,
    export_csv,
    generate_report,
)


def _find_htf_bar_at_time(candles: list[Candle], m5_time: int) -> int:
    """Find the index of the HTF bar that was active at the given M5 bar time."""
    for i in range(len(candles) - 1, -1, -1):
        if candles[i].time <= m5_time:
            return i
    return 0


def _simulate_m5_signal(candles: list[Candle], closed_i: int) -> tuple[bool, str, float, Side | None]:
    """
    Lightweight M5 signal simulation (simplified pipeline proxy).
    Returns (should_trade, reason, score, side).

    This is NOT the full pipeline — it's a simplified proxy for calibration purposes.
    It detects basic directional setups to generate comparison signals.
    """
    if closed_i < 20:
        return False, "insufficient_data", 0.0, None

    # Simple EMA-based bias detection (mirrors setup.py logic)
    closes = [c.close for c in candles[:closed_i + 1]]
    ma_10 = sum(closes[-10:]) / 10
    ma_prev = sum(closes[-11:-1]) / 10

    close = candles[closed_i].close
    prev_close = candles[closed_i - 1].close

    # Basic directional signal
    bullish = close > ma_10 and ma_10 > ma_prev and close > prev_close
    bearish = close < ma_10 and ma_10 < ma_prev and close < prev_close

    if not bullish and not bearish:
        return False, "no_setup", 0.0, None

    side = Side.BUY if bullish else Side.SELL

    # Simplified confluence score (2-7 range like real system)
    score = 3.0  # base
    body = abs(close - candles[closed_i].open)
    candle_range = candles[closed_i].high - candles[closed_i].low
    if candle_range > 0 and body / candle_range > 0.6:
        score += 1.0  # strong body
    if abs(close - ma_10) > 0.0005:
        score += 1.0  # clear MA distance

    # Check if score meets threshold
    min_score = 5.0
    if score < min_score:
        return False, f"score_below_threshold ({score:.1f})", score, side

    return True, "signal_ok", score, side


def run_calibration(
    symbol: str,
    m5_candles: list[Candle],
    h4_candles: list[Candle],
    h1_candles: list[Candle],
    m15_candles: list[Candle],
    config=None,
) -> CalibrationMetrics:
    """
    Run dual-pipeline simulation comparing M5-only vs M5+HTF decisions.

    For each M5 bar:
      1. Simulate baseline M5 decision
      2. Build HTF context from corresponding HTF bars
      3. Apply HTF constraints
      4. Compare decisions
    """
    if config is None:
        from core import config as _cfg
        config = _cfg

    metrics = CalibrationMetrics()
    start_i = max(50, 1)  # Need warmup bars

    for i in range(start_i, len(m5_candles) - 1):
        closed_i = i
        m5_time = m5_candles[closed_i].time
        current_price = m5_candles[closed_i].close

        # 1. Baseline M5 decision
        baseline_trade, baseline_reason, baseline_score, signal_side = _simulate_m5_signal(
            m5_candles, closed_i
        )

        # 2. Build HTF context at this point in time
        h4_idx = _find_htf_bar_at_time(h4_candles, m5_time)
        h1_idx = _find_htf_bar_at_time(h1_candles, m5_time)
        m15_idx = _find_htf_bar_at_time(m15_candles, m5_time)

        # Run analyzers on historical data up to this point
        h4_slice = h4_candles[:h4_idx + 1] if h4_idx >= 20 else []
        h1_slice = h1_candles[:h1_idx + 1] if h1_idx >= 20 else []
        m15_slice = m15_candles[:m15_idx + 1] if m15_idx >= 10 else []

        regime_snap = analyze_regime(h4_slice) if h4_slice else None
        bias_snap = analyze_bias(h1_slice) if h1_slice else None
        structure_snap = analyze_structure(m15_slice, current_price) if m15_slice else None

        htf_context = HTFContext(
            regime=regime_snap,
            bias=bias_snap,
            structure=structure_snap,
        )

        # 3. Apply HTF constraints (only if baseline would trade)
        htf_block = False
        htf_block_reason = ""
        htf_score_adj = 0.0
        htf_min_score_adj = 0.0
        mtf_trade = baseline_trade
        mtf_score = baseline_score

        if baseline_trade and signal_side is not None and htf_context.is_populated:
            influence = apply_htf_constraints(
                htf_context=htf_context,
                signal_side=signal_side,
                evaluation_bias=signal_side,
                config=config,
            )
            htf_score_adj = influence.score_adjustment
            htf_min_score_adj = influence.min_score_adjustment

            if influence.is_blocking:
                htf_block = True
                htf_block_reason = influence.block_reason
                mtf_trade = False
                mtf_score = baseline_score + htf_score_adj
            else:
                # Apply score adjustment
                mtf_score = baseline_score + htf_score_adj
                adjusted_min = 5.0 + htf_min_score_adj
                if mtf_score < adjusted_min:
                    mtf_trade = False
                    htf_block_reason = f"score_below_adjusted_threshold ({mtf_score:.1f} < {adjusted_min:.1f})"

        # 4. Record result
        result = CycleResult(
            timestamp=m5_time,
            symbol=symbol,
            baseline_should_trade=baseline_trade,
            baseline_reason=baseline_reason,
            baseline_score=baseline_score,
            mtf_should_trade=mtf_trade,
            mtf_reason=htf_block_reason if htf_block else baseline_reason,
            mtf_score=mtf_score,
            htf_block=htf_block,
            htf_block_reason=htf_block_reason,
            htf_score_adjustment=htf_score_adj,
            htf_min_score_adjustment=htf_min_score_adj,
            h4_regime=regime_snap.classification.value if regime_snap else "N/A",
            h1_bias=bias_snap.direction.value if bias_snap else "N/A",
            m15_quality=structure_snap.quality_score if structure_snap else 0.0,
        )
        metrics.record(result)

    return metrics


def main():
    parser = argparse.ArgumentParser(description="MTF Calibration Backtest")
    parser.add_argument("--symbol", default="EURUSD", help="Symbol to test")
    parser.add_argument("--bars", type=int, default=1000, help="M5 bars to evaluate")
    parser.add_argument("--output", default="logs/mtf_calibration", help="Output directory")
    parser.add_argument("--terminal", default=None, help="MT5 terminal path")
    args = parser.parse_args()

    print(f"MTF Calibration Backtest — {args.symbol}")
    print(f"Bars: {args.bars}")
    print()

    # Initialize MT5
    terminal = args.terminal or r"C:\Program Files\Pepperstone MetaTrader 5\terminal64.exe"
    if not init_mt5(terminal):
        print(f"ERROR: Failed to initialize MT5 at {terminal}")
        sys.exit(1)

    try:
        # Load data
        print("Loading data...")
        data = load_all_timeframes(
            symbol=args.symbol,
            m5_count=args.bars + 100,  # extra for warmup
            h4_count=100,
            h1_count=200,
            m15_count=200,
        )
        print(f"  M5:  {len(data['M5'])} bars")
        print(f"  H4:  {len(data['H4'])} bars")
        print(f"  H1:  {len(data['H1'])} bars")
        print(f"  M15: {len(data['M15'])} bars")
        print()

        # Run calibration
        print("Running calibration...")
        t0 = time.perf_counter()
        metrics = run_calibration(
            symbol=args.symbol,
            m5_candles=data["M5"],
            h4_candles=data["H4"],
            h1_candles=data["H1"],
            m15_candles=data["M15"],
        )
        elapsed = time.perf_counter() - t0
        print(f"Completed in {elapsed:.2f}s")
        print()

        # Generate report
        report = generate_report(metrics, args.symbol)
        print(report)

        # Export CSV
        output_dir = Path(args.output)
        output_dir.mkdir(parents=True, exist_ok=True)
        csv_path = str(output_dir / f"{args.symbol}_calibration.csv")
        export_csv(metrics, csv_path)
        print(f"\nCSV exported: {csv_path}")

        # Save report
        report_path = str(output_dir / f"{args.symbol}_report.txt")
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"Report saved: {report_path}")

    finally:
        shutdown_mt5()


if __name__ == "__main__":
    main()
