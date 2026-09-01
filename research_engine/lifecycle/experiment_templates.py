"""
Experiment Template Registry — Canonical execution methodologies for each ExperimentType.

Eliminates the need for callers to provide a custom execute_fn per investigation.
Each template defines:
    - Required parameters and population constraints
    - Control/treatment construction logic
    - Canonical shadow simulation execution
    - Required validation methods
    - Applicable placebo methodology

All templates reuse the canonical ShadowTradeEngine methodology:
    - SL checked before TP (conservative)
    - 60-bar M5 horizon (configurable)
    - R = pnl / abs(entry - sl)
    - MFE/MAE tracked each bar

This module NEVER modifies production V10.
"""

from __future__ import annotations

import json
import statistics
import random
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from research_engine.lifecycle.experiment_protocol import (
    ExperimentDefinition,
    ExperimentResult,
    ExperimentType,
    PopulationSpec,
    SimulationSpec,
    ValidationSpec,
)
from research_engine.lifecycle.dataset_fingerprint import build_dataset_fingerprint
from research_engine.lifecycle.validation_harness import (
    bootstrap_ci,
    compute_full_validation,
    permutation_test_paired,
)


# ═══════════════════════════════════════════════════════════════════════════════
# CANONICAL SHADOW SIMULATION (replicates ShadowTradeEngine.evaluate_bar)
# ═══════════════════════════════════════════════════════════════════════════════

def _simulate_trade(*, direction: str, entry_price: float, stop_loss: float,
                    take_profit: float, candles: list[dict], max_bars: int = 60) -> dict:
    """
    Canonical shadow trade simulation.
    
    Replicates ShadowTradeEngine.evaluate_bar():
    - BUY: SL if bar_low <= stop_loss, TP if bar_high >= take_profit
    - SELL: SL if bar_high >= stop_loss, TP if bar_low <= take_profit
    - Timeout at max_bars with exit at bar_close
    - MFE/MAE tracked before exit check on each bar
    """
    risk = abs(entry_price - stop_loss)
    if risk == 0:
        return {"r_multiple": 0, "exit_reason": "zero_risk", "bars_held": 0, "mfe_r": 0, "mae_r": 0}
    max_fav, max_adv = entry_price, entry_price
    exit_price, exit_reason, bars_held = None, "", 0
    for candle in candles[:max_bars]:
        bars_held += 1
        bh, bl, bc = candle["high"], candle["low"], candle["close"]
        if direction == "BUY":
            max_fav, max_adv = max(max_fav, bh), min(max_adv, bl)
            if bl <= stop_loss:
                exit_price, exit_reason = stop_loss, "stop_loss"; break
            elif bh >= take_profit:
                exit_price, exit_reason = take_profit, "take_profit"; break
        else:
            max_fav, max_adv = min(max_fav, bl), max(max_adv, bh)
            if bh >= stop_loss:
                exit_price, exit_reason = stop_loss, "stop_loss"; break
            elif bl <= take_profit:
                exit_price, exit_reason = take_profit, "take_profit"; break
    if exit_price is None:
        exit_price = candles[min(max_bars - 1, len(candles) - 1)]["close"] if candles else entry_price
        exit_reason, bars_held = "max_bars_timeout", min(max_bars, len(candles))
    pnl = (exit_price - entry_price) if direction == "BUY" else (entry_price - exit_price)
    r_mult = round(pnl / risk, 4)
    mfe = max(0, (max_fav - entry_price) if direction == "BUY" else (entry_price - max_fav)) / risk
    mae = max(0, (entry_price - max_adv) if direction == "BUY" else (max_adv - entry_price)) / risk
    return {"r_multiple": r_mult, "exit_reason": exit_reason, "bars_held": bars_held,
            "mfe_r": round(mfe, 4), "mae_r": round(mae, 4)}


# ═══════════════════════════════════════════════════════════════════════════════
# DATA LOADING (canonical shadow population)
# ═══════════════════════════════════════════════════════════════════════════════

