"""
V10 RESEARCH ENGINE — INVESTIGATIVE RESEARCH CYCLE #1

Based on baseline findings, investigates:
1. WHY is realised expectancy negative (-0.18R) when opportunity pool is positive (+0.07R)?
2. WHY does score NOT predict outcome?
3. WHICH strategies/regimes are positive vs negative in shadow?
4. WHERE in the pipeline is value being destroyed?

Does NOT modify V10.
"""
import sys
import json
import statistics
from pathlib import Path
from collections import defaultdict, Counter

sys.path.insert(0, ".")

from research_engine.v10.universes.shadow_outcome_universe import ShadowOutcomeUniverseBuilder
from research_engine.v10.universes.models import Population, Universe
from research_engine.v10.universes import (
    ExecutionUniverseBuilder, DecisionUniverseBuilder,
)
from research_engine.v10.universes.outcome_enrichment import OutcomeEnrichment


def main():
    out = []
    out.append("=" * 70)
    out.append("V10 INVESTIGATIVE RESEARCH CYCLE #1")
    out.append("=" * 70)
    out.append("")
    out.append("CENTRAL QUESTION: Why is V10 losing money when opportunities exist?")
    out.append("")
    
    # Build universes
    exe_builder = ExecutionUniverseBuilder()
    exe_builder.build()
    dec_builder = DecisionUniverseBuilder()
    dec_builder.build()
    enrichment = OutcomeEnrichment(exe_builder)
    enrichment.enrich(dec_builder)
    shadow_builder = ShadowOutcomeUniverseBuilder()
    shadow_builder.build()
    
    # Load decision traces for deeper analysis
    dt_dir = Path("logs/decision_trace")
    dt_by_entity = {}
    if dt_dir.exists():
        for sym_dir in sorted(dt_dir.iterdir()):
            if not sym_dir.is_dir(): continue
            for f in sorted(sym_dir.glob("*.jsonl")):
                for line in open(f, encoding="utf-8"):
                    line = line.strip()
                    if not line: continue
                    try:
                        rec = json.loads(line)
                    except: continue
                    eid = rec.get("entity_id", "")
                    if eid and eid not in dt_by_entity:
                        dt_by_entity[eid] = rec
    
    # ═══════════════════════════════════════════════════════════════
    # INVESTIGATION 1: Execution vs Opportunity Selection
    # ═══════════════════════════════════════════════════════════════
    out.append("━" * 70)
    out.append("INVESTIGATION 1: EXECUTION SELECTION QUALITY")
    out.append("━" * 70)
    out.append("")
    
    # Live executed trades
    live_trades = exe_builder.records
    live_r = [r["r_multiple"] for r in live_trades if r.get("r_multiple") is not None]
    
    # V10_PRIMARY shadows (same trades, shadow model)
    primary_shadows = shadow_builder.get_population(Population.PRIMARY_V10_SHADOW)
    primary_r = [r["r_multiple"] for r in primary_shadows if r.get("r_multiple") is not None]
    
    # Horizon alternatives for NO_TRADE
    no_trade_shadows = shadow_builder.get_population(Population.SHADOW_FROM_NO_TRADE)
    no_trade_r = [r["r_multiple"] for r in no_trade_shadows if r.get("r_multiple") is not None]
    
    out.append(f"  LIVE executed trades: n={len(live_r)}, mean_R={statistics.mean(live_r):+.4f}, win_rate={len([r for r in live_r if r>0])/len(live_r):.1%}")
    out.append(f"  V10_PRIMARY shadows:  n={len(primary_r)}, mean_R={statistics.mean(primary_r):+.4f}, win_rate={len([r for r in primary_r if r>0])/len(primary_r):.1%}")
    out.append(f"  NO_TRADE shadows:     n={len(no_trade_r)}, mean_R={statistics.mean(no_trade_r):+.4f}, win_rate={len([r for r in no_trade_r if r>0])/len(no_trade_r):.1%}")
    out.append("")
    
    # Key insight
    out.append(f"  KEY FINDING:")
    out.append(f"    V10_PRIMARY (V10 geometry) mean = {statistics.mean(primary_r):+.4f}R")
    out.append(f"    Live realised mean = {statistics.mean(live_r):+.4f}R")
    if primary_r and live_r:
        leakage = statistics.mean(primary_r) - statistics.mean(live_r)
        out.append(f"    Execution leakage (shadow - live) = {leakage:+.4f}R")
        if leakage > 0:
            out.append(f"    → V10's geometry WORKS mechanically but execution/management loses {leakage:.4f}R")
        else:
            out.append(f"    → Live execution performs BETTER than pure SL/TP/timeout model")
    out.append("")
    
    # ═══════════════════════════════════════════════════════════════
    # INVESTIGATION 2: Strategy Segmentation (Shadow)
    # ═══════════════════════════════════════════════════════════════
    out.append("━" * 70)
    out.append("INVESTIGATION 2: STRATEGY PERFORMANCE (COUNTERFACTUAL)")
    out.append("━" * 70)
    out.append("")
    
    all_shadows = shadow_builder.get_population(Population.ALL_SHADOW_OUTCOMES)
    by_strategy = defaultdict(list)
    for r in all_shadows:
        strat = r.get("strategy_id", "") or r.get("pattern", "") or "UNKNOWN"
        if strat and r.get("r_multiple") is not None:
            by_strategy[strat].append(r["r_multiple"])
    
    out.append(f"  {'Strategy':<25} {'Count':>6} {'Mean R':>8} {'Win%':>6} {'Total R':>9}")
    out.append(f"  {'─'*25} {'─'*6} {'─'*8} {'─'*6} {'─'*9}")
    for strat, values in sorted(by_strategy.items(), key=lambda x: -statistics.mean(x[1]) if len(x[1]) >= 5 else -999):
        if len(values) < 5: continue
        mean = statistics.mean(values)
        wr = len([v for v in values if v > 0]) / len(values)
        total = sum(values)
        out.append(f"  {strat:<25} {len(values):>6} {mean:>+8.4f} {wr:>5.0%} {total:>+9.2f}")
    out.append("")
    
    # ═══════════════════════════════════════════════════════════════
    # INVESTIGATION 3: Regime Segmentation (Shadow)
    # ═══════════════════════════════════════════════════════════════
    out.append("━" * 70)
    out.append("INVESTIGATION 3: REGIME PERFORMANCE (COUNTERFACTUAL)")
    out.append("━" * 70)
    out.append("")
    
    by_regime = defaultdict(list)
    for r in all_shadows:
        regime = r.get("regime", "") or "UNKNOWN"
        if regime and r.get("r_multiple") is not None:
            by_regime[regime].append(r["r_multiple"])
    
    out.append(f"  {'Regime':<20} {'Count':>6} {'Mean R':>8} {'Win%':>6} {'Total R':>9}")
    out.append(f"  {'─'*20} {'─'*6} {'─'*8} {'─'*6} {'─'*9}")
    for regime, values in sorted(by_regime.items(), key=lambda x: -len(x[1])):
        if len(values) < 3: continue
        mean = statistics.mean(values)
        wr = len([v for v in values if v > 0]) / len(values)
        total = sum(values)
        out.append(f"  {regime:<20} {len(values):>6} {mean:>+8.4f} {wr:>5.0%} {total:>+9.2f}")
    out.append("")
    
    # ═══════════════════════════════════════════════════════════════
    # INVESTIGATION 4: Horizon Performance
    # ═══════════════════════════════════════════════════════════════
    out.append("━" * 70)
    out.append("INVESTIGATION 4: HORIZON PERFORMANCE (COUNTERFACTUAL)")
    out.append("━" * 70)
    out.append("")
    
    by_horizon = defaultdict(list)
    for r in all_shadows:
        hz = r.get("trade_horizon") or r.get("evaluated_horizon") or "UNKNOWN"
        if r.get("r_multiple") is not None:
            by_horizon[hz].append(r["r_multiple"])
    
    out.append(f"  {'Horizon':<15} {'Count':>6} {'Mean R':>8} {'Win%':>6} {'Total R':>9} {'TP%':>5} {'SL%':>5} {'TO%':>5}")
    out.append(f"  {'─'*15} {'─'*6} {'─'*8} {'─'*6} {'─'*9} {'─'*5} {'─'*5} {'─'*5}")
    
    for hz in ["SCALP", "INTRADAY", "EXTENDED", "UNKNOWN"]:
        values = by_horizon.get(hz, [])
        if not values: continue
        mean = statistics.mean(values)
        wr = len([v for v in values if v > 0]) / len(values)
        total = sum(values)
        # Exit reasons for this horizon
        hz_records = [r for r in all_shadows if (r.get("trade_horizon") or r.get("evaluated_horizon") or "UNKNOWN") == hz]
        exits = Counter(r.get("exit_reason", "?") for r in hz_records)
        tp_pct = exits.get("take_profit", 0) / max(len(hz_records), 1)
        sl_pct = exits.get("stop_loss", 0) / max(len(hz_records), 1)
        to_pct = exits.get("max_bars_timeout", 0) / max(len(hz_records), 1)
        out.append(f"  {hz:<15} {len(values):>6} {mean:>+8.4f} {wr:>5.0%} {total:>+9.2f} {tp_pct:>4.0%} {sl_pct:>4.0%} {to_pct:>4.0%}")
    out.append("")
    
    # ═══════════════════════════════════════════════════════════════
    # INVESTIGATION 5: Score vs Shadow Outcome
    # ═══════════════════════════════════════════════════════════════
    out.append("━" * 70)
    out.append("INVESTIGATION 5: DOES SCORE PREDICT SHADOW OUTCOME?")
    out.append("━" * 70)
    out.append("")
    
    # For shadows with score, bucket and compare
    scored_shadows = [(r["score"], r["r_multiple"]) for r in all_shadows 
                      if r.get("score") is not None and r.get("r_multiple") is not None and r["score"] > 0]
    scored_shadows.sort(key=lambda x: x[0])
    
    if len(scored_shadows) >= 20:
        bucket_size = len(scored_shadows) // 4
        out.append(f"  Total scored shadow observations: {len(scored_shadows)}")
        out.append(f"  {'Bucket':<10} {'Score Range':<20} {'Count':>6} {'Mean R':>8} {'Win%':>6}")
        out.append(f"  {'─'*10} {'─'*20} {'─'*6} {'─'*8} {'─'*6}")
        
        for i in range(4):
            start = i * bucket_size
            end = start + bucket_size if i < 3 else len(scored_shadows)
            chunk = scored_shadows[start:end]
            if not chunk: continue
            scores = [s for s, _ in chunk]
            rs = [r for _, r in chunk]
            score_range = f"{min(scores):.3f}-{max(scores):.3f}"
            mean_r = statistics.mean(rs)
            wr = len([r for r in rs if r > 0]) / len(rs)
            out.append(f"  Q{i+1:<8} {score_range:<20} {len(chunk):>6} {mean_r:>+8.4f} {wr:>5.0%}")
        
        # Monotonicity
        bucket_means = []
        for i in range(4):
            start = i * bucket_size
            end = start + bucket_size if i < 3 else len(scored_shadows)
            chunk = scored_shadows[start:end]
            if chunk:
                bucket_means.append(statistics.mean([r for _, r in chunk]))
        monotonic = all(bucket_means[i] <= bucket_means[i+1] for i in range(len(bucket_means)-1))
        out.append(f"  Monotonic (higher score → higher R): {monotonic}")
        if len(bucket_means) >= 2:
            out.append(f"  Top-bottom spread: {bucket_means[-1] - bucket_means[0]:+.4f}R")
    else:
        out.append(f"  Insufficient scored shadows: {len(scored_shadows)}")
    out.append("")
    
    # ═══════════════════════════════════════════════════════════════
    # INVESTIGATION 6: Rejection Stage Deep Dive
    # ═══════════════════════════════════════════════════════════════
    out.append("━" * 70)
    out.append("INVESTIGATION 6: REJECTION STAGE COUNTERFACTUAL R")
    out.append("━" * 70)
    out.append("")
    
    # Join shadows to decisions for terminal_reason
    joined = []
    for r in no_trade_shadows:
        eid = r.get("entity_id", "")
        if eid and eid in dt_by_entity:
            dt = dt_by_entity[eid]
            reason = dt.get("terminal_reason", "") or ""
            # Simplify
            if "opportunity" in reason.lower():
                stage = "OPPORTUNITY"
            elif "strategy" in reason.lower():
                stage = "STRATEGY"
            elif "entry" in reason.lower():
                stage = "ENTRY"
            elif "risk" in reason.lower():
                stage = "RISK"
            elif "exec" in reason.lower():
                stage = "EXECUTION"
            else:
                stage = "OTHER"
            joined.append({"stage": stage, "r": r["r_multiple"]})
    
    by_stage = defaultdict(list)
    for j in joined:
        by_stage[j["stage"]].append(j["r"])
    
    if by_stage:
        out.append(f"  {'Stage':<15} {'Count':>6} {'Mean R':>8} {'Win%':>6} {'Total R':>9} {'Interpretation'}")
        out.append(f"  {'─'*15} {'─'*6} {'─'*8} {'─'*6} {'─'*9} {'─'*30}")
        for stage in ["OPPORTUNITY", "STRATEGY", "ENTRY", "RISK", "EXECUTION", "OTHER"]:
            values = by_stage.get(stage, [])
            if len(values) < 3: continue
            mean = statistics.mean(values)
            wr = len([v for v in values if v > 0]) / len(values)
            total = sum(values)
            interp = "← PROTECTING" if mean < 0 else "← DESTROYING EDGE" if mean > 0.05 else "← NEUTRAL"
            out.append(f"  {stage:<15} {len(values):>6} {mean:>+8.4f} {wr:>5.0%} {total:>+9.2f} {interp}")
    out.append("")
    
    # ═══════════════════════════════════════════════════════════════
    # SYNTHESIS: HYPOTHESES
    # ═══════════════════════════════════════════════════════════════
    out.append("━" * 70)
    out.append("RESEARCH SYNTHESIS: HYPOTHESES FOR TESTING")
    out.append("━" * 70)
    out.append("")
    
    out.append("Based on the above investigations:")
    out.append("")
    out.append("HYPOTHESIS 1: V10 EXECUTION LEAKAGE")
    if primary_r and live_r:
        leakage = statistics.mean(primary_r) - statistics.mean(live_r)
        if leakage > 0.1:
            out.append(f"  V10's trade geometry works (+{statistics.mean(primary_r):.4f}R shadow) but")
            out.append(f"  live execution loses {leakage:.4f}R — suggesting trade management,")
            out.append(f"  slippage, or timing degrades outcomes.")
            out.append(f"  EXPERIMENT: Compare shadow exit timing vs live exit timing.")
        elif leakage < -0.1:
            out.append(f"  Live execution OUTPERFORMS shadow model by {-leakage:.4f}R.")
            out.append(f"  Trade management (trailing, BE) adds value.")
        else:
            out.append(f"  Execution leakage is minimal ({leakage:+.4f}R). Problem is upstream.")
    out.append("")
    
    out.append("HYPOTHESIS 2: SCORE DOES NOT SELECT WELL")
    out.append(f"  D-001: Score is NOT_PREDICTIVE of live R.")
    out.append(f"  Investigation 5: Score {'DOES' if monotonic else 'does NOT'} predict shadow R.")
    if not monotonic:
        out.append(f"  → Score may be fundamentally miscalibrated (not just noisy).")
        out.append(f"  EXPERIMENT: Test whether removing score threshold improves shadow expectancy.")
    else:
        out.append(f"  → Score predicts opportunity quality but V10 selects from the wrong end.")
    out.append("")
    
    out.append("HYPOTHESIS 3: REGIME/STRATEGY INTERACTION")
    out.append(f"  Some strategy×regime combinations may be positive while others are negative.")
    out.append(f"  EXPERIMENT: POPULATION_FILTER excluding worst-performing strategy×regime.")
    out.append("")
    
    out.append("HYPOTHESIS 4: HORIZON SELECTION SUBOPTIMAL")
    hz_means = {hz: statistics.mean(vals) for hz, vals in by_horizon.items() if len(vals) >= 10}
    if hz_means:
        best_hz = max(hz_means, key=hz_means.get)
        worst_hz = min(hz_means, key=hz_means.get)
        out.append(f"  Best horizon: {best_hz} ({hz_means[best_hz]:+.4f}R)")
        out.append(f"  Worst horizon: {worst_hz} ({hz_means[worst_hz]:+.4f}R)")
        out.append(f"  EXPERIMENT: Test preferring {best_hz} over {worst_hz}.")
    out.append("")
    
    # ═══════════════════════════════════════════════════════════════
    # CANDIDATE EXPERIMENTS
    # ═══════════════════════════════════════════════════════════════
    out.append("━" * 70)
    out.append("PROPOSED CANDIDATE EXPERIMENTS")
    out.append("━" * 70)
    out.append("")
    out.append("(These are proposals — NOT implemented)")
    out.append("")
    
    # Find worst strategy
    worst_strategies = [(s, statistics.mean(v)) for s, v in by_strategy.items() if len(v) >= 10 and statistics.mean(v) < -0.1]
    if worst_strategies:
        worst = min(worst_strategies, key=lambda x: x[1])
        out.append(f"EXPERIMENT A: Exclude worst strategy '{worst[0]}' (mean shadow R = {worst[1]:+.4f})")
        out.append(f"  Filter: strategy_id != '{worst[0]}'")
        out.append(f"  Population: ALL_SHADOW_OUTCOMES")
        out.append(f"  Baseline: {statistics.mean([r['r_multiple'] for r in all_shadows if r.get('r_multiple') is not None]):+.4f}R")
        out.append("")
    
    # Find worst regime
    worst_regimes = [(reg, statistics.mean(v)) for reg, v in by_regime.items() if len(v) >= 10 and statistics.mean(v) < -0.1]
    if worst_regimes:
        worst_reg = min(worst_regimes, key=lambda x: x[1])
        out.append(f"EXPERIMENT B: Exclude worst regime '{worst_reg[0]}' (mean shadow R = {worst_reg[1]:+.4f})")
        out.append(f"  Filter: regime != '{worst_reg[0]}'")
        out.append(f"  Population: ALL_SHADOW_OUTCOMES")
        out.append("")
    
    out.append("─" * 70)
    out.append("END OF INVESTIGATIVE CYCLE #1")
    out.append("─" * 70)
    out.append("")
    out.append("NEXT STEPS:")
    out.append("1. Review hypotheses with human researcher")
    out.append("2. Select most promising experiment")
    out.append("3. Run POPULATION_FILTER candidate experiment")
    out.append("4. Validate statistical significance")
    out.append("5. IF validated → propose to human for approval")
    out.append("")
    out.append("NO V10 CHANGES HAVE BEEN MADE.")
    
    output = "\n".join(out)
    Path("reports/research/baseline/investigative_cycle_1.txt").write_text(output, encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
