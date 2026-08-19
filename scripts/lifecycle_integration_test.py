"""
RESEARCH LIFECYCLE INTEGRATION TEST — TBC/TWS Direction Inversion

End-to-end demonstration of the autonomous governed research lifecycle:
1. DETECT & REGISTER: Create hypothesis from TBC/TWS failure finding
2. EXPERIMENT: Run full 60-bar inversion simulation via canonical shadow methodology
3. VALIDATE: OOS split, bootstrap CI, permutation test, symbol/temporal robustness
4. PLACEBO: Run inversion on all other patterns as negative control
5. CONCLUDE: Apply governed decision logic (Bonferroni, placebo gate, OOS gate)
6. KNOWLEDGE MAP: Persist conclusion
7. REPORT: Generate complete human-readable research report

Uses the real V10 shadow trade dataset and MT5 historical candles.
Does NOT modify production strategy code.
"""
import sys
import json
import statistics
import random
from pathlib import Path
from collections import defaultdict, Counter
from datetime import datetime as _dt, timezone as _tz

sys.path.insert(0, ".")

from research_engine.lifecycle.orchestrator import ResearchOrchestrator
from research_engine.lifecycle.hypothesis import HypothesisCategory, HypothesisStatus, ConclusionType
from research_engine.lifecycle.experiment_protocol import (
    ExperimentDefinition, ExperimentResult, ExperimentType,
    PopulationSpec, SimulationSpec, ValidationSpec,
)
from research_engine.lifecycle.validation_harness import (
    bootstrap_ci, compute_full_validation, permutation_test,
)
from research_engine.lifecycle.placebo_controller import run_placebo_test, PlaceboTestOutcome

# ═══════════════════════════════════════════════════════════════════════════════
# SHADOW SIMULATION (same canonical method as ShadowTradeEngine)
# ═══════════════════════════════════════════════════════════════════════════════

MAX_BARS = 60

def simulate_trade(*, direction, entry_price, stop_loss, take_profit, candles):
    risk = abs(entry_price - stop_loss)
    if risk == 0:
        return {"r_multiple": 0, "exit_reason": "zero_risk", "bars_held": 0, "mfe_r": 0, "mae_r": 0}
    max_fav, max_adv = entry_price, entry_price
    exit_price, exit_reason, bars_held = None, "", 0
    for candle in candles[:MAX_BARS]:
        bars_held += 1
        bh, bl, bc = candle["high"], candle["low"], candle["close"]
        if direction == "BUY":
            max_fav, max_adv = max(max_fav, bh), min(max_adv, bl)
            if bl <= stop_loss: exit_price, exit_reason = stop_loss, "stop_loss"; break
            elif bh >= take_profit: exit_price, exit_reason = take_profit, "take_profit"; break
        else:
            max_fav, max_adv = min(max_fav, bl), max(max_adv, bh)
            if bh >= stop_loss: exit_price, exit_reason = stop_loss, "stop_loss"; break
            elif bl <= take_profit: exit_price, exit_reason = take_profit, "take_profit"; break
    if exit_price is None:
        exit_price = candles[min(MAX_BARS-1, len(candles)-1)]["close"] if candles else entry_price
        exit_reason, bars_held = "max_bars_timeout", min(MAX_BARS, len(candles))
    pnl = (exit_price - entry_price) if direction == "BUY" else (entry_price - exit_price)
    r_mult = round(pnl / risk, 4)
    mfe = max(0, (max_fav - entry_price) if direction == "BUY" else (entry_price - max_fav)) / risk
    mae = max(0, (entry_price - max_adv) if direction == "BUY" else (max_adv - entry_price)) / risk
    return {"r_multiple": r_mult, "exit_reason": exit_reason, "bars_held": bars_held,
            "mfe_r": round(mfe, 4), "mae_r": round(mae, 4)}

def load_candles(symbol, start_time):
    import MetaTrader5 as mt5
    from datetime import datetime, timezone
    dt_s = datetime.fromtimestamp(start_time + 1, tz=timezone.utc)
    dt_e = datetime.fromtimestamp(start_time + 20000, tz=timezone.utc)
    rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M5, dt_s, dt_e)
    if rates is None or len(rates) == 0: return []
    return [{"high": float(rates[i][2]), "low": float(rates[i][3]), "close": float(rates[i][4])}
            for i in range(min(65, len(rates)))]

def get_session(ts):
    h = _dt.fromtimestamp(ts, tz=_tz.utc).hour
    if 7 <= h < 12: return "LONDON"
    elif 12 <= h < 17: return "NY"
    elif 0 <= h < 7: return "ASIA"
    return "OFF_SESSION"