def _load_shadow_population() -> list[dict]:
    """Load deduplicated real shadow trade population."""
    raw = []
    base = Path("logs/shadow_trades")
    if not base.exists():
        return []
    for f in base.rglob("*.jsonl"):
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    raw.append(json.loads(line))
                except (json.JSONDecodeError, ValueError):
                    pass

    def extract(rec):
        if "identity" in rec:
            i, s = rec["identity"], rec["decision_snapshot"]
            return {"symbol": i.get("symbol", ""), "cid": i.get("correlation_id", ""),
                    "dir": s.get("direction", ""), "entry": s.get("entry_intent_price", 0),
                    "sl": s.get("stop_loss_intent", 0), "tp": s.get("take_profit_intent", 0),
                    "time": s.get("timestamp_decision_utc", 0), "pattern": s.get("pattern", ""),
                    "score": s.get("score", 0)}
        return {"symbol": rec.get("symbol", ""), "cid": rec.get("correlation_id", ""),
                "dir": rec.get("direction", ""), "entry": rec.get("entry_price", 0),
                "sl": rec.get("stop_loss", 0), "tp": rec.get("take_profit", 0),
                "time": rec.get("entry_time", 0), "pattern": rec.get("pattern", ""),
                "score": rec.get("score", 0)}

    seen = set()
    result = []
    for r in raw:
        p = extract(r)
        if not (p["cid"] and p["entry"] and p["sl"]):
            continue
        key = (p["symbol"], p["time"], p["pattern"], p["dir"])
        if key not in seen:
            seen.add(key)
            result.append(p)
    return result


def _load_candles(symbol: str, start_time: float) -> list[dict]:
    """Load M5 candles after start_time from MT5."""
    try:
        import MetaTrader5 as mt5
        from datetime import datetime as _dt, timezone as _tz
        if not mt5.terminal_info():
            mt5.initialize()
        dt_s = _dt.fromtimestamp(start_time + 1, tz=_tz.utc)
        dt_e = _dt.fromtimestamp(start_time + 20000, tz=_tz.utc)
        rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M5, dt_s, dt_e)
        if rates is None or len(rates) == 0:
            return []
        return [{"high": float(rates[i][2]), "low": float(rates[i][3]), "close": float(rates[i][4])}
                for i in range(min(65, len(rates)))]
    except Exception:
        return []


def _filter_population(population: list[dict], spec: PopulationSpec) -> list[dict]:
    """Apply PopulationSpec filters to the raw population."""
    result = population
    if spec.pattern_filter:
        result = [p for p in result if p.get("pattern") in spec.pattern_filter]
    if spec.symbol_filter:
        result = [p for p in result if p.get("symbol") in spec.symbol_filter]
    if spec.direction_filter:
        result = [p for p in result if p.get("dir") == spec.direction_filter]
    if spec.require_correlation_id:
        result = [p for p in result if p.get("cid")]
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# EXPERIMENT TEMPLATE
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class ExperimentTemplate:
    """
    Defines the canonical execution methodology for an experiment type.
    
    The template provides execute_fn without the caller needing to write custom code.
    """
    experiment_type: ExperimentType
    description: str
    required_parameters: tuple[str, ...] = ()
    min_sample_size: int = 30
    requires_mt5: bool = True
    requires_placebo: bool = False
    placebo_methodology: str = ""
    validation_methods: tuple[str, ...] = ("bootstrap_ci", "oos_split", "symbol_robustness",
                                            "temporal_stability", "outlier_influence")
    limitations: tuple[str, ...] = ()

    def validate_definition(self, definition: ExperimentDefinition) -> tuple[bool, str]:
        """Check whether an ExperimentDefinition meets template requirements."""
        # Population-wide experiments (CONDITIONING_ANALYSIS, OOS_VALIDATION, etc.)
        # are valid without pattern_filter — they analyse the full population.
        # Only pattern-specific experiments require a pattern filter.
        if not definition.population.pattern_filter and not definition.population.symbol_filter:
            if self.experiment_type in (ExperimentType.DIRECTION_INVERSION,):
                return False, "DIRECTION_INVERSION requires pattern_filter or symbol_filter"
        return True, "valid"


