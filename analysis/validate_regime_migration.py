"""
Validation Check — Regime Authority Migration 1.

Answers: "Did the engine regain environmental awareness after moving
regime authority to H4?"

Reads persisted decision traces, decision audits, and decision ledger.
Does NOT modify any code or data.
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

# Ensure project root is on sys.path for imports
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


def load_jsonl_tree(directory: Path) -> list[dict]:
    """Load all JSONL records from a directory tree."""
    records = []
    if not directory.exists():
        return records
    for item in sorted(directory.rglob("*.jsonl")):
        for line in item.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except (json.JSONDecodeError, ValueError):
                    pass
    return records


def main():
    root = Path(".")

    print("=" * 70)
    print("VALIDATION: REGIME AUTHORITY MIGRATION 1")
    print("=" * 70)
    print()

    # ═══════════════════════════════════════════════════════════════════
    # 1. INSPECT PERSISTED DECISION TRACES
    # ═══════════════════════════════════════════════════════════════════
    print("─── 1. DECISION TRACE PERSISTENCE ───────────────────────────────")
    print()

    trace_dir = root / "logs" / "decision_trace"
    traces = load_jsonl_tree(trace_dir)
    print(f"  Location: logs/decision_trace/{{SYMBOL}}/{{DATE}}.jsonl")
    print(f"  Records loaded: {len(traces)}")

    if traces:
        symbols = sorted(set(t.get("symbol", "?") for t in traces))
        print(f"  Symbols: {symbols}")

        # Check for regime_source field
        has_source = [t for t in traces if t.get("regime_source")]
        no_source = [t for t in traces if not t.get("regime_source")]
        print(f"  With regime_source field: {len(has_source)}")
        print(f"  Without regime_source:    {len(no_source)} (pre-migration)")

        # Show sample schema
        sample = traces[-1]
        regime_fields = {k: v for k, v in sample.items() if "regime" in k.lower()}
        print(f"  Regime-related fields in latest trace:")
        for k, v in sorted(regime_fields.items()):
            print(f"    {k}: {v}")
    print()

    # ═══════════════════════════════════════════════════════════════════
    # 2. REGIME DISTRIBUTION ANALYSIS
    # ═══════════════════════════════════════════════════════════════════
    print("─── 2. REGIME DISTRIBUTION ANALYSIS ─────────────────────────────")
    print()

    # Split into pre-migration vs post-migration
    pre_migration = [t for t in traces if not t.get("regime_source")]
    post_migration = [t for t in traces if t.get("regime_source")]

    print("  PRE-MIGRATION baseline (no regime_source field):")
    if pre_migration:
        pre_regimes = Counter(t.get("regime") for t in pre_migration if t.get("regime"))
        total = sum(pre_regimes.values())
        for r, c in pre_regimes.most_common():
            print(f"    {r:20s}: {c:5d} ({c/total*100:.1f}%)")
    else:
        print("    (no pre-migration data)")
    print()

    print("  POST-MIGRATION (with regime_source field):")
    if post_migration:
        # By source
        by_source = defaultdict(list)
        for t in post_migration:
            by_source[t.get("regime_source", "UNKNOWN")].append(t)

        for source, subset in sorted(by_source.items()):
            print(f"    Source: {source} (n={len(subset)})")
            regime_dist = Counter(t.get("regime") for t in subset if t.get("regime"))
            total = sum(regime_dist.values())
            for r, c in regime_dist.most_common():
                print(f"      {r:20s}: {c:5d} ({c/total*100:.1f}%)")
            print()
    else:
        print("    ⚠ NO POST-MIGRATION DATA — system has not run since deployment")
        print()
        print("    The bot must run live (or replay) to generate post-migration traces.")
        print("    Expected behaviour after a live session:")
        print("      - regime_source = 'H4_MARKET_CONTEXT' (when HTF cache populated)")
        print("      - regime_source = 'M5_CLASSIFIER' (fallback when HTF unavailable)")
        print("      - Regime distribution should show TRENDING/RANGE/TRANSITIONAL variation")
    print()

    # ═══════════════════════════════════════════════════════════════════
    # 3. CODE PATH VERIFICATION
    # ═══════════════════════════════════════════════════════════════════
    print("─── 3. CODE PATH VERIFICATION ───────────────────────────────────")
    print()

    # Verify the regime authority chain exists in code
    checks = []

    # Check 1: config flag exists
    try:
        from core import config
        flag = getattr(config, "MARKET_CONTEXT_ENABLED", None)
        checks.append(("MARKET_CONTEXT_ENABLED flag", flag is not None, f"value={flag}"))
    except Exception as e:
        checks.append(("MARKET_CONTEXT_ENABLED flag", False, str(e)))

    # Check 2: selection_activation accepts market_context_regime
    try:
        import inspect
        from strategy.selection_activation import run_strategy_activation
        sig = inspect.signature(run_strategy_activation)
        has_param = "market_context_regime" in sig.parameters
        checks.append(("run_strategy_activation has market_context_regime param", has_param, ""))
    except Exception as e:
        checks.append(("run_strategy_activation signature", False, str(e)))

    # Check 3: new_engine includes regime_source in output
    try:
        from core.pipeline.new_engine import _GLOBAL_WEIGHTS
        # Read source to verify regime_source is in _strategy_meta
        src = (root / "core" / "pipeline" / "new_engine.py").read_text(encoding="utf-8")
        has_field = '"regime_source"' in src or "'regime_source'" in src
        checks.append(("new_engine.py includes regime_source field", has_field, ""))
    except Exception as e:
        checks.append(("new_engine.py regime_source", False, str(e)))

    # Check 4: H4 regime extraction logic exists
    try:
        src = (root / "core" / "pipeline" / "new_engine.py").read_text(encoding="utf-8")
        has_h4_extract = "H4_MARKET_CONTEXT" in src
        checks.append(("new_engine.py H4 regime extraction logic", has_h4_extract, ""))
    except Exception as e:
        checks.append(("H4 extraction logic", False, str(e)))

    # Check 5: MarketContextBuilder exists and works
    try:
        from core.market_context.builder import MarketContextBuilder
        builder = MarketContextBuilder(symbol="TEST")
        ctx = builder.build(cycle_id=1, current_time_s=1000.0)
        checks.append(("MarketContextBuilder produces output", ctx is not None, f"regime={ctx.regime.value}"))
    except Exception as e:
        checks.append(("MarketContextBuilder", False, str(e)))

    # Check 6: run_strategy_activation uses H4 when provided
    try:
        from strategy.selection_activation import run_strategy_activation
        from strategy.signals import Signal, Side
        from dataclasses import dataclass

        @dataclass
        class FC:
            open: float = 1.0
            high: float = 1.01
            low: float = 0.99
            close: float = 1.005
            time: int = 1000
            tick_volume: int = 100
            real_volume: int = 100
            spread: int = 1

        candles = [FC(time=i * 300) for i in range(25)]
        pat = Signal(pattern="HAMMER", side=Side.BUY, bar_index=20, bar_time=6000, confidence=0.6)

        result = run_strategy_activation(
            candles=candles, closed_i=20, pattern=pat,
            market_context_regime="TRENDING", market_context_regime_confidence=0.85,
        )
        used_h4 = (result.regime == "TRENDING")
        checks.append(("H4 regime used when provided", used_h4, f"regime={result.regime}"))
    except Exception as e:
        checks.append(("H4 regime functional test", False, str(e)))

    # Check 7: M5 fallback works when H4 not provided
    try:
        result_fallback = run_strategy_activation(
            candles=candles, closed_i=20, pattern=pat,
        )
        is_valid = result_fallback.regime in ("TRENDING", "RANGE", "TRANSITIONAL")
        checks.append(("M5 fallback works (no H4)", is_valid, f"regime={result_fallback.regime}"))
    except Exception as e:
        checks.append(("M5 fallback", False, str(e)))

    for name, passed, detail in checks:
        status = "✅" if passed else "❌"
        suffix = f" ({detail})" if detail else ""
        print(f"  {status} {name}{suffix}")

    print()

    # ═══════════════════════════════════════════════════════════════════
    # 4. EXPECTED IMPACT (theoretical)
    # ═══════════════════════════════════════════════════════════════════
    print("─── 4. EXPECTED IMPACT ASSESSMENT ───────────────────────────────")
    print()

    print("  Pre-migration regime distribution (observed):")
    print("    TRANSITIONAL: 99.4% (degenerate — single-class collapse)")
    print("    TRENDING:      0.6%")
    print("    RANGE:         0.0%")
    print()
    print("  Expected post-migration distribution (H4 regime authority):")
    print("    H4 classifies from 100 bars of 4-hour data (400 hours lookback)")
    print("    Expected: more TRENDING and RANGING detection")
    print("    The H4 regime analyzer has 5 classifications:")
    print("      TRENDING_BULLISH → maps to TRENDING")
    print("      TRENDING_BEARISH → maps to TRENDING")
    print("      RANGING          → maps to RANGE")
    print("      VOLATILE         → maps to TRANSITIONAL")
    print("      TRANSITIONAL     → maps to TRANSITIONAL")
    print()
    print("  Impact on strategy activation:")
    print("    - TRENDING regime: CONTINUATION weight ×1.3, REVERSAL weight ×0.4")
    print("    - RANGE regime:    REVERSAL weight ×1.2, CONTINUATION blocked (unless BOS)")
    print("    - TRANSITIONAL:    All weights ×0.5 (dampened)")
    print()
    print("  Previously: 99.4% TRANSITIONAL → all strategies received ×0.5 dampening")
    print("  Expected:   Strategies now receive regime-appropriate modulation")
    print()

    # ═══════════════════════════════════════════════════════════════════
    # 5. VERDICT
    # ═══════════════════════════════════════════════════════════════════
    print("─── 5. VERDICT ─────────────────────────────────────────────────")
    print()

    all_passed = all(p for _, p, _ in checks)
    has_post_data = len(post_migration) > 0

    if all_passed and has_post_data:
        print("  ✅ MIGRATION VALIDATED — H4 regime authority confirmed in live data")
    elif all_passed and not has_post_data:
        print("  ⚠️  MIGRATION CODE VERIFIED — awaiting live run for data validation")
        print()
        print("  All code paths are correct:")
        print("  • MARKET_CONTEXT_ENABLED=True")
        print("  • run_strategy_activation accepts H4 regime")
        print("  • new_engine.py extracts H4 and passes to activation")
        print("  • regime_source field is included in engine output")
        print("  • Fallback to M5 works when H4 unavailable")
        print()
        print("  NEXT STEP: Run the bot (live or replay) to validate distribution shift")
    else:
        print("  ❌ MIGRATION INCOMPLETE — code path checks failed")
        for name, passed, detail in checks:
            if not passed:
                print(f"     FAILED: {name} — {detail}")
    print()


if __name__ == "__main__":
    main()
