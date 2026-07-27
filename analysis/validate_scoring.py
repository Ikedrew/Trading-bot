"""Validate scoring after MarketContext authority migration."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dataclasses import dataclass
from unittest.mock import patch
from core.pipeline.new_engine import _compute_all_scores, _GLOBAL_WEIGHTS
from strategy.signals import Signal, Side


@dataclass
class FC:
    open: float = 1.1; high: float = 1.101; low: float = 1.099; close: float = 1.1005
    time: int = 1000; tick_volume: int = 100; real_volume: int = 100; spread: int = 1

@dataclass
class FakeState:
    current_bias: object = None; bias_phase: str = "CONFIRMED"; bias_strength: float = 60.0
    regime_state: str = "RANGING"

class FakeDir:
    value = "BULLISH"

@dataclass
class FakeBias:
    direction: object = None; confidence: float = 0.7; bar_time: int = 0
    ema_position: float = 0.5; swing_structure: str = "HH_HL"
    bos_confirmed: bool = True; bos_direction: str = "BULLISH"

@dataclass
class FakeRegime:
    classification: object = None; confidence: float = 0.8; atr_ratio: float = 1.1
    ema_slope: float = 0.2; trend_bias: str = "BULLISH"; trend_strength: float = 0.7; bar_time: int = 0

class FakeClass:
    value = "TRENDING_BULLISH"

@dataclass
class FakeStruct:
    quality_score: float = 0.65; bar_time: int = 0
    nearest_support: float = 1.099; nearest_resistance: float = 1.102
    at_key_level: bool = True; order_block_present: bool = False

@dataclass
class FakeHTF:
    regime: object = None; bias: object = None; structure: object = None


def main():
    candles = [FC(close=1.1 + i * 0.0001, time=i * 300) for i in range(65)]
    pattern = Signal(pattern="BULLISH_ENGULFING", side=Side.BUY, bar_index=60, bar_time=18000, confidence=0.8)
    state = FakeState()
    state.current_bias = Side.BUY
    htf = FakeHTF(
        regime=FakeRegime(classification=FakeClass()),
        bias=FakeBias(direction=FakeDir()),
        structure=FakeStruct(),
    )
    cfg = type("C", (), {"TREND_EMA_PERIOD": 50, "MARKET_FILTER_LOOKBACK": 5})()

    with patch("core.config.MARKET_CONTEXT_ENABLED", True):
        scores = _compute_all_scores(
            candles=candles, closed_i=60, best_pattern=pattern,
            engine_state=state, config=cfg, htf_context=htf,
        )

    issues = []

    # 1. All 10 components present
    expected_keys = set(_GLOBAL_WEIGHTS.keys())
    actual_keys = set(scores.keys())
    if expected_keys != actual_keys:
        issues.append(f"Key mismatch: missing={expected_keys - actual_keys}, extra={actual_keys - expected_keys}")

    # 2. Weights sum to 1.0
    weight_sum = sum(_GLOBAL_WEIGHTS.values())
    if abs(weight_sum - 1.0) > 0.01:
        issues.append(f"Weight sum={weight_sum:.4f} (expected 1.0)")

    # 3. All scores in [0, 1]
    for k, v in scores.items():
        if not (0.0 <= v <= 1.0):
            issues.append(f"{k}={v} out of bounds [0,1]")

    # 4. Migrated components use correct source
    if scores["market_quality"] != 0.65:
        issues.append(f"market_quality={scores['market_quality']} (expected 0.65 from M15)")
    if scores["chop_clarity"] != 0.80:
        issues.append(f"chop_clarity={scores['chop_clarity']} (expected 0.80 from M15 0.65+0.15 key_level)")
    if scores["trend_alignment"] < 0.8:
        issues.append(f"trend_alignment={scores['trend_alignment']} (expected >=0.8 from H1 BULLISH aligned)")
    if scores["h4_alignment"] < 0.5:
        issues.append(f"h4_alignment={scores['h4_alignment']} (expected >0.5 from H4 TRENDING_BULLISH aligned)")

    # 5. M5 execution components still contributing
    if scores["pattern_quality"] != 1.0:
        issues.append(f"pattern_quality={scores['pattern_quality']} (expected 1.0 for STRONG pattern)")
    if scores["bias_alignment"] != 1.0:
        issues.append(f"bias_alignment={scores['bias_alignment']} (expected 1.0 for CONFIRMED+aligned)")
    if scores["bias_stability"] != 0.6:
        issues.append(f"bias_stability={scores['bias_stability']} (expected 0.6 = 60/100)")
    if not (0.0 < scores["confirmation_pre"] <= 1.0):
        issues.append(f"confirmation_pre={scores['confirmation_pre']} (expected >0)")
    if not (0.0 <= scores["volatility_quality"] <= 1.0):
        issues.append(f"volatility_quality={scores['volatility_quality']} out of range")

    # 6. Final score calculation
    weighted = sum(_GLOBAL_WEIGHTS.get(k, 0.0) * v for k, v in scores.items())
    if not (0.0 < weighted <= 1.0):
        issues.append(f"Weighted sum={weighted} out of range")

    # 7. No double-counting (10 unique keys, each in weights)
    if len(scores) != 10:
        issues.append(f"Component count={len(scores)} (expected 10)")

    # Output
    print("=" * 60)
    print("SCORING VALIDATION — Post MarketContext Migration")
    print("=" * 60)
    print()
    for k, v in sorted(scores.items()):
        src = "M15" if k in ("market_quality", "chop_clarity") else "H1" if k == "trend_alignment" else "H4" if k == "h4_alignment" else "H1+M15" if k == "htf_alignment" else "M5"
        print(f"  {k:22s}: {v:.4f}  (source: {src}, weight: {_GLOBAL_WEIGHTS[k]:.2f})")
    print()
    print(f"  Final weighted score: {weighted:.4f}")
    print(f"  Weight sum: {weight_sum:.4f}")
    print(f"  Components: {len(scores)}/10")
    print()

    if issues:
        print(f"⚠️  ISSUES FOUND ({len(issues)}):")
        for i in issues:
            print(f"    - {i}")
    else:
        print("✅ Working — all components present, correct sources, no double-counting")


if __name__ == "__main__":
    main()