# ═══════════════════════════════════════════════════════════════════════════════
# CANONICAL EXECUTION FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def _execute_direction_inversion(definition: ExperimentDefinition) -> ExperimentResult:
    """
    Canonical execution for DIRECTION_INVERSION experiments.
    
    Treatment: Flip direction (BUY→SELL or SELL→BUY), construct new SL/TP
    Control: Original direction with original geometry
    Null: Direction label assignment has no systematic effect
    """
    random.seed(42)
    population = _load_shadow_population()
    filtered = _filter_population(population, definition.population)

    if len(filtered) < definition.population.min_sample_size:
        return ExperimentResult(
            experiment_id=definition.experiment_id,
            hypothesis_id=definition.hypothesis_id,
            status="failed", n=len(filtered),
        )

    sim = definition.simulation
    treatment_results = []
    control_results = []
    used_records = []

    for p in filtered:
        risk = abs(p["entry"] - p["sl"])
        if risk <= 0:
            continue
        candles = _load_candles(p["symbol"], p["time"])
        if len(candles) < 10:
            continue

        # Treatment: inverted direction
        inv_dir = "BUY" if p["dir"] == "SELL" else "SELL"
        if inv_dir == "BUY":
            new_sl = p["entry"] - risk * sim.stop_multiplier
            new_tp = p["entry"] + risk * sim.tp_multiplier
        else:
            new_sl = p["entry"] + risk * sim.stop_multiplier
            new_tp = p["entry"] - risk * sim.tp_multiplier

        inv_res = _simulate_trade(direction=inv_dir, entry_price=p["entry"],
                                   stop_loss=new_sl, take_profit=new_tp,
                                   candles=candles, max_bars=sim.max_bars)

        # Control: original direction
        orig_res = _simulate_trade(direction=p["dir"], entry_price=p["entry"],
                                    stop_loss=p["sl"], take_profit=p["tp"],
                                    candles=candles, max_bars=sim.max_bars)

        treatment_results.append({**inv_res, "symbol": p["symbol"], "time": p["time"]})
        control_results.append(orig_res)
        used_records.append(p)

    if not treatment_results:
        return ExperimentResult(
            experiment_id=definition.experiment_id,
            hypothesis_id=definition.hypothesis_id, status="failed", n=0,
        )

    return _build_result(definition, treatment_results, control_results, used_records)


def _execute_counterfactual_geometry(definition: ExperimentDefinition) -> ExperimentResult:
    """
    Canonical execution for COUNTERFACTUAL_GEOMETRY experiments.
    
    Treatment: Modified SL/TP (wider/tighter stop, different RR)
    Control: Original geometry
    Null: SL width has no systematic effect on R
    """
    random.seed(42)
    population = _load_shadow_population()
    filtered = _filter_population(population, definition.population)

    if len(filtered) < definition.population.min_sample_size:
        return ExperimentResult(
            experiment_id=definition.experiment_id,
            hypothesis_id=definition.hypothesis_id, status="failed", n=len(filtered),
        )

    sim = definition.simulation
    treatment_results = []
    control_results = []
    used_records = []

    for p in filtered:
        risk = abs(p["entry"] - p["sl"])
        if risk <= 0:
            continue
        candles = _load_candles(p["symbol"], p["time"])
        if len(candles) < 10:
            continue

        # Treatment: modified geometry (same direction, different SL/TP)
        direction = p["dir"] if sim.direction in ("", "SAME") else sim.direction
        if direction == "BUY":
            new_sl = p["entry"] - risk * sim.stop_multiplier
            new_tp = p["entry"] + risk * sim.tp_multiplier
        else:
            new_sl = p["entry"] + risk * sim.stop_multiplier
            new_tp = p["entry"] - risk * sim.tp_multiplier

        treat_res = _simulate_trade(direction=direction, entry_price=p["entry"],
                                     stop_loss=new_sl, take_profit=new_tp,
                                     candles=candles, max_bars=sim.max_bars)

        # Control: original geometry
        orig_res = _simulate_trade(direction=p["dir"], entry_price=p["entry"],
                                    stop_loss=p["sl"], take_profit=p["tp"],
                                    candles=candles, max_bars=sim.max_bars)

        treatment_results.append({**treat_res, "symbol": p["symbol"], "time": p["time"]})
        control_results.append(orig_res)
        used_records.append(p)

    if not treatment_results:
        return ExperimentResult(
            experiment_id=definition.experiment_id,
            hypothesis_id=definition.hypothesis_id, status="failed", n=0,
        )

    return _build_result(definition, treatment_results, control_results, used_records)