# ═══════════════════════════════════════════════════════════════════════════════
# DATA LOADING
# ═══════════════════════════════════════════════════════════════════════════════

def load_shadow_population():
    """Load and parse all shadow trade records."""
    raw = []
    for f in Path("logs/shadow_trades").rglob("*.jsonl"):
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try: raw.append(json.loads(line))
                except: pass

    def extract(rec):
        if "identity" in rec:
            i, s, o = rec["identity"], rec["decision_snapshot"], rec.get("simulated_outcome", {})
            return {"symbol": i.get("symbol",""), "cid": i.get("correlation_id",""),
                    "dir": s.get("direction",""), "entry": s.get("entry_intent_price",0),
                    "sl": s.get("stop_loss_intent",0), "tp": s.get("take_profit_intent",0),
                    "time": s.get("timestamp_decision_utc",0), "pattern": s.get("pattern",""),
                    "score": s.get("score",0)}
        return {"symbol": rec.get("symbol",""), "cid": rec.get("correlation_id",""),
                "dir": rec.get("direction",""), "entry": rec.get("entry_price",0),
                "sl": rec.get("stop_loss",0), "tp": rec.get("take_profit",0),
                "time": rec.get("entry_time",0), "pattern": rec.get("pattern",""),
                "score": rec.get("score",0)}

    # Deduplicate
    seen = set()
    result = []
    for r in raw:
        p = extract(r)
        if not (p["cid"] and p["entry"] and p["sl"]): continue
        key = (p["symbol"], p["time"], p["pattern"], p["dir"])
        if key not in seen:
            seen.add(key)
            result.append(p)
    return result

# ═══════════════════════════════════════════════════════════════════════════════
# EXPERIMENT EXECUTION FUNCTION
# ═══════════════════════════════════════════════════════════════════════════════

def run_inversion_experiment(experiment: ExperimentDefinition) -> ExperimentResult:
    """
    Execute the direction-inversion experiment using canonical shadow methodology.
    This is the function passed to orchestrator.run_experiment().
    """
    random.seed(42)
    population = load_shadow_population()

    # Filter to target patterns
    filtered = [p for p in population if p["pattern"] in experiment.population.pattern_filter]

    # Run simulation
    results = []
    orig_results = []
    population_records = []  # Track actual records for fingerprinting
    for p in filtered:
        risk = abs(p["entry"] - p["sl"])
        if risk <= 0: continue
        candles = load_candles(p["symbol"], p["time"])
        if len(candles) < 10: continue

        # Inverted direction
        inv_dir = "BUY" if p["dir"] == "SELL" else "SELL"
        sl_mult = experiment.simulation.stop_multiplier
        tp_mult = experiment.simulation.tp_multiplier
        if inv_dir == "BUY":
            new_sl = p["entry"] - risk * sl_mult
            new_tp = p["entry"] + risk * tp_mult
        else:
            new_sl = p["entry"] + risk * sl_mult
            new_tp = p["entry"] - risk * tp_mult

        inv_res = simulate_trade(direction=inv_dir, entry_price=p["entry"],
                                  stop_loss=new_sl, take_profit=new_tp, candles=candles)
        orig_res = simulate_trade(direction=p["dir"], entry_price=p["entry"],
                                   stop_loss=p["sl"], take_profit=p["tp"], candles=candles)

        results.append({**inv_res, "symbol": p["symbol"], "time": p["time"],
                       "session": get_session(p["time"]), "score": p["score"]})
        orig_results.append(orig_res)
        population_records.append(p)
        return ExperimentResult(experiment_id=experiment.experiment_id,
                                hypothesis_id=experiment.hypothesis_id, status="failed")

    # Compute full validation
    r_vals = [r["r_multiple"] for r in results]
    orig_vals = [r["r_multiple"] for r in orig_results]
    validation = compute_full_validation(results, r_field="r_multiple",
                                          time_field="time", symbol_field="symbol")

    # Permutation test (inverted vs original — PAIRED, same observations)
    from research_engine.lifecycle.validation_harness import permutation_test_paired
    p_value = permutation_test_paired(r_vals, orig_vals, n_perms=5000, seed=42)

    # Exit distribution
    exits = Counter(r["exit_reason"] for r in results)
    n = len(results)

    # Build result
    ci_lo, ci_hi = bootstrap_ci(r_vals, seed=42)

    return ExperimentResult(
        experiment_id=experiment.experiment_id,
        hypothesis_id=experiment.hypothesis_id,
        status="complete",
        n=len(r_vals),
        mean_r=statistics.mean(r_vals),
        median_r=statistics.median(r_vals),
        total_r=sum(r_vals),
        win_rate=sum(1 for v in r_vals if v > 0) / len(r_vals),
        std_dev=statistics.stdev(r_vals) if len(r_vals) > 1 else 0,
        ci_lower=ci_lo,
        ci_upper=ci_hi,
        permutation_p=p_value,
        sl_rate=exits.get("stop_loss", 0) / n,
        tp_rate=exits.get("take_profit", 0) / n,
        timeout_rate=exits.get("max_bars_timeout", 0) / n,
        mean_mfe=statistics.mean([r["mfe_r"] for r in results]),
        mean_mae=statistics.mean([r["mae_r"] for r in results]),
        oos_n=validation.get("oos_n", 0),
        oos_mean_r=validation.get("oos_mean_r", 0),
        oos_ci_lower=validation.get("oos_ci_lower"),
        oos_ci_upper=validation.get("oos_ci_upper"),
        symbols_positive=validation.get("symbols_positive", 0),
        symbols_total=validation.get("symbols_total", 0),
        survives_best_symbol_removal=validation.get("survives_best_removal", False),
        survives_top10_removal=validation.get("survives_top10", False),
        survives_top20_removal=validation.get("survives_top20", False),
        top10_contribution_pct=validation.get("top10_contribution_pct", 0),
        periods_positive=validation.get("periods_positive", 0),
        periods_total=validation.get("periods_total", 5),
        timestamp=_dt.now(tz=_tz.utc).isoformat(),
        dataset_fingerprint=_build_fp(population_records, experiment),
    )