def _execute_conditioning_analysis(definition: ExperimentDefinition) -> ExperimentResult:
    """
    Canonical execution for CONDITIONING_ANALYSIS experiments.
    
    Segments the population by a conditioning variable and reports R per segment.
    No separate control — the full population IS the reference.
    """
    random.seed(42)
    population = _load_shadow_population()
    filtered = _filter_population(population, definition.population)

    if len(filtered) < definition.population.min_sample_size:
        return ExperimentResult(
            experiment_id=definition.experiment_id,
            hypothesis_id=definition.hypothesis_id, status="failed", n=len(filtered),
        )

    sim = definition.simulation
    results = []
    used_records = []

    for p in filtered:
        risk = abs(p["entry"] - p["sl"])
        if risk <= 0:
            continue
        candles = _load_candles(p["symbol"], p["time"])
        if len(candles) < 10:
            continue

        res = _simulate_trade(direction=p["dir"], entry_price=p["entry"],
                               stop_loss=p["sl"], take_profit=p["tp"],
                               candles=candles, max_bars=sim.max_bars)
        results.append({**res, "symbol": p["symbol"], "time": p["time"]})
        used_records.append(p)

    if not results:
        return ExperimentResult(
            experiment_id=definition.experiment_id,
            hypothesis_id=definition.hypothesis_id, status="failed", n=0,
        )

    # For conditioning: no paired control — just population metrics
    return _build_result(definition, results, [], used_records)


# ═══════════════════════════════════════════════════════════════════════════════
# RESULT BUILDER (shared by all templates)
# ═══════════════════════════════════════════════════════════════════════════════