def _build_fp(records, experiment):
    """Build dataset fingerprint for the experiment population."""
    from research_engine.lifecycle.dataset_fingerprint import build_dataset_fingerprint
    fp = build_dataset_fingerprint(
        records,
        dataset_id="V10_PRIMARY_TBC_TWS",
        dataset_version="shadow_trades_v2",
        population=experiment.title,
        schema_version="shadow_trades_v2",
        filters_applied=[f"pattern_filter={experiment.population.pattern_filter}",
                         "require_correlation_id=True"],
        time_field="time",
    )
    return fp.to_dict()


def run_placebo_experiment(population_by_pattern, pattern_name) -> list[float]:
    """Placebo function: invert any given pattern and return R values."""
    filtered = population_by_pattern
    results = []
    for p in filtered[:80]:  # Cap for speed
        risk = abs(p["entry"] - p["sl"])
        if risk <= 0: continue
        candles = load_candles(p["symbol"], p["time"])
        if len(candles) < 10: continue
        inv_dir = "BUY" if p["dir"] == "SELL" else "SELL"
        if inv_dir == "BUY":
            new_sl, new_tp = p["entry"] - risk, p["entry"] + risk * 3.0
        else:
            new_sl, new_tp = p["entry"] + risk, p["entry"] - risk * 3.0
        r = simulate_trade(direction=inv_dir, entry_price=p["entry"],
                            stop_loss=new_sl, take_profit=new_tp, candles=candles)
        results.append(r["r_multiple"])
    return results


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN — END-TO-END LIFECYCLE EXECUTION
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    print("=" * 80)
    print("RESEARCH LIFECYCLE INTEGRATION TEST")
    print("TBC/TWS Direction Inversion Hypothesis")
    print("=" * 80)
    print()

    import MetaTrader5 as mt5
    mt5.initialize()

    # ─── STEP 1: DETECT & REGISTER ───────────────────────────────────
    print("STEP 1: Detecting and registering hypothesis...")
    orch = ResearchOrchestrator()

    hypothesis = orch.detect_and_register(
        title="THREE_BLACK_CROWS/THREE_WHITE_SOLDIERS contain reversal information",
        description=(
            "TBC and TWS patterns currently produce -1R outcomes (100% SL hit). "
            "Initial investigation suggests they identify exhaustion points where "
            "the opposite direction (fading the move) may be profitable."
        ),
        claim=(
            "Inverting the direction of TBC (→BUY) and TWS (→SELL) produces positive "
            "expected value over a 60-bar horizon using the canonical shadow methodology."
        ),
        null_hypothesis=(
            "The inverted direction produces no better results than the original direction, "
            "OR the improvement is a general dataset property (not pattern-specific)."
        ),
        category=HypothesisCategory.DIRECTION_INVERSION,
        source="Baseline research: three_candle_failure_analysis",
        population_description="All V10_PRIMARY shadow trades with pattern THREE_BLACK_CROWS or THREE_WHITE_SOLDIERS",
        falsification_conditions=[
            "OOS validation period shows mean R <= 0",
            "Permutation test p-value fails Bonferroni correction (p > 0.002)",
            "Majority (>50%) of placebo patterns also show positive inverted R",
            "Effect concentrated in <3 symbols",
            "Effect disappears after removing top-20 outliers",
        ],
        discovery_bias_notes=(
            "Hypothesis discovered after testing 6 stop widths × 2 patterns × 2 directions = 24 variants. "
            "Only inverted direction at 1R stop showed strong positive."
        ),
        multiple_testing_count=24,
        tags=["pattern_signal", "direction_inversion", "exhaustion", "tbc", "tws"],
    )

    print(f"  Registered: {hypothesis.hypothesis_id}")
    print(f"  Status: {hypothesis.status.value}")
    print(f"  Bonferroni threshold: p < {hypothesis.bonferroni_threshold:.4f}")
    print()

    # ─── STEP 2: DEFINE & RUN PRIMARY EXPERIMENT ──────────────────────
    print("STEP 2: Running primary experiment (TBC+TWS combined inversion)...")

    experiment_def = ExperimentDefinition(
        hypothesis_id=hypothesis.hypothesis_id,
        experiment_type=ExperimentType.DIRECTION_INVERSION,
        title="TBC+TWS combined direction inversion (1R stop, 3R TP, 60-bar horizon)",
        description="Invert TBC→BUY and TWS→SELL, using same entry/risk, over canonical 60 bars",
        population=PopulationSpec(
            pattern_filter=["THREE_BLACK_CROWS", "THREE_WHITE_SOLDIERS"],
            require_correlation_id=True,
            min_sample_size=30,
        ),
        simulation=SimulationSpec(
            direction="INVERT",
            stop_multiplier=1.0,
            tp_multiplier=3.0,
            max_bars=60,
        ),
        validation=ValidationSpec(
            oos_split=0.6,
            bootstrap_n=2000,
            bootstrap_ci=0.90,
            permutation_n=5000,
            bonferroni_tests=24,
            min_symbols_positive=3,
        ),
    )

    result = orch.run_experiment(hypothesis, experiment_def, run_inversion_experiment)

    print(f"  N: {result.n}")
    print(f"  Mean R: {result.mean_r:+.4f}")
    print(f"  Win Rate: {result.win_rate:.1%}")
    print(f"  90% CI: [{result.ci_lower:+.3f}, {result.ci_upper:+.3f}]" if result.ci_lower else "")
    print(f"  Permutation p: {result.permutation_p:.4f}" if result.permutation_p else "")
    print(f"  OOS Mean R: {result.oos_mean_r:+.4f} (N={result.oos_n})")
    print(f"  Symbols positive: {result.symbols_positive}/{result.symbols_total}")
    print(f"  Temporal stability: {result.periods_positive}/{result.periods_total} periods positive")
    if result.dataset_fingerprint:
        fp = result.dataset_fingerprint
        print(f"  Dataset fingerprint: {fp.get('content_hash', '')[:16]}... ({fp.get('observation_count')} obs)")
    print()

    # ─── STEP 3: PLACEBO CONTROL ─────────────────────────────────────
    print("STEP 3: Running placebo control (invert ALL other patterns)...")

    all_pop = load_shadow_population()
    # Build control populations (other patterns)
    control_pops = defaultdict(list)
    target_patterns = {"THREE_BLACK_CROWS", "THREE_WHITE_SOLDIERS"}
    for p in all_pop:
        if p["pattern"] and p["pattern"] not in target_patterns:
            control_pops[p["pattern"]].append(p)

    # Only use patterns with enough data
    eligible_controls = {pat: recs for pat, recs in control_pops.items() if len(recs) >= 20}

    placebo_outcome = run_placebo_test(
        hypothesis_id=hypothesis.hypothesis_id,
        experiment_fn=run_placebo_experiment,
        control_populations=eligible_controls,
        min_n=15,
        positive_threshold=0.5,
    )

    print(f"  Placebo patterns tested: {placebo_outcome.total_placebos}")
    print(f"  Positive placebos: {placebo_outcome.positive_placebos}/{placebo_outcome.total_placebos}")
    print(f"  Fraction: {placebo_outcome.positive_fraction:.2%}")
    print(f"  Placebo PASSES: {placebo_outcome.placebo_passes}")
    print(f"  {placebo_outcome.interpretation}")
    print()

    # Transfer placebo results to ExperimentResult
    result.placebo_positive_fraction = placebo_outcome.positive_fraction
    result.placebo_patterns_tested = placebo_outcome.total_placebos
    result.placebo_passes = placebo_outcome.placebo_passes

    # ─── STEP 4: CHALLENGE ────────────────────────────────────────────
    print("STEP 4: Challenging hypothesis with validation evidence...")
    orch.challenge(hypothesis, result, placebo_outcome)
    print(f"  Status: {hypothesis.status.value}")
    print()

    # ─── STEP 5: CONCLUDE ─────────────────────────────────────────────
    print("STEP 5: Reaching governed conclusion...")
    conclusion = orch.conclude(hypothesis, result, placebo_outcome)
    print(f"  Conclusion: {conclusion.value}")
    print(f"  Reason: {hypothesis.conclusion_reason}")
    print(f"  Confidence: {hypothesis.conclusion_confidence}")
    print(f"  Classification: {result.classification}")
    print()

    # ─── STEP 6: KNOWLEDGE MAP ────────────────────────────────────────
    print("STEP 6: Updating knowledge map...")
    orch.update_knowledge_map(hypothesis, result)
    print(f"  Written to: analysis/summaries/research_knowledge.json")
    print()

    # ─── STEP 7: GENERATE REPORT ─────────────────────────────────────
    print("STEP 7: Generating human-readable report...")
    report = orch.generate_report(hypothesis, result, placebo_outcome)
    print(f"  Report saved to: reports/research/lifecycle/{hypothesis.hypothesis_id}_report.md")
    print()

    # ─── STEP 8: GOVERNANCE GATE CHECK ────────────────────────────────
    print("STEP 8: Checking governance gate...")
    eligible, reason = orch.gate.can_promote(hypothesis)
    print(f"  Eligible for promotion: {eligible}")
    print(f"  Reason: {reason}")
    print()

    # ─── SUMMARY ──────────────────────────────────────────────────────
    print("=" * 80)
    print("LIFECYCLE EXECUTION COMPLETE")
    print("=" * 80)
    print()
    print(f"  Hypothesis: {hypothesis.hypothesis_id}")
    print(f"  Title: {hypothesis.title}")
    print(f"  Final Status: {hypothesis.status.value}")
    print(f"  Conclusion: {hypothesis.conclusion_type.value if hypothesis.conclusion_type else 'NONE'}")
    print(f"  Classification: {result.classification}")
    print(f"  Experiments run: {len(hypothesis.experiments)}")
    print(f"  Transitions: {len(hypothesis.transitions)}")
    print()
    print("  AUDIT TRAIL:")
    for t in hypothesis.transitions:
        print(f"    {t.timestamp[:19]} | {t.from_status} → {t.to_status} | {t.reason[:60]}")
    print()

    # Verify registry persistence
    registry = orch.registry
    loaded = registry.get(hypothesis.hypothesis_id)
    if loaded:
        print(f"  ✓ Hypothesis persisted in registry (status: {loaded.status.value})")
    else:
        print(f"  ✗ Hypothesis NOT found in registry")

    # Verify knowledge map
    km_path = Path("analysis/summaries/research_knowledge.json")
    if km_path.exists():
        km = json.loads(km_path.read_text(encoding="utf-8"))
        lf = km.get("lifecycle_findings", {})
        if hypothesis.hypothesis_id in lf:
            print(f"  ✓ Finding persisted in knowledge map (lifecycle_findings)")
        else:
            print(f"  ✗ Finding NOT in knowledge map lifecycle_findings")
        # Verify existing entries preserved
        existing_findings = km.get("findings", {})
        if isinstance(existing_findings, dict) and "Q1" in existing_findings:
            print(f"  ✓ Existing knowledge map entries preserved ({len(existing_findings)} findings)")
        elif existing_findings:
            print(f"  ✓ Existing findings structure present ({type(existing_findings).__name__})")
    print()

    # ─── STEP 9: RESTART PERSISTENCE ─────────────────────────────────
    print("STEP 9: Verifying restart persistence...")
    from research_engine.lifecycle.registry import InvestigationRegistry
    fresh_registry = InvestigationRegistry()
    reloaded = fresh_registry.get(hypothesis.hypothesis_id)
    if reloaded:
        print(f"  ✓ Hypothesis survives restart (status: {reloaded.status.value})")
        print(f"  ✓ Conclusion preserved: {reloaded.conclusion_type.value if reloaded.conclusion_type else 'None'}")
        print(f"  ✓ Experiments preserved: {len(reloaded.experiments)}")
        print(f"  ✓ Transitions preserved: {len(reloaded.transitions)}")
    else:
        print(f"  ✗ Hypothesis NOT found after reload")
    print()

    mt5.shutdown()

    # Print the full report for verification
    print("─" * 80)
    print("GENERATED REPORT:")
    print("─" * 80)
    print(report)


if __name__ == "__main__":
    main()