def _build_result(definition: ExperimentDefinition, treatment_results: list[dict],
                  control_results: list[dict], used_records: list[dict]) -> ExperimentResult:
    """Build ExperimentResult with full validation from treatment/control outcomes."""
    r_vals = [r["r_multiple"] for r in treatment_results]
    n = len(r_vals)

    # Core metrics
    ci_lo, ci_hi = bootstrap_ci(r_vals, seed=42)

    # Paired permutation test (if control available)
    p_value = None
    if control_results and len(control_results) == len(r_vals):
        ctrl_vals = [r["r_multiple"] for r in control_results]
        try:
            p_value = permutation_test_paired(r_vals, ctrl_vals, n_perms=5000, seed=42)
        except ValueError:
            p_value = None

    # Full validation suite
    validation = compute_full_validation(treatment_results, r_field="r_multiple",
                                          time_field="time", symbol_field="symbol")

    # Exit distribution
    exits = Counter(r["exit_reason"] for r in treatment_results)

    # Dataset fingerprint
    fp = build_dataset_fingerprint(
        used_records,
        dataset_id=f"population_{definition.experiment_type.value}",
        dataset_version="shadow_trades_v1",
        population=definition.title or definition.experiment_type.value,
        filters_applied=[f"pattern={definition.population.pattern_filter}",
                         f"symbol={definition.population.symbol_filter}",
                         f"direction={definition.population.direction_filter}"],
        time_field="time",
    )

    return ExperimentResult(
        experiment_id=definition.experiment_id,
        hypothesis_id=definition.hypothesis_id,
        status="complete",
        n=n,
        mean_r=statistics.mean(r_vals) if r_vals else 0,
        median_r=statistics.median(r_vals) if r_vals else 0,
        total_r=sum(r_vals),
        win_rate=sum(1 for v in r_vals if v > 0) / n if n > 0 else 0,
        std_dev=statistics.stdev(r_vals) if n > 1 else 0,
        ci_lower=ci_lo,
        ci_upper=ci_hi,
        permutation_p=p_value,
        sl_rate=exits.get("stop_loss", 0) / n if n > 0 else 0,
        tp_rate=exits.get("take_profit", 0) / n if n > 0 else 0,
        timeout_rate=exits.get("max_bars_timeout", 0) / n if n > 0 else 0,
        mean_mfe=statistics.mean([r["mfe_r"] for r in treatment_results]) if treatment_results else 0,
        mean_mae=statistics.mean([r["mae_r"] for r in treatment_results]) if treatment_results else 0,
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
        dataset_fingerprint=fp.to_dict(),
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# TEMPLATE REGISTRY
# ═══════════════════════════════════════════════════════════════════════════════

_TEMPLATES: dict[ExperimentType, ExperimentTemplate] = {
    ExperimentType.DIRECTION_INVERSION: ExperimentTemplate(
        experiment_type=ExperimentType.DIRECTION_INVERSION,
        description="Flip trade direction (BUY↔SELL) while keeping same entry point and risk distance. "
                    "Tests whether the pattern identifies reversal/exhaustion points.",
        required_parameters=("pattern_filter",),
        min_sample_size=30,
        requires_mt5=True,
        requires_placebo=True,
        placebo_methodology="Run the same inversion on all other patterns. If majority are also positive, "
                            "the effect is general (dataset bias) not pattern-specific.",
        validation_methods=("bootstrap_ci", "permutation_test_paired", "oos_split",
                            "symbol_robustness", "temporal_stability", "outlier_influence"),
        limitations=("Requires MT5 for candle data", "Results depend on entry timing",
                     "Shadow model uses midpoint entry (no spread)"),
    ),
    ExperimentType.COUNTERFACTUAL_GEOMETRY: ExperimentTemplate(
        experiment_type=ExperimentType.COUNTERFACTUAL_GEOMETRY,
        description="Modify stop-loss and/or take-profit geometry while keeping direction. "
                    "Tests whether alternative risk construction improves outcomes.",
        required_parameters=("pattern_filter",),
        min_sample_size=30,
        requires_mt5=True,
        requires_placebo=False,
        placebo_methodology="",
        validation_methods=("bootstrap_ci", "permutation_test_paired", "oos_split",
                            "symbol_robustness", "temporal_stability"),
        limitations=("Requires MT5 for candle data", "Does not account for spread changes at wider stops"),
    ),
    ExperimentType.CONDITIONING_ANALYSIS: ExperimentTemplate(
        experiment_type=ExperimentType.CONDITIONING_ANALYSIS,
        description="Segment population by a conditioning variable and measure R per segment. "
                    "Tests whether a factor systematically affects outcome.",
        required_parameters=("pattern_filter",),
        min_sample_size=20,
        requires_mt5=True,
        requires_placebo=False,
        validation_methods=("bootstrap_ci", "oos_split", "symbol_robustness"),
        limitations=("Segmentation reduces per-bucket N", "Multiple comparisons within segments"),
    ),
    ExperimentType.POPULATION_COMPARISON: ExperimentTemplate(
        experiment_type=ExperimentType.POPULATION_COMPARISON,
        description="Compare two populations (e.g., guard-passed vs guard-blocked) on the same metric.",
        required_parameters=(),
        min_sample_size=20,
        requires_mt5=True,
        requires_placebo=False,
        validation_methods=("bootstrap_ci", "permutation_test_paired", "oos_split"),
        limitations=("Populations may differ on confounds beyond the tested variable",),
    ),
    ExperimentType.PLACEBO_CONTROL: ExperimentTemplate(
        experiment_type=ExperimentType.PLACEBO_CONTROL,
        description="Apply the same experimental protocol to unrelated populations. "
                    "Tests whether an observed effect is general or specific.",
        required_parameters=("pattern_filter",),
        min_sample_size=20,
        requires_mt5=True,
        requires_placebo=False,  # IS the placebo
        validation_methods=("bootstrap_ci",),
        limitations=("Placebo populations may not be truly independent of the target",),
    ),
    ExperimentType.OOS_VALIDATION: ExperimentTemplate(
        experiment_type=ExperimentType.OOS_VALIDATION,
        description="Chronological train/test split to validate in-sample findings.",
        required_parameters=("pattern_filter",),
        min_sample_size=50,
        requires_mt5=True,
        requires_placebo=False,
        validation_methods=("bootstrap_ci", "oos_split", "temporal_stability"),
        limitations=("Single split point may not represent regime changes",),
    ),
    ExperimentType.ROBUSTNESS_CHECK: ExperimentTemplate(
        experiment_type=ExperimentType.ROBUSTNESS_CHECK,
        description="Test whether a finding survives symbol exclusion, outlier removal, "
                    "and temporal segmentation.",
        required_parameters=("pattern_filter",),
        min_sample_size=30,
        requires_mt5=True,
        requires_placebo=False,
        validation_methods=("bootstrap_ci", "symbol_robustness", "temporal_stability", "outlier_influence"),
        limitations=("Small sub-populations after exclusion reduce statistical power",),
    ),
}

_EXECUTE_FNS: dict[ExperimentType, Callable[[ExperimentDefinition], ExperimentResult]] = {
    ExperimentType.DIRECTION_INVERSION: _execute_direction_inversion,
    ExperimentType.COUNTERFACTUAL_GEOMETRY: _execute_counterfactual_geometry,
    ExperimentType.CONDITIONING_ANALYSIS: _execute_conditioning_analysis,
    # POPULATION_COMPARISON, PLACEBO_CONTROL, OOS_VALIDATION, ROBUSTNESS_CHECK
    # use the conditioning/geometry patterns or require custom population setup.
    # They can be added as needed — the registry validates presence.
}


class ExperimentTemplateRegistry:
    """
    Discoverable registry of canonical experiment execution methodologies.
    
    Usage:
        registry = ExperimentTemplateRegistry()
        
        if registry.supports(ExperimentType.DIRECTION_INVERSION):
            template = registry.get(ExperimentType.DIRECTION_INVERSION)
            execute_fn = registry.get_execute_fn(ExperimentType.DIRECTION_INVERSION)
            result = execute_fn(experiment_definition)
    """

    def get(self, experiment_type: ExperimentType) -> ExperimentTemplate | None:
        """Get the template for an experiment type."""
        return _TEMPLATES.get(experiment_type)

    def supports(self, experiment_type: ExperimentType) -> bool:
        """Check if a canonical execute_fn exists for this type."""
        return experiment_type in _EXECUTE_FNS

    def get_execute_fn(self, experiment_type: ExperimentType) -> Callable[[ExperimentDefinition], ExperimentResult] | None:
        """Get the canonical execution function for an experiment type."""
        return _EXECUTE_FNS.get(experiment_type)

    def list_templates(self) -> list[ExperimentTemplate]:
        """List all registered templates."""
        return list(_TEMPLATES.values())

    def list_supported_types(self) -> list[ExperimentType]:
        """List experiment types with canonical execute_fn available."""
        return list(_EXECUTE_FNS.keys())

    def validate(self, definition: ExperimentDefinition) -> tuple[bool, str]:
        """
        Validate that an ExperimentDefinition meets its template requirements.
        
        Returns (valid, reason).
        """
        template = _TEMPLATES.get(definition.experiment_type)
        if not template:
            return False, f"No template for experiment type {definition.experiment_type}"

        # Check minimum sample size
        if definition.population.min_sample_size < template.min_sample_size:
            return False, (f"min_sample_size {definition.population.min_sample_size} "
                           f"below template minimum {template.min_sample_size}")

        # Check required parameters
        for param in template.required_parameters:
            if param == "pattern_filter" and not definition.population.pattern_filter:
                # Only enforce for experiment types that genuinely require a pattern
                if definition.experiment_type in (ExperimentType.DIRECTION_INVERSION,):
                    return False, "pattern_filter is required for DIRECTION_INVERSION but empty"
                # Other types (CONDITIONING_ANALYSIS, COUNTERFACTUAL_GEOMETRY) can work population-wide

        # Template-specific validation
        return template.validate_definition(definition)
